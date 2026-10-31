"""enable quotations on all subscription plans

Quotations shipped only on the Enterprise plan, so the nav entry was hidden
and /api/quotations returned 403 for everyone else. The plan module lists are
persisted as JSON on subscription_plans, and seed_platform_defaults only
inserts missing plans, so existing rows must be refreshed explicitly. The
expected values are read from app.models.subscription to avoid drift.

Revision ID: b3d6e8a1f470
Revises: f1a7c93e5b28
Create Date: 2026-09-26

"""

import sqlalchemy as sa
from alembic import op

revision = "b3d6e8a1f470"
down_revision = "f1a7c93e5b28"
branch_labels = None
depends_on = None

# The module lists as they stood before quotations were made universal.
PREVIOUS = {
    "free": ["core", "customers", "suppliers", "items", "inventory", "sales",
             "finance", "expenses", "dashboard", "reports", "notifications",
             "audit", "todos"],
    "starter": ["core", "customers", "suppliers", "items", "sales", "finance",
                "expenses", "dashboard", "notifications", "todos", "iam"],
    "professional": ["core", "customers", "suppliers", "items", "inventory",
                     "sales", "finance", "expenses", "dashboard", "reports",
                     "notifications", "audit", "iam", "todos", "admin"],
    "enterprise": None,  # derived from SYSTEM_MODULES, unchanged
}


def _sync(connection, definitions):
    plans = sa.table(
        "subscription_plans",
        sa.column("code", sa.String),
        sa.column("modules", sa.JSON),
    )
    for code, modules in definitions.items():
        if modules is None:
            continue
        connection.execute(
            plans.update().where(plans.c.code == code).values(modules=modules)
        )


def upgrade():
    from app.models.subscription import get_default_plan_modules

    bind = op.get_bind()
    _sync(bind, {tier: get_default_plan_modules(tier) for tier in PREVIOUS})


def downgrade():
    bind = op.get_bind()
    _sync(bind, PREVIOUS)
