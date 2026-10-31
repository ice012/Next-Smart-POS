from flask import Blueprint, g, request

from ...schemas import (
    PurchaseOrderCancelSchema,
    PurchaseOrderSchema,
    PurchaseOrderSearchSchema,
    PurchaseOrderStatusSchema,
    PurchaseOrderUpdateSchema,
    PurchaseReceiveSchema,
)
from ...services.purchase_service import (
    cancel_purchase_order as cancel_purchase_order_service,
    create_purchase_order as create_purchase_order_service,
    delete_purchase_order as delete_purchase_order_service,
    get_purchase_order as get_purchase_order_service,
    list_purchase_orders as list_purchase_orders_service,
    receive_purchase_order as receive_purchase_order_service,
    update_purchase_order as update_purchase_order_service,
)
from ...utils.security import parse_uuid
from ...validation import validate_body, validate_query
from ..permissions import login_required, require_module, require_permission
from ..responses import created, no_content, ok

purchases_bp = Blueprint("purchases", __name__)


def _business_id():
    return g.ctx.business_id


@purchases_bp.route("", methods=["GET"])
@login_required
@require_module("purchases")
@require_permission("purchase.read")
@validate_query(PurchaseOrderSearchSchema)
def list_purchase_orders_route():
    q = request.validated_query
    items, total, pages = list_purchase_orders_service(
        _business_id(), q,
        status=q.status, supplier_id=q.supplier_id,
        from_date=q.from_date, to_date=q.to_date, q=q.q,
    )
    return ok(
        {"purchase_orders": items},
        meta={"page": q.page, "per_page": q.per_page, "total": total, "pages": pages},
    )


@purchases_bp.route("", methods=["POST"])
@login_required
@require_module("purchases")
@require_permission("purchase.write")
@validate_body(PurchaseOrderSchema)
def create_purchase_order_route():
    order = create_purchase_order_service(_business_id(), request.validated_body, g.ctx.user_id)
    return created({"purchase_order": order})


@purchases_bp.route("/<order_id>", methods=["GET"])
@login_required
@require_module("purchases")
@require_permission("purchase.read")
def get_purchase_order_route(order_id):
    return ok({"purchase_order": get_purchase_order_service(_business_id(), parse_uuid(order_id))})


@purchases_bp.route("/<order_id>", methods=["PATCH"])
@login_required
@require_module("purchases")
@require_permission("purchase.write")
@validate_body(PurchaseOrderUpdateSchema)
def update_purchase_order_route(order_id):
    order = update_purchase_order_service(
        _business_id(), parse_uuid(order_id), request.validated_body, g.ctx.user_id
    )
    return ok({"purchase_order": order})


@purchases_bp.route("/<order_id>/status", methods=["POST"])
@login_required
@require_module("purchases")
@require_permission("purchase.write")
@validate_body(PurchaseOrderStatusSchema)
def set_purchase_order_status_route(order_id):
    order = update_purchase_order_service(
        _business_id(), parse_uuid(order_id),
        PurchaseOrderStatusSchema(status=request.validated_body.status),
        g.ctx.user_id,
    )
    return ok({"purchase_order": order})


@purchases_bp.route("/<order_id>/receive", methods=["POST"])
@login_required
@require_module("purchases")
@require_permission("purchase.write")
@validate_body(PurchaseReceiveSchema)
def receive_purchase_order_route(order_id):
    order = receive_purchase_order_service(
        _business_id(), parse_uuid(order_id), request.validated_body, g.ctx.user_id
    )
    return ok({"purchase_order": order})


@purchases_bp.route("/<order_id>/cancel", methods=["POST"])
@login_required
@require_module("purchases")
@require_permission("purchase.write")
@validate_body(PurchaseOrderCancelSchema)
def cancel_purchase_order_route(order_id):
    order = cancel_purchase_order_service(
        _business_id(), parse_uuid(order_id), g.ctx.user_id,
        reason=request.validated_body.reason,
    )
    return ok({"purchase_order": order})


@purchases_bp.route("/<order_id>", methods=["DELETE"])
@login_required
@require_module("purchases")
@require_permission("purchase.write")
def delete_purchase_order_route(order_id):
    delete_purchase_order_service(_business_id(), parse_uuid(order_id), g.ctx.user_id)
    return no_content()
