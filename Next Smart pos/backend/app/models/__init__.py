from ..extensions import db
from .mixins import AuditedBase, UUIDMixin, TimestampMixin, AuditMixin, touch
from .business import Business, BusinessConfig, Branch, generate_slug
from .identity import User
from .iam import (
    Permission,
    Role,
    RolePermission,
    BusinessUser,
    RefreshToken,
    PasswordResetToken,
    SYSTEM_ROLES,
    PERMISSION_CATALOG,
    hash_token,
)
from .customers import Customer, CustomerPayment
from .suppliers import Supplier, SupplierPayment
from .items import ProductCategory, UnitOfMeasure, Item
from .inventory import (
    Warehouse,
    InventoryBalance,
    StockMovement,
    StockAdjustment,
    StockAdjustmentItem,
)
from .sales import SaleInvoice, SaleItem
from .quotations import Quotation, QuotationItem
from .purchases import PurchaseOrder, PurchaseOrderItem
from .finance import (
    Invoice,
    InvoiceItem,
    ExpenseCategory,
    Expense,
    ExpenseAttachment,
    Payment,
    PaymentAllocation,
    FinancialAccount,
    JournalEntry,
    JournalLine,
    TaxConfig,
    INVOICE_STATUSES,
    EXPENSE_STATUSES,
    PAYMENT_METHODS,
    PAYMENT_TYPES,
    ACCOUNT_TYPES,
    JOURNAL_STATUSES,
    JOURNAL_LINE_TYPES,
)
from .todos import Todo
from .subscription import (
    SubscriptionPlan,
    Subscription,
    SystemModule,
    SUBSCRIPTION_STATUSES,
    SUBSCRIPTION_TIERS,
    BILLING_CYCLES,
    SYSTEM_MODULES,
    get_default_plan_modules,
)
from .audit_log import AuditLog
from .earnings import EarningsAdvanceRequest, ADVANCE_STATUSES, BALANCE_HOLDING_STATUSES
from .notification import Notification, NOTIFICATION_CATEGORIES, NOTIFICATION_PRIORITIES
# Imported last: app.mpesa.models builds on AuditedBase, which is only bound by
# the time this line runs. Putting it earlier would import the model registry
# while it is still being populated.
from ..mpesa.models import MpesaStkRequest, MPESA_REQUEST_STATUSES, TERMINAL_STATUSES

__all__ = [
    "db",
    "AuditedBase",
    "UUIDMixin",
    "TimestampMixin",
    "AuditMixin",
    "touch",
    "Business",
    "BusinessConfig",
    "Branch",
    "generate_slug",
    "User",
    "Permission",
    "Role",
    "RolePermission",
    "BusinessUser",
    "RefreshToken",
    "PasswordResetToken",
    "SYSTEM_ROLES",
    "PERMISSION_CATALOG",
    "hash_token",
    "Customer",
    "CustomerPayment",
    "Supplier",
    "SupplierPayment",
    "ProductCategory",
    "UnitOfMeasure",
    "Item",
    "Warehouse",
    "InventoryBalance",
    "StockMovement",
    "StockAdjustment",
    "StockAdjustmentItem",
    "SaleInvoice",
    "SaleItem",
    "Payment",
    "PaymentAllocation",
    "Quotation",
    "QuotationItem",
    "PurchaseOrder",
    "PurchaseOrderItem",
    "Invoice",
    "InvoiceItem",
    "ExpenseCategory",
    "Expense",
    "ExpenseAttachment",
    "FinancialAccount",
    "JournalEntry",
    "JournalLine",
    "TaxConfig",
    "INVOICE_STATUSES",
    "EXPENSE_STATUSES",
    "PAYMENT_METHODS",
    "PAYMENT_TYPES",
    "ACCOUNT_TYPES",
    "JOURNAL_STATUSES",
    "JOURNAL_LINE_TYPES",
    "Todo",
    "SubscriptionPlan",
    "Subscription",
    "SystemModule",
    "SUBSCRIPTION_STATUSES",
    "SUBSCRIPTION_TIERS",
    "BILLING_CYCLES",
    "SYSTEM_MODULES",
    "get_default_plan_modules",
    "AuditLog",
    "EarningsAdvanceRequest",
    "ADVANCE_STATUSES",
    "BALANCE_HOLDING_STATUSES",
    "Notification",
    "NOTIFICATION_CATEGORIES",
    "NOTIFICATION_PRIORITIES",
    "MpesaStkRequest",
    "MPESA_REQUEST_STATUSES",
    "TERMINAL_STATUSES",
]
