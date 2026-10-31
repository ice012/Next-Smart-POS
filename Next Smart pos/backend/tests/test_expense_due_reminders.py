"""Tests for the 7-day owner reminder on recurring expense due dates."""

from datetime import datetime, timedelta, timezone

from conftest import register_business

from app.services.notification_service import (
    EXPENSE_DUE_REMINDER_DAYS,
    generate_expense_due_reminders,
)


def _date_in(days):
    return (datetime.now(timezone.utc) + timedelta(days=days)).date().isoformat()


def _category_id(client, headers, name):
    categories = client.get(
        "/api/finance/expense-categories", headers=headers
    ).get_json()["data"]["categories"]
    return next(c["id"] for c in categories if c["name"] == name)


def _make_expense(client, headers, category_name, due_in_days, status="draft", amount=100.0):
    return client.post(
        "/api/finance/expenses",
        headers=headers,
        json={
            "category_id": _category_id(client, headers, category_name),
            "description": f"{category_name} bill",
            "amount": amount,
            "date": _date_in(0),
            "due_date": _date_in(due_in_days),
            "status": status,
        },
    )


def _notifications(client, headers):
    response = client.get("/api/notifications", headers=headers)
    assert response.status_code == 200, response.get_json()
    return response.get_json()["data"]["notifications"]


def test_reminder_is_created_seven_days_before_due_date(client, owner):
    client_post = _make_expense(client, owner["headers"], "Rent", due_in_days=7)
    assert client_post.status_code == 201, client_post.get_json()

    reminders = [
        n for n in _notifications(client, owner["headers"])
        if (n.get("metadata") or {}).get("tag") == "expense_due_reminder"
    ]
    assert len(reminders) == 1
    assert reminders[0]["category"] == "expense"
    assert reminders[0]["reference_type"] == "expense"
    assert reminders[0]["metadata"]["days_left"] == 7
    assert reminders[0]["metadata"]["category_name"] == "Rent"
    assert "Rent" in reminders[0]["title"]


def test_reminder_covers_each_recurring_category(client, owner):
    headers = owner["headers"]
    for name in ("Rent", "Salaries and Wages", "Utilities", "Transport"):
        assert _make_expense(client, headers, name, due_in_days=3).status_code == 201

    reminders = [
        n for n in _notifications(client, headers)
        if (n.get("metadata") or {}).get("tag") == "expense_due_reminder"
    ]
    assert {n["metadata"]["category_name"] for n in reminders} == {
        "Rent", "Salaries and Wages", "Utilities", "Transport",
    }


def test_no_reminder_for_non_recurring_categories(client, owner):
    headers = owner["headers"]
    for name in ("Equipment", "Marketing", "Other"):
        assert _make_expense(client, headers, name, due_in_days=3).status_code == 201

    reminders = [
        n for n in _notifications(client, headers)
        if (n.get("metadata") or {}).get("tag") == "expense_due_reminder"
    ]
    assert reminders == []


def test_no_reminder_beyond_the_window(client, owner):
    assert _make_expense(client, owner["headers"], "Rent", due_in_days=8).status_code == 201
    reminders = [
        n for n in _notifications(client, owner["headers"])
        if (n.get("metadata") or {}).get("tag") == "expense_due_reminder"
    ]
    assert reminders == []


def test_no_reminder_without_a_due_date(client, owner):
    response = client.post(
        "/api/finance/expenses",
        headers=owner["headers"],
        json={
            "category_id": _category_id(client, owner["headers"], "Rent"),
            "description": "Rent paid upfront",
            "amount": 100.0,
            "date": _date_in(0),
            "status": "draft",
        },
    )
    assert response.status_code == 201, response.get_json()
    reminders = [
        n for n in _notifications(client, owner["headers"])
        if (n.get("metadata") or {}).get("tag") == "expense_due_reminder"
    ]
    assert reminders == []


