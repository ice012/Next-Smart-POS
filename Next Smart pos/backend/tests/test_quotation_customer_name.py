"""Free-text customer names on quotations.

A quotation can be raised for someone who is not yet in the customer list. The
name is resolved to a customer via POST /api/customers/resolve, which is
idempotent so repeating a name never trips the unique-name constraint.
"""

from decimal import Decimal

from conftest import auth_headers, register_business  # noqa: F401

SA_HEADERS = {
    "X-SuperAdmin-Token": "test-superadmin-token",
    **auth_headers("unused"),
}
SA_HEADERS.pop("Authorization")


def _enable_enterprise(client, owner):
    """Quotations sit above the default plan, so upgrade first."""
    plans = client.get("/api/superadmin/plans", headers=SA_HEADERS)
    assert plans.status_code == 200, plans.get_json()
    plan_ids = {p["code"]: p["id"] for p in plans.get_json()["data"]["plans"]}
    changed = client.post(
        f"/api/superadmin/businesses/{owner['business_id']}/plan",
        headers=SA_HEADERS,
        json={"plan_id": plan_ids["enterprise"]},
    )
    assert changed.status_code == 200, changed.get_json()


def _resolve(client, headers, display_name, **extra):
    body = {"display_name": display_name}
    body.update(extra)
    return client.post("/api/customers/resolve", headers=headers, json=body)


def _customer_count(client, headers):
    response = client.get("/api/customers?per_page=100", headers=headers)
    assert response.status_code == 200, response.get_json()
    return response.get_json()["meta"]["total"]


def _create_item(client, headers):
    response = client.post("/api/items", headers=headers, json={
        "type": "product",
        "name": "Widget",
        "cost_price": 20,
        "selling_price": 50,
        "is_inventory_tracked": False,
    })
    assert response.status_code == 201, response.get_json()
    return response.get_json()["data"]["item"]


def _create_tax(client, headers, rate=0):
    response = client.post("/api/finance/tax-configs", headers=headers, json={
        "name": f"VAT {rate}",
        "code": f"VAT{rate}",
        "rate": rate,
        "applies_to": "sales",
        "is_default": True,
    })
    assert response.status_code == 201, response.get_json()


# --- resolve endpoint -------------------------------------------------------

def test_resolve_creates_customer_when_name_is_new(client, owner):
    response = _resolve(client, owner["headers"], "Walk-in Enquiry")

    assert response.status_code == 200, response.get_json()
    customer = response.get_json()["data"]["customer"]
    assert customer["display_name"] == "Walk-in Enquiry"
    assert customer["customer_type"] == "individual"
    assert customer["customer_number"].startswith("CUST-")
    assert _customer_count(client, owner["headers"]) == 1


def test_resolve_is_idempotent_for_the_same_name(client, owner):
    first = _resolve(client, owner["headers"], "Repeat Buyer")
    second = _resolve(client, owner["headers"], "Repeat Buyer")

    assert first.status_code == 200, first.get_json()
    assert second.status_code == 200, second.get_json()
    assert second.get_json()["data"]["customer"]["id"] == first.get_json()["data"]["customer"]["id"]
    assert _customer_count(client, owner["headers"]) == 1


def test_resolve_ignores_case_and_surrounding_whitespace(client, owner):
    first = _resolve(client, owner["headers"], "Acme Supplies")
    second = _resolve(client, owner["headers"], "  acme supplies  ")

    assert second.status_code == 200, second.get_json()
    assert second.get_json()["data"]["customer"]["id"] == first.get_json()["data"]["customer"]["id"]
    assert _customer_count(client, owner["headers"]) == 1


def test_resolve_reuses_a_customer_created_through_the_normal_route(client, owner):
    created = client.post("/api/customers", headers=owner["headers"], json={
        "display_name": "Existing Client",
        "customer_type": "corporate",
    })
    assert created.status_code == 201, created.get_json()
    existing = created.get_json()["data"]["customer"]

    resolved = _resolve(client, owner["headers"], "Existing Client")

    assert resolved.status_code == 200, resolved.get_json()
    assert resolved.get_json()["data"]["customer"]["id"] == existing["id"]
    # The pre-existing record keeps its own type rather than being replaced.
    assert resolved.get_json()["data"]["customer"]["customer_type"] == "corporate"
    assert _customer_count(client, owner["headers"]) == 1


