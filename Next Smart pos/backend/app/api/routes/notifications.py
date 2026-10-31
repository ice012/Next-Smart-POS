from flask import Blueprint, g, request

from ...services import (
    get_notification_service, create_notification, mark_read, mark_all_read,
    list_notifications, unread_count, generate_low_stock_notifications,
    generate_expense_due_reminders,
)
from ...schemas import NotificationMarkReadSchema, PaginationSchema
from ...validation import validate_body, validate_query
from ..permissions import login_required, require_module, require_permission, require_permission_or_privileged
from ..responses import ok, created

notifications_bp = Blueprint("notifications", __name__)


def _business_id():
    return g.ctx.business_id


@notifications_bp.route("", methods=["GET"])
@login_required
@require_module("notifications")
@require_permission("notification.read")
@validate_query(PaginationSchema)
def list_notes():
    pagination = request.validated_query
    items, total, pages = list_notifications(_business_id(), pagination, user_id=g.ctx.user_id)
    return ok(
        {"notifications": [n.to_dict() for n in items]},
        meta={"page": pagination.page, "per_page": pagination.per_page, "total": total, "pages": pages},
    )


@notifications_bp.route("/unread-count", methods=["GET"])
@login_required
@require_permission("notification.read")
def unread():
    count = unread_count(_business_id(), user_id=g.ctx.user_id)
    return ok({"unread_count": count})


@notifications_bp.route("/mark-read", methods=["POST"])
@login_required
@require_permission("notification.read")
@validate_body(NotificationMarkReadSchema)
def mark_read_route():
    mark_read(_business_id(), request.validated_body.notification_ids, user_id=g.ctx.user_id)
    return ok({"marked_read": len(request.validated_body.notification_ids)})


@notifications_bp.route("/mark-all-read", methods=["POST"])
@login_required
@require_permission("notification.read")
def mark_all():
    mark_all_read(_business_id(), user_id=g.ctx.user_id)
    return ok({"marked_read": "all"})


@notifications_bp.route("/low-stock-check", methods=["POST"])
@login_required
@require_module("notifications")
@require_permission_or_privileged("notification.write")
def low_stock_check():
    count = generate_low_stock_notifications(_business_id())
    return ok({"generated": count})


@notifications_bp.route("/expense-due-check", methods=["POST"])
@login_required
@require_module("notifications")
@require_permission_or_privileged("notification.write")
def expense_due_check():
    """Raise owner reminders for recurring expenses due within the next week."""
    data = request.json or {}
    count = generate_expense_due_reminders(_business_id(), days_ahead=data.get("days_ahead"))
    return ok({"generated": count})


@notifications_bp.route("", methods=["POST"])
@login_required
@require_module("notifications")
@require_permission_or_privileged("notification.write")
def create_note():
    data = request.json or {}
    note = create_notification(
        business_id=_business_id(),
        user_id=data.get("user_id"),
        category=data.get("category", "system"),
        title=data.get("title", ""),
        message=data.get("message", ""),
        priority=data.get("priority", "normal"),
        channels=data.get("channels") or ["in_app"],
        meta=data.get("meta"),
    )
    return created({"notification": note.to_dict()})
