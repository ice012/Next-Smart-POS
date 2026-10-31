"""Quotation service: priced quotes that convert into posted sales."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, or_, select

from ..exceptions import BusinessError, NotFoundError, ValidationError
from ..extensions import db
from ..models import Customer, Quotation, QuotationItem
from ..utils.db import commit
from ..utils.pagination import paginate
from ..utils.security import to_uuid
from .audit_log_service import log_event
from .sale_service import line_totals, price_lines

DEFAULT_VALIDITY_DAYS = 30

VALID_TRANSITIONS = {
    "draft": {"sent", "expired", "cancelled", "rejected"},
    "sent": {"accepted", "rejected", "expired"},
    "accepted": {"converted"},
    "rejected": set(),
    "expired": set(),
    "converted": set(),
}


def _parse_dt(value):
    if value is None or isinstance(value, datetime):
        return value
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        raise ValidationError(f"Invalid date: {value}")
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _next_quotation_number(business_id):
    count = db.session.scalar(
        select(func.count()).where(Quotation.business_id == business_id)
    ) or 0
    return f"QUO-{count + 1:06d}"


def _get_quotation(business_id, quotation_id):
    quotation = db.session.get(Quotation, quotation_id)
    if quotation is None or quotation.business_id != business_id:
        raise NotFoundError("Quotation not found")
    return quotation


def list_quotations(business_id, pagination, status=None, customer_id=None,
                    from_date=None, to_date=None, q=None):
    stmt = select(Quotation).where(Quotation.business_id == business_id)

    if status:
        stmt = stmt.where(Quotation.status == status)
    if customer_id:
        stmt = stmt.where(Quotation.customer_id == to_uuid(customer_id))
    if from_date:
        stmt = stmt.where(Quotation.date >= _parse_dt(from_date))
    if to_date:
        stmt = stmt.where(Quotation.date <= _parse_dt(to_date))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                Quotation.quotation_number.ilike(like),
                Quotation.notes.ilike(like),
            )
        )

    stmt = stmt.order_by(Quotation.date.desc(), Quotation.created_at.desc())
    rows, total, pages = paginate(stmt, pagination.page, pagination.per_page)
    return [row.to_dict() for row in rows], total, pages


def get_quotation(business_id, quotation_id):
    return _get_quotation(business_id, quotation_id).to_dict()


def get_quotation_record(business_id, quotation_id):
    """The Quotation model itself, for document generation (PDF).

    `get_quotation` serialises for JSON; document builders need the live row
    so its items, customer and branch relationships are loaded.
    """
    return _get_quotation(business_id, quotation_id)


def _replace_items(business_id, quotation, items_data, actor_id):
    for existing in list(quotation.items):
        db.session.delete(existing)
    db.session.flush()

    lines = price_lines(business_id, items_data)
    for priced in lines:
        row = QuotationItem(
            business_id=business_id,
            quotation_id=quotation.id,
            item_id=priced["item"].id,
            description=priced["item"].name,
            quantity=priced["quantity"],
            unit_price=priced["unit_price"],
            cost_price=priced["cost_price"],
            discount_amount=priced["discount_amount"],
            tax_rate=priced["tax_rate"],
            tax_amount=priced["tax_amount"],
            line_total=priced["line_total"],
            is_service=priced["is_service"],
        )
        row.created_by = actor_id
        row.updated_by = actor_id
        db.session.add(row)

    subtotal, discount, tax = line_totals(lines)
    quotation.subtotal = subtotal
    quotation.discount_amount = discount
    quotation.tax_amount = tax
    quotation.total_amount = (subtotal - discount + tax).quantize(Decimal("0.01"))


def create_quotation(business_id, data, actor_id, branch_id=None):
    if data.customer_id:
        customer = db.session.get(Customer, to_uuid(data.customer_id))
        if customer is None or customer.business_id != business_id:
            raise NotFoundError("Customer not found")

    now = datetime.now(timezone.utc)
    quotation = Quotation(
        business_id=business_id,
        branch_id=to_uuid(branch_id) if branch_id else None,
        customer_id=to_uuid(data.customer_id) if data.customer_id else None,
        quotation_number=_next_quotation_number(business_id),
        date=_parse_dt(data.date) or now,
        expiry_date=_parse_dt(data.expiry_date) or (now + timedelta(days=DEFAULT_VALIDITY_DAYS)),
        status="draft",
        notes=data.notes,
    )
    quotation.created_by = actor_id
    quotation.updated_by = actor_id
    db.session.add(quotation)
    db.session.flush()

    _replace_items(business_id, quotation, data.items, actor_id)
    log_event(business_id, "create", "quotation", actor_id, quotation.id, {
        "quotation_number": quotation.quotation_number,
        "total_amount": float(quotation.total_amount or 0),
    })
    commit()
    return quotation.to_dict()


def update_quotation(business_id, quotation_id, data, actor_id):
    quotation = _get_quotation(business_id, quotation_id)
    if quotation.status in ("converted", "rejected", "expired"):
        raise BusinessError(f"Cannot modify a {quotation.status} quotation")

    if data.customer_id is not None:
        customer = db.session.get(Customer, to_uuid(data.customer_id))
        if customer is None or customer.business_id != business_id:
            raise NotFoundError("Customer not found")
        quotation.customer_id = customer.id
    if data.date is not None:
        quotation.date = _parse_dt(data.date)
    if data.expiry_date is not None:
        quotation.expiry_date = _parse_dt(data.expiry_date)
    if data.notes is not None:
        quotation.notes = data.notes
    if data.status is not None:
        set_quotation_status(business_id, quotation_id, data.status, actor_id)
    if data.items is not None:
        _replace_items(business_id, quotation, data.items, actor_id)

    quotation.updated_by = actor_id
    commit()
    return quotation.to_dict()


def set_quotation_status(business_id, quotation_id, status, actor_id):
    quotation = _get_quotation(business_id, quotation_id)
    if status == quotation.status:
        return quotation.to_dict()
    if status not in VALID_TRANSITIONS.get(quotation.status, set()):
        raise BusinessError(
            f"Invalid status transition: {quotation.status} -> {status}"
        )
    quotation.status = status
    quotation.updated_by = actor_id
    log_event(business_id, "status_change", "quotation", actor_id, quotation.id, {
        "quotation_number": quotation.quotation_number, "status": status,
    })
    commit()
    return quotation.to_dict()


def convert_quotation_to_sale(business_id, quotation_id, data, actor_id):
    quotation = _get_quotation(business_id, quotation_id)
    if quotation.status == "converted":
        raise BusinessError("Quotation has already been converted")
    if quotation.status not in ("accepted", "sent"):
        raise BusinessError("Only an accepted quotation can be converted")

    from ..schemas import SaleCreateSchema, SaleItemInputSchema
    from .sale_service import create_sale

    payload = SaleCreateSchema(
        customer_id=str(quotation.customer_id) if quotation.customer_id else None,
        sale_type=getattr(data, "sale_type", "credit") or "credit",
        notes=getattr(data, "notes", None) or f"Converted from {quotation.quotation_number}",
        items=[
            SaleItemInputSchema(
                item_id=str(line.item_id),
                quantity=float(line.quantity or 0),
                discount_amount=float(line.discount_amount or 0),
            )
            for line in quotation.items
            if line.item_id
        ],
    )
    if not payload.items:
        raise BusinessError("Quotation has no convertible line items")

    sale = create_sale(
        business_id, payload, actor_id,
        branch_id=str(quotation.branch_id) if quotation.branch_id else None,
    )
    quotation.status = "converted"
    quotation.updated_by = actor_id
    commit()
    return sale


def delete_quotation(business_id, quotation_id, actor_id):
    quotation = _get_quotation(business_id, quotation_id)
    if quotation.status == "converted":
        raise BusinessError("A converted quotation cannot be deleted")
    number = quotation.quotation_number
    db.session.delete(quotation)
    log_event(business_id, "delete", "quotation", actor_id, quotation_id, {
        "quotation_number": number,
    })
    commit()
