"""Notify business owners about recurring expenses that fall due within a week.

Run this on a schedule (daily is enough) to keep reminders going for expenses
that were created or had their due date set some time ago:

    python scripts/expense_due_reminders.py
    python scripts/expense_due_reminders.py --days 3

Writing an expense already re-checks reminders for that tenant, so this job is
the backstop rather than the only trigger. It is idempotent: an expense is
reminded once per due date.
"""

import argparse
import os
import pathlib
import sys

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.chdir(BACKEND_DIR)

from sqlalchemy import select  # noqa: E402

from app import create_app  # noqa: E402
from app.config import Config  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Business  # noqa: E402
from app.services.notification_service import (  # noqa: E402
    EXPENSE_DUE_REMINDER_DAYS,
    generate_expense_due_reminders,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days",
        type=int,
        default=EXPENSE_DUE_REMINDER_DAYS,
        help=f"reminder window in days (default {EXPENSE_DUE_REMINDER_DAYS})",
    )
    args = parser.parse_args()

    app = create_app(Config)
    with app.app_context():
        business_ids = [row[0] for row in db.session.execute(select(Business.id)).all()]
        total = 0
        for business_id in business_ids:
            created = generate_expense_due_reminders(business_id, days_ahead=args.days)
            if created:
                print(f"business {business_id}: {created} reminder(s)")
            total += created
    print(f"{total} reminder(s) created across {len(business_ids)} businesses")


if __name__ == "__main__":
    main()
