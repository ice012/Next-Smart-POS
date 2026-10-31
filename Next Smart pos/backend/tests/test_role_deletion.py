"""Role deletion, including roles the platform seeded.

The seeded roles used to be undeletable, which was a blunt instrument: a business
could neither retire a built-in role it did not use nor remove a custom role that
had been created with a confusingly similar name. These tests pin the behaviour
that replaced it, and more importantly the guard that makes it safe.

`business_users.role_id` is declared ON DELETE RESTRICT, but this app never turns
on SQLite's `PRAGMA foreign_keys`, so the database will happily accept a delete
that orphans memberships. The service has to refuse it, and these tests exist so
that the guard cannot be dropped by accident.
"""

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import BusinessUser, Role

from conftest import auth_headers, register_business


# ---- helpers ----------------------------------------------------------------


def _role_id(app, business_id, name):
    with app.app_context():
        return db.session.scalar(
            select(Role.id).where(
                Role.business_id == business_id, Role.name == name
            )
        )


def _role_exists(app, business_id, role_id):
    with app.app_context():
        return (
            db.session.get(Role, role_id) is not None
            and str(db.session.get(Role, role_id).business_id) == str(business_id)
        )


def _membership_count(app, business_id, role_id):
    with app.app_context():
        return len(
            db.session.scalars(
                select(BusinessUser).where(
                    BusinessUser.business_id == business_id,
                    BusinessUser.role_id == role_id,
                )
            ).all()
        )


def _create_staff(app, client, owner, role_id, email):
    created = client.post(
        "/api/iam/users",
        headers=owner["headers"],
        json={
            "first_name": "Sam",
            "last_name": "Stock",
            "email": email,
            "role_id": str(role_id),
        },
    )
    assert created.status_code == 201, created.get_json()
    membership = created.get_json()["data"]["membership"]
    return membership["user"]["id"]


# ---- the core requirement ----------------------------------------------------


def test_owner_can_delete_a_custom_role(client, owner, app):
    created = client.post(
        "/api/iam/roles",
        headers=owner["headers"],
        json={"name": "Night Auditor", "permission_names": ["sale.read"]},
    )
    assert created.status_code == 201, created.get_json()
    role = created.get_json()["data"]["role"]

    deleted = client.delete(f"/api/iam/roles/{role['id']}", headers=owner["headers"])
    assert deleted.status_code == 204, deleted.get_json()

    assert not _role_exists(app, owner["business_id"], role["id"])


def test_owner_can_delete_a_seeded_system_role(client, owner, app):
    role_id = _role_id(app, owner["business_id"], "Accountant")
    assert role_id is not None, "system roles must be seeded per business"

    deleted = client.delete(f"/api/iam/roles/{role_id}", headers=owner["headers"])
    assert deleted.status_code == 204, deleted.get_json()
    assert not _role_exists(app, owner["business_id"], role_id)


def test_deleting_a_role_removes_its_permission_grants(client, owner, app):
    role_id = _role_id(app, owner["business_id"], "Accountant")
    assert client.delete(
        f"/api/iam/roles/{role_id}", headers=owner["headers"]
    ).status_code == 204

    with app.app_context():
        from app.models import RolePermission

        leftover = db.session.scalars(
            select(RolePermission).where(RolePermission.role_id == role_id)
        ).all()
    assert leftover == [], "permission grants must be cleaned up, not orphaned"


# ---- the guard that makes the above safe -------------------------------------


def test_cannot_delete_a_role_that_still_has_staff(client, owner, app):
    role_id = _role_id(app, owner["business_id"], "Cashier")
    _create_staff(app, client, owner, role_id, "till@example.com")

    deleted = client.delete(f"/api/iam/roles/{role_id}", headers=owner["headers"])
    assert deleted.status_code == 409, deleted.get_json()
    assert "assigned" in deleted.get_json()["message"].lower()
    assert _role_exists(app, owner["business_id"], role_id)


def test_refused_delete_leaves_the_membership_intact(client, owner, app):
    """The failure mode this all guards: a member with a dangling role_id.

    Losing every permission silently is far worse than a refused delete, so the
    membership must be exactly as it was afterwards.
    """
    role_id = _role_id(app, owner["business_id"], "Cashier")
    user_id = _create_staff(app, client, owner, role_id, "till@example.com")

    client.delete(f"/api/iam/roles/{role_id}", headers=owner["headers"])

    with app.app_context():
        membership = db.session.scalar(
            select(BusinessUser).where(
                BusinessUser.business_id == owner["business_id"],
                BusinessUser.user_id == user_id,
            )
        )
        assert membership is not None
        assert str(membership.role_id) == str(role_id)
        assert db.session.get(Role, role_id) is not None


