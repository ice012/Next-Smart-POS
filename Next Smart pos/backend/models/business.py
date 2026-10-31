import uuid as uuidlib

from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from .mixins import UUIDType as SAB_UUID

from ..extensions import db
from ..models.mixins import AuditedBase


def generate_slug(name):
    base = "".join(c if c.isalnum() else "-" for c in (name or "").lower()).strip("-")
    base = base or "business"
    base = base[:60]
    return f"{base}-{uuidlib.uuid4().hex[:8]}"


class Business(AuditedBase):
    __tablename__ = "businesses"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_businesses_slug"),
        UniqueConstraint("name", name="uq_businesses_name"),
        Index("ix_businesses_slug", "slug"),
    )

    name = Column(String(150), nullable=False)
    slug = Column(String(160), nullable=False)
    logo = Column(String(2048), nullable=True)
    default_country = Column(String(2), nullable=True)
    default_currency = Column(String(3), nullable=True)
    default_timezone = Column(String(100), nullable=True)

    # Contact details printed on invoices, quotations, receipts and reports.
    email = Column(String(255), nullable=True)
    phone = Column(String(50), nullable=True)
    website = Column(String(255), nullable=True)

    # Postal address.
    address_line1 = Column(String(255), nullable=True)
    address_line2 = Column(String(255), nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(100), nullable=True)
    postal_code = Column(String(32), nullable=True)

    subscription_tier = Column(String(20), nullable=False, default="free")
    subscription_plan_id = Column(
        SAB_UUID(),
        ForeignKey("subscription_plans.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    subscription_status = Column(String(20), nullable=False, default="trial")

    config = db.relationship(
        "BusinessConfig", back_populates="business", uselist=False, cascade="all, delete-orphan"
    )
    branches = db.relationship("Branch", back_populates="business", cascade="all, delete-orphan")
    roles = db.relationship("Role", back_populates="business", cascade="all, delete-orphan")
    memberships = db.relationship("BusinessUser", back_populates="business")
    users = db.relationship(
        "User", back_populates="business", foreign_keys="User.primary_business_id"
    )
    todos = db.relationship("Todo", back_populates="business", cascade="all, delete-orphan")
    subscriptions = db.relationship("Subscription", back_populates="business", cascade="all, delete-orphan")
    subscription_plan = db.relationship("SubscriptionPlan", backref="businesses")
    audit_logs = db.relationship("AuditLog", backref="business")
    notifications = db.relationship("Notification", backref="business")

    def to_dict(self):
        return {
            "id": str(self.id),
            "name": self.name,
            "slug": self.slug,
            "logo": self.logo,
            "default_country": self.default_country,
            "default_currency": self.default_currency,
            "default_timezone": self.default_timezone,
            "email": self.email,
            "phone": self.phone,
            "website": self.website,
            "address_line1": self.address_line1,
            "address_line2": self.address_line2,
            "city": self.city,
            "state": self.state,
            "postal_code": self.postal_code,
            "subscription_tier": self.subscription_tier,
            "subscription_plan_id": str(self.subscription_plan_id) if self.subscription_plan_id else None,
            "subscription_status": self.subscription_status,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class BusinessConfig(AuditedBase):
    __tablename__ = "business_configs"
    __table_args__ = (
        UniqueConstraint("business_id", name="uq_business_configs_business_id"),
    )

    business_id = Column(
        SAB_UUID(),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )

    country = Column(String(2), nullable=False)
    currency = Column(String(3), nullable=False)
    timezone = Column(String(100), nullable=False)

    financial_year_start_month = Column(Integer, default=1, nullable=False)
    invoice_prefix = Column(String(20), default="INV-", nullable=False)
    invoice_next_number = Column(Integer, default=1, nullable=False)
    invoice_terms = Column(String(1000), nullable=True)
    invoice_due_days = Column(Integer, default=30, nullable=False)
    tax_rules = Column(JSON, default=list, nullable=False)
    extra_settings = Column(JSON, default=dict, nullable=False)

    business = db.relationship("Business", back_populates="config")

    def to_dict(self):
        extra = dict(self.extra_settings or {})
        mpesa = extra.get("mpesa")
        if isinstance(mpesa, dict):
            # The Daraja consumer secret is write-only: it is blanked here so no
            # API response can leak it, and the flag lets the settings form
            # show "configured" without revealing the value.
            safe = dict(mpesa)
            safe["consumer_secret"] = ""
            safe["has_consumer_secret"] = bool(mpesa.get("consumer_secret"))
            extra["mpesa"] = safe
        return {
            "business_id": str(self.business_id),
            "country": self.country,
            "currency": self.currency,
            "timezone": self.timezone,
            "financial_year_start_month": self.financial_year_start_month,
            "invoice_prefix": self.invoice_prefix,
            "invoice_next_number": self.invoice_next_number,
            "invoice_terms": self.invoice_terms,
            "invoice_due_days": self.invoice_due_days,
            "tax_rules": self.tax_rules or [],
            "extra_settings": extra,
        }


class Branch(AuditedBase):
    __tablename__ = "branches"
    __table_args__ = (
        UniqueConstraint("business_id", "code", name="uq_branches_business_code"),
        UniqueConstraint("business_id", "name", name="uq_branches_business_name"),
        Index("ix_businesses_branches_business_id", "business_id"),
    )

    business_id = Column(
        SAB_UUID(),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name = Column(String(150), nullable=False)
    code = Column(String(50), nullable=False)
    phone = Column(String(50), nullable=True)
    email = Column(String(255), nullable=True)
    address = Column(JSON, nullable=True)
    is_head = Column(Boolean, default=False, nullable=False)

    business = db.relationship("Business", back_populates="branches")
    memberships = db.relationship("BusinessUser", back_populates="branch")

    def to_dict(self):
        return {
            "id": str(self.id),
            "business_id": str(self.business_id),
            "name": self.name,
            "code": self.code,
            "phone": self.phone,
            "email": self.email,
            "address": self.address or {},
            "is_head": self.is_head,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
