from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select

from ..exceptions import NotFoundError, BusinessError, ValidationError
from ..extensions import db
from ..models import Item, InventoryBalance, StockMovement, Warehouse
from ..utils.db import commit
from ..utils.pagination import paginate
from ..utils.security import to_uuid


def get_default_warehouse(business_id):
    wh = db.session.scalar(
        select(Warehouse).where(
            Warehouse.business_id == business_id,
            Warehouse.is_active.is_(True),
        ).order_by(Warehouse.is_default.is_(True).desc(), Warehouse.name.asc())
    )
    if wh is None:
        raise BusinessError("No active warehouse found. Create a warehouse first.")
    return wh


def get_warehouse(business_id, warehouse_id):
    wh = db.session.get(Warehouse, warehouse_id)
    if wh is None or wh.business_id != business_id:
        raise NotFoundError("Warehouse not found")
    return wh


def list_warehouses(business_id):
    whs = db.session.scalars(
        select(Warehouse)
        .where(Warehouse.business_id == business_id, Warehouse.is_active.is_(True))
        .order_by(Warehouse.is_default.desc(), Warehouse.name.asc())
    ).all()
    return [w.to_dict() for w in whs]


def create_warehouse(business_id, data, actor_id):
    existing = db.session.scalar(
        select(Warehouse).where(
            Warehouse.business_id == business_id,
            Warehouse.code == data.code,
        )
    )
    if existing is not None:
        raise BusinessError("A warehouse with this code already exists")

    if data.is_default:
        db.session.execute(
            Warehouse.__table__.update()
            .where(
                Warehouse.business_id == business_id,
                Warehouse.is_default.is_(True),
            )
            .values(is_default=False)
        )

    wh = Warehouse(
        business_id=business_id,
        branch_id=to_uuid(data.branch_id) if data.branch_id else None,
        code=data.code,
        name=data.name,
        address=data.address,
        is_default=bool(data.is_default),
    )
    wh.created_by = actor_id
    wh.updated_by = actor_id
    db.session.add(wh)
    commit()
    return wh.to_dict()


def update_warehouse(business_id, warehouse_id, data, actor_id):
    wh = get_warehouse(business_id, warehouse_id)
    for field in ("code", "name", "address"):
        value = getattr(data, field, None)
        if value is not None:
            setattr(wh, field, value)
    if data.branch_id is not None:
        wh.branch_id = to_uuid(data.branch_id) if data.branch_id else None
    if data.is_default is not None:
        if data.is_default:
            db.session.execute(
                Warehouse.__table__.update()
                .where(
                    Warehouse.business_id == business_id,
                    Warehouse.is_default.is_(True),
                )
                .values(is_default=False)
            )
        wh.is_default = data.is_default
    if data.is_active is not None:
        wh.is_active = data.is_active
    wh.updated_by = actor_id
    commit()
    return wh.to_dict()


def delete_warehouse(business_id, warehouse_id):
    wh = get_warehouse(business_id, warehouse_id)
    if wh.is_default:
        raise BusinessError("Cannot delete the default warehouse")
    has_stock = db.session.scalar(
        select(func.count()).where(InventoryBalance.warehouse_id == wh.id)
    ) or 0
    if has_stock > 0:
        raise BusinessError("Cannot delete a warehouse with stock. Transfer stock first.")
    db.session.delete(wh)
    commit()


def get_inventory_balance(business_id, item_id, warehouse_id):
    ib = db.session.scalar(
        select(InventoryBalance).where(
            InventoryBalance.business_id == business_id,
            InventoryBalance.item_id == item_id,
            InventoryBalance.warehouse_id == warehouse_id,
        )
    )
    if ib is None:
        warehouse_id = warehouse_id or get_default_warehouse(business_id).id
        return get_inventory_balance(business_id, item_id, warehouse_id)
    return ib.to_dict()


