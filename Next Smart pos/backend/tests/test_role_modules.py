"""The module tree is the contract the role editor renders from, and the flag
that puts a module behind an owner's password is enforced by the API rather than
by the UI.

Three things are under test:

1. The module tree and the permission catalog describe the same thing, so a
   role can be built entirely out of module checkboxes without losing a
   permission or inventing one the API has never heard of.
2. ``requires_owner_approval`` is enforced on the route, including for a role
   that already holds the permission — that is the whole point of the flag.
3. An owner/admin PIN works everywhere a password does, because on a shared
   counter nobody is going to type a full password.
"""

import pytest

from app.models.iam import PERMISSION_CATALOG, SYSTEM_ROLES
from app.models.module_catalog import (
    MODULES_BY_CODE,
    MODULE_TREE,
    MUTATION_MODULE,
    PERMISSION_MODULE,
    modules_for_permissions,
    serialize_tree,
)
from conftest import auth_headers, register_business

CASHIER_PASSWORD = "T1llPass!2026"
OWNER_PASSWORD = "Str0ngPass!1"
OWNER_PIN = "9182"


def _role_id(app, business_id, name):
    from sqlalchemy import select

    from app.extensions import db
    from app.models import Role

    with app.app_context():
        return db.session.scalar(
            select(Role.id).where(Role.business_id == business_id, Role.name == name)
        )


def _make_staff(app, client, owner, role_name, email, password=CASHIER_PASSWORD):
    role_id = _role_id(app, owner["business_id"], role_name)
    invited = client.post(
        "/api/iam/users",
        headers=owner["headers"],
        json={
            "email": email,
            "first_name": "Test",
            "last_name": "Staff",
            "role_id": str(role_id),
        },
    )
    assert invited.status_code == 201, invited.get_json()
    token = invited.get_json()["data"].get("dev_reset_token")
    assert client.post(
        "/api/auth/reset-password",
        json={"token": token, "password": password},
    ).status_code == 200
    login = client.post(
        "/api/auth/login", json={"identifier": email, "password": password}
    )
    assert login.status_code == 200, login.get_json()
    return auth_headers(login.get_json()["data"]["access_token"])


@pytest.fixture()
def cashier(app, client, owner):
    return _make_staff(app, client, owner, "Cashier", "cashier@example.com")


# ---- the catalog itself -------------------------------------------------------


def test_every_permission_belongs_to_a_module():
    """Otherwise the role editor silently drops it: no checkbox, no grant."""
    catalog = {name for name, *_rest in PERMISSION_CATALOG}
    assert catalog == set(PERMISSION_MODULE)


def test_every_module_permission_exists_in_the_catalog():
    for code, module in MODULES_BY_CODE.items():
        unknown = [p for p in module["permissions"] if p not in PERMISSION_MODULE]
        assert not unknown, f"{code} names permissions the catalog lacks: {unknown}"


def test_the_five_groups_are_present_in_order():
    assert [g["code"] for g in MODULE_TREE] == [
        "sales",
        "products",
        "finance",
        "business",
        "reports_admin",
    ]
    assert [g["label"] for g in serialize_tree()] == [
        "Sales",
        "Products",
        "Finance",
        "Business",
        "Reports & Admin",
    ]


def test_module_codes_are_unique():
    codes = [m["code"] for g in MODULE_TREE for m in g["modules"]]
    assert len(codes) == len(set(codes))


def test_sales_carries_the_point_of_sale():
    sales = next(g for g in MODULE_TREE if g["code"] == "sales")
    pos = next(m for m in sales["modules"] if m["code"] == "sales.pos")
    # A till that cannot take payment cannot complete a checkout.
    assert {"sale.read", "sale.write", "payment.write"} <= set(pos["permissions"])


def test_reads_are_never_mutations():
    for name in MUTATION_MODULE:
        assert name.split(".", 1)[1] != "read", name


def test_system_roles_only_name_permissions_that_exist():
    known = {name for name, *_rest in PERMISSION_CATALOG}
    for role, names in SYSTEM_ROLES.items():
        unknown = [n for n in names if n not in known]
        assert not unknown, f"{role} names permissions the catalog lacks: {unknown}"


# ---- the tree is served, not copied -------------------------------------------


def test_the_tree_is_served_to_a_role_holder(client, owner):
    response = client.get("/api/iam/permission-modules", headers=owner["headers"])
    assert response.status_code == 200, response.get_json()
    groups = response.get_json()["data"]["groups"]
    assert [g["label"] for g in groups] == [
        "Sales",
        "Products",
        "Finance",
        "Business",
        "Reports & Admin",
    ]
    assert groups[0]["modules"][0]["code"] == "sales.pos"


def test_the_tree_needs_role_read(client, cashier):
    response = client.get("/api/iam/permission-modules", headers=cashier)
    assert response.status_code == 403, response.get_json()


