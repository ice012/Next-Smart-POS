"""M-Pesa STK push: pricing, the held sale, and the Safaricom callback.

The HTTP layer is always faked. What is under test is the part that actually
matters for the till: that no sale exists until Safaricom confirms, that a
declined push leaves nothing behind, and that a replayed callback cannot create
a second sale.
"""
import base64
from decimal import Decimal

import pytest

from conftest import auth_headers, register_business

from app.mpesa import service as mpesa_service

CALLBACK_URL = "https://pos.example.com/api/mpesa/callback"

DEFAULT_MPESA = {
    "enabled": True,
    "environment": "sandbox",
    "consumer_key": "test-consumer-key",
    "consumer_secret": "test-consumer-secret",
    "shortcode": "174379",
    "passkey": "test-passkey",
    "callback_url": CALLBACK_URL,
}


@pytest.fixture(autouse=True)
def _clear_token_cache():
    """Daraja tokens are cached in a module dict that would otherwise leak
    between tests and mask the token request entirely."""
    mpesa_service.clear_token_cache()
    yield
    mpesa_service.clear_token_cache()


@pytest.fixture()
def ctx(app):
    """An application context for the tests that call the service directly.

    Deliberately NOT autouse: a context that stays pushed for the whole test
    makes Flask's test client reuse the same ``g``, which caches the auth
    context between requests and would hide cross-tenant bugs.
    """
    with app.app_context():
        yield



# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _configure_mpesa(client, headers, **overrides):
    body = dict(DEFAULT_MPESA)
    body.update(overrides)
    return client.patch("/api/business/settings", json={"mpesa": body}, headers=headers)


def _create_item(client, headers, **overrides):
    body = {
        "type": "product",
        "name": "Claw Hammer",
        "cost_price": 20,
        "selling_price": 50,
        "is_inventory_tracked": True,
    }
    body.update(overrides)
    response = client.post("/api/items", headers=headers, json=body)
    assert response.status_code == 201, response.get_json()
    return response.get_json()["data"]["item"]


def _stock_in(client, headers, item_id, quantity=50):
    warehouse = client.get("/api/inventory/default-warehouse", headers=headers)
    assert warehouse.status_code == 200, warehouse.get_json()
    warehouse_id = warehouse.get_json()["data"]["warehouse"]["id"]
    response = client.post(
        f"/api/inventory/balances/{item_id}/in",
        headers=headers,
        json={"warehouse_id": warehouse_id, "quantity": quantity, "note": "opening"},
    )
    assert response.status_code == 201, response.get_json()


def _balance(client, headers, item_id):
    response = client.get(f"/api/inventory/balances/{item_id}", headers=headers)
    assert response.status_code == 200, response.get_json()
    return Decimal(str(response.get_json()["data"]["balance"]["quantity_on_hand"]))


def _sale_count(client, headers):
    response = client.get("/api/sales", headers=headers)
    assert response.status_code == 200, response.get_json()
    return response.get_json()["meta"]["total"]


def fake_daraja(monkeypatch, *, stk_response=None, oauth_response=None, capture=None):
    """Replace the Daraja HTTP call, recording every request made."""

    def _fake(url, payload, headers=None, timeout=None, basic_auth=None, form=False):
        if capture is not None:
            capture.append({"url": url, "payload": payload, "headers": headers or {}})
        if "/oauth/" in url:
            return oauth_response or {"access_token": "test-access-token", "expires_in": 3600}
        if stk_response is not None:
            return stk_response
        return {
            "BusinessKeyCode": 0,
            "RequestID": "ws_CO_TEST_CHECKOUT_01",
            "ResponseCode": "0",
            "ResponseDescription": "Accept the Service request successfully.",
        }

    monkeypatch.setattr(mpesa_service, "_post_json", _fake)
    # Handing back the capture list is what the callers actually want; the
    # function is only useful if they asked for something to patch by hand.
    return capture if capture is not None else _fake