def test_becomes_deletable_once_the_last_member_is_reassigned(client, owner, app):
    role_id = _role_id(app, owner["business_id"], "Cashier")
    user_id = _create_staff(app, client, owner, role_id, "till@example.com")
    other_role = _role_id(app, owner["business_id"], "Employee")

    client.patch(
        f"/api/iam/users/{user_id}",
        headers=owner["headers"],
        json={"role_id": str(other_role)},
    )
    assert _membership_count(app, owner["business_id"], role_id) == 0

    assert client.delete(
        f"/api/iam/roles/{role_id}", headers=owner["headers"]
    ).status_code == 204


def test_cannot_delete_the_callers_own_role(client, owner, app):
    """Deleting your own role would strip your access mid-request."""
    with app.app_context():
        own_role_id = db.session.scalar(
            select(BusinessUser.role_id).where(
                BusinessUser.business_id == owner["business_id"],
                BusinessUser.user_id == owner["user_id"],
            )
        )
    assert own_role_id is not None

    deleted = client.delete(
        f"/api/iam/roles/{own_role_id}", headers=owner["headers"]
    )
    assert deleted.status_code == 409, deleted.get_json()
    assert _role_exists(app, owner["business_id"], own_role_id)


# ---- boundaries --------------------------------------------------------------


def test_cannot_delete_a_role_from_another_business(client, owner, app):
    other = register_business(client, "other-owner@example.com", "Other Retail Ltd")
    other_role_id = _role_id(app, other["business"]["id"], "Accountant")
    assert other_role_id is not None

    deleted = client.delete(
        f"/api/iam/roles/{other_role_id}", headers=owner["headers"]
    )
    # Scoped as "not found" rather than "forbidden", so the endpoint cannot be
    # used to probe which role ids exist in a tenant you do not own.
    assert deleted.status_code == 404, deleted.get_json()
    assert _role_exists(app, other["business"]["id"], other_role_id)


def test_cannot_delete_a_missing_role(client, owner):
    import uuid

    deleted = client.delete(
        f"/api/iam/roles/{uuid.uuid4()}", headers=owner["headers"]
    )
    assert deleted.status_code == 404, deleted.get_json()


def test_role_delete_requires_the_permission(client, owner, app):
    """A member without role.delete must not be able to remove roles.

    The Cashier is the useful case: it can reach the roles page and read roles,
    but deleting one is an owner action.
    """
    role_id = _role_id(app, owner["business_id"], "Cashier")
    invited = client.post(
        "/api/iam/users",
        headers=owner["headers"],
        json={
            "first_name": "Till",
            "last_name": "Operator",
            "email": "till-3@example.com",
            "role_id": str(role_id),
        },
    )
    assert invited.status_code == 201, invited.get_json()
    token = invited.get_json()["data"].get("dev_reset_token")
    assert token, "a dev invite must return a reset token"
    assert client.post(
        "/api/auth/reset-password",
        json={"token": token, "password": "Str0ngPass!1"},
    ).status_code == 200
    login = client.post(
        "/api/auth/login",
        json={"identifier": "till-3@example.com", "password": "Str0ngPass!1"},
    )
    assert login.status_code == 200, login.get_json()
    cashier_headers = auth_headers(login.get_json()["data"]["access_token"])

    accountant_id = _role_id(app, owner["business_id"], "Accountant")
    deleted = client.delete(
        f"/api/iam/roles/{accountant_id}", headers=cashier_headers
    )
    assert deleted.status_code == 403, deleted.get_json()
    assert _role_exists(app, owner["business_id"], accountant_id)


def test_deleted_role_no_longer_appears_in_the_role_list(client, owner, app):
    role_id = _role_id(app, owner["business_id"], "Accountant")
    assert client.delete(
        f"/api/iam/roles/{role_id}", headers=owner["headers"]
    ).status_code == 204

    roles = client.get("/api/iam/roles", headers=owner["headers"]).get_json()["data"][
        "roles"
    ]
    assert str(role_id) not in {r["id"] for r in roles}


@pytest.mark.parametrize("role_name", ["Accountant", "Cashier", "Employee"])
def test_system_roles_stay_deletable_individually(client, owner, app, role_name):
    """Each seeded role is on its own; deleting one must not cascade to others."""
    role_id = _role_id(app, owner["business_id"], role_name)
    assert client.delete(
        f"/api/iam/roles/{role_id}", headers=owner["headers"]
    ).status_code == 204

    remaining = {
        r["name"]
        for r in client.get("/api/iam/roles", headers=owner["headers"]).get_json()[
            "data"
        ]["roles"]
    }
    assert "Business Owner" in remaining
    assert role_name not in remaining
