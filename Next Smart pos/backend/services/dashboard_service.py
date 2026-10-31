"""Business dashboard service: live KPI aggregations and chart series.

All queries are tenant-scoped to ``business_id`` and use server-side SQL
aggregations for performance. Numbers are returned as floats for JSON.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from ..extensions import db
from ..models import (
    Invoice, Payment, Expense, Customer, Supplier, Item, InventoryBalance,
    Warehouse, JournalEntry, JournalLine, FinancialAccount,
)


def _parse_date(value, end_of_day=False):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if end_of_day and len(str(value).strip()) == 10:
        # A date-only bound covers the whole day, so ``to_date`` must not
        # exclude records timestamped later that same day.
        parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
    return parsed


def _default_range():
    now = datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start, now


def _day_series(business_id, start, end, label, model_date_col, amount_col, where_extra=None):
    """Return per-day totals for a date range (SQLite + PostgreSQL portable).

    Each entry is ``{"date": "YYYY-MM-DD", "label": "DD MMM", "value": total}``.
    ``label`` is the series name and is used for error context only.
    """
    bucket = {
        str(start.date() + timedelta(days=i)): 0.0
        for i in range((end.date() - start.date()).days + 1)
    }

    model = model_date_col.class_
    stmt = select(model_date_col, amount_col).where(
        model.business_id == business_id,
        model_date_col >= start,
        model_date_col <= end,
    )
    if where_extra is not None:
        stmt = stmt.where(where_extra)
    rows = db.session.execute(stmt).all()
    for row in rows:
        d = row[0]
        if d is None:
            continue
        key = d.date().isoformat() if hasattr(d, "date") else str(d)[:10]
        if key in bucket:
            bucket[key] = bucket.get(key, 0.0) + float(row[1] or 0)
    return [
        {
            "date": day,
            "label": datetime.fromisoformat(day).strftime("%d %b"),
            "value": round(bucket.get(day, 0.0), 2),
        }
        for day in sorted(bucket.keys())
    ]


def get_business_dashboard(business_id, from_date=None, to_date=None) -> dict:
    start = _parse_date(from_date)
    end = _parse_date(to_date, end_of_day=True)
    if start is None and end is None:
        start, end = _default_range()
    elif start is None:
        start, end = end - timedelta(days=29), end
    elif end is None:
        end = start + timedelta(days=30)

    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    # ---- Totals ----
    inv_sum = db.session.scalar(
        select(func.coalesce(func.sum(Invoice.total_amount), 0))
        .where(
            Invoice.business_id == business_id,
            Invoice.status.in_(["sent", "partially_paid", "paid", "overdue"]),
            Invoice.date >= start,
            Invoice.date <= end,
        )
    ) or 0
    received = db.session.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0))
        .where(
            Payment.business_id == business_id,
            Payment.payment_type == "customer_receipt",
            Payment.date >= start,
            Payment.date <= end,
        )
    ) or 0
    expenses = db.session.scalar(
        select(func.coalesce(func.sum(Expense.total_amount), 0))
        .where(
            Expense.business_id == business_id,
            Expense.status.in_(["approved", "paid"]),
            Expense.date >= start,
            Expense.date <= end,
        )
    ) or 0
    receivables = db.session.scalar(
        select(func.coalesce(func.sum(Invoice.balance_due), 0))
        .where(
            Invoice.business_id == business_id,
            Invoice.status.in_(["sent", "partially_paid", "overdue"]),
        )
    ) or 0
    payables = db.session.scalar(
        select(func.coalesce(func.sum(Expense.total_amount), 0))
        .where(Expense.business_id == business_id, Expense.status == "approved")
    ) or 0

    # Inventory value = sum(cost_price * quantity_on_hand)
    inv_value = db.session.scalar(
        select(func.coalesce(
            func.sum(InventoryBalance.quantity_on_hand * Item.cost_price), 0
        ))
        .join(Item, InventoryBalance.item_id == Item.id)
        .where(InventoryBalance.business_id == business_id, Item.is_inventory_tracked.is_(True))
    ) or 0

    # Counts
    customer_count = db.session.scalar(select(func.count()).where(Customer.business_id == business_id)) or 0
    supplier_count = db.session.scalar(select(func.count()).where(Supplier.business_id == business_id)) or 0
    item_count = db.session.scalar(select(func.count()).where(Item.business_id == business_id, Item.is_active.is_(True))) or 0
    invoice_count = db.session.scalar(select(func.count()).where(Invoice.business_id == business_id)) or 0
    overdraft_count = db.session.scalar(
        select(func.count()).where(
            Invoice.business_id == business_id,
            Invoice.status.in_(["sent", "partially_paid", "overdue"]),
            Invoice.balance_due > 0,
        )
    ) or 0

    # Invoice status breakdown
    status_breakdown = {}
    for status_label in ["draft", "sent", "partially_paid", "paid", "overdue", "cancelled"]:
        status_breakdown[status_label] = (
            db.session.scalar(
                select(func.count()).where(
                    Invoice.business_id == business_id, Invoice.status == status_label
                )
            ) or 0
        )

    # Chart series
    sales_series = _day_series(
        business_id, start, end, "sales", Invoice.date, Invoice.total_amount
    )
    expense_series = _day_series(
        business_id, start, end, "expenses", Expense.date, Expense.total_amount
    )
    collection_series = _day_series(
        business_id, start, end, "collections", Payment.date, Payment.amount,
        where_extra=(Payment.payment_type == "customer_receipt"),
    )

    # Top 5 customers by outstanding receivables
    top_rows = db.session.execute(
        select(Customer.display_name, func.coalesce(func.sum(Invoice.balance_due), 0).label("bal"))
        .outerjoin(Invoice, Invoice.customer_id == Customer.id)
        .where(
            Invoice.business_id == business_id,
            Invoice.status.in_(["sent", "partially_paid", "overdue"]),
        )
        .group_by(Customer.id)
        .having(func.coalesce(func.sum(Invoice.balance_due), 0) > 0)
        .order_by(func.sum(Invoice.balance_due).desc())
        .limit(5)
    ).all()

    # Recent invoices + payments
    recent_invoices = db.session.scalars(
        select(Invoice).where(Invoice.business_id == business_id)
        .order_by(Invoice.date.desc())
        .limit(5)
    ).all()
    recent_payments = db.session.scalars(
        select(Payment).where(Payment.business_id == business_id)
        .order_by(Payment.date.desc())
        .limit(5)
    ).all()

    avg_invoice = float(inv_sum / invoice_count) if invoice_count else 0.0
    collection_rate = float((received / inv_sum) * 100) if inv_sum else 0.0

    return {
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "kpi": {
            "total_sales": float(inv_sum),
            "total_received": float(received),
            "total_expenses": float(expenses),
            "receivables": float(receivables),
            "payables": float(payables),
            "inventory_value": float(inv_value),
            "low_stock_count": len(_low_stock(business_id)),
            "overdue_invoices_count": overdraft_count,
            "average_invoice_value": round(avg_invoice, 2),
            "collection_rate_percent": round(collection_rate, 2),
        },
        "counts": {
            "customers": customer_count,
            "suppliers": supplier_count,
            "items": item_count,
            "invoices": invoice_count,
        },
        "invoice_status_breakdown": status_breakdown,
        "charts": {
            "daily_sales": sales_series,
            "daily_expenses": expense_series,
            "daily_collections": collection_series,
        },
        "top_customers": [
            {"name": r.display_name, "outstanding": float(r.bal)} for r in top_rows
        ],
        "recent_invoices": [inv.to_dict() for inv in recent_invoices],
        "recent_payments": [p.to_dict() for p in recent_payments],
    }


def _low_stock(business_id):
    stmt = (
        select(InventoryBalance, Item)
        .join(Item, InventoryBalance.item_id == Item.id)
        .where(
            InventoryBalance.business_id == business_id,
            Item.is_inventory_tracked.is_(True),
            InventoryBalance.quantity_on_hand <= Item.minimum_stock,
        )
    )
    return db.session.execute(stmt).all()
