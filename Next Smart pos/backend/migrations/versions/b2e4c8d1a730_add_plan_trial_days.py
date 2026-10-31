"""add per-plan trial length

Each subscription plan now carries the length of the free trial a signup on
that plan receives, instead of every tenant sharing one global 14-day constant.
Existing plans are backfilled to 14 so no tenant sees their trial change.

Revision ID: b2e4c8d1a730
Revises: a1c6d92e40f8
Create Date: 2026-09-29

"""

import sqlalchemy as sa
from alembic import op

revision = "b2e4c8d1a730"
down_revision = "a1c6d92e40f8"
branch_labels = None
depends_on = None


def upgrade():
    # The server_default backfills existing plans and covers any raw insert
    # that does not name the column; every application write sets it anyway.
    with op.batch_alter_table("subscription_plans") as batch:
        batch.add_column(
            sa.Column("trial_days", sa.Integer(), nullable=False, server_default="14")
        )


def downgrade():
    with op.batch_alter_table("subscription_plans") as batch:
        batch.drop_column("trial_days")
