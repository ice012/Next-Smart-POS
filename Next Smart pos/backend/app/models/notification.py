from sqlalchemy import Boolean, Column, ForeignKey, Index, JSON, String, Text
from .mixins import UUIDType as SAB_UUID

from ..extensions import db
from ..models.mixins import AuditedBase


NOTIFICATION_CATEGORIES = {
    "invoice": "invoice",
    "payment": "payment",
    "expense": "expense",
    "inventory": "inventory",
    "system": "system",
}

NOTIFICATION_PRIORITIES = {"low", "normal", "high", "urgent"}


class Notification(AuditedBase):
    """Business- and user-scoped notifications (alerts, reminders, events)."""

    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_unread_business", "business_id", "is_read"),
        Index("ix_notifications_date", "created_at"),
    )

    business_id = Column(
        SAB_UUID(), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=True, index=True
    )
    user_id = Column(
        SAB_UUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    category = Column(String(50), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    message = Column(Text, nullable=False)
    reference_type = Column(String(30), nullable=True)
    reference_id = Column(SAB_UUID(), nullable=True)
    is_read = Column(Boolean, default=False, nullable=False, index=True)
    priority = Column(String(20), nullable=False, default="normal")
    channels = Column(JSON, nullable=False, default=list)
    meta = Column("meta_json", JSON, nullable=True)

    def to_dict(self):
        return {
            "id": str(self.id),
            "business_id": str(self.business_id) if self.business_id else None,
            "user_id": str(self.user_id) if self.user_id else None,
            "category": self.category,
            "title": self.title,
            "message": self.message,
            "reference_type": self.reference_type,
            "reference_id": str(self.reference_id) if self.reference_id else None,
            "is_read": self.is_read,
            "priority": self.priority,
            "channels": self.channels or [],
            "metadata": self.meta or {},
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
