"""Notification service for in-app alerts and event-driven messages."""
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import and_, or_, select, func, update

from ..extensions import db
from ..models import Notification, NOTIFICATION_CATEGORIES
from ..utils.pagination import paginate

# How far ahead a recurring expense due date triggers an owner reminder.
EXPENSE_DUE_REMINDER_DAYS = 7
# Marks the notification as the 7-day recurring-expense reminder so the same
# expense never produces the same reminder twice.
EXPENSE_DUE_REMINDER_TAG = "expense_due_reminder"


def create_notification(
    business_id=None,
    user_id=None,
    category: str = "system",
    title: str = "",
    message: str = "",
    reference_type: Optional[str] = None,
    reference_id=None,
    priority: str = "normal",
    channels: Optional[list] = None,
    meta: Optional[dict] = None,
) -> Notification:
    """Create a notification (optionally addressed to a single user or the whole business)."""
    notif = Notification(
        business_id=business_id,
        user_id=user_id,
        category=category,
        title=title,
        message=message,
        reference_type=reference_type,
        reference_id=reference_id,
        priority=priority,
        channels=channels or ["in_app"],
        meta=meta,
    )
    db.session.add(notif)
    db.session.flush()
    return notif


def list_notifications(business_id, pagination, user_id=None, is_read=None):
    stmt = select(Notification).where(Notification.business_id == business_id)
    if user_id is not None:
        stmt = stmt.where(
            or_(Notification.user_id == user_id, Notification.user_id.is_(None))
        )
    if is_read is not None:
        stmt = stmt.where(Notification.is_read.is_(is_read))
    stmt = stmt.order_by(Notification.created_at.desc())
    items, total, pages = paginate(stmt, pagination.page, pagination.per_page)
    return items, total, pages


def unread_count(business_id, user_id=None) -> int:
    stmt = select(func.count()).where(
        Notification.business_id == business_id, Notification.is_read.is_(False)
    )
    if user_id is not None:
        stmt = stmt.where(
            or_(Notification.user_id == user_id, Notification.user_id.is_(None))
        )
    return db.session.scalar(stmt) or 0


def mark_read(business_id, notification_ids, user_id=None):
    stmt = (
        update(Notification)
        .where(Notification.business_id == business_id, Notification.is_read.is_(False))
    )
    if user_id is not None:
        stmt = stmt.where(
            or_(Notification.user_id == user_id, Notification.user_id.is_(None))
        )
    if notification_ids:
        stmt = stmt.where(Notification.id.in_(notification_ids))
    stmt = stmt.values(is_read=True)
    db.session.execute(stmt)
    db.session.commit()


def mark_all_read(business_id, user_id=None):
    stmt = (
        update(Notification)
        .where(Notification.business_id == business_id, Notification.is_read.is_(False))
    )
    if user_id is not None:
        stmt = stmt.where(
            or_(Notification.user_id == user_id, Notification.user_id.is_(None))
        )
    db.session.execute(stmt)
    db.session.commit()


def generate_low_stock_notifications(business_id):
    """Generate (if absent) low-stock notifications for tracked items below minimum."""
    from .inventory_service import get_low_stock_alerts

    alerts = get_low_stock_alerts(business_id)
    count = 0
    for alert in alerts:
        existing = db.session.scalar(
            select(Notification).where(
                Notification.business_id == business_id,
                Notification.category == "inventory",
                Notification.reference_id == alert["item_id"],
                Notification.is_read.is_(False),
            )
        )
        if existing is None:
            create_notification(
                business_id=business_id,
                category="inventory",
                title="Low stock alert",
                message=f"'{alert['item_name']}' (#{alert['sku']}) is below minimum stock: {alert['quantity_on_hand']} left.",
                reference_type="item",
                reference_id=alert["item_id"],
                priority="high",
            )
            count += 1
    db.session.commit()
    return count


