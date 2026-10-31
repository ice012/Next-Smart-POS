"""Staff earnings derived live from their own sales, plus advance requests.

Earnings are not stored. They are recomputed on every read from the sales a
member actually rang up, so the figure on the dashboard can never disagree with
the sales that produced it. The trade-off is that there is no closed pay-period
history: cancelling a sale retroactively lowers what the member has earned.
"""

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select

from ..exceptions import BusinessError, ConflictError, NotFoundError
from ..extensions import db
from ..models import (
    BALANCE_HOLDING_STATUSES,
    BusinessUser,
    EarningsAdvanceRequest,
    SaleInvoice,
)
from ..utils.db import commit

CENTS = Decimal("0.01")
ZERO = Decimal("0")

# Sale states that count towards earnings. A draft was never really rung up and
# a cancelled sale has been reversed, so neither should pay commission.
EARNING_SALE_STATUSES = ("posted",)

# Guards against a typo turning into an unbounded request.
MAX_ADVANCE_AMOUNT = Decimal("10000000")


def _money(value) -> Decimal:
    return Decimal(value or 0).quantize(CENTS, rounding=ROUND_HALF_UP)


def _now():
    return datetime.now(timezone.utc)


def _month_start(reference: datetime) -> datetime:
    return reference.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def get_membership(business_id, user_id) -> BusinessUser:
    membership = db.session.scalar(
        select(BusinessUser).where(
            BusinessUser.business_id == business_id,
            BusinessUser.user_id == user_id,
        )
    )
    if membership is None:
        raise NotFoundError("No staff record for this user in this business")
    return membership


def _sales_total(business_id, user_id, since=None) -> Decimal:
    stmt = select(func.coalesce(func.sum(SaleInvoice.total_amount), 0)).where(
        SaleInvoice.business_id == business_id,
        SaleInvoice.created_by == user_id,
        SaleInvoice.status.in_(EARNING_SALE_STATUSES),
    )
    if since is not None:
        stmt = stmt.where(SaleInvoice.date >= since)
    return _money(db.session.scalar(stmt))


def _sale_count(business_id, user_id, since=None) -> int:
    stmt = select(func.count()).select_from(SaleInvoice).where(
        SaleInvoice.business_id == business_id,
        SaleInvoice.created_by == user_id,
        SaleInvoice.status.in_(EARNING_SALE_STATUSES),
    )
    if since is not None:
        stmt = stmt.where(SaleInvoice.date >= since)
    return int(db.session.scalar(stmt) or 0)


def _gross_for(membership, sales_total: Decimal) -> Decimal:
    """Price a member's sales into an earnings figure.

    Mirrors the arithmetic the Staff page already used, so the owner sees the
    same number here that they see on the staff list. A rate with no explicit
    payment mode still pays, because that was the existing behaviour and
    silently dropping it would quietly change someone's pay.
    """
    mode = membership.payment_mode
    rate = Decimal(membership.commission_rate or 0)
    total = ZERO

    if mode in ("salary", "salary_plus_commission"):
        total += _money(membership.base_salary)
    if mode in ("commission", "salary_plus_commission"):
        total += _money(sales_total * rate / Decimal("100"))
    if membership.commission_rate is not None and not mode:
        total += _money(sales_total * rate / Decimal("100"))
    return _money(total)


def _advances_summary(business_id, user_id) -> dict:
    """Totals for a member's advances, split by what they do to the balance."""
    rows = db.session.execute(
        select(EarningsAdvanceRequest.status, func.coalesce(func.sum(EarningsAdvanceRequest.requested_amount), 0))
        .where(
            EarningsAdvanceRequest.business_id == business_id,
            EarningsAdvanceRequest.user_id == user_id,
        )
        .group_by(EarningsAdvanceRequest.status)
    ).all()

    by_status = {status: _money(total) for status, total in rows}
    held = sum(
        (by_status.get(status, ZERO) for status in BALANCE_HOLDING_STATUSES), ZERO
    )
    return {
        "pending": by_status.get("pending", ZERO),
        "approved": by_status.get("approved", ZERO),
        "rejected": by_status.get("rejected", ZERO),
        "cancelled": by_status.get("cancelled", ZERO),
        # Pending counts against the balance as well: otherwise a member could
        # submit the same request repeatedly while a decision is outstanding.
        "held_total": _money(held),
        "count": sum(1 for _status, _total in rows),
    }


