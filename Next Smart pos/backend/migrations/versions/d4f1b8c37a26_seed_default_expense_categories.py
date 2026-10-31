"""seed default expense categories

Backfills the starter expense category list for every business that has no
expense categories yet, so the category dropdown on the New Expense form is
populated for tenants created before this list existed. Businesses that already
curated their own categories are left untouched.

Revision ID: d4f1b8c37a26
Revises: c7a2f5e91b34
Create Date: 2026-09-28

"""

import uuid

import sqlalchemy as sa
from alembic import op

revision = "d4f1b8c37a26"
down_revision = "c7a2f5e91b34"
branch_labels = None
depends_on = None

DEFAULT_EXPENSE_CATEGORIES = [
    ("Rent", "Premises rent and service charge"),
    ("Utilities", "Electricity, water, internet and phone"),
    ("Salaries and Wages", "Staff salaries, wages and statutory benefits"),
    ("Supplies", "Consumables, stationery and cleaning supplies"),
    ("Transport and Fuel", "Fuel, fares, deliveries and vehicle upkeep"),
    ("Marketing", "Advertising, print and online promotion"),
    ("Equipment", "Tools, machines and IT equipment"),
    ("Bank Charges", "Account fees and card processing charges"),
    ("Professional Fees", "Legal, accounting and consultancy"),
    ("Other", "Anything that does not fit another category"),
]

expense_categories = sa.table(
    "expense_categories",
    sa.column("id", sa.Uuid()),
    sa.column("business_id", sa.Uuid()),
    sa.column("name", sa.String(100)),
    sa.column("description", sa.Text()),
    sa.column("is_active", sa.Boolean()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)

businesses = sa.table(
    "businesses",
    sa.column("id", sa.Uuid()),
)


def upgrade():
    bind = op.get_bind()
    business_ids = [row[0] for row in bind.execute(sa.select(businesses.c.id))]
    if not business_ids:
        return

    rows = []
    for business_id in business_ids:
        has_any = bind.execute(
            sa.select(sa.func.count())
            .select_from(expense_categories)
            .where(expense_categories.c.business_id == business_id)
        ).scalar()
        if has_any:
            continue
        for name, description in DEFAULT_EXPENSE_CATEGORIES:
            rows.append(
                {
                    # The model's id default is Python-side, so bulk_insert has
                    # to supply the primary key itself.
                    "id": uuid.uuid4(),
                    "business_id": business_id,
                    "name": name,
                    "description": description,
                    "is_active": True,
                }
            )

    if rows:
        op.bulk_insert(expense_categories, rows)


def downgrade():
    # Seeded rows are indistinguishable from user-created ones, so removing
    # them here could delete a category a business relies on. No-op by design.
    pass
