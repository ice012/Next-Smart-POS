"""Signup must provision a Business Owner with an immediate 14-day free trial."""
from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.models import Subscription
from conftest import auth_headers, register_business

TRIAL_DAYS = 14


def test_registration_assigns_business_owner_role(client):
    data = register_business(client, "owner-role@example.com", "Owner Role Business")
    assert data["role"] == "Business Owner"

    me = client.get("/api/auth/me", headers=auth_headers(data["access_token"]))
    assert me.status_code == 200, me.get_json()
    session = me.get_json()["data"]
    assert session["role"] == "Business Owner"
    assert session["is_owner"] is True


def test_registration_starts_14_day_trial(app, client):
    register_business(client, "trial@example.com", "Trial Business")
    with app.app_context():
        sub = db.session.query(Subscription).one()
        assert sub.status == "trial"
        assert sub.trial_ends_at is not None
        assert sub.current_period_end is not None
        # SQLite hands back naive datetimes, so normalise before comparing.
        ends_at = sub.trial_ends_at
        if ends_at.tzinfo is None:
            ends_at = ends_at.replace(tzinfo=timezone.utc)
        delta = ends_at - datetime.now(timezone.utc)
        # Allow a small tolerance for clock drift between the two calls.
        assert timedelta(days=TRIAL_DAYS) - timedelta(minutes=1) <= delta
        assert delta <= timedelta(days=TRIAL_DAYS) + timedelta(minutes=1)


def test_trial_countdown_is_exposed_on_session(client):
    data = register_business(client, "countdown@example.com", "Countdown Business")
    me = client.get("/api/auth/me", headers=auth_headers(data["access_token"]))
    subscription = me.get_json()["data"]["subscription"]

    assert subscription["is_trial"] is True
    assert subscription["status"] == "trial"
    assert subscription["trial_total_days"] == TRIAL_DAYS
    assert subscription["trial_ends_at"]
    assert subscription["trial_days_remaining"] == TRIAL_DAYS
    assert subscription["is_expired"] is False


def test_trial_countdown_reaches_zero_as_trial_lapses(app, client):
    """Once the trial lapses, ``/me`` is itself rejected by the tier-2 access
    check, so the countdown is asserted at the service layer."""
    from app.services.subscription_service import trial_info

    data = register_business(client, "expiring@example.com", "Expiring Business")
    business_id = data["business"]["id"]
    with app.app_context():
        sub = db.session.query(Subscription).one()
        sub.trial_ends_at = datetime.now(timezone.utc) - timedelta(seconds=30)
        db.session.commit()

        subscription = trial_info(business_id)

    assert subscription["is_trial"] is True
    assert subscription["trial_days_remaining"] == 0
    assert subscription["is_expired"] is True


def test_new_owner_reaches_dashboard_immediately(client):
    """The trial must be provisioned in the same flow, or tier-2 auth locks the
    new owner out of every endpoint with 'Subscription is not active'."""
    data = register_business(client, "dash@example.com", "Dashboard Business")
    headers = auth_headers(data["access_token"])

    dashboard = client.get("/api/dashboard", headers=headers)
    assert dashboard.status_code == 200, dashboard.get_json()

    me = client.get("/api/auth/me", headers=headers)
    session = me.get_json()["data"]
    assert "dashboard" in session["module_access"]
    # Business Owner carries the full permission set, incl. role management.
    assert "role.read" in session["permissions"]


def test_registration_survives_missing_default_plan(app, client, monkeypatch):
    """If trial provisioning fails, signup must fail loudly rather than create
    a business that can never authenticate."""
    from app.services import subscription_service

    def boom(business):
        raise RuntimeError("no default plan")

    monkeypatch.setattr(subscription_service, "get_default_plan", boom)
    monkeypatch.setattr(
        "app.services.auth_service.ensure_subscription",
        boom,
    )

    response = client.post(
        "/api/auth/register",
        json={
            "email": "broken@example.com",
            "password": "Str0ngPass!1",
            "first_name": "Ada",
            "last_name": "Lovelace",
            "business_name": "Broken Business",
            "business_country": "GB",
            "business_currency": "GBP",
            "timezone": "Europe/London",
        },
    )
    assert response.status_code >= 400
