"""Subscription and access-control service.

Manages subscription plans, per-business subscriptions (trial/active/...),
system modules and the module-access check used by the 3-tier auth layer.
"""
import math
import uuid as uuidlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func

from ..exceptions import (
    NotFoundError,
    ConflictError,
    ForbiddenError,
    PlanLimitError,
    ValidationError,
)
from ..extensions import db
from ..models import (
    Branch,
    Business,
    BusinessUser,
    Invoice,
    SubscriptionPlan,
    Subscription,
    SystemModule,
    SYSTEM_MODULES,
    get_default_plan_modules,
    SUBSCRIPTION_STATUSES,
)
from ..utils.db import commit


TRIAL_DAYS = 14
ACTIVE_STATUSES = {"trial", "active", "incomplete"}
CORE_MODULES = {"core", "admin", "todos"}
PLAN_TIER_ORDER = {"free": 0, "starter": 1, "professional": 2, "enterprise": 3}

# resource key -> the plan column that caps it. A ``None`` cap means unlimited.
LIMIT_RESOURCES = {
    "users": "max_users",
    "branches": "max_branches",
    "invoices": "max_invoices",
}
LIMIT_LABELS = {
    "users": "user",
    "branches": "branch",
    "invoices": "invoice",
}



def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_past(value: datetime) -> bool:
    if value.tzinfo is None:
        return value < _now().replace(tzinfo=None)
    return value < _now()


def _as_aware(value: datetime | None) -> datetime | None:
    """Normalise a stored timestamp to UTC-aware.

    SQLite drops timezone information, so a ``DateTime(timezone=True)`` column
    can hand back a naive value. Comparisons and serialisation both need a
    consistent, aware value.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_default_plan():
    plan = db.session.scalar(
        select(SubscriptionPlan).where(SubscriptionPlan.is_default.is_(True), SubscriptionPlan.is_active.is_(True))
    )
    if plan is None:
        plan = db.session.scalar(select(SubscriptionPlan).where(SubscriptionPlan.is_default.is_(True)))
    if plan is None:
        # Falling back to the cheapest active plan keeps signups working when
        # the default flag is missing or pointed at a deactivated plan. Raising
        # here would lock out every new registration.
        plan = db.session.scalar(
            select(SubscriptionPlan)
            .where(SubscriptionPlan.is_active.is_(True))
            .order_by(SubscriptionPlan.price_cents.asc())
        )
    if plan is None:
        raise ForbiddenError("No subscription plan available")
    return plan


def get_subscription(business_id):
    return db.session.scalar(
        select(Subscription).where(Subscription.business_id == business_id).order_by(Subscription.created_at.desc())
    )


def is_subscription_active(business_id) -> bool:
    sub = get_subscription(business_id)
    if sub is None:
        return False
    if sub.status not in ACTIVE_STATUSES:
        return False
    if sub.current_period_end is not None and _is_past(sub.current_period_end):
        return False
    if sub.trial_ends_at is not None and _is_past(sub.trial_ends_at) and sub.status == "trial":
        return False
    return True


def trial_info(business_id) -> dict:
    """Return trial countdown details for *business_id*.

    Powers the "days left in your free trial" banner. ``trial_days_remaining``
    is the authoritative server-side figure (rounded up so any part of a
    remaining day still counts as a day); the client ticks the seconds down
    locally from ``trial_ends_at``.
    """
    now = _now()
    sub = get_subscription(business_id)
    trial_ends_at = _as_aware(sub.trial_ends_at) if sub else None
    current_period_end = _as_aware(sub.current_period_end) if sub else None

    # A business is "in trial" only while it is actually on a trial status and
    # has a trial end date; paid/expired subscriptions report no countdown.
    is_trial = bool(sub and sub.status == "trial" and trial_ends_at)
    days_remaining = None
    if is_trial:
        remaining = trial_ends_at - now
        # Round up: 0.1 days left should still read as "1 day left".
        days_remaining = max(0, math.ceil(remaining.total_seconds() / 86400))

    # Report the plan's own trial length rather than the global default, so a
    # 30-day plan's banner does not claim 14.
    plan = sub.plan if sub and sub.plan else None
    if plan is not None and plan.trial_days is not None:
        total_days = plan.trial_days
    else:
        started_at = _as_aware(sub.started_at) if sub else None
        if started_at and trial_ends_at:
            total_days = max(
                0,
                math.ceil((trial_ends_at - started_at).total_seconds() / 86400),
            )
        else:
            total_days = TRIAL_DAYS

    return {
        "is_trial": is_trial,
        "status": sub.status if sub else None,
        "plan_name": sub.plan.name if sub and sub.plan else None,
        "plan_tier": sub.plan.tier if sub and sub.plan else None,
        "trial_ends_at": trial_ends_at.isoformat() if trial_ends_at else None,
        "trial_days_remaining": days_remaining,
        "trial_total_days": total_days,
        "current_period_end": current_period_end.isoformat() if current_period_end else None,
        "is_expired": bool(is_trial and days_remaining == 0),
    }


def can_access_module(business_id, module_code) -> bool:
    """Return True if the business's subscription grants access to *module_code*."""
    if module_code in CORE_MODULES:
        return True
    sub = get_subscription(business_id)
    plan = sub.plan if sub else None
    if plan is None:
        plan = get_default_plan()
    granted = set(plan.modules or [])
    if module_code not in granted:
        return False
    system_module = db.session.scalar(select(SystemModule).where(SystemModule.code == module_code))
    if system_module is not None:
        if not system_module.is_enabled:
            return False
        if not _tier_allows(plan.tier, system_module.min_plan_tier):
            return False
    return True


