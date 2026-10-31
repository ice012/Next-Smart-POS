from flask import Blueprint, g, request

from ...schemas import (
    PaginationSchema, ReportQuerySchema,
    SubscriptionPlanSchema, SubscriptionPlanUpdateSchema,
    BusinessStatusSchema, PlanChangeSchema, TrialExtensionSchema,
)
from ...services import (
    superadmin_stats, list_all_businesses, get_business_detail,
    set_business_status, change_business_plan, delete_business,
    list_global_plans, get_global_plan, create_global_plan, update_global_plan,
    delete_global_plan, list_all_users, list_global_audit_logs, plan_usage_summary,
)
from ...services.subscription_service import extend_trial, list_modules, update_module
from ...utils.security import parse_uuid
from ...validation import validate_body, validate_query
from ..permissions import require_superadmin
from ..responses import ok, created, no_content

superadmin_bp = Blueprint("superadmin", __name__)


@superadmin_bp.route("/stats", methods=["GET"])
@require_superadmin
def stats():
    return ok(superadmin_stats())


@superadmin_bp.route("/businesses", methods=["GET"])
@require_superadmin
@validate_query(PaginationSchema)
def businesses():
    items = list_all_businesses(pagination=request.validated_query)
    return ok({"businesses": [b.to_dict() for b in items]})


@superadmin_bp.route("/businesses/<business_id>", methods=["GET"])
@require_superadmin
def business_detail(business_id):
    detail = get_business_detail(parse_uuid(business_id))
    if detail is None:
        from ...exceptions import NotFoundError
        raise NotFoundError("Business not found")
    return ok(detail)


@superadmin_bp.route("/businesses/<business_id>/status", methods=["PATCH"])
@require_superadmin
@validate_body(BusinessStatusSchema)
def business_status(business_id):
    updated = set_business_status(parse_uuid(business_id), request.validated_body.status)
    if updated is None:
        from ...exceptions import NotFoundError
        raise NotFoundError("Business not found")
    return ok(updated)


@superadmin_bp.route("/businesses/<business_id>/plan", methods=["POST"])
@require_superadmin
@validate_body(PlanChangeSchema)
def assign_plan(business_id):
    result = change_business_plan(parse_uuid(business_id), parse_uuid(request.validated_body.plan_id))
    if result is None:
        from ...exceptions import NotFoundError
        raise NotFoundError("Business or plan not found")
    return ok(result)


@superadmin_bp.route("/businesses/<business_id>", methods=["DELETE"])
@require_superadmin
def delete_business_route(business_id):
    result = delete_business(parse_uuid(business_id))
    if result is None:
        from ...exceptions import NotFoundError
        raise NotFoundError("Business not found")
    return no_content()


@superadmin_bp.route("/businesses/<business_id>/trial", methods=["POST"])
@require_superadmin
@validate_body(TrialExtensionSchema)
def extend_business_trial(business_id):
    result = extend_trial(
        parse_uuid(business_id),
        request.validated_body.days,
        reason=request.validated_body.reason,
    )
    return ok(result)


@superadmin_bp.route("/plans", methods=["GET"])
@require_superadmin
def plans():
    return ok({"plans": [p.to_dict() for p in list_global_plans()]})


@superadmin_bp.route("/plans", methods=["POST"])
@require_superadmin
@validate_body(SubscriptionPlanSchema)
def create_plan():
    plan = create_global_plan(**request.validated_body.model_dump())
    return created({"plan": plan.to_dict()})


@superadmin_bp.route("/plans/usage", methods=["GET"])
@require_superadmin
def plans_usage():
    return ok({"usage": plan_usage_summary()})


@superadmin_bp.route("/plans/<plan_id>", methods=["GET"])
@require_superadmin
def get_plan(plan_id):
    plan = get_global_plan(parse_uuid(plan_id))
    if plan is None:
        from ...exceptions import NotFoundError
        raise NotFoundError("Plan not found")
    return ok({"plan": plan.to_dict()})