def _push(client, headers, item_id, phone="0712345678", **overrides):
    body = {
        "phone": phone,
        "sale": {
            "items": [{"item_id": item_id, "quantity": 2}],
            "notes": "M-Pesa order",
        },
    }
    body.update(overrides)
    return client.post("/api/mpesa/stkpush", headers=headers, json=body)


def _callback(checkout_id="ws_CO_TEST_CHECKOUT_01", result_code=0, amount=100, **extra):
    stk = {
        "MerchantRequestID": "ignored-in-favour-of-checkout-id",
        "CheckoutRequestID": checkout_id,
        "ResultCode": result_code,
        "ResultDesc": "The service request is successful." if result_code == 0 else "The service request was rejected",
        "CallbackMetadata": {
            "Item": [
                {"Name": "MpesaReceiptNumber", "Value": "QJG7X2K9LM"},
                {"Name": "Amount", "Value": amount},
            ]
        },
    }
    stk.update(extra)
    return {"Body": {"stkCallback": stk}}


# ---------------------------------------------------------------------------
# phone normalisation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "entered,expected",
    [
        ("0712345678", "254712345678"),
        ("+254 712 345 678", "254712345678"),
        ("254712345678", "254712345678"),
        ("254-712-345-678", "254712345678"),
        ("(0712) 345-678", "254712345678"),
        ("712345678", "254712345678"),
        ("+254712345678", "254712345678"),
    ],
)
def test_msisdn_is_normalised_to_international_form(entered, expected, ctx):
    assert mpesa_service.normalize_msisdn(entered) == expected


@pytest.mark.parametrize("entered", ["", "   ", None, "not-a-number", "12345"])
def test_invalid_msisdn_is_rejected(entered, ctx):
    with pytest.raises(Exception):
        mpesa_service.normalize_msisdn(entered)


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------
def test_settings_never_return_the_consumer_secret(client, owner):
    assert _configure_mpesa(client, owner["headers"]).status_code == 200

    response = client.get("/api/business/settings", headers=owner["headers"])
    assert response.status_code == 200, response.get_json()
    mpesa = response.get_json()["data"]["config"]["extra_settings"]["mpesa"]

    assert mpesa["consumer_secret"] == ""
    assert mpesa["has_consumer_secret"] is True
    assert mpesa["shortcode"] == "174379"
    assert "test-consumer-secret" not in response.get_data(as_text=True)


def test_blank_secret_on_patch_keeps_the_stored_one(client, owner):
    _configure_mpesa(client, owner["headers"])
    body = dict(DEFAULT_MPESA)
    body["consumer_secret"] = ""
    body["shortcode"] = "600000"

    assert client.patch(
        "/api/business/settings", json={"mpesa": body}, headers=owner["headers"]
    ).status_code == 200

    mpesa = client.get("/api/business/settings", headers=owner["headers"]).get_json()[
        "data"
    ]["config"]["extra_settings"]["mpesa"]
    assert mpesa["shortcode"] == "600000"
    assert mpesa["has_consumer_secret"] is True


def test_mpesa_config_endpoint_reports_what_is_missing(client, owner):
    before = client.get("/api/mpesa/config", headers=owner["headers"])
    assert before.status_code == 200, before.get_json()
    assert before.get_json()["data"]["is_ready"] is False
    assert "enable M-Pesa in Settings" in before.get_json()["data"]["missing"]

    _configure_mpesa(client, owner["headers"])
    after = client.get("/api/mpesa/config", headers=owner["headers"])
    assert after.get_json()["data"]["is_ready"] is True
    assert after.get_json()["data"]["missing"] == []


def test_push_is_refused_until_mpesa_is_configured(client, owner, monkeypatch):
    item = _create_item(client, owner["headers"])
    _stock_in(client, owner["headers"], item["id"])
    fake_daraja(monkeypatch)

    response = _push(client, owner["headers"], item["id"])
    assert response.status_code == 422, response.get_json()
    assert "M-Pesa is not ready" in response.get_json()["message"]


