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
from sqlalchemy.orm import validates

from ..extensions import db
from ..models.mixins import AuditedBase


CUSTOMER_TYPES = {"individual": "individual", "corporate": "corporate"}


class CustomerPayment(AuditedBase):
    """Customer ledger payment: reduces the customer's statement balance."""

    __tablename__ = "customer_payments"
    __table_args__ = (
        UniqueConstraint("business_id", "payment_number", name="uq_customer_payments_number"),
        Index("ix_customer_payments_business_id", "business_id"),
        Index("ix_customer_payments_customer_id", "customer_id"),
        Index("ix_customer_payments_date", "date"),
        Index("ix_customer_payments_method", "payment_method"),
        Index("ix_customer_payments_sale_invoice_id", "sale_invoice_id"),
    )

    business_id = Column(
        SAB_UUID(),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    customer_id = Column(
        SAB_UUID(),
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
    )
    sale_invoice_id = Column(
        SAB_UUID(),
        ForeignKey("sale_invoices.id", ondelete="SET NULL"),
        nullable=True,
    )

    payment_number = Column(String(50), nullable=False)
    amount = Column(Numeric(15, 2), default=0, nullable=False)
    payment_method = Column(String(20), nullable=False)
    date = Column(DateTime(timezone=True), nullable=False)
    reference = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)
    status = Column(String(20), default="completed", nullable=False)

    customer = db.relationship("Customer", backref="customer_payments")
    sale_invoice = db.relationship("SaleInvoice")

    def to_dict(self):
        return {
            "id": str(self.id),
            "business_id": str(self.business_id),
            "customer_id": str(self.customer_id),
            "sale_invoice_id": str(self.sale_invoice_id) if self.sale_invoice_id else None,
            "payment_number": self.payment_number,
            "amount": float(self.amount) if self.amount else 0.0,
            "payment_method": self.payment_method,
            "date": self.date.isoformat() if self.date else None,
            "reference": self.reference,
            "notes": self.notes,
            "status": self.status,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Customer(AuditedBase):
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("business_id", "customer_number", name="uq_customers_business_number"),
        UniqueConstraint("business_id", "display_name", name="uq_customers_business_name"),
        Index("ix_customers_business_type", "business_id", "customer_type"),
        Index("ix_customers_branch_id", "branch_id"),
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

    customer_number = Column(String(50), nullable=False)
    display_name = Column(String(200), nullable=False)
    customer_type = Column(String(20), default="individual", nullable=False)

    first_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)
    company_name = Column(String(200), nullable=True)
    contact_person = Column(String(100), nullable=True)
    tax_id = Column(String(100), nullable=True)

    email = Column(String(255), nullable=True)
    phone = Column(String(50), nullable=True)
    address = Column(db.JSON, nullable=True)

    statement_balance = Column(Numeric(15, 2), default=0, nullable=False)
    notes = Column(Text, nullable=True)

    def to_dict(self):
        return {
            "id": str(self.id),
            "business_id": str(self.business_id),
            "branch_id": str(self.branch_id) if self.branch_id else None,
            "customer_number": self.customer_number,
            "display_name": self.display_name,
            "customer_type": self.customer_type,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "company_name": self.company_name,
            "contact_person": self.contact_person,
            "tax_id": self.tax_id,
            "email": self.email,
            "phone": self.phone,
            "address": self.address or {},
            "statement_balance": float(self.statement_balance) if self.statement_balance else 0.0,
            "notes": self.notes,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
