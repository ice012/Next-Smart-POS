"""Financial & operational reporting service.

Provides six report types with consistent filtering and CSV/PDF export:
  - profit_and_loss   (revenue, COGS, expenses, net)
  - sales              (totals, by customer, by item, daily)
  - expenses           (totals, by category, by supplier, daily)
  - receivables_aging  (customer outstanding buckets)
  - payables_aging     (supplier outstanding buckets)
  - cash_flow          (receipts vs payments out, daily)
  - inventory          (balances + value, low-stock flag)

Export helpers return raw bytes (CSV text or PDF) suitable for HTTP responses.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from ..extensions import db
from ..models import (
    Invoice, InvoiceItem, JournalLine, JournalEntry, FinancialAccount, Payment, Expense,
)
from .pdf_service import generate_report_pdf as _pdf


REPORT_DEFINITIONS = {
    "profit_and_loss": {"title": "Profit & Loss Statement"},
    "sales": {"title": "Sales Report"},
    "expenses": {"title": "Expenses Report"},
    "receivables_aging": {"title": "Receivables Aging"},
    "payables_aging": {"title": "Payables Aging"},
    "cash_flow": {"title": "Cash Flow Report"},
    "inventory": {"title": "Inventory Valuation"},
}


def _parse(value, default=None, end_of_day=False):
    if value is None:
        return default
    if isinstance(value, datetime):
        return value
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return default
    if end_of_day and len(str(value).strip()) == 10:
        # A date-only bound covers the whole day.
        parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
    return parsed


def _range(filters):
    start = _parse(filters.get("from_date"))
    end = _parse(filters.get("to_date"), end_of_day=True)
    if start is None and end is None:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=30)
    elif start is None:
        start = end - timedelta(days=30)
    elif end is None:
        end = start + timedelta(days=30)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return start, end


def _aging_buckets(outstanding_date=None):
    ref = _parse(outstanding_date) or datetime.now(timezone.utc)
    # bucket boundaries in days
    return ref, [(0, 30), (31, 60), (61, 90), (91, 999999)]


def _invoice_where(business_id, start, end, statuses=None):
    statuses = statuses or ["sent", "partially_paid", "paid", "overdue"]
    return (
        Invoice.business_id == business_id,
        Invoice.status.in_(statuses),
        Invoice.date >= start,
        Invoice.date <= end,
    )


def report_profit_and_loss(business_id, from_date=None, to_date=None):
    start, end = _range({"from_date": from_date, "to_date": to_date})
    revenue = db.session.scalar(
        select(func.coalesce(func.sum(Invoice.total_amount), 0)).where(*_invoice_where(business_id, start, end))
    ) or 0
    tax = db.session.scalar(
        select(func.coalesce(func.sum(Invoice.tax_amount), 0)).where(*_invoice_where(business_id, start, end))
    ) or 0
    cogs = db.session.scalar(
        select(func.coalesce(func.sum(InvoiceItem.cost_price * InvoiceItem.quantity), 0))
        .join(Invoice, InvoiceItem.invoice_id == Invoice.id)
        .where(*_invoice_where(business_id, start, end))
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
    gross = float(revenue) - float(cogs)
    net = gross - float(expenses)
    return {
        "title": "Profit & Loss",
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "summary": {
            "revenue": round(float(revenue), 2),
            "tax": round(float(tax), 2),
            "cogs": round(float(cogs), 2),
            "gross_profit": round(gross, 2),
            "expenses": round(float(expenses), 2),
            "net_profit": round(net, 2),
        },
        "lines": [
            {"label": "Revenue", "amount": round(float(revenue), 2)},
            {"label": "Cost of Goods Sold", "amount": round(float(cogs), 2)},
            {"label": "Gross Profit", "amount": round(gross, 2)},
            {"label": "Operating Expenses", "amount": round(float(expenses), 2)},
            {"label": "Net Profit", "amount": round(net, 2)},
        ],
    }


def report_sales(business_id, from_date=None, to_date=None):
    start, end = _range({"from_date": from_date, "to_date": to_date})
    q = (
        select(Invoice)
        .where(Invoice.business_id == business_id, Invoice.status.in_(["sent", "partially_paid", "paid", "overdue"]),
               Invoice.date >= start, Invoice.date <= end)
    )
    invoices = db.session.scalars(q).all()
    total = float(sum(i.total_amount or 0 for i in invoices))
    tax = float(sum(i.tax_amount or 0 for i in invoices))

    by_customer = {}
    for i in invoices:
        cust = i.customer_id
        name = i.customer.display_name if i.customer else "Unknown"
        by_customer.setdefault(cust, {"name": name, "amount": 0.0, "count": 0})
        by_customer[cust]["amount"] += float(i.total_amount or 0)
        by_customer[cust]["count"] += 1
    top_customers = sorted(by_customer.values(), key=lambda v: v["amount"], reverse=True)[:10]

    by_item = {}
    for i in invoices:
        for line in i.items:
            key = line.item_id or "unknown"
            name = line.item.name if line.item else str(line.description or "Item")
            by_item.setdefault(key, {"name": name, "quantity": 0.0, "amount": 0.0})
            by_item[key]["quantity"] += float(line.quantity or 0)
            by_item[key]["amount"] += float(line.line_total or 0)
    top_items = sorted(by_item.values(), key=lambda v: v["amount"], reverse=True)[:10]

    daily = {}
    for i in invoices:
        day = i.date.date().isoformat()
        daily[day] = daily.get(day, 0.0) + float(i.total_amount or 0)
    daily_series = [{"date": d, "sales": a} for d, a in sorted(daily.items())]

    return {
        "title": "Sales",
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "summary": {
            "total_sales": round(total, 2),
            "total_tax": round(tax, 2),
            "invoice_count": len(invoices),
            "average_invoice": round(total / len(invoices), 2) if invoices else 0,
        },
        "by_customer": top_customers,
        "by_item": top_items,
        "daily": daily_series,
        "invoices": [i.to_dict() for i in invoices],
    }


def report_expenses(business_id, from_date=None, to_date=None):
    start, end = _range({"from_date": from_date, "to_date": to_date})
    q = (
        select(Expense)
        .where(
            Expense.business_id == business_id,
            Expense.status.in_(["approved", "paid"]),
            Expense.date >= start,
            Expense.date <= end,
        )
    )
    expenses = db.session.scalars(q).all()
    total = float(sum(e.total_amount or 0 for e in expenses))

    by_category = {}
    for e in expenses:
        cat = e.category.name if e.category else "uncategorised"
        by_category[cat] = by_category.get(cat, 0.0) + float(e.total_amount or 0)
    category_list = [{"category": c, "amount": round(a, 2)} for c, a in
                     sorted(by_category.items(), key=lambda x: x[1], reverse=True)]

    by_supplier = {}
    for e in expenses:
        sup = e.supplier_id
        name = e.supplier.display_name if e.supplier else "Cash/Online"
        by_supplier.setdefault(sup, {"name": name, "amount": 0.0, "count": 0})
        by_supplier[sup]["amount"] += float(e.total_amount or 0)
        by_supplier[sup]["count"] += 1
    supplier_list = sorted(by_supplier.values(), key=lambda v: v["amount"], reverse=True)[:10]

    daily = {}
    for e in expenses:
        day = e.date.date().isoformat()
        daily[day] = daily.get(day, 0.0) + float(e.total_amount or 0)
    daily_series = [{"date": d, "expenses": a} for d, a in sorted(daily.items())]

    return {
        "title": "Expenses",
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "summary": {"total_expenses": round(total, 2), "expense_count": len(expenses)},
        "by_category": category_list,
        "by_supplier": supplier_list,
        "daily": daily_series,
        "expenses": [e.to_dict() for e in expenses],
    }


def _aging_for_invoices(business_id, ref_date):
    ref = _parse(ref_date) or datetime.now(timezone.utc)
    buckets = {"0-30": 0.0, "31-60": 0.0, "61-90": 0.0, "90+": 0.0}
    per_customer = {}
    q = select(Invoice).where(
        Invoice.business_id == business_id,
        Invoice.status.in_(["sent", "partially_paid", "overdue"]),
        Invoice.balance_due > 0,
    )
    invoices = db.session.scalars(q).all()
    for i in invoices:
        due_days = (ref.date() - i.due_date.date()).days if i.due_date else 0
        outstanding = float(i.balance_due or 0)
        key = "0-30" if due_days <= 30 else "31-60" if due_days <= 60 else "61-90" if due_days <= 90 else "90+"
        buckets[key] = buckets.get(key, 0.0) + outstanding
        cust = i.customer_id
        name = i.customer.display_name if i.customer else "Unknown"
        per_customer.setdefault(cust, {"name": name, "outstanding": 0.0, "invoices": 0, "oldest_due": i.due_date})
        per_customer[cust]["outstanding"] += outstanding
        per_customer[cust]["invoices"] += 1
        if i.due_date and (per_customer[cust]["oldest_due"] is None or i.due_date < per_customer[cust]["oldest_due"]):
            per_customer[cust]["oldest_due"] = i.due_date
    return {
        "title": "Receivables Aging",
        "as_of": ref.isoformat(),
        "summary": {**buckets, "total_receivables": round(sum(buckets.values()), 2)},
        "customers": [
            {"name": v["name"], "outstanding": round(v["outstanding"], 2), "invoices": v["invoices"]}
            for v in sorted(per_customer.values(), key=lambda x: x["outstanding"], reverse=True)
        ],
    }


def _aging_for_expenses(business_id, ref_date):
    ref = _parse(ref_date) or datetime.now(timezone.utc)
    buckets = {"0-30": 0.0, "31-60": 0.0, "61-90": 0.0, "90+": 0.0}
    per_supplier = {}
    q = select(Expense).where(
        Expense.business_id == business_id,
        Expense.status == "approved",
        Expense.total_amount > 0,
    )
    expenses = db.session.scalars(q).all()
    for e in expenses:
        # Treat unpaid portion; here we age the full approved amount by expense date.
        due_days = (ref.date() - e.date.date()).days
        amount = float(e.total_amount or 0)
        key = "0-30" if due_days <= 30 else "31-60" if due_days <= 60 else "61-90" if due_days <= 90 else "90+"
        buckets[key] = buckets.get(key, 0.0) + amount
        sup = e.supplier_id
        name = e.supplier.display_name if e.supplier else "Cash/Online"
        per_supplier.setdefault(sup, {"name": name, "outstanding": 0.0, "count": 0})
        per_supplier[sup]["outstanding"] += amount
        per_supplier[sup]["count"] += 1
    return {
        "title": "Payables Aging",
        "as_of": ref.isoformat(),
        "summary": {**buckets, "total_payables": round(sum(buckets.values()), 2)},
        "suppliers": [
            {"name": v["name"], "outstanding": round(v["outstanding"], 2), "count": v["count"]}
            for v in sorted(per_supplier.values(), key=lambda x: x["outstanding"], reverse=True)
        ],
    }


def report_cash_flow(business_id, from_date=None, to_date=None):
    start, end = _range({"from_date": from_date, "to_date": to_date})
    receipts = db.session.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.business_id == business_id,
            Payment.payment_type == "customer_receipt",
            Payment.date >= start,
            Payment.date <= end,
        )
    ) or 0
    disbursements = db.session.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.business_id == business_id,
            Payment.payment_type == "supplier_payment",
            Payment.date >= start,
            Payment.date <= end,
        )
    ) or 0
    expenses_paid = db.session.scalar(
        select(func.coalesce(func.sum(Expense.total_amount), 0)).where(
            Expense.business_id == business_id,
            Expense.status == "paid",
            Expense.date >= start,
            Expense.date <= end,
        )
    ) or 0
    receipts = float(receipts)
    disbursements = float(disbursements) + float(expenses_paid)
    net = receipts - disbursements
    daily = {}
    for p in db.session.scalars(select(Payment).where(
        Payment.business_id == business_id, Payment.date >= start, Payment.date <= end
    )).all():
        day = p.date.date().isoformat()
        direction = 1 if p.payment_type == "customer_receipt" else -1
        daily[day] = daily.get(day, 0.0) + float(p.amount or 0) * direction
    daily_series = [{"date": d, "net": round(a, 2)} for d, a in sorted(daily.items())]
    return {
        "title": "Cash Flow",
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "summary": {
            "total_receipts": round(receipts, 2),
            "total_disbursements": round(disbursements, 2),
            "net_cash_flow": round(net, 2),
        },
        "daily": daily_series,
    }


def report_inventory(business_id, from_date=None, to_date=None):
    from ..models import Item, InventoryBalance

    stmt = (
        select(InventoryBalance, Item)
        .join(Item, InventoryBalance.item_id == Item.id)
        .where(
            InventoryBalance.business_id == business_id,
            Item.is_inventory_tracked.is_(True),
        )
    )
    rows = db.session.execute(stmt).all()
    items = []
    total_value = 0.0
    low_stock = 0
    for bal, item in rows:
        value = float(bal.quantity_on_hand or 0) * float(item.cost_price or 0)
        total_value += value
        is_low = (bal.quantity_on_hand or 0) <= (item.minimum_stock or 0)
        if is_low:
            low_stock += 1
        items.append({
            "item_id": str(item.id),
            "name": item.name,
            "sku": item.sku,
            "quantity_on_hand": float(bal.quantity_on_hand or 0),
            "reserved": float(bal.reserved_quantity or 0),
            "available": float((bal.quantity_on_hand or 0) - (bal.reserved_quantity or 0)),
            "cost_price": float(item.cost_price or 0),
            "value": round(value, 2),
            "minimum_stock": float(item.minimum_stock or 0),
            "is_low_stock": is_low,
        })
    return {
        "title": "Inventory Valuation",
        "summary": {
            "total_items": len(items),
            "total_value": round(total_value, 2),
            "low_stock_count": low_stock,
        },
        "items": sorted(items, key=lambda x: x["value"], reverse=True),
    }


_DISPATCH = {
    "profit_and_loss": report_profit_and_loss,
    "pnl": report_profit_and_loss,
    "sales": report_sales,
    "expenses": report_expenses,
    "receivables_aging": _aging_for_invoices,
    "receivables": _aging_for_invoices,
    "payables_aging": _aging_for_expenses,
    "payables": _aging_for_expenses,
    "cash_flow": report_cash_flow,
    "inventory": report_inventory,
}


def generate_report(report_type, business_id, from_date=None, to_date=None, aging_date=None, **filters):
    fn = _DISPATCH.get(report_type)
    if fn is None:
        raise ValueError(f"Unknown report type: {report_type}")
    if report_type in ("receivables_aging", "receivables"):
        return fn(business_id, aging_date or filters.get("aging_date"))
    if report_type in ("payables_aging", "payables"):
        return fn(business_id, aging_date or filters.get("aging_date"))
    return fn(business_id, from_date or filters.get("from_date"), to_date or filters.get("to_date"))


def _rows_for_csv(result):
    """Flatten a report result dict into (headers, rows)."""
    title = result.get("title", "Report")
    rows = []
    if "summary" in result:
        rows.append({"section": "summary", **{k: v for k, v in result["summary"].items()}})
    for key in ("by_customer", "by_supplier", "by_category", "customers", "suppliers",
                "lines", "daily", "items", "expenses", "invoices"):
        if key in result:
            for item in result[key]:
                row = {"section": key}
                row.update(item)
                rows.append(row)
    return rows


def export_report_csv(report_type, business_id, from_date=None, to_date=None, **filters):
    import csv
    import io
    result = generate_report(report_type, business_id, from_date, to_date, **filters)
    rows = _rows_for_csv(result)
    output = io.StringIO()
    if not rows:
        fieldnames = ["section"]
    else:
        fieldnames = list(rows[0].keys())
        for r in rows[1:]:
            for k in r.keys():
                if k not in fieldnames:
                    fieldnames.append(k)
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: ("" if v is None else v) for k, v in r.items()})
    return output.getvalue().encode("utf-8")


def export_report_pdf(report_type, business_id, from_date=None, to_date=None, **filters):
    result = generate_report(report_type, business_id, from_date, to_date, **filters)
    rows = _rows_for_csv(result)
    title = REPORT_DEFINITIONS.get(report_type, {}).get("title") or result.get("title", "Report")
    return _pdf(title, rows)
