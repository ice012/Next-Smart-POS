from decimal import Decimal

import pytest

from conftest import auth_headers, register_business  # noqa: F401

SA_HEADERS = {
    "X-SuperAdmin-Token": "test-superadmin-token",
    **auth_headers("unused"),
}
SA_HEADERS.pop("Authorization")


def _enable_enterprise(client, owner):
    """Quotations and purchases sit above the default plan, so upgrade first."""
    plans = client.get("/api/superadmin/plans", headers=SA_HEADERS)
    assert plans.status_code == 200, plans.get_json()
    plan_ids = {p["code"]: p["id"] for p in plans.get_json()["data"]["plans"]}
    assert "enterprise" in plan_ids, plan_ids
    changed = client.post(
        f"/api/superadmin/businesses/{owner['business_id']}/plan",
        headers=SA_HEADERS,
        json={"plan_id": plan_ids["enterprise"]},
    )
    assert changed.status_code == 200, changed.get_json()


def _default_warehouse_id(client, headers):
    response = client.get("/api/inventory/default-warehouse", headers=headers)
    assert response.status_code == 200, response.get_json()
    return response.get_json()["data"]["warehouse"]["id"]


def _stock_in(client, headers, item_id, quantity, warehouse_id):
    response = client.post(f"/api/inventory/balances/{item_id}/in", headers=headers, json={
        "warehouse_id": warehouse_id,
        "quantity": quantity,
        "note": "opening stock",
    })
    assert response.status_code == 201, response.get_json()


def _create_item(client, headers, **overrides):
    body = {
        "type": "product",
        "name": "Widget",
        "cost_price": 20,
        "selling_price": 50,
        "is_inventory_tracked": True,
    }
    body.update(overrides)
    response = client.post("/api/items", headers=headers, json=body)
    assert response.status_code == 201, response.get_json()
    return response.get_json()["data"]["item"]


def _create_supplier(client, headers, **overrides):
    body = {"display_name": "Acme Supplies", "email": "sales@acme.com"}
    body.update(overrides)
    response = client.post("/api/suppliers", headers=headers, json=body)
    assert response.status_code == 201, response.get_json()
    return response.get_json()["data"]["supplier"]


def _create_customer(client, headers, **overrides):
    body = {"display_name": "Walk-in Customer", "customer_type": "individual"}
    body.update(overrides)
    response = client.post("/api/customers", headers=headers, json=body)
    assert response.status_code == 201, response.get_json()
    return response.get_json()["data"]["customer"]


def _create_tax(client, headers, rate=10, applies_to="sales"):
    response = client.post("/api/finance/tax-configs", headers=headers, json={
        "name": f"VAT {rate}",
        "code": f"VAT{rate}",
        "rate": rate,
        "applies_to": applies_to,
        "is_default": True,
    })
    assert response.status_code == 201, response.get_json()
    return response.get_json()["data"]["tax_config"]


def _balance(client, headers, item_id):
    response = client.get(f"/api/inventory/balances/{item_id}", headers=headers)
    assert response.status_code == 200, response.get_json()
    return Decimal(str(response.get_json()["data"]["balance"]["quantity_on_hand"]))


def _create_sale(client, headers, item_id, **overrides):
    body = {
        "sale_type": "cash",
        "items": [{"item_id": item_id, "quantity": 2}],
    }
    body.update(overrides)
    return client.post("/api/sales", headers=headers, json=body)


def test_sale_totals_and_stock_reduction(client, owner):
    _create_tax(client, owner["headers"], rate=10)
    item = _create_item(client, owner["headers"])
    _stock_in(client, owner["headers"], item["id"], 10, _default_warehouse_id(client, owner["headers"]))
    before = _balance(client, owner["headers"], item["id"])

    response = _create_sale(client, owner["headers"], item["id"])
    assert response.status_code == 201, response.get_json()
    sale = response.get_json()["data"]["sale"]

    assert sale["status"] == "posted"
    assert sale["subtotal"] == 100.0
    assert sale["tax_amount"] == 10.0
    assert sale["total_amount"] == 110.0
    assert sale["paid_amount"] == 0.0
    assert sale["balance_due"] == 110.0
    assert len(sale["items"]) == 1
    assert sale["items"][0]["unit_price"] == 50.0
    assert _balance(client, owner["headers"], item["id"]) == before - 2


