import uuid as uuidlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from werkzeug.security import generate_password_hash

from ..exceptions import ConflictError, ForbiddenError, NotFoundError
from ..extensions import db
from ..models import BusinessUser, Permission, Role, RolePermission, SYSTEM_ROLES, User
from ..models.iam import PasswordResetToken, hash_token
from ..utils.db import commit

SYSTEM_ROLE_NAMES = set(SYSTEM_ROLES.keys())


def _now():
    return datetime.now(timezone.utc)


def _role_of_business(role_id, business_id):
    role = db.session.get(Role, role_id)
    if role is None:
        raise NotFoundError("Role not found")
    if role.business_id != business_id:
        raise NotFoundError("Role not found")
    return role


def list_users(business_id, pagination):
    from ..utils.pagination import paginate

    query = (
        select(BusinessUser, User, Role)
        .join(User, BusinessUser.user_id == User.id)
        .outerjoin(Role, BusinessUser.role_id == Role.id)
        .where(BusinessUser.business_id == business_id)
        .order_by(User.email.asc())
    )
    total_rows, total, pages = paginate(query, pagination.page, pagination.per_page)
    items = []
    for membership, user, role in total_rows:
        items.append(_membership_dto(membership, user, role))
    return items, total, pages


def get_user(business_id, user_id):
    membership = db.session.scalar(
        select(BusinessUser).where(
            BusinessUser.business_id == business_id,
            BusinessUser.user_id == user_id,
        )
    )
    if membership is None:
        raise NotFoundError("User not found in this business")
    user = db.session.get(User, membership.user_id)
    role = db.session.get(Role, membership.role_id)
    return _membership_dto(membership, user, role)


PAYMENT_MODES = {"salary", "commission", "salary_plus_commission"}


def _membership_dto(membership, user, role):
    return {
        "membership_id": str(membership.id),
        "user": user.to_dict() if user else None,
        "role_id": str(membership.role_id) if membership.role_id else None,
        "role": role.name if role else None,
        "branch_id": str(membership.branch_id) if membership.branch_id else None,
        "is_default": membership.is_default,
        "is_active": membership.is_active,
        "payment_mode": membership.payment_mode,
        "commission_rate": float(membership.commission_rate) if membership.commission_rate is not None else None,
        "base_salary": float(membership.base_salary) if membership.base_salary is not None else None,
    }


def _apply_compensation(membership, data):
    """Apply payment mode + rates when the client supplied them."""
    if getattr(data, "payment_mode", None) is not None:
        membership.payment_mode = data.payment_mode
    if getattr(data, "commission_rate", None) is not None:
        membership.commission_rate = data.commission_rate
    if getattr(data, "base_salary", None) is not None:
        membership.base_salary = data.base_salary


def invite_user(business_id, data, actor_id):
    from .subscription_service import check_plan_limit

    # Only a *new* membership consumes a seat; re-inviting someone who is
    # already on the team must not be blocked by their own existing seat.
    existing_user = db.session.scalar(select(User).where(User.email == data.email))
    already_member = False
    if existing_user is not None:
        already_member = db.session.scalar(
            select(BusinessUser).where(
                BusinessUser.business_id == business_id,
                BusinessUser.user_id == existing_user.id,
            )
        ) is not None
    if not already_member:
        check_plan_limit(business_id, "users")

    role = _role_of_business(data.role_id, business_id)

    user = existing_user
    created_user = False
    if user is None:
        password_provided = bool(data.password)
        password_hash = (
            generate_password_hash(data.password, method="scrypt")
            if data.password
            else generate_password_hash(uuidlib.uuid4().hex, method="scrypt")
        )
        user = User(
            email=data.email,
            password_hash=password_hash,
            first_name=data.first_name,
            last_name=data.last_name,
            phone=data.phone,
            is_active=password_provided,
            primary_business_id=business_id,
        )
        if data.username:
            _assign_username(user, data.username)
        user.created_by = actor_id
        user.updated_by = actor_id
        db.session.add(user)
        db.session.flush()
        created_user = True

    membership = db.session.scalar(
        select(BusinessUser).where(
            BusinessUser.business_id == business_id,
            BusinessUser.user_id == user.id,
        )
    )
    if membership is None:
        membership = BusinessUser(
            business_id=business_id,
            user_id=user.id,
            email=user.email,
            role_id=role.id,
            branch_id=data.branch_id,
            is_default=False,
            payment_mode=getattr(data, "payment_mode", None),
            commission_rate=getattr(data, "commission_rate", None),
            base_salary=getattr(data, "base_salary", None),
        )
        membership.created_by = actor_id
    else:
        if not membership.is_active:
            membership.is_active = True
        membership.role_id = role.id
        if data.branch_id is not None:
            membership.branch_id = data.branch_id
        _apply_compensation(membership, data)
        membership.updated_by = actor_id
    db.session.add(membership)
    db.session.flush()

    reset_token = None
    if created_user:
        raw = secrets_token()
        record = PasswordResetToken(
            user_id=user.id,
            token_hash=hash_token(raw),
            expires_at=_now() + timedelta(hours=24),
            used=False,
        )
        db.session.add(record)
        reset_token = raw

    commit()
    return _membership_dto(membership, user, role), reset_token


