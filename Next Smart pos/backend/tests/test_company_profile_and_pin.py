"""Tests for sign-in PIN setup and the company profile endpoints."""

import io

from conftest import auth_headers, register_business


def test_setup_pin_requires_authentication(client):
    response = client.post("/api/auth/setup-pin", json={"pin": "1234", "current_password": "x"})
    assert response.status_code == 401


def test_setup_pin_stores_hashed_pin_and_enables_pin_login(owner):
    client = owner["client"]
    headers = owner["headers"]

    created = client.post(
        "/api/auth/setup-pin",
        json={"pin": "4821", "current_password": "Str0ngPass!1"},
        headers=headers,
    )
    assert created.status_code == 200, created.get_json()
    assert created.get_json()["data"]["user"]["has_pin"] is True

    login = client.post(
        "/api/auth/login",
        json={"email": "owner@example.com", "pin": "4821"},
    )
    assert login.status_code == 200, login.get_json()
    assert login.get_json()["data"]["access_token"]


def test_setup_pin_rejects_wrong_current_password(owner):
    response = owner["client"].post(
        "/api/auth/setup-pin",
        json={"pin": "4821", "current_password": "WrongPass!9"},
        headers=owner["headers"],
    )
    assert response.status_code == 401


def test_setup_pin_rejects_non_numeric_pin(owner):
    response = owner["client"].post(
        "/api/auth/setup-pin",
        json={"pin": "abcd", "current_password": "Str0ngPass!1"},
        headers=owner["headers"],
    )
    assert response.status_code == 422


def test_login_with_wrong_pin_is_rejected(owner):
    client = owner["client"]
    client.post(
        "/api/auth/setup-pin",
        json={"pin": "4821", "current_password": "Str0ngPass!1"},
        headers=owner["headers"],
    )
    response = client.post("/api/auth/login", json={"email": "owner@example.com", "pin": "9999"})
    assert response.status_code == 401


def test_login_requires_password_or_pin(client):
    response = client.post("/api/auth/login", json={"email": "owner@example.com"})
    assert response.status_code == 422


def test_password_login_still_works_without_a_pin(owner):
    response = owner["client"].post(
        "/api/auth/login",
        json={"email": "owner@example.com", "password": "Str0ngPass!1"},
    )
    assert response.status_code == 200


def test_business_info_exposes_contact_fields(owner):
    response = owner["client"].get("/api/business/info", headers=owner["headers"])
    assert response.status_code == 200, response.get_json()
    business = response.get_json()["data"]["business"]
    for field in ("email", "phone", "website", "address_line1", "city", "postal_code"):
        assert field in business


def test_business_info_patch_persists_contact_and_address(owner):
    payload = {
        "email": "accounts@retail.example.com",
        "phone": "+44 20 7946 0000",
        "website": "https://retail.test",
        "address_line1": "12 Market Street",
        "city": "London",
        "state": "Greater London",
        "postal_code": "SW1A 1AA",
        "default_country": "GB",
    }
    patched = owner["client"].patch(
        "/api/business/info", json=payload, headers=owner["headers"]
    )
    assert patched.status_code == 200, patched.get_json()
    business = patched.get_json()["data"]["business"]
    assert business["city"] == "London"
    assert business["address_line1"] == "12 Market Street"

    reloaded = owner["client"].get("/api/business/info", headers=owner["headers"])
    assert reloaded.get_json()["data"]["business"]["city"] == "London"


def test_business_info_patch_can_clear_a_field(owner):
    owner["client"].patch(
        "/api/business/info", json={"city": "Leeds"}, headers=owner["headers"]
    )
    cleared = owner["client"].patch(
        "/api/business/info", json={"city": None}, headers=owner["headers"]
    )
    assert cleared.status_code == 200, cleared.get_json()
    assert cleared.get_json()["data"]["business"]["city"] is None


def test_business_info_patch_rejects_unknown_fields(owner):
    response = owner["client"].patch(
        "/api/business/info", json={"not_a_field": "x"}, headers=owner["headers"]
    )
    assert response.status_code == 422


def _png_bytes():
    # Minimal valid 1x1 PNG.
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
        b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def test_business_logo_upload_and_delete(owner):
    client = owner["client"]
    headers = owner["headers"]

    response = client.post(
        "/api/business/logo",
        data={"logo": (io.BytesIO(_png_bytes()), "logo.png")},
        headers=headers,
        content_type="multipart/form-data",
    )
    assert response.status_code == 200, response.get_json()
    logo_url = response.get_json()["data"]["logo"]
    assert logo_url and "/uploads/logos/" in logo_url

    fetched = client.get(logo_url)
    assert fetched.status_code == 200
    assert fetched.data[:8] == b"\x89PNG\r\n\x1a\n"

    removed = client.delete("/api/business/logo", headers=headers)
    assert removed.status_code == 200, removed.get_json()
    assert removed.get_json()["data"]["logo"] is None


def test_business_logo_rejects_non_image_extension(owner):
    response = owner["client"].post(
        "/api/business/logo",
        data={"logo": (io.BytesIO(b"not-an-image"), "payload.exe")},
        headers=owner["headers"],
        content_type="multipart/form-data",
    )
    assert response.status_code == 422


def test_business_logo_rejects_oversized_file(owner):
    oversized = b"\x89PNG\r\n\x1a\n" + b"0" * (3 * 1024 * 1024)
    response = owner["client"].post(
        "/api/business/logo",
        data={"logo": (io.BytesIO(oversized), "big.png")},
        headers=owner["headers"],
        content_type="multipart/form-data",
    )
    assert response.status_code in (413, 422)


def test_cors_preflight_succeeds_for_the_new_endpoints(app, owner):
    client = owner["client"]
    for path, method in (
        ("/api/business/info", "PATCH"),
        ("/api/business/logo", "POST"),
        ("/api/auth/setup-pin", "POST"),
    ):
        response = client.options(
            path,
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert response.status_code == 200, f"preflight failed for {path}"
        assert (
            response.headers.get("Access-Control-Allow-Origin")
            == "http://localhost:5173"
        )
