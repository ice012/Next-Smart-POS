import uuid as uuidlib
from datetime import datetime, timezone

from sqlalchemy import select

from ..exceptions import AuthError, ForbiddenError, AppError
from ..extensions import db
from ..models.business import Business
from ..models.identity import User
from ..models.iam import BusinessUser, Role, Permission, RolePermission, hash_token


class RequestContext:
    """Server-side derived request context.

    The ``business_id`` is taken from the signed access token (set by the
    server at login/switch) and then re-validated against an active membership,
    so a client cannot operate on a business it does not belong to.

    Three-tier auth:
      1. Permissions (role)        — ``self.permissions``
      2. Subscription status        — ``self.subscription_status``
      3. Module access              — ``self.module_access`` (resolved from the plan)
    """

    def __init__(self, user, business, business_id, membership, role, permissions,
                 subscription_status=None, subscription_plan=None, module_access=None):
        self.user = user
        self.business = business
        self.business_id = business_id
        self.membership = membership
        self.role = role
        self.permissions = permissions
        # --- Subscription / module access (tier 2 + 3) ---
        self.subscription_status = subscription_status
        self.subscription_plan = subscription_plan or getattr(business, "subscription_tier", None)
        self.module_access = module_access or []

    @property
    def user_id(self):
        return self.user.id

    @property
    def is_subscribed(self) -> bool:
        return self.subscription_status == "active"

    def can_access(self, module_name: str) -> bool:
        return module_name in self.module_access

    def require_subscribed(self):
        if not self.is_subscribed:
            raise ForbiddenError("Subscription is not active")

    def require_module(self, module_name: str):
        self.require_subscribed()
        if module_name not in self.module_access:
            raise ForbiddenError(f"Module '{module_name}' is not available on this plan")



def get_bearer_token():
    from flask import request

    auth = request.headers.get("Authorization", "")
    if not auth:
        raise AuthError("Missing authorization header")
    parts = auth.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise AuthError("Invalid authorization header")
    return parts[1]


def _role_permissions(role_id):
    rows = db.session.execute(
        select(Permission.name)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .where(RolePermission.role_id == role_id)
    )
    return {row.name for row in rows}


def get_current_context():
    from flask import g

    if getattr(g, "ctx", None) is not None:
        return g.ctx

    from ..services.token_service import decode_access_token

    token = get_bearer_token()
    try:
        claims = decode_access_token(token)
    except AppError:
        raise AuthError("Invalid or expired token")
    if claims.get("type") != "access":
        raise AuthError("Invalid token type")

    try:
        user_id = uuidlib.UUID(claims["sub"])
        business_id = uuidlib.UUID(claims["bid"])
    except (KeyError, ValueError):
        raise AuthError("Invalid token claims")

    user = db.session.get(User, user_id)
    if user is None or not user.is_active:
        raise ForbiddenError("Account is disabled")

    membership = db.session.scalar(
        select(BusinessUser).where(
            BusinessUser.business_id == business_id,
            BusinessUser.user_id == user.id,
            BusinessUser.is_active.is_(True),
        )
    )
    if membership is None:
        raise ForbiddenError("Business context invalid for user")

    role = db.session.get(Role, membership.role_id)
    if role is None or not role.is_active:
        raise ForbiddenError("Role unavailable")

    business = db.session.get(Business, business_id)
    permissions = _role_permissions(role.id)

    # --- Tier 2 + 3: subscription status + module access ---
    from ..services.subscription_service import (
        is_subscription_active, accessible_modules,
    )
    sub_active = is_subscription_active(business_id)
    sub_status = "active" if sub_active else (
        getattr(business, "subscription_status", None) or "inactive"
    )
    module_access = accessible_modules(business_id) or []

    # Expire access tokens for tenants whose subscription has lapsed: even a
    # still-valid short-lived JWT must be rejected so existing sessions cannot
    # continue to operate after the plan expires.
    if not sub_active:
        raise ForbiddenError("Subscription is not active")

    ctx = RequestContext(
        user=user,
        business=business,
        business_id=business_id,
        membership=membership,
        role=role,
        permissions=permissions,
        subscription_status=sub_status,
        subscription_plan=getattr(business, "subscription_tier", None),
        module_access=module_access,
    )
    g.ctx = ctx
    return ctx
