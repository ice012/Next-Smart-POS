from flask import Blueprint, g, request

from ...exceptions import NotFoundError
from ...schemas import AuditLogSearchSchema
from ...services.audit_log_service import list_audit_logs, get_audit_log
from ...utils.security import parse_uuid
from ...validation import validate_query
from ..permissions import login_required, require_module, require_permission
from ..responses import ok

audit_bp = Blueprint("audit", __name__)


@audit_bp.route("", methods=["GET"])
@login_required
@require_module("audit")
@require_permission("audit.read")
@validate_query(AuditLogSearchSchema)
def list_logs():
    q = request.validated_query
    items, total, pages = list_audit_logs(
        g.ctx.business_id, q,
        action=q.action, resource=q.resource,
        user_id=q.user_id, resource_id=q.resource_id,
        from_date=q.from_date, to_date=q.to_date,
    )
    return ok(
        {"logs": [log.to_dict() for log in items]},
        meta={"page": q.page, "per_page": q.per_page, "total": total, "pages": pages},
    )


@audit_bp.route("/<log_id>", methods=["GET"])
@login_required
@require_module("audit")
@require_permission("audit.read")
def get_log(log_id):
    log = get_audit_log(g.ctx.business_id, parse_uuid(log_id))
    if log is None:
        raise NotFoundError("Audit log not found")
    return ok({"log": log.to_dict()})
