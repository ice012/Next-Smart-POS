"""Purchase order service: supplier orders and goods receipt."""
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, or_, select

from ..exceptions import BusinessError, NotFoundError, ValidationError
from ..extensions import db
from ..models import Item, PurchaseOrder, PurchaseOrderItem, Supplier
from ..utils.db import commit
from ..utils.pagination import paginate
from ..utils.security import to_uuid
from .audit_log_service import log_event
from .inventory_service import get_default_warehouse, get_warehouse, receive_stock


def _parse_dt(value):
    if value is None or isinstance(value, datetime):
        return value
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        raise ValidationError(f"Invalid date: {value}")
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _next_order_number(business_id):
    count = db.session.scalar(
        select(func.count()).where(PurchaseOrder.business_id == business_id)
    ) or 0
    return f"PO-{count + 1:06d}"


def _get_order(business_id, order_id):
    order = db.session.get(PurchaseOrder, order_id)
    if order is None or order.business_id != business_id:
        raise NotFoundError("Purchase order not found")
    return order


def list_purchase_orders(business_id, pagination, status=None, supplier_id=None,
                         from_date=None, to_date=None, q=None):
    stmt = select(PurchaseOrder).where(PurchaseOrder.business_id == business_id)

    if status:
        stmt = stmt.where(PurchaseOrder.status == status)
    if supplier_id:
        stmt = stmt.where(PurchaseOrder.supplier_id == to_uuid(supplier_id))
    if from_date:
        stmt = stmt.where(PurchaseOrder.date >= _parse_dt(from_date))
    if to_date:
        stmt = stmt.where(PurchaseOrder.date <= _parse_dt(to_date))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                PurchaseOrder.order_number.ilike(like),
                PurchaseOrder.notes.ilike(like),
            )
        )

    stmt = stmt.order_by(PurchaseOrder.date.desc(), PurchaseOrder.created_at.desc())
    rows, total, pages = paginate(stmt, pagination.page, pagination.per_page)
    return [row.to_dict() for row in rows], total, pages


def get_purchase_order(business_id, order_id):
    return _get_order(business_id, order_id).to_dict()


def _price_lines(business_id, items_data):
    """Price supplier lines from the catalog cost price."""
    from .sale_service import item_tax_rate

    if not items_data:
        raise ValidationError("At least one line item is required")

    lines = []
    for line in items_data:
        item = db.session.get(Item, to_uuid(line.item_id))
        if item is None or item.business_id != business_id:
            raise NotFoundError(f"Item {line.item_id} not found")

        quantity = Decimal(str(line.quantity))
        if quantity <= 0:
            raise ValidationError("Quantity must be greater than zero")
        discount = Decimal(str(line.discount_amount or 0))
        unit_cost = Decimal(str(item.cost_price or 0))
        tax_rate = item_tax_rate(item, business_id, "purchases")

        gross = unit_cost * quantity
        if discount > gross:
            raise BusinessError(f"Discount for '{item.name}' exceeds the line total")
        net = gross - discount
        tax = (net * (tax_rate / Decimal("100"))).quantize(Decimal("0.01"))

        is_service = item.type == "service"
        lines.append({
            "item": item,
            "quantity": quantity,
            "discount_amount": discount,
            "unit_cost": unit_cost,
            "tax_rate": tax_rate,
            "tax_amount": tax,
            "line_total": net + tax,
            "is_tracked": bool(item.is_inventory_tracked) and not is_service,
        })
    return lines


def _replace_items(business_id, order, items_data, actor_id):
    for existing in list(order.items):
        db.session.delete(existing)
    db.session.flush()

    lines = _price_lines(business_id, items_data)
    for priced in lines:
        row = PurchaseOrderItem(
            business_id=business_id,
            order_id=order.id,
            item_id=priced["item"].id,
            description=priced["item"].name,
            quantity=priced["quantity"],
            unit_cost=priced["unit_cost"],
            received_quantity=Decimal("0"),
            tax_rate=priced["tax_rate"],
            tax_amount=priced["tax_amount"],
            line_total=priced["line_total"],
        )
        row.created_by = actor_id
        row.updated_by = actor_id
        db.session.add(row)

    subtotal = sum(
        (priced["unit_cost"] * priced["quantity"] for priced in lines), Decimal("0")
    ).quantize(Decimal("0.01"))
    discount = sum((p["discount_amount"] for p in lines), Decimal("0")).quantize(Decimal("0.01"))
    tax = sum((p["tax_amount"] for p in lines), Decimal("0")).quantize(Decimal("0.01"))
    order.subtotal = subtotal
    order.tax_amount = tax
    order.total_amount = (subtotal - discount + tax).quantize(Decimal("0.01"))


