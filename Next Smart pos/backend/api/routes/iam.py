from flask import Blueprint, current_app, g, request

from ...exceptions import ValidationError
from ...extensions import db, limiter
from ...schemas import (
    MembershipPatchSchema,
    PaginationSchema,
    PrivilegedAuthorizationSchema,
    RolePatchSchema,
    RoleSchema,
    UserInviteSchema,
    UserUpdateSchema,
)
from ...services import iam_service, privileged_service
from ...services.audit_log_service import log_event
from ...utils.security import parse_uuid
from ...validation import validate_body, validate_query
from ..permissions import require_permission
from ..responses import no_content, ok, created

iam_bp = Blueprint("iam", __name__)
# Deliberately not module-gated: team management is how an owner runs their own
# account, and roles/permissions bootstrap from it. Team *size* is capped by the
# plan's max_users instead.


@iam_bp.route("/privileged-authorizations", methods=["POST"])
@require_permission("profile.read")
@validate_body(PrivilegedAuthorizationSchema)
@limiter.limit(lambda: current_app.config.get("PRIVILEGED_AUTH_RATELIMIT", "10 per minute"))
def create_privileged_authorization():
    """Exchange an owner/admin password or PIN for a token scoped to one action.

    The streamlined roles can key in products but not remove them, and hiding
    the delete button is not an answer: the API is reachable directly. This is
    where a lesser role comes to get the authority it needs, having asked a
    manager to unlock it.
    """
    from ...models.module_catalog import MUTATION_MODULE, privileged_label

    data = request.validated_body
    # Only mutations, and only ones the catalog has thought about, can be
    # unlocked. A client cannot mint a token for a read, or invent an action.
    if data.action not in MUTATION_MODULE:
        raise ValidationError("That action cannot be authorized this way")

    business_id = g.ctx.business_id
    approver = privileged_service.verify_privileged_credentials(
        business_id, data.password, data.pin
    )
    token = privileged_service.issue_authorization(business_id, approver, data.action)

    log_event(
        business_id,
        "authorize",
        data.action.split(".")[0],
        g.ctx.user_id,
        None,
        {
            "action": data.action,
            "action_label": privileged_label(data.action),
            "approved_by": approver.email,
            # Recorded so an auditor can tell a 4-digit unlock from a full
            # password re-entry; a PIN is a much weaker second signature.
            "via": "pin" if data.pin else "password",
        },
    )
    # log_event only flushes, and this route owns the whole transaction, so the
    # authorization is committed here rather than by some later write.
    db.session.commit()
    return ok(
        {
            "token": token,
            "action": data.action,
            "approved_by": approver.email,
            "expires_in": int(privileged_service.AUTHORIZATION_TTL.total_seconds()),
        }
    )


@iam_bp.route("/users", methods=["GET"])
@require_permission("user.read")
@validate_query(PaginationSchema)
def list_users():
    items, total, pages = iam_service.list_users(
        g.ctx.business_id, request.validated_query
    )
    return ok(
        {"users": items},
        meta={
            "page": request.validated_query.page,
            "per_page": request.validated_query.per_page,
            "total": total,
            "pages": pages,
        },
    )


@iam_bp.route("/users", methods=["POST"])
@require_permission("user.write")
@validate_body(UserInviteSchema)
def invite_user():
    member, reset_token = iam_service.invite_user(
        g.ctx.business_id, request.validated_body, g.ctx.user_id
    )
    payload = {"membership": member}
    if reset_token:
        payload["dev_reset_token"] = reset_token
    return created(payload)


@iam_bp.route("/users/<user_id>", methods=["GET"])
@require_permission("user.read")
def get_user(user_id):
    member = iam_service.get_user(g.ctx.business_id, parse_uuid(user_id))
    return ok({"user": member})


