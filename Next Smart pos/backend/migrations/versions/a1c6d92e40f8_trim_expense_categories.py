"""trim expense categories to the core list

Removes the three categories dropped from the default list (Supplies,
Transport and Fuel, Bank Charges, Professional Fees) and renames the
"Transport and Fuel" category to the shorter "Transport" every business now
uses. A category that an existing expense still points at is left in place so
historical expenses keep their category.

Revision ID: a1c6d92e40f8
Revises: d4f1b8c37a26
Create Date: 2026-09-28

"""

import sqlalchemy as sa
from alembic import op

revision = "a1c6d92e40f8"
down_revision = "d4f1b8c37a26"
branch_labels = None
depends_on = None

# Categories that no longer belong in the default list.
RETIRED_CATEGORY_NAMES = (
    "Supplies",
    "Bank Charges",
    "Professional Fees",
)
RENAMES = {"Transport and Fuel": "Transport"}

expense_categories = sa.table(
    "expense_categories",
    sa.column("id", sa.Uuid()),
    sa.column("business_id", sa.Uuid()),
    sa.column("name", sa.String(100)),
    sa.column("description", sa.Text()),
)
expenses = sa.table(
    "expenses",
    sa.column("id", sa.Uuid()),
    sa.column("category_id", sa.Uuid()),
)


def _in_use(bind, category_id):
    return bool(
        bind.execute(
            sa.select(sa.func.count())
            .select_from(expenses)
            .where(expenses.c.category_id == category_id)
        ).scalar()
    )


def upgrade():
    bind = op.get_bind()

    for old_name, new_name in RENAMES.items():
        rows = bind.execute(
            sa.select(expense_categories.c.id, expense_categories.c.business_id).where(
                expense_categories.c.name == old_name
            )
        ).all()
        for category_id, _business_id in rows:
            clash = bind.execute(
                sa.select(expense_categories.c.id).where(
                    expense_categories.c.business_id == _business_id,
                    expense_categories.c.name == new_name,
                )
            ).first()
            if clash is not None:
                # The business already has a "Transport" category: drop the old
                # one if unused, otherwise leave it alone.
                if not _in_use(bind, category_id):
                    bind.execute(
                        sa.delete(expense_categories).where(
                            expense_categories.c.id == category_id
                        )
                    )
                continue
            bind.execute(
                sa.update(expense_categories)
                .where(expense_categories.c.id == category_id)
                .values(name=new_name)
            )

    for name in RETIRED_CATEGORY_NAMES:
        rows = bind.execute(
            sa.select(expense_categories.c.id).where(
                expense_categories.c.name == name
            )
        ).all()
        for (category_id,) in rows:
            if not _in_use(bind, category_id):
                bind.execute(
                    sa.delete(expense_categories).where(
                        expense_categories.c.id == category_id
                    )
                )


def downgrade():
    # Re-adding the retired defaults is not safe to do blindly: a business may
    # have since created its own category with the same name, which would trip
    # the unique (business_id, name) constraint. Left as a no-op by design.
    pass
