from datetime import datetime, timedelta, timezone

import pytest

from conftest import auth_headers

REPORT_CODES = [
    "profit_and_loss",
    "sales",
    "expenses",
    "receivables_aging",
    "payables_aging",
    "cash_flow",
    "inventory",
]


def _seed(client, headers):
    customer = client.post(
        "/api/customers",
        headers=headers,
        json={"display_name": "Report Customer", "customer_type": "individual"},
    ).get_json()["data"]["customer"]
    item = client.post(
        "/api/items",
        headers=headers,
        json={
            "type": "product",
            "name": "Report Widget",
            "cost_price": 20,
            "selling_price": 60,
            "is_inventory_tracked": True,
        },
    ).get_json()["data"]["item"]
    warehouse = client.get("/api/inventory/default-warehouse", headers=headers).get_json()["data"][
        "warehouse"
    ]["id"]
    client.post(
        f"/api/inventory/balances/{item['id']}/in",
        headers=headers,
        json={"warehouse_id": warehouse, "quantity": 12},
    )
    invoice = client.post(
        "/api/finance/invoices",
        headers=headers,
        json={
            "customer_id": customer["id"],
            "items": [
                {
                    "item_id": item["id"],
                    "description": "Widget",
                    "quantity": 2,
                    "unit_price": 60,
                    "cost_price": 20,
                }
            ],
        },
    ).get_json()["data"]["invoice"]
    client.post(f"/api/finance/invoices/{invoice['id']}/send", headers=headers)
    client.post(
        "/api/finance/payments",
        headers=headers,
        json={
            "payment_type": "customer_receipt",
            "customer_id": customer["id"],
            "invoice_id": invoice["id"],
            "amount": 60,
            "payment_method": "cash",
        },
    )
    # "Utilities" ships with every new business, so reuse the seeded category
    # instead of creating a duplicate (which the API rejects).
    category = next(
        c
        for c in client.get(
            "/api/finance/expense-categories", headers=headers
        ).get_json()["data"]["categories"]
        if c["name"] == "Utilities"
    )
    expense = client.post(
        "/api/finance/expenses",
        headers=headers,
        json={
            "category_id": category["id"],
            "description": "Power",
            "amount": 30,
            "payment_method": "cash",
        },
    ).get_json()["data"]["expense"]
    client.post(f"/api/finance/expenses/{expense['id']}/approve", headers=headers)
    return {"customer": customer, "item": item, "invoice": invoice}


@pytest.mark.parametrize("report", REPORT_CODES)
def test_report_shapes(client, owner, report):
    headers = owner["headers"]
    _seed(client, headers)

    today = datetime.now(timezone.utc).date().isoformat()
    response = client.get(
        f"/api/reports?report={report}&from_date={today}&to_date={today}", headers=headers
    )
    assert response.status_code == 200, (report, response.get_json())
    data = response.get_json()["data"]
    assert data["title"], report
    assert isinstance(data["summary"], dict), report
    assert data["summary"], report

    for key, value in data["summary"].items():
        assert isinstance(value, (int, float)), (report, key, value)


def test_profit_and_loss_uses_live_totals(client, owner):
    headers = owner["headers"]
    _seed(client, headers)
    today = datetime.now(timezone.utc).date().isoformat()
    data = client.get(
        f"/api/reports?report=profit_and_loss&from_date={today}&to_date={today}",
        headers=headers,
    ).get_json()["data"]
    assert data["summary"]["revenue"] == 120.0
    assert data["summary"]["cogs"] == 40.0
    assert data["summary"]["expenses"] == 30.0
    assert data["summary"]["gross_profit"] == 80.0
    assert data["summary"]["net_profit"] == 50.0


def test_cash_flow_reflects_receipts(client, owner):
    headers = owner["headers"]
    _seed(client, headers)
    today = datetime.now(timezone.utc).date().isoformat()
    data = client.get(
        f"/api/reports?report=cash_flow&from_date={today}&to_date={today}", headers=headers
    ).get_json()["data"]
    assert data["summary"]["total_receipts"] == 60.0
    assert data["summary"]["net_cash_flow"] == 60.0


def test_inventory_report_values_stock(client, owner):
    headers = owner["headers"]
    _seed(client, headers)
    data = client.get("/api/reports?report=inventory", headers=headers).get_json()["data"]
    assert data["summary"]["total_items"] == 1
    assert data["items"][0]["quantity_on_hand"] == 12.0
    assert data["summary"]["total_value"] == pytest.approx(240.0)


def test_report_date_range_excludes_older_records(client, owner):
    headers = owner["headers"]
    _seed(client, headers)
    past_from = (datetime.now(timezone.utc) - timedelta(days=90)).date().isoformat()
    past_to = (datetime.now(timezone.utc) - timedelta(days=60)).date().isoformat()
    data = client.get(
        f"/api/reports?report=sales&from_date={past_from}&to_date={past_to}", headers=headers
    ).get_json()["data"]
    assert data["summary"]["total_sales"] == 0.0
    assert data["summary"]["invoice_count"] == 0


def test_reports_are_tenant_isolated(client, owner):
    _seed(client, owner["headers"])
    from conftest import register_business

    other = register_business(client, "report-iso@example.com", "Isolated Reports")
    today = datetime.now(timezone.utc).date().isoformat()
    data = client.get(
        f"/api/reports?report=sales&from_date={today}&to_date={today}",
        headers=auth_headers(other["access_token"]),
    ).get_json()["data"]
    assert data["summary"]["total_sales"] == 0.0
    assert data["invoices"] == []


@pytest.mark.parametrize("report", REPORT_CODES)
def test_report_exports(client, owner, report):
    headers = owner["headers"]
    _seed(client, headers)

    csv_response = client.get(f"/api/reports?report={report}&export=csv", headers=headers)
    assert csv_response.status_code == 200, (report, csv_response.get_json())
    assert csv_response.mimetype == "text/csv"
    assert csv_response.data

    pdf_response = client.get(f"/api/reports?report={report}&export=pdf", headers=headers)
    assert pdf_response.status_code == 200, (report, pdf_response.get_json())
    assert pdf_response.data.startswith(b"%PDF")
