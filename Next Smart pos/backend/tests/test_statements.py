def _create_customer(client, headers, **overrides):
    body = {"display_name": "Statement Customer", "customer_type": "individual"}
    body.update(overrides)
    response = client.post("/api/customers", headers=headers, json=body)
    assert response.status_code == 201, response.get_json()
    return response.get_json()["data"]["customer"]


def _create_supplier(client, headers, **overrides):
    body = {"display_name": "Statement Supplier"}
    body.update(overrides)
    response = client.post("/api/suppliers", headers=headers, json=body)
    assert response.status_code == 201, response.get_json()
    return response.get_json()["data"]["supplier"]


def test_customer_statement_pdf_is_a_pdf(client, owner):
    customer = _create_customer(client, owner["headers"])
    response = client.get(
        f"/api/finance/customers/{customer['id']}/statement/pdf", headers=owner["headers"]
    )
    assert response.status_code == 200, response.get_json()
    assert response.mimetype == "application/pdf"
    assert response.data.startswith(b"%PDF")


def test_supplier_statement_pdf_is_a_pdf(client, owner):
    supplier = _create_supplier(client, owner["headers"])
    response = client.get(
        f"/api/finance/suppliers/{supplier['id']}/statement/pdf", headers=owner["headers"]
    )
    assert response.status_code == 200, response.get_json()
    assert response.mimetype == "application/pdf"
    assert response.data.startswith(b"%PDF")


def test_statement_pdf_rejects_a_party_from_another_business(client, owner):
    other = client.post("/api/auth/register", json={
        "email": "stranger@example.com",
        "password": "Str0ngPass!1",
        "first_name": "Grace",
        "last_name": "Hopper",
        "business_name": "Other Retail Ltd",
        "business_country": "GB",
        "business_currency": "GBP",
        "timezone": "Europe/London",
    })
    assert other.status_code == 201, other.get_json()
    stranger = other.get_json()["data"]

    customer = _create_customer(client, owner["headers"])
    response = client.get(
        f"/api/finance/customers/{customer['id']}/statement/pdf",
        headers={"Authorization": f"Bearer {stranger['access_token']}"},
    )
    assert response.status_code == 404, response.get_json()


def test_supplier_statement_json_still_available(client, owner):
    supplier = _create_supplier(client, owner["headers"])
    response = client.get(
        f"/api/finance/suppliers/{supplier['id']}/statement", headers=owner["headers"]
    )
    assert response.status_code == 200, response.get_json()
    statement = response.get_json()["data"]["statement"]
    assert statement["supplier"]["display_name"] == "Statement Supplier"
    assert statement["expenses"] == []