def test_push_explains_the_missing_credential(client, owner, monkeypatch):
    item = _create_item(client, owner["headers"])
    _stock_in(client, owner["headers"], item["id"])
    fake_daraja(monkeypatch)
    _configure_mpesa(client, owner["headers"], shortcode="")

    response = _push(client, owner["headers"], item["id"])
    assert response.status_code == 422, response.get_json()
    assert "shortcode" in response.get_json()["message"]


# ---------------------------------------------------------------------------
# sending the push
# ---------------------------------------------------------------------------
def test_stk_push_is_built_correctly_and_holds_the_sale(client, owner, monkeypatch):
    item = _create_item(client, owner["headers"])
    _stock_in(client, owner["headers"], item["id"])
    before = _balance(client, owner["headers"], item["id"])
    _configure_mpesa(client, owner["headers"])
    calls = fake_daraja(monkeypatch, capture=[])

    response = _push(client, owner["headers"], item["id"], phone="0712 345 678")
    assert response.status_code == 201, response.get_json()
    stk = response.get_json()["data"]["mpesa_request"]

    assert stk["status"] == "pending"
    assert stk["is_terminal"] is False
    assert stk["checkout_request_id"] == "ws_CO_TEST_CHECKOUT_01"
    assert stk["phone"] == "254712345678"
    assert stk["amount"] == 100.0

    stk_call = next(c for c in calls if "stkpush" in c["url"])
    payload = stk_call["payload"]
    assert payload["BusinessShortCode"] == "174379"
    assert payload["TransactionType"] == "CustomerPayBillOnline"
    assert payload["Amount"] == 100
    assert payload["PhoneNumber"] == "254712345678"
    assert payload["PartyA"] == "254712345678"
    assert payload["PartyB"] == "174379"
    assert payload["CallBackURL"] == CALLBACK_URL
    assert payload["ReferenceCode"] == stk["merchant_request_id"]
    assert stk_call["headers"]["Authorization"] == "Bearer test-access-token"
    # Safaricom authenticates with base64(shortcode + passkey + timestamp).
    decoded = base64.b64decode(payload["Password"]).decode()
    assert decoded.startswith("174379test-passkey")
    assert decoded.endswith(payload["Timestamp"])

    # Nothing is sold, nothing leaves stock, until the money is confirmed.
    assert _sale_count(client, owner["headers"]) == 0
    assert _balance(client, owner["headers"], item["id"]) == before


def test_push_amount_is_rounded_to_whole_shillings(client, owner, monkeypatch):
    """STK pushes carry integers only, so the parked sale uses the same figure."""
    item = _create_item(client, owner["headers"], selling_price="33.30")
    _stock_in(client, owner["headers"], item["id"])
    _configure_mpesa(client, owner["headers"])
    fake_daraja(monkeypatch)

    response = _push(client, owner["headers"], item["id"])
    assert response.status_code == 201, response.get_json()
    assert response.get_json()["data"]["mpesa_request"]["amount"] == 67.0  # 66.60 -> 67


def test_till_can_push_an_amount_other_than_the_total(client, owner, monkeypatch):
    """The tender box is not cosmetic: whatever the cashier enters is what the
    customer is asked to approve, and what the sale is then recorded as."""
    item = _create_item(client, owner["headers"])
    _stock_in(client, owner["headers"], item["id"])
    _configure_mpesa(client, owner["headers"])
    calls = fake_daraja(monkeypatch, capture=[])

    response = _push(client, owner["headers"], item["id"], amount=250)
    assert response.status_code == 201, response.get_json()
    assert response.get_json()["data"]["mpesa_request"]["amount"] == 250.0

    stk_call = next(c for c in calls if "stkpush" in c["url"])
    assert stk_call["payload"]["Amount"] == 250


