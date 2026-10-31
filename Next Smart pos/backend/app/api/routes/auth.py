from flask import Blueprint, current_app, g, request
from sqlalchemy import select
from werkzeug.security import generate_password_hash

from ...exceptions import AuthError, ConflictError
from ...extensions import db, limiter
from ...models import User
from ...models.business import Business
from ...models.iam import BusinessUser, Role
from ...models.module_catalog import modules_for_permissions
from ...schemas import (
    ChangePasswordSchema,
    ForgotPasswordSchema,
    LoginSchema,
    RegisterSchema,
    ResetPasswordSchema,
    SetupPinSchema,
    SuperAdminLoginSchema,
    SwitchBusinessSchema,
    UpdateProfileSchema,
)
from ...services import auth_service
from ...services.subscription_service import public_plan_catalogue, trial_info
from ...validation import validate_body
from ..permissions import login_required
from ..responses import (
    clear_refresh_cookie,
    created,
    ok,
    set_refresh_cookie,
    unauthorized,
)

auth_bp = Blueprint("auth", __name__)


def _limit(config_key, fallback):
    def resolver():
        return current_app.config.get(config_key, fallback)

    return resolver


REGISTER_LIMIT = _limit("REGISTER_RATELIMIT", "10 per minute")
LOGIN_LIMIT = _limit("LOGIN_RATELIMIT", "20 per minute")
REFRESH_LIMIT = _limit("REFRESH_RATELIMIT", "30 per minute")
FORGOT_PASSWORD_LIMIT = _limit("FORGOT_PASSWORD_RATELIMIT", "10 per minute")
AUTH_WRITE_LIMIT = _limit("AUTH_RATELIMIT", "30 per minute")


@auth_bp.route("/plans", methods=["GET"])
def public_plans():
    """Plans a prospective signup can choose from.

    Unauthenticated on purpose: the signup form has to render the tier list
    before anyone has an account. Only marketing fields are exposed.
    """
    return ok({"plans": public_plan_catalogue()})


@auth_bp.route("/register", methods=["POST"])
@validate_body(RegisterSchema)
@limiter.limit(REGISTER_LIMIT)
def register():
    data = request.validated_body
    try:
        business, user, membership, _head, _subscription = auth_service.register(data)
        access, refresh = auth_service.issue_tokens(user, business.id)
        db.session.commit()
    except (ConflictError, AuthError):
        db.session.rollback()
        raise
    except Exception:
        db.session.rollback()
        raise
    resp = created(
        {
            "access_token": access,
            "refresh_token": refresh,
            "user": user.to_dict(),
            "business": business.to_dict(),
            "role": membership.role.name if membership.role else None,
            "subscription": trial_info(business.id),
            "redirect_to": "/",
        }
    )
    set_refresh_cookie(resp, refresh)
    return resp


@auth_bp.route("/login", methods=["POST"])
@validate_body(LoginSchema)
@limiter.limit(LOGIN_LIMIT)
def login():
    data = request.validated_body
    if data.pin:
        user = auth_service.authenticate_with_pin(data.identifier, data.pin)
    else:
        user = auth_service.authenticate(data.identifier, data.password)
    if user is None:
        raise AuthError("Invalid credentials")
    membership = auth_service.default_business(user)
    if membership is None:
        raise AuthError("No active business membership found")
    access, refresh = auth_service.issue_tokens(user, membership.business_id)
    db.session.commit()
    business = membership.business
    resp = ok(
        {
            "access_token": access,
            "refresh_token": refresh,
            "user": user.to_dict(),
            "business": business.to_dict() if business else None,
            "role": membership.role.name if membership.role else None,
            "redirect_to": "/",
            "memberships": auth_service.user_businesses(user),
        }
    )
    set_refresh_cookie(resp, refresh)
    return resp


@auth_bp.route("/superadmin/login", methods=["POST"])
@validate_body(SuperAdminLoginSchema)
@limiter.limit(LOGIN_LIMIT)
def superadmin_login():
    data = request.validated_body
    # PIN sign-in is the short form; the password is the fallback. Only the
    # branch that was actually used is tried, so a password never gets
    # hash-checked against a PIN submission and vice versa.
    if data.pin:
        user = auth_service.authenticate_with_pin(data.identifier, data.pin)
    else:
        user = auth_service.authenticate(data.identifier, data.password)
    if user is None or not user.is_superadmin:
        raise AuthError("Invalid superadmin credentials")
    if not user.is_active:
        raise AuthError("Account is disabled")
    # Return the configured superadmin API token for cross-tenant access
    superadmin_token = current_app.config.get("SUPERADMIN_API_TOKEN")
    resp = ok(
        {
            "superadmin_token": superadmin_token,
            "user": user.to_dict(),
            "is_superadmin": True,
            "signed_in_with": "pin" if data.pin else "password",
        }
    )
    return resp


@auth_bp.route("/logout", methods=["POST"])
def logout():
    token = request.cookies.get("refresh_token")
    if token:
        try:
            auth_service.revoke_refresh_token(token)
        except Exception:
            current_app.logger.warning("Failed to revoke refresh token on logout")
    resp = ok({"message": "Logged Out"})
    clear_refresh_cookie(resp)
    return resp


