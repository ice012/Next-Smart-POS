"""Double-entry accounting service with journal entries and account management."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select

from ..exceptions import NotFoundError, ConflictError, ValidationError, BusinessError
from ..extensions import db
from ..models import FinancialAccount, JournalEntry, JournalLine, Invoice, Payment, Expense
from ..utils.db import commit
from ..utils.security import to_uuid
from .financial_calculation_service import get_calculation_service


class AccountingService:
    """Double-entry accounting service with strict debit=credit enforcement."""

    def __init__(self, business_id):
        self.business_id = business_id
        self.calc = get_calculation_service(business_id)

    # ==================== Chart of Accounts ====================

    def list_accounts(self, account_type: str = None, is_bank_account: bool = None, is_active: bool = True):
        stmt = select(FinancialAccount).where(FinancialAccount.business_id == self.business_id)
        if is_active is not None:
            stmt = stmt.where(FinancialAccount.is_active.is_(is_active))
        if account_type:
            stmt = stmt.where(FinancialAccount.account_type == account_type)
        if is_bank_account is not None:
            stmt = stmt.where(FinancialAccount.is_bank_account.is_(is_bank_account))
        stmt = stmt.order_by(FinancialAccount.code.asc())
        return db.session.scalars(stmt).all()

    def get_account(self, account_id: str):
        account = db.session.get(FinancialAccount, account_id)
        if account is None or account.business_id != self.business_id:
            raise NotFoundError("Financial account not found")
        return account

    def get_account_by_code(self, code: str):
        account = db.session.scalar(
            select(FinancialAccount).where(
                FinancialAccount.business_id == self.business_id,
                FinancialAccount.code == code,
            )
        )
        if account is None:
            raise NotFoundError("Financial account not found")
        return account

    def create_account(self, data, actor_id):
        # Check unique code
        existing = db.session.scalar(
            select(FinancialAccount).where(
                FinancialAccount.business_id == self.business_id,
                FinancialAccount.code == data.code,
            )
        )
        if existing:
            raise ConflictError("An account with this code already exists")

        # Check unique name
        existing = db.session.scalar(
            select(FinancialAccount).where(
                FinancialAccount.business_id == self.business_id,
                FinancialAccount.name == data.name,
            )
        )
        if existing:
            raise ConflictError("An account with this name already exists")

        # Validate parent
        if data.parent_id:
            parent = self.get_account(data.parent_id)
            if parent.account_type != data.account_type:
                raise ValidationError("Parent account must have the same account type")

        account = FinancialAccount(
            business_id=self.business_id,
            code=data.code,
            name=data.name,
            description=data.description,
            account_type=data.account_type,
            parent_id=to_uuid(data.parent_id) if data.parent_id else None,
            is_control_account=data.is_control_account or False,
            default_currency=data.default_currency,
            is_bank_account=data.is_bank_account or False,
            bank_details=data.bank_details,
        )
        account.created_by = actor_id
        account.updated_by = actor_id
        db.session.add(account)
        commit()
        return account

    def update_account(self, account_id: str, data, actor_id):
        account = self.get_account(account_id)

        if data.code is not None and data.code != account.code:
            existing = db.session.scalar(
                select(FinancialAccount).where(
                    FinancialAccount.business_id == self.business_id,
                    FinancialAccount.code == data.code,
                    FinancialAccount.id != account_id,
                )
            )
            if existing:
                raise ConflictError("An account with this code already exists")
            account.code = data.code

        if data.name is not None and data.name != account.name:
            existing = db.session.scalar(
                select(FinancialAccount).where(
                    FinancialAccount.business_id == self.business_id,
                    FinancialAccount.name == data.name,
                    FinancialAccount.id != account_id,
                )
            )
            if existing:
                raise ConflictError("An account with this name already exists")
            account.name = data.name

        if data.description is not None:
            account.description = data.description

        if data.account_type is not None and data.account_type != account.account_type:
            # Check if account has children
            children = db.session.scalar(
                select(func.count()).where(FinancialAccount.parent_id == account.id)
            )
            if children > 0:
                raise ValidationError("Cannot change account type with existing children")
            account.account_type = data.account_type

        if data.parent_id is not None:
            if data.parent_id:
                if data.parent_id == str(account_id):
                    raise ValidationError("An account cannot be its own parent")
                parent = self.get_account(data.parent_id)
                if parent.account_type != account.account_type:
                    raise ValidationError("Parent account must have the same account type")
                account.parent_id = to_uuid(data.parent_id)
            else:
                account.parent_id = None

        if data.is_control_account is not None:
            account.is_control_account = data.is_control_account

        if data.default_currency is not None:
            account.default_currency = data.default_currency

        if data.is_bank_account is not None:
            account.is_bank_account = data.is_bank_account

        if data.bank_details is not None:
            account.bank_details = data.bank_details

        if data.is_active is not None:
            account.is_active = data.is_active

        account.updated_by = actor_id
        commit()
        return account

    def delete_account(self, account_id: str):
        account = self.get_account(account_id)

        # Check for children
        children = db.session.scalar(
            select(func.count()).where(FinancialAccount.parent_id == account.id)
        )
        if children > 0:
            raise BusinessError("Cannot delete account with child accounts")

        # Check for journal lines
        lines = db.session.scalar(
            select(func.count()).where(JournalLine.account_id == account.id)
        )
        if lines > 0:
            raise BusinessError("Cannot delete account with posted journal lines")

        db.session.delete(account)
        commit()

    def get_account_balance(self, account_id: str, as_of_date: datetime = None) -> Decimal:
        """Calculate running balance for an account from journal lines."""
        account = self.get_account(account_id)

        stmt = (
            select(
                func.coalesce(func.sum(JournalLine.amount), 0)
            )
            .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
            .where(
                JournalLine.account_id == account_id,
                JournalEntry.business_id == self.business_id,
                JournalEntry.status == "posted",
            )
        )

        if as_of_date:
            stmt = stmt.where(JournalEntry.date <= as_of_date)

        # Apply debit/credit sign based on account type
        # Assets, Expenses, COGS: debit increases, credit decreases
        # Liabilities, Equity, Revenue: credit increases, debit decreases
        debit_sum = db.session.scalar(
            stmt.where(JournalLine.line_type == "debit")
        ) or Decimal("0")
        credit_sum = db.session.scalar(
            stmt.where(JournalLine.line_type == "credit")
        ) or Decimal("0")

        if account.account_type in ("asset", "expense", "cogs", "cash", "bank", "ar"):
            return debit_sum - credit_sum
        else:
            return credit_sum - debit_sum

    def get_trial_balance(self, as_of_date: datetime = None) -> list:
        """Get trial balance for all accounts."""
        accounts = self.list_accounts(is_active=True)
        balances = []
        for account in accounts:
            balance = self.get_account_balance(account.id, as_of_date)
            if balance != 0:
                if account.account_type in ("asset", "expense", "cogs", "cash", "bank", "ar"):
                    balances.append({
                        "account_id": str(account.id),
                        "code": account.code,
                        "name": account.name,
                        "account_type": account.account_type,
                        "debit": float(balance) if balance > 0 else 0,
                        "credit": float(-balance) if balance < 0 else 0,
                    })
                else:
                    balances.append({
                        "account_id": str(account.id),
                        "code": account.code,
                        "name": account.name,
                        "account_type": account.account_type,
                        "debit": float(-balance) if balance < 0 else 0,
                        "credit": float(balance) if balance > 0 else 0,
                    })
        return balances

    # ==================== Journal Entries ====================

    def list_journal_entries(self, pagination, status: str = None, from_date: str = None, to_date: str = None, reference_type: str = None, reference_id: str = None):
        stmt = select(JournalEntry).where(JournalEntry.business_id == self.business_id)
        if status:
            stmt = stmt.where(JournalEntry.status == status)
        if from_date:
            stmt = stmt.where(JournalEntry.date >= from_date)
        if to_date:
            stmt = stmt.where(JournalEntry.date <= to_date)
        if reference_type:
            stmt = stmt.where(JournalEntry.reference_type == reference_type)
        if reference_id:
            stmt = stmt.where(JournalEntry.reference_id == reference_id)
        stmt = stmt.order_by(JournalEntry.date.desc(), JournalEntry.created_at.desc())

        from ..utils.pagination import paginate
        items, total, pages = paginate(stmt, pagination.page, pagination.per_page)
        return items, total, pages

    def get_journal_entry(self, entry_id: str):
        entry = db.session.get(JournalEntry, entry_id)
        if entry is None or entry.business_id != self.business_id:
            raise NotFoundError("Journal entry not found")
        return entry

    def _validate_journal_lines(self, lines: list[dict]) -> dict:
        """Validate journal lines balance (debits == credits)."""
        result = self.calc.calculate_journal_balance(lines)
        if not result["is_balanced"]:
            raise ValidationError(
                f"Journal entry is not balanced: debits={result['total_debit']}, credits={result['total_credit']}, difference={result['difference']}"
            )
        return result

    def create_journal_entry(self, data, actor_id):
        # Validate balance
        self._validate_journal_lines(data.lines)

        # Generate entry number
        count = db.session.scalar(
            select(func.count()).where(JournalEntry.business_id == self.business_id)
        ) or 0
        entry_number = self.calc.get_next_journal_number(count)

        # Validate all accounts exist and belong to business
        for line_data in data.lines:
            account = db.session.get(FinancialAccount, line_data.account_id)
            if account is None or account.business_id != self.business_id:
                raise NotFoundError(f"Account {line_data.account_id} not found")
            if not account.is_active:
                raise ValidationError(f"Account {account.code} is not active")

        entry = JournalEntry(
            business_id=self.business_id,
            entry_number=entry_number,
            date=data.date,
            description=data.description,
            reference_type=data.reference_type,
            reference_id=to_uuid(data.reference_id) if data.reference_id else None,
            status="draft",
        )
        entry.created_by = actor_id
        entry.updated_by = actor_id
        db.session.add(entry)
        db.session.flush()

        total_debit = Decimal("0")
        total_credit = Decimal("0")

        for line_data in data.lines:
            amount = Decimal(str(line_data.amount))
            line = JournalLine(
                business_id=self.business_id,
                journal_entry_id=entry.id,
                account_id=to_uuid(line_data.account_id),
                line_type=line_data.line_type,
                amount=amount,
                description=line_data.description,
            )
            line.created_by = actor_id
            line.updated_by = actor_id
            db.session.add(line)

            if line_data.line_type == "debit":
                total_debit += amount
            else:
                total_credit += amount

        entry.total_debit = total_debit
        entry.total_credit = total_credit
        commit()
        return entry

    def update_journal_entry(self, entry_id: str, data, actor_id):
        entry = self.get_journal_entry(entry_id)

        if entry.status == "posted":
            raise BusinessError("Cannot modify a posted journal entry. Reverse it first.")
        if entry.status == "cancelled":
            raise BusinessError("Cannot modify a cancelled journal entry.")

        if data.description is not None:
            entry.description = data.description
        if data.date is not None:
            entry.date = data.date
        if data.reference_type is not None:
            entry.reference_type = data.reference_type
        if data.reference_id is not None:
            entry.reference_id = to_uuid(data.reference_id)
        if data.status is not None:
            entry.status = data.status

        if data.lines is not None:
            # Validate new lines
            self._validate_journal_lines(data.lines)

            # Delete old lines
            for line in entry.lines:
                db.session.delete(line)
            db.session.flush()

            total_debit = Decimal("0")
            total_credit = Decimal("0")

            for line_data in data.lines:
                account = db.session.get(FinancialAccount, line_data.account_id)
                if account is None or account.business_id != self.business_id:
                    raise NotFoundError(f"Account {line_data.account_id} not found")
                if not account.is_active:
                    raise ValidationError(f"Account {account.code} is not active")

                amount = Decimal(str(line_data.amount))
                line = JournalLine(
                    business_id=self.business_id,
                    journal_entry_id=entry.id,
                    account_id=to_uuid(line_data.account_id),
                    line_type=line_data.line_type,
                    amount=amount,
                    description=line_data.description,
                )
                line.created_by = actor_id
                line.updated_by = actor_id
                db.session.add(line)

                if line_data.line_type == "debit":
                    total_debit += amount
                else:
                    total_credit += amount

            entry.total_debit = total_debit
            entry.total_credit = total_credit

        entry.updated_by = actor_id
        commit()
        return entry

    def post_journal_entry(self, entry_id: str, actor_id):
        """Post a draft journal entry, updating account balances."""
        entry = self.get_journal_entry(entry_id)
        if entry.status != "draft":
            raise BusinessError(f"Cannot post entry with status: {entry.status}")

        # Re-validate balance
        lines = [
            {"line_type": line.line_type, "amount": line.amount}
            for line in entry.lines
        ]
        self._validate_journal_lines(lines)

        entry.status = "posted"
        entry.updated_by = actor_id
        commit()
        return entry

    def reverse_journal_entry(self, entry_id: str, actor_id, reversal_date: datetime = None, reason: str = None):
        """Create a reversing journal entry for a posted entry."""
        entry = self.get_journal_entry(entry_id)
        if entry.status != "posted":
            raise BusinessError(f"Can only reverse posted entries, current status: {entry.status}")

        reversal_date = reversal_date or datetime.now(timezone.utc)
        reversal_lines = []
        for line in entry.lines:
            reversal_lines.append({
                "account_id": str(line.account_id),
                "line_type": "credit" if line.line_type == "debit" else "debit",
                "amount": line.amount,
                "description": f"Reversal of {entry.entry_number}: {line.description or ''} {reason or ''}".strip(),
            })

        # Create reversal entry
        reversal_data = type('obj', (object,), {
            'date': reversal_date,
            'description': f"Reversal of {entry.entry_number}: {entry.description} {reason or ''}".strip(),
            'reference_type': 'reversal',
            'reference_id': str(entry.id),
            'lines': reversal_lines,
        })

        reversal_entry = self.create_journal_entry(reversal_data, actor_id)
        reversal_entry.status = "posted"
        reversal_entry.updated_by = actor_id

        # Mark original as reversed
        entry.status = "reversed"
        entry.updated_by = actor_id

        commit()
        return reversal_entry

    def cancel_journal_entry(self, entry_id: str, actor_id):
        """Cancel a draft journal entry."""
        entry = self.get_journal_entry(entry_id)
        if entry.status != "draft":
            raise BusinessError(f"Can only cancel draft entries, current status: {entry.status}")
        entry.status = "cancelled"
        entry.updated_by = actor_id
        commit()
        return entry

    # ==================== Standard Workflows ====================

    def _get_or_create_default_accounts(self) -> dict:
        """Get or create standard default accounts."""
        defaults = {
            "ar": {"code": "1200", "name": "Accounts Receivable", "account_type": "ar"},
            "ap": {"code": "2100", "name": "Accounts Payable", "account_type": "ap"},
            "cash": {"code": "1100", "name": "Cash", "account_type": "cash"},
            "bank": {"code": "1110", "name": "Bank", "account_type": "bank"},
            "revenue": {"code": "4000", "name": "Sales Revenue", "account_type": "revenue"},
            "cogs": {"code": "5000", "name": "Cost of Goods Sold", "account_type": "cogs"},
            "expense": {"code": "6000", "name": "General Expenses", "account_type": "expense"},
            "tax_payable": {"code": "2200", "name": "Tax Payable", "account_type": "liability"},
            "inventory": {"code": "1300", "name": "Inventory", "account_type": "asset"},
        }

        result = {}
        for key, info in defaults.items():
            account = db.session.scalar(
                select(FinancialAccount).where(
                    FinancialAccount.business_id == self.business_id,
                    FinancialAccount.code == info["code"],
                )
            )
            if account is None:
                # Find parent
                parent = None
                if key in ("ar", "cash", "bank", "inventory"):
                    parent = db.session.scalar(
                        select(FinancialAccount).where(
                            FinancialAccount.business_id == self.business_id,
                            FinancialAccount.account_type == "asset",
                        ).order_by(FinancialAccount.code.asc())
                    )
                elif key == "ap":
                    parent = db.session.scalar(
                        select(FinancialAccount).where(
                            FinancialAccount.business_id == self.business_id,
                            FinancialAccount.account_type == "liability",
                        ).order_by(FinancialAccount.code.asc())
                    )
                elif key in ("revenue",):
                    parent = db.session.scalar(
                        select(FinancialAccount).where(
                            FinancialAccount.business_id == self.business_id,
                            FinancialAccount.account_type == "revenue",
                        ).order_by(FinancialAccount.code.asc())
                    )
                elif key in ("cogs", "expense"):
                    parent = db.session.scalar(
                        select(FinancialAccount).where(
                            FinancialAccount.business_id == self.business_id,
                            FinancialAccount.account_type == "expense",
                        ).order_by(FinancialAccount.code.asc())
                    )

                account = FinancialAccount(
                    business_id=self.business_id,
                    code=info["code"],
                    name=info["name"],
                    account_type=info["account_type"],
                    parent_id=parent.id if parent else None,
                    is_control_account=(key in ("ar", "ap", "cash", "bank", "tax_payable", "inventory")),
                )
                db.session.add(account)
                db.session.flush()
            result[key] = account

        return result

    def post_sale_invoice(self, invoice_id: str, actor_id) -> JournalEntry:
        """Create journal entry for a sale invoice (AR + Revenue + Tax)."""
        invoice = db.session.get(Invoice, invoice_id)
        if invoice is None or invoice.business_id != self.business_id:
            raise NotFoundError("Invoice not found")
        if invoice.status not in ("sent", "partially_paid", "paid", "overdue"):
            raise BusinessError("Invoice must be sent before posting to accounting")

        accounts = self._get_or_create_default_accounts()

        lines = []

        # Debit: Accounts Receivable (total amount)
        lines.append({
            "account_id": str(accounts["ar"].id),
            "line_type": "debit",
            "amount": invoice.total_amount,
            "description": f"Invoice {invoice.invoice_number} - {invoice.customer.display_name if invoice.customer else 'Walk-in'}",
        })

        # Credit: Revenue (subtotal - discount)
        if invoice.subtotal > 0:
            lines.append({
                "account_id": str(accounts["revenue"].id),
                "line_type": "credit",
                "amount": invoice.subtotal - invoice.discount_amount,
                "description": f"Revenue from {invoice.invoice_number}",
            })

        # Credit: Tax Payable
        if invoice.tax_amount > 0:
            lines.append({
                "account_id": str(accounts["tax_payable"].id),
                "line_type": "credit",
                "amount": invoice.tax_amount,
                "description": f"Tax from {invoice.invoice_number}",
            })

        # Credit: Discount (if any)
        if invoice.discount_amount > 0:
            lines.append({
                "account_id": str(accounts["revenue"].id),
                "line_type": "debit",
                "amount": invoice.discount_amount,
                "description": f"Discount allowed on {invoice.invoice_number}",
            })

        entry_data = type('obj', (object,), {
            'date': invoice.date,
            'description': f"Sale invoice {invoice.invoice_number}",
            'reference_type': 'invoice',
            'reference_id': str(invoice.id),
            'lines': lines,
        })

        entry = self.create_journal_entry(entry_data, actor_id)
        entry = self.post_journal_entry(entry.id, actor_id)
        return entry

    def post_cogs(self, invoice_id: str, actor_id) -> JournalEntry:
        """Create journal entry for COGS and inventory reduction."""
        invoice = db.session.get(Invoice, invoice_id)
        if invoice is None or invoice.business_id != self.business_id:
            raise NotFoundError("Invoice not found")

        accounts = self._get_or_create_default_accounts()

        # Calculate total COGS from invoice items
        total_cogs = Decimal("0")
        for item in invoice.items:
            if not item.is_service and item.cost_price and item.quantity:
                total_cogs += item.cost_price * item.quantity

        if total_cogs == 0:
            return None

        lines = []

        # Debit: COGS
        lines.append({
            "account_id": str(accounts["cogs"].id),
            "line_type": "debit",
            "amount": total_cogs,
            "description": f"COGS for {invoice.invoice_number}",
        })

        # Credit: Inventory
        lines.append({
            "account_id": str(accounts["inventory"].id),
            "line_type": "credit",
            "amount": total_cogs,
            "description": f"Inventory reduction for {invoice.invoice_number}",
        })

        entry_data = type('obj', (object,), {
            'date': invoice.date,
            'description': f"COGS for invoice {invoice.invoice_number}",
            'reference_type': 'cogs',
            'reference_id': str(invoice.id),
            'lines': lines,
        })

        entry = self.create_journal_entry(entry_data, actor_id)
        entry = self.post_journal_entry(entry.id, actor_id)
        return entry

    def post_customer_payment(self, payment_id: str, actor_id) -> JournalEntry:
        """Create journal entry for customer payment (Cash/Bank + AR)."""
        payment = db.session.get(Payment, payment_id)
        if payment is None or payment.business_id != self.business_id:
            raise NotFoundError("Payment not found")
        if payment.payment_type != "customer_receipt":
            raise BusinessError("Payment is not a customer receipt")

        accounts = self._get_or_create_default_accounts()

        # Determine cash/bank account
        cash_account = payment.financial_account
        if not cash_account:
            cash_account = accounts["cash"] if payment.payment_method in ("cash", "mpesa") else accounts["bank"]

        lines = []

        # Debit: Cash/Bank
        lines.append({
            "account_id": str(cash_account.id),
            "line_type": "debit",
            "amount": payment.amount,
            "description": f"Payment {payment.payment_number} - {payment.reference or ''}",
        })

        # Credit: AR (or specific invoice)
        if payment.invoice_id:
            lines.append({
                "account_id": str(accounts["ar"].id),
                "line_type": "credit",
                "amount": payment.amount,
                "description": f"Payment for invoice {payment.invoice.invoice_number if payment.invoice else 'N/A'}",
            })
        else:
            lines.append({
                "account_id": str(accounts["ar"].id),
                "line_type": "credit",
                "amount": payment.amount,
                "description": f"Payment on account {payment.payment_number}",
            })

        entry_data = type('obj', (object,), {
            'date': payment.date,
            'description': f"Customer payment {payment.payment_number}",
            'reference_type': 'payment',
            'reference_id': str(payment.id),
            'lines': lines,
        })

        entry = self.create_journal_entry(entry_data, actor_id)
        entry = self.post_journal_entry(entry.id, actor_id)
        return entry

    def post_expense(self, expense_id: str, actor_id) -> JournalEntry:
        """Create journal entry for expense (Expense + Cash/Bank/AP)."""
        expense = db.session.get(Expense, expense_id)
        if expense is None or expense.business_id != self.business_id:
            raise NotFoundError("Expense not found")

        accounts = self._get_or_create_default_accounts()

        # Determine payment account
        pay_account = expense.financial_account
        if not pay_account:
            if expense.payment_method in ("cash", "mpesa"):
                pay_account = accounts["cash"]
            elif expense.payment_method in ("bank", "card", "cheque"):
                pay_account = accounts["bank"]
            else:
                pay_account = accounts["ap"]

        lines = []

        # Debit: Expense
        lines.append({
            "account_id": str(accounts["expense"].id),
            "line_type": "debit",
            "amount": expense.amount,
            "description": f"Expense {expense.expense_number}: {expense.description}",
        })

        # Debit: Tax (if any)
        if expense.tax_amount > 0:
            lines.append({
                "account_id": str(accounts["tax_payable"].id),
                "line_type": "debit",
                "amount": expense.tax_amount,
                "description": f"Input tax on {expense.expense_number}",
            })

        # Credit: Cash/Bank/AP
        lines.append({
            "account_id": str(pay_account.id),
            "line_type": "credit",
            "amount": expense.total_amount,
            "description": f"Payment for {expense.expense_number}",
        })

        entry_data = type('obj', (object,), {
            'date': expense.date,
            'description': f"Expense {expense.expense_number}",
            'reference_type': 'expense',
            'reference_id': str(expense.id),
            'lines': lines,
        })

        entry = self.create_journal_entry(entry_data, actor_id)
        entry = self.post_journal_entry(entry.id, actor_id)
        return entry

    def post_supplier_payment(self, payment_id: str, actor_id) -> JournalEntry:
        """Create journal entry for supplier payment (AP + Cash/Bank)."""
        payment = db.session.get(Payment, payment_id)
        if payment is None or payment.business_id != self.business_id:
            raise NotFoundError("Payment not found")
        if payment.payment_type != "supplier_payment":
            raise BusinessError("Payment is not a supplier payment")

        accounts = self._get_or_create_default_accounts()

        # Determine cash/bank account
        cash_account = payment.financial_account
        if not cash_account:
            cash_account = accounts["cash"] if payment.payment_method in ("cash", "mpesa") else accounts["bank"]

        lines = []

        # Debit: AP
        lines.append({
            "account_id": str(accounts["ap"].id),
            "line_type": "debit",
            "amount": payment.amount,
            "description": f"Payment to supplier {payment.payment_number}",
        })

        # Credit: Cash/Bank
        lines.append({
            "account_id": str(cash_account.id),
            "line_type": "credit",
            "amount": payment.amount,
            "description": f"Supplier payment {payment.payment_number}",
        })

        entry_data = type('obj', (object,), {
            'date': payment.date,
            'description': f"Supplier payment {payment.payment_number}",
            'reference_type': 'supplier_payment',
            'reference_id': str(payment.id),
            'lines': lines,
        })

        entry = self.create_journal_entry(entry_data, actor_id)
        entry = self.post_journal_entry(entry.id, actor_id)
        return entry


def get_accounting_service(business_id):
    return AccountingService(business_id)