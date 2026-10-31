from flask import Blueprint, g, request

from ...schemas import (
    QuotationConvertSchema,
    QuotationCreateSchema,
    QuotationSearchSchema,
    QuotationStatusSchema,
    QuotationUpdateSchema,
)
from ...services.quotation_service import (
    convert_quotation_to_sale as convert_quotation_service,
    create_quotation as create_quotation_service,
    delete_quotation as delete_quotation_service,
    get_quotation as get_quotation_service,
    list_quotations as list_quotations_service,
    set_quotation_status as set_quotation_status_service,
    update_quotation as update_quotation_service,
)
from ...utils.security import parse_uuid
from ...validation import validate_body, validate_query
from ..permissions import login_required, require_module, require_permission
from ..responses import created, no_content, ok

quotations_bp = Blueprint("quotations", __name__)


def _business_id():
    return g.ctx.business_id


@quotations_bp.route("", methods=["GET"])
@login_required
@require_module("quotations")
@require_permission("quotation.read")
@validate_query(QuotationSearchSchema)
def list_quotations_route():
    q = request.validated_query
    items, total, pages = list_quotations_service(
        _business_id(), q,
        status=q.status, customer_id=q.customer_id,
        from_date=q.from_date, to_date=q.to_date, q=q.q,
    )
    return ok(
        {"quotations": items},
        meta={"page": q.page, "per_page": q.per_page, "total": total, "pages": pages},
    )


@quotations_bp.route("", methods=["POST"])
@login_required
@require_module("quotations")
@require_permission("quotation.write")
@validate_body(QuotationCreateSchema)
def create_quotation_route():
    quotation = create_quotation_service(_business_id(), request.validated_body, g.ctx.user_id)
    return created({"quotation": quotation})


@quotations_bp.route("/<quotation_id>", methods=["GET"])
@login_required
@require_module("quotations")
@require_permission("quotation.read")
def get_quotation_route(quotation_id):
    return ok({"quotation": get_quotation_service(_business_id(), parse_uuid(quotation_id))})


@quotations_bp.route("/<quotation_id>/pdf", methods=["GET"])
@login_required
@require_module("quotations")
@require_permission("quotation.read")
def get_quotation_pdf_route(quotation_id):
    """Printable A4 quotation, for viewing or downloading."""
    from flask import send_file
    from io import BytesIO

    from ...exceptions import BusinessError
    from ...services import business_service
    from ...services.pdf_service import PDFGenerationError, get_pdf_service
    from ...services.quotation_service import get_quotation_record

    business_id = _business_id()
    quotation = get_quotation_record(business_id, parse_uuid(quotation_id))
    config = business_service.get_config(business_id)

    try:
        pdf_bytes = get_pdf_service(business_id).generate_quotation_pdf(
            quotation, g.ctx.business, settings=config,
        )
    except PDFGenerationError as exc:
        raise BusinessError(f"Quotation PDF generation failed: {exc}")

    return send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=False,
        download_name=f"quotation-{quotation.quotation_number or quotation.id}.pdf",
    )


@quotations_bp.route("/<quotation_id>", methods=["PATCH"])
@login_required
@require_module("quotations")
@require_permission("quotation.write")
@validate_body(QuotationUpdateSchema)
def update_quotation_route(quotation_id):
    quotation = update_quotation_service(
        _business_id(), parse_uuid(quotation_id), request.validated_body, g.ctx.user_id
    )
    return ok({"quotation": quotation})


@quotations_bp.route("/<quotation_id>/status", methods=["POST"])
@login_required
@require_module("quotations")
@require_permission("quotation.write")
@validate_body(QuotationStatusSchema)
def set_quotation_status_route(quotation_id):
    quotation = set_quotation_status_service(
        _business_id(), parse_uuid(quotation_id),
        request.validated_body.status, g.ctx.user_id,
    )
    return ok({"quotation": quotation})


@quotations_bp.route("/<quotation_id>/convert", methods=["POST"])
@login_required
@require_module("quotations")
@require_permission("quotation.write")
@validate_body(QuotationConvertSchema)
def convert_quotation_route(quotation_id):
    sale = convert_quotation_service(
        _business_id(), parse_uuid(quotation_id), request.validated_body, g.ctx.user_id
    )
    return created({"sale": sale})


@quotations_bp.route("/<quotation_id>", methods=["DELETE"])
@login_required
@require_module("quotations")
@require_permission("quotation.delete")
def delete_quotation_route(quotation_id):
    delete_quotation_service(_business_id(), parse_uuid(quotation_id), g.ctx.user_id)
    return no_content()