def has_outstanding_balance(item_id):
    return (db.session.scalar(
        select(func.count()).where(
            InventoryBalance.item_id == item_id,
            InventoryBalance.quantity_on_hand != 0,
        )
    ) or 0) > 0


def ensure_inventory_balance(business_id, item_id, actor_id):
    """Ensure an inventory balance row exists for an item. Called when a tracked product is created."""
    wh = get_default_warehouse(business_id)
    existing = db.session.scalar(
        select(InventoryBalance).where(
            InventoryBalance.business_id == business_id,
            InventoryBalance.item_id == item_id,
            InventoryBalance.warehouse_id == wh.id,
        )
    )
    if existing is None:
        ib = InventoryBalance(
            business_id=business_id,
            item_id=item_id,
            warehouse_id=wh.id,
            quantity_on_hand=Decimal("0"),
            reserved_quantity=Decimal("0"),
            created_by=actor_id,
            updated_by=actor_id,
        )
        db.session.add(ib)
        db.session.flush()
    return wh


def _record_movement(business_id, item_id, warehouse_id, quantity, direction,
                     movement_type, reference_type=None, reference_id=None,
                     user_id=None, note=None):
    """Record a stock movement and update the inventory balance (must be in a transaction)."""
    item = db.session.get(Item, item_id)
    if item is None or item.business_id != business_id:
        raise NotFoundError("Item not found")

    warehouse = db.session.get(Warehouse, warehouse_id)
    if warehouse is None or warehouse.business_id != business_id:
        raise NotFoundError("Warehouse not found")

    qty = Decimal(str(quantity))
    movement = StockMovement(
        business_id=business_id,
        item_id=item_id,
        warehouse_id=warehouse_id,
        quantity=qty,
        direction=direction,
        movement_type=movement_type,
        reference_type=reference_type,
        reference_id=reference_id,
        user_id=user_id,
        note=note,
    )
    movement.created_by = user_id
    movement.updated_by = user_id
    db.session.add(movement)

    balance = db.session.scalar(
        select(InventoryBalance).where(
            InventoryBalance.business_id == business_id,
            InventoryBalance.item_id == item_id,
            InventoryBalance.warehouse_id == warehouse_id,
        )
    )
    if balance is None:
        balance = InventoryBalance(
            business_id=business_id,
            item_id=item_id,
            warehouse_id=warehouse_id,
            quantity_on_hand=Decimal("0"),
            reserved_quantity=Decimal("0"),
            created_by=user_id,
            updated_by=user_id,
        )
        db.session.add(balance)
        db.session.flush()

    if direction == "in":
        balance.quantity_on_hand = (balance.quantity_on_hand or Decimal("0")) + qty
    elif direction == "out":
        new_qty = (balance.quantity_on_hand or Decimal("0")) - qty
        if new_qty < 0:
            raise BusinessError(
                f"Insufficient stock for '{item.name}'. Available: {balance.quantity_on_hand}, requested: {qty}"
            )
        balance.quantity_on_hand = new_qty
    balance.updated_by = user_id
    db.session.flush()
    return movement


def reduce_stock(business_id, item_id, warehouse_id, quantity, user_id=None,
                 reference_type=None, reference_id=None, note=None):
    """Reduce stock for a product sale (critical: always server-side in a transaction)."""
    _record_movement(
        business_id, item_id, warehouse_id, quantity, "out",
        "sale", reference_type=reference_type, reference_id=reference_id,
        user_id=user_id, note=note,
    )


def return_stock(business_id, item_id, warehouse_id, quantity, user_id=None,
                 reference_type=None, reference_id=None, note=None):
    """Return stock to the warehouse (cancelled sale or customer return)."""
    _record_movement(
        business_id, item_id, warehouse_id, quantity, "in",
        "sale_return", reference_type=reference_type, reference_id=reference_id,
        user_id=user_id, note=note,
    )


