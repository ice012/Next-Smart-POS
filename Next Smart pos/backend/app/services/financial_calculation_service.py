"""Financial calculation service - backend-only monetary logic.
All computations use Decimal for precision and ACID transaction safety.
"""
from decimal import Decimal
from typing import Optional

from flask import current_app

from .money import money, qty, compute_tax, compute_total, apply_rounding


class FinancialCalculationService:
    """Centralized financial calculation engine for invoices, expenses, and payments."""

    def __init__(self, business_id):
        self.business_id = business_id

    def calculate_invoice_totals(
        self,
        items: list[dict],
        discount_amount: Decimal = Decimal("0"),
        tax_overrides: Optional[dict] = None
    ) -> dict:
        """Calculate invoice totals from line items.

        Args:
            items: List of dicts with quantity, unit_price, discount_amount, tax_rate
            discount_amount: Header-level discount
            tax_overrides: Optional dict mapping tax_code -> rate for tax config lookup

        Returns:
            Dict with subtotal, discount_amount, tax_amount, total_amount, rounding_amount
        """
        subtotal = Decimal("0")
        tax_amount = Decimal("0")

        for item in items:
            q = qty(item["quantity"])
            unit_price = money(item["unit_price"])
            line_discount = money(item.get("discount_amount", "0"))
            line_subtotal = (q * unit_price) - line_discount
            subtotal += line_subtotal

            tax_rate = Decimal(str(item.get("tax_rate", "0")))
            if tax_overrides and item.get("tax_code"):
                tax_rate = Decimal(str(tax_overrides.get(item["tax_code"], tax_rate)))
            line_tax = compute_tax(line_subtotal, tax_rate)
            tax_amount += line_tax

        total = compute_total(subtotal, discount_amount, tax_amount)
        rounded_total, rounding = apply_rounding(total)
        balance_due = rounded_total

        return {
            "subtotal": money(subtotal),
            "discount_amount": money(discount_amount),
            "tax_amount": money(tax_amount),
            "total_amount": rounded_total,
            "rounding_amount": money(rounding),
            "balance_due": money(balance_due),
        }

    def calculate_expense_total(
        self,
        amount: Decimal,
        tax_amount: Decimal = Decimal("0")
    ) -> dict:
        """Calculate expense total."""
        total = money(amount + tax_amount)
        rounded_total, rounding = apply_rounding(total)
        return {
            "amount": money(amount),
            "tax_amount": money(tax_amount),
            "total_amount": rounded_total,
            "rounding_amount": money(rounding),
        }

    def calculate_payment_allocation(
        self,
        payment_amount: Decimal,
        invoice_balance_due: Decimal
    ) -> dict:
        """Calculate how a payment allocates to an invoice.

        Returns allocated amount and remaining payment/unpaid balance.
        Prevents overpayment by capping allocation at balance_due.
        """
        allocatable = min(payment_amount, invoice_balance_due)
        remaining_payment = payment_amount - allocatable
        new_balance_due = invoice_balance_due - allocatable

        if new_balance_due < Decimal("0.005"):  # Account for rounding
            new_balance_due = Decimal("0")

        return {
            "allocated_amount": money(allocatable),
            "remaining_payment": money(remaining_payment),
            "new_balance_due": money(new_balance_due),
            "is_fully_paid": new_balance_due == Decimal("0"),
        }

    def apply_tax_inclusive(
        self,
        line_total: Decimal,
        tax_rate: Decimal
    ) -> dict:
        """Calculate exclusive amounts from tax-inclusive total."""
        if tax_rate == 0:
            return {"exclusive_amount": line_total, "tax_amount": Decimal("0")}
        divisor = Decimal("1") + (money(tax_rate) / Decimal("100"))
        exclusive = money(line_total / divisor)
        tax_amt = money(line_total - exclusive)
        return {"exclusive_amount": exclusive, "tax_amount": tax_amt}

    def calculate_journal_balance(self, lines: list[dict]) -> dict:
        """Verify journal entry balances (debits == credits)."""
        total_debit = Decimal("0")
        total_credit = Decimal("0")

        for line in lines:
            amount = money(line["amount"])
            if line["line_type"] == "debit":
                total_debit += amount
            elif line["line_type"] == "credit":
                total_credit += amount

        is_balanced = total_debit == total_credit
        difference = money(total_debit - total_credit)

        return {
            "total_debit": money(total_debit),
            "total_credit": money(total_credit),
            "is_balanced": is_balanced,
            "difference": difference,
        }

    def calculate_account_balance(
        self,
        account_id: str,
        as_of_date: Optional[str] = None
    ) -> Decimal:
        """Calculate running balance for a financial account.
        To be implemented with journal line aggregation.
        """
        # This will be implemented in accounting service
        raise NotImplementedError("Use AccountingService.get_account_balance")

    def get_next_invoice_number(self, config) -> str:
        """Generate next invoice number from business config."""
        prefix = config.invoice_prefix or "INV-"
        next_num = config.invoice_next_number or 1
        return f"{prefix}{next_num:06d}"

    def get_next_payment_number(self, existing_count: int) -> str:
        """Generate next payment number."""
        return f"PAY-{existing_count + 1:06d}"

    def get_next_expense_number(self, existing_count: int) -> str:
        """Generate next expense number."""
        return f"EXP-{existing_count + 1:06d}"

    def get_next_journal_number(self, existing_count: int) -> str:
        """Generate next journal entry number."""
        return f"JE-{existing_count + 1:06d}"

    def get_next_account_code(self, account_type: str, existing_codes: list) -> str:
        """Generate next account code based on type prefix."""
        prefixes = {
            "asset": "1",
            "liability": "2",
            "equity": "3",
            "revenue": "4",
            "cogs": "5",
            "expense": "6",
            "ar": "1",
            "ap": "2",
            "cash": "1",
            "bank": "1",
        }
        prefix = prefixes.get(account_type, "9")
        existing = [c for c in existing_codes if c.startswith(prefix)]
        max_num = 0
        for code in existing:
            try:
                num = int(code[len(prefix):])
                max_num = max(max_num, num)
            except (ValueError, IndexError):
                continue
        return f"{prefix}{max_num + 1:04d}"


def get_calculation_service(business_id):
    return FinancialCalculationService(business_id)