@iam_bp.route("/users/<user_id>", methods=["PATCH"])
@require_permission("user.write")
@validate_body(UserUpdateSchema)
def update_user(user_id):
    member = iam_service.update_user(
        g.ctx.business_id, parse_uuid(user_id), request.validated_body, g.ctx.user_id
    )
    return ok({"user": member})


@iam_bp.route("/users/<user_id>", methods=["DELETE"])
@require_permission("user.delete")
def delete_user(user_id):
    iam_service.delete_user(g.ctx.business_id, parse_uuid(user_id))
    return no_content()


@iam_bp.route("/roles", methods=["GET"])
@require_permission("role.read")
def list_roles():
    return ok({"roles": iam_service.list_roles(g.ctx.business_id)})


@iam_bp.route("/roles", methods=["POST"])
@require_permission("role.write")
@validate_body(RoleSchema)
def create_role():
    role = iam_service.create_role(
        g.ctx.business_id, request.validated_body, g.ctx.user_id
    )
    return created({"role": role})


@iam_bp.route("/roles/<role_id>", methods=["GET"])
@require_permission("role.read")
def get_role(role_id):
    role = iam_service.get_role(g.ctx.business_id, parse_uuid(role_id))
    return ok({"role": role})


@iam_bp.route("/roles/<role_id>", methods=["PATCH"])
@require_permission("role.write")
@validate_body(RolePatchSchema)
def update_role(role_id):
    role = iam_service.update_role(
        g.ctx.business_id, parse_uuid(role_id), request.validated_body, g.ctx.user_id
    )
    return ok({"role": role})


@iam_bp.route("/roles/<role_id>", methods=["DELETE"])
@require_permission("role.delete")
def delete_role(role_id):
    iam_service.delete_role(
        g.ctx.business_id, parse_uuid(role_id), actor_id=g.ctx.user_id
    )
    return no_content()


@iam_bp.route("/permissions", methods=["GET"])
@require_permission("permission.read")
def list_permissions():
    return ok({"permissions": iam_service.list_permissions()})


@iam_bp.route("/permission-modules", methods=["GET"])
@require_permission("role.read")
def list_permission_modules():
    """The module tree the role editor renders.

    Deliberately gated on ``role.read`` rather than ``permission.read``: someone
    who may see the roles needs to see what those roles are made of, and a role
    that cannot be rendered is a role that cannot be edited safely.
    """
    return ok(
        {
            "groups": iam_service.list_permission_modules(),
            "privileged_actions": iam_service.list_privileged_actions(),
        }
    )


@iam_bp.route("/roles/<role_id>/permissions", methods=["GET"])
@require_permission("role.read")
def get_role_permissions(role_id):
    perms = iam_service.get_role_permissions(
        g.ctx.business_id, parse_uuid(role_id)
    )
    return ok({"permissions": perms})


@iam_bp.route("/roles/<role_id>/permissions", methods=["PATCH"])
@require_permission("role.write")
@validate_body(RolePatchSchema)
def set_role_permissions(role_id):
    role = iam_service.assign_role_permissions(
        g.ctx.business_id,
        parse_uuid(role_id),
        request.validated_body.permission_names or [],
    )
    return ok({"role": role})


@iam_bp.route("/memberships", methods=["GET"])
@require_permission("user.read")
@validate_query(PaginationSchema)
def list_memberships():
    items, total, pages = iam_service.list_memberships(
        g.ctx.business_id, request.validated_query
    )
    return ok(
        {"memberships": items},
        meta={
            "page": request.validated_query.page,
            "per_page": request.validated_query.per_page,
            "total": total,
            "pages": pages,
        },
    )


@iam_bp.route("/memberships/<membership_id>", methods=["PATCH"])
@require_permission("user.write")
@validate_body(MembershipPatchSchema)
def update_membership(membership_id):
    member = iam_service.update_membership(
        g.ctx.business_id, parse_uuid(membership_id), request.validated_body, g.ctx.user_id
    )
    return ok({"membership": member})
