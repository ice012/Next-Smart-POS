from sqlalchemy import Column, ForeignKey, Index, JSON, String, Text
from .mixins import UUIDType as SAB_UUID

from ..extensions import db
from ..models.mixins import AuditedBase


class AuditLog(AuditedBase):
    """Platform-wide, tenant-scoped audit trail for security-relevant events."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_date", "created_at"),
    )

    business_id = Column(
        SAB_UUID(), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=True, index=True
    )
    user_id = Column(
        SAB_UUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action = Column(String(100), nullable=False, index=True)
    resource = Column(String(80), nullable=False, index=True)
    resource_id = Column(String(64), nullable=True)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)
    details = Column(JSON, nullable=True)

    user = db.relationship("User", foreign_keys=[user_id])

    def to_dict(self):
        details = self.details or {}
        return {
            "id": str(self.id),
            "business_id": str(self.business_id) if self.business_id else None,
            "user_id": str(self.user_id) if self.user_id else None,
            "user_email": self.user.email if self.user else None,
            "action": self.action,
            "resource": self.resource,
            "resource_id": self.resource_id,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "details": details,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