def test_paid_and_cancelled_expenses_are_not_reminded(client, owner):
    headers = owner["headers"]
    assert _make_expense(client, headers, "Rent", due_in_days=2, status="paid").status_code == 201
    assert _make_expense(client, headers, "Rent", due_in_days=2, status="cancelled").status_code == 201
    reminders = [
        n for n in _notifications(client, headers)
        if (n.get("metadata") or {}).get("tag") == "expense_due_reminder"
    ]
    assert reminders == []


def test_overdue_expenses_are_not_reminded(client, owner):
    assert _make_expense(client, owner["headers"], "Rent", due_in_days=-3).status_code == 201
    reminders = [
        n for n in _notifications(client, owner["headers"])
        if (n.get("metadata") or {}).get("tag") == "expense_due_reminder"
    ]
    assert reminders == []


def test_repeated_runs_do_not_duplicate_reminders(client, owner, app):
    from app.extensions import db as _db

    assert _make_expense(client, owner["headers"], "Rent", due_in_days=4).status_code == 201
    with app.app_context():
        assert generate_expense_due_reminders(owner["business_id"]) == 0
    reminders = [
        n for n in _notifications(client, owner["headers"])
        if (n.get("metadata") or {}).get("tag") == "expense_due_reminder"
    ]
    assert len(reminders) == 1


def test_reminders_are_addressed_to_the_business_owner(client, owner, app):
    from app.extensions import db as _db
    from app.models import User

    assert _make_expense(client, owner["headers"], "Rent", due_in_days=2).status_code == 201
    with app.app_context():
        owner_user = _db.session.get(User, owner["user_id"])
        reminders = [
            n for n in _notifications(client, owner["headers"])
            if (n.get("metadata") or {}).get("tag") == "expense_due_reminder"
        ]
        assert reminders[0]["user_id"] == str(owner_user.id)


def test_reminders_do_not_leak_across_tenants(client, owner):
    second = register_business(client, email="owner2@example.com", business_name="Tenant Two Ltd")
    second_headers = {"Authorization": f"Bearer {second['access_token']}"}

    _make_expense(client, owner["headers"], "Rent", due_in_days=2)
    reminders = [
        n for n in _notifications(client, second_headers)
        if (n.get("metadata") or {}).get("tag") == "expense_due_reminder"
    ]
    assert reminders == []


def test_due_date_endpoint_reports_generated_count(client, owner):
    assert _make_expense(client, owner["headers"], "Rent", due_in_days=5).status_code == 201
    response = client.post(
        "/api/notifications/expense-due-check", headers=owner["headers"], json={}
    )
    assert response.status_code == 200, response.get_json()
    # The expense write already raised the reminder, so the check adds none.
    assert response.get_json()["data"]["generated"] == 0


def test_reminder_message_carries_the_amount(client, owner):
    assert _make_expense(
        client, owner["headers"], "Salaries and Wages", due_in_days=1, amount=4500
    ).status_code == 201
    reminders = [
        n for n in _notifications(client, owner["headers"])
        if (n.get("metadata") or {}).get("tag") == "expense_due_reminder"
    ]
    assert "4,500.00" in reminders[0]["message"]
    assert "due tomorrow" in reminders[0]["title"]


def test_setting_a_due_date_on_update_triggers_the_reminder(client, owner, app):
    from app.extensions import db as _db
    from app.services.notification_service import generate_expense_due_reminders

    response = _make_expense(client, owner["headers"], "Rent", due_in_days=30)
    assert response.status_code == 201
    expense_id = response.get_json()["data"]["expense"]["id"]
    assert not [
        n for n in _notifications(client, owner["headers"])
        if (n.get("metadata") or {}).get("tag") == "expense_due_reminder"
    ]

    moved = client.patch(
        f"/api/finance/expenses/{expense_id}",
        headers=owner["headers"],
        json={"due_date": _date_in(6)},
    )
    assert moved.status_code == 200, moved.get_json()
    reminders = [
        n for n in _notifications(client, owner["headers"])
        if (n.get("metadata") or {}).get("tag") == "expense_due_reminder"
    ]
    assert len(reminders) == 1
    assert reminders[0]["reference_id"] == expense_id
