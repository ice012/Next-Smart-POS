"""Centralized payment engine with overpayment prevention and balance updates."""
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Optional

from sqlalchemy import func, select

from ..exceptions import NotFoundError, ConflictError, ValidationError, BusinessError
from ..extensions import db
from ..models import Customer, Supplier, Invoice, Expense, Payment, PaymentAllocation, FinancialAccount
from ..utils.db import commit
from ..utils.security import to_uuid
from .financial_calculation_service import get_calculation_service


def _parse_dt(value):
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        raise ValidationError(f"Invalid date: {value}")


class PaymentService:
    """Centralized payment processing for all payment types."""

    def __init__(self, business_id):
        self.business_id = business_id
        self.calc = get_calculation_service(business_id)

    def _get_next_payment_number(self) -> str:
        count = db.session.scalar(
            select(func.count()).where(Payment.business_id == self.business_id)
        ) or 0
        return self.calc.get_next_payment_number(count)

    def _validate_payment_references(self, data):
        """Validate that referenced entities exist and belong to business."""
        if data.customer_id:
            customer = db.session.get(Customer, data.customer_id)
            if customer is None or customer.business_id != self.business_id:
                raise NotFoundError("Customer not found")

        if data.supplier_id:
            supplier = db.session.get(Supplier, data.supplier_id)
            if supplier is None or supplier.business_id != self.business_id:
                raise NotFoundError("Supplier not found")

        if data.invoice_id:
            invoice = db.session.get(Invoice, data.invoice_id)
            if invoice is None or invoice.business_id != self.business_id:
                raise NotFoundError("Invoice not found")
            # Validate customer matches
            if data.customer_id and invoice.customer_id != to_uuid(data.customer_id):
                raise ValidationError("Invoice does not belong to the specified customer")

        if data.expense_id:
            expense = db.session.get(Expense, data.expense_id)
            if expense is None or expense.business_id != self.business_id:
                raise NotFoundError("Expense not found")
            # Validate supplier matches
            if data.supplier_id and expense.supplier_id and expense.supplier_id != to_uuid(data.supplier_id):
                raise ValidationError("Expense does not belong to the specified supplier")

        if data.financial_account_id:
            account = db.session.get(FinancialAccount, data.financial_account_id)
            if account is None or account.business_id != self.business_id:
                raise NotFoundError("Financial account not found")

        if data.counterparty_id:
            account = db.session.get(FinancialAccount, data.counterparty_id)
            if account is None or account.business_id != self.business_id:
                raise NotFoundError("Counterparty account not found")

        if data.related_transaction_id:
            related = db.session.get(Payment, data.related_transaction_id)
            if related is None or related.business_id != self.business_id:
                raise NotFoundError("Related transaction not found")

    def create_payment(self, data, actor_id):
        """Create a payment with validation and automatic allocation."""
        self._validate_payment_references(data)

        payment = Payment(
            business_id=self.business_id,
            branch_id=to_uuid(data.branch_id) if data.branch_id else None,
            customer_id=to_uuid(data.customer_id) if data.customer_id else None,
            supplier_id=to_uuid(data.supplier_id) if data.supplier_id else None,
            invoice_id=to_uuid(data.invoice_id) if data.invoice_id else None,
            expense_id=to_uuid(data.expense_id) if data.expense_id else None,
            financial_account_id=to_uuid(data.financial_account_id) if data.financial_account_id else None,
            counterparty_id=to_uuid(data.counterparty_id) if data.counterparty_id else None,
            related_transaction_id=to_uuid(data.related_transaction_id) if data.related_transaction_id else None,
            payment_number=self._get_next_payment_number(),
            payment_type=data.payment_type,
            amount=Decimal(str(data.amount)),
            payment_method=data.payment_method,
            date=_parse_dt(data.date) or datetime.now(timezone.utc),
            reference=data.reference,
            notes=data.notes,
            status="completed",
        )
        payment.created_by = actor_id
        payment.updated_by = actor_id
        db.session.add(payment)
        db.session.flush()

        # Handle automatic allocation for customer receipts
        if data.payment_type == "customer_receipt" and data.invoice_id:
            self._allocate_payment_to_invoice(payment, data.invoice_id, data.amount, actor_id)
        elif data.payment_type == "customer_receipt" and data.customer_id:
            # Allocate to oldest invoices first (FIFO)
            self._allocate_payment_to_customer(payment, data.customer_id, data.amount, actor_id)

        from .audit_log_service import log_event
        log_event(self.business_id, "create", "payment", actor_id, payment.id, {
            "payment_number": payment.payment_number,
            "payment_type": payment.payment_type,
            "amount": float(payment.amount or 0),
        })
        commit()
        return payment

    def _allocate_payment_to_invoice(self, payment: Payment, invoice_id: str, amount: float, actor_id):
        """Allocate payment to a specific invoice with overpayment prevention."""
        invoice = db.session.get(Invoice, invoice_id)
        if invoice is None:
            raise NotFoundError("Invoice not found")

        if Decimal(str(amount)) > invoice.balance_due:
            raise BusinessError(
                f"Payment exceeds invoice balance due of {invoice.balance_due}"
            )
        allocation_amount = min(Decimal(str(amount)), invoice.balance_due)
        if allocation_amount <= 0:
            raise BusinessError("Invoice is already fully paid or has no balance due")

        # Create allocation
        allocation = PaymentAllocation(
            business_id=self.business_id,
            payment_id=payment.id,
            invoice_id=invoice.id,
            amount=allocation_amount,
        )
        allocation.created_by = actor_id
        allocation.updated_by = actor_id
        db.session.add(allocation)

        # Update invoice
        invoice.paid_amount = (invoice.paid_amount or Decimal("0")) + allocation_amount
        invoice.balance_due = (invoice.total_amount or Decimal("0")) - invoice.paid_amount
        if invoice.balance_due < Decimal("0.005"):
            invoice.balance_due = Decimal("0")

        # Update status
        if invoice.balance_due == 0:
            invoice.status = "paid"
        elif invoice.paid_amount > 0:
            invoice.status = "partially_paid"

        invoice.updated_by = actor_id

        # Update customer statement balance
        if invoice.customer:
            invoice.customer.statement_balance = (invoice.customer.statement_balance or Decimal("0")) - allocation_amount
            invoice.customer.updated_by = actor_id

    def _allocate_payment_to_customer(self, payment: Payment, customer_id: str, amount: float, actor_id):
        """Allocate payment to customer's oldest outstanding invoices (FIFO)."""
        amount_remaining = Decimal(str(amount))
        customer = db.session.get(Customer, customer_id)

        # Get outstanding invoices ordered by date
        invoices = db.session.scalars(
            select(Invoice).where(
                Invoice.business_id == self.business_id,
                Invoice.customer_id == customer_id,
                Invoice.status.in_(["sent", "partially_paid", "overdue"]),
                Invoice.balance_due > 0,
            ).order_by(Invoice.date.asc())
        ).all()

        for invoice in invoices:
            if amount_remaining <= 0:
                break

            allocation_amount = min(amount_remaining, invoice.balance_due)
            if allocation_amount <= 0:
                continue

            allocation = PaymentAllocation(
                business_id=self.business_id,
                payment_id=payment.id,
                invoice_id=invoice.id,
                amount=allocation_amount,
            )
            allocation.created_by = actor_id
            allocation.updated_by = actor_id
            db.session.add(allocation)

            invoice.paid_amount = (invoice.paid_amount or Decimal("0")) + allocation_amount
            invoice.balance_due = (invoice.total_amount or Decimal("0")) - invoice.paid_amount
            if invoice.balance_due < Decimal("0.005"):
                invoice.balance_due = Decimal("0")

            if invoice.balance_due == 0:
                invoice.status = "paid"
            elif invoice.paid_amount > 0:
                invoice.status = "partially_paid"

            invoice.updated_by = actor_id
            amount_remaining -= allocation_amount

        # Update customer statement balance
        if customer:
            customer.statement_balance = (customer.statement_balance or Decimal("0")) - (Decimal(str(amount)) - amount_remaining)
            customer.updated_by = actor_id

        # If there's remaining amount, it becomes a credit on account
        if amount_remaining > 0:
            # Create an unallocated payment record or credit note
            pass  # Handled by leaving payment partially unallocated

    def update_payment(self, payment_id: str, data, actor_id):
        payment = db.session.get(Payment, payment_id)
        if payment is None or payment.business_id != self.business_id:
            raise NotFoundError("Payment not found")

        if payment.status == "completed" and data.amount is not None:
            raise BusinessError("Cannot modify amount of completed payment. Create a reversal instead.")

        if any(
            getattr(data, field, None) is not None
            for field in (
                "customer_id", "supplier_id", "invoice_id", "expense_id",
                "financial_account_id", "counterparty_id", "related_transaction_id",
            )
        ):
            merged = SimpleNamespace(
                customer_id=data.customer_id if data.customer_id is not None else (
                    str(payment.customer_id) if payment.customer_id else None
                ),
                supplier_id=data.supplier_id if data.supplier_id is not None else (
                    str(payment.supplier_id) if payment.supplier_id else None
                ),
                invoice_id=data.invoice_id if data.invoice_id is not None else (
                    str(payment.invoice_id) if payment.invoice_id else None
                ),
                expense_id=data.expense_id if data.expense_id is not None else (
                    str(payment.expense_id) if payment.expense_id else None
                ),
                financial_account_id=data.financial_account_id if data.financial_account_id is not None else (
                    str(payment.financial_account_id) if payment.financial_account_id else None
                ),
                counterparty_id=data.counterparty_id if data.counterparty_id is not None else (
                    str(payment.counterparty_id) if payment.counterparty_id else None
                ),
                related_transaction_id=data.related_transaction_id if data.related_transaction_id is not None else (
                    str(payment.related_transaction_id) if payment.related_transaction_id else None
                ),
            )
            self._validate_payment_references(merged)

        if data.amount is not None:
            payment.amount = Decimal(str(data.amount))
        if data.payment_method is not None:
            payment.payment_method = data.payment_method
        if data.date is not None:
            payment.date = _parse_dt(data.date)
        if data.reference is not None:
            payment.reference = data.reference
        if data.notes is not None:
            payment.notes = data.notes
        if data.status is not None:
            payment.status = data.status
        if data.customer_id is not None:
            payment.customer_id = to_uuid(data.customer_id)
        if data.supplier_id is not None:
            payment.supplier_id = to_uuid(data.supplier_id)
        if data.invoice_id is not None:
            payment.invoice_id = to_uuid(data.invoice_id)
        if data.expense_id is not None:
            payment.expense_id = to_uuid(data.expense_id)
        if data.financial_account_id is not None:
            payment.financial_account_id = to_uuid(data.financial_account_id)
        if data.counterparty_id is not None:
            payment.counterparty_id = to_uuid(data.counterparty_id)
        if data.related_transaction_id is not None:
            payment.related_transaction_id = to_uuid(data.related_transaction_id)

        payment.updated_by = actor_id
        commit()
        return payment

    def reverse_payment(self, payment_id: str, actor_id, reason: str = None):
        """Reverse a completed payment by creating a negative payment."""
        payment = db.session.get(Payment, payment_id)
        if payment is None or payment.business_id != self.business_id:
            raise NotFoundError("Payment not found")
        if payment.status != "completed":
            raise BusinessError("Can only reverse completed payments")

        original_allocations = list(payment.allocations)
        payment.allocations = []
        reversal_data = SimpleNamespace(
            payment_type=payment.payment_type,
            customer_id=str(payment.customer_id) if payment.customer_id else None,
            supplier_id=str(payment.supplier_id) if payment.supplier_id else None,
            invoice_id=None,
            expense_id=None,
            financial_account_id=str(payment.financial_account_id) if payment.financial_account_id else None,
            counterparty_id=str(payment.counterparty_id) if payment.counterparty_id else None,
            related_transaction_id=str(payment.id),
            branch_id=str(payment.branch_id) if payment.branch_id else None,
            amount=float(payment.amount),
            payment_method=payment.payment_method,
            date=datetime.now(timezone.utc),
            reference=f"REV: {payment.payment_number} - {reason or 'Reversal'}",
            notes=f"Reversal of payment {payment.payment_number}. {reason or ''}",
        )
        reversal = self.create_payment(reversal_data, actor_id)
        reversal.status = "completed"

        payment.status = "cancelled"
        payment.updated_by = actor_id

        for allocation in original_allocations:
            invoice = allocation.invoice
            invoice.paid_amount = (invoice.paid_amount or Decimal("0")) - allocation.amount
            invoice.balance_due = (invoice.total_amount or Decimal("0")) - invoice.paid_amount
            if invoice.balance_due < Decimal("0.005"):
                invoice.balance_due = Decimal("0")

            if invoice.balance_due > 0 and invoice.status == "paid":
                invoice.status = "partially_paid" if invoice.paid_amount > 0 else "sent"
            elif invoice.balance_due == 0:
                invoice.status = "paid"

            invoice.updated_by = actor_id

            # Update customer statement balance
            if invoice.customer:
                invoice.customer.statement_balance = (invoice.customer.statement_balance or Decimal("0")) + allocation.amount
                invoice.customer.updated_by = actor_id

            # Delete allocation
            db.session.delete(allocation)

        from .audit_log_service import log_event
        log_event(self.business_id, "reverse", "payment", actor_id, payment.id, {
            "payment_number": payment.payment_number, "reversal_id": reversal.id, "reason": reason,
        })
        commit()
        return reversal

    def list_payments(self, pagination, customer_id: str = None, supplier_id: str = None,
                      invoice_id: str = None, expense_id: str = None,
                      payment_type: str = None, payment_method: str = None,
                      status: str = None, from_date: str = None, to_date: str = None):
        stmt = select(Payment).where(Payment.business_id == self.business_id)

        if customer_id:
            stmt = stmt.where(Payment.customer_id == customer_id)
        if supplier_id:
            stmt = stmt.where(Payment.supplier_id == supplier_id)
        if invoice_id:
            stmt = stmt.where(Payment.invoice_id == invoice_id)
        if expense_id:
            stmt = stmt.where(Payment.expense_id == expense_id)
        if payment_type:
            stmt = stmt.where(Payment.payment_type == payment_type)
        if payment_method:
            stmt = stmt.where(Payment.payment_method == payment_method)
        if status:
            stmt = stmt.where(Payment.status == status)
        if from_date:
            stmt = stmt.where(Payment.date >= from_date)
        if to_date:
            stmt = stmt.where(Payment.date <= to_date)

        stmt = stmt.order_by(Payment.date.desc(), Payment.created_at.desc())

        from ..utils.pagination import paginate
        items, total, pages = paginate(stmt, pagination.page, pagination.per_page)
        return items, total, pages

    def get_payment(self, payment_id: str):
        payment = db.session.get(Payment, payment_id)
        if payment is None or payment.business_id != self.business_id:
            raise NotFoundError("Payment not found")
        return payment

    def delete_payment(self, payment_id: str):
        """Only allow deletion of draft payments."""
        payment = db.session.get(Payment, payment_id)
        if payment is None or payment.business_id != self.business_id:
            raise NotFoundError("Payment not found")
        if payment.status != "draft":
            raise BusinessError("Can only delete draft payments")
        db.session.delete(payment)
        commit()


def get_payment_service(business_id):
    return PaymentService(business_id)