def secrets_token():
    import secrets

    return secrets.token_urlsafe(32)


def _assign_username(user, username):
    """Set a sign-in username on *user*, checking global uniqueness."""
    normalized = (username or "").strip().lower()
    existing = db.session.scalar(select(User).where(User.username == normalized))
    if existing is not None and existing.id != user.id:
        raise ConflictError("That username is already taken")
    user.username = normalized


def update_user(business_id, user_id, data, actor_id):
    membership = db.session.scalar(
        select(BusinessUser).where(
            BusinessUser.business_id == business_id,
            BusinessUser.user_id == user_id,
        )
    )
    if membership is None:
        raise NotFoundError("User not found in this business")
    user = db.session.get(User, user_id)
    role = db.session.get(Role, membership.role_id)

    if data.role_id is not None:
        _role_of_business(data.role_id, business_id)
        membership.role_id = data.role_id
        role = db.session.get(Role, data.role_id)
    if data.branch_id is not None:
        membership.branch_id = data.branch_id
    if data.is_active is not None:
        membership.is_active = data.is_active
        if data.is_active and user is not None:
            user.is_active = True
    if data.first_name is not None:
        user.first_name = data.first_name
    if data.last_name is not None:
        user.last_name = data.last_name
    if data.phone is not None:
        user.phone = data.phone
    _apply_compensation(membership, data)

    if data.username is not None:
        _assign_username(user, data.username)
    if data.password is not None:
        user.password_hash = generate_password_hash(data.password, method="scrypt")
        if not user.is_active:
            user.is_active = True

    membership.updated_by = actor_id
    if user:
        user.updated_by = actor_id
    commit()
    return _membership_dto(membership, user, role)


def delete_user(business_id, user_id):
    membership = db.session.scalar(
        select(BusinessUser).where(
            BusinessUser.business_id == business_id,
            BusinessUser.user_id == user_id,
        )
    )
    if membership is None:
        raise NotFoundError("User not found in this business")
    other = db.session.scalar(
        select(BusinessUser).where(
            BusinessUser.user_id == user_id,
            BusinessUser.business_id != business_id,
            BusinessUser.is_active.is_(True),
        )
    )
    db.session.delete(membership)
    if other is None:
        user = db.session.get(User, user_id)
        if user is not None:
            user.is_active = False
    commit()


def list_roles(business_id):
    roles = db.session.scalars(
        select(Role).where(Role.business_id == business_id).order_by(Role.is_system.desc(), Role.name)
    ).all()
    return [r.to_dict(include_permissions=True) for r in roles]


def get_role(business_id, role_id):
    role = _role_of_business(role_id, business_id)
    return role.to_dict(include_permissions=True)


def create_role(business_id, data, actor_id):
    if data.name in SYSTEM_ROLE_NAMES:
        raise ConflictError("System role names are reserved")
    existing = db.session.scalar(
        select(Role).where(Role.business_id == business_id, Role.name == data.name)
    )
    if existing is not None:
        raise ConflictError("A role with this name already exists")
    role = Role(
        business_id=business_id,
        name=data.name,
        description=data.description,
        is_system=False,
        requires_owner_approval=_approval_map(data),
    )
    role.created_by = actor_id
    role.updated_by = actor_id
    db.session.add(role)
    db.session.flush()
    _assign_permissions(role.id, data.permission_names)
    commit()
    return role.to_dict(include_permissions=True)


def _approval_map(data):
    """Normalise the ``requires_owner_approval`` payload onto the role.

    Absent means "no module is flagged", which is exactly how every role behaved
    before the field existed. Storing ``{}`` rather than ``None`` keeps the
    column's not-null guarantee meaningful.
    """
    flags = getattr(data, "requires_owner_approval", None)
    if not flags:
        return {}
    return {code: True for code, flag in flags.items() if flag}


def update_role(business_id, role_id, data, actor_id):
    role = _role_of_business(role_id, business_id)
    if role.is_system and data.name is not None:
        raise ConflictError("System role names cannot be changed")
    if data.name is not None:
        existing = db.session.scalar(
            select(Role).where(
                Role.business_id == business_id, Role.name == data.name, Role.id != role_id
            )
        )
        if existing is not None:
            raise ConflictError("A role with this name already exists")
        role.name = data.name
    if data.description is not None:
        role.description = data.description
    if getattr(data, "requires_owner_approval", None) is not None:
        role.requires_owner_approval = _approval_map(data)
    role.updated_by = actor_id
    if data.permission_names is not None:
        _assign_permissions(role.id, data.permission_names, replace=True)
    commit()
    return role.to_dict(include_permissions=True)


