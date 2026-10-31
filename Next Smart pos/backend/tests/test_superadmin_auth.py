"""Superadmin sign-in: username, password, and the short-form PIN."""

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import SubscriptionPlan, User
from app.seed import create_superadmin_user

EMAIL = "nextadmin@example.com"
PASSWORD = "Admin@123"
USERNAME = "nextadmin"
PIN = "0005"


@pytest.fixture(autouse=True)
def _app_context(app):
    with app.app_context():
        yield


@pytest.fixture()
def superadmin(app):
    user = create_superadmin_user(
        EMAIL,
        PASSWORD,
        first_name="Next",
        last_name="Admin",
        username=USERNAME,
        pin=PIN,
    )
    db.session.commit()
    return user


def _login(client, **body):
    return client.post("/api/auth/superadmin/login", json=body)


# ---- password sign-in -------------------------------------------------------


def test_password_login_by_email(client, superadmin):
    response = _login(client, identifier=EMAIL, password=PASSWORD)
    assert response.status_code == 200, response.get_json()
    data = response.get_json()["data"]
    assert data["is_superadmin"] is True
    assert data["signed_in_with"] == "password"
    assert data["user"]["email"] == EMAIL


def test_password_login_by_username(client, superadmin):
    response = _login(client, identifier=USERNAME, password=PASSWORD)
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["data"]["user"]["username"] == USERNAME


# ---- PIN sign-in ------------------------------------------------------------


def test_pin_login_by_username(client, superadmin):
    response = _login(client, identifier=USERNAME, pin=PIN)
    assert response.status_code == 200, response.get_json()
    data = response.get_json()["data"]
    assert data["signed_in_with"] == "pin"
    assert data["superadmin_token"]


def test_pin_login_by_email(client, superadmin):
    response = _login(client, identifier=EMAIL, pin=PIN)
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["data"]["signed_in_with"] == "pin"


def test_pin_login_rejects_a_wrong_pin(client, superadmin):
    assert _login(client, identifier=USERNAME, pin="0006").status_code == 401


def test_a_non_numeric_pin_is_rejected_before_any_lookup(client, superadmin):
    assert _login(client, identifier=USERNAME, pin=PASSWORD).status_code == 422
    assert _login(client, identifier=USERNAME, pin=PIN + PIN).status_code == 422


def test_password_login_rejects_the_pin_as_a_password(client, superadmin):
    assert _login(client, identifier=USERNAME, password=PIN).status_code == 401


def test_pin_must_be_four_to_six_digits(client, superadmin):
    for bad in ("005", "1234567", "abcd"):
        assert _login(client, identifier=USERNAME, pin=bad).status_code == 422


def test_login_requires_some_credential(client, superadmin):
    assert _login(client, identifier=USERNAME).status_code == 422
    assert _login(client, identifier="", pin=PIN).status_code == 422


# ---- PIN is optional and never leaks ---------------------------------------


def test_superadmin_without_a_pin_still_signs_in_with_a_password(client, app):
    create_superadmin_user("nopass@example.com", PASSWORD)
    db.session.commit()
    response = _login(client, identifier="nopass@example.com", password=PASSWORD)
    assert response.status_code == 200, response.get_json()
    assert _login(client, identifier="nopass@example.com", pin=PIN).status_code == 401


def test_login_response_never_returns_the_pin(client, superadmin):
    response = _login(client, identifier=USERNAME, pin=PIN)
    user = response.get_json()["data"]["user"]
    assert user["has_pin"] is True
    assert "pin_hash" not in user
    assert PIN not in response.get_data(as_text=True)


# ---- PIN sign-in is superadmin only ----------------------------------------


def test_pin_login_is_refused_for_a_tenant_user(client, app):
    from conftest import register_business

    register_business(client, "tenant@example.com", "Tenant Co")
    response = _login(client, identifier="tenant@example.com", pin=PIN)
    assert response.status_code == 401


