"""Staff earnings: live derivation from a member's own sales, and advance requests.

There is no payroll ledger in this system. A member's earnings are derived on
demand from the sales they ring up, priced with the `commission_rate` and
`base_salary` held on their `business_users` membership. That keeps the figure
honest — it can never drift from the sales that produced it — at the cost of
having no closed-pay-period history.

An advance is money paid out ahead of that balance, so requests are recorded
here and approved by someone holding `earnings.advance_approve`.
"""

from decimal import Decimal
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.orm import validates
from .mixins import UUIDType as SAB_UUID

from ..extensions import db
from ..models.mixins import AuditedBase

ADVANCE_STATUSES = {
    "pending": "pending",
    "approved": "approved",
    "rejected": "rejected",
    "cancelled": "cancelled",
}

# Statuses that hold money back from the requester's available balance. A
# pending request counts as well as an approved one: otherwise a member could
# fire off the same request repeatedly while waiting for a decision.
BALANCE_HOLDING_STATUSES = ("pending", "approved")


class EarningsAdvanceRequest(AuditedBase):
    """A staff member's request to be paid out ahead of their earned balance."""

    __tablename__ = "earnings_advance_requests"
    __table_args__ = (
        Index("ix_earnings_advance_requests_business", "business_id", "status"),
        Index("ix_earnings_advance_requests_user", "user_id"),
    )

    business_id = Column(
        SAB_UUID(),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        SAB_UUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Retained so the request still reads sensibly if the membership's pay
    # settings are later changed, and so the record explains itself later.
    requested_amount = Column(Numeric(12, 2), nullable=False)
    reason = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default="pending", index=True)

    requested_at = Column(DateTime(timezone=True), nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    decided_by = Column(
        SAB_UUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decision_note = Column(Text, nullable=True)

    user = db.relationship("User", foreign_keys=[user_id])
    decider = db.relationship("User", foreign_keys=[decided_by])

    @validates("requested_amount")
    def _check_amount(self, key, value):
        if value is None:
            raise ValueError("An advance amount is required")
        if Decimal(value) <= 0:
            raise ValueError("Advance amount must be greater than zero")
        return value

    @validates("status")
    def _check_status(self, key, value):
        if value not in ADVANCE_STATUSES:
            raise ValueError(f"Unknown advance status: {value}")
        return value

    def to_dict(self):
        return {
            "id": str(self.id),
            "business_id": str(self.business_id),
            "user_id": str(self.user_id),
            "requested_amount": float(self.requested_amount) if self.requested_amount is not None else 0.0,
            "reason": self.reason,
            "status": self.status,
            "requested_at": self.requested_at.isoformat() if self.requested_at else None,
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
            "decided_by": str(self.decided_by) if self.decided_by else None,
            "decider_name": self._decider_name(),
            "decision_note": self.decision_note,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def _decider_name(self):
        if self.decider is None:
            return None
        parts = [p for p in (self.decider.first_name, self.decider.last_name) if p]
        return " ".join(parts) or self.decider.email
