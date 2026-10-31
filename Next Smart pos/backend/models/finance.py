from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Enum as SQLEnum,
)
from .mixins import UUIDType as SAB_UUID
from sqlalchemy.orm import validates

from ..extensions import db
from ..models.mixins import AuditedBase


INVOICE_STATUSES = {
    "draft": "draft",
    "sent": "sent",
    "partially_paid": "partially_paid",
    "paid": "paid",
    "overdue": "overdue",
    "cancelled": "cancelled",
}

EXPENSE_STATUSES = {
    "draft": "draft",
    "approved": "approved",
    "paid": "paid",
    "cancelled": "cancelled",
}

PAYMENT_METHODS = {
    "cash", "mpesa", "bank", "card", "cheque", "other"
}

PAYMENT_TYPES = {
    "customer_receipt": "customer_receipt",
    "supplier_payment": "supplier_payment",
    "expense_payment": "expense_payment",
    "refund": "refund",
    "other": "other",
}

ACCOUNT_TYPES = {
    "asset": "asset",
    "liability": "liability",
    "equity": "equity",
    "revenue": "revenue",
    "cogs": "cogs",
    "expense": "expense",
    "ar": "ar",
    "ap": "ap",
    "cash": "cash",
    "bank": "bank",
}

JOURNAL_STATUSES = {
    "draft": "draft",
    "posted": "posted",
    "reversed": "reversed",
    "cancelled": "cancelled",
}

JOURNAL_LINE_TYPES = {
    "debit": "debit",
    "credit": "credit",
}