def test_pin_login_is_refused_for_a_deactivated_superadmin(client, superadmin):
    superadmin.is_active = False
    db.session.commit()
    assert _login(client, identifier=USERNAME, pin=PIN).status_code == 401


# ---- seeding is idempotent and updates in place -----------------------------


def test_reseeding_updates_the_password_username_and_pin(client, superadmin):
    same = create_superadmin_user(
        EMAIL, "R0tatedPass!2", username=USERNAME, pin="1234"
    )
    db.session.commit()
    assert same.id == superadmin.id
    assert _login(client, identifier=USERNAME, password="R0tatedPass!2").status_code == 200
    assert _login(client, identifier=USERNAME, password=PASSWORD).status_code == 401
    assert _login(client, identifier=USERNAME, pin="1234").status_code == 200
    assert _login(client, identifier=USERNAME, pin=PIN).status_code == 401


def test_reseeding_the_password_alone_leaves_the_pin_intact(client, superadmin):
    create_superadmin_user(EMAIL, "R0tatedPass!2")
    db.session.commit()
    assert _login(client, identifier=USERNAME, pin=PIN).status_code == 200


def test_reseeding_refuses_to_steal_another_users_username(client, superadmin):
    from app.exceptions import ConflictError

    create_superadmin_user("other@example.com", PASSWORD, username="someone-else")
    db.session.commit()

    with pytest.raises(ConflictError):
        create_superadmin_user(EMAIL, PASSWORD, username="someone-else")
    db.session.rollback()

    holder = db.session.scalar(select(User).where(User.username == "someone-else"))
    assert holder.email == "other@example.com"
    assert superadmin.username == USERNAME


# ---- default plan catalogue -------------------------------------------------


def test_starter_is_the_seeded_default_plan(app):
    plan = db.session.scalar(
        select(SubscriptionPlan).where(SubscriptionPlan.is_default.is_(True))
    )
    assert plan is not None
    assert plan.code == "starter"


def test_no_free_plan_is_seeded(app):
    assert db.session.scalar(
        select(SubscriptionPlan).where(SubscriptionPlan.code == "free")
    ) is None


# ---- regression: paginate must not collapse joined rows ---------------------


def test_paginate_keeps_all_entities_of_a_joined_row(app):
    from sqlalchemy import select as sa_select

    from app.models import Branch, Business
    from app.utils.pagination import paginate

    business = Business(
        name="Pagination Co",
        slug="pagination-co",
        default_country="KE",
        default_currency="KES",
        default_timezone="Africa/Nairobi",
    )
    db.session.add(business)
    db.session.flush()
    db.session.add_all(
        [
            Branch(business_id=business.id, name="Alpha", code="ALPHA", is_head=True),
            Branch(business_id=business.id, name="Beta", code="BETA"),
        ]
    )
    db.session.commit()

    joined = sa_select(Branch, Business).join(
        Business, Branch.business_id == Business.id
    )
    rows, total, _pages = paginate(joined, 1, 10)
    # A multi-entity select must come back with every column intact. The old
    # heuristic saw an "entity" on the first column, called .scalars(), and
    # handed back bare Branch objects that crashed the caller on unpacking.
    assert total == 2
    assert all(len(row) == 2 for row in rows)
    assert all(isinstance(row[0], Branch) and isinstance(row[1], Business) for row in rows)

    single = sa_select(Branch)
    items, total, _pages = paginate(single, 1, 10)
    assert total == 2
    assert all(isinstance(item, Branch) for item in items)


def test_listing_team_members_returns_every_joined_column(client, owner):
    """The endpoint that exposed the paginate bug: it unpacks three entities."""
    response = client.get("/api/iam/users", headers=owner["headers"])
    assert response.status_code == 200, response.get_json()
    users = response.get_json()["data"]["users"]
    assert len(users) == 1
    assert users[0]["user"]["email"] == "owner@example.com"
    assert users[0]["role"] is not None