# ---- the admin-required flag --------------------------------------------------


def _flag_module(app, client, owner, role_name, module_code):
    role_id = _role_id(app, owner["business_id"], role_name)
    return client.patch(
        f"/api/iam/roles/{role_id}",
        headers=owner["headers"],
        json={"requires_owner_approval": {module_code: True}},
    )


def _create_item(client, headers, name="Widget"):
    response = client.post(
        "/api/items",
        headers=headers,
        json={
            "type": "product",
            "name": name,
            "selling_price": 10,
            "cost_price": 5,
            "minimum_stock": 0,
            "is_inventory_tracked": False,
        },
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()["data"]["item"]


def test_an_unflagged_role_writes_without_approval(client, cashier):
    """The default: a role's own permission is the whole answer."""
    assert _create_item(client, cashier, name="Unflagged")["name"] == "Unflagged"


def test_flagging_a_module_persists_on_the_role(app, client, owner):
    response = _flag_module(app, client, owner, "Cashier", "products.items")
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["data"]["role"]["requires_owner_approval"] == {
        "products.items": True
    }


def test_flagging_a_module_stops_an_otherwise_permitted_write(client, app, owner):
    cashier = _make_staff(app, client, owner, "Cashier", "gated@example.com")
    _create_item(client, cashier, name="Before")

    assert _flag_module(app, client, owner, "Cashier", "products.items").status_code == 200

    # The role still holds `item.write`. The flag is what changed.
    denied = client.post(
        "/api/items",
        headers=cashier,
        json={
            "type": "product",
            "name": "After",
            "selling_price": 10,
            "cost_price": 5,
            "minimum_stock": 0,
            "is_inventory_tracked": False,
        },
    )
    assert denied.status_code == 403, denied.get_json()
    body = denied.get_json()
    assert body["requires_privileged_authorization"] is True
    assert body["action"] == "item.write"
    assert body["action_label"]


def test_reading_a_flagged_module_stays_open(client, app, owner):
    cashier = _make_staff(app, client, owner, "Cashier", "reader@example.com")
    item = _create_item(client, cashier, name="Readable")
    assert _flag_module(app, client, owner, "Cashier", "products.items").status_code == 200

    # Gating a module must not make it unusable: the point is to check changes,
    # not to lock the price list away from the till.
    assert client.get("/api/items", headers=cashier).status_code == 200
    assert client.get(f"/api/items/{item['id']}", headers=cashier).status_code == 200


def test_a_flagged_write_goes_through_once_a_manager_approves(client, app, owner):
    cashier = _make_staff(app, client, owner, "Cashier", "approved@example.com")
    assert _flag_module(app, client, owner, "Cashier", "products.items").status_code == 200

    approval = client.post(
        "/api/iam/privileged-authorizations",
        headers=cashier,
        json={"action": "item.write", "password": OWNER_PASSWORD},
    )
    assert approval.status_code == 200, approval.get_json()
    token = approval.get_json()["data"]["token"]

    created = client.post(
        "/api/items",
        headers={**cashier, "X-Privileged-Token": token},
        json={
            "type": "product",
            "name": "Approved",
            "selling_price": 10,
            "cost_price": 5,
            "minimum_stock": 0,
            "is_inventory_tracked": False,
        },
    )
    assert created.status_code == 201, created.get_json()


def test_a_token_for_the_wrong_action_does_not_unlock_the_flag(client, app, owner):
    cashier = _make_staff(app, client, owner, "Cashier", "swap@example.com")
    assert _flag_module(app, client, owner, "Cashier", "products.items").status_code == 200

    # A real, valid token — for a different action. Scoping it to one action is
    # what stops a manager signing off a price change from also signing off a
    # deletion with the same five minutes of authority.
    token = client.post(
        "/api/iam/privileged-authorizations",
        headers=cashier,
        json={"action": "customer.delete", "password": OWNER_PASSWORD},
    ).get_json()["data"]["token"]

    denied = client.post(
        "/api/items",
        headers={**cashier, "X-Privileged-Token": token},
        json={
            "type": "product",
            "name": "Swapped",
            "selling_price": 10,
            "cost_price": 5,
            "minimum_stock": 0,
            "is_inventory_tracked": False,
        },
    )
    assert denied.status_code == 403, denied.get_json()


def test_unflagging_the_module_restores_the_role(app, client, owner):
    cashier = _make_staff(app, client, owner, "Cashier", "unflag@example.com")
    role_id = _role_id(app, owner["business_id"], "Cashier")
    assert _flag_module(app, client, owner, "Cashier", "products.items").status_code == 200

    blocked = client.post(
        "/api/items",
        headers=cashier,
        json={
            "type": "product",
            "name": "Blocked",
            "selling_price": 10,
            "cost_price": 5,
            "minimum_stock": 0,
            "is_inventory_tracked": False,
        },
    )
    assert blocked.status_code == 403, blocked.get_json()

    assert client.patch(
        f"/api/iam/roles/{role_id}",
        headers=owner["headers"],
        json={"requires_owner_approval": {}},
    ).status_code == 200
    assert _create_item(client, cashier, name="Unblocked")["name"] == "Unblocked"


def test_an_unknown_module_code_is_refused(app, client, owner):
    role_id = _role_id(app, owner["business_id"], "Cashier")
    response = client.patch(
        f"/api/iam/roles/{role_id}",
        headers=owner["headers"],
        json={"requires_owner_approval": {"products.unicorns": True}},
    )
    assert response.status_code == 422, response.get_json()


def test_a_new_role_can_be_built_from_modules_alone(client, owner):
    """The whole point of the screen: no permission names typed by hand."""
    tree = client.get("/api/iam/permission-modules", headers=owner["headers"]).get_json()["data"]["groups"]
    pos = next(
        m for g in tree if g["code"] == "sales" for m in g["modules"] if m["code"] == "sales.pos"
    )
    response = client.post(
        "/api/iam/roles",
        headers=owner["headers"],
        json={
            "name": "Till Only",
            "description": "Modules, not permissions",
            "permission_names": pos["permissions"],
            "requires_owner_approval": {"sales.pos": True},
        },
    )
    assert response.status_code == 201, response.get_json()
    role = response.get_json()["data"]["role"]
    assert set(role["permission_names"]) == set(pos["permissions"])
    assert role["requires_owner_approval"] == {"sales.pos": True}


# ---- a PIN is as good as a password here --------------------------------------


@pytest.fixture()
def owner_with_pin(app, client, owner):
    assert client.post(
        "/api/auth/setup-pin",
        headers=owner["headers"],
        json={"pin": OWNER_PIN, "current_password": OWNER_PASSWORD},
    ).status_code in (200, 201)
    return owner


def test_an_owner_pin_unlocks_a_flagged_action(client, app, owner_with_pin):
    cashier = _make_staff(app, client, owner_with_pin, "Cashier", "pin@example.com")
    assert _flag_module(
        app, client, owner_with_pin, "Cashier", "products.items"
    ).status_code == 200

    approval = client.post(
        "/api/iam/privileged-authorizations",
        headers=cashier,
        json={"action": "item.write", "pin": OWNER_PIN},
    )
    assert approval.status_code == 200, approval.get_json()
    granted = approval.get_json()["data"]
    assert granted["approved_by"] == "owner@example.com"

    created = client.post(
        "/api/items",
        headers={**cashier, "X-Privileged-Token": granted["token"]},
        json={
            "type": "product",
            "name": "Approved by PIN",
            "selling_price": 10,
            "cost_price": 5,
            "minimum_stock": 0,
            "is_inventory_tracked": False,
        },
    )
    assert created.status_code == 201, created.get_json()


def test_a_pin_that_is_not_an_owner_pin_unlocks_nothing(client, app, owner_with_pin):
    cashier = _make_staff(app, client, owner_with_pin, "Cashier", "wrongpin@example.com")
    _make_staff(
        app,
        client,
        owner_with_pin,
        "Cashier",
        "otherpin@example.com",
        password=CASHIER_PASSWORD,
    )
    # Give the second cashier a PIN of their own, so the failure is about role
    # rather than about there being no PIN set at all.
    assert client.post(
        "/api/auth/setup-pin",
        headers=auth_headers(
            client.post(
                "/api/auth/login",
                json={"identifier": "otherpin@example.com", "password": CASHIER_PASSWORD},
            ).get_json()["data"]["access_token"]
        ),
        json={"pin": "1234", "current_password": CASHIER_PASSWORD},
    ).status_code in (200, 201)

    response = client.post(
        "/api/iam/privileged-authorizations",
        headers=cashier,
        json={"action": "item.delete", "pin": "1234"},
    )
    assert response.status_code == 401, response.get_json()


def test_a_malformed_pin_is_refused(client, cashier):
    response = client.post(
        "/api/iam/privileged-authorizations",
        headers=cashier,
        json={"action": "item.delete", "pin": "abcdef"},
    )
    assert response.status_code == 422, response.get_json()


def test_the_password_still_works_when_a_pin_is_set(client, app, owner_with_pin, cashier):
    """Setting a PIN must not quietly replace the password."""
    response = client.post(
        "/api/iam/privileged-authorizations",
        headers=cashier,
        json={"action": "item.delete", "password": OWNER_PASSWORD},
    )
    assert response.status_code == 200, response.get_json()


# ---- no role is left unable to use the app -----------------------------------


def test_every_system_role_can_read_its_notifications():
    """A role that cannot read its own inbox cannot use the app.

    Notifications gained a permission in the module-permissions change, and the
    routes started enforcing it. A business that existed before that change has
    roles that predate the permission, so if the grant is ever missed the
    symptom is a 403 on a page every signed-in user loads — which reads as "the
    upgrade broke login" rather than as a missing grant. Guard the whole set.
    """
    for role, names in SYSTEM_ROLES.items():
        assert "notification.read" in names, f"{role} cannot read its notifications"


def test_notification_write_stays_with_owner_and_admin():
    """Raising low-stock and expense alerts is an owner decision."""
    for role in ("Business Owner", "Admin"):
        assert "notification.write" in SYSTEM_ROLES[role]
    for role in ("Accountant", "Employee", "Cashier"):
        assert "notification.write" not in SYSTEM_ROLES[role]


def test_the_inbox_is_reachable_for_every_role_that_signs_in(client, app, owner):
    for role_name, email in [
        ("Cashier", "inbox-cashier@example.com"),
        ("Employee", "inbox-employee@example.com"),
    ]:
        headers = _make_staff(app, client, owner, role_name, email)
        assert client.get(
            "/api/notifications/unread-count", headers=headers
        ).status_code == 200, f"{role_name} cannot read its notifications"


# ---- the allocation is served, so the menu follows the editor ------------------


def test_a_partial_grant_still_counts_as_the_module():
    """A role that can read the price list but not edit it has the catalog.

    This is the same rule the Roles editor ticks checkboxes by. Requiring the
    whole set would report a role the owner can legitimately build as having no
    products module at all.
    """
    assert "products.items" in modules_for_permissions(["item.read"])


def test_a_module_needs_at_least_one_of_its_permissions():
    held = set(SYSTEM_ROLES["Accountant"])
    for code in modules_for_permissions(held):
        assert set(MODULES_BY_CODE[code]["permissions"]) & held, code


def test_a_cashier_is_given_the_till_and_nothing_it_was_not():
    granted = set(modules_for_permissions(SYSTEM_ROLES["Cashier"]))
    assert {"sales.pos", "products.items", "reports_admin.earnings"} <= granted
    # All company-wide, and none of them reachable from a till.
    assert granted.isdisjoint(
        {"reports_admin.dashboard", "reports_admin.reports", "reports_admin.audit"}
    )
    assert granted.isdisjoint({"business.roles", "finance.invoices", "products.purchases"})


def test_holding_nothing_is_granted_nothing():
    assert modules_for_permissions([]) == []
    assert modules_for_permissions(None) == []


def test_the_session_says_which_modules_the_role_has(client, cashier):
    response = client.get("/api/auth/me", headers=cashier)
    assert response.status_code == 200, response.get_json()
    me = response.get_json()["data"]
    assert me["role_modules"] == modules_for_permissions(me["permissions"])
    assert "sales.pos" in me["role_modules"]


def test_editing_a_role_changes_the_modules_the_session_reports(app, client, owner):
    """The whole point: the menu moves with the role editor, and nothing else."""
    headers = _make_staff(app, client, owner, "Cashier", "edited@example.com")
    before = client.get("/api/auth/me", headers=headers).get_json()["data"]
    assert "products.items" in before["role_modules"]

    role_id = _role_id(app, owner["business_id"], "Cashier")
    trimmed = client.patch(
        f"/api/iam/roles/{role_id}",
        headers=owner["headers"],
        json={"permission_names": ["sale.read", "sale.write", "profile.read"]},
    )
    assert trimmed.status_code == 200, trimmed.get_json()

    after = client.get("/api/auth/me", headers=headers).get_json()["data"]
    assert "products.items" not in after["role_modules"]
    assert "sales.pos" in after["role_modules"]


# ---- customers and suppliers are no longer unguarded -------------------------


def test_a_cashier_cannot_delete_a_customer(client, app, owner):
    cashier = _make_staff(app, client, owner, "Cashier", "cust@example.com")
    created = client.post(
        "/api/customers",
        headers=owner["headers"],
        json={"display_name": "Walk-in", "customer_type": "individual"},
    )
    assert created.status_code == 201, created.get_json()
    customer_id = created.get_json()["data"]["customer"]["id"]

    denied = client.delete(f"/api/customers/{customer_id}", headers=cashier)
    assert denied.status_code == 403, denied.get_json()
    # The cashier holds customer.read, so the refusal is about the delete.
    assert client.get(f"/api/customers/{customer_id}", headers=cashier).status_code == 200


def test_a_cashier_cannot_list_suppliers(client, app, owner):
    cashier = _make_staff(app, client, owner, "Cashier", "supp@example.com")
    response = client.get("/api/suppliers", headers=cashier)
    assert response.status_code == 403, response.get_json()
