"""Super Admin service for cross-tenant administration."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import desc, func, select

from ..extensions import db
from ..models import (
    AuditLog,
    Business,
    BusinessUser,
    Invoice,
    Role,
    Subscription,
    SubscriptionPlan,
    SystemModule,
    User,
)
from .subscription_service import (
    create_plan,
    delete_plan,
    get_plan,
    list_plans,
    update_plan,
)


def _now():
    return datetime.now(timezone.utc)


def superadmin_stats() -> dict:
    total_businesses = db.session.scalar(select(func.count()).select_from(Business)) or 0
    total_users = db.session.scalar(select(func.count()).select_from(User)) or 0
    total_subs = db.session.scalar(select(func.count()).select_from(Subscription)) or 0
    active_subs = db.session.scalar(
        select(func.count()).select_from(Subscription).where(Subscription.status == "active")
    ) or 0
    trial_subs = db.session.scalar(
        select(func.count()).select_from(Subscription).where(Subscription.status == "trial")
    ) or 0
    total_revenue = db.session.scalar(
        select(func.coalesce(func.sum(Invoice.total_amount), 0)).where(
            Invoice.status.in_(["sent", "partially_paid", "paid", "overdue"])
        )
    ) or 0
    now = _now()
    expiring = db.session.scalar(
        select(func.count()).select_from(Subscription).where(
            Subscription.status == "trial",
            Subscription.trial_ends_at.is_not(None),
            Subscription.trial_ends_at <= now + timedelta(days=7),
            Subscription.trial_ends_at >= now,
        )
    ) or 0
    return {
        "total_businesses": total_businesses,
        "total_users": total_users,
        "total_subscriptions": total_subs,
        "active_subscriptions": active_subs,
        "trial_subscriptions": trial_subs,
        "expiring_soon": expiring,
        "total_revenue": round(float(total_revenue), 2),
    }


def list_all_businesses(pagination=None, status=None, tier=None):
    stmt = select(Business).order_by(Business.created_at.desc())
    if tier:
        stmt = stmt.where(Business.subscription_tier == tier)
    if status in ("active", "trial"):
        stmt = stmt.where(Business.subscription_status == status)
    elif status in ("inactive", "suspended"):
        stmt = stmt.where(Business.is_active.is_(False))
    if pagination:
        stmt = stmt.limit(pagination.per_page).offset((pagination.page - 1) * pagination.per_page)
    return db.session.scalars(stmt).all()


def get_business_detail(business_id) -> dict:
    business = db.session.get(Business, business_id)
    if business is None:
        return None
    subscription = db.session.scalar(
        select(Subscription).where(Subscription.business_id == business_id)
    )
    user_count = db.session.scalar(
        select(func.count()).select_from(BusinessUser).where(
            BusinessUser.business_id == business_id,
            BusinessUser.is_active.is_(True),
        )
    ) or 0
    from .subscription_service import plan_limit_status

    return {
        "business": business.to_dict(),
        "subscription": subscription.to_dict() if subscription else None,
        "user_count": user_count,
        "limits": plan_limit_status(business_id),
    }


def set_business_status(business_id, status) -> dict:
    from .subscription_service import reactivate_subscriptions, suspend_subscriptions

    business = db.session.get(Business, business_id)
    if business is None:
        return None
    if status in ("inactive", "suspended"):
        business.is_active = False
        suspend_subscriptions(business_id)
    else:
        business.is_active = True
        if status in ("active", "trial"):
            reactivate_subscriptions(business_id)
            if status == "trial":
                business.subscription_status = "trial"
    db.session.commit()
    return business.to_dict()


def change_business_plan(business_id, plan_id, reason=None) -> dict:
    from datetime import timedelta

    from ..exceptions import BusinessError
    from .subscription_service import LIMIT_RESOURCES, plan_usage

    business = db.session.get(Business, business_id)
    plan = db.session.get(SubscriptionPlan, plan_id)
    if business is None or plan is None:
        return None

    # Moving a tenant onto a plan it has already outgrown would leave it in
    # breach the moment it lands, with no way to tell which cap it broke.
    # Refuse instead and name the offending cap.
    usage = plan_usage(business_id)
    breaches = [
        f"{key} ({usage[key]}/{getattr(plan, column, None)})"
        for key, column in LIMIT_RESOURCES.items()
        if getattr(plan, column, None) is not None and usage[key] > getattr(plan, column)
    ]
    if breaches:
        raise BusinessError(
            f"{business.name} already exceeds the {plan.name} plan: "
            + ", ".join(breaches)
            + ". Raise the plan limits first or remove the excess records.",
            status_code=409,
            payload={"plan_id": str(plan_id), "usage": usage, "breaches": breaches},
        )

    subscription = db.session.scalar(
        select(Subscription).where(Subscription.business_id == business_id)
    )
    now = _now()
    if subscription is None:
        subscription = Subscription(
            business_id=business_id,
            plan_id=plan_id,
            status="active",
            started_at=now,
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
        )
        db.session.add(subscription)
    else:
        subscription.plan_id = plan_id
        subscription.status = "active"
        subscription.started_at = now
        subscription.current_period_start = now
        subscription.current_period_end = now + timedelta(days=30)
        subscription.trial_ends_at = None
        subscription.canceled_at = None
    business.subscription_plan_id = plan.id
    business.subscription_tier = plan.tier
    business.subscription_status = "active"
    db.session.commit()
    return {
        "business": business.to_dict(),
        "subscription": subscription.to_dict(),
    }


def list_global_plans():
    return list_plans()


def get_global_plan(plan_id):
    try:
        return get_plan(plan_id)
    except Exception:
        return None


def create_global_plan(**data) -> SubscriptionPlan:
    from .subscription_service import validate_plan_modules

    plan_data = SimpleNamespace(
        name=data["name"],
        code=data["code"],
        description=data.get("description"),
        tier=data["tier"],
        price_cents=data["price_cents"],
        currency=data["currency"],
        billing_cycle=data["billing_cycle"],
        max_users=data.get("max_users"),
        max_branches=data.get("max_branches"),
        max_invoices=data.get("max_invoices"),
        storage_mb=data.get("storage_mb", 0),
        trial_days=data.get("trial_days", 14),
        is_active=data.get("is_active", True),
        is_default=data.get("is_default", False),
        modules=validate_plan_modules(data.get("modules") or []),
    )
    plan = create_plan(plan_data, actor_id=None)
    if data.get("module_ids"):
        modules = db.session.scalars(
            select(SystemModule).where(SystemModule.id.in_(data["module_ids"]))
        ).all()
        plan.modules = [m.code for m in modules]
        db.session.commit()
    return plan


def update_global_plan(plan_id, **data) -> SubscriptionPlan:
    from .subscription_service import validate_plan_modules

    allowed = (
        "name", "description", "tier", "price_cents", "currency", "billing_cycle",
        "max_users", "max_branches", "max_invoices", "storage_mb", "trial_days",
        "is_active", "is_default", "modules",
    )
    payload = {k: v for k, v in data.items() if k in allowed}
    if "modules" in payload:
        payload["modules"] = validate_plan_modules(payload["modules"] or [])
    for field in ("is_active", "is_default", "name", "code", "tier", "price_cents", "currency", "billing_cycle", "storage_mb", "trial_days"):
        payload.setdefault(field, None)
    plan = update_plan(plan_id, SimpleNamespace(**payload), actor_id=None)
    return plan


def delete_global_plan(plan_id) -> bool:
    delete_plan(plan_id)
    return True


def delete_business(business_id) -> bool:
    from ..models import (
        AuditLog,
        Branch,
        BusinessConfig,
        BusinessUser,
        Invoice,
        Notification,
        PasswordResetToken,
        RefreshToken,
        Role,
        RolePermission,
        Subscription,
        User,
    )

    business = db.session.get(Business, business_id)
    if business is None:
        return None
    try:
        # Remove dependent records in a safe order
        db.session.query(AuditLog).filter(AuditLog.business_id == business_id).delete()
        db.session.query(Invoice).filter(Invoice.business_id == business_id).delete()
        db.session.query(Subscription).filter(Subscription.business_id == business_id).delete()
        db.session.query(BusinessUser).filter(BusinessUser.business_id == business_id).delete()
        db.session.query(Branch).filter(Branch.business_id == business_id).delete()
        db.session.query(BusinessConfig).filter(BusinessConfig.business_id == business_id).delete()
        db.session.query(Notification).filter(Notification.business_id == business_id).delete()
        # Remove roles and permissions for this business
        role_ids = [r.id for r in db.session.scalars(select(Role).where(Role.business_id == business_id)).all()]
        if role_ids:
            db.session.query(RolePermission).filter(RolePermission.role_id.in_(role_ids)).delete(synchronize_session=False)
            db.session.query(Role).filter(Role.id.in_(role_ids)).delete(synchronize_session=False)
        db.session.delete(business)
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        raise


def list_all_users(pagination=None, business_id=None, role=None):
    stmt = select(User).order_by(User.created_at.desc())
    if business_id:
        stmt = stmt.join(BusinessUser, BusinessUser.user_id == User.id).where(
            BusinessUser.business_id == business_id
        )
    if role:
        stmt = stmt.join(BusinessUser, BusinessUser.user_id == User.id).join(
            Role, Role.id == BusinessUser.role_id
        ).where(Role.name == role)
    if pagination:
        stmt = stmt.limit(pagination.per_page).offset((pagination.page - 1) * pagination.per_page)
    return db.session.scalars(stmt).unique().all()


def list_global_audit_logs(pagination=None, action=None, resource=None, business_id=None):
    stmt = select(AuditLog).order_by(desc(AuditLog.created_at))
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if resource:
        stmt = stmt.where(AuditLog.resource == resource)
    if business_id:
        stmt = stmt.where(AuditLog.business_id == business_id)
    if pagination:
        stmt = stmt.limit(pagination.per_page).offset((pagination.page - 1) * pagination.per_page)
    return db.session.scalars(stmt).all()


def plan_usage_summary() -> list:
    """Per-plan consumption rollup for the super-admin plan table.

    Reports how many tenants sit on each plan, their aggregate usage, and how
    many of them are at or over a cap, so an operator can see which plan a
    tenant needs to move to before they hit a wall.
    """
    from .subscription_service import LIMIT_RESOURCES, plan_usage

    summary = {
        plan.id: {
            "businesses": 0,
            "usage": {key: 0 for key in LIMIT_RESOURCES},
            "at_limit": 0,
        }
        for plan in db.session.scalars(select(SubscriptionPlan)).all()
    }
    rows = db.session.execute(
        select(Subscription.business_id, Subscription.plan_id)
    ).all()
    for business_id, plan_id in rows:
        entry = summary.get(plan_id)
        if entry is None:
            continue
        entry["businesses"] += 1
        usage = plan_usage(business_id)
        for key in LIMIT_RESOURCES:
            entry["usage"][key] += usage[key]
        plan = db.session.get(SubscriptionPlan, plan_id)
        if plan is not None and any(
            getattr(plan, column, None) is not None and usage[key] >= getattr(plan, column)
            for key, column in LIMIT_RESOURCES.items()
        ):
            entry["at_limit"] += 1
    return [
        {"plan_id": str(plan_id), **payload}
        for plan_id, payload in summary.items()
    ]


def get_superadmin_service():
    return SimpleNamespace(
        stats=superadmin_stats,
        list_businesses=list_all_businesses,
        business_detail=get_business_detail,
        set_status=set_business_status,
        change_plan=change_business_plan,
        delete_business=delete_business,
        plans=list_global_plans,
        plan=get_global_plan,
        create_plan=create_global_plan,
        update_plan=update_global_plan,
        delete_plan=delete_global_plan,
        users=list_all_users,
        audit_logs=list_global_audit_logs,
    )


__all__ = [
    "get_superadmin_service", "superadmin_stats", "list_all_businesses", "get_business_detail",
    "set_business_status", "change_business_plan", "delete_business", "list_global_plans",
    "get_global_plan", "create_global_plan", "update_global_plan",
    "delete_global_plan", "list_all_users", "list_global_audit_logs",
    "plan_usage_summary",
]
