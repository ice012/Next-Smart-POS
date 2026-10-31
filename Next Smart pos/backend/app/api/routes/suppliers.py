from flask import Blueprint, g, request

from ...schemas import SupplierSchema, SupplierUpdateSchema, SupplierSearchSchema
from ...services.supplier_service import (
    list_suppliers as list_suppliers_service,
    get_supplier as get_supplier_service,
    create_supplier as create_supplier_service,
    update_supplier as update_supplier_service,
    delete_supplier as delete_supplier_service,
    search_suppliers as search_suppliers_service,
)
from ...utils.security import parse_uuid
from ...validation import validate_body, validate_query
from ..permissions import gate_module, login_required, require_permission, require_permission_or_privileged
from ..responses import no_content, ok, created

suppliers_bp = Blueprint("suppliers", __name__)
suppliers_bp.before_request(gate_module("suppliers"))


def _business_id():
    return g.ctx.business_id


@suppliers_bp.route("", methods=["GET"])
@login_required
@require_permission("supplier.read")
@validate_query(SupplierSearchSchema)
def list_suppliers():
    pagination = request.validated_query
    items, total, pages = list_suppliers_service(_business_id(), pagination, is_active=pagination.is_active)
    return ok(
        {"suppliers": items},
        meta={"page": pagination.page, "per_page": pagination.per_page, "total": total, "pages": pages},
    )


@suppliers_bp.route("/search", methods=["GET"])
@login_required
@require_permission("supplier.read")
def search_suppliers():
    q = request.args.get("q", "")
    limit = int(request.args.get("limit", "20") or "20")
    items = search_suppliers_service(_business_id(), q, limit)
    return ok({"suppliers": items})


@suppliers_bp.route("", methods=["POST"])
@login_required
@require_permission("supplier.write")
@validate_body(SupplierSchema)
def create_supplier():
    supplier = create_supplier_service(_business_id(), request.validated_body, g.ctx.user_id)
    return created({"supplier": supplier})


@suppliers_bp.route("/<supplier_id>", methods=["GET"])
@login_required
@require_permission("supplier.read")
def get_supplier(supplier_id):
    supplier = get_supplier_service(_business_id(), parse_uuid(supplier_id))
    return ok({"supplier": supplier})


@suppliers_bp.route("/<supplier_id>", methods=["PATCH"])
@login_required
@require_permission("supplier.write")
@validate_body(SupplierUpdateSchema)
def update_supplier(supplier_id):
    supplier = update_supplier_service(_business_id(), parse_uuid(supplier_id), request.validated_body, g.ctx.user_id)
    return ok({"supplier": supplier})


@suppliers_bp.route("/<supplier_id>", methods=["DELETE"])
@login_required
@require_permission_or_privileged("supplier.delete")
def delete_supplier(supplier_id):
    delete_supplier_service(_business_id(), parse_uuid(supplier_id))
    return no_content()