def test_cash_sale_with_payment_records_balance(client, owner):
    _create_tax(client, owner["headers"], rate=0)
    customer = _create_customer(client, owner["headers"])
    item = _create_item(client, owner["headers"])
    _stock_in(client, owner["headers"], item["id"], 10, _default_warehouse_id(client, owner["headers"]))

    response = _create_sale(
        client, owner["headers"], item["id"],
        customer_id=customer["id"],
        payments=[{"payment_method": "cash", "amount": 60}],
    )
    assert response.status_code == 201, response.get_json()
    sale = response.get_json()["data"]["sale"]

    assert sale["customer_name"] == customer["display_name"]
    assert sale["paid_amount"] == 60.0
    assert sale["balance_due"] == 40.0
    assert len(sale["payments"]) == 1

    payment = client.post(
        f"/api/sales/{sale['id']}/payments",
        headers=owner["headers"],
        json={"payment_method": "bank", "amount": 40},
    )
    assert payment.status_code == 201, payment.get_json()
    settled = payment.get_json()["data"]["sale"]
    assert settled["paid_amount"] == 100.0
    assert settled["balance_due"] == 0.0


def test_walk_in_sale_needs_no_customer(client, owner):
    """A cash sale must post with no customer selected at all."""
    _create_tax(client, owner["headers"], rate=0)
    item = _create_item(client, owner["headers"])
    _stock_in(client, owner["headers"], item["id"], 10, _default_warehouse_id(client, owner["headers"]))

    response = client.post(
        "/api/sales",
        headers=owner["headers"],
        json={
            "sale_type": "cash",
            "items": [{"item_id": item["id"], "quantity": 1}],
            "payments": [{"payment_method": "cash", "amount": 50, "date": "2026-09-28"}],
        },
    )
    assert response.status_code == 201, response.get_json()
    sale = response.get_json()["data"]["sale"]
    assert sale["customer_id"] is None
    assert sale["customer_name"] is None
    assert sale["paid_amount"] == 50.0
    assert sale["balance_due"] == 0.0


def test_payment_accepts_a_plain_calendar_day(client, owner):
    """dateInputValue sends YYYY-MM-DD, which must parse without a time part."""
    _create_tax(client, owner["headers"], rate=0)
    item = _create_item(client, owner["headers"])
    _stock_in(client, owner["headers"], item["id"], 10, _default_warehouse_id(client, owner["headers"]))

    response = _create_sale(
        client, owner["headers"], item["id"],
        payments=[{"payment_method": "cash", "amount": 100, "date": "2026-09-28"}],
    )
    assert response.status_code == 201, response.get_json()
    sale = response.get_json()["data"]["sale"]
    assert sale["balance_due"] == 0.0
    assert sale["payments"][0]["date"].startswith("2026-09-28")


def test_payment_rejects_a_non_iso_date(client, owner):
    _create_tax(client, owner["headers"], rate=0)
    item = _create_item(client, owner["headers"])
    _stock_in(client, owner["headers"], item["id"], 10, _default_warehouse_id(client, owner["headers"]))

    response = _create_sale(
        client, owner["headers"], item["id"],
        payments=[{"payment_method": "cash", "amount": 100, "date": "Mon Sep 28"}],
    )
    assert response.status_code == 422, response.get_json()
    assert "Invalid date" in response.get_json()["message"]


def test_sale_cancellation_restocks(client, owner):
    _create_tax(client, owner["headers"], rate=0)
    item = _create_item(client, owner["headers"])
    _stock_in(client, owner["headers"], item["id"], 10, _default_warehouse_id(client, owner["headers"]))
    before = _balance(client, owner["headers"], item["id"])

    sale = _create_sale(client, owner["headers"], item["id"]).get_json()["data"]["sale"]
    assert _balance(client, owner["headers"], item["id"]) == before - 2

    cancelled = client.post(
        f"/api/sales/{sale['id']}/cancel",
        headers=owner["headers"],
        json={"reason": "customer returned goods"},
    )
    assert cancelled.status_code == 200, cancelled.get_json()
    assert cancelled.get_json()["data"]["sale"]["status"] == "cancelled"
    assert _balance(client, owner["headers"], item["id"]) == before

    again = client.post(f"/api/sales/{sale['id']}/cancel", headers=owner["headers"], json={})
    assert again.status_code == 422, again.get_json()


