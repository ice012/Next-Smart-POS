"""Invoice service for managing invoices, items, and status transitions."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select

from ..exceptions import NotFoundError, ConflictError, ValidationError, BusinessError
from ..extensions import db
from ..models import (
    Customer, Item, Invoice, InvoiceItem, BusinessConfig, Branch, Payment, TaxConfig
)
from ..utils.db import commit
from ..utils.pagination import paginate
from ..utils.security import to_uuid
from .financial_calculation_service import get_calculation_service
from .tax_service import get_tax_service


def _parse_dt(value):
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        raise ValidationError(f"Invalid date: {value}")


class InvoiceService:
    """Invoice management with calculation, status workflow, and numbering."""

    def __init__(self, business_id):
        self.business_id = business_id
        self.calc = get_calculation_service(business_id)
        self.tax_service = get_tax_service(business_id)

    def _get_next_invoice_number(self) -> str:
        config = db.session.scalar(
            select(BusinessConfig).where(BusinessConfig.business_id == self.business_id)
        )
        if config is None:
            raise BusinessError("Business configuration not found")
        number = f"{config.invoice_prefix or 'INV-'}{config.invoice_next_number:06d}"
        config.invoice_next_number += 1
        config.updated_by = self.business_id
        return number

    def _validate_invoice_data(self, data):
        """Validate invoice data including customer, items, and references."""
        if data.customer_id:
            customer = db.session.get(Customer, data.customer_id)
            if customer is None or customer.business_id != self.business_id:
                raise NotFoundError("Customer not found")

        if data.branch_id:
            branch = db.session.get(Branch, data.branch_id)
            if branch is None or branch.business_id != self.business_id:
                raise NotFoundError("Branch not found")

        if not data.items or len(data.items) == 0:
            raise ValidationError("At least one line item is required")

        for item_data in data.items:
            if item_data.item_id:
                item = db.session.get(Item, item_data.item_id)
                if item is None or item.business_id != self.business_id:
                    raise NotFoundError(f"Item {item_data.item_id} not found")

    def _calculate_item_totals(self, item_data) -> dict:
        """Calculate line item totals."""
        qty = Decimal(str(item_data.quantity))
        unit_price = Decimal(str(item_data.unit_price))
        discount = Decimal(str(item_data.discount_amount or 0))
        tax_rate = Decimal(str(item_data.tax_rate or 0))

        line_subtotal = (qty * unit_price) - discount
        tax_amount = line_subtotal * (tax_rate / Decimal("100"))
        line_total = line_subtotal + tax_amount

        return {
            "line_subtotal": line_subtotal,
            "tax_amount": tax_amount,
            "line_total": line_total,
        }

    def _process_items(self, invoice: Invoice, items_data, actor_id) -> dict:
        """Process invoice items and calculate totals."""
        # Clear existing items if updating
        for item in invoice.items:
            db.session.delete(item)
        db.session.flush()

        subtotal = Decimal("0")
        tax_amount = Decimal("0")
        discount_amount = Decimal("0")

        for idx, item_data in enumerate(items_data):
            calc = self._calculate_item_totals(item_data)

            item = InvoiceItem(
                business_id=self.business_id,
                invoice_id=invoice.id,
                item_id=to_uuid(item_data.item_id) if item_data.item_id else None,
                description=item_data.description,
                quantity=Decimal(str(item_data.quantity)),
                unit_price=Decimal(str(item_data.unit_price)),
                cost_price=Decimal(str(item_data.cost_price or 0)),
                discount_amount=Decimal(str(item_data.discount_amount or 0)),
                tax_rate=Decimal(str(item_data.tax_rate or 0)),
                tax_amount=calc["tax_amount"],
                line_total=calc["line_total"],
                is_service=item_data.is_service or False,
            )
            item.created_by = actor_id
            item.updated_by = actor_id
            db.session.add(item)

            subtotal += calc["line_subtotal"]
            tax_amount += calc["tax_amount"]
            discount_amount += item.discount_amount

        return {
            "subtotal": subtotal,
            "discount_amount": discount_amount,
            "tax_amount": tax_amount,
        }

    def create_invoice(self, data, actor_id):
        """Create a new invoice in draft status."""
        from .subscription_service import check_plan_limit

        check_plan_limit(self.business_id, "invoices")
        self._validate_invoice_data(data)

        # Generate invoice number
        invoice_number = self._get_next_invoice_number()

        invoice = Invoice(
            business_id=self.business_id,
            branch_id=to_uuid(data.branch_id) if data.branch_id else None,
            customer_id=to_uuid(data.customer_id) if data.customer_id else None,
            invoice_number=invoice_number,
            date=_parse_dt(data.date) or datetime.now(timezone.utc),
            due_date=_parse_dt(data.due_date),
            status="draft",
            notes=data.notes,
            terms=data.terms,
        )
        invoice.created_by = actor_id
        invoice.updated_by = actor_id
        db.session.add(invoice)
        db.session.flush()

        # Process items and calculate totals
        totals = self._process_items(invoice, data.items, actor_id)

        # Apply header discount
        header_discount = Decimal(str(getattr(data, 'discount_amount', 0) or 0))

        # Calculate final totals
        final_totals = self.calc.calculate_invoice_totals(
            [{"quantity": i.quantity, "unit_price": i.unit_price,
              "discount_amount": i.discount_amount, "tax_rate": i.tax_rate}
             for i in data.items],
            header_discount
        )

        invoice.subtotal = final_totals["subtotal"]
        invoice.discount_amount = final_totals["discount_amount"]
        invoice.tax_amount = final_totals["tax_amount"]
        invoice.total_amount = final_totals["total_amount"]
        invoice.rounding_amount = final_totals["rounding_amount"]
        invoice.paid_amount = Decimal("0")
        invoice.balance_due = final_totals["balance_due"]

        from .audit_log_service import log_event
        log_event(self.business_id, "create", "invoice", actor_id, invoice.id, {
            "invoice_number": invoice.invoice_number, "total_amount": float(invoice.total_amount or 0),
        })
        commit()
        return invoice

    def get_invoice(self, invoice_id: str):
        invoice = db.session.get(Invoice, invoice_id)
        if invoice is None or invoice.business_id != self.business_id:
            raise NotFoundError("Invoice not found")
        return invoice

    def update_invoice(self, invoice_id: str, data, actor_id):
        invoice = self.get_invoice(invoice_id)

        if invoice.status not in ("draft",):
            raise BusinessError(f"Cannot modify invoice in {invoice.status} status")

        self._validate_invoice_data(data)

        if data.branch_id is not None:
            invoice.branch_id = to_uuid(data.branch_id) if data.branch_id else None
        if data.customer_id is not None:
            invoice.customer_id = to_uuid(data.customer_id) if data.customer_id else None
        if data.date is not None:
            invoice.date = _parse_dt(data.date)
        if data.due_date is not None:
            invoice.due_date = _parse_dt(data.due_date)
        if data.notes is not None:
            invoice.notes = data.notes
        if data.terms is not None:
            invoice.terms = data.terms
        if data.status is not None:
            self._transition_status(invoice, data.status, actor_id)

        if data.items is not None:
            totals = self._process_items(invoice, data.items, actor_id)

            header_discount = Decimal(str(getattr(data, 'discount_amount', 0) or 0))
            final_totals = self.calc.calculate_invoice_totals(
                [{"quantity": i.quantity, "unit_price": i.unit_price,
                  "discount_amount": i.discount_amount, "tax_rate": i.tax_rate}
                 for i in data.items],
                header_discount
            )

            invoice.subtotal = final_totals["subtotal"]
            invoice.discount_amount = final_totals["discount_amount"]
            invoice.tax_amount = final_totals["tax_amount"]
            invoice.total_amount = final_totals["total_amount"]
            invoice.rounding_amount = final_totals["rounding_amount"]
            invoice.balance_due = final_totals["balance_due"]

        invoice.updated_by = actor_id
        commit()
        return invoice

    def _transition_status(self, invoice: Invoice, new_status: str, actor_id):
        """Validate and perform status transition."""
        valid_transitions = {
            "draft": ["sent", "cancelled"],
            "sent": ["partially_paid", "paid", "overdue", "cancelled"],
            "partially_paid": ["paid", "overdue", "cancelled"],
            "paid": ["cancelled"],  # Only via credit note/refund
            "overdue": ["paid", "partially_paid", "cancelled"],
            "cancelled": [],  # Terminal state
        }

        if new_status not in valid_transitions.get(invoice.status, []):
            raise BusinessError(f"Invalid status transition: {invoice.status} -> {new_status}")

        invoice.status = new_status
        invoice.updated_by = actor_id

    def send_invoice(self, invoice_id: str, actor_id):
        """Mark invoice as sent."""
        invoice = self.get_invoice(invoice_id)
        self._transition_status(invoice, "sent", actor_id)
        from .audit_log_service import log_event
        log_event(self.business_id, "send", "invoice", actor_id, invoice.id, {
            "invoice_number": invoice.invoice_number, "status": invoice.status,
        })
        commit()
        return invoice

    def cancel_invoice(self, invoice_id: str, actor_id, reason: str = None):
        """Cancel an invoice."""
        invoice = self.get_invoice(invoice_id)
        if invoice.status in ("paid",):
            raise BusinessError("Cannot cancel a paid invoice. Create a credit note instead.")
        self._transition_status(invoice, "cancelled", actor_id)
        if reason:
            invoice.notes = (invoice.notes or "") + f"\nCancelled: {reason}"
        from .audit_log_service import log_event
        log_event(self.business_id, "cancel", "invoice", actor_id, invoice.id, {
            "invoice_number": invoice.invoice_number, "reason": reason,
        })
        commit()
        return invoice

    def check_overdue_invoices(self):
        """Mark overdue invoices."""
        now = datetime.now(timezone.utc)
        invoices = db.session.scalars(
            select(Invoice).where(
                Invoice.business_id == self.business_id,
                Invoice.status.in_(["sent", "partially_paid"]),
                Invoice.due_date < now,
            )
        ).all()

        from .audit_log_service import log_event
        count = 0
        for invoice in invoices:
            invoice.status = "overdue"
            invoice.updated_by = "system"
            log_event(self.business_id, "status_change", "invoice", None, invoice.id, {
                "invoice_number": invoice.invoice_number, "old_status": "sent", "new_status": "overdue",
            })
            count += 1
        commit()
        return count

    def list_invoices(self, pagination, customer_id: str = None, status: str = None,
                      from_date: str = None, to_date: str = None):
        stmt = select(Invoice).where(Invoice.business_id == self.business_id)

        if customer_id:
            stmt = stmt.where(Invoice.customer_id == customer_id)
        if status:
            stmt = stmt.where(Invoice.status == status)
        if from_date:
            stmt = stmt.where(Invoice.date >= from_date)
        if to_date:
            stmt = stmt.where(Invoice.date <= to_date)

        stmt = stmt.order_by(Invoice.date.desc(), Invoice.created_at.desc())
        items, total, pages = paginate(stmt, pagination.page, pagination.per_page)
        return items, total, pages

    def delete_invoice(self, invoice_id: str):
        """Only allow deletion of draft invoices."""
        invoice = self.get_invoice(invoice_id)
        if invoice.status != "draft":
            raise BusinessError("Can only delete draft invoices")
        db.session.delete(invoice)
        commit()

    def get_customer_statement_documents(self, customer_id: str, from_date: str = None, to_date: str = None):
        """Return the (customer, invoices, payments) ORM objects behind a statement."""
        customer = db.session.get(Customer, customer_id)
        if customer is None or customer.business_id != self.business_id:
            raise NotFoundError("Customer not found")

        inv_stmt = select(Invoice).where(
            Invoice.business_id == self.business_id,
            Invoice.customer_id == customer_id,
        )
        pay_stmt = select(Payment).where(
            Payment.business_id == self.business_id,
            Payment.customer_id == customer_id,
            Payment.status == "completed",
        )
        if from_date:
            inv_stmt = inv_stmt.where(Invoice.date >= from_date)
            pay_stmt = pay_stmt.where(Payment.date >= from_date)
        if to_date:
            inv_stmt = inv_stmt.where(Invoice.date <= to_date)
            pay_stmt = pay_stmt.where(Payment.date <= to_date)

        invoices = db.session.scalars(inv_stmt.order_by(Invoice.date.asc())).all()
        payments = db.session.scalars(pay_stmt.order_by(Payment.date.asc())).all()
        return customer, invoices, payments

    def get_customer_statement(self, customer_id: str, from_date: str = None, to_date: str = None):
        """Get customer statement with invoices and payments."""
        customer = db.session.get(Customer, customer_id)
        if customer is None or customer.business_id != self.business_id:
            raise NotFoundError("Customer not found")

        stmt = select(Invoice).where(
            Invoice.business_id == self.business_id,
            Invoice.customer_id == customer_id,
        )
        if from_date:
            stmt = stmt.where(Invoice.date >= from_date)
        if to_date:
            stmt = stmt.where(Invoice.date <= to_date)
        stmt = stmt.order_by(Invoice.date.asc())

        invoices = db.session.scalars(stmt).all()

        return {
            "customer": customer.to_dict(),
            "statement_balance": float(customer.statement_balance or 0),
            "invoices": [inv.to_dict() for inv in invoices],
        }


def get_invoice_service(business_id):
    return InvoiceService(business_id)