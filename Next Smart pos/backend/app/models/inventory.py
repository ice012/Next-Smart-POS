from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from .mixins import UUIDType as SAB_UUID

from ..extensions import db
from ..models.mixins import AuditedBase


class StockAdjustment(AuditedBase):
    """A header record for a stock adjustment operation.

    The actual balance change is performed by the inventory service, which
    records a matching ``StockMovement`` with ``movement_type = adjustment``.
    """

    __tablename__ = "stock_adjustments"
    __table_args__ = (
        UniqueConstraint("business_id", "adjustment_number", name="uq_stock_adjustments_number"),
        Index("ix_stock_adjustments_business_id", "business_id"),
        Index("ix_stock_adjustments_warehouse_id", "warehouse_id"),
        Index("ix_stock_adjustments_status", "status"),
        Index("ix_stock_adjustments_date", "date"),
    )

    business_id = Column(
        SAB_UUID(),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    warehouse_id = Column(
        SAB_UUID(),
        ForeignKey("warehouses.id", ondelete="CASCADE"),
        nullable=False,
    )

    adjustment_number = Column(String(50), nullable=False)
    date = Column(DateTime(timezone=True), nullable=False)
    movement_type = Column(String(30), default="adjustment", nullable=False)
    status = Column(String(20), default="applied", nullable=False)
    note = Column(Text, nullable=True)

    warehouse = db.relationship("Warehouse", backref="stock_adjustments")
    items = db.relationship("StockAdjustmentItem", back_populates="adjustment", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": str(self.id),
            "business_id": str(self.business_id),
            "warehouse_id": str(self.warehouse_id),
            "adjustment_number": self.adjustment_number,
            "date": self.date.isoformat() if self.date else None,
            "movement_type": self.movement_type,
            "status": self.status,
            "note": self.note,
            "items": [i.to_dict() for i in self.items],
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class StockAdjustmentItem(AuditedBase):
    __tablename__ = "stock_adjustment_items"
    __table_args__ = (
        Index("ix_stock_adjustment_items_adjustment_id", "adjustment_id"),
        Index("ix_stock_adjustment_items_item_id", "item_id"),
    )

    business_id = Column(
        SAB_UUID(),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    adjustment_id = Column(
        SAB_UUID(),
        ForeignKey("stock_adjustments.id", ondelete="CASCADE"),
        nullable=False,
    )
    item_id = Column(
        SAB_UUID(),
        ForeignKey("items.id", ondelete="CASCADE"),
        nullable=False,
    )

    quantity = Column(Numeric(15, 2), default=0, nullable=False)
    direction = Column(String(10), nullable=False)
    note = Column(Text, nullable=True)

    adjustment = db.relationship("StockAdjustment", back_populates="items")
    item = db.relationship("Item")

    def to_dict(self):
        return {
            "id": str(self.id),
            "business_id": str(self.business_id),
            "adjustment_id": str(self.adjustment_id),
            "item_id": str(self.item_id),
            "quantity": float(self.quantity) if self.quantity else 0.0,
            "direction": self.direction,
            "note": self.note,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Warehouse(AuditedBase):
    __tablename__ = "warehouses"
    __table_args__ = (
        UniqueConstraint("business_id", "code", name="uq_warehouses_business_code"),
        UniqueConstraint("business_id", "name", name="uq_warehouses_business_name"),
        Index("ix_warehouses_branch_id", "branch_id"),
    )

    business_id = Column(
        SAB_UUID(),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    branch_id = Column(
        SAB_UUID(),
        ForeignKey("branches.id", ondelete="SET NULL"),
        nullable=True,
    )

    code = Column(String(50), nullable=False)
    name = Column(String(150), nullable=False)
    address = Column(db.JSON, nullable=True)
    is_default = Column(Boolean, default=False, nullable=False)

    def to_dict(self):
        return {
            "id": str(self.id),
            "business_id": str(self.business_id),
            "branch_id": str(self.branch_id) if self.branch_id else None,
            "code": self.code,
            "name": self.name,
            "address": self.address or {},
            "is_default": self.is_default,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class InventoryBalance(AuditedBase):
    __tablename__ = "inventory_balances"
    __table_args__ = (
        UniqueConstraint(
            "business_id", "item_id", "warehouse_id",
            name="uq_inventory_balances_item_warehouse",
        ),
        Index("ix_inventory_balances_item_id", "item_id"),
        Index("ix_inventory_balances_warehouse_id", "warehouse_id"),
    )

    business_id = Column(
        SAB_UUID(),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_id = Column(
        SAB_UUID(),
        ForeignKey("items.id", ondelete="CASCADE"),
        nullable=False,
    )
    warehouse_id = Column(
        SAB_UUID(),
        ForeignKey("warehouses.id", ondelete="CASCADE"),
        nullable=False,
    )

    quantity_on_hand = Column(Numeric(15, 2), default=0, nullable=False)
    reserved_quantity = Column(Numeric(15, 2), default=0, nullable=False)

    item = db.relationship("Item", backref="inventory_balances")
    warehouse = db.relationship("Warehouse", backref="inventory_balances")

    def to_dict(self):
        return {
            "id": str(self.id),
            "business_id": str(self.business_id),
            "item_id": str(self.item_id),
            "warehouse_id": str(self.warehouse_id),
            "quantity_on_hand": float(self.quantity_on_hand) if self.quantity_on_hand else 0.0,
            "reserved_quantity": float(self.reserved_quantity) if self.reserved_quantity else 0.0,
            "available": float(self.quantity_on_hand - self.reserved_quantity) if self.quantity_on_hand else 0.0,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


MOVEMENT_TYPES = {
    "in": "stock_in",
    "out": "stock_out",
    "adjustment": "adjustment",
    "sale": "sale",
    "purchase_return": "purchase_return",
    "sale_return": "sale_return",
}


class StockMovement(AuditedBase):
    __tablename__ = "stock_movements"
    __table_args__ = (
        Index("ix_stock_movements_item_id", "item_id"),
        Index("ix_stock_movements_warehouse_id", "warehouse_id"),
        Index("ix_stock_movements_movement_type", "movement_type"),
        Index("ix_stock_movements_created_at", "created_at"),
    )

    business_id = Column(
        SAB_UUID(),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_id = Column(
        SAB_UUID(),
        ForeignKey("items.id", ondelete="CASCADE"),
        nullable=False,
    )
    warehouse_id = Column(
        SAB_UUID(),
        ForeignKey("warehouses.id", ondelete="CASCADE"),
        nullable=True,
    )

    quantity = Column(Numeric(15, 2), nullable=False)
    direction = Column(String(10), nullable=False)
    movement_type = Column(String(30), nullable=False)
    reference_type = Column(String(30), nullable=True)
    reference_id = Column(SAB_UUID(), nullable=True)
    user_id = Column(SAB_UUID(), nullable=True)
    note = Column(Text, nullable=True)

    def to_dict(self):
        return {
            "id": str(self.id),
            "business_id": str(self.business_id),
            "item_id": str(self.item_id),
            "item_name": self.item.name if hasattr(self, "item") and self.item else None,
            "warehouse_id": str(self.warehouse_id) if self.warehouse_id else None,
            "quantity": float(self.quantity) if self.quantity else 0.0,
            "direction": self.direction,
            "movement_type": self.movement_type,
            "reference_type": self.reference_type,
            "reference_id": str(self.reference_id) if self.reference_id else None,
            "user_id": str(self.user_id) if self.user_id else None,
            "note": self.note,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
