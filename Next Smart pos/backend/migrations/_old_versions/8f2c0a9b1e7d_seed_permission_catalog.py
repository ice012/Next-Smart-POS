"""seed permission catalog

Revision ID: 8f2c0a9b1e7d
Revises: 1cf19a4aebfe
Create Date: 2026-09-24

"""
import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "8f2c0a9b1e7d"
down_revision = "1cf19a4aebfe"
branch_labels = None
depends_on = None


PERMISSION_CATALOG = [
    ("business.read", "business", "read", "View business configuration"),
    ("business.write", "business", "write", "Create/update business configuration"),
    ("branch.read", "branch", "read", "View branches"),
    ("branch.write", "branch", "write", "Create/update branches"),
    ("user.read", "user", "read", "View users in the business"),
    ("user.write", "user", "write", "Create/update users in the business"),
    ("user.delete", "user", "delete", "Deactivate users in the business"),
    ("role.read", "role", "read", "View roles"),
    ("role.write", "role", "write", "Create/update roles"),
    ("role.delete", "role", "delete", "Delete custom roles"),
    ("permission.read", "permission", "read", "View the permission catalog"),
    ("profile.read", "profile", "read", "View own profile"),
    ("profile.write", "profile", "write", "Update own profile"),
]


def _permissions_table():
    return sa.table(
        "permissions",
        sa.column("id", sa.UUID()),
        sa.column("name", sa.String()),
        sa.column("resource", sa.String()),
        sa.column("action", sa.String()),
        sa.column("description", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        sa.column("is_active", sa.Boolean()),
        sa.column("created_by", sa.UUID()),
        sa.column("updated_by", sa.UUID()),
    )


def upgrade():
    conn = op.get_bind()
    existing = {
        row[0]
        for row in conn.execute(sa.text("SELECT name FROM permissions")).fetchall()
    }
    now = datetime.now(timezone.utc)
    rows = [
        {
            "id": uuid.uuid4(),
            "name": name,
            "resource": resource,
            "action": action,
            "description": desc,
            "created_at": now,
            "updated_at": now,
            "is_active": True,
            "created_by": None,
            "updated_by": None,
        }
        for (name, resource, action, desc) in PERMISSION_CATALOG
        if name not in existing
    ]
    if rows:
        op.bulk_insert(_permissions_table(), rows)


def downgrade():
    conn = op.get_bind()
    names = ",".join(f"'{n}'" for n, _, _, _ in PERMISSION_CATALOG)
    conn.execute(sa.text(f"DELETE FROM permissions WHERE name IN ({names})"))