class Invoice(AuditedBase):
    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("business_id", "invoice_number", name="uq_invoices_number"),
        Index("ix_invoices_branch_id", "branch_id"),
        Index("ix_invoices_customer_id", "customer_id"),
        Index("ix_invoices_date", "date"),
        Index("ix_invoices_due_date", "due_date"),
        Index("ix_invoices_status", "status"),
        Index("ix_invoices_sale_invoice_id", "sale_invoice_id"),
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
    sale_invoice_id = Column(
        SAB_UUID(),
        ForeignKey("sale_invoices.id", ondelete="SET NULL"),
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

    status = Column(String(20), default="draft", nullable=False)
    notes = Column(Text, nullable=True)
    terms = Column(Text, nullable=True)

    customer = db.relationship("Customer", backref="invoices")
    branch = db.relationship("Branch", backref="invoices")
    sale_invoice = db.relationship("SaleInvoice")
    items = db.relationship("InvoiceItem", back_populates="invoice", cascade="all, delete-orphan")
    payments = db.relationship("Payment", foreign_keys="Payment.invoice_id", back_populates="invoice")

    def to_dict(self):
        return {
            "id": str(self.id),
            "business_id": str(self.business_id),
            "branch_id": str(self.branch_id) if self.branch_id else None,
            "customer_id": str(self.customer_id) if self.customer_id else None,
            "customer_name": self.customer.display_name if self.customer else None,
            "sale_invoice_id": str(self.sale_invoice_id) if self.sale_invoice_id else None,
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
            "status": self.status,
            "notes": self.notes,
            "terms": self.terms,
            "items": [item.to_dict() for item in self.items],
            "payments": [p.to_dict() for p in self.payments],
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class InvoiceItem(AuditedBase):
    __tablename__ = "invoice_items"
    __table_args__ = (
        Index("ix_invoice_items_invoice_id", "invoice_id"),
        Index("ix_invoice_items_item_id", "item_id"),
    )

    business_id = Column(
        SAB_UUID(),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    invoice_id = Column(
        SAB_UUID(),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
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

    invoice = db.relationship("Invoice", back_populates="items")
    item = db.relationship("Item", backref="invoice_items")

    def to_dict(self):
        return {
            "id": str(self.id),
            "business_id": str(self.business_id),
            "invoice_id": str(self.invoice_id),
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


class ExpenseCategory(AuditedBase):
    __tablename__ = "expense_categories"
    __table_args__ = (
        UniqueConstraint("business_id", "name", name="uq_expense_categories_business_name"),
        Index("ix_expense_categories_parent_id", "parent_id"),
    )

    business_id = Column(
        SAB_UUID(),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    parent_id = Column(
        SAB_UUID(),
        ForeignKey("expense_categories.id", ondelete="SET NULL"),
        nullable=True,
    )

    parent = db.relationship("ExpenseCategory", remote_side="ExpenseCategory.id", backref="children")

    def to_dict(self):
        return {
            "id": str(self.id),
            "business_id": str(self.business_id),
            "name": self.name,
            "description": self.description,
            "parent_id": str(self.parent_id) if self.parent_id else None,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Expense(AuditedBase):
    __tablename__ = "expenses"
    __table_args__ = (
        UniqueConstraint("business_id", "expense_number", name="uq_expenses_number"),
        Index("ix_expenses_branch_id", "branch_id"),
        Index("ix_expenses_supplier_id", "supplier_id"),
        Index("ix_expenses_category_id", "category_id"),
        Index("ix_expenses_date", "date"),
        Index("ix_expenses_status", "status"),
        Index("ix_expenses_payment_method", "payment_method"),
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
    supplier_id = Column(
        SAB_UUID(),
        ForeignKey("suppliers.id", ondelete="SET NULL"),
        nullable=True,
    )
    category_id = Column(
        SAB_UUID(),
        ForeignKey("expense_categories.id", ondelete="SET NULL"),
        nullable=True,
    )
    financial_account_id = Column(
        SAB_UUID(),
        ForeignKey("financial_accounts.id", ondelete="SET NULL"),
        nullable=True,
    )

    expense_number = Column(String(50), nullable=False)
    date = Column(DateTime(timezone=True), nullable=False)
    due_date = Column(DateTime(timezone=True), nullable=True)

    description = Column(Text, nullable=False)
    amount = Column(Numeric(15, 2), default=0, nullable=False)
    tax_amount = Column(Numeric(15, 2), default=0, nullable=False)
    total_amount = Column(Numeric(15, 2), default=0, nullable=False)

    payment_method = Column(String(20), nullable=True)
    reference = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)
    status = Column(String(20), default="draft", nullable=False)

    supplier = db.relationship("Supplier", backref="expenses")
    category = db.relationship("ExpenseCategory", backref="expenses")
    financial_account = db.relationship("FinancialAccount", backref="expenses")
    attachments = db.relationship("ExpenseAttachment", back_populates="expense", cascade="all, delete-orphan")
    payments = db.relationship("Payment", foreign_keys="Payment.expense_id", back_populates="expense")

    def to_dict(self):
        return {
            "id": str(self.id),
            "business_id": str(self.business_id),
            "branch_id": str(self.branch_id) if self.branch_id else None,
            "supplier_id": str(self.supplier_id) if self.supplier_id else None,
            "supplier_name": self.supplier.display_name if self.supplier else None,
            "category_id": str(self.category_id) if self.category_id else None,
            "category_name": self.category.name if self.category else None,
            "financial_account_id": str(self.financial_account_id) if self.financial_account_id else None,
            "expense_number": self.expense_number,
            "date": self.date.isoformat() if self.date else None,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "description": self.description,
            "amount": float(self.amount) if self.amount else 0.0,
            "tax_amount": float(self.tax_amount) if self.tax_amount else 0.0,
            "total_amount": float(self.total_amount) if self.total_amount else 0.0,
            "payment_method": self.payment_method,
            "reference": self.reference,
            "notes": self.notes,
            "status": self.status,
            "attachments": [a.to_dict() for a in self.attachments],
            "payments": [p.to_dict() for p in self.payments],
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ExpenseAttachment(AuditedBase):
    __tablename__ = "expense_attachments"
    __table_args__ = (
        Index("ix_expense_attachments_expense_id", "expense_id"),
    )

    business_id = Column(
        SAB_UUID(),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    expense_id = Column(
        SAB_UUID(),
        ForeignKey("expenses.id", ondelete="CASCADE"),
        nullable=False,
    )

    filename = Column(String(255), nullable=False)
    original_filename = Column(String(255), nullable=False)
    mime_type = Column(String(100), nullable=True)
    file_size = Column(Integer, nullable=True)
    storage_path = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)

    expense = db.relationship("Expense", back_populates="attachments")

    def to_dict(self):
        return {
            "id": str(self.id),
            "business_id": str(self.business_id),
            "expense_id": str(self.expense_id),
            "filename": self.filename,
            "original_filename": self.original_filename,
            "mime_type": self.mime_type,
            "file_size": self.file_size,
            "storage_path": self.storage_path,
            "description": self.description,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Payment(AuditedBase):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("business_id", "payment_number", name="uq_payments_number"),
        Index("ix_payments_branch_id", "branch_id"),
        Index("ix_payments_customer_id", "customer_id"),
        Index("ix_payments_supplier_id", "supplier_id"),
        Index("ix_payments_invoice_id", "invoice_id"),
        Index("ix_payments_expense_id", "expense_id"),
        Index("ix_payments_date", "date"),
        Index("ix_payments_method", "payment_method"),
        Index("ix_payments_type", "payment_type"),
        Index("ix_payments_account_id", "financial_account_id"),
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
    supplier_id = Column(
        SAB_UUID(),
        ForeignKey("suppliers.id", ondelete="SET NULL"),
        nullable=True,
    )
    invoice_id = Column(
        SAB_UUID(),
        ForeignKey("invoices.id", ondelete="SET NULL"),
        nullable=True,
    )
    sale_invoice_id = Column(
        SAB_UUID(),
        ForeignKey("sale_invoices.id", ondelete="SET NULL"),
        nullable=True,
    )
    expense_id = Column(
        SAB_UUID(),
        ForeignKey("expenses.id", ondelete="SET NULL"),
        nullable=True,
    )
    financial_account_id = Column(
        SAB_UUID(),
        ForeignKey("financial_accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    counterparty_id = Column(
        SAB_UUID(),
        ForeignKey("financial_accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    related_transaction_id = Column(
        SAB_UUID(),
        ForeignKey("payments.id", ondelete="SET NULL"),
        nullable=True,
    )

    payment_number = Column(String(50), nullable=False)
    payment_type = Column(String(30), default="customer_receipt", nullable=False)
    amount = Column(Numeric(15, 2), default=0, nullable=False)
    payment_method = Column(String(20), nullable=False)
    date = Column(DateTime(timezone=True), nullable=False)
    reference = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)
    status = Column(String(20), default="completed", nullable=False)

    customer = db.relationship("Customer", backref="payments")
    supplier = db.relationship("Supplier", backref="payments")
    invoice = db.relationship("Invoice", foreign_keys=[invoice_id], back_populates="payments")
    sale_invoice = db.relationship("SaleInvoice", foreign_keys=[sale_invoice_id], back_populates="payments")
    expense = db.relationship("Expense", foreign_keys=[expense_id], back_populates="payments")
    financial_account = db.relationship("FinancialAccount", foreign_keys=[financial_account_id], backref="payments")
    counterparty_account = db.relationship("FinancialAccount", foreign_keys=[counterparty_id], backref="counterparty_payments")
    related_transaction = db.relationship("Payment", remote_side="Payment.id", backref="related_payments")
    allocations = db.relationship("PaymentAllocation", foreign_keys="PaymentAllocation.payment_id", back_populates="payment", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": str(self.id),
            "business_id": str(self.business_id),
            "branch_id": str(self.branch_id) if self.branch_id else None,
            "customer_id": str(self.customer_id) if self.customer_id else None,
            "supplier_id": str(self.supplier_id) if self.supplier_id else None,
            "invoice_id": str(self.invoice_id) if self.invoice_id else None,
            "expense_id": str(self.expense_id) if self.expense_id else None,
            "financial_account_id": str(self.financial_account_id) if self.financial_account_id else None,
            "counterparty_id": str(self.counterparty_id) if self.counterparty_id else None,
            "related_transaction_id": str(self.related_transaction_id) if self.related_transaction_id else None,
            "payment_number": self.payment_number,
            "payment_type": self.payment_type,
            "amount": float(self.amount) if self.amount else 0.0,
            "payment_method": self.payment_method,
            "date": self.date.isoformat() if self.date else None,
            "reference": self.reference,
            "notes": self.notes,
            "status": self.status,
            "allocations": [a.to_dict() for a in self.allocations],
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class PaymentAllocation(AuditedBase):
    __tablename__ = "payment_allocations"
    __table_args__ = (
        UniqueConstraint(
            "business_id", "payment_id", "invoice_id",
            name="uq_payment_allocations_payment_invoice",
        ),
        Index("ix_payment_allocations_payment_id", "payment_id"),
        Index("ix_payment_allocations_invoice_id", "invoice_id"),
    )

    business_id = Column(
        SAB_UUID(),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    payment_id = Column(
        SAB_UUID(),
        ForeignKey("payments.id", ondelete="CASCADE"),
        nullable=False,
    )
    invoice_id = Column(
        SAB_UUID(),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
    )
    amount = Column(Numeric(15, 2), default=0, nullable=False)

    payment = db.relationship("Payment", foreign_keys=[payment_id], back_populates="allocations")
    invoice = db.relationship("Invoice", foreign_keys=[invoice_id])

    def to_dict(self):
        return {
            "id": str(self.id),
            "business_id": str(self.business_id),
            "payment_id": str(self.payment_id),
            "invoice_id": str(self.invoice_id),
            "amount": float(self.amount) if self.amount else 0.0,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class FinancialAccount(AuditedBase):
    __tablename__ = "financial_accounts"
    __table_args__ = (
        UniqueConstraint("business_id", "code", name="uq_financial_accounts_business_code"),
        UniqueConstraint("business_id", "name", name="uq_financial_accounts_business_name"),
        Index("ix_financial_accounts_parent_id", "parent_id"),
        Index("ix_financial_accounts_account_type", "account_type"),
    )

    business_id = Column(
        SAB_UUID(),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    code = Column(String(20), nullable=False)
    name = Column(String(150), nullable=False)
    description = Column(Text, nullable=True)
    account_type = Column(String(20), nullable=False)
    parent_id = Column(
        SAB_UUID(),
        ForeignKey("financial_accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_control_account = Column(Boolean, default=False, nullable=False)
    default_currency = Column(String(3), nullable=True)
    is_bank_account = Column(Boolean, default=False, nullable=False)
    bank_details = Column(db.JSON, nullable=True)

    parent = db.relationship("FinancialAccount", remote_side="FinancialAccount.id", backref="children")

    def to_dict(self):
        return {
            "id": str(self.id),
            "business_id": str(self.business_id),
            "code": self.code,
            "name": self.name,
            "description": self.description,
            "account_type": self.account_type,
            "parent_id": str(self.parent_id) if self.parent_id else None,
            "is_control_account": self.is_control_account,
            "default_currency": self.default_currency,
            "is_bank_account": self.is_bank_account,
            "bank_details": self.bank_details or {},
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @validates('account_type')
    def validate_account_type(self, key, value):
        if value not in ACCOUNT_TYPES:
            raise ValueError(f"Invalid account type: {value}")
        return value


class JournalEntry(AuditedBase):
    __tablename__ = "journal_entries"
    __table_args__ = (
        UniqueConstraint("business_id", "entry_number", name="uq_journal_entries_number"),
        Index("ix_journal_entries_date", "date"),
        Index("ix_journal_entries_status", "status"),
        Index("ix_journal_entries_reference", "reference_type", "reference_id"),
    )

    business_id = Column(
        SAB_UUID(),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    entry_number = Column(String(50), nullable=False)
    date = Column(DateTime(timezone=True), nullable=False)
    description = Column(Text, nullable=False)
    reference_type = Column(String(30), nullable=True)
    reference_id = Column(SAB_UUID(), nullable=True)
    status = Column(String(20), default="draft", nullable=False)
    total_debit = Column(Numeric(15, 2), default=0, nullable=False)
    total_credit = Column(Numeric(15, 2), default=0, nullable=False)

    lines = db.relationship("JournalLine", back_populates="journal_entry", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": str(self.id),
            "business_id": str(self.business_id),
            "entry_number": self.entry_number,
            "date": self.date.isoformat() if self.date else None,
            "description": self.description,
            "reference_type": self.reference_type,
            "reference_id": str(self.reference_id) if self.reference_id else None,
            "status": self.status,
            "total_debit": float(self.total_debit) if self.total_debit else 0.0,
            "total_credit": float(self.total_credit) if self.total_credit else 0.0,
            "lines": [line.to_dict() for line in self.lines],
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class JournalLine(AuditedBase):
    __tablename__ = "journal_lines"
    __table_args__ = (
        Index("ix_journal_lines_entry_id", "journal_entry_id"),
        Index("ix_journal_lines_account_id", "account_id"),
        Index("ix_journal_lines_type", "line_type"),
    )

    business_id = Column(
        SAB_UUID(),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    journal_entry_id = Column(
        SAB_UUID(),
        ForeignKey("journal_entries.id", ondelete="CASCADE"),
        nullable=False,
    )
    account_id = Column(
        SAB_UUID(),
        ForeignKey("financial_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    line_type = Column(String(10), nullable=False)
    amount = Column(Numeric(15, 2), default=0, nullable=False)
    description = Column(Text, nullable=True)

    journal_entry = db.relationship("JournalEntry", back_populates="lines")
    account = db.relationship("FinancialAccount", backref="journal_lines")

    def to_dict(self):
        return {
            "id": str(self.id),
            "business_id": str(self.business_id),
            "journal_entry_id": str(self.journal_entry_id),
            "account_id": str(self.account_id),
            "account_code": self.account.code if self.account else None,
            "account_name": self.account.name if self.account else None,
            "line_type": self.line_type,
            "amount": float(self.amount) if self.amount else 0.0,
            "description": self.description,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    @validates('line_type')
    def validate_line_type(self, key, value):
        if value not in JOURNAL_LINE_TYPES:
            raise ValueError(f"Invalid line type: {value}")
        return value


class TaxConfig(AuditedBase):
    __tablename__ = "tax_configs"
    __table_args__ = (
        UniqueConstraint("business_id", "code", name="uq_tax_configs_business_code"),
        UniqueConstraint("business_id", "name", name="uq_tax_configs_business_name"),
        Index("ix_tax_configs_business_id", "business_id"),
    )

    business_id = Column(
        SAB_UUID(),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    name = Column(String(100), nullable=False)
    code = Column(String(20), nullable=False)
    rate = Column(Numeric(5, 2), default=0, nullable=False)
    is_inclusive = Column(Boolean, default=False, nullable=False)
    is_default = Column(Boolean, default=False, nullable=False)
    applies_to = Column(String(20), default="both", nullable=False)
    description = Column(Text, nullable=True)

    def to_dict(self):
        return {
            "id": str(self.id),
            "business_id": str(self.business_id),
            "name": self.name,
            "code": self.code,
            "rate": float(self.rate) if self.rate else 0.0,
            "is_inclusive": self.is_inclusive,
            "is_default": self.is_default,
            "applies_to": self.applies_to,
            "description": self.description,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @validates('applies_to')
    def validate_applies_to(self, key, value):
        if value not in ("sales", "purchases", "both"):
            raise ValueError(f"Invalid applies_to: {value}")
        return value