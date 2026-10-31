from decimal import Decimal
from sqlalchemy import func, select
from sqlalchemy import or_

from ..exceptions import NotFoundError, ConflictError, ValidationError, BusinessError
from ..extensions import db
from ..models import Item, ProductCategory, UnitOfMeasure
from ..utils.db import commit
from ..utils.pagination import paginate
from ..utils.security import is_valid_uuid, to_uuid


def _check_item_owned(business_id, item_id):
    item = db.session.get(Item, item_id)
    if item is None or item.business_id != business_id:
        raise NotFoundError("Item not found")
    return item


def _check_category_owned(business_id, category_id):
    cat = db.session.get(ProductCategory, category_id)
    if cat is None or cat.business_id != business_id:
        raise NotFoundError("Category not found")
    return cat


def _check_uom_owned(business_id, uom_id):
    uom = db.session.get(UnitOfMeasure, uom_id)
    if uom is None or uom.business_id != business_id:
        raise NotFoundError("Unit of measure not found")
    return uom


def _next_sku(business_id):
    count = db.session.scalar(
        select(func.count()).where(
            Item.business_id == business_id,
            Item.type == "product",
        )
    ) or 0
    return f"SKU-{count + 1:06d}"


def list_items(business_id, pagination, item_type=None, category_id=None, is_active=True):
    stmt = select(Item).where(Item.business_id == business_id)
    if is_active is not None:
        stmt = stmt.where(Item.is_active.is_(is_active))
    if item_type:
        stmt = stmt.where(Item.type == item_type)
    if category_id:
        stmt = stmt.where(Item.category_id == category_id)

    query = pagination or _default_page()
    if getattr(query, "q", None):
        q = query.q
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                Item.name.ilike(like),
                Item.sku.ilike(like),
                Item.barcode.ilike(like),
                Item.description.ilike(like),
            )
        )

    stmt = stmt.order_by(Item.name.asc())
    items, total, pages = paginate(stmt, query.page, query.per_page)
    return [i.to_dict() for i in items], total, pages


def get_item(business_id, item_id):
    item = _check_item_owned(business_id, item_id)
    return item.to_dict()


def _apply_service_defaults(data):
    """Services are never stock-managed, so cost, reorder level and the
    inventory-tracking flag are forced to their "not applicable" values.

    This keeps the rule in one place so a service cannot become
    inventory-tracked regardless of what the client sends.
    """
    if data.type != "service":
        return
    data.is_inventory_tracked = False
    data.minimum_stock = 0
    data.cost_price = 0


def create_item(business_id, data, actor_id):
    # Products default to inventory-tracked, but an explicit choice is honoured.
    if data.type == "product" and data.is_inventory_tracked is None:
        data.is_inventory_tracked = True

    _apply_service_defaults(data)

    if data.type == "product" and not data.sku:
        data.sku = _next_sku(business_id)

    existing = db.session.scalar(
        select(Item).where(
            Item.business_id == business_id,
            Item.name == data.name,
        )
    )
    if existing is not None:
        raise ConflictError("An item with this name already exists")

    if data.barcode:
        dup = db.session.scalar(
            select(Item).where(
                Item.business_id == business_id,
                Item.barcode == data.barcode,
            )
        )
        if dup is not None:
            raise ConflictError("An item with this barcode already exists")

    item = Item(
        business_id=business_id,
        type=data.type,
        name=data.name,
        description=data.description,
        sku=data.sku,
        barcode=data.barcode,
        category_id=data.category_id,
        cost_price=Decimal(str(data.cost_price)),
        selling_price=Decimal(str(data.selling_price)),
        minimum_stock=Decimal(str(data.minimum_stock)),
        is_inventory_tracked=bool(data.is_inventory_tracked),
        uom_id=data.uom_id,
        tax_config=data.tax_config or {},
    )
    item.created_by = actor_id
    item.updated_by = actor_id
    db.session.add(item)
    db.session.flush()

    if item.is_inventory_tracked:
        from .inventory_service import ensure_inventory_balance
        ensure_inventory_balance(business_id, item.id, actor_id)

    from .audit_log_service import log_event
    log_event(business_id, "create", "item", actor_id, item.id, {
        "sku": item.sku, "name": item.name,
    })
    commit()
    return item.to_dict()


