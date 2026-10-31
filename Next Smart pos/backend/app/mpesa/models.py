"""M-Pesa STK push tracking.

An M-Pesa sale is not posted when the cashier presses Charge. The till sends a
STK push, and Safaricom calls the business back once the customer approves or
declines on their phone. This table is the hand-off point between those two
worlds: it holds the sale draft that was priced but not yet written, so the
callback can create the sale only once the money has actually moved.
"""
from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import validates

from ..extensions import db
from ..models.mixins import AuditedBase, UUIDType as SAB_UUID

MPESA_REQUEST_STATUSES = {
    "pending": "pending",
    "completed": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
    "timeout": "timeout",
}

#: Statuses a request can no longer move out of. The POS stops polling here.
TERMINAL_STATUSES = {"completed", "failed", "cancelled", "timeout"}


class MpesaStkRequest(AuditedBase):
    __tablename__ = "mpesa_stk_requests"
    __table_args__ = (
        UniqueConstraint(
            "merchant_request_id", name="uq_mpesa_stk_requests_merchant_request_id"
        ),
        Index("ix_mpesa_stk_checkout_request", "checkout_request_id"),
        Index("ix_mpesa_stk_business_status", "business_id", "status"),
    )

    business_id = Column(
        SAB_UUID(),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    customer_id = Column(
        SAB_UUID(), ForeignKey("customers.id", ondelete="SET NULL"), nullable=True
    )
    # Populated only once the payment is confirmed and the sale is written.
    sale_id = Column(
        SAB_UUID(),
        ForeignKey("sale_invoices.id", ondelete="SET NULL"),
        nullable=True,
    )
    # The cashier who rang the sale, so the deferred sale is attributed to them.
    requested_by = Column(SAB_UUID(), nullable=True)

    merchant_request_id = Column(String(64), nullable=False)
    checkout_request_id = Column(String(64), nullable=True)

    phone = Column(String(20), nullable=False)
    amount = Column(Numeric(15, 2), nullable=False)
    account_reference = Column(String(64), nullable=True)
    transaction_description = Column(String(120), nullable=True)

    status = Column(String(20), default="pending", nullable=False)
    result_code = Column(String(10), nullable=True)
    result_desc = Column(String(255), nullable=True)
    mpesa_receipt = Column(String(60), nullable=True)
    callback_payload = Column(JSON, nullable=True)

    # The priced-but-unwritten sale. Replayed verbatim by the callback handler,
    # with the M-Pesa payment appended, once Safaricom confirms.
    sale_payload = Column(JSON, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)

    @validates("status")
    def _check_status(self, key, value):
        if value not in MPESA_REQUEST_STATUSES:
            raise ValueError(f"Invalid M-Pesa request status: {value}")
        return value

    @property
    def is_terminal(self):
        return self.status in TERMINAL_STATUSES

    def to_dict(self):
        return {
            "id": str(self.id),
            "business_id": str(self.business_id),
            "customer_id": str(self.customer_id) if self.customer_id else None,
            "sale_id": str(self.sale_id) if self.sale_id else None,
            "merchant_request_id": self.merchant_request_id,
            "checkout_request_id": self.checkout_request_id,
            "phone": self.phone,
            "amount": float(self.amount) if self.amount is not None else None,
            "account_reference": self.account_reference,
            "transaction_description": self.transaction_description,
            "status": self.status,
            "is_terminal": self.is_terminal,
            "result_code": self.result_code,
            "result_desc": self.result_desc,
            "mpesa_receipt": self.mpesa_receipt,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
