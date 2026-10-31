"""Owner/admin re-authentication for actions a lesser role may not take alone.

Some operations are safe for a till operator to *ask* for but not to perform
unattended — removing a product from the catalog is the case this exists for, and
an owner can extend the same treatment to any module they choose in Roles.
Hiding the button in the UI is not an answer to that: the API is reachable
directly, so the decision has to be made server-side on every call.

The flow is deliberately two steps rather than "send the password with the
delete":

1. ``POST /api/iam/privileged-authorizations`` exchanges an owner/admin password
   or PIN for a short-lived signed token scoped to one action and one business.
2. The destructive call presents that token in ``X-Privileged-Token``.

The credential is checked against every active owner/admin membership of the
business, so the operator does not have to know *which* account to type, and it
is never stored, logged or carried on the request that does the damage.
"""

from datetime import timedelta

from sqlalchemy import select

from ..exceptions import AuthError, ForbiddenError
from ..extensions import db
from ..models import BusinessUser, Role, User
from ..utils.security import verify_password

# Roles whose credential unlocks a privileged action. Matched by role name
# because these are the seeded system roles an owner can count on existing; a
# business that renamed or deleted them simply has nobody who can unlock the
# action, which is the safe direction to fail in.
PRIVILEGED_ROLE_NAMES = ("Business Owner", "Admin")

# Long enough to type a password and finish the form it was asked for, short
# enough that a token left on a shared till machine is worthless by the time
# anyone goes looking for it.
AUTHORIZATION_TTL = timedelta(minutes=5)


def privileged_members(business_id):
    """Active owner/admin memberships of this business, with their users."""
    return db.session.execute(
        select(User)
        .join(BusinessUser, BusinessUser.user_id == User.id)
        .join(Role, BusinessUser.role_id == Role.id)
        .where(
            BusinessUser.business_id == business_id,
            BusinessUser.is_active.is_(True),
            BusinessUser.user_id.is_not(None),
            Role.business_id == business_id,
            Role.is_active.is_(True),
            Role.name.in_(PRIVILEGED_ROLE_NAMES),
            User.is_active.is_(True),
        )
    ).scalars().all()


def verify_privileged_credentials(business_id, password=None, pin=None):
    """Return the owner/admin whose password or PIN was supplied, or raise.

    Every candidate is checked against both of its credential hashes rather
    than stopping at the first match, so the time taken does not reveal how far
    down the list a correct credential sits — or whether the approver happened
    to be the first owner in the table.

    A PIN is deliberately accepted alongside the password: on a busy counter the
    owner should not have to type a full password on a shared keyboard to sign
    off a price change. It is a weaker second signature, which is why the audit
    entry records which one was used.
    """
    candidates = privileged_members(business_id)
    matched = None
    for user in candidates:
        if password and verify_password(password, user.password_hash):
            matched = user
        if pin and user.pin_hash and verify_password(pin, user.pin_hash):
            matched = user
    if matched is None:
        # 401, not 403: the supplied credential is what failed, and saying so
        # is what lets the client re-prompt instead of showing a dead end.
        raise AuthError("Incorrect owner or admin password or PIN")
    return matched


def verify_privileged_password(business_id, password):
    """Password-only variant, kept for callers that do not offer a PIN field."""
    return verify_privileged_credentials(business_id, password=password)


def issue_authorization(business_id, user, action):
    """Mint a short-lived token authorising *user* to perform *action*."""
    from .token_service import create_privileged_token

    return create_privileged_token(user.id, business_id, action, AUTHORIZATION_TTL)


def _decode(token, business_id, action):
    from .token_service import decode_privileged_token

    try:
        claims = decode_privileged_token(token)
    except AuthError as exc:
        # A token that will not parse, or that is an access token rather than an
        # approval, is a failed authorization rather than a failed sign-in.
        # Answering 401 here would send the client off to refresh a session that
        # was never the problem.
        raise ForbiddenError("Invalid or expired approval") from exc
    if str(claims.get("bid")) != str(business_id):
        # A token minted for another tenant must not authorise anything here.
        raise ForbiddenError("Authorization does not apply to this business")
    if claims.get("act") != action:
        raise ForbiddenError("Authorization was issued for a different action")
    return claims


def require_privileged_authorization(business_id, action):
    """Authorise *action* on the current request, or raise ``ForbiddenError``.

    Returns the id of the owner/admin who unlocked it, for the audit trail.
    Call this only where the caller's own permission is not enough: an owner or
    admin who already holds ``item.delete`` never reaches it.
    """
    from flask import request

    token = request.headers.get("X-Privileged-Token")
    if not token:
        from ..models.module_catalog import privileged_label

        raise ForbiddenError(
            "This action needs a business owner or admin to approve it",
            payload={
                "requires_privileged_authorization": True,
                "action": action,
                "action_label": privileged_label(action),
            },
        )
    return _decode(token, business_id, action).get("sub")
