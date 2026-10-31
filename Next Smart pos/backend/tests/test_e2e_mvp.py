from datetime import datetime, timedelta, timezone

from conftest import auth_headers, register_business


def _create_customer(client, headers, name="Grace Hopper"):
    response = client.post(
        "/api/customers",
        headers=headers,
        json={
            "display_name": name,
            "customer_type": "individual",
            "first_name": "Grace",
            "last_name": "Hopper",
            "email": f"{name.split()[0].lower()}@example.com",
        },
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()["data"]["customer"]


def _create_item(client, headers, name="Widget"):
    response = client.post(
        "/api/items",
        headers=headers,
        json={
            "type": "product",
            "name": name,
            "cost_price": 40,
            "selling_price": 100,
            "minimum_stock": 5,
            "is_inventory_tracked": True,
        },
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()["data"]["item"]


def test_mvp_end_to_end_workflow(client, owner):
    headers = owner["headers"]
    customer = _create_customer(client, headers)
    item = _create_item(client, headers)

    warehouse = client.get("/api/inventory/default-warehouse", headers=headers)
    assert warehouse.status_code == 200, warehouse.get_json()
    warehouse_id = warehouse.get_json()["data"]["warehouse"]["id"]

    stock = client.post(
        f"/api/inventory/balances/{item['id']}/in",
        headers=headers,
        json={"warehouse_id": warehouse_id, "quantity": 20, "note": "Opening stock"},
    )
    assert stock.status_code == 201, stock.get_json()

    balance = client.get(
        f"/api/inventory/balances/{item['id']}?warehouse_id={warehouse_id}",
        headers=headers,
    )
    assert balance.get_json()["data"]["balance"]["quantity_on_hand"] == 20.0

    invoice = client.post(
        "/api/finance/invoices",
        headers=headers,
        json={
            "customer_id": customer["id"],
            "due_date": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
            "items": [
                {
                    "item_id": item["id"],
                    "description": item["name"],
                    "quantity": 2,
                    "unit_price": 100,
                    "cost_price": 40,
                    "tax_rate": 10,
                }
            ],
        },
    )
    assert invoice.status_code == 201, invoice.get_json()
    invoice_data = invoice.get_json()["data"]["invoice"]
    assert invoice_data["subtotal"] == 200.0
    assert invoice_data["tax_amount"] == 20.0
    assert invoice_data["total_amount"] == 220.0

    sent = client.post(f"/api/finance/invoices/{invoice_data['id']}/send", headers=headers)
    assert sent.status_code == 200, sent.get_json()

    payment = client.post(
        "/api/finance/payments",
        headers=headers,
        json={
            "payment_type": "customer_receipt",
            "customer_id": customer["id"],
            "invoice_id": invoice_data["id"],
            "amount": 100,
            "payment_method": "cash",
        },
    )
    assert payment.status_code == 201, payment.get_json()
    payment_data = payment.get_json()["data"]["payment"]
    assert float(payment_data["allocations"][0]["amount"]) == 100.0

    refreshed = client.get(f"/api/finance/invoices/{invoice_data['id']}", headers=headers)
    refreshed_invoice = refreshed.get_json()["data"]["invoice"]
    assert refreshed_invoice["status"] == "partially_paid"
    assert refreshed_invoice["balance_due"] == 120.0

    # "Rent" ships with every new business, so reuse the seeded category.
    categories = client.get("/api/finance/expense-categories", headers=headers)
    assert categories.status_code == 200, categories.get_json()
    category_id = next(
        c["id"]
        for c in categories.get_json()["data"]["categories"]
        if c["name"] == "Rent"
    )

    expense = client.post(
        "/api/finance/expenses",
        headers=headers,
        json={
            "category_id": category_id,
            "description": "Monthly rent",
            "amount": 50,
            "payment_method": "cash",
            "status": "draft",
        },
    )
    assert expense.status_code == 201, expense.get_json()
    expense_id = expense.get_json()["data"]["expense"]["id"]
    assert client.post(f"/api/finance/expenses/{expense_id}/approve", headers=headers).status_code == 200

    dashboard = client.get("/api/dashboard", headers=headers)
    assert dashboard.status_code == 200, dashboard.get_json()
    kpi = dashboard.get_json()["data"]["kpi"]
    assert kpi["total_sales"] == 220.0
    assert kpi["total_received"] == 100.0
    assert kpi["total_expenses"] == 50.0
    assert kpi["receivables"] == 120.0

    for report in (
        "profit_and_loss",
        "sales",
        "expenses",
        "receivables_aging",
        "payables_aging",
        "cash_flow",
        "inventory",
    ):
        response = client.get(f"/api/reports?report={report}", headers=headers)
        assert response.status_code == 200, (report, response.get_json())
        csv_response = client.get(f"/api/reports?report={report}&export=csv", headers=headers)
        assert csv_response.status_code == 200
        assert csv_response.mimetype == "text/csv"
        pdf_response = client.get(f"/api/reports?report={report}&export=pdf", headers=headers)
        assert pdf_response.status_code == 200
        assert pdf_response.data.startswith(b"%PDF")

    logs = client.get("/api/audit", headers=headers)
    assert logs.status_code == 200
    assert logs.get_json()["meta"]["total"] >= 4


def test_invoice_pdf_and_audit_generation(client, owner):
    headers = owner["headers"]
    customer = _create_customer(client, headers, "Katherine Johnson")
    invoice = client.post(
        "/api/finance/invoices",
        headers=headers,
        json={
            "customer_id": customer["id"],
            "items": [
                {"description": "Consulting", "quantity": 1, "unit_price": 500, "is_service": True}
            ],
        },
    )
    assert invoice.status_code == 201, invoice.get_json()
    invoice_id = invoice.get_json()["data"]["invoice"]["id"]

    pdf = client.get(f"/api/finance/invoices/{invoice_id}/pdf", headers=headers)
    assert pdf.status_code == 200, pdf.get_json()
    assert pdf.data.startswith(b"%PDF")


def test_cross_tenant_dashboard_isolation(client):
    first = register_business(client, "first@example.com", "First Business")
    second = register_business(client, "second@example.com", "Second Business")
    first_headers = auth_headers(first["access_token"])
    second_headers = auth_headers(second["access_token"])

    item = _create_item(client, first_headers, "Isolated Widget")
    customer = _create_customer(client, first_headers, "Isolated Customer")
    invoice = client.post(
        "/api/finance/invoices",
        headers=first_headers,
        json={
            "customer_id": customer["id"],
            "items": [{"item_id": item["id"], "description": "Widget", "quantity": 1, "unit_price": 100}],
        },
    )
    assert invoice.status_code == 201

    second_dashboard = client.get("/api/dashboard", headers=second_headers)
    assert second_dashboard.status_code == 200
    assert second_dashboard.get_json()["data"]["kpi"]["total_sales"] == 0.0
