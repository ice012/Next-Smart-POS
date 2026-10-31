from sqlalchemy import (
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


QUOTATION_STATUSES = {"draft", "sent", "accepted", "rejected", "expired", "converted"}


class Quotation(AuditedBase):
    __tablename__ = "quotations"
    __table_args__ = (
        UniqueConstraint("business_id", "quotation_number", name="uq_quotations_number"),
        Index("ix_quotations_branch_id", "branch_id"),
        Index("ix_quotations_customer_id", "customer_id"),
        Index("ix_quotations_date", "date"),
        Index("ix_quotations_status", "status"),
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
    customer_id = Column(
        SAB_UUID(),
        ForeignKey("customers.id", ondelete="SET NULL"),
        nullable=True,
    )

    quotation_number = Column(String(50), nullable=False)
    date = Column(DateTime(timezone=True), nullable=False)
    expiry_date = Column(DateTime(timezone=True), nullable=True)

    subtotal = Column(Numeric(15, 2), default=0, nullable=False)
    discount_amount = Column(Numeric(15, 2), default=0, nullable=False)
    tax_amount = Column(Numeric(15, 2), default=0, nullable=False)
    total_amount = Column(Numeric(15, 2), default=0, nullable=False)

    status = Column(String(20), default="draft", nullable=False)
    notes = Column(Text, nullable=True)

    customer = db.relationship("Customer", backref="quotations")
    branch = db.relationship("Branch", backref="quotations")
    items = db.relationship("QuotationItem", back_populates="quotation", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": str(self.id),
            "business_id": str(self.business_id),
            "branch_id": str(self.branch_id) if self.branch_id else None,
            "customer_id": str(self.customer_id) if self.customer_id else None,
            "customer_name": self.customer.display_name if self.customer else None,
            "quotation_number": self.quotation_number,
            "date": self.date.isoformat() if self.date else None,
            "expiry_date": self.expiry_date.isoformat() if self.expiry_date else None,
            "subtotal": float(self.subtotal) if self.subtotal else 0.0,
            "discount_amount": float(self.discount_amount) if self.discount_amount else 0.0,
            "tax_amount": float(self.tax_amount) if self.tax_amount else 0.0,
            "total_amount": float(self.total_amount) if self.total_amount else 0.0,
            "status": self.status,
            "notes": self.notes,
            "items": [item.to_dict() for item in self.items],
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class QuotationItem(AuditedBase):
    __tablename__ = "quotation_items"
    __table_args__ = (
        Index("ix_quotation_items_item_id", "item_id"),
    )

    business_id = Column(
        SAB_UUID(),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    quotation_id = Column(
        SAB_UUID(),
        ForeignKey("quotations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_id = Column(
        SAB_UUID(),
        ForeignKey("items.id", ondelete="SET NULL"),
        nullable=True,
    )

    description = Column(String(500), nullable=False)
    quantity = Column(Numeric(15, 2), default=0, nullable=False)
    unit_price = Column(Numeric(15, 2), default=0, nullable=False)
    cost_price = Column(Numeric(15, 2), default=0, nullable=False)
    discount_amount = Column(Numeric(15, 2), default=0, nullable=False)
    tax_rate = Column(Numeric(5, 2), default=0, nullable=False)
    tax_amount = Column(Numeric(15, 2), default=0, nullable=False)
    line_total = Column(Numeric(15, 2), default=0, nullable=False)
    is_service = Column(db.Boolean, default=False, nullable=False)

    quotation = db.relationship("Quotation", back_populates="items")
    item = db.relationship("Item", backref="quotation_items")

    def to_dict(self):
        return {
            "id": str(self.id),
            "business_id": str(self.business_id),
            "quotation_id": str(self.quotation_id),
            "item_id": str(self.item_id) if self.item_id else None,
            "description": self.description,
            "quantity": float(self.quantity) if self.quantity else 0.0,
            "unit_price": float(self.unit_price) if self.unit_price else 0.0,
            "cost_price": float(self.cost_price) if self.cost_price else 0.0,
            "discount_amount": float(self.discount_amount) if self.discount_amount else 0.0,
            "tax_rate": float(self.tax_rate) if self.tax_rate else 0.0,
            "tax_amount": float(self.tax_amount) if self.tax_amount else 0.0,
            "line_total": float(self.line_total) if self.line_total else 0.0,
            "is_service": self.is_service,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
