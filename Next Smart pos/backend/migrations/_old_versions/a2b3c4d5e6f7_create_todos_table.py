"""create todos table

Revision ID: a2b3c4d5e6f7
Revises: 8f2c0a9b1e7d
Create Date: 2026-09-25 08:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "a2b3c4d5e6f7"
down_revision = "8f2c0a9b1e7d"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('todos',
    sa.Column('business_id', sa.UUID(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('due_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed', sa.Boolean(), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('updated_by', sa.UUID(), nullable=True),
    sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('todos', schema=None) as batch_op:
        batch_op.create_index('ix_todos_business_id', ['business_id'], unique=False)
        batch_op.create_index('ix_todos_completed', ['completed'], unique=False)
        batch_op.create_index('ix_todos_due_at', ['due_at'], unique=False)
        batch_op.create_index('ix_todos_position', ['position'], unique=False)


def downgrade():
    with op.batch_alter_table('todos', schema=None) as batch_op:
        batch_op.drop_index('ix_todos_position')
        batch_op.drop_index('ix_todos_due_at')
        batch_op.drop_index('ix_todos_completed')
        batch_op.drop_index('ix_todos_business_id')

    op.drop_table('todos')