def test_resolve_honours_corporate_type_for_a_new_name(client, owner):
    response = _resolve(client, owner["headers"], "Nairobi Foods Ltd", customer_type="corporate")

    assert response.status_code == 200, response.get_json()
    assert response.get_json()["data"]["customer"]["customer_type"] == "corporate"


def test_resolve_does_not_reuse_a_soft_deleted_customer(client, owner):
    """The name is still reserved by the unique constraint, so this must be a
    clear error rather than a 500 or a silent reuse of an archived customer."""
    created = client.post("/api/customers", headers=owner["headers"], json={
        "display_name": "Former Client",
    })
    assert created.status_code == 201, created.get_json()
    customer_id = created.get_json()["data"]["customer"]["id"]

    deleted = client.delete(f"/api/customers/{customer_id}", headers=owner["headers"])
    assert deleted.status_code == 204, deleted.get_json()

    resolved = _resolve(client, owner["headers"], "Former Client")

    assert resolved.status_code == 409, resolved.get_json()
    assert "Former Client" in resolved.get_json()["message"]


def test_resolve_is_scoped_to_the_business(client, owner):
    other = register_business(client, email="other@example.com", business_name="Other Retail Ltd")
    other_headers = auth_headers(other["access_token"])

    first = _resolve(client, owner["headers"], "Shared Name Ltd")
    second = _resolve(client, other_headers, "Shared Name Ltd")

    assert first.status_code == 200, first.get_json()
    assert second.status_code == 200, second.get_json()
    assert first.get_json()["data"]["customer"]["id"] != second.get_json()["data"]["customer"]["id"]


def test_resolve_rejects_a_blank_name(client, owner):
    response = _resolve(client, owner["headers"], "   ")

    assert response.status_code == 422, response.get_json()
    assert _customer_count(client, owner["headers"]) == 0


def test_resolve_rejects_an_unknown_field(client, owner):
    response = _resolve(client, owner["headers"], "Someone", nickname="Sneaky")

    assert response.status_code == 422, response.get_json()


def test_resolve_requires_authentication(client):
    assert _resolve(client, {}, "Anonymous").status_code == 401


# --- the quotation flow this exists for -------------------------------------

def test_quotation_accepts_a_typed_customer_name(client, owner):
    """The reported bug: a name typed in the quotation form was discarded."""
    _enable_enterprise(client, owner)
    _create_tax(client, owner["headers"])
    item = _create_item(client, owner["headers"])

    resolved = _resolve(client, owner["headers"], "Prospect Never Registered")
    assert resolved.status_code == 200, resolved.get_json()
    customer_id = resolved.get_json()["data"]["customer"]["id"]

    created = client.post("/api/quotations", headers=owner["headers"], json={
        "customer_id": customer_id,
        "items": [{"item_id": item["id"], "quantity": 2}],
    })

    assert created.status_code == 201, created.get_json()
    quotation = created.get_json()["data"]["quotation"]
    assert quotation["customer_id"] == customer_id
    assert quotation["customer_name"] == "Prospect Never Registered"
    assert quotation["total_amount"] == 100.0


def test_a_quotation_for_a_typed_name_converts_to_a_sale(client, owner):
    """Creating the customer is what keeps the convert path working."""
    _enable_enterprise(client, owner)
    _create_tax(client, owner["headers"])
    item = _create_item(client, owner["headers"])

    customer_id = _resolve(
        client, owner["headers"], "Walk-in Buyer"
    ).get_json()["data"]["customer"]["id"]

    created = client.post("/api/quotations", headers=owner["headers"], json={
        "customer_id": customer_id,
        "items": [{"item_id": item["id"], "quantity": 1}],
    })
    assert created.status_code == 201, created.get_json()
    quotation_id = created.get_json()["data"]["quotation"]["id"]

    for status in ("sent", "accepted"):
        step = client.post(
            f"/api/quotations/{quotation_id}/status",
            headers=owner["headers"],
            json={"status": status},
        )
        assert step.status_code == 200, step.get_json()

    converted = client.post(
        f"/api/quotations/{quotation_id}/convert",
        headers=owner["headers"],
        json={"sale_type": "credit"},
    )

    assert converted.status_code == 201, converted.get_json()
    sale = converted.get_json()["data"]["sale"]
    assert sale["customer_id"] == customer_id
    assert sale["customer_name"] == "Walk-in Buyer"


