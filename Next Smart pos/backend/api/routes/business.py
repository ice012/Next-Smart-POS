from flask import Blueprint, g, request

from ...schemas import (
    BranchSchema,
    BranchUpdateSchema,
    BusinessConfigPatchSchema,
    BusinessPatchSchema,
    PaginationSchema,
)
from ...services import business_service
from ...utils.security import parse_uuid
from ...utils.uploads import delete_image, save_image
from ...validation import validate_body, validate_query
from ..permissions import gate_module, login_required, require_permission
from ..responses import no_content, ok, created

business_bp = Blueprint("business", __name__)
# Company profile and branches are part of the core platform, so the gate is
# the always-granted "core" module; a suspended tenant is still blocked.
business_bp.before_request(gate_module("core"))


def _business_id():
    return g.ctx.business_id


def _save_logo(file_storage):
    return save_image(
        file_storage, "logos", field_label="logo", max_bytes_key="LOGO_MAX_BYTES"
    )


@business_bp.route("/info", methods=["GET"])
@require_permission("business.read")
def get_business_info():
    return ok({"business": g.ctx.business.to_dict()})


@business_bp.route("/info", methods=["PATCH"])
@require_permission("business.write")
@validate_body(BusinessPatchSchema)
def update_business_info():
    business = business_service.update_business(
        _business_id(), request.validated_body, g.ctx.user_id
    )
    return ok({"business": business.to_dict()})


@business_bp.route("/logo", methods=["POST"])
@require_permission("business.write")
def upload_logo():
    logo_url = _save_logo(request.files.get("logo"))
    business = business_service.update_business(
        _business_id(), {"logo": logo_url}, g.ctx.user_id
    )
    return ok({"business": business.to_dict(), "logo": logo_url})


@business_bp.route("/logo", methods=["DELETE"])
@require_permission("business.write")
def delete_logo():
    previous = g.ctx.business.logo
    business = business_service.update_business(
        _business_id(), {"logo": None}, g.ctx.user_id
    )
    delete_image(previous, "logos")
    return ok({"business": business.to_dict(), "logo": None})


@business_bp.route("/settings", methods=["GET"])
@require_permission("business.read")
def get_settings():
    config = business_service.get_config(_business_id())
    return ok({"config": config.to_dict()})


@business_bp.route("/settings", methods=["PATCH"])
@require_permission("business.write")
@validate_body(BusinessConfigPatchSchema)
def update_settings():
    config = business_service.update_config(
        _business_id(), request.validated_body, g.ctx.user_id
    )
    return ok({"config": config.to_dict()})


@business_bp.route("/branches", methods=["GET"])
@require_permission("branch.read")
@validate_query(PaginationSchema)
def list_branches():
    items, total, pages = business_service.list_branches(
        _business_id(), request.validated_query
    )
    return ok(
        {"branches": [b.to_dict() for b in items]},
        meta={
            "page": request.validated_query.page,
            "per_page": request.validated_query.per_page,
            "total": total,
            "pages": pages,
        },
    )


@business_bp.route("/branches", methods=["POST"])
@require_permission("branch.write")
@validate_body(BranchSchema)
def create_branch():
    branch = business_service.create_branch(
        _business_id(), request.validated_body, g.ctx.user_id
    )
    return created({"branch": branch.to_dict()})


@business_bp.route("/branches/<branch_id>", methods=["GET"])
@require_permission("branch.read")
def get_branch(branch_id):
    branch = business_service.get_branch(_business_id(), parse_uuid(branch_id))
    return ok({"branch": branch.to_dict()})


@business_bp.route("/branches/<branch_id>", methods=["PATCH"])
@require_permission("branch.write")
@validate_body(BranchUpdateSchema)
def update_branch(branch_id):
    branch = business_service.update_branch(
        _business_id(), parse_uuid(branch_id), request.validated_body, g.ctx.user_id
    )
    return ok({"branch": branch.to_dict()})


@business_bp.route("/branches/<branch_id>", methods=["DELETE"])
@require_permission("business.write")
def delete_branch(branch_id):
    business_service.delete_branch(_business_id(), parse_uuid(branch_id))
    return no_content()
