import uuid as uuidlib

from sqlalchemy import Boolean, Column, ForeignKey, Index, String
from .mixins import UUIDType as SAB_UUID
from sqlalchemy.orm import validates

from ..extensions import db
from ..models.mixins import AuditedBase
from ..utils.security import is_valid_uuid


class User(AuditedBase):
    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_email", "email", unique=True),
        Index("ix_users_username", "username", unique=True),
    )

    email = Column(String(255), nullable=False)
    # Optional handle that can be used in place of the email at sign-in.
    # Nullable so existing accounts keep working until a username is set.
    username = Column(String(50), nullable=True)
    password_hash = Column(String(255), nullable=False)
    # Optional short-form sign-in PIN. Stored hashed, never in plaintext.
    pin_hash = Column(String(255), nullable=True)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    phone = Column(String(50), nullable=True)
    is_email_verified = Column(Boolean, default=False, nullable=False)
    is_superadmin = Column(Boolean, default=False, nullable=False)
    primary_business_id = Column(
        SAB_UUID(),
        ForeignKey("businesses.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    business = db.relationship("Business", back_populates="users")
    memberships = db.relationship(
        "BusinessUser", back_populates="user", cascade="all, delete-orphan"
    )
    refresh_tokens = db.relationship("RefreshToken", back_populates="user", cascade="all, delete-orphan")
    reset_tokens = db.relationship("PasswordResetToken", back_populates="user", cascade="all, delete-orphan")

    @validates("email")
    def _normalize_email(self, key, value):
        return (value or "").strip().lower()

    @validates("username")
    def _normalize_username(self, key, value):
        if value is None:
            return None
        normalized = (value or "").strip().lower()
        return normalized or None

    def to_dict(self, with_memberships=False):
        data = {
            "id": str(self.id),
            "email": self.email,
            "username": self.username,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "phone": self.phone,
            "is_email_verified": self.is_email_verified,
            "is_active": self.is_active,
            "is_superadmin": self.is_superadmin,
            "has_pin": bool(self.pin_hash),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if with_memberships:
            data["memberships"] = [m.to_dict() for m in self.memberships if m.is_active]
        return data


def get_user_by_id(user_id):
    import uuid as uuidlib
    if not is_valid_uuid(user_id):
        return None
    return db.session.get(User, uuidlib.UUID(str(user_id)))
