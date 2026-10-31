"""add employee / staff payment mode

Adds the compensation columns used to register staff members with a payment
mode (salary, commission or salary + commission) on the business membership.

Revision ID: e5f9a2b7c810
Revises: b3d6e8a1f470
Create Date: 2026-09-27

"""

import sqlalchemy as sa
from alembic import op

revision = "e5f9a2b7c810"
down_revision = "b3d6e8a1f470"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("business_users") as batch:
        batch.add_column(sa.Column("payment_mode", sa.String(length=20), nullable=True))
        batch.add_column(sa.Column("commission_rate", sa.Numeric(precision=12, scale=4), nullable=True))
        batch.add_column(sa.Column("base_salary", sa.Numeric(precision=12, scale=2), nullable=True))


def downgrade():
    with op.batch_alter_table("business_users") as batch:
        batch.drop_column("base_salary")
        batch.drop_column("commission_rate")
        batch.drop_column("payment_mode")