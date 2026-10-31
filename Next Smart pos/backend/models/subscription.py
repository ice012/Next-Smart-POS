from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from .mixins import UUIDType as SAB_UUID

from ..extensions import db
from ..models.mixins import AuditedBase


SUBSCRIPTION_STATUSES = {
    "trial": "trial",
    "active": "active",
    "past_due": "past_due",
    "cancelled": "cancelled",
    "suspended": "suspended",
    "expired": "expired",
}

SUBSCRIPTION_TIERS = {
    "free": "free",
    "starter": "starter",
    "professional": "professional",
    "enterprise": "enterprise",
}

BILLING_CYCLES = {"monthly", "yearly", "lifetime"}

SYSTEM_MODULES = [
    {"code": "core", "name": "Core", "is_core": True, "description": "Core platform functionality"},
    {"code": "customers", "name": "Customers", "is_core": False, "description": "Customer relationship management"},
    {"code": "suppliers", "name": "Suppliers", "is_core": False, "description": "Supplier management"},
    {"code": "items", "name": "Products & Services", "is_core": False, "description": "Catalog management"},
    {"code": "inventory", "name": "Inventory", "is_core": False, "description": "Stock tracking and control"},
    {"code": "sales", "name": "Sales", "is_core": False, "description": "Sales invoicing"},
    {"code": "purchases", "name": "Purchases", "is_core": False, "description": "Purchase order management"},
    {"code": "quotations", "name": "Quotations", "is_core": False, "description": "Quotation management"},
    {"code": "finance", "name": "Finance", "is_core": False, "description": "Ledger, accounts and taxes"},
    {"code": "expenses", "name": "Expenses", "is_core": False, "description": "Expense tracking"},
    {"code": "dashboard", "name": "Dashboard", "is_core": False, "description": "Executive dashboard and KPIs"},
    {"code": "reports", "name": "Reports", "is_core": False, "description": "Financial and operational reports"},
    {"code": "notifications", "name": "Notifications", "is_core": False, "description": "Alerts and notifications"},
    {"code": "audit", "name": "Audit Trail", "is_core": False, "description": "Audit logging and traceability"},
    {"code": "iam", "name": "Team & Permissions", "is_core": False, "description": "Users, roles and access control"},
    {"code": "todos", "name": "Tasks", "is_core": True, "description": "Task and to-do management"},
    {"code": "admin", "name": "Admin", "is_core": True, "description": "Administrative controls"},
]

FREE_PLAN_MODULES = ["core", "customers", "suppliers", "items", "inventory", "sales", "quotations", "finance", "expenses", "dashboard", "reports", "notifications", "audit", "todos"]
ENTERPRISE_PLAN_MODULES = [m["code"] for m in SYSTEM_MODULES]
# Quotations ships on every tier; Purchases stays Enterprise-only. Starter is
# the default signup tier, so it has to be a working POS on day one: inventory,
# reports and audit all ship here. Without them a paying Starter tenant loses
# stock tracking and reporting the moment it registers.
PROFESSIONAL_PLAN_MODULES = [m["code"] for m in SYSTEM_MODULES if m["code"] != "purchases"]
STARTER_PLAN_MODULES = ["core", "customers", "suppliers", "items", "inventory", "sales", "quotations", "finance", "expenses", "dashboard", "reports", "notifications", "audit", "iam", "todos"]


