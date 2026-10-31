"""Tests for username sign-in and the merged Profile page fields."""

from conftest import auth_headers, register_business


def test_profile_can_set_a_username(owner):
    response = owner["client"].patch(
        "/api/auth/profile",
        json={"username": "ada.lovelace"},
        headers=owner["headers"],
    )
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["data"]["user"]["username"] == "ada.lovelace"


def test_username_is_normalized_to_lowercase(owner):
    response = owner["client"].patch(
        "/api/auth/profile",
        json={"username": "  AdaL  "},
        headers=owner["headers"],
    )
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["data"]["user"]["username"] == "adal"


def test_username_is_exposed_on_the_session(owner):
    client = owner["client"]
    client.patch(
        "/api/auth/profile",
        json={"username": "ada"},
        headers=owner["headers"],
    )
    response = client.get("/api/auth/me", headers=owner["headers"])
    assert response.status_code == 200
    assert response.get_json()["data"]["user"]["username"] == "ada"


def test_login_with_username_and_password(owner):
    owner["client"].patch(
        "/api/auth/profile",
        json={"username": "ada"},
        headers=owner["headers"],
    )
    response = owner["client"].post(
        "/api/auth/login",
        json={"identifier": "ada", "password": "Str0ngPass!1"},
    )
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["data"]["access_token"]


def test_login_with_username_and_pin(owner):
    client = owner["client"]
    client.patch("/api/auth/profile", json={"username": "ada"}, headers=owner["headers"])
    client.post(
        "/api/auth/setup-pin",
        json={"pin": "4821", "current_password": "Str0ngPass!1"},
        headers=owner["headers"],
    )
    response = client.post("/api/auth/login", json={"identifier": "ada", "pin": "4821"})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["data"]["access_token"]


def test_login_still_accepts_the_email_field(owner):
    response = owner["client"].post(
        "/api/auth/login",
        json={"email": "owner@example.com", "password": "Str0ngPass!1"},
    )
    assert response.status_code == 200, response.get_json()


def test_login_with_unknown_username_is_rejected(owner):
    response = owner["client"].post(
        "/api/auth/login",
        json={"identifier": "nobody", "password": "Str0ngPass!1"},
    )
    assert response.status_code == 401


def test_login_requires_an_identifier(client):
    response = client.post("/api/auth/login", json={"password": "Str0ngPass!1"})
    assert response.status_code == 422


def test_username_must_be_unique(client, owner):
    owner["client"].patch(
        "/api/auth/profile", json={"username": "shared"}, headers=owner["headers"]
    )
    second = register_business(client, email="second@example.com", business_name="Second Ltd")
    response = client.patch(
        "/api/auth/profile",
        json={"username": "shared"},
        headers=auth_headers(second["access_token"]),
    )
    assert response.status_code == 409
    assert "already taken" in response.get_json()["message"]


def test_username_can_be_changed_by_its_owner(owner):
    client = owner["client"]
    client.patch("/api/auth/profile", json={"username": "ada"}, headers=owner["headers"])
    response = client.patch(
        "/api/auth/profile", json={"username": "ada2"}, headers=owner["headers"]
    )
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["data"]["user"]["username"] == "ada2"


def test_invalid_usernames_are_rejected(owner):
    for value in ("ab", "has space", "a" * 31, "-leading"):
        response = owner["client"].patch(
            "/api/auth/profile", json={"username": value}, headers=owner["headers"]
        )
        assert response.status_code == 422, value


def test_profile_save_without_a_username_leaves_it_unset(owner):
    response = owner["client"].patch(
        "/api/auth/profile",
        json={"username": None, "first_name": "Augusta"},
        headers=owner["headers"],
    )
    assert response.status_code == 200, response.get_json()
    user = response.get_json()["data"]["user"]
    assert user["username"] is None
    assert user["first_name"] == "Augusta"


def test_blank_phone_from_a_form_is_accepted(owner):
    # Profile forms post their text inputs whether or not the user filled them
    # in, so an empty phone must not trip the 1-character minimum.
    response = owner["client"].patch(
        "/api/auth/profile",
        json={"first_name": "Ada", "last_name": "Lovelace", "phone": ""},
        headers=owner["headers"],
    )
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["data"]["user"]["phone"] is None


def test_blank_username_and_phone_still_let_a_username_be_set(owner):
    response = owner["client"].patch(
        "/api/auth/profile",
        json={"username": "ada", "phone": "   "},
        headers=owner["headers"],
    )
    assert response.status_code == 200, response.get_json()
    user = response.get_json()["data"]["user"]
    assert user["username"] == "ada"
    assert user["phone"] is None


def test_a_real_phone_is_still_stored(owner):
    response = owner["client"].patch(
        "/api/auth/profile",
        json={"phone": "+254700000000"},
        headers=owner["headers"],
    )
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["data"]["user"]["phone"] == "+254700000000"