def test_a_tender_below_one_shilling_is_rejected(client, owner, monkeypatch):
    item = _create_item(client, owner["headers"], selling_price="0.20")
    _stock_in(client, owner["headers"], item["id"])
    _configure_mpesa(client, owner["headers"])
    fake_daraja(monkeypatch)

    # Two of them come to 0.40, which Safaricom would reject as a non-integer.
    response = _push(client, owner["headers"], item["id"])
    assert response.status_code == 422, response.get_json()
    assert "at least 1" in response.get_json()["message"]


def test_a_non_positive_tender_is_rejected(client, owner, monkeypatch):
    item = _create_item(client, owner["headers"])
    _stock_in(client, owner["headers"], item["id"])
    _configure_mpesa(client, owner["headers"])
    fake_daraja(monkeypatch)

    response = _push(client, owner["headers"], item["id"], amount=0)
    assert response.status_code == 422, response.get_json()


def test_confirmed_tender_becomes_the_sale_payment(client, owner, monkeypatch):
    item = _create_item(client, owner["headers"])
    _stock_in(client, owner["headers"], item["id"])
    _configure_mpesa(client, owner["headers"])
    fake_daraja(monkeypatch)

    # Cart is 100; the customer is asked for 250.
    _push(client, owner["headers"], item["id"], amount=250)
    assert client.post("/api/mpesa/callback", json=_callback(amount=250)).status_code == 200

    sale = client.get("/api/sales", headers=owner["headers"]).get_json()["data"]["sales"][0]
    assert sale["total_amount"] == 100.0
    assert sale["paid_amount"] == 250.0
    assert sale["balance_due"] == 0.0
    assert sale["payments"][0]["amount"] == 250.0


def test_a_part_payment_leaves_a_balance_due(client, owner, monkeypatch):
    item = _create_item(client, owner["headers"])
    _stock_in(client, owner["headers"], item["id"])
    _configure_mpesa(client, owner["headers"])
    fake_daraja(monkeypatch)

    _push(client, owner["headers"], item["id"], amount=40)
    assert client.post("/api/mpesa/callback", json=_callback(amount=40)).status_code == 200

    sale = client.get("/api/sales", headers=owner["headers"]).get_json()["data"]["sales"][0]
    assert sale["total_amount"] == 100.0
    assert sale["paid_amount"] == 40.0
    assert sale["balance_due"] == 60.0


def test_callback_url_must_be_https(client, owner):
    """A plain-http callback would never be called by Safaricom, so it is
    rejected while the cashier is looking at the form."""
    response = client.patch(
        "/api/business/settings",
        json={"mpesa": dict(DEFAULT_MPESA, callback_url="http://pos.example.com/cb")},
        headers=owner["headers"],
    )
    assert response.status_code == 422, response.get_json()


def test_daraja_refusing_the_push_fails_immediately(client, owner, monkeypatch):
    item = _create_item(client, owner["headers"])
    _stock_in(client, owner["headers"], item["id"])
    _configure_mpesa(client, owner["headers"])
    fake_daraja(monkeypatch, stk_response={
        "ResponseCode": "1002",
        "ResponseDescription": "Request amount is invalid",
    })

    response = _push(client, owner["headers"], item["id"])
    assert response.status_code == 422, response.get_json()
    assert "Request amount is invalid" in response.get_json()["message"]

    listed = client.get("/api/mpesa/requests", headers=owner["headers"]).get_json()[
        "data"
    ]["mpesa_requests"]
    assert [r["status"] for r in listed] == ["failed"]


def test_push_requires_a_phone_number(client, owner, monkeypatch):
    item = _create_item(client, owner["headers"])
    _stock_in(client, owner["headers"], item["id"])
    _configure_mpesa(client, owner["headers"])
    fake_daraja(monkeypatch)

    response = _push(client, owner["headers"], item["id"], phone="")
    assert response.status_code == 422, response.get_json()
    assert _sale_count(client, owner["headers"]) == 0