def _tier_allows(plan_tier: str | None, min_plan_tier: str | None) -> bool:
    """Is *plan_tier* at or above the module's *min_plan_tier*?

    An unset ``min_plan_tier`` places no floor on the module, so every plan
    that carries the module in its list keeps access.
    """
    if not min_plan_tier:
        return True
    have = PLAN_TIER_ORDER.get(plan_tier or "free", 0)
    need = PLAN_TIER_ORDER.get(min_plan_tier, 0)
    return have >= need



def accessible_modules(business_id) -> list:
    if not is_subscription_active(business_id):
        return []
    plan = _resolved_plan(business_id)
    granted = set(plan.modules or [])
    enabled = {
        m.code for m in db.session.scalars(
            select(SystemModule).where(SystemModule.is_enabled.is_(True))
        ).all()
    }
    # A module's min_plan_tier floors the plan list: listing a module on a plan
    # below that floor must not hand out access.
    floors = {
        m.code: m.min_plan_tier
        for m in db.session.scalars(select(SystemModule)).all()
        if m.min_plan_tier
    }
    allowed = {
        code
        for code in (granted | CORE_MODULES) & enabled
        if _tier_allows(plan.tier, floors.get(code))
    }
    return sorted(allowed)


def _resolved_plan(business_id):
    sub = get_subscription(business_id)
    if sub and sub.plan:
        return sub.plan
    return get_default_plan()


def plan_usage(business_id) -> dict:
    """Current consumption of every capped resource for *business_id*.

    Counts only live rows, so deactivating a user or a branch frees a slot
    again rather than locking the tenant out of its own plan.
    """
    users = db.session.scalar(
        select(func.count()).select_from(BusinessUser).where(
            BusinessUser.business_id == business_id,
            BusinessUser.is_active.is_(True),
        )
    ) or 0
    branches = db.session.scalar(
        select(func.count()).select_from(Branch).where(
            Branch.business_id == business_id,
            Branch.is_active.is_(True),
        )
    ) or 0
    invoices = db.session.scalar(
        select(func.count()).select_from(Invoice).where(Invoice.business_id == business_id)
    ) or 0
    return {"users": int(users), "branches": int(branches), "invoices": int(invoices)}


def plan_limits(business_id) -> dict:
    """The business's plan caps, with ``None`` for an unlimited resource."""
    plan = _resolved_plan(business_id)
    return {key: getattr(plan, column, None) for key, column in LIMIT_RESOURCES.items()}


def plan_limit_status(business_id) -> dict:
    """Limits plus live usage, for UI limit meters and support lookups."""
    plan = _resolved_plan(business_id)
    usage = plan_usage(business_id)
    limits = {key: getattr(plan, column, None) for key, column in LIMIT_RESOURCES.items()}
    return {
        "plan_id": str(plan.id),
        "plan_name": plan.name,
        "plan_tier": plan.tier,
        "usage": usage,
        "limits": limits,
        "at_limit": {
            key: limits[key] is not None and usage[key] >= limits[key]
            for key in LIMIT_RESOURCES
        },
    }


