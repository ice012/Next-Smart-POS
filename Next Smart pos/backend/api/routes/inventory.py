from flask import Blueprint, g, request

from ...schemas import (
    WarehouseSchema, WarehouseUpdateSchema,
    InventoryBalanceSearchSchema, StockAdjustmentSchema, StockMovementSearchSchema,
)
from ...services.inventory_service import (
    get_inventory_balance as get_balance_service,
    list_inventory_balances as list_balances_service,
    adjust_stock as adjust_stock_service,
    stock_in as stock_in_service,
    stock_out as stock_out_service,
    get_stock_history as get_stock_history_service,
    get_low_stock_alerts as get_low_stock_alerts_service,
    get_default_warehouse as get_default_warehouse_service,
    get_warehouse as get_warehouse_service,
    list_warehouses as list_warehouses_service,
    create_warehouse as create_warehouse_service,
    update_warehouse as update_warehouse_service,
    delete_warehouse as delete_warehouse_service,
)
from ...utils.security import parse_uuid
from ...validation import validate_body, validate_query
from ..permissions import login_required, require_module, require_permission, require_permission
from ..responses import no_content, ok, created

inventory_bp = Blueprint("inventory", __name__)


def _business_id():
    return g.ctx.business_id


@inventory_bp.route("/warehouses", methods=["GET"])
@login_required
@require_module("inventory")
@require_permission("inventory.read")
def list_warehouses():
    warehouses = list_warehouses_service(_business_id())
    return ok({"warehouses": warehouses})


@inventory_bp.route("/warehouses", methods=["POST"])
@login_required
@require_module("inventory")
@require_permission("inventory.write")
@validate_body(WarehouseSchema)
def create_warehouse():
    wh = create_warehouse_service(_business_id(), request.validated_body, g.ctx.user_id)
    return created({"warehouse": wh})


@inventory_bp.route("/warehouses/<warehouse_id>", methods=["GET"])
@login_required
@require_module("inventory")
@require_permission("inventory.read")
def get_warehouse(warehouse_id):
    wh = get_warehouse_service(_business_id(), parse_uuid(warehouse_id))
    return ok({"warehouse": wh})


@inventory_bp.route("/warehouses/<warehouse_id>", methods=["PATCH"])
@login_required
@require_module("inventory")
@require_permission("inventory.write")
@validate_body(WarehouseUpdateSchema)
def update_warehouse(warehouse_id):
    wh = update_warehouse_service(_business_id(), parse_uuid(warehouse_id), request.validated_body, g.ctx.user_id)
    return ok({"warehouse": wh})


@inventory_bp.route("/warehouses/<warehouse_id>", methods=["DELETE"])
@login_required
@require_module("inventory")
@require_permission("inventory.write")
def delete_warehouse(warehouse_id):
    delete_warehouse_service(_business_id(), parse_uuid(warehouse_id))
    return no_content()


@inventory_bp.route("/default-warehouse", methods=["GET"])
@login_required
@require_module("inventory")
@require_permission("inventory.read")
def default_warehouse():
    wh = get_default_warehouse_service(_business_id())
    return ok({"warehouse": wh.to_dict()})


@inventory_bp.route("/balances", methods=["GET"])
@login_required
@require_module("inventory")
@require_permission("inventory.read")
@validate_query(InventoryBalanceSearchSchema)
def list_balances():
    pagination = request.validated_query
    items = list_balances_service(_business_id())
    if getattr(pagination, "warehouse_id", None):
        items = [i for i in items if i["warehouse_id"] == str(pagination.warehouse_id)]
    if getattr(pagination, "q", None):
        query = pagination.q.lower()
        items = [i for i in items if query in (i.get("item_name") or "").lower() or query in (i.get("sku") or "").lower()]
    if getattr(pagination, "low_stock", False):
        items = [i for i in items if i["is_low_stock"]]
    start = (pagination.page - 1) * pagination.per_page
    page_items = items[start:start + pagination.per_page]
    pages = (len(items) + pagination.per_page - 1) // pagination.per_page
    return ok(
        {"balances": page_items},
        meta={"page": pagination.page, "per_page": pagination.per_page, "total": len(items), "pages": pages},
    )


@inventory_bp.route("/balances/<item_id>", methods=["GET"])
@login_required
@require_module("inventory")
@require_permission("inventory.read")
def get_balance(item_id):
    wh = request.args.get("warehouse_id")
    balance = get_balance_service(_business_id(), parse_uuid(item_id), parse_uuid(wh) if wh else None)
    return ok({"balance": balance})


@inventory_bp.route("/balances/<item_id>/in", methods=["POST"])
@login_required
@require_module("inventory")
@require_permission("inventory.write")
@validate_body(StockAdjustmentSchema)
def stock_in(item_id):
    data = request.validated_body
    balance = stock_in_service(_business_id(), parse_uuid(item_id), parse_uuid(data.warehouse_id),
                               float(data.quantity), g.ctx.user_id, data.note)
    return created({"movement": balance})


@inventory_bp.route("/balances/<item_id>/out", methods=["POST"])
@login_required
@require_module("inventory")
@require_permission("inventory.write")
@validate_body(StockAdjustmentSchema)
def stock_out(item_id):
    data = request.validated_body
    balance = stock_out_service(_business_id(), parse_uuid(item_id), parse_uuid(data.warehouse_id),
                                float(data.quantity), g.ctx.user_id, data.note)
    return created({"movement": balance})


@inventory_bp.route("/balances/<item_id>/adjust", methods=["POST"])
@login_required
@require_module("inventory")
@require_permission("inventory.write")
@validate_body(StockAdjustmentSchema)
def adjust(item_id):
    data = request.validated_body
    balance = adjust_stock_service(_business_id(), parse_uuid(item_id), parse_uuid(data.warehouse_id),
                                   float(data.quantity), g.ctx.user_id, data.note)
    return created({"movement": balance})


@inventory_bp.route("/low-stock", methods=["GET"])
@login_required
@require_module("inventory")
@require_permission("inventory.read")
def low_stock():
    alerts = get_low_stock_alerts_service(_business_id())
    return ok({"alerts": alerts})


@inventory_bp.route("/movements", methods=["GET"])
@login_required
@require_module("inventory")
@require_permission("inventory.read")
@validate_query(StockMovementSearchSchema)
def movements():
    pagination = request.validated_query
    movements = get_stock_history_service(
        _business_id(),
        item_id=parse_uuid(pagination.q) if pagination.q and _is_uuid(pagination.q) else None,
        warehouse_id=parse_uuid(pagination.warehouse_id) if pagination.warehouse_id else None,
        limit=pagination.per_page,
    )
    return ok(
        {"movements": movements},
        meta={"page": pagination.page, "per_page": pagination.per_page, "total": len(movements), "pages": 1},
    )


def _is_uuid(value):
    try:
        parse_uuid(value)
        return True
    except Exception:
        return False