def delete_role(business_id, role_id, actor_id=None):
    """Delete a role, including a system one.

    System roles are seeded and normally reappear the next time roles are synced
    or the tenant is re-seeded, so deleting one is reversible in a way deleting a
    custom role is not. It is still allowed: a business should be able to retire
    a role it does not use, and a custom role that happens to shadow a system
    name is a mistake worth undoing.

    The real hazard is a role that staff are still assigned to. ``business_users
    .role_id`` is declared ON DELETE RESTRICT, but SQLite does not enforce
    foreign keys unless PRAGMA foreign_keys is on, and this app never turns it
    on. Left unchecked that turns a delete into silent data corruption: the
    membership survives with a role_id pointing at a row that no longer exists,
    the member loses every permission at once, and the breakage only shows up
    later as mysterious 403s. So the check is done here, in the service,
    rather than delegated to the database.
    """
    role = _role_of_business(role_id, business_id)

    assigned = db.session.scalars(
        select(BusinessUser).where(
            BusinessUser.business_id == business_id,
            BusinessUser.role_id == role.id,
        )
    ).all()
    if assigned:
        count = len(assigned)
        raise ConflictError(
            f"Cannot delete this role: {count} staff member"
            f"{'s are' if count > 1 else ' is'} still assigned to it. "
            "Reassign them to another role first."
        )

    # Deleting the role the caller is using would strip the caller's own access
    # mid-request and, for an owner, could leave the tenant with no way back in.
    if actor_id is not None:
        actor_role = db.session.scalar(
            select(BusinessUser.role_id).where(
                BusinessUser.business_id == business_id,
                BusinessUser.user_id == actor_id,
            )
        )
        if actor_role is not None and str(actor_role) == str(role.id):
            raise ConflictError("You cannot delete the role assigned to your own account")

    db.session.delete(role)
    commit()
    return {"deleted": True}


def get_role_permissions(business_id, role_id):
    role = _role_of_business(role_id, business_id)
    return [rp.permission.name for rp in role.role_permissions]


def assign_role_permissions(business_id, role_id, permission_names):
    role = _role_of_business(role_id, business_id)
    _assign_permissions(role.id, permission_names, replace=True)
    commit()
    return role.to_dict(include_permissions=True)


def list_permission_modules():
    """The module tree the role editor renders.

    Served from the server so the frontend cannot drift from the permissions
    the API actually enforces — the flat list it used to carry had already
    fallen behind by a dozen names, which turned a role save into a 404.
    """
    from ..models.module_catalog import serialize_tree

    return serialize_tree()


def list_privileged_actions():
    """Actions an owner/admin credential can unlock, with prompt wording."""
    from ..models.module_catalog import MUTATION_MODULE, privileged_label

    return [
        {"action": name, "label": privileged_label(name), "module": MUTATION_MODULE[name]}
        for name in sorted(MUTATION_MODULE)
    ]


def _assign_permissions(role_id, permission_names, replace=False):
    perm_map = {p.name: p for p in db.session.scalars(select(Permission)).all()}
    names = list(dict.fromkeys(permission_names))
    unknown = [n for n in names if n not in perm_map]
    if unknown:
        raise NotFoundError(f"Unknown permissions: {', '.join(unknown)}")
    if replace:
        existing = db.session.scalars(
            select(RolePermission).where(RolePermission.role_id == role_id)
        ).all()
        for rp in existing:
            db.session.delete(rp)
        # The deletes have to reach the database before the inserts below.
        # `role_permissions` is unique on (role_id, permission_id), so a save
        # that keeps any permission the role already had — which is what
        # unticking one module box does — would otherwise collide with the rows
        # queued for deletion and fail the whole save.
        db.session.flush()
    for name in names:
        db.session.add(RolePermission(role_id=role_id, permission_id=perm_map[name].id))
    db.session.flush()


def list_memberships(business_id, pagination=None):
    from ..utils.pagination import paginate

    query = (
        select(BusinessUser, Role, User)
        .outerjoin(Role, BusinessUser.role_id == Role.id)
        .outerjoin(User, BusinessUser.user_id == User.id)
        .where(BusinessUser.business_id == business_id)
        .order_by(BusinessUser.created_at.desc())
    )
    page = (pagination or _default_page()).page
    per_page = (pagination or _default_page()).per_page
    rows, total, pages = paginate(query, page, per_page)
    return [_membership_dto(m, u, r) for m, r, u in rows], total, pages


def _default_page():
    from ..schemas import PaginationSchema

    return PaginationSchema()


def list_permissions():
    perms = db.session.scalars(select(Permission).order_by(Permission.resource, Permission.action)).all()
    return [p.to_dict() for p in perms]


def update_membership(business_id, membership_id, data, actor_id):
    membership = db.session.get(BusinessUser, membership_id)
    if membership is None or membership.business_id != business_id:
        raise NotFoundError("Membership not found")
    if data.role_id is not None:
        _role_of_business(data.role_id, business_id)
        membership.role_id = data.role_id
    if data.branch_id is not None:
        membership.branch_id = data.branch_id
    if data.is_active is not None:
        membership.is_active = data.is_active
    _apply_compensation(membership, data)
    membership.updated_by = actor_id
    commit()
    user = db.session.get(User, membership.user_id)
    role = db.session.get(Role, membership.role_id)
    return _membership_dto(membership, user, role)
