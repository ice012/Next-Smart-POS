from flask import Blueprint, g, request, send_file
from io import BytesIO

from ...services import get_business_dashboard, export_report_csv, export_report_pdf
from ...validation import validate_query
from ..permissions import login_required, require_module, require_permission
from ..responses import ok

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("", methods=["GET"])
@login_required
@require_module("dashboard")
@require_permission("dashboard.read")
def get_dashboard():
    from_date = request.args.get("from_date")
    to_date = request.args.get("to_date")
    data = get_business_dashboard(g.ctx.business_id, from_date=from_date, to_date=to_date)
    return ok(data)
