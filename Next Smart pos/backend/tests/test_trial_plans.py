"""Per-plan trial length: signup honours the chosen plan, admin can extend it."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import Business, Subscription, SubscriptionPlan
from app.services.subscription_service import (
    extend_trial,
    public_plan_catalogue,
    trial_info,
)

from conftest import auth_headers

SA_HEADERS = {"X-SuperAdmin-Token": "test-superadmin-token"}


@pytest.fixture(autouse=True)
def _app_context(app):
    """The app fixture's context closes before it yields, so push our own."""
    with app.app_context():
        yield


def _register(client, email, business_name, plan_id=None):
    body = {
        "email": email,
        "password": "Str0ngPass!1",
        "first_name": "Ada",
        "last_name": "Lovelace",
        "business_name": business_name,
        "business_country": "KE",
        "business_currency": "KES",
        "timezone": "Africa/Nairobi",
    }
    if plan_id is not None:
        body["plan_id"] = plan_id
    response = client.post("/api/auth/register", json=body)
    return response


def _sub(business_id):
    return db.session.scalar(
        select(Subscription).where(Subscription.business_id == business_id)
    )


def _aware(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _plan(**overrides):
    defaults = {
        "name": "Trial Plan",
        "code": "trial-plan",
        "tier": "starter",
        "price_cents": 1000,
        "currency": "KES",
        "billing_cycle": "monthly",
        "storage_mb": 500,
        "trial_days": 30,
        "max_users": 3,
        "max_branches": 1,
        "max_invoices": 50,
        "modules": ["core", "sales", "customers"],
    }
    defaults.update(overrides)
    plan = SubscriptionPlan(**defaults)
    db.session.add(plan)
    db.session.commit()
    return plan


# ---- public catalogue -----------------------------------------------------


def test_plans_endpoint_is_public(client):
    response = client.get("/api/auth/plans")
    assert response.status_code == 200, response.get_json()
    plans = response.get_json()["data"]["plans"]
    assert plans
    assert all("price_cents" in plan and "trial_days" in plan for plan in plans)


def test_plans_endpoint_hides_inactive_plans(client):
    _plan(name="Hidden", code="hidden", trial_days=5)
    hidden = db.session.scalar(
        select(SubscriptionPlan).where(SubscriptionPlan.code == "hidden")
    )
    hidden.is_active = False
    db.session.commit()

    # `code` is internal, so assert on the public name field instead.
    names = [plan["name"] for plan in public_plan_catalogue()]
    assert "Hidden" not in names


def test_plans_endpoint_does_not_leak_internal_fields(client):
    plan = public_plan_catalogue()[0]
    # The id is intentionally present: the signup form has to submit it back.
    assert plan["id"]
    for internal in ("modules", "is_default", "is_active", "created_at", "code"):
        assert internal not in plan


# ---- registration honours the chosen plan ---------------------------------


def test_signup_uses_the_selected_plan(client):
    plan = _plan(trial_days=30)

    response = _register(client, "picked@example.com", "Picked Plan Co", plan_id=str(plan.id))
    assert response.status_code == 201, response.get_json()
    data = response.get_json()["data"]

    sub = _sub(data["business"]["id"])
    assert str(sub.plan_id) == str(plan.id)
    assert data["subscription"]["plan_tier"] == "starter"
    assert data["subscription"]["trial_total_days"] == 30

    delta = _aware(sub.trial_ends_at) - datetime.now(timezone.utc)
    assert timedelta(days=30) - timedelta(minutes=1) <= delta
    assert delta <= timedelta(days=30) + timedelta(minutes=1)


def test_selected_plan_caps_apply_from_day_one(client):
    plan = _plan(trial_days=30, max_users=1)

    response = _register(client, "capped@example.com", "Capped Co", plan_id=str(plan.id))
    business_id = response.get_json()["data"]["business"]["id"]

    detail = client.get(
        f"/api/superadmin/businesses/{business_id}", headers=SA_HEADERS
    )
    assert detail.get_json()["data"]["limits"]["limits"]["users"] == 1


def test_signup_without_a_plan_lands_on_the_default(client):
    response = _register(client, "defaulted@example.com", "Defaulted Co")
    assert response.status_code == 201, response.get_json()
    data = response.get_json()["data"]

    default = db.session.scalar(
        select(SubscriptionPlan).where(SubscriptionPlan.is_default.is_(True))
    )
    sub = _sub(data["business"]["id"])
    assert str(sub.plan_id) == str(default.id)
    assert data["subscription"]["trial_total_days"] == default.trial_days


def test_signup_rejects_an_unknown_plan(client):
    response = _register(
        client, "bad@example.com", "Bad Co", plan_id="00000000-0000-0000-0000-000000000000"
    )
    assert response.status_code == 422, response.get_json()
    assert db.session.scalar(select(Business).where(Business.name == "Bad Co")) is None


def test_signup_rejects_a_deactivated_plan(client):
    plan = _plan(name="Retired", code="retired")
    plan.is_active = False
    db.session.commit()

    response = _register(client, "retired@example.com", "Retired Co", plan_id=str(plan.id))
    assert response.status_code == 422, response.get_json()


def test_zero_day_trial_starts_the_account_active(client):
    plan = _plan(name="No Trial", code="no-trial", trial_days=0)

    response = _register(client, "notrial@example.com", "No Trial Co", plan_id=str(plan.id))
    assert response.status_code == 201, response.get_json()
    data = response.get_json()["data"]

    sub = _sub(data["business"]["id"])
    assert sub.status == "active"
    assert sub.trial_ends_at is None
    assert data["subscription"]["is_trial"] is False
    # Active, so the tenant can actually work immediately.
    me = client.get("/api/auth/me", headers=auth_headers(data["access_token"]))
    assert me.status_code == 200, me.get_json()


# ---- admin extension ------------------------------------------------------


def test_extend_trial_adds_days_on_top_of_the_current_end(client):
    _register(client, "ext@example.com", "Ext Co")
    business = db.session.scalar(select(Business).where(Business.name == "Ext Co"))
    before = _aware(_sub(business.id).trial_ends_at)

    result = extend_trial(business.id, 10, reason="payment received")

    assert result["days_added"] == 10
    assert result["reinstated"] is False
    after = _aware(_sub(business.id).trial_ends_at)
    assert timedelta(days=9) < (after - before) <= timedelta(days=10)
    assert _sub(business.id).status == "trial"


def test_extend_trial_reinstates_a_lapsed_trial(client):
    _register(client, "lapsed@example.com", "Lapsed Co")
    business = db.session.scalar(select(Business).where(Business.name == "Lapsed Co"))
    sub = _sub(business.id)
    sub.status = "past_due"
    sub.trial_ends_at = datetime.now(timezone.utc) - timedelta(days=9)
    sub.current_period_end = datetime.now(timezone.utc) - timedelta(days=9)
    db.session.commit()

    result = extend_trial(business.id, 14)

    assert result["reinstated"] is True
    sub = _sub(business.id)
    assert sub.status == "trial"
    # Counted from now, not from the lapsed date.
    assert _aware(sub.trial_ends_at) > datetime.now(timezone.utc) + timedelta(days=13)
    assert trial_info(business.id)["is_trial"] is True


def test_extend_trial_writes_an_audit_trail(client):
    from app.models import AuditLog

    _register(client, "audited@example.com", "Audited Co")
    business = db.session.scalar(select(Business).where(Business.name == "Audited Co"))

    extend_trial(business.id, 7, reason="M-Pesa confirmed")

    entries = db.session.scalars(
        select(AuditLog).where(
            AuditLog.business_id == business.id, AuditLog.resource == "subscription"
        )
    ).all()
    extended = [e for e in entries if (e.details or {}).get("action") == "trial_extended"]
    assert len(extended) == 1
    assert extended[0].details["days"] == 7
    assert extended[0].details["reason"] == "M-Pesa confirmed"


def test_superadmin_can_extend_a_trial_over_the_api(client):
    _register(client, "api-ext@example.com", "Api Ext Co")
    business = db.session.scalar(select(Business).where(Business.name == "Api Ext Co"))

    response = client.post(
        f"/api/superadmin/businesses/{business.id}/trial",
        headers=SA_HEADERS,
        json={"days": 30, "reason": "annual prepay"},
    )

    assert response.status_code == 200, response.get_json()
    assert response.get_json()["data"]["days_added"] == 30


def test_extend_trial_route_requires_the_superadmin_token(client):
    _register(client, "guard@example.com", "Guard Co")
    business = db.session.scalar(select(Business).where(Business.name == "Guard Co"))

    assert (
        client.post(
            f"/api/superadmin/businesses/{business.id}/trial", json={"days": 5}
        ).status_code
        == 403
    )


def test_extend_trial_rejects_a_non_positive_term(client):
    _register(client, "zero@example.com", "Zero Co")
    business = db.session.scalar(select(Business).where(Business.name == "Zero Co"))

    for days in (0, -5):
        assert (
            client.post(
                f"/api/superadmin/businesses/{business.id}/trial",
                headers=SA_HEADERS,
                json={"days": days},
            ).status_code
            == 422
        )


def test_extend_trial_rejects_an_unknown_business(client):
    response = client.post(
        "/api/superadmin/businesses/00000000-0000-0000-0000-000000000000/trial",
        headers=SA_HEADERS,
        json={"days": 5},
    )
    assert response.status_code == 404, response.get_json()


# ---- plan editing ---------------------------------------------------------


def test_superadmin_can_change_a_plans_trial_length(client):
    plan = _plan(trial_days=14)

    response = client.patch(
        f"/api/superadmin/plans/{plan.id}", headers=SA_HEADERS, json={"trial_days": 45}
    )

    assert response.status_code == 200, response.get_json()
    assert response.get_json()["data"]["plan"]["trial_days"] == 45


def test_plan_trial_length_is_validated(client):
    response = client.post(
        "/api/superadmin/plans",
        headers=SA_HEADERS,
        json={
            "name": "Absurd",
            "code": "absurd",
            "tier": "starter",
            "price_cents": 100,
            "currency": "KES",
            "billing_cycle": "monthly",
            "storage_mb": 0,
            "trial_days": 9999,
            "modules": ["core"],
        },
    )
    assert response.status_code == 422, response.get_json()
