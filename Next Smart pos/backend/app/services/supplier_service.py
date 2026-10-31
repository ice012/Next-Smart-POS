from decimal import Decimal
from sqlalchemy import func, select
from sqlalchemy import or_

from ..exceptions import NotFoundError
from ..extensions import db
from ..models import Supplier
from ..utils.db import commit
from ..utils.pagination import paginate


def _ensure_number(business_id):
    existing = db.session.scalar(
        select(func.max(Supplier.supplier_number)).where(
            Supplier.business_id == business_id
        )
    )
    seq = 1
    if existing:
        try:
            seq = int(existing.split("-")[-1]) + 1
        except (ValueError, IndexError):
            seq = 1
    return f"SUP-{seq:04d}"


def list_suppliers(business_id, pagination, is_active=True):
    stmt = select(Supplier).where(
        Supplier.business_id == business_id,
    )
    if is_active is not None:
        stmt = stmt.where(Supplier.is_active.is_(is_active))

    query = pagination or _default_page()
    if getattr(query, "q", None):
        q = query.q
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                Supplier.display_name.ilike(like),
                Supplier.email.ilike(like),
                Supplier.phone.ilike(like),
                Supplier.company_name.ilike(like),
                Supplier.supplier_number.ilike(like),
            )
        )

    stmt = stmt.order_by(Supplier.display_name.asc())
    items, total, pages = paginate(stmt, query.page, query.per_page)
    return [s.to_dict() for s in items], total, pages


def get_supplier(business_id, supplier_id):
    supplier = db.session.get(Supplier, supplier_id)
    if supplier is None or supplier.business_id != business_id:
        raise NotFoundError("Supplier not found")
    return supplier.to_dict()


def create_supplier(business_id, data, actor_id, branch_id=None):
    number = _ensure_number(business_id)
    supplier = Supplier(
        business_id=business_id,
        branch_id=branch_id,
        supplier_number=number,
        display_name=data.display_name,
        company_name=data.company_name,
        contact_person=data.contact_person,
        tax_id=data.tax_id,
        email=data.email,
        phone=data.phone,
        address=data.address,
        notes=data.notes,
        statement_balance=Decimal("0"),
    )
    supplier.created_by = actor_id
    supplier.updated_by = actor_id
    db.session.add(supplier)
    commit()
    return supplier.to_dict()


def update_supplier(business_id, supplier_id, data, actor_id):
    supplier = db.session.get(Supplier, supplier_id)
    if supplier is None or supplier.business_id != business_id:
        raise NotFoundError("Supplier not found")
    for field in ("display_name", "company_name", "contact_person",
                  "tax_id", "email", "phone", "address", "notes"):
        value = getattr(data, field, None)
        if value is not None:
            setattr(supplier, field, value)
    if data.is_active is not None:
        supplier.is_active = data.is_active
    supplier.updated_by = actor_id
    commit()
    return supplier.to_dict()


def delete_supplier(business_id, supplier_id):
    supplier = db.session.get(Supplier, supplier_id)
    if supplier is None or supplier.business_id != business_id:
        raise NotFoundError("Supplier not found")
    supplier.is_active = False
    commit()


def search_suppliers(business_id, query_str, limit=20):
    like = f"%{query_str}%"
    stmt = (
        select(Supplier)
        .where(
            Supplier.business_id == business_id,
            Supplier.is_active.is_(True),
            or_(
                Supplier.display_name.ilike(like),
                Supplier.email.ilike(like),
                Supplier.phone.ilike(like),
                Supplier.company_name.ilike(like),
                Supplier.supplier_number.ilike(like),
            ),
        )
        .order_by(Supplier.display_name.asc())
        .limit(limit)
    )
    results = db.session.scalars(stmt).all()
    return [s.to_dict() for s in results]


def _default_page():
    from ..schemas import PaginationSchema
    return PaginationSchema()
