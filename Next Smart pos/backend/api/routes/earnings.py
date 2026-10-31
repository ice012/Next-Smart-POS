"""Staff earnings and advance requests.

Deliberately not module-gated, like the IAM blueprint. Earnings is a
compensation surface rather than a product module, and adding a plan module for
it would mean every plan's module list and the superadmin module editor had to
change together. Access is controlled by the `earnings.*` permissions instead.
"""

from flask import Blueprint, g, request

from ...schemas import (
    AdvanceDecisionSchema,
    AdvanceRequestSchema,
    AdvanceRequestSearchSchema,
    PaginationSchema,
)
from ...services.earnings_service import (
    advance_request_dto,
    cancel_advance_request,
    decide_advance_request,
    list_advance_requests,
    my_advance_requests,
    my_earnings,
    request_advance,
    staff_overview,
)
from ...validation import validate_body, validate_query
from ...utils.security import parse_uuid
from ..permissions import login_required, require_permission
from ..responses import created, ok

earnings_bp = Blueprint("earnings", __name__)


def _business_id():
    return g.ctx.business_id


@earnings_bp.route("/me", methods=["GET"])
@login_required
@require_permission("earnings.read")
def my_earnings_route():
    return ok({"earnings": my_earnings(_business_id(), g.ctx.user_id)})


@earnings_bp.route("/staff", methods=["GET"])
@login_required
@require_permission("earnings.advance_approve")
def staff_overview_route():
    """Every staff member's sales, earnings and advances in one call.

    What the staff list shows per person. Gated on the same permission as the
    advance queue: it is a compensation view of other people, which is exactly
    what `earnings.read` (own figures only) does not permit.
    """
    return ok(staff_overview(_business_id()))


@earnings_bp.route("/me/advances", methods=["GET"])
@login_required
@require_permission("earnings.read")
@validate_query(PaginationSchema)
def my_advance_requests_route():
    pagination = request.validated_query
    items, total, pages = my_advance_requests(
        _business_id(), g.ctx.user_id, pagination
    )
    return ok(
        {"advances": [a.to_dict() for a in items]},
        meta={
            "page": pagination.page,
            "per_page": pagination.per_page,
            "total": total,
            "pages": pages,
        },
    )


@earnings_bp.route("/me/advances", methods=["POST"])
@login_required
@require_permission("earnings.advance_request")
@validate_body(AdvanceRequestSchema)
def request_advance_route():
    data = request.validated_body
    request_row = request_advance(
        _business_id(), g.ctx.user_id, data.amount, data.reason
    )
    return created({"advance": request_row.to_dict()})


@earnings_bp.route("/me/advances/<advance_id>/cancel", methods=["POST"])
@login_required
@require_permission("earnings.advance_request")
def cancel_advance_route(advance_id):
    request_row = cancel_advance_request(
        _business_id(), g.ctx.user_id, parse_uuid(advance_id)
    )
    return ok({"advance": request_row.to_dict()})


@earnings_bp.route("/advance-requests", methods=["GET"])
@login_required
@require_permission("earnings.advance_approve")
@validate_query(AdvanceRequestSearchSchema)
def list_advance_requests_route():
    pagination = request.validated_query
    items, total, pages = list_advance_requests(
        _business_id(), pagination, status=pagination.status
    )
    return ok(
        {"advances": [advance_request_dto(a) for a in items]},
        meta={
            "page": pagination.page,
            "per_page": pagination.per_page,
            "total": total,
            "pages": pages,
        },
    )


@earnings_bp.route("/advance-requests/<advance_id>/decide", methods=["POST"])
@login_required
@require_permission("earnings.advance_approve")
@validate_body(AdvanceDecisionSchema)
def decide_advance_route(advance_id):
    data = request.validated_body
    request_row = decide_advance_request(
        _business_id(),
        parse_uuid(advance_id),
        data.decision,
        g.ctx.user_id,
        note=data.note,
    )
    return ok({"advance": advance_request_dto(request_row)})


@earnings_bp.route("/staff/<user_id>", methods=["GET"])
@login_required
@require_permission("earnings.advance_approve")
def staff_earnings_route(user_id):
    """Earnings for any member, so an approver can check affordability.

    Deliberately the same payload as `/me`: `my_earnings` is already scoped by
    an explicit user id, and reusing it keeps the approver's view and the
    member's view from drifting apart.
    """
    return ok({"earnings": my_earnings(_business_id(), parse_uuid(user_id))})