def create_purchase_order(business_id, data, actor_id):
    supplier = db.session.get(Supplier, to_uuid(data.supplier_id))
    if supplier is None or supplier.business_id != business_id:
        raise NotFoundError("Supplier not found")

    warehouse = None
    if data.warehouse_id:
        warehouse = get_warehouse(business_id, to_uuid(data.warehouse_id))
    else:
        warehouse = get_default_warehouse(business_id)

    order = PurchaseOrder(
        business_id=business_id,
        supplier_id=supplier.id,
        warehouse_id=warehouse.id,
        order_number=_next_order_number(business_id),
        date=datetime.now(timezone.utc),
        expected_date=_parse_dt(data.expected_date),
        status="draft",
        notes=data.notes,
    )
    order.created_by = actor_id
    order.updated_by = actor_id
    db.session.add(order)
    db.session.flush()

    _replace_items(business_id, order, data.items, actor_id)
    log_event(business_id, "create", "purchase_order", actor_id, order.id, {
        "order_number": order.order_number,
        "total_amount": float(order.total_amount or 0),
    })
    commit()
    return order.to_dict()


def update_purchase_order(business_id, order_id, data, actor_id):
    order = _get_order(business_id, order_id)
    if order.status in ("received", "cancelled"):
        raise BusinessError(f"Cannot modify a {order.status} purchase order")

    if data.warehouse_id:
        order.warehouse_id = get_warehouse(business_id, to_uuid(data.warehouse_id)).id
    if data.expected_date is not None:
        order.expected_date = _parse_dt(data.expected_date)
    if data.notes is not None:
        order.notes = data.notes
    if data.status is not None and data.status != order.status:
        if data.status not in ("draft", "ordered"):
            raise BusinessError("Use the receive or cancel action for that status")
        order.status = data.status
    if data.items is not None:
        _replace_items(business_id, order, data.items, actor_id)

    order.updated_by = actor_id
    commit()
    return order.to_dict()


def receive_purchase_order(business_id, order_id, data, actor_id):
    order = _get_order(business_id, order_id)
    if order.status in ("received", "cancelled"):
        raise BusinessError(f"Cannot receive a {order.status} purchase order")

    warehouse_id = order.warehouse_id
    if data.warehouse_id:
        warehouse_id = get_warehouse(business_id, to_uuid(data.warehouse_id)).id

    lines = {str(line.item_id): line for line in order.items if line.item_id}
    for entry in data.items:
        item_id = str(entry.get("item_id") or "")
        line = lines.get(item_id)
        if line is None:
            raise NotFoundError(f"Line item {item_id} is not part of this order")
        quantity = Decimal(str(entry.get("quantity") or 0))
        if quantity < 0:
            raise ValidationError("Received quantity cannot be negative")
        outstanding = Decimal(str(line.quantity or 0)) - Decimal(str(line.received_quantity or 0))
        if quantity > outstanding:
            raise BusinessError(
                f"Cannot receive {quantity} of '{line.description}'. Outstanding: {outstanding}"
            )
        line.received_quantity = Decimal(str(line.received_quantity or 0)) + quantity
        line.updated_by = actor_id
        if quantity > 0 and line.item_id:
            item = db.session.get(Item, line.item_id)
            if item is not None and item.is_inventory_tracked and item.type != "service":
                receive_stock(
                    business_id, item.id, warehouse_id, quantity,
                    user_id=actor_id,
                    note=getattr(data, "note", None) or f"Received {order.order_number}",
                )

    fully_received = all(
        Decimal(str(line.received_quantity or 0)) >= Decimal(str(line.quantity or 0))
        for line in order.items
    )
    order.status = "received" if fully_received else "ordered"
    order.warehouse_id = warehouse_id
    order.updated_by = actor_id
    log_event(business_id, "receive", "purchase_order", actor_id, order.id, {
        "order_number": order.order_number, "status": order.status,
    })
    commit()
    return order.to_dict()


def cancel_purchase_order(business_id, order_id, actor_id, reason=None):
    order = _get_order(business_id, order_id)
    if order.status == "cancelled":
        raise BusinessError("Purchase order is already cancelled")
    if any(Decimal(str(line.received_quantity or 0)) > 0 for line in order.items):
        raise BusinessError("Received stock cannot be cancelled. Return the stock first.")

    order.status = "cancelled"
    order.updated_by = actor_id
    log_event(business_id, "cancel", "purchase_order", actor_id, order.id, {
        "order_number": order.order_number, "reason": reason,
    })
    commit()
    return order.to_dict()


def delete_purchase_order(business_id, order_id, actor_id):
    order = _get_order(business_id, order_id)
    if order.status not in ("draft", "cancelled"):
        raise BusinessError("Only draft or cancelled purchase orders can be deleted")
    number = order.order_number
    db.session.delete(order)
    log_event(business_id, "delete", "purchase_order", actor_id, order_id, {
        "order_number": number,
    })
    commit()
