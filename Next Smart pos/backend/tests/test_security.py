from conftest import auth_headers, register_business


def test_rejects_unauthenticated_requests(client):
    assert client.get("/api/dashboard").status_code == 401
    assert client.get("/api/customers").status_code == 401
    assert client.get("/api/reports").status_code == 401


def test_rejects_foreign_business_records(client):
    owner = register_business(client, "owner1@example.com", "Owner One")
    other = register_business(client, "owner2@example.com", "Owner Two")
    owner_headers = auth_headers(owner["access_token"])

    created = client.post(
        "/api/customers",
        headers=owner_headers,
        json={"display_name": "Private Customer", "customer_type": "individual"},
    )
    assert created.status_code == 201
    customer_id = created.get_json()["data"]["customer"]["id"]

    other_headers = auth_headers(other["access_token"])
    foreign = client.get(f"/api/customers/{customer_id}", headers=other_headers)
    assert foreign.status_code == 404, foreign.get_json()
    assert client.patch(
        f"/api/customers/{customer_id}",
        headers=other_headers,
        json={"display_name": "Hacked"},
    ).status_code == 404


def test_payment_cannot_reference_foreign_invoice(client):
    first = register_business(client, "pay1@example.com", "Payments One")
    second = register_business(client, "pay2@example.com", "Payments Two")
    first_headers = auth_headers(first["access_token"])
    second_headers = auth_headers(second["access_token"])

    customer = client.post(
        "/api/customers",
        headers=first_headers,
        json={"display_name": "Invoice Customer", "customer_type": "individual"},
    ).get_json()["data"]["customer"]
    invoice = client.post(
        "/api/finance/invoices",
        headers=first_headers,
        json={"customer_id": customer["id"], "items": [{"description": "Service", "quantity": 1, "unit_price": 50}]},
    ).get_json()["data"]["invoice"]
    client.post(f"/api/finance/invoices/{invoice['id']}/send", headers=first_headers)

    response = client.post(
        "/api/finance/payments",
        headers=second_headers,
        json={
            "payment_type": "customer_receipt",
            "invoice_id": invoice["id"],
            "amount": 10,
            "payment_method": "cash",
        },
    )
    assert response.status_code == 404


def test_super_admin_requires_token_and_isolated(client):
    owner = register_business(client, "sa-owner@example.com", "Super Admin Test Business")
    assert client.get("/api/superadmin/stats").status_code == 403

    response = client.get(
        "/api/superadmin/stats",
        headers={"X-SuperAdmin-Token": "test-superadmin-token"},
    )
    assert response.status_code == 200
    assert response.get_json()["data"]["total_businesses"] == 1

    business_id = owner["business"]["id"]
    updated = client.patch(
        f"/api/superadmin/businesses/{business_id}/status",
        headers={"X-SuperAdmin-Token": "test-superadmin-token"},
        json={"status": "suspended"},
    )
    assert updated.status_code == 200
    assert updated.get_json()["data"]["is_active"] is False

    denied = client.get(
        "/api/dashboard",
        headers=auth_headers(owner["access_token"]),
    )
    assert denied.status_code == 403


def test_suspended_subscription_blocks_protected_modules(client):
    owner = register_business(client, "trial-owner@example.com", "Trial Suspension Business")
    headers = auth_headers(owner["access_token"])
    from app.extensions import db
    from app.models import Subscription

    with client.application.app_context():
        from uuid import UUID
        from app.extensions import db
        from app.models import Subscription

        subscription = db.session.query(Subscription).filter_by(
            business_id=UUID(owner["business"]["id"])
        ).one()
        subscription.status = "suspended"
        db.session.commit()

    assert client.get("/api/dashboard", headers=headers).status_code == 403
    assert client.get("/api/reports", headers=headers).status_code == 403


def test_audit_logs_are_tenant_scoped(client):
    first = register_business(client, "audit1@example.com", "Audit One")
    second = register_business(client, "audit2@example.com", "Audit Two")

    client.post(
        "/api/customers",
        headers=auth_headers(first["access_token"]),
        json={"display_name": "Audited", "customer_type": "individual"},
    )
    second_logs = client.get("/api/audit", headers=auth_headers(second["access_token"]))
    assert second_logs.status_code == 200
    logs = second_logs.get_json()["data"]["logs"]
    assert all(log["business_id"] == second["business"]["id"] for log in logs)
    assert not [log for log in logs if log["resource"] == "customer"]

    first_logs = client.get("/api/audit", headers=auth_headers(first["access_token"]))
    assert first_logs.status_code == 200
    assert any(
        log["resource"] == "customer"
        for log in first_logs.get_json()["data"]["logs"]
    )