def business_owner_user_ids(business_id):
    """User ids of every active Business Owner membership in the tenant."""
    from ..models.iam import BusinessUser, Role

    rows = db.session.execute(
        select(BusinessUser.user_id)
        .join(Role, Role.id == BusinessUser.role_id)
        .where(
            BusinessUser.business_id == business_id,
            BusinessUser.is_active.is_(True),
            BusinessUser.user_id.is_not(None),
            Role.name == "Business Owner",
        )
    ).all()
    return [row[0] for row in rows]


def generate_expense_due_reminders(business_id, days_ahead=None):
    """Notify the business owner about recurring expenses due soon.

    An expense qualifies when it sits in a recurring category (rent, salaries,
    utilities, transport), still owes money, and falls due within the next
    ``days_ahead`` days. One reminder per expense per due date, so re-running
    this — on a schedule, or after every expense write — never duplicates.
    """
    from ..models import Expense
    from ..models.finance import ExpenseCategory
    from ..seed import RECURRING_EXPENSE_CATEGORIES

    days = EXPENSE_DUE_REMINDER_DAYS if days_ahead is None else days_ahead
    now = datetime.now(timezone.utc)
    today = now.date()
    horizon = today + timedelta(days=days)

    owner_ids = business_owner_user_ids(business_id)
    if not owner_ids:
        return 0

    stmt = (
        select(Expense, ExpenseCategory)
        .join(ExpenseCategory, ExpenseCategory.id == Expense.category_id)
        .where(
            Expense.business_id == business_id,
            Expense.is_active.is_(True),
            Expense.due_date.is_not(None),
            Expense.status.notin_(("cancelled", "paid")),
            ExpenseCategory.name.in_(RECURRING_EXPENSE_CATEGORIES),
        )
    )

    created = 0
    for expense, category in db.session.execute(stmt).all():
        due = expense.due_date.date() if expense.due_date else None
        if due is None or due > horizon:
            continue
        days_left = (due - today).days
        if days_left < 0:
            continue

        # Already reminded for this exact due date?
        already = db.session.scalar(
            select(Notification).where(
                Notification.business_id == business_id,
                Notification.reference_id == expense.id,
                Notification.reference_type == "expense",
                Notification.category == "expense",
                Notification.meta["tag"].as_string() == EXPENSE_DUE_REMINDER_TAG,
                Notification.meta["due_date"].as_string() == due.isoformat(),
            )
        )
        if already is not None:
            continue

        if days_left == 0:
            timing = f"due today ({due.isoformat()})"
        elif days_left == 1:
            timing = f"due tomorrow ({due.isoformat()})"
        else:
            timing = f"due in {days_left} days ({due.isoformat()})"
        for owner_id in owner_ids:
            create_notification(
                business_id=business_id,
                user_id=owner_id,
                category="expense",
                title=f"{category.name} payment {timing}",
                message=(
                    f"{expense.expense_number} — {expense.description} "
                    f"({float(expense.total_amount or 0):,.2f}) is {timing}."
                ),
                reference_type="expense",
                reference_id=expense.id,
                priority="high" if days_left <= 2 else "normal",
                meta={
                    "tag": EXPENSE_DUE_REMINDER_TAG,
                    "due_date": due.isoformat(),
                    "days_before": days,
                    "days_left": days_left,
                    "category_name": category.name,
                },
            )
            created += 1
    db.session.commit()
    return created


def get_notification_service(business_id):
    """Facade retained for consistency; operations are module-level functions."""
    return NotificationService(business_id)


class NotificationService:
    def __init__(self, business_id):
        self.business_id = business_id

    def list(self, pagination, user_id=None, is_read=None):
        return list_notifications(self.business_id, pagination, user_id=user_id, is_read=is_read)

    def unread(self, user_id=None):
        return unread_count(self.business_id, user_id=user_id)

    def mark_read(self, ids, user_id=None):
        return mark_read(self.business_id, ids, user_id=user_id)

    def mark_all_read(self, user_id=None):
        return mark_all_read(self.business_id, user_id=user_id)
