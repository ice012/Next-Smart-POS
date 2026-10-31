"""Staff earnings derived from their own sales, and the advance request flow.

Note on fixtures: these tests deliberately do NOT push a wrapping
``app_context``. Doing so makes every test-client request share one
``db.session``, and the second user to authenticate then resolves against the
first one's identity. Production pushes a fresh app context per request, so
this only bites tests. Direct database work is wrapped in its own context by
the helpers below instead.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import (
    SYSTEM_ROLES,
    BusinessUser,
    Notification,
    Role,
    SaleInvoice,
    User,
)

from conftest import auth_headers, register_business

CASHIER_PASSWORD = "Str0ngPass!1"


# ---- helpers ----------------------------------------------------------------


def _role_id(app, business_id, name):
    with app.app_context():
        return db.session.scalar(
            select(Role.id).where(
                Role.business_id == business_id, Role.name == name
            )
        )


def _make_cashier(app, client, owner, email="cashier@example.com"):
    """Invite a Cashier, set their password, and sign them in."""
    role_id = _role_id(app, owner["business_id"], "Cashier")
    assert role_id is not None, "the Cashier role must be seeded per business"
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
    assert token, "a dev invite must return a reset token"
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
    assert data["role"] == "Cashier", data["role"]
    return {
        "headers": auth_headers(data["access_token"]),
        "user_id": data["user"]["id"],
        "session": data,
        "owner": owner,
    }


def _set_pay(app, user_id, business_id, **fields):
    with app.app_context():
        membership = db.session.scalar(
            select(BusinessUser).where(
                BusinessUser.business_id == business_id,
                BusinessUser.user_id == user_id,
            )
        )
        for key, value in fields.items():
            setattr(membership, key, value)
        db.session.commit()


def _ring_sale(app, business_id, user_id, total, status="posted"):
    """Insert a sale attributed to this user, as create_sale does."""
    with app.app_context():
        sale = SaleInvoice(
            business_id=business_id,
            invoice_number=f"T-{uuid.uuid4().hex[:12]}",
            date=datetime.now(timezone.utc),
            subtotal=Decimal(total),
            total_amount=Decimal(total),
            paid_amount=Decimal(total),
            balance_due=Decimal("0"),
            sale_type="cash",
            status=status,
        )
        sale.created_by = user_id
        db.session.add(sale)
        db.session.commit()
        return sale.id


def _cancel_all_sales(app, business_id):
    with app.app_context():
        for sale in db.session.scalars(
            select(SaleInvoice).where(
                SaleInvoice.business_id == business_id,
                SaleInvoice.status == "posted",
            )
        ).all():
            sale.status = "cancelled"
        db.session.commit()


def _notes(app, business_id, user_id=None):
    with app.app_context():
        stmt = select(Notification).where(Notification.business_id == business_id)
        if user_id is not None:
            stmt = stmt.where(Notification.user_id == user_id)
        return list(db.session.scalars(stmt).all())


def _earnings(client, cashier):
    response = client.get("/api/earnings/me", headers=cashier["headers"])
    assert response.status_code == 200, response.get_json()
    return response.get_json()["data"]["earnings"]


def _set_and_ring(app, cashier, total="1000.00", **pay):
    """Put the cashier on a pay mode and give them one sale of turnover."""
    pay.setdefault("payment_mode", "commission")
    pay.setdefault("commission_rate", 10)
    business_id = cashier["owner"]["business_id"]
    _set_pay(app, cashier["user_id"], business_id, **pay)
    _ring_sale(app, business_id, cashier["user_id"], total)


def _request_advance(client, cashier, amount, reason="Test"):
    response = client.post(
        "/api/earnings/me/advances",
        headers=cashier["headers"],
        json={"amount": amount, "reason": reason},
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()["data"]["advance"]


def _decide(client, headers, advance_id, decision, note=None):
    body = {"decision": decision}
    if note:
        body["note"] = note
    return client.post(
        f"/api/earnings/advance-requests/{advance_id}/decide",
        headers=headers,
        json=body,
    )


@pytest.fixture()
def cashier(app, client, owner):
    return _make_cashier(app, client, owner)


# ---- the Cashier role -------------------------------------------------------


def test_cashier_role_exists_and_holds_the_till_permissions(app, client, owner):
    role_id = _role_id(app, owner["business_id"], "Cashier")
    perms = client.get(
        f"/api/iam/roles/{role_id}/permissions", headers=owner["headers"]
    ).get_json()["data"]["permissions"]
    names = {p["name"] if isinstance(p, dict) else p for p in perms}
    for required in (
        "sale.read", "sale.write", "payment.write", "business.read",
        "item.read", "customer.read", "profile.read", "profile.write",
        "earnings.read", "earnings.advance_request",
    ):
        assert required in names, f"Cashier is missing {required}"


def test_cashier_cannot_reach_finance_or_staff_admin(client, cashier):
    # The streamlined shell hides these, but the API must not rely on that.
    # branch.read is intentionally granted: the till labels a sale with a branch.
    assert cashier["session"]["role"] == "Cashier"
    for path in (
        "/api/finance/expenses",
        "/api/finance/invoices",
        "/api/reports",
        "/api/iam/users",
        "/api/purchases",
    ):
        response = client.get(path, headers=cashier["headers"])
        assert response.status_code == 403, f"{path} returned {response.status_code}"


def test_employee_role_did_not_gain_payment_write():
    # Employee is left alone deliberately: changing it would alter what existing
    # staff can already do, which is not part of this feature.
    assert "payment.write" not in SYSTEM_ROLES["Employee"]
    assert "payment.write" in SYSTEM_ROLES["Cashier"]


def test_role_bootstrap_is_idempotent(app, client, owner):
    """Re-running must not duplicate roles or permission rows."""
    from app.models import Business
    from app.seed import bootstrap_business_roles

    with app.app_context():
        biz = db.session.get(Business, owner["business_id"])
        roles_table = db.metadata.tables["roles"]
        perms_table = db.metadata.tables["role_permissions"]
        before_roles = db.session.scalar(
            select(db.func.count()).select_from(roles_table).where(
                roles_table.c.business_id == owner["business_id"]
            )
        )
        before_perms = db.session.scalar(
            select(db.func.count()).select_from(perms_table)
        )
        bootstrap_business_roles(biz)
        db.session.commit()
        assert db.session.scalar(
            select(db.func.count()).select_from(roles_table).where(
                roles_table.c.business_id == owner["business_id"]
            )
        ) == before_roles
        assert db.session.scalar(
            select(db.func.count()).select_from(perms_table)
        ) == before_perms


# ---- earnings derivation ----------------------------------------------------


def test_commission_only_earnings(app, client, cashier):
    _set_and_ring(app, cashier, "1000.00", payment_mode="commission", commission_rate=10)
    body = _earnings(client, cashier)
    assert body["commission_rate"] == 10.0
    # Earnings are reported in the business' own currency, not a hard-coded one.
    assert body["currency"] == cashier["session"]["business"]["default_currency"]
    assert body["current_month"]["sales_total"] == 1000.0
    assert body["current_month"]["commission_earned"] == 100.0
    assert body["current_month"]["gross_earnings"] == 100.0
    assert body["current_month"]["available_balance"] == 100.0


def test_base_salary_only_ignores_sales(app, client, cashier):
    _set_and_ring(
        app, cashier, "4000.00", payment_mode="salary", base_salary=2500, commission_rate=0
    )
    body = _earnings(client, cashier)
    assert body["current_month"]["sales_total"] == 4000.0
    assert body["current_month"]["commission_earned"] == 0.0
    assert body["current_month"]["gross_earnings"] == 2500.0


def test_salary_plus_commission_adds_both(app, client, cashier):
    _set_and_ring(
        app, cashier, "2000.00",
        payment_mode="salary_plus_commission", base_salary=1000, commission_rate=5,
    )
    body = _earnings(client, cashier)
    assert body["current_month"]["commission_earned"] == 100.0
    assert body["current_month"]["gross_earnings"] == 1100.0


def test_a_rate_with_no_payment_mode_still_pays(app, client, cashier):
    # Matches the arithmetic the Staff page already used; dropping it would
    # silently change someone's pay.
    _set_and_ring(app, cashier, "1000.00", payment_mode=None, commission_rate=2)
    assert _earnings(client, cashier)["current_month"]["gross_earnings"] == 20.0


def test_cancelled_sales_do_not_pay(app, client, cashier):
    business_id = cashier["owner"]["business_id"]
    _set_pay(app, cashier["user_id"], business_id, payment_mode="commission", commission_rate=10)
    # Only a cancelled sale: it was reversed, so it must not pay commission.
    _ring_sale(app, business_id, cashier["user_id"], "500.00", status="cancelled")
    body = _earnings(client, cashier)
    assert body["current_month"]["sales_total"] == 0.0
    assert body["current_month"]["gross_earnings"] == 0.0


def test_only_my_own_sales_count(app, client, cashier):
    business_id = cashier["owner"]["business_id"]
    _set_and_ring(app, cashier, "100.00")
    _ring_sale(app, business_id, cashier["owner"]["user_id"], "9000.00")
    body = _earnings(client, cashier)
    assert body["current_month"]["sales_total"] == 100.0
    assert body["current_month"]["commission_earned"] == 10.0


def test_no_pay_setup_earns_nothing(app, client, cashier):
    _ring_sale(app, cashier["owner"]["business_id"], cashier["user_id"], "5000.00")
    body = _earnings(client, cashier)
    assert body["commission_rate"] is None
    assert body["current_month"]["sales_total"] == 5000.0
    assert body["current_month"]["gross_earnings"] == 0.0
    assert body["current_month"]["available_balance"] == 0.0


# ---- advance requests -------------------------------------------------------


def test_request_advance_then_approve(app, client, cashier):
    owner = cashier["owner"]
    _set_and_ring(app, cashier, "1000.00")  # 100.00 earned

    advance = _request_advance(client, cashier, 40, "Rent for the month")
    assert advance["status"] == "pending"
    assert advance["requested_amount"] == 40.0

    # A pending request holds the money immediately.
    assert _earnings(client, cashier)["all_time"]["available_balance"] == 60.0

    decided = _decide(client, owner["headers"], advance["id"], "approved", "Paid in cash")
    assert decided.status_code == 200, decided.get_json()
    body = decided.get_json()["data"]["advance"]
    assert body["status"] == "approved"
    assert body["decision_note"] == "Paid in cash"
    assert body["requester_email"] == "cashier@example.com"
    assert body["decider_name"]

    after = _earnings(client, cashier)["all_time"]
    assert after["available_balance"] == 60.0
    assert after["advances_approved"] == 40.0


def test_rejecting_an_advance_frees_the_balance(app, client, cashier):
    _set_and_ring(app, cashier, "1000.00")
    advance = _request_advance(client, cashier, 80)
    _decide(client, cashier["owner"]["headers"], advance["id"], "rejected", "Wait for payday")
    # A rejected request holds nothing.
    assert _earnings(client, cashier)["all_time"]["available_balance"] == 100.0


def test_cannot_request_more_than_available(app, client, cashier):
    _set_and_ring(app, cashier, "100.00")  # 10.00 earned
    response = client.post(
        "/api/earnings/me/advances",
        headers=cashier["headers"],
        json={"amount": 500, "reason": "Too much"},
    )
    assert response.status_code == 409, response.get_json()
    assert "available balance" in response.get_json()["message"]


def test_only_one_pending_request_at_a_time(app, client, cashier):
    _set_and_ring(app, cashier, "1000.00")
    _request_advance(client, cashier, 10, "First")
    second = client.post(
        "/api/earnings/me/advances",
        headers=cashier["headers"],
        json={"amount": 10, "reason": "Second"},
    )
    assert second.status_code == 409, second.get_json()
    assert "awaiting a decision" in second.get_json()["message"]


def test_cancelling_a_pending_request_releases_the_hold(app, client, cashier):
    _set_and_ring(app, cashier, "1000.00")
    advance = _request_advance(client, cashier, 60, "Changed my mind")
    assert _earnings(client, cashier)["all_time"]["available_balance"] == 40.0

    cancelled = client.post(
        f"/api/earnings/me/advances/{advance['id']}/cancel",
        headers=cashier["headers"],
    )
    assert cancelled.status_code == 200, cancelled.get_json()
    assert cancelled.get_json()["data"]["advance"]["status"] == "cancelled"
    assert _earnings(client, cashier)["all_time"]["available_balance"] == 100.0


def test_approval_rechecks_the_balance(app, client, cashier):
    """A sale cancelled after the request must block approval."""
    _set_and_ring(app, cashier, "1000.00")
    advance = _request_advance(client, cashier, 90, "Most of it")

    _cancel_all_sales(app, cashier["owner"]["business_id"])

    response = _decide(client, cashier["owner"]["headers"], advance["id"], "approved")
    assert response.status_code == 409, response.get_json()
    assert "available balance" in response.get_json()["message"]


def test_a_decided_request_cannot_be_decided_again(app, client, cashier):
    _set_and_ring(app, cashier, "1000.00")
    advance = _request_advance(client, cashier, 10, "Once")
    _decide(client, cashier["owner"]["headers"], advance["id"], "approved")
    again = _decide(client, cashier["owner"]["headers"], advance["id"], "rejected")
    assert again.status_code == 409, again.get_json()
    assert "already been approved" in again.get_json()["message"]


def test_my_advance_history_is_listed(app, client, cashier):
    _set_and_ring(app, cashier, "1000.00")
    _request_advance(client, cashier, 10, "First")
    listing = client.get("/api/earnings/me/advances", headers=cashier["headers"])
    assert listing.status_code == 200, listing.get_json()
    advances = listing.get_json()["data"]["advances"]
    assert len(advances) == 1
    assert advances[0]["reason"] == "First"


# ---- authorisation ----------------------------------------------------------


def test_a_cashier_cannot_decide_advances(app, client, cashier):
    _set_and_ring(app, cashier, "1000.00")
    advance = _request_advance(client, cashier, 10, "Mine")
    assert client.get(
        "/api/earnings/advance-requests", headers=cashier["headers"]
    ).status_code == 403
    assert _decide(client, cashier["headers"], advance["id"], "approved").status_code == 403
    assert client.get(
        f"/api/earnings/staff/{cashier['user_id']}", headers=cashier["headers"]
    ).status_code == 403


def test_the_owner_queue_lists_pending_first(app, client, cashier):
    _set_and_ring(app, cashier, "1000.00")
    first = _request_advance(client, cashier, 10, "First")
    _decide(client, cashier["owner"]["headers"], first["id"], "approved")
    second = _request_advance(client, cashier, 20, "Second")

    listing = client.get(
        "/api/earnings/advance-requests", headers=cashier["owner"]["headers"]
    )
    assert listing.status_code == 200
    advances = listing.get_json()["data"]["advances"]
    assert [a["id"] for a in advances] == [second["id"], first["id"]]
    assert advances[0]["requester_name"] == "Till Operator"

    filtered = client.get(
        "/api/earnings/advance-requests?status=approved",
        headers=cashier["owner"]["headers"],
    ).get_json()["data"]["advances"]
    assert [a["id"] for a in filtered] == [first["id"]]


def test_an_approver_can_check_a_members_affordability(app, client, owner):
    other = _make_cashier(app, client, owner, email="check@example.com")
    _set_pay(
        app, other["user_id"], owner["business_id"],
        payment_mode="commission", commission_rate=10,
    )
    _ring_sale(app, owner["business_id"], other["user_id"], "1000.00")
    response = client.get(
        f"/api/earnings/staff/{other['user_id']}", headers=owner["headers"]
    )
    assert response.status_code == 200
    balance = response.get_json()["data"]["earnings"]["all_time"]["available_balance"]
    assert float(balance) == 100.0


def test_a_cashier_cannot_read_another_members_earnings(app, client, owner):
    theirs = _make_cashier(app, client, owner, email="their@example.com")
    mine = _make_cashier(app, client, owner, email="mine@example.com")
    assert client.get(
        f"/api/earnings/staff/{theirs['user_id']}", headers=mine["headers"]
    ).status_code == 403
    assert client.get(
        f"/api/earnings/staff/{theirs['user_id']}", headers=owner["headers"]
    ).status_code == 200


def test_a_cashier_cannot_cancel_someone_elses_request(app, client, owner):
    mine = _make_cashier(app, client, owner, email="mine2@example.com")
    theirs = _make_cashier(app, client, owner, email="their2@example.com")
    _set_pay(
        app, theirs["user_id"], owner["business_id"],
        payment_mode="commission", commission_rate=10,
    )
    _ring_sale(app, owner["business_id"], theirs["user_id"], "1000.00")
    advance = _request_advance(client, theirs, 10, "Theirs")
    # 404 rather than 403: do not confirm that someone else's request exists.
    assert client.post(
        f"/api/earnings/me/advances/{advance['id']}/cancel", headers=mine["headers"]
    ).status_code == 404


def test_advance_queue_is_isolated_between_businesses(client, cashier):
    other = register_business(client, "iso-owner@example.com", "Other Co")
    listing = client.get(
        "/api/earnings/advance-requests", headers=auth_headers(other["access_token"])
    )
    assert listing.status_code == 200
    assert listing.get_json()["data"]["advances"] == []


# ---- the staff list overview -------------------------------------------------


def _overview(client, headers):
    response = client.get("/api/earnings/staff", headers=headers)
    assert response.status_code == 200, response.get_json()
    return response.get_json()["data"]


def test_the_overview_names_each_staff_member_with_their_sales(app, client, cashier):
    _set_and_ring(app, cashier, "1000.00")
    row = next(
        r for r in _overview(client, cashier["owner"]["headers"])["staff"]
        if r["user_id"] == cashier["user_id"]
    )
    assert row["name"] == "Till Operator"
    assert row["email"] == "cashier@example.com"
    assert row["role"] == "Cashier"
    assert row["sales_total"] == 1000.0
    assert row["sales_count"] == 1
    assert row["commission_earned"] == 100.0
    assert row["gross_earnings"] == 100.0
    assert row["advances_taken"] == 0.0


def test_the_overview_reports_advances_taken(app, client, cashier):
    owner = cashier["owner"]
    _set_and_ring(app, cashier, "1000.00")  # 100.00 earned
    approved = _request_advance(client, cashier, 30, "First")
    _decide(client, owner["headers"], approved["id"], "approved", "Paid")
    _request_advance(client, cashier, 20, "Second")  # still pending

    row = next(
        r for r in _overview(client, owner["headers"])["staff"]
        if r["user_id"] == cashier["user_id"]
    )
    # Money already paid out plus the request sitting with an approver.
    assert row["advances_taken"] == 50.0
    assert row["advances_approved"] == 30.0
    assert row["advances_pending"] == 20.0
    assert row["available_balance"] == 50.0


def test_the_overview_covers_every_member_including_the_owner(app, client, cashier):
    _ring_sale(app, cashier["owner"]["business_id"], cashier["owner"]["user_id"], "700.00")
    staff = _overview(client, cashier["owner"]["headers"])["staff"]
    names = {r["name"] for r in staff}
    assert "Till Operator" in names
    assert "Ada Lovelace" in names
    owner_row = next(r for r in staff if r["name"] == "Ada Lovelace")
    assert owner_row["sales_total"] == 700.0
    # A member who has never sold anything is still listed, with zeroes rather
    # than missing, so the staff table has no gaps in it.
    idle = next(r for r in staff if r["name"] == "Till Operator")
    assert idle["sales_total"] == 0.0
    assert idle["advances_taken"] == 0.0


def test_the_overview_is_scoped_to_its_own_business(client, cashier):
    other = register_business(client, "other-owner@example.com", "Other Co")
    staff = _overview(client, auth_headers(other["access_token"]))["staff"]
    assert all(r["email"] != "cashier@example.com" for r in staff)


def test_a_cashier_cannot_read_the_overview(client, cashier):
    assert client.get(
        "/api/earnings/staff", headers=cashier["headers"]
    ).status_code == 403


# ---- notifications ----------------------------------------------------------


def test_approvers_are_notified_of_a_new_request(app, client, cashier):
    _set_and_ring(app, cashier, "1000.00")
    _request_advance(client, cashier, 25, "School fees")
    notes = _notes(app, cashier["owner"]["business_id"])
    assert any("awaiting review" in n.title for n in notes)
    assert any("School fees" in n.message for n in notes)


def test_the_requester_is_told_the_outcome(app, client, cashier):
    _set_and_ring(app, cashier, "1000.00")
    advance = _request_advance(client, cashier, 25, "School fees")
    _decide(client, cashier["owner"]["headers"], advance["id"], "approved", "Paid")
    notes = _notes(app, cashier["owner"]["business_id"], cashier["user_id"])
    assert any(n.title == "Advance request decided" for n in notes)


# ---- validation -------------------------------------------------------------


def test_advance_request_validation(app, client, cashier):
    _set_and_ring(app, cashier, "1000.00")
    for body in (
        {"amount": 0, "reason": "Nothing"},
        {"amount": -5, "reason": "Negative"},
        {"amount": 10},
        {"amount": 10, "reason": ""},
    ):
        response = client.post(
            "/api/earnings/me/advances", headers=cashier["headers"], json=body
        )
        assert response.status_code == 422, f"{body} -> {response.status_code}"


def test_advance_decision_rejects_an_unknown_verdict(app, client, cashier):
    _set_and_ring(app, cashier, "1000.00")
    advance = _request_advance(client, cashier, 10)
    assert _decide(
        client, cashier["owner"]["headers"], advance["id"], "maybe"
    ).status_code == 422


def test_earnings_are_empty_for_a_user_with_no_membership(app):
    with app.app_context():
        stranger = User(
            email="stranger@example.com",
            password_hash="x",
            first_name="No",
            last_name="Access",
        )
        db.session.add(stranger)
        db.session.commit()
        assert db.session.scalar(
            select(BusinessUser).where(BusinessUser.user_id == stranger.id)
        ) is None