def test_push_draft_cannot_carry_a_client_supplied_payment(client, owner, monkeypatch):
    """A client must not be able to declare money that Safaricom has not taken."""
    item = _create_item(client, owner["headers"])
    _stock_in(client, owner["headers"], item["id"])
    _configure_mpesa(client, owner["headers"])
    fake_daraja(monkeypatch)

    response = client.post("/api/mpesa/stkpush", headers=owner["headers"], json={
        "phone": "0712345678",
        "sale": {
            "items": [{"item_id": item["id"], "quantity": 2}],
            "payments": [{"payment_method": "mpesa", "amount": 1}],
        },
    })
    assert response.status_code == 422, response.get_json()


# ---------------------------------------------------------------------------
# the callback
# ---------------------------------------------------------------------------
def test_successful_callback_posts_the_sale(client, owner, monkeypatch):
    item = _create_item(client, owner["headers"])
    _stock_in(client, owner["headers"], item["id"])
    before = _balance(client, owner["headers"], item["id"])
    _configure_mpesa(client, owner["headers"])
    fake_daraja(monkeypatch)

    stk = _push(client, owner["headers"], item["id"]).get_json()["data"]["mpesa_request"]

    callback = client.post("/api/mpesa/callback", json=_callback(amount=100))
    assert callback.status_code == 200, callback.get_json()

    assert _sale_count(client, owner["headers"]) == 1
    sales = client.get("/api/sales", headers=owner["headers"]).get_json()["data"]["sales"]
    sale = sales[0]
    assert sale["status"] == "posted"
    assert sale["total_amount"] == 100.0
    assert sale["paid_amount"] == 100.0
    assert sale["balance_due"] == 0.0
    assert sale["sale_type"] == "cash"
    assert len(sale["payments"]) == 1
    payment = sale["payments"][0]
    assert payment["payment_method"] == "mpesa"
    assert payment["amount"] == 100.0
    # The Daraja receipt number is what the customer quotes when querying.
    assert payment["reference"] == "QJG7X2K9LM"
    assert _balance(client, owner["headers"], item["id"]) == before - 2

    settled = client.get(
        f"/api/mpesa/requests/{stk['id']}", headers=owner["headers"]
    ).get_json()["data"]["mpesa_request"]
    assert settled["status"] == "completed"
    assert settled["is_terminal"] is True
    assert settled["sale_id"] == sale["id"]
    assert settled["mpesa_receipt"] == "QJG7X2K9LM"


def test_declined_callback_leaves_no_sale(client, owner, monkeypatch):
    item = _create_item(client, owner["headers"])
    _stock_in(client, owner["headers"], item["id"])
    before = _balance(client, owner["headers"], item["id"])
    _configure_mpesa(client, owner["headers"])
    fake_daraja(monkeypatch)

    stk = _push(client, owner["headers"], item["id"]).get_json()["data"]["mpesa_request"]

    callback = client.post(
        "/api/mpesa/callback", json=_callback(result_code=1, amount=0)
    )
    assert callback.status_code == 200, callback.get_json()

    assert _sale_count(client, owner["headers"]) == 0
    assert _balance(client, owner["headers"], item["id"]) == before

    settled = client.get(
        f"/api/mpesa/requests/{stk['id']}", headers=owner["headers"]
    ).get_json()["data"]["mpesa_request"]
    assert settled["status"] == "failed"
    assert settled["is_terminal"] is True
    assert settled["sale_id"] is None
    assert "rejected" in settled["result_desc"]


def test_repeated_callback_cannot_create_a_second_sale(client, owner, monkeypatch):
    item = _create_item(client, owner["headers"])
    _stock_in(client, owner["headers"], item["id"])
    _configure_mpesa(client, owner["headers"])
    fake_daraja(monkeypatch)

    _push(client, owner["headers"], item["id"])
    assert client.post("/api/mpesa/callback", json=_callback()).status_code == 200
    assert client.post("/api/mpesa/callback", json=_callback()).status_code == 200
    assert client.post("/api/mpesa/callback", json=_callback()).status_code == 200

    assert _sale_count(client, owner["headers"]) == 1


