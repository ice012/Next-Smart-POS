from conftest import auth_headers, register_business


def _create_item(client, headers, **overrides):
    body = {
        "type": "product",
        "name": "Tracked Widget",
        "cost_price": 20,
        "selling_price": 75,
    }
    body.update(overrides)
    return client.post("/api/items", headers=headers, json=body)


def test_product_defaults_to_inventory_tracked(client, owner):
    response = _create_item(client, owner["headers"])
    assert response.status_code == 201, response.get_json()
    item = response.get_json()["data"]["item"]
    assert item["is_inventory_tracked"] is True

    balances = client.get("/api/inventory/balances", headers=owner["headers"])
    assert balances.status_code == 200
    rows = balances.get_json()["data"]["balances"]
    assert [row["item_id"] for row in rows] == [item["id"]]


def test_explicit_untracked_product_is_honoured(client, owner):
    response = _create_item(
        client, owner["headers"], name="Service Call", is_inventory_tracked=False
    )
    assert response.status_code == 201, response.get_json()
    item = response.get_json()["data"]["item"]
    assert item["is_inventory_tracked"] is False

    balances = client.get("/api/inventory/balances", headers=owner["headers"])
    rows = balances.get_json()["data"]["balances"]
    assert item["id"] not in [row["item_id"] for row in rows]


def test_service_type_is_not_inventory_tracked(client, owner):
    response = _create_item(
        client, owner["headers"], name="Consulting", type="service", is_inventory_tracked=False
    )
    assert response.status_code == 201, response.get_json()
    assert response.get_json()["data"]["item"]["is_inventory_tracked"] is False


def test_service_ignores_stock_fields_even_if_sent(client, owner):
    """A service can never be stock-managed, whatever the client sends."""
    response = _create_item(
        client,
        owner["headers"],
        name="Installation",
        type="service",
        cost_price=40,
        minimum_stock=5,
        is_inventory_tracked=True,
    )
    assert response.status_code == 201, response.get_json()
    item = response.get_json()["data"]["item"]
    assert item["is_inventory_tracked"] is False
    assert item["cost_price"] == 0
    assert item["minimum_stock"] == 0

    balances = client.get("/api/inventory/balances", headers=owner["headers"])
    rows = balances.get_json()["data"]["balances"]
    assert item["id"] not in [row["item_id"] for row in rows]


def test_switched_service_leaves_stale_balance_row_visible(client, owner):
    """A product holding stock that becomes a service drops out of balances."""
    created = _create_item(
        client, owner["headers"], name="Cable", cost_price=10, minimum_stock=2
    ).get_json()["data"]["item"]

    warehouse_id = client.get(
        "/api/inventory/default-warehouse", headers=owner["headers"]
    ).get_json()["data"]["warehouse"]["id"]
    stock_in = client.post(
        f"/api/inventory/balances/{created['id']}/in",
        headers=owner["headers"],
        json={"warehouse_id": warehouse_id, "quantity": 5, "note": "seed"},
    )
    assert stock_in.status_code == 201, stock_in.get_json()

    balances = client.get("/api/inventory/balances", headers=owner["headers"])
    assert created["id"] in [row["item_id"] for row in balances.get_json()["data"]["balances"]]

    client.patch(
        f"/api/items/{created['id']}",
        headers=owner["headers"],
        json={"type": "service"},
    )

    balances = client.get("/api/inventory/balances", headers=owner["headers"])
    rows = balances.get_json()["data"]["balances"]
    assert created["id"] not in [row["item_id"] for row in rows]

    alerts = client.get("/api/inventory/low-stock", headers=owner["headers"])
    assert created["id"] not in [
        alert.get("item_id") for alert in alerts.get_json()["data"]["alerts"]
    ]


def test_balance_rows_expose_stock_type_flags(client, owner):
    created = _create_item(
        client, owner["headers"], name="Flagged", minimum_stock=1
    ).get_json()["data"]["item"]

    balances = client.get("/api/inventory/balances", headers=owner["headers"])
    rows = balances.get_json()["data"]["balances"]
    row = next(r for r in rows if r["item_id"] == created["id"])
    assert row["is_inventory_tracked"] is True
    assert row["item_type"] == "product"


def test_switching_product_to_service_drops_stock_settings(client, owner):
    created = _create_item(
        client,
        owner["headers"],
        name="Widget",
        cost_price=20,
        minimum_stock=5,
        is_inventory_tracked=True,
    ).get_json()["data"]["item"]

    response = client.patch(
        f"/api/items/{created['id']}",
        headers=owner["headers"],
        json={
            "type": "service",
            "cost_price": 99,
            "minimum_stock": 9,
            "is_inventory_tracked": True,
        },
    )
    assert response.status_code == 200, response.get_json()
    item = response.get_json()["data"]["item"]
    assert item["type"] == "service"
    assert item["is_inventory_tracked"] is False
    assert item["cost_price"] == 0
    assert item["minimum_stock"] == 0


def test_service_stock_is_kept_zero_on_plain_update(client, owner):
    created = _create_item(
        client, owner["headers"], name="Support", type="service"
    ).get_json()["data"]["item"]

    response = client.patch(
        f"/api/items/{created['id']}",
        headers=owner["headers"],
        json={"name": "Support Plan", "selling_price": 120},
    )
    assert response.status_code == 200, response.get_json()
    item = response.get_json()["data"]["item"]
    assert item["name"] == "Support Plan"
    assert item["selling_price"] == 120
    assert item["is_inventory_tracked"] is False
    assert item["cost_price"] == 0
    assert item["minimum_stock"] == 0


def test_untracked_item_is_absent_from_inventory_report(client, owner):
    _create_item(client, owner["headers"], name="Untracked", is_inventory_tracked=False)
    report = client.get("/api/reports?report=inventory", headers=owner["headers"])
    assert report.status_code == 200, report.get_json()
    assert report.get_json()["data"]["items"] == []

    dashboard = client.get("/api/dashboard", headers=owner["headers"])
    assert dashboard.get_json()["data"]["kpi"]["low_stock_count"] == 0
