"""Item search for the till.

The important behaviour is that a miss is an empty result, not a 404. The POS
calls this endpoint on every keystroke of the product box, so a 404 there
filled the browser console and read as a server fault even though nothing had
actually broken.
"""
import pytest

from conftest import auth_headers, register_business  # noqa: F401


def _create_item(client, headers, **overrides):
    body = {
        "type": "product",
        "name": "Claw Hammer",
        "sku": "HAM-001",
        "cost_price": 20,
        "selling_price": 50,
    }
    body.update(overrides)
    response = client.post("/api/items", headers=headers, json=body)
    assert response.status_code == 201, response.get_json()
    return response.get_json()["data"]["item"]


def _search(client, headers, q):
    response = client.get(f"/api/items/search?q={q}", headers=headers)
    return response, response.get_json()["data"]["items"] if response.status_code == 200 else None


def test_a_miss_is_an_empty_result_not_a_404(client, owner):
    _create_item(client, owner["headers"])
    response, items = _search(client, owner["headers"], "zzzz")

    assert response.status_code == 200, response.get_json()
    assert items == []


def test_an_empty_term_is_an_empty_result(client, owner):
    _create_item(client, owner["headers"])
    response, items = _search(client, owner["headers"], "")

    assert response.status_code == 200
    assert items == []


def test_search_matches_name(client, owner):
    _create_item(client, owner["headers"])
    _create_item(client, owner["headers"], name="Screwdriver Set", sku="SD-002")

    response, items = _search(client, owner["headers"], "screw")
    assert response.status_code == 200
    assert [i["name"] for i in items] == ["Screwdriver Set"]


def test_search_matches_sku(client, owner):
    _create_item(client, owner["headers"])
    response, items = _search(client, owner["headers"], "HAM-001")

    assert response.status_code == 200
    assert [i["sku"] for i in items] == ["HAM-001"]


def test_an_exact_barcode_wins_outright(client, owner):
    """A scanner sends a complete code in one go, so that item must come back
    on its own rather than trailing a list of partial name matches."""
    _create_item(client, owner["headers"], name="Pliers", sku="PL-009", barcode="6001234567892")
    _create_item(client, owner["headers"], name="Pliers Large", sku="PL-010", barcode="6001234567893")

    response, items = _search(client, owner["headers"], "6001234567892")
    assert response.status_code == 200
    assert [i["sku"] for i in items] == ["PL-009"]


def test_partial_barcode_still_matches_as_text(client, owner):
    _create_item(client, owner["headers"], name="Pliers", sku="PL-009", barcode="6001234567892")

    response, items = _search(client, owner["headers"], "6001234")
    assert response.status_code == 200
    assert [i["sku"] for i in items] == ["PL-009"]


def test_inactive_items_are_not_offered(client, owner):
    item = _create_item(client, owner["headers"])
    patched = client.patch(
        f"/api/items/{item['id']}", headers=owner["headers"], json={"is_active": False}
    )
    assert patched.status_code == 200, patched.get_json()

    response, items = _search(client, owner["headers"], "Claw")
    assert response.status_code == 200
    assert items == []


def test_search_does_not_leak_across_businesses(client, owner):
    _create_item(client, owner["headers"], name="Confidential Widget")

    other = register_business(client, email="nosee@example.com", business_name="Other Ltd")
    response, items = _search(client, auth_headers(other["access_token"]), "Confidential")

    assert response.status_code == 200
    assert items == []


def test_search_requires_authentication(client):
    assert client.get("/api/items/search?q=a").status_code == 401


def test_a_nonsense_limit_falls_back_instead_of_erroring(client, owner):
    _create_item(client, owner["headers"])
    response = client.get("/api/items/search?q=HAM&limit=abc", headers=owner["headers"])

    assert response.status_code == 200, response.get_json()
    assert len(response.get_json()["data"]["items"]) == 1


def test_results_are_capped(client, owner):
    for i in range(6):
        _create_item(client, owner["headers"], name=f"Bolt M{i}", sku=f"BLT-{i}")

    response = client.get("/api/items/search?q=Bolt&limit=3", headers=owner["headers"])
    assert response.status_code == 200
    assert len(response.get_json()["data"]["items"]) == 3