def _period(business_id, membership, user_id, since=None) -> dict:
    sales_total = _sales_total(business_id, user_id, since)
    gross = _gross_for(membership, sales_total)
    advances = _advances_summary(business_id, user_id)
    # Advances are paid against the whole balance, not a single period, so the
    # deduction is only meaningful on the all-time figure. For the month it is
    # reported as context rather than subtracted.
    available = gross - advances["held_total"]
    if available < ZERO:
        available = ZERO
    return {
        "sales_total": sales_total,
        "sales_count": _sale_count(business_id, user_id, since),
        "commission_earned": _money(
            sales_total * Decimal(membership.commission_rate or 0) / Decimal("100")
        ),
        "base_salary": _money(membership.base_salary),
        "gross_earnings": gross,
        "advances_approved": advances["approved"],
        "advances_pending": advances["pending"],
        "advances_held": advances["held_total"],
        "available_balance": _money(available),
    }


def _as_floats(period: dict) -> dict:
    """Render a period's figures as floats.

    Decimals do not serialise through Flask's JSON encoder — they would reach
    the client as strings like "40.00" and silently break every arithmetic
    comparison in the UI. Money is already quantised to cents by `_money`.
    """
    return {
        key: float(value) if isinstance(value, Decimal) else value
        for key, value in period.items()
    }


def my_earnings(business_id, user_id) -> dict:
    """Earnings for the signed-in member, for the current month and all time."""
    membership = get_membership(business_id, user_id)
    now = _now()
    return {
        "currency": _business_currency(business_id),
        "payment_mode": membership.payment_mode,
        "commission_rate": float(membership.commission_rate) if membership.commission_rate is not None else None,
        "base_salary": _money(membership.base_salary),
        "role_id": str(membership.role_id) if membership.role_id else None,
        "current_month": dict(
            _as_floats(_period(business_id, membership, user_id, _month_start(now))),
            period="month",
        ),
        "all_time": dict(
            _as_floats(_period(business_id, membership, user_id, None)),
            period="all_time",
        ),
    }


def _business_currency(business_id) -> str:
    from ..models import Business

    business = db.session.get(Business, business_id)
    return (business.default_currency if business else None) or "USD"


def my_advance_requests(business_id, user_id, pagination) -> tuple:
    stmt = (
        select(EarningsAdvanceRequest)
        .where(
            EarningsAdvanceRequest.business_id == business_id,
            EarningsAdvanceRequest.user_id == user_id,
        )
        .order_by(EarningsAdvanceRequest.created_at.desc())
    )
    from ..utils.pagination import paginate

    return paginate(stmt, pagination.page, pagination.per_page)


def request_advance(business_id, user_id, amount, reason) -> EarningsAdvanceRequest:
    """Submit an advance request, capped by what the member has left available."""
    membership = get_membership(business_id, user_id)
    requested = _money(amount)
    if requested <= ZERO:
        raise BusinessError("Advance amount must be greater than zero")
    if requested > MAX_ADVANCE_AMOUNT:
        raise BusinessError("Advance amount is unreasonably large")

    now = _now()
    current = _period(business_id, membership, user_id, None)
    available = current["available_balance"]
    if requested > available:
        raise ConflictError(
            f"Requested amount exceeds the available balance of {available}."
        )

    # One live request at a time. A second identical-amount request while the
    # first is pending is almost always a double submit, and the held total
    # would otherwise silently eat into the balance twice.
    existing = db.session.scalar(
        select(EarningsAdvanceRequest).where(
            EarningsAdvanceRequest.business_id == business_id,
            EarningsAdvanceRequest.user_id == user_id,
            EarningsAdvanceRequest.status == "pending",
        )
    )
    if existing is not None:
        raise ConflictError(
            "You already have an advance request awaiting a decision."
        )

    request = EarningsAdvanceRequest(
        business_id=business_id,
        user_id=user_id,
        requested_amount=requested,
        reason=reason,
        status="pending",
        requested_at=now,
    )
    db.session.add(request)
    commit()
    _notify_deciders(business_id, user_id, requested, reason)
    return request