def test_callback_for_an_unknown_push_is_acknowledged(client, owner, monkeypatch):
    """Safaricom retries anything that is not answered, so a stale callback must
    still get a 200 and must not create a sale."""
    _configure_mpesa(client, owner["headers"])
    fake_daraja(monkeypatch)

    response = client.post(
        "/api/mpesa/callback", json=_callback(checkout_id="ws_CO_DOES_NOT_EXIST")
    )
    assert response.status_code == 200, response.get_json()
    assert _sale_count(client, owner["headers"]) == 0


def test_callback_amount_mismatch_refuses_to_post_the_sale(client, owner, monkeypatch):
    item = _create_item(client, owner["headers"])
    _stock_in(client, owner["headers"], item["id"])
    before = _balance(client, owner["headers"], item["id"])
    _configure_mpesa(client, owner["headers"])
    fake_daraja(monkeypatch)

    stk = _push(client, owner["headers"], item["id"]).get_json()["data"]["mpesa_request"]
    assert client.post(
        "/api/mpesa/callback", json=_callback(amount=99999)
    ).status_code == 200

    assert _sale_count(client, owner["headers"]) == 0
    assert _balance(client, owner["headers"], item["id"]) == before
    settled = client.get(
        f"/api/mpesa/requests/{stk['id']}", headers=owner["headers"]
    ).get_json()["data"]["mpesa_request"]
    assert settled["status"] == "failed"
    assert "mismatch" in settled["result_desc"].lower()


def test_malformed_callback_body_is_tolerated(client, owner):
    _configure_mpesa(client, owner["headers"])
    for body in ({}, {"Body": {}}, {"Body": {"stkCallback": None}}, {"Body": []}):
        response = client.post("/api/mpesa/callback", json=body)
        assert response.status_code == 200, (body, response.get_json())


# ---------------------------------------------------------------------------
# pending-state management
# ---------------------------------------------------------------------------
def test_pending_request_expires_when_safaricom_never_calls_back(client, owner, monkeypatch):
    item = _create_item(client, owner["headers"])
    _stock_in(client, owner["headers"], item["id"])
    _configure_mpesa(client, owner["headers"])
    fake_daraja(monkeypatch)

    stk = _push(client, owner["headers"], item["id"]).get_json()["data"]["mpesa_request"]

    with client.application.app_context():
        from app.extensions import db
        from app.models import MpesaStkRequest
        from datetime import datetime, timedelta, timezone

        row = db.session.get(MpesaStkRequest, stk["id"])
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
        db.session.commit()

    settled = client.get(
        f"/api/mpesa/requests/{stk['id']}", headers=owner["headers"]
    ).get_json()["data"]["mpesa_request"]
    assert settled["status"] == "timeout"
    assert settled["is_terminal"] is True
    assert _sale_count(client, owner["headers"]) == 0


def test_a_cancelled_push_cannot_be_completed_later(client, owner, monkeypatch):
    """Abandoning a prompt the customer never saw must not sell anything."""
    item = _create_item(client, owner["headers"])
    _stock_in(client, owner["headers"], item["id"])
    _configure_mpesa(client, owner["headers"])
    fake_daraja(monkeypatch)

    stk = _push(client, owner["headers"], item["id"]).get_json()["data"]["mpesa_request"]
    cancelled = client.post(
        f"/api/mpesa/requests/{stk['id']}/cancel", headers=owner["headers"]
    )
    assert cancelled.status_code == 200, cancelled.get_json()
    assert cancelled.get_json()["data"]["mpesa_request"]["status"] == "cancelled"

    assert client.post("/api/mpesa/callback", json=_callback()).status_code == 200
    assert _sale_count(client, owner["headers"]) == 0


