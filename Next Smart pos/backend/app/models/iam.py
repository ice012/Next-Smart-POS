import hashlib
import secrets

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    JSON,
    Numeric,
    String,
    UniqueConstraint,
)
from .mixins import UUIDType as SAB_UUID

from ..extensions import db
from ..models.mixins import AuditedBase


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_token() -> str:
    return secrets.token_urlsafe(32)


class Permission(AuditedBase):
    __tablename__ = "permissions"
    __table_args__ = (UniqueConstraint("name", name="uq_permissions_name"),)

    name = Column(String(120), nullable=False)
    resource = Column(String(80), nullable=False)
    action = Column(String(40), nullable=False)
    description = Column(String(500), nullable=True)

    role_permissions = db.relationship("RolePermission", back_populates="permission", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": str(self.id),
            "name": self.name,
            "resource": self.resource,
            "action": self.action,
            "description": self.description,
        }


class Role(AuditedBase):
    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("business_id", "name", name="uq_roles_business_name"),
        Index("ix_roles_business_id", "business_id"),
    )

    name = Column(String(100), nullable=False)
    description = Column(String(500), nullable=True)
    is_system = Column(Boolean, default=False, nullable=False)

    # Module codes (see ``module_catalog``) whose write/delete actions may only
    # be taken with an owner or admin unlocking them. Absent or false means the
    # role's own permission is enough, which is how every role behaved before
    # this column existed — an empty mapping is a valid, unremarkable state, not
    # "nothing configured yet".
    requires_owner_approval = Column(JSON, nullable=False, default=dict)

    business_id = Column(
        SAB_UUID(),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )

    business = db.relationship("Business", back_populates="roles")
    role_permissions = db.relationship("RolePermission", back_populates="role", cascade="all, delete-orphan")
    memberships = db.relationship("BusinessUser", back_populates="role")

    def to_dict(self, include_permissions=False):
        data = {
            "id": str(self.id),
            "business_id": str(self.business_id),
            "name": self.name,
            "description": self.description,
            "is_system": self.is_system,
            "is_active": self.is_active,
            "requires_owner_approval": dict(self.requires_owner_approval or {}),
        }
        if include_permissions:
            data["permission_names"] = [rp.permission.name for rp in self.role_permissions]
        return data


class RolePermission(AuditedBase):
    __tablename__ = "role_permissions"
    __table_args__ = (
        UniqueConstraint("role_id", "permission_id", name="uq_role_permissions_role_perm"),
        Index("ix_role_permissions_role_id", "role_id"),
        Index("ix_role_permissions_permission_id", "permission_id"),
    )

    role_id = Column(
        SAB_UUID(),
        ForeignKey("roles.id", ondelete="CASCADE"),
        nullable=False,
    )
    permission_id = Column(
        SAB_UUID(),
        ForeignKey("permissions.id", ondelete="CASCADE"),
        nullable=False,
    )

    role = db.relationship("Role", back_populates="role_permissions")
    permission = db.relationship("Permission")


