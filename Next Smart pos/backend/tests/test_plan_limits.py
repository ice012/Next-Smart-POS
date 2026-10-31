"""Subscription plan cap enforcement: users, branches, invoices and modules."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import (
    Role,
    SubscriptionPlan,
    SystemModule,
)
from app.services.subscription_service import (
    accessible_modules,
    plan_limit_status,
    plan_usage,
    validate_plan_modules,
)

from conftest import register_business

@pytest.fixture(autouse=True)
def _app_context(app):
    """The app fixture's context closes before it yields, so push our own."""
    with app.app_context():
        yield


SA_TOKEN = "test-superadmin-token"
SA_HEADERS = {"X-SuperAdmin-Token": SA_TOKEN}


def _default_plan() -> SubscriptionPlan:
    plan = db.session.scalar(
        select(SubscriptionPlan).where(SubscriptionPlan.is_default.is_(True))
    )
    assert plan is not None, "seed must provide a default plan"
    return plan


def set_limits(**limits) -> SubscriptionPlan:
    """Tighten the plan the test tenant sits on.

    ``None`` is the explicit "unlimited" value, so a test can put a cap back
    the way it found it by passing the field as None.
    """
    plan = _default_plan()
    for field, value in limits.items():
        setattr(plan, field, value)
    db.session.commit()
    return plan


def owner_role_id(business_id):
    return db.session.scalar(
        select(Role.id).where(Role.business_id == business_id, Role.name == "Employee")
    )


def invite(client, headers, business_id, email, role_id):
    return client.post(
        "/api/iam/users",
        headers=headers,
        json={
            "email": email,
            "first_name": "Invited",
            "last_name": "Person",
            "role_id": str(role_id),
        },
    )


def create_branch(client, headers, name, code):
    return client.post(
        "/api/business/branches",
        headers=headers,
        json={"name": name, "code": code},
    )


def create_invoice(client, headers):
    return client.post(
        "/api/finance/invoices",
        headers=headers,
        json={
            "due_date": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
            "items": [
                {
                    "description": "Consulting",
                    "quantity": 1,
                    "unit_price": 100,
                    "tax_rate": 0,
                }
            ],
        },
    )


def create_sale(client, headers, item_id):
    return client.post(
        "/api/sales",
        headers=headers,
        json={
            "sale_type": "cash",
            "items": [{"item_id": item_id, "quantity": 1}],
        },
    )