def test_sale_beyond_stock_is_rejected(client, owner):
    _create_tax(client, owner["headers"], rate=0)
    item = _create_item(client, owner["headers"])

    response = client.post("/api/sales", headers=owner["headers"], json={
        "sale_type": "cash",
        "items": [{"item_id": item["id"], "quantity": 5}],
    })
    assert response.status_code == 422, response.get_json()


def test_sales_list_exposes_pagination(client, owner):
    _create_tax(client, owner["headers"], rate=0)
    item = _create_item(client, owner["headers"])
    _stock_in(client, owner["headers"], item["id"], 10, _default_warehouse_id(client, owner["headers"]))
    _create_sale(client, owner["headers"], item["id"])

    response = client.get("/api/sales?per_page=10", headers=owner["headers"])
    assert response.status_code == 200, response.get_json()
    payload = response.get_json()
    assert len(payload["data"]["sales"]) == 1
    assert payload["meta"]["total"] == 1
    assert payload["meta"]["page"] == 1

    filtered = client.get("/api/sales?status=cancelled", headers=owner["headers"])
    assert filtered.get_json()["data"]["sales"] == []


def test_quotation_lifecycle_and_conversion(client, owner):
    _enable_enterprise(client, owner)
    _create_tax(client, owner["headers"], rate=0)
    customer = _create_customer(client, owner["headers"])
    item = _create_item(client, owner["headers"])
    _stock_in(client, owner["headers"], item["id"], 10, _default_warehouse_id(client, owner["headers"]))
    stock_before = _balance(client, owner["headers"], item["id"])

    created = client.post("/api/quotations", headers=owner["headers"], json={
        "customer_id": customer["id"],
        "items": [{"item_id": item["id"], "quantity": 3}],
    })
    assert created.status_code == 201, created.get_json()
    quotation = created.get_json()["data"]["quotation"]

    assert quotation["status"] == "draft"
    assert quotation["total_amount"] == 150.0
    assert quotation["expiry_date"] is not None
    # A quotation must never move stock.
    assert _balance(client, owner["headers"], item["id"]) == stock_before

    sent = client.post(
        f"/api/quotations/{quotation['id']}/status",
        headers=owner["headers"],
        json={"status": "sent"},
    )
    assert sent.status_code == 200, sent.get_json()
    assert sent.get_json()["data"]["quotation"]["status"] == "sent"

    accepted = client.post(
        f"/api/quotations/{quotation['id']}/status",
        headers=owner["headers"],
        json={"status": "accepted"},
    )
    assert accepted.status_code == 200, accepted.get_json()
    assert accepted.get_json()["data"]["quotation"]["status"] == "accepted"

    converted = client.post(
        f"/api/quotations/{quotation['id']}/convert",
        headers=owner["headers"],
        json={"sale_type": "credit"},
    )
    assert converted.status_code == 201, converted.get_json()
    sale = converted.get_json()["data"]["sale"]
    assert sale["total_amount"] == 150.0
    assert sale["sale_type"] == "credit"
    assert _balance(client, owner["headers"], item["id"]) == stock_before - 3

    repeat = client.post(
        f"/api/quotations/{quotation['id']}/convert",
        headers=owner["headers"],
        json={"sale_type": "credit"},
    )
    assert repeat.status_code == 422, repeat.get_json()

    deleted = client.delete(f"/api/quotations/{quotation['id']}", headers=owner["headers"])
    assert deleted.status_code == 422, deleted.get_json()


def test_quotation_status_transitions_are_validated(client, owner):
    _enable_enterprise(client, owner)
    _create_tax(client, owner["headers"], rate=0)
    item = _create_item(client, owner["headers"])
    created = client.post("/api/quotations", headers=owner["headers"], json={
        "items": [{"item_id": item["id"], "quantity": 1}],
    })
    quotation = created.get_json()["data"]["quotation"]

    invalid = client.post(
        f"/api/quotations/{quotation['id']}/status",
        headers=owner["headers"],
        json={"status": "converted"},
    )
    assert invalid.status_code == 422, invalid.get_json()

    list_response = client.get("/api/quotations", headers=owner["headers"])
    assert list_response.status_code == 200, list_response.get_json()
    assert list_response.get_json()["meta"]["total"] == 1