def check_plan_limit(business_id, resource: str, additional: int = 1) -> None:
    """Raise :class:`PlanLimitError` if *resource* would exceed the plan cap.

    ``additional`` is how many more of the resource the caller is about to
    create, so a caller replacing a row can pass 0 to validate without
    counting the new row twice.
    """
    column = LIMIT_RESOURCES.get(resource)
    if column is None:
        raise ValueError(f"Unknown plan limit resource: {resource}")
    cap = getattr(_resolved_plan(business_id), column, None)
    if cap is None:
        return
    used = plan_usage(business_id)[resource]
    if used + additional <= cap:
        return
    plan = _resolved_plan(business_id)
    label = LIMIT_LABELS[resource]
    raise PlanLimitError(
        f"Your {plan.name} plan allows {cap} {label}{'s' if cap != 1 else ''}. "
        f"You currently have {used}. Upgrade your plan to add more.",
        payload={
            "limit": resource,
            "cap": cap,
            "used": used,
            "plan_id": str(plan.id),
            "plan_name": plan.name,
        },
    )


def over_limit_resources(business_id) -> list:
    """Resource keys where the business already sits at or above its cap."""
    status = plan_limit_status(business_id)
    return [key for key, at_limit in status["at_limit"].items() if at_limit]


def validate_plan_modules(codes) -> list:
    """Reject module codes that are not in the system module catalogue.

    A typo in a plan's module list would otherwise grant nothing while
    looking like a working entitlement, and would silently widen on the
    next catalogue edit.
    """
    if not codes:
        return []
    known = {m.code for m in db.session.scalars(select(SystemModule)).all()}
    known.update(m["code"] for m in SYSTEM_MODULES)
    unknown = sorted({code for code in codes if code not in known})
    if unknown:
        from ..exceptions import ValidationError

        raise ValidationError(
            f"Unknown module code(s): {', '.join(unknown)}",
            payload={"unknown_modules": unknown},
        )
    return list(codes)



def ensure_subscription(business, plan=None) -> Subscription:
    """Provision a brand-new tenant's subscription (idempotent).

    ``plan`` is the tier the signup chose; when omitted the default plan is
    used. The trial length comes from the plan, so a plan configured for 30 days
    hands out 30 and a plan set to 0 goes straight to active-and-paying.
    """
    existing = get_subscription(business.id)
    if existing is not None:
        return existing
    now = _now()
    if plan is None:
        plan = get_default_plan()
    trial_days = max(0, int(plan.trial_days or 0))
    period_end = now + timedelta(days=trial_days) if trial_days else None
    # A zero-day trial is not a trial: the account would be created already
    # locked out, so provision it active instead.
    status = "trial" if trial_days else "active"
    business.subscription_tier = plan.tier
    business.subscription_plan_id = plan.id
    sub = Subscription(
        business_id=business.id,
        plan_id=plan.id,
        status=status,
        started_at=now,
        current_period_start=now,
        current_period_end=period_end,
        trial_ends_at=period_end,
        cancel_at_period_end=False,
    )
    db.session.add(sub)
    db.session.flush()
    business.subscription_status = status
    update_status(business.id)
    commit()
    return sub


def get_plan_for_signup(plan_id):
    """Resolve a plan chosen on the signup form, rejecting unusable ones."""
    if not plan_id:
        return None
    try:
        plan_id = uuidlib.UUID(str(plan_id))
    except (ValueError, AttributeError, TypeError):
        raise ValidationError("Invalid plan_id")
    plan = db.session.get(SubscriptionPlan, plan_id)
    if plan is None or not plan.is_active:
        raise ValidationError("That plan is not available for signup")
    return plan


def public_plan_catalogue() -> list:
    """The plan list a prospective signup is allowed to see.

    Deliberately narrow: marketing-facing fields only, so an anonymous visitor
    cannot enumerate tenant counts, internal codes or module wiring.
    """
    plans = db.session.scalars(
        select(SubscriptionPlan)
        .where(SubscriptionPlan.is_active.is_(True))
        .order_by(SubscriptionPlan.price_cents.asc())
    ).all()
    return [
        {
            "id": str(plan.id),
            "name": plan.name,
            "description": plan.description,
            "tier": plan.tier,
            "price_cents": plan.price_cents,
            "currency": plan.currency,
            "billing_cycle": plan.billing_cycle,
            "trial_days": plan.trial_days or 0,
            "max_users": plan.max_users,
            "max_branches": plan.max_branches,
            "max_invoices": plan.max_invoices,
            "storage_mb": plan.storage_mb,
            "module_count": len(plan.modules or []),
        }
        for plan in plans
    ]


