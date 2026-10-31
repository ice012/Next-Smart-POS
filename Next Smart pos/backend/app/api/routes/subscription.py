from flask import Blueprint, g, request

from ...models import Subscription
from ...services.subscription_service import list_plans, get_plan, can_access_module
from ...validation import validate_query
from ..permissions import login_required
from ..responses import ok

subscription_bp = Blueprint("subscription", __name__)


def _business_id():
    return g.ctx.business_id


@subscription_bp.route("", methods=["GET"])
@login_required
def current_subscription():
    business = g.ctx.business
    sub = None
    subs = business.subscriptions
    if subs:
        sub = max(subs, key=lambda s: s.started_at or s.current_period_start)
    plan_dict = sub.plan.to_dict() if (sub and sub.plan) else None
    return ok({
        "business": business.to_dict(),
        "subscription": sub.to_dict() if sub else None,
        "plan": plan_dict,
        "modules": sub.plan.modules if (sub and sub.plan) else [],
    })


@subscription_bp.route("/plans", methods=["GET"])
@login_required
def available_plans():
    plans = [p.to_dict() for p in list_plans() if p.is_active]
    return ok({"plans": plans, "current_tier": g.ctx.business.subscription_tier})