def _notify_deciders(business_id, requester_id, amount, reason) -> None:
    """Tell whoever can approve that a request is waiting."""
    from .notification_service import create_notification
    from ..models import Role, User, BusinessUser as _BU

    approver_role_ids = db.session.scalars(
        select(Role.id).where(
            Role.business_id == business_id, Role.name.in_(("Business Owner", "Admin"))
        )
    ).all()
    if not approver_role_ids:
        return
    approver_ids = db.session.scalars(
        select(_BU.user_id).where(
            _BU.business_id == business_id,
            _BU.role_id.in_(approver_role_ids),
            _BU.user_id.is_not(None),
        )
    ).all()
    requester = db.session.get(User, requester_id)
    who = requester.email if requester else "A staff member"
    for approver_id in set(approver_ids):
        if approver_id == requester_id:
            continue
        create_notification(
            business_id=business_id,
            user_id=approver_id,
            category="system",
            title="Advance request awaiting review",
            message=(
                f"{who} requested an advance of {amount}. "
                f"Reason: {reason}"
            ),
            reference_type="earnings_advance",
            priority="high",
        )
    commit()


def list_advance_requests(business_id, pagination, status=None) -> tuple:
    from ..utils.pagination import paginate

    stmt = select(EarningsAdvanceRequest).where(
        EarningsAdvanceRequest.business_id == business_id
    )
    if status:
        stmt = stmt.where(EarningsAdvanceRequest.status == status)
    stmt = stmt.order_by(
        # Pending first so the review queue is what an approver lands on. The
        # comparison yields 1 for everything that is not pending, so ascending
        # is what floats the pending rows to the top.
        (EarningsAdvanceRequest.status != "pending").asc(),
        EarningsAdvanceRequest.created_at.desc(),
    )
    return paginate(stmt, pagination.page, pagination.per_page)


def advance_request_dto(request: EarningsAdvanceRequest) -> dict:
    """Request plus the requester's name, for the approver's queue."""
    data = request.to_dict()
    user = request.user
    if user is not None:
        parts = [p for p in (user.first_name, user.last_name) if p]
        data["requester_name"] = " ".join(parts) or user.email
        data["requester_email"] = user.email
    else:
        data["requester_name"] = None
        data["requester_email"] = None
    return data


def _get_request(business_id, request_id) -> EarningsAdvanceRequest:
    request = db.session.get(EarningsAdvanceRequest, request_id)
    if request is None or request.business_id != business_id:
        raise NotFoundError("Advance request not found")
    return request


def decide_advance_request(business_id, request_id, decision, decider_id, note=None):
    """Approve or reject a pending advance request."""
    request = _get_request(business_id, request_id)
    if request.status != "pending":
        raise ConflictError(
            f"This request has already been {request.status}."
        )

    if decision == "approved":
        # Re-check the balance at approval time. The member's sales may have
        # been cancelled since they asked, and approving into a negative
        # balance is how advances turn into unrecoverable debt.
        membership = get_membership(business_id, request.user_id)
        current = _period(business_id, membership, request.user_id, None)
        if request.requested_amount > current["available_balance"]:
            raise ConflictError(
                "This request now exceeds the member's available balance."
            )

    request.status = decision
    request.decided_at = _now()
    request.decided_by = decider_id
    request.decision_note = note
    db.session.flush()
    _notify_requester(business_id, request, decision, note)
    commit()
    return request


def cancel_advance_request(business_id, user_id, request_id):
    """Let the requester withdraw their own pending request."""
    request = _get_request(business_id, request_id)
    if request.user_id != user_id:
        # Do not confirm the request exists to someone else's member.
        raise NotFoundError("Advance request not found")
    if request.status != "pending":
        raise ConflictError(f"This request has already been {request.status}.")
    request.status = "cancelled"
    request.decided_at = _now()
    commit()
    return request


def _notify_requester(business_id, request, decision, note) -> None:
    from .notification_service import create_notification

    approved = decision == "approved"
    message = (
        f"Your advance of {request.requested_amount} was approved."
        if approved
        else f"Your advance request for {request.requested_amount} was rejected."
    )
    if note:
        message = f"{message} Note: {note}"
    create_notification(
        business_id=business_id,
        user_id=request.user_id,
        category="system",
        title="Advance request decided",
        message=message,
        reference_type="earnings_advance",
        reference_id=request.id,
        priority="high" if not approved else "normal",
    )


