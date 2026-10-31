from datetime import datetime, timedelta, timezone

from conftest import auth_headers, register_business


def _create_customer(client, headers, name="Chart Customer"):
    response = client.post(
        "/api/customers",
        headers=headers,
        json={"display_name": name, "customer_type": "individual"},
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()["data"]["customer"]


def _create_item(client, headers, name="Chart Widget"):
    response = client.post(
        "/api/items",
        headers=headers,
        json={
            "type": "product",
            "name": name,
            "cost_price": 10,
            "selling_price": 25,
            "is_inventory_tracked": False,
        },
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()["data"]["item"]


def _paid_invoice(client, headers, customer, item, total=100.0):
    invoice = client.post(
        "/api/finance/invoices",
        headers=headers,
        json={
            "customer_id": customer["id"],
            "items": [
                {
                    "item_id": item["id"],
                    "description": item["name"],
                    "quantity": 1,
                    "unit_price": total,
                }
            ],
        },
    )
    assert invoice.status_code == 201, invoice.get_json()
    data = invoice.get_json()["data"]["invoice"]
    client.post(f"/api/finance/invoices/{data['id']}/send", headers=headers)
    payment = client.post(
        "/api/finance/payments",
        headers=headers,
        json={
            "payment_type": "customer_receipt",
            "customer_id": customer["id"],
            "invoice_id": data["id"],
            "amount": total,
            "payment_method": "cash",
        },
    )
    assert payment.status_code == 201, payment.get_json()
    return data


def test_dashboard_chart_series_expose_date_label_value(client, owner):
    headers = owner["headers"]
    customer = _create_customer(client, headers)
    item = _create_item(client, headers)
    _paid_invoice(client, headers, customer, item)

    response = client.get("/api/dashboard", headers=headers)
    assert response.status_code == 200, response.get_json()
    charts = response.get_json()["data"]["charts"]

    for series in ("daily_sales", "daily_expenses", "daily_collections"):
        rows = charts[series]
        assert rows, series
        for row in rows:
            assert set(row) == {"date", "label", "value"}, row
            assert isinstance(row["value"], (int, float))

    today = datetime.now(timezone.utc).date().isoformat()
    sales_today = next(row["value"] for row in charts["daily_sales"] if row["date"] == today)
    assert sales_today == 100.0


def test_dashboard_respects_date_range(client, owner):
    headers = owner["headers"]
    customer = _create_customer(client, headers, "Range Customer")
    item = _create_item(client, headers, "Range Widget")
    _paid_invoice(client, headers, customer, item, 250.0)

    past_from = (datetime.now(timezone.utc) - timedelta(days=90)).date().isoformat()
    past_to = (datetime.now(timezone.utc) - timedelta(days=60)).date().isoformat()
    response = client.get(
        f"/api/dashboard?from_date={past_from}&to_date={past_to}", headers=headers
    )
    assert response.status_code == 200, response.get_json()
    data = response.get_json()["data"]
    assert data["kpi"]["total_sales"] == 0.0
    assert data["period"]["from"].startswith(past_from)

    today = datetime.now(timezone.utc).date().isoformat()
    current = client.get(
        f"/api/dashboard?from_date={today}&to_date={today}", headers=headers
    )
    assert current.get_json()["data"]["kpi"]["total_sales"] == 250.0


def test_dashboard_is_tenant_isolated(client):
    first = register_business(client, "chart1@example.com", "Chart One")
    second = register_business(client, "chart2@example.com", "Chart Two")
    customer = _create_customer(client, auth_headers(first["access_token"]))
    item = _create_item(client, auth_headers(first["access_token"]))
    _paid_invoice(client, auth_headers(first["access_token"]), customer, item)

    other = client.get("/api/dashboard", headers=auth_headers(second["access_token"]))
    assert other.status_code == 200
    data = other.get_json()["data"]
    assert data["kpi"]["total_sales"] == 0.0
    assert data["kpi"]["total_received"] == 0.0
    assert all(row["value"] == 0.0 for row in data["charts"]["daily_sales"])