def extend_trial(business_id, days: int, actor_id=None, reason: str | None = None) -> dict:
    """Add trial days to a tenant by hand, e.g. once a payment clears.

    Extends from the later of now and the existing end date, so a lapsed trial
    that is reinstated gets a full fresh window rather than a few hours. A
    tenant whose trial already lapsed is moved back to ``trial`` so the
    extension actually takes effect.
    """
    if days < 1:
        raise ValidationError("Trial extension must be at least 1 day")
    sub = get_subscription(business_id)
    if sub is None:
        raise NotFoundError("Business has no subscription to extend")

    now = _now()
    current_end = _as_aware(sub.trial_ends_at)
    # Lapsed (or never trialled) tenants start counting from now; an active
    # trial is extended from where it already ends.
    base = current_end if current_end and current_end > now else now
    new_end = base + timedelta(days=days)

    was_lapsed = sub.status != "trial" or current_end is None or current_end <= now
    sub.trial_ends_at = new_end
    sub.current_period_end = new_end
    if was_lapsed:
        sub.status = "trial"
        sub.started_at = now
        sub.current_period_start = now
    sub.canceled_at = None
    sub.cancel_at_period_end = False
    sub.updated_by = actor_id
    db.session.flush()

    business = db.session.get(Business, business_id)
    if business is not None:
        business.subscription_status = "trial"
        db.session.commit()
    update_status(business_id)

    from .audit_log_service import log_event

    log_event(
        business_id,
        "update",
        "subscription",
        actor_id,
        sub.id,
        {
            "action": "trial_extended",
            "days": days,
            "reason": reason,
            "previous_trial_ends_at": current_end.isoformat() if current_end else None,
            "trial_ends_at": new_end.isoformat(),
        },
    )
    return {
        "subscription": sub.to_dict(),
        "days_added": days,
        "trial_ends_at": new_end.isoformat(),
        "reinstated": was_lapsed,
    }


def update_status(business_id):
    sub = get_subscription(business_id)
    business = db.session.get(Business, business_id)
    if sub is None:
        business.subscription_tier = "free"
        business.subscription_status = "trial"
        return
    prev_status = business.subscription_status
    if sub.current_period_end is not None and _is_past(sub.current_period_end):
        status = "expired"
    elif sub.trial_ends_at is not None and _is_past(sub.trial_ends_at) and sub.status == "trial":
        status = "past_due"
    elif sub.status == "cancelled":
        status = "cancelled"
    elif sub.status == "suspended":
        status = "suspended"
    else:
        status = sub.status
    business.subscription_status = status
    business.subscription_tier = sub.plan.tier if sub.plan else "free"
    business.subscription_plan_id = sub.plan_id
    commit()

    # Revoke all refresh tokens when the subscription transitions away from an
    # active status so no new access tokens can be minted for the tenant.
    # Existing short-lived access tokens will expire according to their JWT
    # lifetime; per-request checks in get_current_context() block their use.
    if status not in ACTIVE_STATUSES and prev_status in ACTIVE_STATUSES:
        from .auth_service import revoke_refresh_tokens_for_business
        revoke_refresh_tokens_for_business(business_id)

    return status


def suspend_subscriptions(business_id):
    business = db.session.get(Business, business_id)
    if business is None:
        raise NotFoundError("Business not found")
    business.subscription_status = "suspended"
    sub = get_subscription(business_id)
    if sub is not None:
        sub.status = "suspended"
    commit()
    from .auth_service import revoke_refresh_tokens_for_business
    revoke_refresh_tokens_for_business(business_id)


def reactivate_subscriptions(business_id):
    business = db.session.get(Business, business_id)
    if business is None:
        raise NotFoundError("Business not found")
    business.subscription_status = "active"
    sub = get_subscription(business_id)
    if sub is not None and sub.status in ("suspended", "cancelled", "expired", "past_due"):
        sub.status = "active"
    commit()


# ---- Super-admin plan / module management ----

def list_plans(is_active: bool = True):
    stmt = select(SubscriptionPlan).order_by(SubscriptionPlan.is_default.desc(), SubscriptionPlan.price_cents.asc())
    if is_active is not None:
        stmt = stmt.where(SubscriptionPlan.is_active.is_(is_active))
    return db.session.scalars(stmt).all()


