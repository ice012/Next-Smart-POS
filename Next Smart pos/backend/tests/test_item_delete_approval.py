"""A till-side role can key in a product but not remove one without approval.

The split under test: a Cashier holds `item.write` so the catalog is never the
reason a sale cannot be rung up, but deleting an item is an owner/admin decision
that the API makes on every call rather than the UI making by hiding a button.
"""

import pytest

from app.exceptions import ForbiddenError
from conftest import auth_headers, register_business

CASHIER_PASSWORD = "T1llPass!2026"
OWNER_PASSWORD = "Str0ngPass!1"


def _role_id(app, business_id, name):
    from sqlalchemy import select

    from app.extensions import db
    from app.models import Role

    with app.app_context():
        return db.session.scalar(
            select(Role.id).where(
                Role.business_id == business_id, Role.name == name
            )
        )


def _make_cashier(app, client, owner, email="cashier@example.com"):
    role_id = _role_id(app, owner["business_id"], "Cashier")
    invited = client.post(
        "/api/iam/users",
        headers=owner["headers"],
        json={
            "email": email,
            "first_name": "Till",
            "last_name": "Operator",
            "role_id": str(role_id),
        },
    )
    assert invited.status_code == 201, invited.get_json()
    token = invited.get_json()["data"].get("dev_reset_token")
    reset = client.post(
        "/api/auth/reset-password",
        json={"token": token, "password": CASHIER_PASSWORD},
    )
    assert reset.status_code == 200, reset.get_json()
    login = client.post(
        "/api/auth/login",
        json={"identifier": email, "password": CASHIER_PASSWORD},
    )
    assert login.status_code == 200, login.get_json()
    data = login.get_json()["data"]
    assert data["role"] == "Cashier"
    return {"headers": auth_headers(data["access_token"]), "owner": owner}


def _create_item(client, headers, name="Test Product", **overrides):
    body = {
        "type": "product",
        "name": name,
        "selling_price": 10,
        "cost_price": 5,
        "minimum_stock": 0,
        "is_inventory_tracked": False,
    }
    body.update(overrides)
    response = client.post("/api/items", headers=headers, json=body)
    assert response.status_code == 201, response.get_json()
    return response.get_json()["data"]["item"]


@pytest.fixture()
def cashier(app, client, owner):
    return _make_cashier(app, client, owner)


# ---- the role itself ---------------------------------------------------------


def test_cashier_gained_item_write_but_not_item_delete():
    from app.models import SYSTEM_ROLES

    assert "item.write" in SYSTEM_ROLES["Cashier"]
    assert "item.delete" not in SYSTEM_ROLES["Cashier"]
    # The catalog dropdowns the create form needs, and nothing more.
    assert {"category.read", "uom.read"} <= set(SYSTEM_ROLES["Cashier"])
    assert "category.write" not in SYSTEM_ROLES["Cashier"]


def test_cashier_can_create_and_update_a_product(client, cashier):
    item = _create_item(client, cashier["headers"], name="Till Snacks")
    assert item["name"] == "Till Snacks"

    patched = client.patch(
        f"/api/items/{item['id']}",
        headers=cashier["headers"],
        json={"selling_price": 12.5},
    )
    assert patched.status_code == 200, patched.get_json()
    assert float(patched.get_json()["data"]["item"]["selling_price"]) == 12.5


# ---- delete needs an owner/admin password ------------------------------------


def test_cashier_delete_is_refused_without_approval(client, cashier):
    item = _create_item(client, cashier["headers"])
    response = client.delete(f"/api/items/{item['id']}", headers=cashier["headers"])
    assert response.status_code == 403, response.get_json()
    body = response.get_json()
    assert body["error"] == "ForbiddenError"
    assert "owner or admin" in body["message"]
    # The response tells the client to collect a password rather than just failing.
    assert body["requires_privileged_authorization"] is True
    assert body["action"] == "item.delete"
    # And nothing was removed.
    assert client.get(
        f"/api/items/{item['id']}", headers=cashier["headers"]
    ).status_code == 200


def test_cashier_delete_goes_through_once_a_manager_approves(client, cashier):
    item = _create_item(client, cashier["headers"])

    approval = client.post(
        "/api/iam/privileged-authorizations",
        headers=cashier["headers"],
        json={"action": "item.delete", "password": OWNER_PASSWORD},
    )
    assert approval.status_code == 200, approval.get_json()
    granted = approval.get_json()["data"]
    assert granted["action"] == "item.delete"
    assert granted["approved_by"] == "owner@example.com"

    deleted = client.delete(
        f"/api/items/{item['id']}",
        headers={
            **cashier["headers"],
            "X-Privileged-Token": granted["token"],
        },
    )
    assert deleted.status_code == 204, deleted.get_json()
    assert client.get(
        f"/api/items/{item['id']}", headers=cashier["headers"]
    ).status_code == 404


def test_a_wrong_password_does_not_authorize_anything(client, cashier):
    response = client.post(
        "/api/iam/privileged-authorizations",
        headers=cashier["headers"],
        json={"action": "item.delete", "password": "not-the-password"},
    )
    assert response.status_code == 401, response.get_json()
    assert response.get_json()["error"] == "AuthError"