class SubscriptionPlan(AuditedBase):
    __tablename__ = "subscription_plans"
    __table_args__ = (
        UniqueConstraint("code", name="uq_subscription_plans_code"),
        UniqueConstraint("name", name="uq_subscription_plans_name"),
        Index("ix_subscription_plans_tier", "tier"),
    )

    name = Column(String(100), nullable=False)
    code = Column(String(30), nullable=False)
    description = Column(Text, nullable=True)
    tier = Column(String(20), nullable=False, default="free")
    price_cents = Column(Integer, nullable=False, default=0)
    currency = Column(String(3), nullable=False, default="USD")
    billing_cycle = Column(String(20), nullable=False, default="monthly")
    max_users = Column(Integer, nullable=True)
    max_branches = Column(Integer, nullable=True)
    max_invoices = Column(Integer, nullable=True)
    storage_mb = Column(Integer, nullable=False, default=0)
    # Length of the free trial a new signup on this plan starts with. Zero
    # means "no trial": the account is provisioned active and must pay
    # immediately. No Python-side default on purpose — every writer sets this
    # explicitly, and a column default would be baked into inserts issued by
    # the seed helpers that run against schemas predating the column.
    trial_days = Column(Integer, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_default = Column(Boolean, default=False, nullable=False)
    modules = Column(JSON, nullable=False, default=list)

    def to_dict(self):
        return {
            "id": str(self.id),
            "name": self.name,
            "code": self.code,
            "description": self.description,
            "tier": self.tier,
            "price_cents": self.price_cents,
            "currency": self.currency,
            "billing_cycle": self.billing_cycle,
            "max_users": self.max_users,
            "max_branches": self.max_branches,
            "max_invoices": self.max_invoices,
            "storage_mb": self.storage_mb,
            "trial_days": self.trial_days,
            "is_active": self.is_active,
            "is_default": self.is_default,
            "modules": self.modules or [],
            "is_active_flag": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Subscription(AuditedBase):
    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint("business_id", name="uq_subscriptions_business_id"),
        Index("ix_subscriptions_status", "status"),
        Index("ix_subscriptions_current_period_end", "current_period_end"),
    )

    business_id = Column(
        SAB_UUID(), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id = Column(
        SAB_UUID(), ForeignKey("subscription_plans.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status = Column(String(20), nullable=False, default="trial")
    started_at = Column(DateTime(timezone=True), nullable=False)
    current_period_start = Column(DateTime(timezone=True), nullable=False)
    current_period_end = Column(DateTime(timezone=True), nullable=True)
    trial_ends_at = Column(DateTime(timezone=True), nullable=True)
    canceled_at = Column(DateTime(timezone=True), nullable=True)
    cancel_at_period_end = Column(Boolean, default=False, nullable=False)

    plan = db.relationship("SubscriptionPlan")
    business = db.relationship("Business", back_populates="subscriptions")

    def to_dict(self):
        return {
            "id": str(self.id),
            "business_id": str(self.business_id),
            "plan_id": str(self.plan_id),
            "plan_name": self.plan.name if self.plan else None,
            "plan_tier": self.plan.tier if self.plan else None,
            "status": self.status,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "current_period_start": self.current_period_start.isoformat() if self.current_period_start else None,
            "current_period_end": self.current_period_end.isoformat() if self.current_period_end else None,
            "trial_ends_at": self.trial_ends_at.isoformat() if self.trial_ends_at else None,
            "canceled_at": self.canceled_at.isoformat() if self.canceled_at else None,
            "cancel_at_period_end": self.cancel_at_period_end,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class SystemModule(AuditedBase):
    __tablename__ = "system_modules"
    __table_args__ = (UniqueConstraint("code", name="uq_system_modules_code"),)

    code = Column(String(50), nullable=False)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    is_enabled = Column(Boolean, default=True, nullable=False)
    is_core = Column(Boolean, default=False, nullable=False)
    min_plan_tier = Column(String(20), nullable=True)

    def to_dict(self):
        return {
            "id": str(self.id),
            "code": self.code,
            "name": self.name,
            "description": self.description,
            "is_enabled": self.is_enabled,
            "is_core": self.is_core,
            "min_plan_tier": self.min_plan_tier,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


def get_default_plan_modules(tier):
    return {
        "free": FREE_PLAN_MODULES,
        "starter": STARTER_PLAN_MODULES,
        "professional": PROFESSIONAL_PLAN_MODULES,
        "enterprise": ENTERPRISE_PLAN_MODULES,
    }.get(tier, FREE_PLAN_MODULES)
