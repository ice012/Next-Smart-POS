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


PAYMENT_METHODS = {"cash", "mpesa", "bank", "card", "credit", "other"}
SALE_TYPES = {"cash", "credit"}
SALE_STATUSES = {"draft", "posted", "cancelled"}


class SaleInvoice(AuditedBase):
    __tablename__ = "sale_invoices"
    __table_args__ = (
        UniqueConstraint("business_id", "invoice_number", name="uq_sales_invoice_number"),
        Index("ix_sale_invoices_branch_id", "branch_id"),
        Index("ix_sale_invoices_customer_id", "customer_id"),
        Index("ix_sale_invoices_date", "date"),
        Index("ix_sale_invoices_sale_type", "sale_type"),
        Index("ix_sale_invoices_status", "status"),
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

    invoice_number = Column(String(50), nullable=False)
    date = Column(DateTime(timezone=True), nullable=False)
    due_date = Column(DateTime(timezone=True), nullable=True)

    subtotal = Column(Numeric(15, 2), default=0, nullable=False)
    discount_amount = Column(Numeric(15, 2), default=0, nullable=False)
    tax_amount = Column(Numeric(15, 2), default=0, nullable=False)
    total_amount = Column(Numeric(15, 2), default=0, nullable=False)
    rounding_amount = Column(Numeric(15, 2), default=0, nullable=False)

    paid_amount = Column(Numeric(15, 2), default=0, nullable=False)
    balance_due = Column(Numeric(15, 2), default=0, nullable=False)

    sale_type = Column(String(20), default="cash", nullable=False)
    status = Column(String(20), default="draft", nullable=False)
    notes = Column(Text, nullable=True)

    customer = db.relationship("Customer", backref="sale_invoices")
    branch = db.relationship("Branch", backref="sale_invoices")
    items = db.relationship("SaleItem", back_populates="sale_invoice", cascade="all, delete-orphan")
    payments = db.relationship(
        "Payment",
        foreign_keys="Payment.sale_invoice_id",
        back_populates="sale_invoice",
        cascade="all, delete-orphan",
    )

    def to_dict(self):
        return {
            "id": str(self.id),
            "business_id": str(self.business_id),
            "branch_id": str(self.branch_id) if self.branch_id else None,
            "customer_id": str(self.customer_id) if self.customer_id else None,
            "customer_name": self.customer.display_name if self.customer else None,
            "invoice_number": self.invoice_number,
            "date": self.date.isoformat() if self.date else None,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "subtotal": float(self.subtotal) if self.subtotal else 0.0,
            "discount_amount": float(self.discount_amount) if self.discount_amount else 0.0,
            "tax_amount": float(self.tax_amount) if self.tax_amount else 0.0,
            "total_amount": float(self.total_amount) if self.total_amount else 0.0,
            "rounding_amount": float(self.rounding_amount) if self.rounding_amount else 0.0,
            "paid_amount": float(self.paid_amount) if self.paid_amount else 0.0,
            "balance_due": float(self.balance_due) if self.balance_due else 0.0,
            "sale_type": self.sale_type,
            "status": self.status,
            "notes": self.notes,
            "items": [item.to_dict() for item in self.items],
            "payments": [p.to_dict() for p in self.payments],
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class SaleItem(AuditedBase):
    __tablename__ = "sale_items"
    __table_args__ = (
        Index("ix_sale_items_item_id", "item_id"),
    )

    business_id = Column(
        SAB_UUID(),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sale_invoice_id = Column(
        SAB_UUID(),
        ForeignKey("sale_invoices.id", ondelete="CASCADE"),
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

    sale_invoice = db.relationship("SaleInvoice", back_populates="items")
    item = db.relationship("Item", backref="sale_items")

    def to_dict(self):
        return {
            "id": str(self.id),
            "business_id": str(self.business_id),
            "sale_invoice_id": str(self.sale_invoice_id),
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
