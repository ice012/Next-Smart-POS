"""add business contact fields and user sign-in pin

Adds the company contact/address columns used by the Company Profile page and
the hashed sign-in PIN used by the short-form login flow.

Revision ID: d4e8f1a92b76
Revises: c2f7a1b93d45
Create Date: 2026-09-26

"""

import sqlalchemy as sa
from alembic import op

revision = "d4e8f1a92b76"
down_revision = "c2f7a1b93d45"
branch_labels = None
depends_on = None

BUSINESS_COLUMNS = (
    ("email", sa.String(length=255)),
    ("phone", sa.String(length=50)),
    ("website", sa.String(length=255)),
    ("address_line1", sa.String(length=255)),
    ("address_line2", sa.String(length=255)),
    ("city", sa.String(length=100)),
    ("state", sa.String(length=100)),
    ("postal_code", sa.String(length=32)),
)


def upgrade():
    with op.batch_alter_table("businesses") as batch:
        for name, column_type in BUSINESS_COLUMNS:
            batch.add_column(sa.Column(name, column_type, nullable=True))

    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("pin_hash", sa.String(length=255), nullable=True))


def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.drop_column("pin_hash")

    with op.batch_alter_table("businesses") as batch:
        for name, _ in BUSINESS_COLUMNS:
            batch.drop_column(name)
