"""M-Pesa request shapes.

Kept out of ``app.schemas`` so the payment integration is self-contained. The
only thing the core still knows about M-Pesa is that the business settings
accept an opaque ``mpesa`` object, which this package validates itself.
"""
from pydantic import Field, field_validator

from ..schemas import SaleItemInputSchema, _Base, _str


class MpesaConfigSchema(_Base):
    """Safaricom Daraja credentials, stored per business.

    ``consumer_secret`` is write-only: it is never returned by the API, so the
    settings form always submits it blank and a blank value means "unchanged".
    """

    enabled: bool | None = None
    environment: str | None = Field(default=None, pattern="^(live|sandbox)$")
    consumer_key: str | None = _str(0, 200, required=False)
    consumer_secret: str | None = _str(0, 200, required=False)
    shortcode: str | None = _str(0, 20, required=False)
    passkey: str | None = _str(0, 200, required=False)
    callback_url: str | None = _str(0, 500, required=False)
    # Daraja truncates anything longer, so these are capped at the source
    # rather than silently losing the tail of a long business name.
    account_reference: str | None = _str(0, 13, required=False)
    transaction_description: str | None = _str(0, 13, required=False)

    @field_validator("callback_url")
    @classmethod
    def _check_callback_url(cls, v):
        # Safaricom will not call a plain http or relative address, so a bad
        # one is caught here rather than silently failing on every push.
        if v and not v.startswith("https://"):
            raise ValueError("callback_url must start with https://")
        return v


class MpesaSaleDraftSchema(_Base):
    """A priced-but-unposted sale queued behind an M-Pesa approval.

    There is deliberately no ``payments`` field: the payment is written by the
    callback handler once Safaricom confirms, and a client-supplied amount
    would be an unverified claim about money that has not moved yet.
    """

    customer_id: str | None = None
    notes: str | None = _str(0, 2000, required=False)
    items: list[SaleItemInputSchema] = Field(min_length=1)


class MpesaStkPushSchema(_Base):
    phone: str = _str(7, 20)
    # Optional because the server prices the cart anyway. The till sends it so
    # a cashier can push an amount other than the exact total (a part-payment
    # against a larger bill, say); the customer still has to approve whatever
    # figure appears in their M-Pesa app.
    amount: float | None = Field(default=None, gt=0, le=100_000_000)
    account_reference: str | None = _str(0, 13, required=False)
    transaction_description: str | None = _str(0, 13, required=False)
    sale: MpesaSaleDraftSchema
