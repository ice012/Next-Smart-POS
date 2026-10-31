from flask import Blueprint, g, request

from ...schemas import (
    ItemSchema, ItemUpdateSchema, ItemSearchSchema,
    CategorySchema, CategoryUpdateSchema,
    UOMSchema, UOMUpdateSchema, PaginationSchema,
)
from ...services.item_service import (
    list_items as list_items_service,
    get_item as get_item_service,
    create_item as create_item_service,
    update_item as update_item_service,
    delete_item as delete_item_service,
    set_item_image as set_item_image_service,
    search_items as search_items_service,
    list_categories as list_categories_service,
    get_category as get_category_service,
    create_category as create_category_service,
    update_category as update_category_service,
    delete_category as delete_category_service,
    list_uoms as list_uoms_service,
    get_uom as get_uom_service,
    create_uom as create_uom_service,
    update_uom as update_uom_service,
    delete_uom as delete_uom_service,
)
from ...utils.security import parse_uuid
from ...utils.uploads import delete_image, save_image
from ...validation import validate_body, validate_query
from ..permissions import gate_module, login_required, require_permission, require_permission_or_privileged
from ..responses import no_content, ok, created

items_bp = Blueprint("items", __name__)
items_bp.before_request(gate_module("items"))


def _business_id():
    return g.ctx.business_id


# ==================== Items ====================

@items_bp.route("", methods=["GET"])
@login_required
@require_permission("item.read")
@validate_query(ItemSearchSchema)
def list_items():
    pagination = request.validated_query
    items, total, pages = list_items_service(
        _business_id(), pagination,
        item_type=pagination.type,
        category_id=pagination.category_id,
        is_active=pagination.is_active,
    )
    return ok(
        {"items": items},
        meta={"page": pagination.page, "per_page": pagination.per_page, "total": total, "pages": pages},
    )


@items_bp.route("/search", methods=["GET"])
@login_required
@require_permission("item.read")
def search_items():
    # A miss is an empty result, not a 404: see item_service.search_items.
    q = request.args.get("q", "")
    try:
        limit = max(1, min(int(request.args.get("limit", "20") or "20"), 100))
    except ValueError:
        limit = 20
    return ok({"items": search_items_service(_business_id(), q, limit)})


@items_bp.route("", methods=["POST"])
@login_required
@require_permission("item.write")
@validate_body(ItemSchema)
def create_item():
    item = create_item_service(_business_id(), request.validated_body, g.ctx.user_id)
    return created({"item": item})


@items_bp.route("/<item_id>", methods=["GET"])
@login_required
@require_permission("item.read")
def get_item(item_id):
    item = get_item_service(_business_id(), parse_uuid(item_id))
    return ok({"item": item})


@items_bp.route("/<item_id>", methods=["PATCH"])
@login_required
@require_permission("item.write")
@validate_body(ItemUpdateSchema)
def update_item(item_id):
    item = update_item_service(_business_id(), parse_uuid(item_id), request.validated_body, g.ctx.user_id)
    return ok({"item": item})


@items_bp.route("/<item_id>", methods=["DELETE"])
@login_required
@require_permission_or_privileged("item.delete", action="item.delete")
def delete_item(item_id):
    # `g.privileged_approver_id` is set only when the caller's own role could
    # not delete, and is recorded so the trail shows whose password was used.
    previous_image = delete_item_service(
        _business_id(), parse_uuid(item_id), approved_by=g.privileged_approver_id
    )
    delete_image(previous_image, "items")
    return no_content()


@items_bp.route("/<item_id>/image", methods=["POST"])
@login_required
@require_permission("item.write")
def upload_item_image(item_id):
    # Persist the file first: a rejected upload should not leave the item
    # pointing at an image that was never written.
    image_url = save_image(request.files.get("image"), "items", field_label="image")
    item, previous = set_item_image_service(
        _business_id(), parse_uuid(item_id), image_url, g.ctx.user_id
    )
    delete_image(previous, "items")
    return ok({"item": item, "image_url": image_url})


@items_bp.route("/<item_id>/image", methods=["DELETE"])
@login_required
@require_permission("item.write")
def delete_item_image(item_id):
    item, previous = set_item_image_service(
        _business_id(), parse_uuid(item_id), None, g.ctx.user_id
    )
    delete_image(previous, "items")
    return ok({"item": item, "image_url": None})


# ==================== Categories ====================

@items_bp.route("/categories", methods=["GET"])
@login_required
@require_permission("category.read")
def list_categories():
    categories = list_categories_service(_business_id())
    return ok({"categories": categories})


@items_bp.route("/categories", methods=["POST"])
@login_required
@require_permission("category.write")
@validate_body(CategorySchema)
def create_category():
    cat = create_category_service(_business_id(), request.validated_body, g.ctx.user_id)
    return created({"category": cat})


@items_bp.route("/categories/<category_id>", methods=["GET"])
@login_required
@require_permission("category.read")
def get_category(category_id):
    cat = get_category_service(_business_id(), parse_uuid(category_id))
    return ok({"category": cat})


@items_bp.route("/categories/<category_id>", methods=["PATCH"])
@login_required
@require_permission("category.write")
@validate_body(CategoryUpdateSchema)
def update_category(category_id):
    cat = update_category_service(_business_id(), parse_uuid(category_id), request.validated_body, g.ctx.user_id)
    return ok({"category": cat})


@items_bp.route("/categories/<category_id>", methods=["DELETE"])
@login_required
@require_permission("category.delete")
def delete_category(category_id):
    delete_category_service(_business_id(), parse_uuid(category_id))
    return no_content()


# ==================== Units of Measure ====================

@items_bp.route("/uoms", methods=["GET"])
@login_required
@require_permission("uom.read")
def list_uoms():
    uoms = list_uoms_service(_business_id())
    return ok({"uoms": uoms})


@items_bp.route("/uoms", methods=["POST"])
@login_required
@require_permission("uom.write")
@validate_body(UOMSchema)
def create_uom():
    uom = create_uom_service(_business_id(), request.validated_body, g.ctx.user_id)
    return created({"uom": uom})


@items_bp.route("/uoms/<uom_id>", methods=["GET"])
@login_required
@require_permission("uom.read")
def get_uom(uom_id):
    uom = get_uom_service(_business_id(), parse_uuid(uom_id))
    return ok({"uom": uom})


@items_bp.route("/uoms/<uom_id>", methods=["PATCH"])
@login_required
@require_permission("uom.write")
@validate_body(UOMUpdateSchema)
def update_uom(uom_id):
    uom = update_uom_service(_business_id(), parse_uuid(uom_id), request.validated_body, g.ctx.user_id)
    return ok({"uom": uom})


@items_bp.route("/uoms/<uom_id>", methods=["DELETE"])
@login_required
@require_permission("uom.delete")
def delete_uom(uom_id):
    delete_uom_service(_business_id(), parse_uuid(uom_id))
    return no_content()