@superadmin_bp.route("/plans/<plan_id>", methods=["PATCH"])
@require_superadmin
@validate_body(SubscriptionPlanUpdateSchema)
def update_plan(plan_id):
    plan = update_global_plan(parse_uuid(plan_id), **request.validated_body.model_dump(exclude_unset=True))
    if plan is None:
        from ...exceptions import NotFoundError
        raise NotFoundError("Plan not found")
    return ok({"plan": plan.to_dict()})


@superadmin_bp.route("/plans/<plan_id>", methods=["DELETE"])
@require_superadmin
def delete_plan(plan_id):
    if not delete_global_plan(parse_uuid(plan_id)):
        from ...exceptions import NotFoundError
        raise NotFoundError("Plan not found")
    return no_content()


@superadmin_bp.route("/modules", methods=["GET"])
@require_superadmin
def modules():
    return ok({"modules": list_modules()})


@superadmin_bp.route("/modules/<module_id>", methods=["PATCH"])
@require_superadmin
def patch_module(module_id):
    from types import SimpleNamespace
    data = request.json or {}
    module = update_module(
        parse_uuid(module_id),
        SimpleNamespace(
            is_enabled=data.get("is_enabled"),
            min_plan_tier=data.get("min_plan_tier"),
        ),
        actor_id=None,
    )
    return ok({"module": module.to_dict()})


@superadmin_bp.route("/modules", methods=["POST"])
@require_superadmin
def create_module():
    from types import SimpleNamespace
    from ...services.subscription_service import create_module as create
    from ...models import SYSTEM_MODULES as catalog
    data = request.json or {}
    code = data.get("code")
    definition = next((m for m in catalog if m["code"] == code), None)
    if definition is None and not data.get("name"):
        from ...exceptions import ValidationError
        raise ValidationError("Unknown module code")
    module = create(
        SimpleNamespace(
            code=code,
            name=data.get("name") or (definition or {}).get("name"),
            description=data.get("description") or (definition or {}).get("description"),
            is_enabled=data.get("is_enabled", True),
            is_core=data.get("is_core", (definition or {}).get("is_core", False)),
            min_plan_tier=data.get("min_plan_tier"),
        ),
        actor_id=None,
    )
    return created({"module": module.to_dict()})


@superadmin_bp.route("/modules/<module_id>/delete", methods=["POST"])
@require_superadmin
def delete_module(module_id):
    from ...services.subscription_service import delete_module as delete
    delete(parse_uuid(module_id))
    return no_content()


@superadmin_bp.route("/users", methods=["GET"])
@require_superadmin
@validate_query(PaginationSchema)
def users():
    items = list_all_users(pagination=request.validated_query,
                           role=request.args.get("role"))
    return ok({"users": [u.to_dict() for u in items]})


@superadmin_bp.route("/reports", methods=["GET"])
@require_superadmin
@validate_query(ReportQuerySchema)
def global_report():
    q = request.validated_query
    # Aggregate a simple revenue-by-business report across all tenants.
    from ...models import Invoice
    from ...extensions import db as _db
    from sqlalchemy import func as _fn
    report = q.report if q.report != "profit_and_loss" else "sales"
    if report == "sales":
        rows = _db.session.execute(
            _db.select(Invoice.business_id, _fn.coalesce(_fn.sum(Invoice.total_amount), 0).label("total"))
            .where(Invoice.status.in_(["sent", "partially_paid", "paid", "overdue"]))
        ).all()
        businesses = [r[0] for r in rows]
        return ok({"businesses": [str(b) for b in businesses], "sales": [round(float(r[1]), 2) for r in rows]})
    return ok({"report": report, "scope": "all_tenants"})


@superadmin_bp.route("/audit-logs", methods=["GET"])
@require_superadmin
@validate_query(PaginationSchema)
def audit_logs():
    logs = list_global_audit_logs(pagination=request.validated_query,
                                  action=request.args.get("action"),
                                  resource=request.args.get("resource"),
                                  business_id=request.args.get("business_id"))
    return ok({"logs": [l.to_dict() for l in logs]})
