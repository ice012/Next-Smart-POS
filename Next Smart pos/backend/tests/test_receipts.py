from io import BytesIO

from conftest import register_business
from app.extensions import db
from app.models import Business

from pdf_text import pdf_text


def _business_rows(client, headers):
    return client.get("/api/business/info", headers=headers).get_json()["data"]["business"]


def _patch_business(client, headers, **fields):
    response = client.patch("/api/business/info", headers=headers, json=fields)
    assert response.status_code == 200, response.get_json()
    return response.get_json()["data"]["business"]


def _upload_logo(client, headers):
    """A real 2x2 PNG, written with Pillow so the bytes are valid."""
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGBA", (2, 2), (6, 43, 92, 255)).save(buffer, format="PNG")
    buffer.seek(0)
    response = client.post(
        "/api/business/logo", headers=headers,
        data={"logo": (buffer, "logo.png")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200, response.get_json()
    return response.get_json()["data"]["logo"]


def _create_item(client, headers, **overrides):
    body = {"type": "product", "name": "Widget", "cost_price": 20, "selling_price": 50}
    body.update(overrides)
    return client.post("/api/items", headers=headers, json=body).get_json()["data"]["item"]


def _stock_in(client, headers, item_id, quantity=10):
    warehouse_id = client.get(
        "/api/inventory/default-warehouse", headers=headers
    ).get_json()["data"]["warehouse"]["id"]
    response = client.post(
        f"/api/inventory/balances/{item_id}/in", headers=headers,
        json={"warehouse_id": warehouse_id, "quantity": quantity, "note": "opening stock"},
    )
    assert response.status_code == 201, response.get_json()


def _sell(client, headers, item_id, **overrides):
    _stock_in(client, headers, item_id)
    body = {
        "sale_type": "cash",
        "items": [{"item_id": item_id, "quantity": 2}],
        "payments": [{"payment_method": "cash", "amount": 100}],
    }
    body.update(overrides)
    response = client.post("/api/sales", headers=headers, json=body)
    assert response.status_code == 201, response.get_json()
    return response.get_json()["data"]["sale"]


def test_receipt_pdf_is_served_for_a_completed_sale(client, owner):
    item = _create_item(client, owner["headers"])
    sale = _sell(client, owner["headers"], item["id"])

    response = client.get(
        f"/api/sales/{sale['id']}/receipt",
        headers=owner["headers"],
        query_string={"tendered": 120, "change_due": 20},
    )
    assert response.status_code == 200, response.get_json()
    assert response.mimetype == "application/pdf"
    assert response.data.startswith(b"%PDF")
    assert f"receipt-{sale['invoice_number']}.pdf".encode() in response.headers["Content-Disposition"].encode()


def test_receipt_carries_business_details_and_totals(client, owner):
    _patch_business(
        client, owner["headers"],
        address_line1="12 Kenyatta Avenue",
        city="Nairobi",
        postal_code="00100",
        phone="+254 700 000 000",
        email="sales@acme.example",
    )
    item = _create_item(client, owner["headers"])
    sale = _sell(client, owner["headers"], item["id"])

    data = client.get(
        f"/api/sales/{sale['id']}/receipt", headers=owner["headers"]
    ).data
    text = pdf_text(data)

    assert "SALES RECEIPT" in text
    assert sale["invoice_number"] in text
    assert "Test Retail Ltd" in text
    assert "12 Kenyatta Avenue" in text
    assert "Nairobi" in text
    assert "00100" in text
    assert "+254 700 000 000" in text
    assert "sales@acme.example" in text
    assert "Walk-in customer" in text
    assert "TOTAL" in text
    assert "100.00" in text


def test_receipt_uses_the_business_currency(client, owner):
    item = _create_item(client, owner["headers"])
    sale = _sell(client, owner["headers"], item["id"])

    data = client.get(
        f"/api/sales/{sale['id']}/receipt", headers=owner["headers"]
    ).data
    text = pdf_text(data)
    # The conftest business registers with GBP, not the KES fallback.
    assert "£100.00" in text


def test_receipt_embeds_the_business_logo(client, owner):
    logo_url = _upload_logo(client, owner["headers"])
    assert "/uploads/logos/" in logo_url
    item = _create_item(client, owner["headers"])
    sale = _sell(client, owner["headers"], item["id"])

    data = client.get(
        f"/api/sales/{sale['id']}/receipt", headers=owner["headers"]
    ).data
    # reportlab embeds the logo as an image XObject.
    assert b"/Image" in data or b"/XObject" in data


def test_receipt_escapes_markup_in_business_and_item_names(client, owner):
    _patch_business(client, owner["headers"], address_line1="Unit & Co <HQ>")
    item = _create_item(client, owner["headers"], name="Bolt <M8> & Washer")
    sale = _sell(client, owner["headers"], item["id"])

    response = client.get(
        f"/api/sales/{sale['id']}/receipt", headers=owner["headers"]
    )
    assert response.status_code == 200
    assert response.data.startswith(b"%PDF")


def test_receipt_falls_back_to_iso_code_for_unrenderable_symbols(client, app, owner):
    """Helvetica/WinAnsi has no Naira or Rupee glyph, so those print as codes."""
    from app.services.money import currency_symbol

    assert currency_symbol("NGN") == "NGN "
    assert currency_symbol("INR") == "INR "
    # Codes that WinAnsiEncoding does cover keep their real symbol.
    assert currency_symbol("GBP") == "£"
    assert currency_symbol("KES") == "Ksh "


def test_receipt_ignores_a_missing_logo_file(client, app, owner):
    """A logo URL pointing at a deleted file must not break the receipt."""
    business = _patch_business(client, owner["headers"])
    item = _create_item(client, owner["headers"])
    sale = _sell(client, owner["headers"], item["id"])

    with app.app_context():
        row = db.session.get(Business, business["id"])
        row.logo = "http://localhost/uploads/logos/does-not-exist.png"
        db.session.commit()

        response = client.get(
            f"/api/sales/{sale['id']}/receipt", headers=owner["headers"]
        )
        assert response.status_code == 200, response.get_json()
        assert response.data.startswith(b"%PDF")


def test_receipt_ignores_a_non_numerical_tender(client, owner):
    item = _create_item(client, owner["headers"])
    sale = _sell(client, owner["headers"], item["id"])

    response = client.get(
        f"/api/sales/{sale['id']}/receipt",
        headers=owner["headers"],
        query_string={"tendered": "not-a-number"},
    )
    assert response.status_code == 200
    assert response.data.startswith(b"%PDF")


def test_receipt_rejects_a_sale_from_another_business(client, owner):
    other = register_business(client, email="other@example.com", business_name="Other Ltd")
    item = _create_item(client, {"Authorization": f"Bearer {other['access_token']}"})
    sale = _sell(client, {"Authorization": f"Bearer {other['access_token']}"}, item["id"])

    response = client.get(f"/api/sales/{sale['id']}/receipt", headers=owner["headers"])
    assert response.status_code == 404