def test_reusing_a_typed_name_across_quotations_reuses_one_customer(client, owner):
    _enable_enterprise(client, owner)
    _create_tax(client, owner["headers"])
    item = _create_item(client, owner["headers"])

    ids = []
    for _ in range(2):
        customer_id = _resolve(
            client, owner["headers"], "Regular Prospect"
        ).get_json()["data"]["customer"]["id"]
        ids.append(customer_id)
        created = client.post("/api/quotations", headers=owner["headers"], json={
            "customer_id": customer_id,
            "items": [{"item_id": item["id"], "quantity": 1}],
        })
        assert created.status_code == 201, created.get_json()

    assert ids[0] == ids[1]
    assert _customer_count(client, owner["headers"]) == 1


def test_quotation_still_works_with_no_customer_at_all(client, owner):
    """A missing name stays valid rather than erroring."""
    _enable_enterprise(client, owner)
    _create_tax(client, owner["headers"])
    item = _create_item(client, owner["headers"])

    created = client.post("/api/quotations", headers=owner["headers"], json={
        "items": [{"item_id": item["id"], "quantity": 1}],
    })

    assert created.status_code == 201, created.get_json()
    assert created.get_json()["data"]["quotation"]["customer_id"] is None


def test_quotation_still_rejects_an_unknown_customer_id(client, owner):
    _enable_enterprise(client, owner)
    _create_tax(client, owner["headers"])
    item = _create_item(client, owner["headers"])

    created = client.post("/api/quotations", headers=owner["headers"], json={
        "customer_id": "00000000-0000-0000-0000-000000000000",
        "items": [{"item_id": item["id"], "quantity": 1}],
    })

    assert created.status_code == 404, created.get_json()


def test_quotation_rejects_a_blank_item_id_as_a_client_error(client, owner):
    """A blank placeholder row used to reach to_uuid and surface as a 500."""
    _enable_enterprise(client, owner)
    _create_tax(client, owner["headers"])
    item = _create_item(client, owner["headers"])

    created = client.post("/api/quotations", headers=owner["headers"], json={
        "items": [
            {"item_id": "", "quantity": 1},
            {"item_id": item["id"], "quantity": 1},
        ],
    })

    assert created.status_code == 422, created.get_json()
    assert "valid item" in created.get_json()["message"]


def test_quotation_rejects_a_malformed_item_id(client, owner):
    _enable_enterprise(client, owner)
    _create_tax(client, owner["headers"])

    created = client.post("/api/quotations", headers=owner["headers"], json={
        "items": [{"item_id": "not-a-uuid", "quantity": 1}],
    })

    assert created.status_code == 422, created.get_json()


def test_a_resolved_customer_becomes_reusable_from_search(client, owner):
    """After quoting, the new name should be pickable from the customer list."""
    _resolve(client, owner["headers"], "Emerging Customer")

    found = client.get("/api/customers/search?q=Emerging", headers=owner["headers"])
    assert found.status_code == 200, found.get_json()
    names = [c["display_name"] for c in found.get_json()["data"]["customers"]]
    assert "Emerging Customer" in names, names


def test_decimal_amounts_survive_the_resolve_flow(client, owner):
    """Guards against the amount being coerced to a float via the name round-trip."""
    _enable_enterprise(client, owner)
    _create_tax(client, owner["headers"])
    item = _create_item(client, owner["headers"])

    customer_id = _resolve(
        client, owner["headers"], "Precision Buyer"
    ).get_json()["data"]["customer"]["id"]

    created = client.post("/api/quotations", headers=owner["headers"], json={
        "customer_id": customer_id,
        "items": [{"item_id": item["id"], "quantity": 3, "discount_amount": 10.5}],
    })

    assert created.status_code == 201, created.get_json()
    quotation = created.get_json()["data"]["quotation"]
    # 3 x 50.00 less 10.50 discount
    assert Decimal(str(quotation["total_amount"])) == Decimal("139.50")
