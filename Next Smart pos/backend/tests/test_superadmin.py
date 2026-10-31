from conftest import auth_headers, register_business

SA_TOKEN = "test-superadmin-token"
SA_HEADERS = {"X-SuperAdmin-Token": SA_TOKEN}


def test_me_exposes_subscription_and_modules(client, owner):
    response = client.get("/api/auth/me", headers=owner["headers"])
    assert response.status_code == 200, response.get_json()
    data = response.get_json()["data"]
    assert data["module_access"], data
    assert data["subscription_status"] in ("trial", "active")


def test_superadmin_requires_token(client):
    assert client.get("/api/superadmin/stats").status_code == 403
    assert client.get(
        "/api/superadmin/stats", headers={"X-SuperAdmin-Token": "wrong"}
    ).status_code == 403
    assert client.get("/api/superadmin/stats", headers=SA_HEADERS).status_code == 200


def test_superadmin_stats_reports_registered_businesses(client):
    register_business(client, "sa-one@example.com", "SA One")
    register_business(client, "sa-two@example.com", "SA Two")
    response = client.get("/api/superadmin/stats", headers=SA_HEADERS)
    assert response.status_code == 200, response.get_json()
    stats = response.get_json()["data"]
    assert stats["total_businesses"] == 2
    assert stats["total_subscriptions"] == 2


def test_superadmin_business_lifecycle(client):
    owner = register_business(client, "sa-biz@example.com", "Managed Business")
    business_id = owner["business"]["id"]

    listing = client.get("/api/superadmin/businesses", headers=SA_HEADERS)
    assert listing.status_code == 200, listing.get_json()
    assert business_id in [b["id"] for b in listing.get_json()["data"]["businesses"]]

    detail = client.get(f"/api/superadmin/businesses/{business_id}", headers=SA_HEADERS)
    assert detail.status_code == 200, detail.get_json()
    assert detail.get_json()["data"]["user_count"] == 1

    suspended = client.patch(
        f"/api/superadmin/businesses/{business_id}/status",
        headers=SA_HEADERS,
        json={"status": "suspended"},
    )
    assert suspended.status_code == 200, suspended.get_json()
    assert suspended.get_json()["data"]["is_active"] is False

    assert client.get(
        "/api/dashboard", headers=auth_headers(owner["access_token"])
    ).status_code == 403

    restored = client.patch(
        f"/api/superadmin/businesses/{business_id}/status",
        headers=SA_HEADERS,
        json={"status": "active"},
    )
    assert restored.status_code == 200
    assert restored.get_json()["data"]["is_active"] is True
    assert client.get(
        "/api/dashboard", headers=auth_headers(owner["access_token"])
    ).status_code == 200


def test_superadmin_plan_assignment(client):
    owner = register_business(client, "sa-plan@example.com", "Plan Business")
    plans = client.get("/api/superadmin/plans", headers=SA_HEADERS)
    assert plans.status_code == 200, plans.get_json()
    plan_ids = {p["code"]: p["id"] for p in plans.get_json()["data"]["plans"]}
    assert "professional" in plan_ids, plan_ids

    assigned = client.post(
        f"/api/superadmin/businesses/{owner['business']['id']}/plan",
        headers=SA_HEADERS,
        json={"plan_id": plan_ids["professional"]},
    )
    assert assigned.status_code == 200, assigned.get_json()
    assert assigned.get_json()["data"]["business"]["subscription_tier"] == "professional"


def test_superadmin_plan_crud(client):
    created = client.post(
        "/api/superadmin/plans",
        headers=SA_HEADERS,
        json={
            "name": "Custom Tier",
            "code": "custom",
            "tier": "starter",
            "price_cents": 4900,
            "currency": "GBP",
            "billing_cycle": "monthly",
            "storage_mb": 500,
            "modules": ["core", "dashboard"],
        },
    )
    assert created.status_code == 201, created.get_json()
    plan = created.get_json()["data"]["plan"]
    assert plan["code"] == "custom"
    assert "dashboard" in plan["modules"]

    updated = client.patch(
        f"/api/superadmin/plans/{plan['id']}",
        headers=SA_HEADERS,
        json={"price_cents": 5900, "modules": ["core"]},
    )
    assert updated.status_code == 200, updated.get_json()
    assert updated.get_json()["data"]["plan"]["price_cents"] == 5900
    assert updated.get_json()["data"]["plan"]["modules"] == ["core"]

    conflict = client.delete(f"/api/superadmin/plans/{plan['id']}", headers=SA_HEADERS)
    assert conflict.status_code in (200, 204), conflict.get_json()

    missing = client.get(f"/api/superadmin/plans/{plan['id']}", headers=SA_HEADERS)
    assert missing.status_code == 404


def test_superadmin_default_plan_cannot_be_deleted(client):
    plans = client.get("/api/superadmin/plans", headers=SA_HEADERS).get_json()["data"]["plans"]
    default_plan = next(p for p in plans if p["is_default"])
    response = client.delete(f"/api/superadmin/plans/{default_plan['id']}", headers=SA_HEADERS)
    assert response.status_code == 409, response.get_json()


def test_superadmin_modules_listing_and_update(client):
    listing = client.get("/api/superadmin/modules", headers=SA_HEADERS)
    assert listing.status_code == 200, listing.get_json()
    modules = listing.get_json()["data"]["modules"]
    assert any(m["code"] == "reports" for m in modules)
    target = next(m for m in modules if m["code"] == "reports")

    patched = client.patch(
        f"/api/superadmin/modules/{target['id']}",
        headers=SA_HEADERS,
        json={"is_enabled": False, "min_plan_tier": "professional"},
    )
    assert patched.status_code == 200, patched.get_json()
    module = patched.get_json()["data"]["module"]
    assert module["is_enabled"] is False
    assert module["min_plan_tier"] == "professional"

    restored = client.patch(
        f"/api/superadmin/modules/{target['id']}",
        headers=SA_HEADERS,
        json={"is_enabled": True},
    )
    assert restored.get_json()["data"]["module"]["is_enabled"] is True


def test_superadmin_users_and_audit_logs(client):
    owner = register_business(client, "sa-users@example.com", "Users Business")
    users = client.get("/api/superadmin/users", headers=SA_HEADERS)
    assert users.status_code == 200, users.get_json()
    assert owner["user"]["id"] in [u["id"] for u in users.get_json()["data"]["users"]]

    logs = client.get("/api/superadmin/audit-logs", headers=SA_HEADERS)
    assert logs.status_code == 200, logs.get_json()
    assert all("user_email" in log for log in logs.get_json()["data"]["logs"])


def test_superadmin_global_report(client):
    register_business(client, "sa-report@example.com", "Report Business")
    response = client.get("/api/superadmin/reports?report=sales", headers=SA_HEADERS)
    assert response.status_code == 200, response.get_json()
    assert "businesses" in response.get_json()["data"]