def receive_stock(business_id, item_id, warehouse_id, quantity, user_id=None, note=None):
    """Increase stock without committing, so the caller owns the transaction."""
    return _record_movement(
        business_id, item_id, warehouse_id, quantity, "in",
        "stock_in", user_id=user_id, note=note,
    )


def stock_in(business_id, item_id, warehouse_id, quantity, user_id=None, note=None):
    movement = _record_movement(
        business_id, item_id, warehouse_id, quantity, "in",
        "stock_in", user_id=user_id, note=note,
    )
    commit()
    return movement.to_dict()


def stock_out(business_id, item_id, warehouse_id, quantity, user_id=None, note=None):
    movement = _record_movement(
        business_id, item_id, warehouse_id, quantity, "out",
        "stock_out", user_id=user_id, note=note,
    )
    commit()
    return movement.to_dict()


def adjust_stock(business_id, item_id, warehouse_id, quantity, user_id=None, note=None):
    new_qty = Decimal(str(quantity))
    current = db.session.scalar(
        select(InventoryBalance).where(
            InventoryBalance.business_id == business_id,
            InventoryBalance.item_id == item_id,
            InventoryBalance.warehouse_id == warehouse_id,
        )
    )
    current_qty = (current.quantity_on_hand or Decimal("0")) if current else Decimal("0")

    direction = "in" if new_qty >= current_qty else "out"
    diff = abs(new_qty - current_qty)
    if diff != 0:
        _record_movement(
            business_id, item_id, warehouse_id, diff, direction,
            "adjustment", user_id=user_id, note=note,
        )
    commit()
    return get_inventory_balance(business_id, item_id, warehouse_id)


def get_stock_history(business_id, item_id=None, warehouse_id=None, limit=50):
    stmt = (
        select(StockMovement)
        .where(StockMovement.business_id == business_id)
        .order_by(StockMovement.created_at.desc())
        .limit(limit)
    )
    if item_id:
        stmt = stmt.where(StockMovement.item_id == item_id)
    if warehouse_id:
        stmt = stmt.where(StockMovement.warehouse_id == warehouse_id)
    results = db.session.scalars(stmt).all()
    return [m.to_dict() for m in results]


def get_low_stock_alerts(business_id):
    stmt = (
        select(InventoryBalance, Item)
        .join(Item, InventoryBalance.item_id == Item.id)
        .where(
            InventoryBalance.business_id == business_id,
            Item.is_inventory_tracked.is_(True),
        )
    )
    results = db.session.execute(stmt).all()
    alerts = []
    for balance, item in results:
        on_hand = balance.quantity_on_hand or Decimal("0")
        if on_hand <= item.minimum_stock:
            alerts.append({
                "item_id": str(item.id),
                "item_name": item.name,
                "sku": item.sku,
                "warehouse_id": str(balance.warehouse_id),
                "quantity_on_hand": float(on_hand),
                "minimum_stock": float(item.minimum_stock),
            })
    return alerts


def list_inventory_balances(business_id):
    stmt = (
        select(InventoryBalance, Item, Warehouse)
        .join(Item, InventoryBalance.item_id == Item.id)
        .outerjoin(Warehouse, InventoryBalance.warehouse_id == Warehouse.id)
        .where(
            InventoryBalance.business_id == business_id,
            Item.is_inventory_tracked.is_(True),
        )
        .order_by(Item.name.asc(), Warehouse.name.asc())
    )
    results = db.session.execute(stmt).all()
    return [
        {
            **b.to_dict(),
            "item_name": item.name,
            "sku": item.sku,
            "barcode": item.barcode,
            "warehouse_name": wh.name if wh else None,
            "minimum_stock": float(item.minimum_stock) if item.minimum_stock else 0.0,
            "item_type": item.type,
            "is_inventory_tracked": bool(item.is_inventory_tracked),
            "is_low_stock": (b.quantity_on_hand or 0) <= (item.minimum_stock or 0),
        }
        for b, item, wh in results
    ]
