"""Sales service: point-of-sale invoicing, payment capture and cancellation."""
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, or_, select

from ..exceptions import BusinessError, NotFoundError, ValidationError
from ..extensions import db
from ..models import Customer, Item, Payment, SaleInvoice, SaleItem, TaxConfig
from ..utils.db import commit
from ..utils.pagination import paginate
from ..utils.security import is_valid_uuid, to_uuid
from .audit_log_service import log_event
from .inventory_service import get_default_warehouse, reduce_stock, return_stock


def _parse_dt(value):
    if value is None or isinstance(value, datetime):
        return value
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        raise ValidationError(f"Invalid date: {value}")
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _next_invoice_number(business_id):
    count = db.session.scalar(
        select(func.count()).where(SaleInvoice.business_id == business_id)
    ) or 0
    return f"SINV-{count + 1:06d}"


def _next_payment_number(business_id):
    count = db.session.scalar(
        select(func.count()).where(Payment.business_id == business_id)
    ) or 0
    return f"PAY-{count + 1:06d}"


def _get_sale(business_id, sale_id):
    sale = db.session.get(SaleInvoice, sale_id)
    if sale is None or sale.business_id != business_id:
        raise NotFoundError("Sale not found")
    return sale


def list_sales(business_id, pagination, status=None, sale_type=None, customer_id=None,
               branch_id=None, from_date=None, to_date=None, q=None):
    stmt = select(SaleInvoice).where(SaleInvoice.business_id == business_id)

    if status:
        stmt = stmt.where(SaleInvoice.status == status)
    if sale_type:
        stmt = stmt.where(SaleInvoice.sale_type == sale_type)
    if customer_id:
        stmt = stmt.where(SaleInvoice.customer_id == to_uuid(customer_id))
    if branch_id:
        stmt = stmt.where(SaleInvoice.branch_id == to_uuid(branch_id))
    if from_date:
        stmt = stmt.where(SaleInvoice.date >= _parse_dt(from_date))
    if to_date:
        stmt = stmt.where(SaleInvoice.date <= _parse_dt(to_date))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                SaleInvoice.invoice_number.ilike(like),
                SaleInvoice.notes.ilike(like),
            )
        )

    stmt = stmt.order_by(SaleInvoice.date.desc(), SaleInvoice.created_at.desc())
    sales, total, pages = paginate(stmt, pagination.page, pagination.per_page)
    return [s.to_dict() for s in sales], total, pages


def search_sales(business_id, q, limit=20):
    stmt = (
        select(SaleInvoice)
        .where(SaleInvoice.business_id == business_id)
        .order_by(SaleInvoice.date.desc())
        .limit(limit)
    )
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                SaleInvoice.invoice_number.ilike(like),
                SaleInvoice.notes.ilike(like),
            )
        )
    return [s.to_dict() for s in db.session.scalars(stmt).all()]


def get_sale(business_id, sale_id):
    return _get_sale(business_id, sale_id).to_dict()


def get_sale_record(business_id, sale_id):
    """The SaleInvoice model itself, for document generation (receipts/PDF).

    `get_sale` serialises for JSON; document builders need the live row so its
    items, payments, customer and branch relationships are loaded.
    """
    return _get_sale(business_id, sale_id)


def item_tax_rate(item, business_id, applies_to="sales"):
    """Resolve the tax rate for an item.

    The item's ``tax_config`` JSON wins (``rate``/``tax_rate`` inline, or a
    ``code`` referencing a TaxConfig); otherwise the business default for the
    given document type applies.
    """
    config = item.tax_config or {}
    for key in ("rate", "tax_rate", "percentage"):
        value = config.get(key)
        if value not in (None, ""):
            return Decimal(str(value))
    code = config.get("code") or config.get("tax_code")
    if code:
        tax = db.session.scalar(
            select(TaxConfig).where(
                TaxConfig.business_id == business_id,
                TaxConfig.code == code,
                TaxConfig.is_active.is_(True),
            )
        )
        if tax is not None:
            return Decimal(str(tax.rate or 0))
    default = db.session.scalar(
        select(TaxConfig).where(
            TaxConfig.business_id == business_id,
            TaxConfig.is_default.is_(True),
            TaxConfig.is_active.is_(True),
            TaxConfig.applies_to.in_([applies_to, "both"]),
        )
    )
    return Decimal(str(default.rate)) if default is not None else Decimal("0")


