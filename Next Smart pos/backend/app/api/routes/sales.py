from io import BytesIO

from flask import Blueprint, g, request

from ...schemas import (
    PaymentSchema,
    SaleCancelSchema,
    SaleCreateSchema,
    SaleSearchSchema,
    SaleUpdateSchema,
)
from ...services.sale_service import (
    add_sale_payment as add_sale_payment_service,
    cancel_sale as cancel_sale_service,
    create_sale as create_sale_service,
    delete_sale as delete_sale_service,
    get_sale as get_sale_service,
    list_sales as list_sales_service,
    search_sales as search_sales_service,
    update_sale as update_sale_service,
)
from ...utils.security import parse_uuid
from ...validation import validate_body, validate_query
from ..permissions import login_required, require_module, require_permission
from ..responses import created, no_content, ok

sales_bp = Blueprint("sales", __name__)


def _business_id():
    return g.ctx.business_id


@sales_bp.route("", methods=["GET"])
@login_required
@require_module("sales")
@require_permission("sale.read")
@validate_query(SaleSearchSchema)
def list_sales_route():
    q = request.validated_query
    items, total, pages = list_sales_service(
        _business_id(), q,
        status=q.status, sale_type=q.sale_type, customer_id=q.customer_id,
        branch_id=q.branch_id, from_date=q.from_date, to_date=q.to_date, q=q.q,
    )
    return ok(
        {"sales": items},
        meta={"page": q.page, "per_page": q.per_page, "total": total, "pages": pages},
    )


@sales_bp.route("/search", methods=["GET"])
@login_required
@require_module("sales")
@require_permission("sale.read")
def search_sales_route():
    term = request.args.get("q", "")
    limit = int(request.args.get("limit", "20") or "20")
    return ok({"sales": search_sales_service(_business_id(), term, limit)})


@sales_bp.route("", methods=["POST"])
@login_required
@require_module("sales")
@require_permission("sale.write")
@validate_body(SaleCreateSchema)
def create_sale_route():
    sale = create_sale_service(_business_id(), request.validated_body, g.ctx.user_id)
    return created({"sale": sale})


@sales_bp.route("/<sale_id>", methods=["GET"])
@login_required
@require_module("sales")
@require_permission("sale.read")
def get_sale_route(sale_id):
    return ok({"sale": get_sale_service(_business_id(), parse_uuid(sale_id))})


@sales_bp.route("/<sale_id>", methods=["PATCH"])
@login_required
@require_module("sales")
@require_permission("sale.write")
@validate_body(SaleUpdateSchema)
def update_sale_route(sale_id):
    sale = update_sale_service(
        _business_id(), parse_uuid(sale_id), request.validated_body, g.ctx.user_id
    )
    return ok({"sale": sale})


@sales_bp.route("/<sale_id>/cancel", methods=["POST"])
@login_required
@require_module("sales")
@require_permission("sale.write")
@validate_body(SaleCancelSchema)
def cancel_sale_route(sale_id):
    sale = cancel_sale_service(
        _business_id(), parse_uuid(sale_id), g.ctx.user_id,
        reason=request.validated_body.reason,
    )
    return ok({"sale": sale})


@sales_bp.route("/<sale_id>/receipt", methods=["GET"])
@login_required
@require_module("sales")
@require_permission("sale.read")
def get_sale_receipt_route(sale_id):
    """Printable 80mm receipt for a completed sale."""
    from flask import send_file

    from ...exceptions import BusinessError
    from ...services import business_service
    from ...services.pdf_service import PDFGenerationError, get_pdf_service
    from ...services.sale_service import get_sale_record

    business_id = _business_id()
    sale = get_sale_record(business_id, parse_uuid(sale_id))
    config = business_service.get_config(business_id)

    # Cash handed over and change are a property of this till transaction, not
    # of the stored sale, so the caller passes them through.
    def _number(name):
        raw = request.args.get(name)
        if raw is None or raw == "":
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    try:
        pdf_bytes = get_pdf_service(business_id).generate_receipt_pdf(
            sale,
            g.ctx.business,
            served_by=_cashier_name(g.ctx),
            tendered=_number("tendered"),
            change_due=_number("change_due"),
            settings=config,
        )
    except PDFGenerationError as exc:
        raise BusinessError(f"Receipt generation failed: {exc}")

    return send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=False,
        download_name=f"receipt-{sale.invoice_number}.pdf",
    )


def _cashier_name(ctx):
    """Display name of the signed-in user, for the receipt's Served By line."""
    user = getattr(ctx, "user", None)
    if user is None:
        return ""
    name = " ".join(
        part for part in ((user.first_name or ""), (user.last_name or "")) if part
    ).strip()
    return name or (user.email or "")


@sales_bp.route("/<sale_id>/invoice", methods=["GET"])
@login_required
@require_module("sales")
@require_permission("sale.read")
def get_sale_invoice_route(sale_id):
    """Printable A4 tax invoice, for viewing or downloading.

    Distinct from ``/receipt``: that one is the 80mm thermal slip handed to the
    customer, this is the full A4 record kept on file.
    """
    from flask import send_file

    from ...exceptions import BusinessError
    from ...services import business_service
    from ...services.pdf_service import PDFGenerationError, get_pdf_service
    from ...services.sale_service import get_sale_record

    business_id = _business_id()
    sale = get_sale_record(business_id, parse_uuid(sale_id))
    config = business_service.get_config(business_id)

    try:
        pdf_bytes = get_pdf_service(business_id).generate_sale_invoice_pdf(
            sale,
            g.ctx.business,
            served_by=_cashier_name(g.ctx),
            settings=config,
        )
    except PDFGenerationError as exc:
        raise BusinessError(f"Invoice generation failed: {exc}")

    return send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=False,
        download_name=f"invoice-{sale.invoice_number}.pdf",
    )


@sales_bp.route("/<sale_id>/payments", methods=["POST"])
@login_required
@require_module("sales")
@require_permission("payment.write")
@validate_body(PaymentSchema)
def add_sale_payment_route(sale_id):
    sale = add_sale_payment_service(
        _business_id(), parse_uuid(sale_id), request.validated_body, g.ctx.user_id
    )
    return created({"sale": sale})


@sales_bp.route("/<sale_id>", methods=["DELETE"])
@login_required
@require_module("sales")
@require_permission("sale.write")
def delete_sale_route(sale_id):
    delete_sale_service(_business_id(), parse_uuid(sale_id), g.ctx.user_id)
    return no_content()
