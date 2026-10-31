"""add earnings advance requests

Revision ID: 86c0bd00b575
Revises: b2e4c8d1a730
Create Date: 2026-09-29 16:21:27.410801

Autogenerate also reported pre-existing drift on `users.is_superadmin` and
`refresh_tokens.business_id`. Those are deliberately left out: they are unrelated
to this feature and already exist in deployed databases, so applying them here
would be a schema change nobody asked for. `users.is_superadmin` is a NOT NULL
column with no server default, so adding it through a migration would fail on any
database that already has users in it.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '86c0bd00b575'
down_revision = 'b2e4c8d1a730'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'earnings_advance_requests',
        sa.Column('business_id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('requested_amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('requested_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('decided_by', sa.Uuid(), nullable=True),
        sa.Column('decision_note', sa.Text(), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('updated_by', sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['decided_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('earnings_advance_requests', schema=None) as batch_op:
        batch_op.create_index('ix_earnings_advance_requests_business', ['business_id', 'status'], unique=False)
        batch_op.create_index(batch_op.f('ix_earnings_advance_requests_business_id'), ['business_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_earnings_advance_requests_status'), ['status'], unique=False)
        batch_op.create_index('ix_earnings_advance_requests_user', ['user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_earnings_advance_requests_user_id'), ['user_id'], unique=False)


def downgrade():
    with op.batch_alter_table('earnings_advance_requests', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_earnings_advance_requests_user_id'))
        batch_op.drop_index('ix_earnings_advance_requests_user')
        batch_op.drop_index(batch_op.f('ix_earnings_advance_requests_status'))
        batch_op.drop_index(batch_op.f('ix_earnings_advance_requests_business_id'))
        batch_op.drop_index('ix_earnings_advance_requests_business')

    op.drop_table('earnings_advance_requests')
