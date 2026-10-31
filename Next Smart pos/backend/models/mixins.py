import uuid as uuidlib

from sqlalchemy import Boolean, Column, DateTime, func
from sqlalchemy import types
from sqlalchemy.orm import validates

from ..extensions import db


class UUIDType(types.TypeDecorator):
    """UUID column that accepts ``uuid.UUID`` or a string on assignment/query."""

    impl = types.Uuid
    cache_ok = True
    python_type = uuidlib.UUID

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, uuidlib.UUID):
            return value
        return uuidlib.UUID(str(value))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, uuidlib.UUID):
            return value
        return uuidlib.UUID(str(value))


class UUIDMixin:
    """Primary key as a UUID.

    On PostgreSQL this maps to the native ``uuid`` type; on SQLite it is
    persisted as a string. Pydantic/SQLAlchemy normalize to ``uuid.UUID``.
    """

    id = Column(UUIDType(), primary_key=True, default=uuidlib.uuid4)


class TimestampMixin:
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class AuditMixin:
    """Auditable columns required on every persistent table."""

    is_active = Column(Boolean, default=True, nullable=False)
    created_by = Column(UUIDType(), nullable=True)
    updated_by = Column(UUIDType(), nullable=True)


class AuditedBase(UUIDMixin, TimestampMixin, AuditMixin, db.Model):
    __abstract__ = True


def touch(obj, user_id):
    """Set created_by/updated_by for auditing. Idempotent & user-aware."""
    if user_id is not None:
        if obj.created_by is None:
            obj.created_by = user_id
        obj.updated_by = user_id
