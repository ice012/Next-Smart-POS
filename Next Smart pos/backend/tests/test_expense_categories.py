"""Tests for the default expense categories provisioned with every business."""

from conftest import register_business

from app.seed import DEFAULT_EXPENSE_CATEGORIES, bootstrap_expense_categories


def test_new_business_starts_with_expense_categories(owner):
    response = owner["client"].get(
        "/api/finance/expense-categories", headers=owner["headers"]
    )
    assert response.status_code == 200, response.get_json()
    names = {c["name"] for c in response.get_json()["data"]["categories"]}
    assert names == {name for name, _ in DEFAULT_EXPENSE_CATEGORIES}


def test_seeded_categories_can_be_used_on_an_expense(owner):
    client = owner["client"]
    headers = owner["headers"]

    cats = client.get("/api/finance/expense-categories", headers=headers)
    category_id = cats.get_json()["data"]["categories"][0]["id"]

    created = client.post(
        "/api/finance/expenses",
        json={
            "category_id": category_id,
            "description": "Office rent",
            "amount": 250.0,
            "tax_amount": 0.0,
            "date": "2026-09-28",
            "payment_method": "bank",
            "status": "draft",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.get_json()
    expense = created.get_json()["data"]["expense"]
    assert expense["category_id"] == category_id
    assert expense["category_name"]


def test_bootstrap_is_idempotent(owner, app):
    from app.extensions import db
    from app.models import Business, ExpenseCategory
    from sqlalchemy import select

    with app.app_context():
        business = db.session.get(Business, owner["business_id"])
        before = len(
            db.session.scalars(
                select(ExpenseCategory).where(ExpenseCategory.business_id == business.id)
            ).all()
        )
        bootstrap_expense_categories(business)
        db.session.commit()
        after = len(
            db.session.scalars(
                select(ExpenseCategory).where(ExpenseCategory.business_id == business.id)
            ).all()
        )
    assert after == before


def test_bootstrap_keeps_business_specific_categories(app, owner):
    from sqlalchemy import select

    from app.extensions import db
    from app.models import Business, ExpenseCategory

    with app.app_context():
        business = db.session.get(Business, owner["business_id"])
        db.session.execute(
            ExpenseCategory.__table__.delete().where(
                ExpenseCategory.business_id == business.id
            )
        )
        db.session.add(
            ExpenseCategory(business_id=business.id, name="Petrol", description="Fuel only")
        )
        db.session.flush()
        bootstrap_expense_categories(business)
        db.session.commit()
        names = {
            c.name
            for c in db.session.scalars(
                select(ExpenseCategory).where(ExpenseCategory.business_id == business.id)
            ).all()
        }
    # The business's own category survives, and the missing defaults are added.
    assert "Petrol" in names
    assert "Rent" in names


def test_a_second_business_gets_its_own_categories(client):
    second = register_business(
        client, email="second@example.com", business_name="Second Tenant Ltd"
    )
    response = client.get(
        "/api/finance/expense-categories",
        headers={"Authorization": f"Bearer {second['access_token']}"},
    )
    assert response.status_code == 200
    names = {c["name"] for c in response.get_json()["data"]["categories"]}
    assert names == {name for name, _ in DEFAULT_EXPENSE_CATEGORIES}


def test_expense_categories_are_tenant_scoped(client, owner):
    second = register_business(
        client, email="other@example.com", business_name="Other Tenant Ltd"
    )
    cats = client.get(
        "/api/finance/expense-categories", headers=owner["headers"]
    ).get_json()["data"]["categories"]

    # A category from the first tenant must not be usable by the second.
    response = client.post(
        "/api/finance/expenses",
        json={
            "category_id": cats[0]["id"],
            "description": "Cross tenant",
            "amount": 10.0,
        },
        headers={"Authorization": f"Bearer {second['access_token']}"},
    )
    assert response.status_code == 404
