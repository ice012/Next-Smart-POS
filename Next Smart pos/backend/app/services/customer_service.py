from datetime import datetime, timezone

from decimal import Decimal
from sqlalchemy import func, select
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError, PendingRollbackError

from ..exceptions import NotFoundError, ConflictError, ValidationError
from ..extensions import db
from ..models import Customer
from ..utils.db import commit
from ..utils.pagination import paginate
from ..utils.security import is_valid_uuid, to_uuid


def _ensure_number(business_id):
    existing = db.session.scalar(
        select(func.max(Customer.customer_number)).where(
            Customer.business_id == business_id
        )
    )
    seq = 1
    if existing:
        try:
            seq = int(existing.split("-")[-1]) + 1
        except (ValueError, IndexError):
            seq = 1
    return f"CUST-{seq:04d}"


def list_customers(business_id, pagination, customer_type=None, is_active=True):
    stmt = select(Customer).where(
        Customer.business_id == business_id,
    )
    if is_active is not None:
        stmt = stmt.where(Customer.is_active.is_(is_active))
    if customer_type:
        stmt = stmt.where(Customer.customer_type == customer_type)

    query = pagination or _default_page()
    if getattr(query, "q", None):
        q = query.q
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                Customer.display_name.ilike(like),
                Customer.email.ilike(like),
                Customer.phone.ilike(like),
                Customer.company_name.ilike(like),
                Customer.customer_number.ilike(like),
            )
        )

    stmt = stmt.order_by(Customer.display_name.asc())
    items, total, pages = paginate(stmt, query.page, query.per_page)
    return [c.to_dict() for c in items], total, pages


def get_customer(business_id, customer_id):
    customer = db.session.get(Customer, customer_id)
    if customer is None or customer.business_id != business_id:
        raise NotFoundError("Customer not found")
    return customer.to_dict()


def create_customer(business_id, data, actor_id, branch_id=None):
    number = _ensure_number(business_id)
    customer = Customer(
        business_id=business_id,
        branch_id=branch_id,
        customer_number=number,
        display_name=data.display_name,
        customer_type=data.customer_type,
        first_name=data.first_name,
        last_name=data.last_name,
        company_name=data.company_name,
        contact_person=data.contact_person,
        tax_id=data.tax_id,
        email=data.email,
        phone=data.phone,
        address=data.address,
        notes=data.notes,
        statement_balance=Decimal("0"),
    )
    customer.created_by = actor_id
    customer.updated_by = actor_id
    db.session.add(customer)
    from .audit_log_service import log_event
    log_event(business_id, "create", "customer", actor_id, customer.id, {
        "customer_number": customer.customer_number,
        "display_name": customer.display_name,
    })
    commit()
    return customer.to_dict()


def update_customer(business_id, customer_id, data, actor_id):
    customer = db.session.get(Customer, customer_id)
    if customer is None or customer.business_id != business_id:
        raise NotFoundError("Customer not found")
    for field in ("display_name", "first_name", "last_name", "company_name",
                  "contact_person", "tax_id", "email", "phone", "address", "notes"):
        value = getattr(data, field, None)
        if value is not None:
            setattr(customer, field, value)
    if data.customer_type is not None:
        customer.customer_type = data.customer_type
    if data.is_active is not None:
        customer.is_active = data.is_active
    customer.updated_by = actor_id
    from .audit_log_service import log_event
    log_event(business_id, "update", "customer", actor_id, customer.id, {
        "customer_number": customer.customer_number,
        "is_active": customer.is_active,
    })
    commit()
    return customer.to_dict()


def delete_customer(business_id, customer_id):
    customer = db.session.get(Customer, customer_id)
    if customer is None or customer.business_id != business_id:
        raise NotFoundError("Customer not found")
    customer.is_active = False
    from .audit_log_service import log_event
    log_event(business_id, "delete", "customer", None, customer.id, {
        "customer_number": customer.customer_number,
    })
    commit()


def search_customers(business_id, query_str, limit=20):
    like = f"%{query_str}%"
    stmt = (
        select(Customer)
        .where(
            Customer.business_id == business_id,
            Customer.is_active.is_(True),
            or_(
                Customer.display_name.ilike(like),
                Customer.email.ilike(like),
                Customer.phone.ilike(like),
                Customer.company_name.ilike(like),
                Customer.customer_number.ilike(like),
            ),
        )
        .order_by(Customer.display_name.asc())
        .limit(limit)
    )
    results = db.session.scalars(stmt).all()
    return [c.to_dict() for c in results]


def _find_by_display_name(business_id, display_name):
    """Match on name regardless of active state.

    uq_customers_business_name spans (business_id, display_name) only, so a
    soft-deleted customer still reserves its name. Ignoring is_active here is
    what lets the caller report that clash instead of hitting the constraint.
    """
    return db.session.scalar(
        select(Customer).where(
            Customer.business_id == business_id,
            func.lower(Customer.display_name) == display_name.lower(),
        )
    )


def find_or_create_customer(business_id, display_name, customer_type="individual", actor_id=None, branch_id=None):
    """Return the customer with this exact name, creating it when absent.

    Name matching ignores case and surrounding whitespace. Idempotent, so
    repeating a name never surfaces the uq_customers_business_name conflict
    that a plain create would.
    """
    name = (display_name or "").strip()
    if not name:
        raise ValidationError("Customer name is required")

    existing = _find_by_display_name(business_id, name)
    if existing is not None:
        if not existing.is_active:
            # Reusing a removed customer would drag its balance history into a
            # new quotation, so make the clash explicit instead.
            raise ConflictError(
                f'A removed customer named "{name}" already exists. Restore it or use another name.'
            )
        return existing.to_dict()

    from ..schemas import CustomerSchema

    try:
        return create_customer(
            business_id,
            CustomerSchema(display_name=name, customer_type=customer_type),
            actor_id,
            branch_id=branch_id,
        )
    except (ConflictError, IntegrityError, PendingRollbackError):
        # A concurrent request inserted the same name first. commit() turns a
        # unique violation into ConflictError, but a violation raised by an
        # earlier flush (audit logging) leaves the session pending-rollback
        # instead, so clear it and re-read before giving up.
        db.session.rollback()
        raced = _find_by_display_name(business_id, name)
        if raced is not None and raced.is_active:
            return raced.to_dict()
        if raced is not None:
            raise ConflictError(
                f'A removed customer named "{name}" already exists. Restore it or use another name.'
            )
        raise


def _default_page():
    from ..schemas import PaginationSchema
    return PaginationSchema()
