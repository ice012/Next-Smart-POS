"""add mpesa stk push tracking

Holds priced-but-unposted M-Pesa sales while Safaricom confirms the STK push.

Revision ID: b8c1d4e7a902
Revises: e5f9a2b7c810
Create Date: 2026-09-28

"""

import sqlalchemy as sa
from alembic import op

revision = "b8c1d4e7a902"
down_revision = "e5f9a2b7c810"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "mpesa_stk_requests",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=True),
        sa.Column("sale_id", sa.Uuid(), nullable=True),
        sa.Column("requested_by", sa.Uuid(), nullable=True),
        sa.Column("merchant_request_id", sa.String(length=64), nullable=False),
        sa.Column("checkout_request_id", sa.String(length=64), nullable=True),
        sa.Column("phone", sa.String(length=20), nullable=False),
        sa.Column("amount", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("account_reference", sa.String(length=64), nullable=True),
        sa.Column("transaction_description", sa.String(length=120), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("result_code", sa.String(length=10), nullable=True),
        sa.Column("result_desc", sa.String(length=255), nullable=True),
        sa.Column("mpesa_receipt", sa.String(length=60), nullable=True),
        sa.Column("callback_payload", sa.JSON(), nullable=True),
        sa.Column("sale_payload", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["sale_id"], ["sale_invoices.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "merchant_request_id", name="uq_mpesa_stk_requests_merchant_request_id"
        ),
    )
    with op.batch_alter_table("mpesa_stk_requests") as batch:
        batch.create_index(
            "ix_mpesa_stk_checkout_request", ["checkout_request_id"], unique=False
        )
        batch.create_index(
            "ix_mpesa_stk_business_status", ["business_id", "status"], unique=False
        )


def downgrade():
    with op.batch_alter_table("mpesa_stk_requests") as batch:
        batch.drop_index("ix_mpesa_stk_business_status")
        batch.drop_index("ix_mpesa_stk_checkout_request")
    op.drop_table("mpesa_stk_requests")