def get_plan(plan_id):
    plan = db.session.get(SubscriptionPlan, plan_id)
    if plan is None:
        raise NotFoundError("Subscription plan not found")
    return plan


def create_plan(data, actor_id):
    existing = db.session.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == data.code))
    if existing is not None:
        raise ConflictError("A plan with this code already exists")
    plan = SubscriptionPlan(
        name=data.name,
        code=data.code,
        description=data.description,
        tier=data.tier,
        price_cents=data.price_cents,
        currency=data.currency,
        billing_cycle=data.billing_cycle,
        max_users=data.max_users,
        max_branches=data.max_branches,
        max_invoices=data.max_invoices,
        storage_mb=data.storage_mb,
        trial_days=data.trial_days,
        is_default=data.is_default,
        modules=data.modules or [],
    )
    plan.created_by = actor_id
    plan.updated_by = actor_id
    db.session.add(plan)
    commit()
    return plan


def update_plan(plan_id, data, actor_id):
    plan = get_plan(plan_id)
    for field in (
        "name", "code", "description", "tier", "price_cents", "currency", "billing_cycle",
        "max_users", "max_branches", "max_invoices", "storage_mb", "trial_days", "modules",
    ):
        value = getattr(data, field, None)
        if value is not None:
            setattr(plan, field, value)
    if data.is_default is not None:
        if data.is_default:
            db.session.execute(
                SubscriptionPlan.__table__.update()
                .where(SubscriptionPlan.is_default.is_(True))
                .values(is_default=False)
            )
        plan.is_default = data.is_default
    if data.is_active is not None:
        plan.is_active = data.is_active
    plan.updated_by = actor_id
    commit()
    return plan


def delete_plan(plan_id):
    plan = get_plan(plan_id)
    if plan.is_default:
        raise ConflictError(
            "Cannot delete the default plan. Mark another plan as default first."
        )
    in_use = db.session.scalar(select(func.count()).where(Subscription.plan_id == plan_id))
    if in_use:
        # Deleting a plan that tenants have billed against would orphan their
        # subscription history, so refuse and point at the reversible option.
        raise ConflictError(
            "Cannot delete a plan that has subscriptions. Deactivate it instead "
            "to stop new signups while keeping existing tenants intact."
        )
    db.session.delete(plan)
    commit()


def list_modules():
    return [m.to_dict() for m in db.session.scalars(select(SystemModule).order_by(SystemModule.is_core.desc(), SystemModule.code)).all()]


def get_module(module_id):
    module = db.session.get(SystemModule, module_id)
    if module is None:
        raise NotFoundError("System module not found")
    return module


def update_module(module_id, data, actor_id):
    module = get_module(module_id)
    if data.is_enabled is not None:
        module.is_enabled = data.is_enabled
    if data.min_plan_tier is not None:
        module.min_plan_tier = data.min_plan_tier
    module.updated_by = actor_id
    commit()
    return module


def create_module(data, actor_id):
    existing = db.session.scalar(select(SystemModule).where(SystemModule.code == data.code))
    if existing is not None:
        raise ConflictError("A module with this code already exists")
    module = SystemModule(
        code=data.code,
        name=data.name,
        description=data.description,
        is_enabled=True if data.is_enabled is None else data.is_enabled,
        is_core=False if data.is_core is None else data.is_core,
        min_plan_tier=data.min_plan_tier,
    )
    module.created_by = actor_id
    module.updated_by = actor_id
    db.session.add(module)
    commit()
    return module


def delete_module(module_id):
    module = get_module(module_id)
    if module.is_core:
        raise ConflictError("Cannot delete a core module")
    db.session.delete(module)
    commit()


def get_subscription_service(business_id):
    return SubscriptionService(business_id)


class SubscriptionService:
    """Instance-style facade retained for compatibility with other services."""

    def __init__(self, business_id):
        self.business_id = business_id

    @property
    def plan(self):
        return _resolved_plan(self.business_id)

    @property
    def subscription(self):
        return get_subscription(self.business_id)

    def is_active(self):
        return is_subscription_active(self.business_id)

    def can_access(self, module_code):
        return can_access_module(self.business_id, module_code)

    def accessible_modules(self):
        return accessible_modules(self.business_id)

    def ensure(self, business=None):
        business = business or db.session.get(Business, self.business_id)
        return ensure_subscription(business)

    def suspend(self):
        return suspend_subscriptions(self.business_id)

    def reactivate(self):
        return reactivate_subscriptions(self.business_id)
