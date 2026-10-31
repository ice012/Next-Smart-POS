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
)
from .mixins import UUIDType as SAB_UUID

from ..extensions import db
from ..models.mixins import AuditedBase


PURCHASE_ORDER_STATUSES = {"draft", "ordered", "received", "cancelled"}


class PurchaseOrder(AuditedBase):
    __tablename__ = "purchase_orders"
    __table_args__ = (
        UniqueConstraint("business_id", "order_number", name="uq_purchase_orders_number"),
        Index("ix_purchase_orders_business_id", "business_id"),
        Index("ix_purchase_orders_supplier_id", "supplier_id"),
        Index("ix_purchase_orders_warehouse_id", "warehouse_id"),
        Index("ix_purchase_orders_status", "status"),
        Index("ix_purchase_orders_date", "date"),
    )

    business_id = Column(
        SAB_UUID(),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    supplier_id = Column(
        SAB_UUID(),
        ForeignKey("suppliers.id", ondelete="CASCADE"),
        nullable=False,
    )
    warehouse_id = Column(
        SAB_UUID(),
        ForeignKey("warehouses.id", ondelete="SET NULL"),
        nullable=True,
    )

    order_number = Column(String(50), nullable=False)
    date = Column(DateTime(timezone=True), nullable=False)
    expected_date = Column(DateTime(timezone=True), nullable=True)

    subtotal = Column(Numeric(15, 2), default=0, nullable=False)
    tax_amount = Column(Numeric(15, 2), default=0, nullable=False)
    total_amount = Column(Numeric(15, 2), default=0, nullable=False)

    status = Column(String(20), default="draft", nullable=False)
    notes = Column(Text, nullable=True)

    supplier = db.relationship("Supplier", backref="purchase_orders")
    warehouse = db.relationship("Warehouse", backref="purchase_orders")
    items = db.relationship("PurchaseOrderItem", back_populates="order", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": str(self.id),
            "business_id": str(self.business_id),
            "supplier_id": str(self.supplier_id),
            "supplier_name": self.supplier.display_name if self.supplier else None,
            "warehouse_id": str(self.warehouse_id) if self.warehouse_id else None,
            "order_number": self.order_number,
            "date": self.date.isoformat() if self.date else None,
            "expected_date": self.expected_date.isoformat() if self.expected_date else None,
            "subtotal": float(self.subtotal) if self.subtotal else 0.0,
            "tax_amount": float(self.tax_amount) if self.tax_amount else 0.0,
            "total_amount": float(self.total_amount) if self.total_amount else 0.0,
            "status": self.status,
            "notes": self.notes,
            "items": [i.to_dict() for i in self.items],
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class PurchaseOrderItem(AuditedBase):
    __tablename__ = "purchase_order_items"
    __table_args__ = (
        Index("ix_purchase_order_items_order_id", "order_id"),
        Index("ix_purchase_order_items_item_id", "item_id"),
    )

    business_id = Column(
        SAB_UUID(),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    order_id = Column(
        SAB_UUID(),
        ForeignKey("purchase_orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    item_id = Column(
        SAB_UUID(),
        ForeignKey("items.id", ondelete="SET NULL"),
        nullable=True,
    )

    description = Column(String(500), nullable=False)
    quantity = Column(Numeric(15, 2), default=0, nullable=False)
    unit_cost = Column(Numeric(15, 2), default=0, nullable=False)
    received_quantity = Column(Numeric(15, 2), default=0, nullable=False)
    tax_rate = Column(Numeric(5, 2), default=0, nullable=False)
    tax_amount = Column(Numeric(15, 2), default=0, nullable=False)
    line_total = Column(Numeric(15, 2), default=0, nullable=False)

    order = db.relationship("PurchaseOrder", back_populates="items")
    item = db.relationship("Item")

    def to_dict(self):
        return {
            "id": str(self.id),
            "business_id": str(self.business_id),
            "order_id": str(self.order_id),
            "item_id": str(self.item_id) if self.item_id else None,
            "description": self.description,
            "quantity": float(self.quantity) if self.quantity else 0.0,
            "unit_cost": float(self.unit_cost) if self.unit_cost else 0.0,
            "received_quantity": float(self.received_quantity) if self.received_quantity else 0.0,
            "tax_rate": float(self.tax_rate) if self.tax_rate else 0.0,
            "tax_amount": float(self.tax_amount) if self.tax_amount else 0.0,
            "line_total": float(self.line_total) if self.line_total else 0.0,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }