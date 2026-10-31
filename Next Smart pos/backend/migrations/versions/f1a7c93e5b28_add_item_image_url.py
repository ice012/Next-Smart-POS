"""add product image column

Adds the image URL used by the product image upload on the catalog.

Revision ID: f1a7c93e5b28
Revises: d4e8f1a92b76
Create Date: 2026-09-26

"""

import sqlalchemy as sa
from alembic import op

revision = "f1a7c93e5b28"
down_revision = "d4e8f1a92b76"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("items") as batch:
        batch.add_column(sa.Column("image_url", sa.String(length=2048), nullable=True))


def downgrade():
    with op.batch_alter_table("items") as batch:
        batch.drop_column("image_url")