def price_lines(business_id, items_data, applies_to="sales"):
    """Resolve catalog pricing for requested lines.

    Returns a list of plain dicts so each document type (sale, quotation,
    purchase order) can map the result onto its own line columns.
    """
    if not items_data:
        raise ValidationError("At least one line item is required")

    lines = []
    for line in items_data:
        # to_uuid raises a bare ValueError on a malformed id, which would
        # surface as a 500. Blank or malformed ids are client input errors.
        if not line.item_id or not is_valid_uuid(line.item_id):
            raise ValidationError("Each line item must reference a valid item")
        item = db.session.get(Item, to_uuid(line.item_id))
        if item is None or item.business_id != business_id:
            raise NotFoundError(f"Item {line.item_id} not found")

        quantity = Decimal(str(line.quantity))
        if quantity <= 0:
            raise ValidationError("Quantity must be greater than zero")
        discount = Decimal(str(line.discount_amount or 0))
        unit_price = Decimal(str(item.selling_price or 0))
        cost_price = Decimal(str(item.cost_price or 0))
        tax_rate = item_tax_rate(item, business_id, applies_to)

        gross = unit_price * quantity
        if discount > gross:
            raise BusinessError(f"Discount for '{item.name}' exceeds the line total")
        net = gross - discount
        tax = (net * (tax_rate / Decimal("100"))).quantize(Decimal("0.01"))

        is_service = item.type == "service"
        lines.append({
            "item": item,
            "quantity": quantity,
            "discount_amount": discount,
            "unit_price": unit_price,
            "cost_price": cost_price,
            "tax_rate": tax_rate,
            "tax_amount": tax,
            "line_net": net,
            "line_total": net + tax,
            "is_service": is_service,
            "is_tracked": bool(item.is_inventory_tracked) and not is_service,
        })
    return lines


def line_totals(lines):
    """Aggregate subtotal, discount and tax across priced lines."""
    subtotal = sum((line["unit_price"] * line["quantity"] for line in lines), Decimal("0"))
    discount = sum((line["discount_amount"] for line in lines), Decimal("0"))
    tax = sum((line["tax_amount"] for line in lines), Decimal("0"))
    return (
        subtotal.quantize(Decimal("0.01")),
        discount.quantize(Decimal("0.01")),
        tax.quantize(Decimal("0.01")),
    )