def test_an_approving_password_is_required(client, cashier):
    response = client.post(
        "/api/iam/privileged-authorizations",
        headers=cashier["headers"],
        json={"action": "item.delete"},
    )
    assert response.status_code == 422, response.get_json()


def test_no_token_is_minted_for_a_read(client, cashier):
    """Reading is never worth a second signature, so it cannot be unlocked."""
    response = client.post(
        "/api/iam/privileged-authorizations",
        headers=cashier["headers"],
        json={"action": "sale.read", "password": OWNER_PASSWORD},
    )
    assert response.status_code == 422, response.get_json()


def test_no_token_is_minted_for_an_action_the_server_has_not_opened_up(client, cashier):
    """The catalog decides what is unlockable, not the client's wish."""
    response = client.post(
        "/api/iam/privileged-authorizations",
        headers=cashier["headers"],
        json={"action": "unicorn.obliterate", "password": OWNER_PASSWORD},
    )
    assert response.status_code == 422, response.get_json()


def test_a_token_for_one_action_does_not_unlock_another(app, client, cashier):
    item = _create_item(client, cashier["headers"])
    token = client.post(
        "/api/iam/privileged-authorizations",
        headers=cashier["headers"],
        json={"action": "item.delete", "password": OWNER_PASSWORD},
    ).get_json()["data"]["token"]

    # A token that exists and is valid is still bound to the action it was
    # minted for, so it cannot be filed under a different one.
    from app.services import privileged_service

    with app.test_request_context(headers={"X-Privileged-Token": token}):
        with pytest.raises(ForbiddenError) as excinfo:
            privileged_service.require_privileged_authorization(
                item["business_id"], "item.write"
            )
    assert "different action" in str(excinfo.value)
    assert token


def test_a_token_minted_in_another_business_does_not_work(app, client, cashier):
    item = _create_item(client, cashier["headers"])
    other = register_business(client, "other-owner@example.com", "Other Co")
    other_token = client.post(
        "/api/iam/privileged-authorizations",
        headers=auth_headers(other["access_token"]),
        json={"action": "item.delete", "password": OWNER_PASSWORD},
    ).get_json()["data"]["token"]

    response = client.delete(
        f"/api/items/{item['id']}",
        headers={**cashier["headers"], "X-Privileged-Token": other_token},
    )
    assert response.status_code == 403, response.get_json()
    assert client.get(
        f"/api/items/{item['id']}", headers=cashier["headers"]
    ).status_code == 200


def test_an_ordinary_access_token_is_not_accepted_as_approval(client, cashier):
    item = _create_item(client, cashier["headers"])
    bearer = cashier["headers"]["Authorization"].split(" ", 1)[1]
    response = client.delete(
        f"/api/items/{item['id']}",
        headers={**cashier["headers"], "X-Privileged-Token": bearer},
    )
    assert response.status_code == 403, response.get_json()


def test_the_privileged_approval_is_audited(app, client, cashier):
    from sqlalchemy import select

    from app.extensions import db
    from app.models import AuditLog

    item = _create_item(client, cashier["headers"])
    approval = client.post(
        "/api/iam/privileged-authorizations",
        headers=cashier["headers"],
        json={"action": "item.delete", "password": OWNER_PASSWORD},
    ).get_json()["data"]
    client.delete(
        f"/api/items/{item['id']}",
        headers={**cashier["headers"], "X-Privileged-Token": approval["token"]},
    )

    with app.app_context():
        events = db.session.scalars(
            select(AuditLog).where(AuditLog.business_id == item["business_id"])
        ).all()
    actions = {e.action for e in events}
    assert "authorize" in actions
    assert "delete" in actions


# ---- owners and admins are unaffected ----------------------------------------


def test_an_owner_deletes_without_any_approval(client, owner):
    item = _create_item(client, owner["headers"])
    assert client.delete(
        f"/api/items/{item['id']}", headers=owner["headers"]
    ).status_code == 204


def test_a_staff_own_password_does_not_unlock_the_delete(client, app, owner):
    """The password has to belong to an owner or admin, not to any account."""
    cashier = _make_cashier(app, client, owner, email="selfish@example.com")
    response = client.post(
        "/api/iam/privileged-authorizations",
        headers=cashier["headers"],
        json={"action": "item.delete", "password": CASHIER_PASSWORD},
    )
    assert response.status_code == 401, response.get_json()


# ---- the rest of the catalog stays permission-gated --------------------------


def test_a_cashier_cannot_create_a_category(client, cashier):
    response = client.post(
        "/api/items/categories", headers=cashier["headers"], json={"name": "Snacks"}
    )
    assert response.status_code == 403, response.get_json()


def test_a_cashier_can_read_categories_and_units(client, cashier):
    assert client.get("/api/items/categories", headers=cashier["headers"]).status_code == 200
    assert client.get("/api/items/uoms", headers=cashier["headers"]).status_code == 200
