import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from werkzeug.security import generate_password_hash

from ..exceptions import (
    AuthError,
    ConflictError,
    ForbiddenError,
)
from ..extensions import db
from ..models.business import Business, BusinessConfig, Branch, generate_slug
from ..models.inventory import Warehouse
from ..models.identity import User
from ..models.iam import (
    BusinessUser,
    PasswordResetToken,
    Role,
    RefreshToken,
    hash_token,
)
from ..utils.db import commit
from ..utils.security import validate_password_strength, verify_password
from .token_service import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
)
from ..seed import bootstrap_business_roles, bootstrap_expense_categories
from .subscription_service import (
    TRIAL_DAYS,
    ensure_subscription,
    get_plan_for_signup,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_expired(value: datetime) -> bool:
    """Compare a stored timestamp against now, tolerating naive columns.

    SQLite drops timezone information, so a ``DateTime(timezone=True)`` column
    can hand back a naive value while ``_now()`` is timezone-aware.
    """
    if value is None:
        return True
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value < _now()


def find_role_by_name(business_id, name):
    return db.session.scalar(
        select(Role).where(
            Role.business_id == business_id, Role.name == name, Role.is_active.is_(True)
        )
    )


def register(data, actor_id=None):
    """Register a new business together with its owning Business Owner.

    The signing-up user is always assigned the ``Business Owner`` role and the
    business is provisioned with an automatic ``TRIAL_DAYS`` free trial, so the
    new owner can reach the Business Owner dashboard immediately.
    """
    if db.session.scalar(select(User).where(User.email == data.email)):
        raise ConflictError("An account with this email already exists")
    if db.session.scalar(
        select(Business).where(Business.name == data.business_name)
    ):
        raise ConflictError("A business with this name already exists")

    business = Business(
        name=data.business_name,
        slug=generate_slug(data.business_name),
        default_country=data.business_country,
        default_currency=data.business_currency,
        default_timezone=data.timezone,
    )
    db.session.add(business)
    db.session.flush()

    config = BusinessConfig(
        business_id=business.id,
        country=data.business_country,
        currency=data.business_currency,
        timezone=data.timezone,
    )
    db.session.add(config)

    head_branch = Branch(
        business_id=business.id,
        name="Head Office",
        code="HEAD",
        is_head=True,
    )
    db.session.add(head_branch)
    db.session.flush()

    db.session.add(
        Warehouse(
            business_id=business.id,
            branch_id=head_branch.id,
            code="MAIN",
            name="Main Warehouse",
            is_default=True,
        )
    )

    user = User(
        email=data.email,
        password_hash=generate_password_hash(data.password, method="scrypt"),
        first_name=data.first_name,
        last_name=data.last_name,
        phone=data.phone,
        primary_business_id=business.id,
    )
    db.session.add(user)
    db.session.flush()

    bootstrap_business_roles(business)
    # A starter expense category list so the expense form has something to
    # select from before the owner has configured anything.
    bootstrap_expense_categories(business)

    # Grant the free trial on the plan the signup chose. This is a required
    # part of signup, not a best-effort side effect: without an active
    # subscription every authenticated request is rejected by the tier-2 access
    # check, so a failure here must abort registration rather than create a
    # locked-out user.
    subscription = ensure_subscription(business, plan=get_plan_for_signup(data.plan_id))

    owner_role = find_role_by_name(business.id, "Business Owner")
    if owner_role is None:
        raise ConflictError("Business Owner role could not be provisioned")
    membership = BusinessUser(
        business_id=business.id,
        user_id=user.id,
        email=user.email,
        role_id=owner_role.id,
        branch_id=head_branch.id,
        is_default=True,
    )
    db.session.add(membership)
    db.session.flush()

    from .audit_log_service import log_event
    log_event(business.id, "create", "business", user.id, business.id, {
        "business_name": business.name, "user_id": str(user.id),
    })
    log_event(business.id, "register", "user", user.id, user.id, {
        "email": user.email, "business_id": str(business.id),
    })
    log_event(business.id, "create", "subscription", user.id, subscription.id, {
        "status": subscription.status,
        "plan": subscription.plan.name if subscription.plan else None,
        "trial_days": (subscription.plan.trial_days if subscription.plan else TRIAL_DAYS),
        "trial_ends_at": subscription.trial_ends_at.isoformat() if subscription.trial_ends_at else None,
    })

    return business, user, membership, head_branch, subscription


def find_user_by_identifier(identifier):
    """Resolve a user from a sign-in identifier: username or email.

    The value is matched against both columns so a user can type whichever one
    they remember. When a value could match two different accounts (one account
    whose username equals another account's email) the email match wins, which
    keeps every pre-existing email sign-in behaving exactly as before.
    """
    value = (identifier or "").strip().lower()
    if not value:
        return None
    if "@" in value:
        return db.session.scalar(select(User).where(User.email == value))
    user = db.session.scalar(select(User).where(User.username == value))
    if user is None:
        user = db.session.scalar(select(User).where(User.email == value))
    return user


def authenticate(identifier, password):
    user = find_user_by_identifier(identifier)
    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def authenticate_with_pin(identifier, pin):
    """Authenticate with the short-form sign-in PIN.

    Mirrors `authenticate` so both paths share the same identifier resolution,
    inactive-user and missing-user handling. The PIN is stored hashed; a user
    who never set one simply fails the hash check.
    """
    user = find_user_by_identifier(identifier)
    if user is None or not user.is_active:
        return None
    if not verify_password(pin, user.pin_hash):
        return None
    return user


def set_username(user, username):
    """Assign a sign-in username, rejecting one already taken by another user."""
    normalized = (username or "").strip().lower()
    existing = db.session.scalar(select(User).where(User.username == normalized))
    if existing is not None and existing.id != user.id:
        raise ConflictError("That username is already taken")
    user.username = normalized
    commit()
    return user


def set_pin(user, pin):
    """Store a hashed sign-in PIN for the given user."""
    user.pin_hash = generate_password_hash(pin, method="scrypt")
    commit()
    return user


def clear_pin(user):
    user.pin_hash = None
    commit()
    return user


def default_business(user):
    """Resolve the user's default (active) business membership."""
    membership = db.session.scalar(
        select(BusinessUser)
        .where(
            BusinessUser.user_id == user.id,
            BusinessUser.is_active.is_(True),
        )
        .order_by(BusinessUser.is_default.is_(True).desc(), BusinessUser.created_at.desc())
    )
    return membership


def issue_tokens(user, business_id):
    """Build a signed access token + a refresh token persisted to the DB.

    The session is flushed so the caller can roll back or commit as part of a
    larger unit of work. Refresh tokens are stored hashed at rest.
    """
    access = create_access_token(user.id, business_id)
    refresh_str, record = create_refresh_token(user.id, business_id)
    db.session.add(record)
    db.session.flush()
    return access, refresh_str


def revoke_refresh_token(token_str):
    claims = decode_refresh_token(token_str)
    record = db.session.scalar(
        select(RefreshToken).where(
            RefreshToken.jti == claims["jti"], RefreshToken.user_id == claims["sub"]
        )
    )
    if record is not None:
        record.revoked = True
        db.session.commit()


def revoke_refresh_tokens_for_business(business_id):
    """Revoke every active refresh token belonging to *business_id*.

    Called when a subscription expires so that no new access tokens can be
    minted for the tenant. Existing short-lived access tokens will naturally
    expire according to their JWT lifetime.
    """
    db.session.execute(
        RefreshToken.__table__.update()
        .where(
            RefreshToken.business_id == business_id,
            RefreshToken.revoked.is_(False),
        )
        .values(revoked=True)
    )
    db.session.commit()


def revoke_refresh_tokens_for_user(user_id):
    """Revoke every active refresh token for a single user."""
    db.session.execute(
        RefreshToken.__table__.update()
        .where(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked.is_(False),
        )
        .values(revoked=True)
    )
    db.session.commit()


def refresh_access(refresh_str):
    """Validate a refresh token, rotate it, and return fresh access + refresh."""
    claims = decode_refresh_token(refresh_str)
    record = db.session.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == hash_token(refresh_str),
            RefreshToken.revoked.is_(False),
        )
    )
    if record is None:
        raise AuthError("Refresh token not recognized")
    if record.revoked:
        raise AuthError("Refresh token revoked")
    if _is_expired(record.expires_at):
        raise AuthError("Refresh token expired")
    if str(record.user_id) != claims["sub"]:
        raise AuthError("Token user mismatch")

    # Deny refresh if the subscription is no longer active. Revoke the
    # refresh token so the client cannot keep minting new access tokens.
    from .subscription_service import is_subscription_active

    if not is_subscription_active(record.business_id):
        record.revoked = True
        db.session.add(record)
        db.session.commit()
        raise AuthError("Subscription has expired")

    # Rotate: revoke the previous refresh token and persist a fresh pair.
    record.revoked = True
    record.last_used_at = _now()
    db.session.add(record)

    user = db.session.get(User, record.user_id)
    if user is None or not user.is_active:
        raise AuthError("User session no longer valid")
    access, new_refresh_str = issue_tokens(user, claims["bid"])
    db.session.commit()
    return access, new_refresh_str