def _add_sale_lines(business_id, sale, items_data, actor_id):
    lines = price_lines(business_id, items_data)
    for priced in lines:
        row = SaleItem(
            business_id=business_id,
            sale_invoice_id=sale.id,
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
    return lines


def create_sale(business_id, data, actor_id, branch_id=None):
    from .subscription_service import check_plan_limit

    # A sale issues its own invoice number, so it draws on the same invoice
    # allowance as a finance invoice rather than getting a separate budget.
    check_plan_limit(business_id, "invoices")

    if data.customer_id:
        customer = db.session.get(Customer, to_uuid(data.customer_id))
        if customer is None or customer.business_id != business_id:
            raise NotFoundError("Customer not found")

    warehouse = get_default_warehouse(business_id)

    sale = SaleInvoice(
        business_id=business_id,
        branch_id=to_uuid(branch_id) if branch_id else None,
        customer_id=to_uuid(data.customer_id) if data.customer_id else None,
        invoice_number=_next_invoice_number(business_id),
        date=datetime.now(timezone.utc),
        sale_type=data.sale_type or "cash",
        status="draft",
        notes=data.notes,
    )
    sale.created_by = actor_id
    sale.updated_by = actor_id
    db.session.add(sale)
    db.session.flush()

    lines = _add_sale_lines(business_id, sale, data.items, actor_id)
    subtotal, discount_total, tax_total = line_totals(lines)
    total = (subtotal - discount_total + tax_total).quantize(Decimal("0.01"))

    sale.subtotal = subtotal
    sale.discount_amount = discount_total
    sale.tax_amount = tax_total
    sale.rounding_amount = Decimal("0.00")
    sale.total_amount = total
    sale.paid_amount = Decimal("0")
    sale.balance_due = total

    for priced in lines:
        if not priced["is_tracked"]:
            continue
        reduce_stock(
            business_id, priced["item"].id, warehouse.id, priced["quantity"],
            user_id=actor_id, reference_type="sale", reference_id=sale.id,
            note=f"Sale {sale.invoice_number}",
        )

    for payment in data.payments or []:
        _record_payment(business_id, sale, payment, actor_id)

    sale.status = "posted"
    log_event(business_id, "create", "sale", actor_id, sale.id, {
        "invoice_number": sale.invoice_number,
        "total_amount": float(sale.total_amount or 0),
    })
    commit()
    return sale.to_dict()


def _record_payment(business_id, sale, data, actor_id):
    amount = Decimal(str(data.amount))
    if amount <= 0:
        raise ValidationError("Payment amount must be greater than zero")

    payment = Payment(
        business_id=business_id,
        branch_id=sale.branch_id,
        customer_id=sale.customer_id,
        sale_invoice_id=sale.id,
        payment_number=_next_payment_number(business_id),
        payment_type="customer_receipt",
        amount=amount,
        payment_method=data.payment_method,
        date=_parse_dt(getattr(data, "date", None)) or datetime.now(timezone.utc),
        reference=getattr(data, "reference", None),
        notes=getattr(data, "notes", None),
        status="completed",
    )
    payment.created_by = actor_id
    payment.updated_by = actor_id
    db.session.add(payment)
    db.session.flush()

    sale.paid_amount = Decimal(str(sale.paid_amount or 0)) + amount
    sale.balance_due = Decimal(str(sale.total_amount or 0)) - sale.paid_amount
    if sale.balance_due < 0:
        sale.balance_due = Decimal("0")
    return payment


def update_sale(business_id, sale_id, data, actor_id):
    sale = _get_sale(business_id, sale_id)
    if sale.status == "cancelled":
        raise BusinessError("Cannot modify a cancelled sale")

    if data.customer_id is not None:
        customer = db.session.get(Customer, to_uuid(data.customer_id))
        if customer is None or customer.business_id != business_id:
            raise NotFoundError("Customer not found")
        sale.customer_id = customer.id
    if data.sale_type is not None:
        sale.sale_type = data.sale_type
    if data.notes is not None:
        sale.notes = data.notes
    if data.status is not None and data.status != sale.status:
        if data.status == "cancelled":
            raise BusinessError("Use the cancel action to cancel a sale")
        sale.status = data.status

    sale.updated_by = actor_id
    commit()
    return sale.to_dict()


def cancel_sale(business_id, sale_id, actor_id, reason=None):
    sale = _get_sale(business_id, sale_id)
    if sale.status == "cancelled":
        raise BusinessError("Sale is already cancelled")

    warehouse = get_default_warehouse(business_id)
    for line in sale.items:
        if line.item_id and not line.is_service:
            item = db.session.get(Item, line.item_id)
            if item is not None and item.is_inventory_tracked:
                return_stock(
                    business_id, item.id, warehouse.id, line.quantity,
                    user_id=actor_id, reference_type="sale", reference_id=sale.id,
                    note=f"Cancel sale {sale.invoice_number}",
                )

    sale.status = "cancelled"
    sale.paid_amount = Decimal("0")
    sale.balance_due = Decimal("0")
    sale.updated_by = actor_id
    log_event(business_id, "cancel", "sale", actor_id, sale.id, {
        "invoice_number": sale.invoice_number, "reason": reason,
    })
    commit()
    return sale.to_dict()


def add_sale_payment(business_id, sale_id, data, actor_id):
    sale = _get_sale(business_id, sale_id)
    if sale.status == "cancelled":
        raise BusinessError("Cannot record a payment against a cancelled sale")
    if sale.sale_type == "cash" and sale.balance_due <= 0:
        raise BusinessError("This sale is already fully paid")

    _record_payment(business_id, sale, data, actor_id)
    sale.updated_by = actor_id
    commit()
    return sale.to_dict()


def delete_sale(business_id, sale_id, actor_id):
    sale = _get_sale(business_id, sale_id)
    if sale.status == "posted":
        raise BusinessError("Only draft or cancelled sales can be deleted")
    if sale.payments:
        raise BusinessError("Delete or reverse the recorded payments first")
    db.session.delete(sale)
    log_event(business_id, "delete", "sale", actor_id, sale_id, {
        "invoice_number": sale.invoice_number,
    })
    commit()
