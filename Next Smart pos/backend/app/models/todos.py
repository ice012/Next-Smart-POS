from datetime import datetime, timezone
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text
from .mixins import UUIDType as SAB_UUID

from ..extensions import db
from ..models.mixins import AuditedBase


class Todo(AuditedBase):
    __tablename__ = "todos"
    __table_args__ = (
        Index("ix_todos_business_id", "business_id"),
        Index("ix_todos_completed", "completed"),
        Index("ix_todos_due_at", "due_at"),
        Index("ix_todos_position", "position"),
    )

    business_id = Column(
        SAB_UUID(),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
    )
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    due_at = Column(DateTime(timezone=True), nullable=True)
    completed = Column(Boolean, default=False, nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    position = Column(Integer, default=0, nullable=False)

    business = db.relationship("Business", back_populates="todos")

    def to_dict(self):
        return {
            "id": str(self.id),
            "business_id": str(self.business_id),
            "title": self.title,
            "description": self.description,
            "due_at": self.due_at.isoformat() if self.due_at else None,
            "completed": self.completed,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "position": self.position,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


def utc_now():
    return datetime.now(timezone.utc)