def switch_business(user, target_business_id):
    membership = db.session.scalar(
        select(BusinessUser)
        .where(
            BusinessUser.user_id == user.id,
            BusinessUser.business_id == target_business_id,
            BusinessUser.is_active.is_(True),
        )
    )
    if membership is None:
        raise ForbiddenError("You are not a member of the requested business")

    db.session.execute(
        BusinessUser.__table__.update()
        .where(
            BusinessUser.user_id == user.id,
            BusinessUser.is_default.is_(True),
        )
        .values(is_default=False)
    )
    membership.is_default = True
    db.session.flush()

    access, refresh_str = issue_tokens(user, membership.business_id)
    return access, refresh_str, membership


def user_businesses(user):
    rows = db.session.scalars(
        select(BusinessUser)
        .where(
            BusinessUser.user_id == user.id,
            BusinessUser.is_active.is_(True),
        )
        .order_by(BusinessUser.is_default.is_(True).desc())
    )
    memberships = list(rows)
    business_ids = [m.business_id for m in memberships]
    businesses = db.session.scalars(
        select(Business).where(Business.id.in_(business_ids))
    ).all()
    by_id = {b.id: b for b in businesses}
    role_ids = [m.role_id for m in memberships]
    roles = db.session.scalars(
        select(Role).where(Role.id.in_(role_ids))
    ).all()
    role_map = {r.id: r.name for r in roles}
    result = []
    for m in memberships:
        b = by_id.get(m.business_id)
        result.append(
            {
                "business": b.to_dict() if b else None,
                "business_id": str(m.business_id),
                "role": role_map.get(m.role_id),
                "role_id": str(m.role_id) if m.role_id else None,
                "is_default": m.is_default,
                "branch_id": str(m.branch_id) if m.branch_id else None,
            }
        )
    return result