def staff_overview(business_id) -> dict:
    """One row per staff member: who they are, what they sold, what they took.

    The staff list needs all three figures for every member at once. Deriving
    them per member would mean a handful of queries each, so the sales and the
    advances are aggregated in two grouped queries and joined onto the
    membership list in memory. Every figure is produced by the same helpers the
    single-member view uses, so the list and `/earnings/me` cannot disagree.
    """
    from ..models import Role, User

    members = db.session.execute(
        select(BusinessUser, User, Role)
        .join(User, BusinessUser.user_id == User.id)
        .outerjoin(Role, BusinessUser.role_id == Role.id)
        .where(BusinessUser.business_id == business_id)
        .order_by(User.first_name.asc(), User.email.asc())
    ).all()

    sales_rows = {
        user_id: (total, count)
        for user_id, total, count in db.session.execute(
            select(
                SaleInvoice.created_by,
                func.coalesce(func.sum(SaleInvoice.total_amount), 0),
                func.count(),
            )
            .where(
                SaleInvoice.business_id == business_id,
                SaleInvoice.status.in_(EARNING_SALE_STATUSES),
                SaleInvoice.created_by.is_not(None),
            )
            .group_by(SaleInvoice.created_by)
        ).all()
    }
    advance_rows = db.session.execute(
        select(
            EarningsAdvanceRequest.user_id,
            EarningsAdvanceRequest.status,
            func.coalesce(func.sum(EarningsAdvanceRequest.requested_amount), 0),
        )
        .where(EarningsAdvanceRequest.business_id == business_id)
        .group_by(EarningsAdvanceRequest.user_id, EarningsAdvanceRequest.status)
    ).all()
    advances_by_user: dict = {}
    for user_id, status, total in advance_rows:
        advances_by_user.setdefault(user_id, {})[status] = _money(total)

    staff = []
    for membership, user, role in members:
        sales_total = _money(sales_rows.get(user.id, (0, 0))[0])
        sales_count = int(sales_rows.get(user.id, (0, 0))[1] or 0)
        by_status = advances_by_user.get(user.id, {})
        approved = by_status.get("approved", ZERO)
        pending = by_status.get("pending", ZERO)
        held = sum((by_status.get(s, ZERO) for s in BALANCE_HOLDING_STATUSES), ZERO)
        gross = _gross_for(membership, sales_total)
        available = gross - held
        if available < ZERO:
            available = ZERO
        parts = [p for p in (user.first_name, user.last_name) if p]
        staff.append(
            {
                "user_id": str(user.id),
                "membership_id": str(membership.id),
                "name": " ".join(parts) or user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "email": user.email,
                "role": role.name if role else None,
                "is_active": membership.is_active,
                "sales_total": float(sales_total),
                "sales_count": sales_count,
                "commission_earned": float(
                    _money(
                        sales_total
                        * Decimal(membership.commission_rate or 0)
                        / Decimal("100")
                    )
                ),
                "base_salary": float(_money(membership.base_salary)),
                "gross_earnings": float(gross),
                "advances_approved": float(approved),
                "advances_pending": float(pending),
                # What they have actually walked away with: approved plus the
                # requests already sitting with an approver, held against the
                # balance but not yet paid.
                "advances_taken": float(_money(approved + pending)),
                "advances_held": float(_money(held)),
                "available_balance": float(_money(available)),
            }
        )

    return {"currency": _business_currency(business_id), "staff": staff}


class EarningsService:
    """Facade mirroring the other service modules in this package."""

    my_earnings = staticmethod(my_earnings)
    staff_overview = staticmethod(staff_overview)
    my_advance_requests = staticmethod(my_advance_requests)
    request_advance = staticmethod(request_advance)
    list_advance_requests = staticmethod(list_advance_requests)
    decide_advance_request = staticmethod(decide_advance_request)
    cancel_advance_request = staticmethod(cancel_advance_request)
    advance_request_dto = staticmethod(advance_request_dto)


def get_earnings_service():
    return EarningsService()
