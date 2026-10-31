from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from .mixins import UUIDType as SAB_UUID

from ..extensions import db
from ..models.mixins import AuditedBase


class ProductCategory(AuditedBase):
    __tablename__ = "product_categories"
    __table_args__ = (
        UniqueConstraint("business_id", "name", name="uq_categories_business_name"),
        Index("ix_categories_parent_id", "parent_id"),
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
        ForeignKey("product_categories.id", ondelete="SET NULL"),
        nullable=True,
    )

    parent = db.relationship("ProductCategory", remote_side="ProductCategory.id", backref="children")

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


class UnitOfMeasure(AuditedBase):
    __tablename__ = "uom_units"
    __table_args__ = (
        UniqueConstraint("business_id", "code", name="uq_uom_business_code"),
        UniqueConstraint("business_id", "name", name="uq_uom_business_name"),
    )

    business_id = Column(
        SAB_UUID(),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(50), nullable=False)
    code = Column(String(20), nullable=False)

    def to_dict(self):
        return {
            "id": str(self.id),
            "business_id": str(self.business_id),
            "name": self.name,
            "code": self.code,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Item(AuditedBase):
    __tablename__ = "items"
    __table_args__ = (
        UniqueConstraint("business_id", "sku", name="uq_items_business_sku"),
        UniqueConstraint("business_id", "name", name="uq_items_business_name"),
        Index("ix_items_business_type", "business_id", "type"),
        Index("ix_items_barcode", "barcode"),
        Index("ix_items_category_id", "category_id"),
    )

    business_id = Column(
        SAB_UUID(),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    type = Column(String(20), default="product", nullable=False)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    sku = Column(String(100), nullable=True)
    barcode = Column(String(200), nullable=True)
    image_url = Column(String(2048), nullable=True)

    category_id = Column(
        SAB_UUID(),
        ForeignKey("product_categories.id", ondelete="SET NULL"),
        nullable=True,
    )
    cost_price = Column(Numeric(15, 2), default=0, nullable=False)
    selling_price = Column(Numeric(15, 2), default=0, nullable=False)

    minimum_stock = Column(Numeric(15, 2), default=0, nullable=False)
    is_inventory_tracked = Column(Boolean, default=False, nullable=False)

    uom_id = Column(
        SAB_UUID(),
        ForeignKey("uom_units.id", ondelete="SET NULL"),
        nullable=True,
    )

    tax_config = Column(db.JSON, default=dict, nullable=False)

    category = db.relationship("ProductCategory", backref="items")
    uom = db.relationship("UnitOfMeasure", backref="items")

    @property
    def quantity_on_hand(self):
        from ..models.inventory import InventoryBalance
        from sqlalchemy import func, select
        total = db.session.execute(
            select(func.coalesce(func.sum(InventoryBalance.quantity_on_hand), 0))
            .where(InventoryBalance.item_id == self.id)
        ).scalar()
        return float(total) if total else 0.0

    def to_dict(self):
        return {
            "id": str(self.id),
            "business_id": str(self.business_id),
            "type": self.type,
            "name": self.name,
            "description": self.description,
            "sku": self.sku,
            "barcode": self.barcode,
            "image_url": self.image_url,
            "category_id": str(self.category_id) if self.category_id else None,
            "category_name": self.category.name if self.category else None,
            "cost_price": float(self.cost_price) if self.cost_price else 0.0,
            "selling_price": float(self.selling_price) if self.selling_price else 0.0,
            "quantity": self.quantity_on_hand,
            "minimum_stock": float(self.minimum_stock) if self.minimum_stock else 0.0,
            "is_inventory_tracked": self.is_inventory_tracked,
            "uom_id": str(self.uom_id) if self.uom_id else None,
            "uom_code": self.uom.code if self.uom else None,
            "tax_config": self.tax_config or {},
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