def create_password_reset(user):
    """Generate a single-use password-reset token and persist its hash."""
    raw = secrets.token_urlsafe(32)
    record = PasswordResetToken(
        user_id=user.id,
        token_hash=hash_token(raw),
        expires_at=_now() + timedelta(hours=1),
        used=False,
    )
    db.session.add(record)
    db.session.flush()
    return raw


def consume_password_reset(token_str, new_password):
    record = db.session.scalar(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == hash_token(token_str),
            PasswordResetToken.used.is_(False),
        )
    )
    if record is None:
        raise AuthError("Invalid or expired reset token")
    if _is_expired(record.expires_at):
        raise AuthError("Reset token expired")

    user = db.session.get(User, record.user_id)
    if user is None:
        raise AuthError("User no longer exists")

    # Consuming an invite's reset token is how an invited member activates
    # their account, so this is the one place a pending user becomes active.
    # An owner removing someone from the business deletes their membership and
    # deactivates the user, so "has an active membership" separates the two:
    # a genuinely deactivated account has none and stays locked out.
    if not user.is_active:
        pending_invite = db.session.scalar(
            select(BusinessUser).where(
                BusinessUser.user_id == user.id, BusinessUser.is_active.is_(True)
            )
        )
        if pending_invite is None:
            raise AuthError("User no longer exists")
        user.is_active = True
        user.is_email_verified = True

    validate_password_strength(new_password)
    user.password_hash = generate_password_hash(new_password, method="scrypt")
    record.used = True
    db.session.commit()