def update_item(business_id, item_id, data, actor_id):
    item = _check_item_owned(business_id, item_id)
    if data.type is not None:
        item.type = data.type

    # A service is never stock-managed, so the stock fields are dropped before
    # the generic copy loop can re-apply whatever the client sent.
    if item.type == "service":
        data.cost_price = 0
        data.minimum_stock = 0
        data.is_inventory_tracked = False

    for field in ("name", "description", "sku", "barcode", "category_id",
                  "cost_price", "selling_price", "minimum_stock", "uom_id", "tax_config"):
        value = getattr(data, field, None)
        if value is not None:
            if field in ("cost_price", "selling_price", "minimum_stock"):
                setattr(item, field, Decimal(str(value)))
            else:
                setattr(item, field, value)
    if data.is_inventory_tracked is not None:
        item.is_inventory_tracked = data.is_inventory_tracked
    if data.is_active is not None:
        item.is_active = data.is_active
    item.updated_by = actor_id
    from .audit_log_service import log_event
    log_event(business_id, "update", "item", actor_id, item.id, {
        "sku": item.sku, "name": item.name, "is_active": item.is_active,
    })
    commit()
    return item.to_dict()


def set_item_image(business_id, item_id, image_url, actor_id):
    """Attach or clear an item image, returning the item and the old URL.

    The previous URL is handed back so the caller can unlink the orphaned file
    only after the row has been committed successfully.
    """
    item = _check_item_owned(business_id, item_id)
    previous = item.image_url
    item.image_url = image_url
    item.updated_by = actor_id
    from .audit_log_service import log_event
    log_event(
        business_id, "update", "item", actor_id, item.id,
        {"sku": item.sku, "name": item.name, "image_url": image_url},
    )
    commit()
    return item.to_dict(), previous


def delete_item(business_id, item_id, approved_by=None):
    """Delete an item outright.

    ``approved_by`` is the owner/admin whose password unlocked a delete made by
    a role that does not hold ``item.delete``. It is recorded on the audit entry
    so the trail names the person who stood behind the removal, not only the
    operator who pressed the button.
    """
    item = _check_item_owned(business_id, item_id)
    if item.type == "product" and item.is_inventory_tracked:
        from .inventory_service import has_outstanding_balance
        if has_outstanding_balance(item.id):
            raise BusinessError("Cannot delete item with existing stock. Adjust stock to zero first.")
    from .audit_log_service import log_event
    details = {"sku": item.sku, "name": item.name}
    if approved_by:
        details["approved_by"] = str(approved_by)
    log_event(business_id, "delete", "item", None, item.id, details)
    image_url = item.image_url
    db.session.delete(item)
    commit()
    # Hand the orphaned image back so the caller can unlink it post-commit.
    return image_url


def lookup_by_barcode(business_id, barcode):
    item = db.session.scalar(
        select(Item).where(
            Item.business_id == business_id,
            Item.barcode == barcode,
        )
    )
    if item is None:
        raise NotFoundError("Item not found")
    return item.to_dict()


def search_items(business_id, term, limit=20):
    """Text search for the till, with an exact barcode hit ranked first.

    Search endpoints return an empty list when nothing matches. A miss is not a
    missing resource, so it must not surface as a 404: the POS and the
    quotation and purchase-order pickers call this on every keystroke, and a
    404 there both spams the console and looks like a server fault.
    """
    term = (term or "").strip()
    if not term:
        return []

    # A scanner emits a complete barcode in one go, so an exact hit can go
    # straight to the top rather than competing with name matches.
    exact = db.session.scalar(
        select(Item).where(
            Item.business_id == business_id,
            Item.is_active.is_(True),
            Item.barcode == term,
        )
    )
    if exact is not None:
        return [exact.to_dict()]

    like = f"%{term}%"
    stmt = (
        select(Item)
        .where(
            Item.business_id == business_id,
            Item.is_active.is_(True),
            or_(
                Item.name.ilike(like),
                Item.sku.ilike(like),
                Item.barcode.ilike(like),
                Item.description.ilike(like),
            ),
        )
        # Name matches are what a human is typing, so they lead.
        .order_by(Item.name.asc())
        .limit(limit)
    )
    return [i.to_dict() for i in db.session.scalars(stmt)]