# ---------------------------------------------------------------------------
# access control
# ---------------------------------------------------------------------------
def test_push_requires_authentication(client):
    # Deliberately no `owner` fixture: signing up would leave a refresh-token
    # cookie on the client, which authenticates on its own and would mask the
    # point of this test.
    response = client.post("/api/mpesa/stkpush", json={})
    assert response.status_code == 401, response.get_json()


def test_a_push_cannot_be_read_across_businesses(client, owner, monkeypatch):
    item = _create_item(client, owner["headers"])
    _stock_in(client, owner["headers"], item["id"])
    _configure_mpesa(client, owner["headers"])
    fake_daraja(monkeypatch)
    stk = _push(client, owner["headers"], item["id"]).get_json()["data"]["mpesa_request"]

    other = register_business(client, email="other@example.com", business_name="Other Ltd")
    other_headers = auth_headers(other["access_token"])

    assert client.get(
        f"/api/mpesa/requests/{stk['id']}", headers=other_headers
    ).status_code == 404
    assert client.get("/api/mpesa/requests", headers=other_headers).get_json()[
        "data"
    ]["mpesa_requests"] == []


def test_callback_rejects_a_bad_shared_token(client, owner, monkeypatch):
    """The callback is unauthenticated, so the token is the only thing standing
    between a stranger and the ability to post sales."""
    app = client.application
    app.config["MPESA_CALLBACK_TOKEN"] = "expected-token"
    monkeypatch.setitem(app.config, "MPESA_CALLBACK_TOKEN", "expected-token")

    rejected = client.post("/api/mpesa/callback?token=wrong", json=_callback())
    assert rejected.status_code == 401, rejected.get_json()
    assert _sale_count(client, owner["headers"]) == 0

    no_token = client.post("/api/mpesa/callback", json=_callback())
    assert no_token.status_code == 401, no_token.get_json()


def test_callback_accepts_the_configured_shared_token(client, owner, monkeypatch):
    item = _create_item(client, owner["headers"])
    _stock_in(client, owner["headers"], item["id"])
    _configure_mpesa(client, owner["headers"])
    fake_daraja(monkeypatch)
    _push(client, owner["headers"], item["id"])
    monkeypatch.setitem(client.application.config, "MPESA_CALLBACK_TOKEN", "expected-token")

    response = client.post("/api/mpesa/callback?token=expected-token", json=_callback())
    assert response.status_code == 200, response.get_json()
    assert _sale_count(client, owner["headers"]) == 1


# ---------------------------------------------------------------------------
# transport failures
# ---------------------------------------------------------------------------
def test_network_failure_surfaces_as_a_readable_error(client, owner, monkeypatch):
    """A Daraja outage must read as a till message, not a 500."""
    import urllib.error

    item = _create_item(client, owner["headers"])
    _stock_in(client, owner["headers"], item["id"])
    _configure_mpesa(client, owner["headers"])

    def _unreachable(*args, **kwargs):
        raise urllib.error.URLError("no route to host")

    # Patched at the socket rather than at _post_json, so the real transport
    # error handling is what is under test.
    monkeypatch.setattr(mpesa_service.urllib.request, "urlopen", _unreachable)

    response = _push(client, owner["headers"], item["id"])
    assert response.status_code == 422, response.get_json()
    assert "Could not reach Safaricom" in response.get_json()["message"]
    assert _sale_count(client, owner["headers"]) == 0


def test_access_token_is_fetched_once_and_reused(client, owner, monkeypatch):
    item = _create_item(client, owner["headers"])
    _stock_in(client, owner["headers"], item["id"])
    _configure_mpesa(client, owner["headers"])
    calls = fake_daraja(monkeypatch, capture=[])

    _push(client, owner["headers"], item["id"])
    _push(client, owner["headers"], item["id"])

    assert sum(1 for c in calls if "/oauth/" in c["url"]) == 1
    assert sum(1 for c in calls if "stkpush" in c["url"]) == 2


