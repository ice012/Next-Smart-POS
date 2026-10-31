from flask import Blueprint, Response, g, request

from ...schemas import ReportQuerySchema
from ...services import generate_report, export_report_csv, export_report_pdf
from ...validation import validate_query
from ..permissions import login_required, require_module, require_permission
from ..responses import ok

reports_bp = Blueprint("reports", __name__)


def _file_response(payload, mimetype, filename):
    response = Response(payload, mimetype=mimetype)
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.headers["Content-Length"] = str(len(payload))
    return response


@reports_bp.route("", methods=["GET"])
@login_required
@require_module("reports")
@require_permission("report.read")
@validate_query(ReportQuerySchema)
def get_report():
    q = request.validated_query
    export = q.export or request.args.get("export")
    if export in ("csv", "pdf"):
        kwargs = {
            "from_date": q.from_date,
            "to_date": q.to_date,
            "aging_date": q.aging_date,
        }
        if export == "csv":
            payload = export_report_csv(q.report, g.ctx.business_id, **kwargs)
            return _file_response(payload, "text/csv", f"report-{q.report}.csv")
        payload = export_report_pdf(q.report, g.ctx.business_id, **kwargs)
        return _file_response(payload, "application/pdf", f"report-{q.report}.pdf")

    result = generate_report(
        q.report, g.ctx.business_id,
        from_date=q.from_date, to_date=q.to_date, aging_date=q.aging_date,
    )
    return ok(result)