class BusinessUser(AuditedBase):
    __tablename__ = "business_users"
    __table_args__ = (
        UniqueConstraint("business_id", "user_id", name="uq_business_users_business_user"),
        UniqueConstraint("business_id", "email", name="uq_business_users_business_email"),
        Index("ix_business_users_business_id", "business_id"),
        Index("ix_business_users_user_id", "user_id"),
        Index("ix_business_users_role_id", "role_id"),
    )

    business_id = Column(
        SAB_UUID(),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = Column(
        SAB_UUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )
    email = Column(String(255), nullable=True)
    role_id = Column(
        SAB_UUID(),
        ForeignKey("roles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    branch_id = Column(
        SAB_UUID(),
        ForeignKey("branches.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    is_default = Column(Boolean, default=False, nullable=False)
    invited_at = Column(DateTime(timezone=True), nullable=True)

    # Employee / staff compensation. ``payment_mode`` selects whether the
    # member is paid a fixed salary, earns a commission on sales, or a base
    # salary plus a commission. The rates are stored in the business' base
    # currency and are only meaningful when the corresponding mode is set.
    payment_mode = Column(String(20), nullable=True)
    commission_rate = Column(Numeric(12, 4), nullable=True)
    base_salary = Column(Numeric(12, 2), nullable=True)

    business = db.relationship("Business", back_populates="memberships")
    user = db.relationship("User", back_populates="memberships")
    role = db.relationship("Role")
    branch = db.relationship("Branch", back_populates="memberships")

    def to_dict(self):
        return {
            "id": str(self.id),
            "business_id": str(self.business_id),
            "user_id": str(self.user_id) if self.user_id else None,
            "email": self.email,
            "role_id": str(self.role_id),
            "branch_id": str(self.branch_id) if self.branch_id else None,
            "is_default": self.is_default,
            "is_active": self.is_active,
            "payment_mode": self.payment_mode,
            "commission_rate": float(self.commission_rate) if self.commission_rate is not None else None,
            "base_salary": float(self.base_salary) if self.base_salary is not None else None,
        }


class RefreshToken(AuditedBase):
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
        Index("ix_refresh_tokens_user_id", "user_id"),
        Index("ix_refresh_tokens_jti", "jti"),
    )

    user_id = Column(
        SAB_UUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    business_id = Column(
        SAB_UUID(),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=True,
    )
    jti = Column(String(64), nullable=False)
    token_hash = Column(String(64), nullable=False)
    issued_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    revoked = Column(Boolean, default=False, nullable=False)

    user = db.relationship("User", back_populates="refresh_tokens")


class PasswordResetToken(AuditedBase):
    __tablename__ = "password_reset_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_password_reset_tokens_token_hash"),
        Index("ix_password_reset_tokens_user_id", "user_id"),
    )

    user_id = Column(
        SAB_UUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash = Column(String(64), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used = Column(Boolean, default=False, nullable=False)

    user = db.relationship("User")


SYSTEM_ROLES = {
    "Business Owner": [
        "business.read", "business.write",
        "branch.read", "branch.write",
        "user.read", "user.write", "user.delete",
        "role.read", "role.write", "role.delete",
        "permission.read",
        "profile.read", "profile.write",
        "customer.read", "customer.write", "customer.delete",
        "supplier.read", "supplier.write", "supplier.delete",
        "item.read", "item.write", "item.delete",
        "category.read", "category.write", "category.delete",
        "uom.read", "uom.write", "uom.delete",
        "warehouse.read", "warehouse.write", "warehouse.delete",
        "inventory.read", "inventory.write",
        "sale.read", "sale.write",
        "quotation.read", "quotation.write", "quotation.delete",
        "payment.read", "payment.write",
        "purchase.read", "purchase.write",
        "supplier_payment.read", "supplier_payment.write",
        "customer_payment.read", "customer_payment.write",
        "todo.read", "todo.write", "todo.delete",
        "notification.read", "notification.write",
    ],
    "Admin": [
        "business.read", "business.write",
        "branch.read", "branch.write",
        "user.read", "user.write", "user.delete",
        "role.read", "role.write",
        "permission.read",
        "profile.read", "profile.write",
        "customer.read", "customer.write", "customer.delete",
        "supplier.read", "supplier.write", "supplier.delete",
        "item.read", "item.write", "item.delete",
        "category.read", "category.write", "category.delete",
        "uom.read", "uom.write", "uom.delete",
        "warehouse.read", "warehouse.write", "warehouse.delete",
        "inventory.read", "inventory.write",
        "sale.read", "sale.write",
        "quotation.read", "quotation.write", "quotation.delete",
        "payment.read", "payment.write",
        "purchase.read", "purchase.write",
        "supplier_payment.read", "supplier_payment.write",
        "customer_payment.read", "customer_payment.write",
        "todo.read", "todo.write", "todo.delete",
        "notification.read", "notification.write",
    ],
    "Accountant": [
        "business.read",
        "branch.read",
        "profile.read", "profile.write",
        "customer.read", "customer.write",
        "supplier.read", "supplier.write",
        "sale.read", "sale.write",
        "quotation.read", "quotation.write",
        "payment.read", "payment.write",
        "purchase.read",
        "supplier_payment.read", "supplier_payment.write",
        "customer_payment.read", "customer_payment.write",
        "notification.read",
    ],
    "Employee": [
        "branch.read",
        "profile.read", "profile.write",
        "customer.read",
        # Same as Cashier: create/update products, never delete them.
        "item.read", "item.write",
        "category.read", "uom.read",
        "sale.read", "sale.write",
        "payment.read",
        "quotation.read",
        # Every role that signs in needs its own inbox; the routes enforce it.
        "notification.read",
    ],
    # Till-side role. Unlike Employee it holds `payment.write` and
    # `business.read`, which the POS genuinely needs to take payment and to
    # read the M-Pesa config. Employee has payment.read only, so it cannot
    # actually complete a checkout.
    "Cashier": [
        "branch.read",
        "business.read",
        "profile.read", "profile.write",
        "customer.read",
        # `item.write` but deliberately not `item.delete`: a till operator needs
        # to key in a product the moment it is needed, but removing one from
        # the catalog is an owner/admin decision and is gated behind their
        # password on the delete endpoint.
        "item.read", "item.write",
        "category.read", "uom.read",
        "inventory.read",
        "sale.read", "sale.write",
        "payment.read", "payment.write",
        "earnings.read", "earnings.advance_request",
        "notification.read",
    ],
}

FINANCE_PERMISSIONS = [
    ("invoice.read", "invoice", "read", "View invoices"),
    ("invoice.write", "invoice", "write", "Create and update invoices"),
    ("expense.read", "expense", "read", "View expenses"),
    ("expense.write", "expense", "write", "Create and update expenses"),
    ("account.read", "account", "read", "View the chart of accounts"),
    ("account.write", "account", "write", "Create and update accounts"),
    ("journal.read", "journal", "read", "View journal entries"),
    ("journal.write", "journal", "write", "Create and post journal entries"),
    ("tax.read", "tax", "read", "View tax configurations"),
    ("tax.write", "tax", "write", "Create and update tax configurations"),
]

SYSTEM_ROLES["Business Owner"].extend(
    p for p, *_ in FINANCE_PERMISSIONS
)
SYSTEM_ROLES["Admin"].extend(
    p for p, *_ in FINANCE_PERMISSIONS
)
SYSTEM_ROLES["Accountant"].extend(
    p for p, *_ in FINANCE_PERMISSIONS
)
# Whoever can approve an advance needs to read the queue. Owner and Admin only:
# a Cashier may see their own figures, never anyone else's.
SYSTEM_ROLES["Business Owner"].extend([
    "earnings.read", "earnings.advance_approve",
    "report.read", "dashboard.read", "audit.read",
])
SYSTEM_ROLES["Admin"].extend([
    "earnings.read", "earnings.advance_approve",
    "report.read", "dashboard.read", "audit.read",
])
# Reports, the business dashboard and the audit trail are company-wide figures.
# Accountant already sees all the underlying finance data, so granting the
# roll-ups keeps that role coherent.
SYSTEM_ROLES["Accountant"].extend([
    "report.read", "dashboard.read", "audit.read",
])

PERMISSION_CATALOG = [
    ("business.read", "business", "read", "View business configuration"),
    ("business.write", "business", "write", "Create/update business configuration"),
    ("branch.read", "branch", "read", "View branches"),
    ("branch.write", "branch", "write", "Create/update branches"),
    ("user.read", "user", "read", "View users in the business"),
    ("user.write", "user", "write", "Create/update users in the business"),
    ("user.delete", "user", "delete", "Deactivate users in the business"),
    ("role.read", "role", "read", "View roles"),
    ("role.write", "role", "write", "Create/update roles"),
    ("role.delete", "role", "delete", "Delete custom roles"),
    ("permission.read", "permission", "read", "View the permission catalog"),
    ("profile.read", "profile", "read", "View own profile"),
    ("profile.write", "profile", "write", "Update own profile"),
    ("customer.read", "customer", "read", "View customers"),
    ("customer.write", "customer", "write", "Create/update customers"),
    ("customer.delete", "customer", "delete", "Delete customers"),
    ("supplier.read", "supplier", "read", "View suppliers"),
    ("supplier.write", "supplier", "write", "Create/update suppliers"),
    ("supplier.delete", "supplier", "delete", "Delete suppliers"),
    ("item.read", "item", "read", "View products and services"),
    ("item.write", "item", "write", "Create/update products and services"),
    ("item.delete", "item", "delete", "Delete products and services"),
    ("category.read", "category", "read", "View product categories"),
    ("category.write", "category", "write", "Create/update product categories"),
    ("category.delete", "category", "delete", "Delete product categories"),
    ("uom.read", "uom", "read", "View units of measure"),
    ("uom.write", "uom", "write", "Create/update units of measure"),
    ("uom.delete", "uom", "delete", "Delete units of measure"),
    ("warehouse.read", "warehouse", "read", "View warehouses"),
    ("warehouse.write", "warehouse", "write", "Create/update warehouses"),
    ("warehouse.delete", "warehouse", "delete", "Delete warehouses"),
    ("inventory.read", "inventory", "read", "View inventory balances and stock movements"),
    ("inventory.write", "inventory", "write", "Adjust stock levels"),
    ("sale.read", "sale", "read", "View sales invoices"),
    ("sale.write", "sale", "write", "Create and process sales"),
    ("quotation.read", "quotation", "read", "View quotations"),
    ("quotation.write", "quotation", "write", "Create and update quotations"),
    ("quotation.delete", "quotation", "delete", "Delete quotations"),
    ("payment.read", "payment", "read", "View payments"),
    ("payment.write", "payment", "write", "Record payments"),
    ("purchase.read", "purchase", "read", "View purchase orders"),
    ("purchase.write", "purchase", "write", "Create/update purchase orders"),
    ("supplier_payment.read", "supplier_payment", "read", "View supplier payments"),
    ("supplier_payment.write", "supplier_payment", "write", "Record supplier payments"),
    ("customer_payment.read", "customer_payment", "read", "View customer payments"),
    ("customer_payment.write", "customer_payment", "write", "Record customer payments"),
    ("todo.read", "todo", "read", "View to-do items"),
    ("todo.write", "todo", "write", "Create and update to-do items"),
    ("todo.delete", "todo", "delete", "Delete to-do items"),
    ("notification.read", "notification", "read", "View the in-app notification inbox"),
    ("notification.write", "notification", "write", "Raise notifications and run the alert checks"),
    ("earnings.read", "earnings", "read", "View own earnings and advance balance"),
    ("earnings.advance_request", "earnings", "advance_request", "Request an advance against own earnings"),
    ("earnings.advance_approve", "earnings", "advance_approve", "Review and decide staff advance requests"),
    ("report.read", "report", "read", "View business reports"),
    ("dashboard.read", "dashboard", "read", "View the business dashboard and KPIs"),
    ("audit.read", "audit", "read", "View the audit trail"),
    *FINANCE_PERMISSIONS,
]