def test_purchase_order_receive_increases_stock(client, owner):
    _enable_enterprise(client, owner)
    _create_tax(client, owner["headers"], rate=0, applies_to="purchases")
    supplier = _create_supplier(client, owner["headers"])
    item = _create_item(client, owner["headers"])
    stock_before = _balance(client, owner["headers"], item["id"])

    created = client.post("/api/purchases", headers=owner["headers"], json={
        "supplier_id": supplier["id"],
        "items": [{"item_id": item["id"], "quantity": 10}],
    })
    assert created.status_code == 201, created.get_json()
    order = created.get_json()["data"]["purchase_order"]

    assert order["status"] == "draft"
    assert order["supplier_name"] == supplier["display_name"]
    assert order["total_amount"] == 200.0
    # Drafting an order must not move stock.
    assert _balance(client, owner["headers"], item["id"]) == stock_before

    ordered = client.patch(
        f"/api/purchases/{order['id']}", headers=owner["headers"], json={"status": "ordered"}
    )
    assert ordered.status_code == 200, ordered.get_json()
    assert ordered.get_json()["data"]["purchase_order"]["status"] == "ordered"

    received = client.post(
        f"/api/purchases/{order['id']}/receive",
        headers=owner["headers"],
        json={"items": [{"item_id": item["id"], "quantity": 4}]},
    )
    assert received.status_code == 200, received.get_json()
    partial = received.get_json()["data"]["purchase_order"]
    assert partial["status"] == "ordered"
    assert partial["items"][0]["received_quantity"] == 4.0
    assert _balance(client, owner["headers"], item["id"]) == stock_before + 4

    over = client.post(
        f"/api/purchases/{order['id']}/receive",
        headers=owner["headers"],
        json={"items": [{"item_id": item["id"], "quantity": 99}]},
    )
    assert over.status_code == 422, over.get_json()

    finished = client.post(
        f"/api/purchases/{order['id']}/receive",
        headers=owner["headers"],
        json={"items": [{"item_id": item["id"], "quantity": 6}]},
    )
    assert finished.status_code == 200, finished.get_json()
    assert finished.get_json()["data"]["purchase_order"]["status"] == "received"
    assert _balance(client, owner["headers"], item["id"]) == stock_before + 10


def test_purchase_order_cancel_after_receipt_is_blocked(client, owner):
    _enable_enterprise(client, owner)
    _create_tax(client, owner["headers"], rate=0, applies_to="purchases")
    supplier = _create_supplier(client, owner["headers"])
    item = _create_item(client, owner["headers"])

    created = client.post("/api/purchases", headers=owner["headers"], json={
        "supplier_id": supplier["id"],
        "items": [{"item_id": item["id"], "quantity": 2}],
    })
    order = created.get_json()["data"]["purchase_order"]

    client.post(
        f"/api/purchases/{order['id']}/receive",
        headers=owner["headers"],
        json={"items": [{"item_id": item["id"], "quantity": 1}]},
    )
    blocked = client.post(
        f"/api/purchases/{order['id']}/cancel", headers=owner["headers"], json={}
    )
    assert blocked.status_code == 422, blocked.get_json()

    draft = client.post("/api/purchases", headers=owner["headers"], json={
        "supplier_id": supplier["id"],
        "items": [{"item_id": item["id"], "quantity": 1}],
    })
    draft_order = draft.get_json()["data"]["purchase_order"]
    cancelled = client.post(
        f"/api/purchases/{draft_order['id']}/cancel",
        headers=owner["headers"],
        json={"reason": "supplier unavailable"},
    )
    assert cancelled.status_code == 200, cancelled.get_json()
    assert cancelled.get_json()["data"]["purchase_order"]["status"] == "cancelled"

    removed = client.delete(f"/api/purchases/{draft_order['id']}", headers=owner["headers"])
    assert removed.status_code == 204, removed.get_json()


@pytest.mark.parametrize(
    "path,body,upgrade",
    [
        ("/api/sales", {"sale_type": "cash", "items": []}, False),
        ("/api/quotations", {"items": []}, True),
        ("/api/purchases", {"supplier_id": "00000000-0000-0000-0000-000000000000", "items": []}, True),
    ],
)
def test_documents_require_at_least_one_line(client, owner, path, body, upgrade):
    if upgrade:
        _enable_enterprise(client, owner)
    response = client.post(path, headers=owner["headers"], json=body)
    assert response.status_code == 422, response.get_json()