def create_item(client, headers, name="Widget"):
    response = client.post(
        "/api/items",
        headers=headers,
        json={
            "type": "product",
            "name": name,
            "cost_price": 20,
            "selling_price": 100,
        },
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()["data"]["item"]


# ---- users ----------------------------------------------------------------


def test_user_cap_blocks_the_invite_that_would_exceed_it(client, owner):
    set_limits(max_users=1)
    role_id = owner_role_id(owner["business_id"])

    response = invite(
        client, owner["headers"], owner["business_id"], "extra@example.com", role_id
    )

    assert response.status_code == 403, response.get_json()
    body = response.get_json()
    assert body["error"] == "PlanLimitError"
    assert body["cap"] == 1
    assert body["used"] == 1
    assert body["limit"] == "users"


def test_user_cap_permits_an_invite_that_stays_under_it(client, owner):
    set_limits(max_users=2)
    role_id = owner_role_id(owner["business_id"])

    response = invite(
        client, owner["headers"], owner["business_id"], "extra@example.com", role_id
    )

    assert response.status_code == 201, response.get_json()
    assert plan_usage(owner["business_id"])["users"] == 2


def test_null_cap_means_unlimited(client, owner):
    set_limits(max_users=None)
    role_id = owner_role_id(owner["business_id"])

    for index in range(4):
        response = invite(
            client,
            owner["headers"],
            owner["business_id"],
            f"many{index}@example.com",
            role_id,
        )
        assert response.status_code == 201, response.get_json()

    assert plan_usage(owner["business_id"])["users"] == 5


def test_reinviting_an_existing_member_does_not_consume_another_seat(client, owner):
    set_limits(max_users=1)
    role_id = owner_role_id(owner["business_id"])
    payload = {
        "email": "repeat@example.com",
        "first_name": "Repeat",
        "last_name": "Caller",
        "role_id": str(role_id),
    }

    first = client.post("/api/iam/users", headers=owner["headers"], json=payload)
    assert first.status_code == 403, first.get_json()

    set_limits(max_users=2)
    assert (
        client.post("/api/iam/users", headers=owner["headers"], json=payload).status_code
        == 201
    )
    again = client.post("/api/iam/users", headers=owner["headers"], json=payload)
    assert again.status_code == 201, again.get_json()
    assert plan_usage(owner["business_id"])["users"] == 2


# ---- branches -------------------------------------------------------------


def test_branch_cap_is_enforced(client, owner):
    # Registration already seeds a head office branch, so a cap of 1 is spent.
    assert plan_usage(owner["business_id"])["branches"] == 1
    set_limits(max_branches=1)
    assert create_branch(client, owner["headers"], "Depot", "DEPOT").status_code == 403

    set_limits(max_branches=2)
    first = create_branch(client, owner["headers"], "Depot", "DEPOT")
    assert first.status_code == 201, first.get_json()

    second = create_branch(client, owner["headers"], "Annex", "ANNEX")
    assert second.status_code == 403, second.get_json()
    body = second.get_json()
    assert body["limit"] == "branches"
    assert body["cap"] == 2
    assert body["used"] == 2


# ---- invoices -------------------------------------------------------------


def test_invoice_cap_is_enforced(client, owner):
    set_limits(max_invoices=1)

    first = create_invoice(client, owner["headers"])
    assert first.status_code == 201, first.get_json()

    second = create_invoice(client, owner["headers"])
    assert second.status_code == 403, second.get_json()
    assert second.get_json()["limit"] == "invoices"


def test_sales_draw_on_the_same_invoice_allowance(client, owner):
    item = create_item(client, owner["headers"])
    set_limits(max_invoices=1)

    assert create_invoice(client, owner["headers"]).status_code == 201
    assert create_sale(client, owner["headers"], item["id"]).status_code == 403


# ---- status reporting -----------------------------------------------------


def test_plan_limit_status_reports_usage_caps_and_at_limit(client, owner):
    set_limits(max_users=1, max_branches=None, max_invoices=None)

    status = plan_limit_status(owner["business_id"])

    assert status["limits"] == {"users": 1, "branches": None, "invoices": None}
    assert status["usage"]["users"] == 1
    assert status["at_limit"] == {"users": True, "branches": False, "invoices": False}


def test_business_detail_exposes_limits(client, owner):
    set_limits(max_users=7)

    response = client.get(
        f"/api/superadmin/businesses/{owner['business_id']}", headers=SA_HEADERS
    )

    assert response.status_code == 200, response.get_json()
    limits = response.get_json()["data"]["limits"]
    assert limits["limits"]["users"] == 7
    assert limits["usage"]["users"] == 1


def test_plan_usage_endpoint_rolls_up_every_plan(client, owner):
    set_limits(max_users=1)

    response = client.get("/api/superadmin/plans/usage", headers=SA_HEADERS)

    assert response.status_code == 200, response.get_json()
    usage = response.get_json()["data"]["usage"]
    by_id = {row["plan_id"]: row for row in usage}
    default = by_id[str(_default_plan().id)]
    assert default["businesses"] == 1
    assert default["usage"]["users"] == 1
    assert default["at_limit"] == 1


# ---- assigning plans ------------------------------------------------------


def _plan_payload(**overrides):
    payload = {
        "name": "Capped",
        "code": "capped",
        "tier": "starter",
        "price_cents": 1000,
        "currency": "KES",
        "billing_cycle": "monthly",
        "max_users": 1,
        "max_branches": 1,
        "max_invoices": 1,
        "storage_mb": 100,
        "modules": ["core", "customers"],
    }
    payload.update(overrides)
    return payload


def test_plan_change_is_refused_when_the_tenant_already_exceeds_the_new_cap(client, owner):
    # The seeded default plan caps branches, so lift the cap to build up the
    # usage this test is about. The plan under test is created below, not the
    # one the tenant is currently on.
    set_limits(max_branches=None, max_invoices=None)
    # Give the tenant two branches and two invoices so it is over a 1/1/1 plan.
    assert create_branch(client, owner["headers"], "Main", "MAIN").status_code == 201
    assert create_branch(client, owner["headers"], "Depot", "DEPOT").status_code == 201
    assert create_invoice(client, owner["headers"]).status_code == 201
    assert create_invoice(client, owner["headers"]).status_code == 201

    created = client.post(
        "/api/superadmin/plans", headers=SA_HEADERS, json=_plan_payload()
    )
    assert created.status_code == 201, created.get_json()
    plan = created.get_json()["data"]["plan"]

    response = client.post(
        f"/api/superadmin/businesses/{owner['business_id']}/plan",
        headers=SA_HEADERS,
        json={"plan_id": plan["id"]},
    )

    assert response.status_code == 409, response.get_json()
    body = response.get_json()
    assert "branches" in body["message"] and "invoices" in body["message"]
    assert _current_plan_id(owner["business_id"]) != plan["id"]


def _current_plan_id(business_id):
    from app.models import Subscription

    return str(
        db.session.scalar(
            select(Subscription.plan_id).where(Subscription.business_id == business_id)
        )
    )


def test_plan_change_succeeds_when_the_tenant_fits(client, owner):
    created = client.post(
        "/api/superadmin/plans",
        headers=SA_HEADERS,
        json=_plan_payload(max_users=5, max_branches=5, max_invoices=50),
    )
    plan = created.get_json()["data"]["plan"]

    response = client.post(
        f"/api/superadmin/businesses/{owner['business_id']}/plan",
        headers=SA_HEADERS,
        json={"plan_id": plan["id"]},
    )

    assert response.status_code == 200, response.get_json()
    assert response.get_json()["data"]["business"]["subscription_tier"] == "starter"


# ---- modules --------------------------------------------------------------


def test_plan_creation_rejects_an_unknown_module_code(client):
    response = client.post(
        "/api/superadmin/plans",
        headers=SA_HEADERS,
        json=_plan_payload(modules=["core", "not_a_module"]),
    )

    assert response.status_code == 422, response.get_json()
    assert "not_a_module" in response.get_json()["message"]


def test_plan_update_rejects_an_unknown_module_code(client):
    created = client.post(
        "/api/superadmin/plans", headers=SA_HEADERS, json=_plan_payload()
    )
    plan_id = created.get_json()["data"]["plan"]["id"]

    response = client.patch(
        f"/api/superadmin/plans/{plan_id}",
        headers=SA_HEADERS,
        json={"modules": ["core", "still_not_real"]},
    )

    assert response.status_code == 422, response.get_json()
    assert "still_not_real" in response.get_json()["message"]


def test_validate_plan_modules_accepts_catalogue_codes():
    assert validate_plan_modules(["core", "sales"]) == ["core", "sales"]
    with pytest.raises(Exception):
        validate_plan_modules(["nope"])


def test_module_min_tier_blocks_a_plan_below_the_floor(client, owner):
    """A module listed on a plan is still refused when its floor is higher."""
    module = db.session.scalar(
        select(SystemModule).where(SystemModule.code == "customers")
    )
    original = module.min_plan_tier
    module.min_plan_tier = "enterprise"
    db.session.commit()
    try:
        response = client.get("/api/customers", headers=owner["headers"])
        assert response.status_code == 403, response.get_json()
        assert response.get_json()["missing"] == ["customers"]
    finally:
        module.min_plan_tier = original
        db.session.commit()
        db.session.expire_all()

    # Asserted at the service level: within one test everything shares a single
    # app context, so g.ctx (and its module_access) stays pinned to the first
    # request even though the real server rebuilds it per request.
    fresh = register_business(client, "tier-check@example.com", "Tier Check")
    assert "customers" in accessible_modules(fresh["business"]["id"])


def test_ungated_blueprint_is_closed_when_the_plan_drops_the_module(client, owner):
    """A plan without the module must lock the whole blueprint, not one route."""
    plan = _default_plan()
    original = list(plan.modules or [])
    plan.modules = [m for m in original if m != "customers"]
    db.session.commit()
    try:
        response = client.post(
            "/api/customers", headers=owner["headers"], json={"name": "Blocked Co"}
        )
        assert response.status_code == 403, response.get_json()
        assert response.get_json()["missing"] == ["customers"]
    finally:
        plan.modules = original
        db.session.commit()


def test_cors_preflight_is_not_blocked_by_the_module_gate(client, owner):
    response = client.options(
        "/api/customers",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == 200
