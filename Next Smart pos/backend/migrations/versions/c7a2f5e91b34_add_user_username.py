"""add user username

Adds an optional unique username on users so an account can be signed in with
a handle instead of an email address. Existing rows stay NULL until the user
sets one from the Profile page.

Revision ID: c7a2f5e91b34
Revises: b8c1d4e7a902
Create Date: 2026-09-28

"""

import sqlalchemy as sa
from alembic import op

revision = "c7a2f5e91b34"
down_revision = "b8c1d4e7a902"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("username", sa.String(length=50), nullable=True))
        batch.create_index("ix_users_username", ["username"], unique=True)


def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.drop_index("ix_users_username")
        batch.drop_column("username")
