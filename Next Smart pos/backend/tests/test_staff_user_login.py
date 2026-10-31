"""Tests for an owner setting a sign-in username and temporary password on a staff user.

Reproduces the 422 the front-end hit when the edit form sent ``username`` and
``password`` to ``PATCH /api/iam/users/<id>`` -- fields the old
``UserUpdateSchema`` rejected because of ``extra="forbid"``.
"""

from conftest import auth_headers, register_business

from app.extensions import db
from app.models import Role, User
from sqlalchemy import select


def _role_id(app, business_id, name):
    with app.app_context():
        return db.session.scalar(
            select(Role.id).where(
                Role.business_id == business_id, Role.name == name
            )
        )


def _invite(app, client, owner, email, **extra):
    """Invite a staff member and return (user_id, dev_reset_token)."""
    role_id = _role_id(app, owner["business_id"], "Employee")
    payload = {
        "first_name": "Sam",
        "last_name": "Stock",
        "email": email,
        "role_id": str(role_id),
    }
    payload.update(extra)
    resp = client.post("/api/iam/users", headers=owner["headers"], json=payload)
    assert resp.status_code == 201, resp.get_json()
    membership = resp.get_json()["data"]["membership"]
    token = resp.get_json()["data"].get("dev_reset_token")
    return membership["user"]["id"], token


def _activate(client, dev_reset_token, password="TempPass!1"):
    """Activate an invited user via the dev reset token (sets a password)."""
    resp = client.post(
        "/api/auth/reset-password",
        json={"token": dev_reset_token, "password": password},
    )
    assert resp.status_code == 200, resp.get_json()


# ---- username on edit -------------------------------------------------------


def test_owner_can_set_username_when_editing_user(client, owner, app):
    user_id, token = _invite(app, client, owner, "sam@example.com")
    _activate(client, token)

    resp = client.patch(
        f"/api/iam/users/{user_id}",
        headers=owner["headers"],
        json={"username": "sam.stock"},
    )
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["data"]["user"]["user"]["username"] == "sam.stock"


def test_owner_username_is_normalized_to_lowercase(client, owner, app):
    user_id, token = _invite(app, client, owner, "sam@example.com")
    _activate(client, token)

    resp = client.patch(
        f"/api/iam/users/{user_id}",
        headers=owner["headers"],
        json={"username": "  Sam.Stock  "},
    )
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["data"]["user"]["user"]["username"] == "sam.stock"


def test_username_taken_returns_conflict(client, owner, app):
    owner["client"].patch(
        "/api/auth/profile",
        json={"username": "taken"},
        headers=owner["headers"],
    )

    first_id, first_token = _invite(app, client, owner, "first@example.com")
    _activate(client, first_token)
    second_id, second_token = _invite(app, client, owner, "second@example.com")
    _activate(client, second_token)

    resp = client.patch(
        f"/api/iam/users/{second_id}",
        headers=owner["headers"],
        json={"username": "taken"},
    )
    assert resp.status_code == 409, resp.get_json()
    assert "taken" in resp.get_json()["message"].lower()


# ---- temporary password on edit --------------------------------------------


def test_owner_can_set_temporary_password(client, owner, app):
    user_id, token = _invite(app, client, owner, "sam@example.com")
    _activate(client, token)

    resp = client.patch(
        f"/api/iam/users/{user_id}",
        headers=owner["headers"],
        json={"password": "Br4ndNew!Pass"},
    )
    assert resp.status_code == 200, resp.get_json()

    login = client.post(
        "/api/auth/login",
        json={"identifier": "sam@example.com", "password": "Br4ndNew!Pass"},
    )
    assert login.status_code == 200, login.get_json()


def test_owner_can_set_username_and_password_together(client, owner, app):
    user_id, token = _invite(app, client, owner, "sam@example.com")
    _activate(client, token)

    client.patch(
        f"/api/iam/users/{user_id}",
        headers=owner["headers"],
        json={"username": "sam.stock", "password": "Br4ndNew!Pass"},
    )
    resp = client.post(
        "/api/auth/login",
        json={"identifier": "sam.stock", "password": "Br4ndNew!Pass"},
    )
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["data"]["access_token"]


def test_old_password_fails_after_owner_sets_new_one(client, owner, app):
    user_id, token = _invite(app, client, owner, "sam@example.com")
    old_password = "TempPass!1"
    _activate(client, token, password=old_password)

    client.patch(
        f"/api/iam/users/{user_id}",
        headers=owner["headers"],
        json={"password": "Br4ndNew!Pass"},
    )
    resp = client.post(
        "/api/auth/login",
        json={"identifier": "sam@example.com", "password": old_password},
    )
    assert resp.status_code == 401