@auth_bp.route("/refresh", methods=["POST"])
@limiter.limit(REFRESH_LIMIT)
def refresh():
    token = request.cookies.get("refresh_token")
    if not token:
        return unauthorized()
    access, new_refresh = auth_service.refresh_access(token)
    resp = ok({"access_token": access})
    set_refresh_cookie(resp, new_refresh)
    return resp


@auth_bp.route("/me", methods=["GET"])
@login_required
def me():
    ctx = g.ctx
    return ok(
        {
            "user": ctx.user.to_dict(),
            "business": ctx.business.to_dict() if ctx.business else None,
            "role": ctx.role.name,
            "permissions": sorted(ctx.permissions),
            # The modules this role was actually given, derived from the same
            # catalog the Roles editor renders. The client uses this to show a
            # person only the screens they were allocated rather than guessing
            # from permission names.
            "role_modules": modules_for_permissions(ctx.permissions),
            "subscription_status": ctx.subscription_status,
            "subscription_plan": ctx.subscription_plan,
            "subscription": trial_info(ctx.business_id),
            "module_access": list(ctx.module_access),
            "is_owner": ctx.role.name == "Business Owner",
            "memberships": auth_service.user_businesses(ctx.user),
        }
    )


@auth_bp.route("/businesses", methods=["GET"])
@login_required
def list_businesses():
    return ok({"businesses": auth_service.user_businesses(g.ctx.user)})


@auth_bp.route("/switch-business", methods=["POST"])
@login_required
@validate_body(SwitchBusinessSchema)
@limiter.limit(AUTH_WRITE_LIMIT)
def switch_business():
    data = request.validated_body
    access, refresh, membership = auth_service.switch_business(g.ctx.user, data.business_id)
    db.session.commit()
    business = membership.business
    resp = ok(
        {
            "access_token": access,
            "refresh_token": refresh,
            "business": business.to_dict() if business else None,
            "role": membership.role.name if membership.role else None,
        }
    )
    set_refresh_cookie(resp, refresh)
    return resp


@auth_bp.route("/forgot-password", methods=["POST"])
@validate_body(ForgotPasswordSchema)
@limiter.limit(FORGOT_PASSWORD_LIMIT)
def forgot_password():
    data = request.validated_body
    user = db.session.scalar(select(User).where(User.email == data.email))
    message = "If that account exists, a reset link has been sent"
    if user is not None and user.is_active:
        raw_token = auth_service.create_password_reset(user)
        current_app.logger.info("Password reset token issued for %s", user.email)
        if current_app.config.get("DEBUG"):
            db.session.rollback()
            return ok(
                {
                    "message": message,
                    "dev_reset_token": raw_token,
                    "user_id": str(user.id),
                }
            )
        db.session.commit()
    return ok({"message": message})


@auth_bp.route("/reset-password", methods=["POST"])
@validate_body(ResetPasswordSchema)
@limiter.limit(AUTH_WRITE_LIMIT)
def reset_password():
    data = request.validated_body
    auth_service.consume_password_reset(data.token, data.password)
    return ok({"message": "Password has been reset"})


@auth_bp.route("/change-password", methods=["POST"])
@login_required
@validate_body(ChangePasswordSchema)
@limiter.limit(AUTH_WRITE_LIMIT)
def change_password():
    data = request.validated_body
    user = g.ctx.user
    if not auth_service.authenticate(user.email, data.old_password):
        raise AuthError("Current password is incorrect")
    user.password_hash = generate_password_hash(data.new_password, method="scrypt")
    db.session.commit()
    return ok({"message": "Password updated"})


@auth_bp.route("/profile", methods=["PATCH"])
@login_required
@validate_body(UpdateProfileSchema)
def update_profile():
    data = request.validated_body
    user = g.ctx.user
    if data.username is not None and data.username != user.username:
        # Raises ConflictError when the handle already belongs to someone else.
        auth_service.set_username(user, data.username)
    for field in ("first_name", "last_name", "phone"):
        value = getattr(data, field)
        if value is not None:
            setattr(user, field, value)
    db.session.commit()
    return ok({"user": user.to_dict()})


@auth_bp.route("/setup-pin", methods=["POST"])
@login_required
@validate_body(SetupPinSchema)
@limiter.limit(AUTH_WRITE_LIMIT)
def setup_pin():
    data = request.validated_body
    user = g.ctx.user
    # Re-authenticate before changing the PIN so a hijacked access token alone
    # cannot silently take over the short-form sign-in.
    if not auth_service.authenticate(user.email, data.current_password):
        raise AuthError("Current password is incorrect")
    auth_service.set_pin(user, data.pin)
    return ok({"message": "Sign-in PIN has been set", "user": user.to_dict()})


@auth_bp.route("/setup-pin", methods=["DELETE"])
@login_required
@limiter.limit(AUTH_WRITE_LIMIT)
def clear_pin():
    auth_service.clear_pin(g.ctx.user)
    return ok({"message": "Sign-in PIN has been removed", "user": g.ctx.user.to_dict()})
