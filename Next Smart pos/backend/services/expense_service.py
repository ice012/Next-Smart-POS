"""Expense service for managing expenses, categories, and attachments."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select

from ..exceptions import NotFoundError, ConflictError, ValidationError, BusinessError
from ..extensions import db
from ..models import (
    Supplier, ExpenseCategory, Expense, ExpenseAttachment, FinancialAccount, Branch, Payment
)
from ..utils.db import commit
from ..utils.pagination import paginate
from ..utils.security import to_uuid
from .financial_calculation_service import get_calculation_service

# How far ahead a recurring expense due date warns the business owner.
EXPENSE_DUE_REMINDER_DAYS = 7


def _parse_dt(value):
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        raise ValidationError(f"Invalid date: {value}")


def _run_due_reminders(business_id):
    """Re-evaluate owner reminders after an expense is written.

    Wrapped so a reminder problem can never fail the expense write itself: the
    expense is already committed by the time this runs, and the scheduled job in
    ``scripts/expense_due_reminders.py`` is the backstop.
    """
    try:
        from .notification_service import generate_expense_due_reminders

        generate_expense_due_reminders(business_id)
    except Exception:
        db.session.rollback()



class ExpenseService:
    """Expense management with categories, approvals, and payment tracking."""

    def __init__(self, business_id):
        self.business_id = business_id
        self.calc = get_calculation_service(business_id)

    def _get_next_expense_number(self) -> str:
        count = db.session.scalar(
            select(func.count()).where(Expense.business_id == self.business_id)
        ) or 0
        return self.calc.get_next_expense_number(count)

    # ==================== Expense Categories ====================

    def list_categories(self, is_active: bool = True):
        stmt = select(ExpenseCategory).where(ExpenseCategory.business_id == self.business_id)
        if is_active is not None:
            stmt = stmt.where(ExpenseCategory.is_active.is_(is_active))
        stmt = stmt.order_by(ExpenseCategory.name.asc())
        return db.session.scalars(stmt).all()

    def get_category(self, category_id: str):
        cat = db.session.get(ExpenseCategory, category_id)
        if cat is None or cat.business_id != self.business_id:
            raise NotFoundError("Expense category not found")
        return cat

    def create_category(self, data, actor_id):
        existing = db.session.scalar(
            select(ExpenseCategory).where(
                ExpenseCategory.business_id == self.business_id,
                ExpenseCategory.name == data.name,
            )
        )
        if existing:
            raise ConflictError("An expense category with this name already exists")

        if data.parent_id:
            parent = self.get_category(data.parent_id)
            # Allow multi-level nesting

        cat = ExpenseCategory(
            business_id=self.business_id,
            name=data.name,
            description=data.description,
            parent_id=to_uuid(data.parent_id) if data.parent_id else None,
        )
        cat.created_by = actor_id
        cat.updated_by = actor_id
        db.session.add(cat)
        commit()
        return cat

    def update_category(self, category_id: str, data, actor_id):
        cat = self.get_category(category_id)

        if data.name is not None and data.name != cat.name:
            existing = db.session.scalar(
                select(ExpenseCategory).where(
                    ExpenseCategory.business_id == self.business_id,
                    ExpenseCategory.name == data.name,
                    ExpenseCategory.id != category_id,
                )
            )
            if existing:
                raise ConflictError("An expense category with this name already exists")
            cat.name = data.name

        if data.description is not None:
            cat.description = data.description

        if data.parent_id is not None:
            if data.parent_id:
                if data.parent_id == str(category_id):
                    raise ValidationError("A category cannot be its own parent")
                self.get_category(data.parent_id)
                cat.parent_id = to_uuid(data.parent_id)
            else:
                cat.parent_id = None

        if data.is_active is not None:
            cat.is_active = data.is_active

        cat.updated_by = actor_id
        commit()
        return cat

    def delete_category(self, category_id: str):
        cat = self.get_category(category_id)
        children = db.session.scalar(
            select(func.count()).where(ExpenseCategory.parent_id == cat.id)
        )
        if children > 0:
            raise BusinessError("Cannot delete category with subcategories")
        expenses = db.session.scalar(
            select(func.count()).where(Expense.category_id == cat.id)
        )
        if expenses > 0:
            raise BusinessError("Cannot delete category with associated expenses")
        db.session.delete(cat)
        commit()

    # ==================== Expenses ====================

    def _validate_expense_data(self, data):
        if data.supplier_id:
            supplier = db.session.get(Supplier, data.supplier_id)
            if supplier is None or supplier.business_id != self.business_id:
                raise NotFoundError("Supplier not found")

        if data.category_id:
            self.get_category(data.category_id)

        if data.financial_account_id:
            account = db.session.get(FinancialAccount, data.financial_account_id)
            if account is None or account.business_id != self.business_id:
                raise NotFoundError("Financial account not found")

        if data.branch_id:
            branch = db.session.get(Branch, data.branch_id)
            if branch is None or branch.business_id != self.business_id:
                raise NotFoundError("Branch not found")

    def create_expense(self, data, actor_id):
        self._validate_expense_data(data)

        expense = Expense(
            business_id=self.business_id,
            supplier_id=to_uuid(data.supplier_id) if data.supplier_id else None,
            category_id=to_uuid(data.category_id) if data.category_id else None,
            financial_account_id=to_uuid(data.financial_account_id) if data.financial_account_id else None,
            branch_id=to_uuid(data.branch_id) if data.branch_id else None,
            expense_number=self._get_next_expense_number(),
            date=_parse_dt(data.date) or datetime.now(timezone.utc),
            due_date=_parse_dt(data.due_date),
            description=data.description,
            amount=Decimal(str(data.amount)),
            tax_amount=Decimal(str(data.tax_amount or 0)),
            total_amount=Decimal(str(data.amount)) + Decimal(str(data.tax_amount or 0)),
            payment_method=data.payment_method,
            reference=data.reference,
            notes=data.notes,
            status=data.status or "draft",
        )
        expense.created_by = actor_id
        expense.updated_by = actor_id
        db.session.add(expense)
        db.session.flush()

        from .audit_log_service import log_event
        log_event(self.business_id, "create", "expense", actor_id, expense.id, {
            "expense_number": expense.expense_number, "total_amount": float(expense.total_amount or 0),
        })
        commit()
        _run_due_reminders(self.business_id)
        return expense

    def get_expense(self, expense_id: str):
        expense = db.session.get(Expense, expense_id)
        if expense is None or expense.business_id != self.business_id:
            raise NotFoundError("Expense not found")
        return expense

    def update_expense(self, expense_id: str, data, actor_id):
        expense = self.get_expense(expense_id)

        if expense.status not in ("draft", "approved"):
            raise BusinessError(f"Cannot modify expense in {expense.status} status")

        self._validate_expense_data(data)

        if data.supplier_id is not None:
            expense.supplier_id = to_uuid(data.supplier_id) if data.supplier_id else None
        if data.category_id is not None:
            expense.category_id = to_uuid(data.category_id) if data.category_id else None
        if data.financial_account_id is not None:
            expense.financial_account_id = to_uuid(data.financial_account_id) if data.financial_account_id else None
        if data.branch_id is not None:
            expense.branch_id = to_uuid(data.branch_id) if data.branch_id else None
        if data.date is not None:
            expense.date = _parse_dt(data.date)
        if data.due_date is not None:
            expense.due_date = _parse_dt(data.due_date)
        if data.description is not None:
            expense.description = data.description
        if data.amount is not None:
            expense.amount = Decimal(str(data.amount))
            expense.total_amount = expense.amount + expense.tax_amount
        if data.tax_amount is not None:
            expense.tax_amount = Decimal(str(data.tax_amount))
            expense.total_amount = expense.amount + expense.tax_amount
        if data.payment_method is not None:
            expense.payment_method = data.payment_method
        if data.reference is not None:
            expense.reference = data.reference
        if data.notes is not None:
            expense.notes = data.notes
        if data.status is not None:
            self._transition_expense_status(expense, data.status, actor_id)

        expense.updated_by = actor_id
        commit()
        _run_due_reminders(self.business_id)
        return expense

    def _transition_expense_status(self, expense: Expense, new_status: str, actor_id):
        valid_transitions = {
            "draft": ["approved", "cancelled"],
            "approved": ["paid", "cancelled"],
            "paid": ["cancelled"],  # Via refund
            "cancelled": [],
        }

        if new_status not in valid_transitions.get(expense.status, []):
            raise BusinessError(f"Invalid status transition: {expense.status} -> {new_status}")

        expense.status = new_status
        expense.updated_by = actor_id

    def approve_expense(self, expense_id: str, actor_id):
        expense = self.get_expense(expense_id)
        self._transition_expense_status(expense, "approved", actor_id)
        from .audit_log_service import log_event
        log_event(self.business_id, "approve", "expense", actor_id, expense.id, {
            "expense_number": expense.expense_number, "status": "approved",
        })
        commit()
        return expense

    def mark_expense_paid(self, expense_id: str, actor_id):
        expense = self.get_expense(expense_id)
        self._transition_expense_status(expense, "paid", actor_id)
        if expense.supplier:
            expense.supplier.statement_balance = (
                expense.supplier.statement_balance or Decimal("0")
            ) + (expense.total_amount or Decimal("0"))
            expense.supplier.updated_by = actor_id
        if expense.financial_account_id:
            from .accounting_service import get_accounting_service
            try:
                get_accounting_service(self.business_id).post_expense(expense.id, actor_id)
            except BusinessError:
                pass
        from .audit_log_service import log_event
        log_event(self.business_id, "mark_paid", "expense", actor_id, expense.id, {
            "expense_number": expense.expense_number, "status": "paid",
        })
        commit()
        return expense

    def cancel_expense(self, expense_id: str, actor_id, reason: str = None):
        expense = self.get_expense(expense_id)
        if expense.status == "paid":
            raise BusinessError("Cannot cancel a paid expense. Create a refund instead.")
        self._transition_expense_status(expense, "cancelled", actor_id)
        if reason:
            expense.notes = (expense.notes or "") + f"\nCancelled: {reason}"
        from .audit_log_service import log_event
        log_event(self.business_id, "cancel", "expense", actor_id, expense.id, {
            "expense_number": expense.expense_number, "reason": reason,
        })
        commit()
        return expense

    def list_expenses(self, pagination, supplier_id: str = None, category_id: str = None,
                      status: str = None, payment_method: str = None,
                      from_date: str = None, to_date: str = None):
        stmt = select(Expense).where(Expense.business_id == self.business_id)

        if supplier_id:
            stmt = stmt.where(Expense.supplier_id == supplier_id)
        if category_id:
            stmt = stmt.where(Expense.category_id == category_id)
        if status:
            stmt = stmt.where(Expense.status == status)
        if payment_method:
            stmt = stmt.where(Expense.payment_method == payment_method)
        if from_date:
            stmt = stmt.where(Expense.date >= from_date)
        if to_date:
            stmt = stmt.where(Expense.date <= to_date)

        stmt = stmt.order_by(Expense.date.desc(), Expense.created_at.desc())
        items, total, pages = paginate(stmt, pagination.page, pagination.per_page)
        return items, total, pages

    def delete_expense(self, expense_id: str):
        expense = self.get_expense(expense_id)
        if expense.status != "draft":
            raise BusinessError("Can only delete draft expenses")
        db.session.delete(expense)
        commit()

    # ==================== Attachments ====================

    def add_attachment(self, expense_id: str, data, actor_id):
        expense = self.get_expense(expense_id)

        attachment = ExpenseAttachment(
            business_id=self.business_id,
            expense_id=expense.id,
            filename=data.filename,
            original_filename=data.original_filename,
            mime_type=data.mime_type,
            file_size=data.file_size,
            storage_path=data.storage_path,
            description=data.description,
        )
        attachment.created_by = actor_id
        attachment.updated_by = actor_id
        db.session.add(attachment)
        commit()
        return attachment

    def remove_attachment(self, attachment_id: str):
        attachment = db.session.get(ExpenseAttachment, attachment_id)
        if attachment is None or attachment.business_id != self.business_id:
            raise NotFoundError("Attachment not found")
        db.session.delete(attachment)
        commit()

    def get_supplier_statement_documents(self, supplier_id: str, from_date: str = None, to_date: str = None):
        """Return the (supplier, expenses, payments) ORM objects behind a statement."""
        supplier = db.session.get(Supplier, supplier_id)
        if supplier is None or supplier.business_id != self.business_id:
            raise NotFoundError("Supplier not found")

        exp_stmt = select(Expense).where(
            Expense.business_id == self.business_id,
            Expense.supplier_id == supplier_id,
        )
        pay_stmt = select(Payment).where(
            Payment.business_id == self.business_id,
            Payment.supplier_id == supplier_id,
            Payment.status == "completed",
        )
        if from_date:
            exp_stmt = exp_stmt.where(Expense.date >= from_date)
            pay_stmt = pay_stmt.where(Payment.date >= from_date)
        if to_date:
            exp_stmt = exp_stmt.where(Expense.date <= to_date)
            pay_stmt = pay_stmt.where(Payment.date <= to_date)

        expenses = db.session.scalars(exp_stmt.order_by(Expense.date.asc())).all()
        payments = db.session.scalars(pay_stmt.order_by(Payment.date.asc())).all()
        return supplier, expenses, payments

    def get_supplier_statement(self, supplier_id: str, from_date: str = None, to_date: str = None):
        supplier = db.session.get(Supplier, supplier_id)
        if supplier is None or supplier.business_id != self.business_id:
            raise NotFoundError("Supplier not found")

        stmt = select(Expense).where(
            Expense.business_id == self.business_id,
            Expense.supplier_id == supplier_id,
        )
        if from_date:
            stmt = stmt.where(Expense.date >= from_date)
        if to_date:
            stmt = stmt.where(Expense.date <= to_date)
        stmt = stmt.order_by(Expense.date.asc())

        expenses = db.session.scalars(stmt).all()

        return {
            "supplier": supplier.to_dict(),
            "statement_balance": float(supplier.statement_balance or 0),
            "expenses": [exp.to_dict() for exp in expenses],
        }


def get_expense_service(business_id):
    return ExpenseService(business_id)