# ---- validation -------------------------------------------------------------


def test_weak_password_rejected_on_edit(client, owner, app):
    user_id, token = _invite(app, client, owner, "sam@example.com")
    _activate(client, token)

    resp = client.patch(
        f"/api/iam/users/{user_id}",
        headers=owner["headers"],
        json={"password": "weak"},
    )
    assert resp.status_code == 422, resp.get_json()


def test_invalid_username_rejected_on_edit(client, owner, app):
    user_id, token = _invite(app, client, owner, "sam@example.com")
    _activate(client, token)

    resp = client.patch(
        f"/api/iam/users/{user_id}",
        headers=owner["headers"],
        json={"username": "bad user!"},
    )
    assert resp.status_code == 422, resp.get_json()


# ---- invite with username + temporary password ------------------------------


def test_invite_with_username_and_password(client, owner, app):
    role_id = _role_id(app, owner["business_id"], "Employee")
    resp = client.post(
        "/api/iam/users",
        headers=owner["headers"],
        json={
            "first_name": "Sam",
            "last_name": "Stock",
            "email": "sam@example.com",
            "role_id": str(role_id),
            "username": "sam.stock",
            "password": "TempPass!1",
        },
    )
    assert resp.status_code == 201, resp.get_json()
    membership = resp.get_json()["data"]["membership"]
    assert membership["user"]["username"] == "sam.stock"
    assert resp.get_json()["data"].get("dev_reset_token")


def test_invite_without_password_still_works(client, owner, app):
    """Backward compatibility: omitting password falls back to a random one."""
    role_id = _role_id(app, owner["business_id"], "Employee")
    resp = client.post(
        "/api/iam/users",
        headers=owner["headers"],
        json={
            "first_name": "Sam",
            "last_name": "Stock",
            "email": "sam@example.com",
            "role_id": str(role_id),
        },
    )
    assert resp.status_code == 201, resp.get_json()
    assert resp.get_json()["data"].get("dev_reset_token")


# ---- activation on password set --------------------------------------------


def test_setting_password_via_patch_activates_the_user(client, owner, app):
    """An invited user who has not used their reset token must still be able to
    sign in once the owner sets a working password on them."""
    user_id, _token = _invite(app, client, owner, "sam@example.com")

    with app.app_context():
        user = db.session.get(User, user_id)
        assert user.is_active is False

    resp = client.patch(
        f"/api/iam/users/{user_id}",
        headers=owner["headers"],
        json={"username": "sam.stock", "password": "Br4ndNew!Pass"},
    )
    assert resp.status_code == 200, resp.get_json()

    with app.app_context():
        user = db.session.get(User, user_id)
        assert user.is_active is True

    login = client.post(
        "/api/auth/login",
        json={"identifier": "sam.stock", "password": "Br4ndNew!Pass"},
    )
    assert login.status_code == 200, login.get_json()


def test_invite_with_password_activates_the_user(client, owner, app):
    """Inviting a user with an owner-chosen password should let them sign in
    without needing the reset-token activation step."""
    role_id = _role_id(app, owner["business_id"], "Employee")
    resp = client.post(
        "/api/iam/users",
        headers=owner["headers"],
        json={
            "first_name": "Sam",
            "last_name": "Stock",
            "email": "sam@example.com",
            "role_id": str(role_id),
            "username": "sam.stock",
            "password": "TempPass!1",
        },
    )
    assert resp.status_code == 201, resp.get_json()
    user_id = resp.get_json()["data"]["membership"]["user"]["id"]

    with app.app_context():
        user = db.session.get(User, user_id)
        assert user.is_active is True

    login = client.post(
        "/api/auth/login",
        json={"identifier": "sam.stock", "password": "TempPass!1"},
    )
    assert login.status_code == 200, login.get_json()


def test_invite_without_password_keeps_user_inactive(client, owner, app):
    """Backward compatibility: an invite with no owner-set password must still
    require the reset-token activation step."""
    role_id = _role_id(app, owner["business_id"], "Employee")
    resp = client.post(
        "/api/iam/users",
        headers=owner["headers"],
        json={
            "first_name": "Sam",
            "last_name": "Stock",
            "email": "sam@example.com",
            "role_id": str(role_id),
        },
    )
    assert resp.status_code == 201, resp.get_json()
    user_id = resp.get_json()["data"]["membership"]["user"]["id"]

    with app.app_context():
        user = db.session.get(User, user_id)
        assert user.is_active is False