def list_categories(business_id):
    cats = db.session.scalars(
        select(ProductCategory).where(ProductCategory.business_id == business_id)
        .order_by(ProductCategory.name.asc())
    ).all()
    return [c.to_dict() for c in cats]


def get_category(business_id, category_id):
    cat = _check_category_owned(business_id, category_id)
    return cat.to_dict()


def create_category(business_id, data, actor_id):
    if data.parent_id:
        parent = _check_category_owned(business_id, data.parent_id)
        if parent.parent_id is not None:
            pass  # allow deeper nesting; could enforce single-level here

    existing = db.session.scalar(
        select(ProductCategory).where(
            ProductCategory.business_id == business_id,
            ProductCategory.name == data.name,
        )
    )
    if existing is not None:
        raise ConflictError("A category with this name already exists")

    cat = ProductCategory(
        business_id=business_id,
        name=data.name,
        description=data.description,
        parent_id=data.parent_id,
    )
    cat.created_by = actor_id
    cat.updated_by = actor_id
    db.session.add(cat)
    commit()
    return cat.to_dict()


def update_category(business_id, category_id, data, actor_id):
    cat = _check_category_owned(business_id, category_id)
    if data.name is not None:
        existing = db.session.scalar(
            select(ProductCategory).where(
                ProductCategory.business_id == business_id,
                ProductCategory.name == data.name,
                ProductCategory.id != category_id,
            )
        )
        if existing is not None:
            raise ConflictError("A category with this name already exists")
        cat.name = data.name
    if data.description is not None:
        cat.description = data.description
    if data.parent_id is not None:
        if data.parent_id == str(category_id):
            raise ValidationError("A category cannot be its own parent")
        _check_category_owned(business_id, data.parent_id)
        cat.parent_id = to_uuid(data.parent_id)
    if data.is_active is not None:
        cat.is_active = data.is_active
    cat.updated_by = actor_id
    commit()
    return cat.to_dict()


def delete_category(business_id, category_id):
    cat = _check_category_owned(business_id, category_id)
    db.session.delete(cat)
    commit()


def list_uoms(business_id, is_active=True):
    stmt = select(UnitOfMeasure).where(UnitOfMeasure.business_id == business_id)
    if is_active is not None:
        stmt = stmt.where(UnitOfMeasure.is_active.is_(is_active))
    stmt = stmt.order_by(UnitOfMeasure.name.asc())
    return [u.to_dict() for u in db.session.scalars(stmt).all()]


def get_uom(business_id, uom_id):
    uom = _check_uom_owned(business_id, uom_id)
    return uom.to_dict()


def create_uom(business_id, data, actor_id):
    existing = db.session.scalar(
        select(UnitOfMeasure).where(
            UnitOfMeasure.business_id == business_id,
            UnitOfMeasure.code == data.code,
        )
    )
    if existing is not None:
        raise ConflictError("A unit with this code already exists")
    uom = UnitOfMeasure(
        business_id=business_id,
        name=data.name,
        code=data.code,
    )
    uom.created_by = actor_id
    uom.updated_by = actor_id
    db.session.add(uom)
    commit()
    return uom.to_dict()


def update_uom(business_id, uom_id, data, actor_id):
    uom = _check_uom_owned(business_id, uom_id)
    if data.name is not None:
        uom.name = data.name
    if data.code is not None:
        existing = db.session.scalar(
            select(UnitOfMeasure).where(
                UnitOfMeasure.business_id == business_id,
                UnitOfMeasure.code == data.code,
                UnitOfMeasure.id != uom_id,
            )
        )
        if existing is not None:
            raise ConflictError("A unit with this code already exists")
        uom.code = data.code
    if data.is_active is not None:
        uom.is_active = data.is_active
    uom.updated_by = actor_id
    commit()
    return uom.to_dict()


def delete_uom(business_id, uom_id):
    uom = _check_uom_owned(business_id, uom_id)
    db.session.delete(uom)
    commit()


def _default_page():
    from ..schemas import PaginationSchema
    return PaginationSchema()
