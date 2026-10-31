from flask import Blueprint, g, request

from ...schemas import (
    CustomerSchema, CustomerUpdateSchema, CustomerSearchSchema, CustomerResolveSchema,
)
from ...services.customer_service import (
    list_customers as list_customers_service,
    get_customer as get_customer_service,
    create_customer as create_customer_service,
    update_customer as update_customer_service,
    delete_customer as delete_customer_service,
    search_customers as search_customers_service,
    find_or_create_customer as find_or_create_customer_service,
)
from ...utils.security import parse_uuid
from ...validation import validate_body, validate_query
from ..permissions import gate_module, login_required, require_permission, require_permission_or_privileged
from ..responses import no_content, ok, created

customers_bp = Blueprint("customers", __name__)
customers_bp.before_request(gate_module("customers"))


def _business_id():
    return g.ctx.business_id


@customers_bp.route("", methods=["GET"])
@login_required
@require_permission("customer.read")
@validate_query(CustomerSearchSchema)
def list_customers():
    pagination = request.validated_query
    items, total, pages = list_customers_service(
        _business_id(), pagination,
        customer_type=pagination.customer_type,
        is_active=pagination.is_active,
    )
    return ok(
        {"customers": items},
        meta={"page": pagination.page, "per_page": pagination.per_page, "total": total, "pages": pages},
    )


@customers_bp.route("/search", methods=["GET"])
@login_required
@require_permission("customer.read")
def search_customers():
    q = request.args.get("q", "")
    limit = int(request.args.get("limit", "20") or "20")
    items = search_customers_service(_business_id(), q, limit)
    return ok({"customers": items})


@customers_bp.route("/resolve", methods=["POST"])
@login_required
@require_permission_or_privileged("customer.write")
@validate_body(CustomerResolveSchema)
def resolve_customer():
    """Return the customer with this exact name, creating it when absent.

    Supports screens that accept a free-text customer name, so the user is not
    forced to create the customer record first.
    """
    payload = request.validated_body
    existing = find_or_create_customer_service(
        _business_id(),
        payload.display_name,
        customer_type=payload.customer_type,
        actor_id=g.ctx.user_id,
    )
    return ok({"customer": existing})


@customers_bp.route("", methods=["POST"])
@login_required
@require_permission("customer.write")
@validate_body(CustomerSchema)
def create_customer():
    customer = create_customer_service(_business_id(), request.validated_body, g.ctx.user_id)
    return created({"customer": customer})


@customers_bp.route("/<customer_id>", methods=["GET"])
@login_required
@require_permission("customer.read")
def get_customer(customer_id):
    customer = get_customer_service(_business_id(), parse_uuid(customer_id))
    return ok({"customer": customer})


@customers_bp.route("/<customer_id>", methods=["PATCH"])
@login_required
@require_permission("customer.write")
@validate_body(CustomerUpdateSchema)
def update_customer(customer_id):
    customer = update_customer_service(_business_id(), parse_uuid(customer_id), request.validated_body, g.ctx.user_id)
    return ok({"customer": customer})


@customers_bp.route("/<customer_id>", methods=["DELETE"])
@login_required
@require_permission_or_privileged("customer.delete")
def delete_customer(customer_id):
    delete_customer_service(_business_id(), parse_uuid(customer_id))
    return no_content()
