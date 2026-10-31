from functools import wraps

from flask import g, current_app

from .deps import RequestContext, get_current_context
from ..exceptions import ForbiddenError


SUPERADMIN_TOKEN = None


def login_required(func):
    """Require an authenticated user with a valid business membership."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        ctx = get_current_context()
        g.ctx = ctx
        return func(*args, **kwargs)

    return wrapper


def require_permission(*permissions):
    """Require the current user to hold ALL of the given permissions.

    Holding a permission is not always the whole answer. When an owner has
    flagged a module in Roles with "admin required", the write/delete
    permissions inside it stop being self-service: the role still needs the
    permission, and it still needs a manager to unlock it. That check lives
    here rather than on each route, because a per-route check is a per-route
    thing to forget — and forgetting it is silent.
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            ctx = get_current_context()
            missing = [p for p in permissions if p not in ctx.permissions]
            if missing:
                raise ForbiddenError(
                    "Insufficient permissions", payload={"missing": missing}
                )
            gated = next(
                (p for p in permissions if module_requires_approval(ctx, p)), None
            )
            if gated is not None:
                from ..services import privileged_service

                g.privileged_approver_id = (
                    privileged_service.require_privileged_authorization(
                        ctx.business_id, gated
                    )
                )
            g.ctx = ctx
            return func(*args, **kwargs)

        return wrapper

    return decorator


def require_permission_or_privileged(permission, action=None):
    """Require *permission*, or an owner/admin unlock for it.

    Two situations send the request here:

    1. The role simply does not hold the permission — a till operator keying in
       a new product, then needing a manager to stand behind removing one.
    2. The role does hold it, but an owner has flagged the module in Roles with
       "admin required", so even a permitted action is not one to be taken
       unattended.

    The role is not widened in either case; a second signature is asked for.

    ``action`` defaults to the permission name, so the token is minted for
    exactly the thing being authorized and cannot be reused for another.
    Permissions that no catalogued module marks as a mutation cannot be unlocked
    at all — there is nothing to approve, and an approval token that opened a
    read would be one more thing to leak.
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            from ..models.module_catalog import MUTATION_MODULE

            ctx = get_current_context()
            held = permission in ctx.permissions
            if held and not module_requires_approval(ctx, permission):
                g.ctx = ctx
                g.privileged_approver_id = None
                return func(*args, **kwargs)

            from ..services import privileged_service

            if permission not in MUTATION_MODULE:
                # Not unlockable: fall back to a plain permission check so the
                # caller still gets an accurate 403 instead of a misleading
                # "ask a manager" prompt that can never be satisfied.
                raise ForbiddenError(
                    "Insufficient permissions", payload={"missing": [permission]}
                )

            g.privileged_approver_id = privileged_service.require_privileged_authorization(
                ctx.business_id, action or permission
            )
            g.ctx = ctx
            return func(*args, **kwargs)

        return wrapper

    return decorator


def module_requires_approval(ctx, permission):
    """Has an owner put this permission's module behind their credential?

    The flag lives on the *role*, not the business: whether a price change needs
    sign-off is a statement about what that job may do alone, and the same
    business can reasonably want one role to check changes with the owner and
    another not to.
    """
    from ..models.module_catalog import MUTATION_MODULE

    module_code = MUTATION_MODULE.get(permission)
    if module_code is None:
        return False
    flags = getattr(ctx.role, "requires_owner_approval", None) or {}
    return bool(flags.get(module_code))


def gate_module(module_code):
    """Return a ``before_request`` hook that gates a whole blueprint.

    Same tier-2/tier-3 check as :func:`require_module`, applied once per
    blueprint instead of per route so a whole feature area cannot end up with
    one unguarded endpoint.
    """

    def before():
        # A CORS preflight carries no Authorization header, so gating it would
        # answer the browser's OPTIONS with 401 and block the real request.
        # Flask-CORS already handled the preflight by the time we get here.
        from flask import request

        if request.method == "OPTIONS":
            return
        ctx = get_current_context()
        ctx.require_subscribed()
        if module_code not in ctx.module_access:
            raise ForbiddenError(
                "Module not available on this subscription",
                payload={"missing": [module_code]},
            )

    return before


def require_role(*role_names):
    """Require the current user's role to be one of the named system roles."""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            ctx = get_current_context()
            if ctx.role.name not in role_names:
                raise ForbiddenError("Insufficient role")
            g.ctx = ctx
            return func(*args, **kwargs)

        return wrapper

    return decorator


def require_module(*modules):
    """Require an active subscription that grants access to the named module(s).

    Tier-2 (subscription) and tier-3 (module) enforcement.
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            ctx = get_current_context()
            ctx.require_subscribed()
            missing = [m for m in modules if m not in ctx.module_access]
            if missing:
                raise ForbiddenError(
                    "Module not available on this subscription", payload={"missing": missing}
                )
            g.ctx = ctx
            return func(*args, **kwargs)

        return wrapper

    return decorator


def require_superadmin(func):
    """Bypass tenant scoping: require the global Super Admin token.

    The token may be supplied via the ``X-SuperAdmin-Token`` header or a
    ``superadmin_token`` query parameter. It is validated against the app
    config ``SUPERADMIN_API_TOKEN`` (constant-time comparison).
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        from flask import request

        expected = current_app.config.get("SUPERADMIN_API_TOKEN")
        if not expected:
            raise ForbiddenError("Super Admin access is not configured")
        provided = (
            request.headers.get("X-SuperAdmin-Token")
            or request.args.get("superadmin_token")
        )
        from hmac import compare_digest

        if not provided or not compare_digest(str(provided), str(expected)):
            raise ForbiddenError("Invalid Super Admin token")
        g.is_superadmin = True
        return func(*args, **kwargs)

    return wrapper


def ctx() -> RequestContext:
    """Helper for handlers to access the parsed request context."""
    return g.ctx
