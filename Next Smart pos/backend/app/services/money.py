"""Decimal arithmetic helpers shared by sales/inventory services.

All monetary and quantity math is performed with ``Decimal`` and rounded to
the configured scale (default 2 decimal places) before being persisted. This
keeps server-side calculations deterministic regardless of client input.
"""
from decimal import ROUND_HALF_UP, Decimal

from flask import current_app

DEFAULT_SCALE = 2

# Symbol per ISO code, mirroring frontend/src/lib/currency.js. Abbreviated
# codes ("Ksh") are separated from the amount by a space; true symbols are not.
#
# The PDF base fonts are Helvetica with WinAnsiEncoding, which has no glyph for
# the Naira or Rupee signs, so those fall back to their ISO code. Printed
# documents must never render a replacement box for a currency symbol.
_CURRENCY_SYMBOLS = {
    "KES": "Ksh ", "UGX": "UGX ", "TZS": "TSh ", "AED": "AED ",
    "USD": "$", "GBP": "£", "EUR": "€", "JPY": "¥",
    "NGN": "NGN ", "INR": "INR ",
    "ZAR": "R", "AUD": "A$", "CAD": "C$",
}
DEFAULT_CURRENCY = "KES"


def currency_symbol(currency: str | None) -> str:
    """Return the display prefix for *currency*, e.g. "Ksh " for KES."""
    code = (currency or "").strip().upper()
    if not code:
        return _CURRENCY_SYMBOLS[DEFAULT_CURRENCY]
    return _CURRENCY_SYMBOLS.get(code, f"{code} ")


def format_money(value, currency: str | None = None) -> str:
    """Grouped amount with the currency prefix, e.g. "Ksh 1,234.00".

    Kept free of the ``Decimal`` type on the way out so it can be dropped
    straight into a report or a receipt line.
    """
    return f"{currency_symbol(currency)}{money(value):,.{_scale()}f}"


def _scale() -> int:
    try:
        return int(current_app.config.get("MONEY_DECIMAL_PLACES", DEFAULT_SCALE))
    except (TypeError, ValueError):
        return DEFAULT_SCALE


def money(value) -> Decimal:
    """Coerce *value* to a Decimal rounded to the money scale."""
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        d = value
    else:
        d = Decimal(str(value))
    return d.quantize(Decimal(1).scaleb(-_scale()), rounding=ROUND_HALF_UP)


def qty(value) -> Decimal:
    """Coerce *value* to a Decimal rounded to the quantity scale."""
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        d = value
    else:
        d = Decimal(str(value))
    return d.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def to_float(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def line_total(unit_price, quantity, discount_amount=Decimal("0")) -> Decimal:
    """Compute a line total: (unit_price * quantity) - discount_amount."""
    return money((qty(unit_price) * qty(quantity)) - money(discount_amount))


def compute_tax(amount: Decimal, rate: Decimal) -> Decimal:
    """Compute tax amount from a taxable base and percentage rate."""
    return money(money(amount) * (money(rate) / Decimal("100")))


def compute_total(subtotal: Decimal, discount_amount: Decimal,
                  tax_amount: Decimal, rounding_amount: Decimal = Decimal("0")) -> Decimal:
    """Invoice total: subtotal - discount + tax + rounding."""
    return money(
        money(subtotal) - money(discount_amount) + money(tax_amount) + money(rounding_amount)
    )


def apply_rounding(amount: Decimal) -> tuple[Decimal, Decimal]:
    """Split *amount* into a rounded total and the rounding delta."""
    rounded = money(amount)
    return rounded, money(rounded - money(amount))