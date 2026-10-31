"""M-Pesa API: STK push, Safaricom callback and push status.

The callback route is the only unauthenticated endpoint in the application. It
creates sales, so it is guarded by an optional shared secret
(``MPESA_CALLBACK_TOKEN``) and only ever acts on a CheckoutRequestID the server
itself issued.
"""
import hmac
import logging

from flask import Blueprint, current_app, g, request

from ..utils.security import parse_uuid
from ..validation import validate_body
from ..api.permissions import login_required, require_module, require_permission
from ..api.responses import created, ok
from . import service as mpesa_service
from .schemas import MpesaStkPushSchema

logger = logging.getLogger("nsp")

mpesa_bp = Blueprint("mpesa", __name__)


def _business_id():
    return g.ctx.business_id


@mpesa_bp.route("/config", methods=["GET"])
@login_required
@require_module("sales")
@require_permission("business.read")
def get_mpesa_config_route():
    """Whether the till can send a push, without exposing the secret.

    The POS calls this to decide up front whether to offer M-Pesa, rather than
    letting the cashier fill in a phone number and only then fail.
    """
    mpesa = mpesa_service.get_mpesa_config(_business_id())
    missing = mpesa_service.missing_requirements(mpesa)
    return ok({
        "mpesa": mpesa_service.redact_mpesa_config(mpesa),
        "is_ready": not missing,
        "missing": missing,
    })


@mpesa_bp.route("/stkpush", methods=["POST"])
@login_required
@require_module("sales")
@require_permission("payment.write")
@validate_body(MpesaStkPushSchema)
def initiate_stk_push_route():
    stk_request = mpesa_service.initiate_stk_push(
        _business_id(), request.validated_body, g.ctx.user_id
    )
    return created({"mpesa_request": stk_request.to_dict()})


@mpesa_bp.route("/requests/<request_id>", methods=["GET"])
@login_required
@require_module("sales")
@require_permission("sale.read")
def get_stk_request_route(request_id):
    stk_request = mpesa_service.get_request(
        _business_id(), parse_uuid(request_id)
    )
    return ok({"mpesa_request": stk_request.to_dict()})


@mpesa_bp.route("/requests", methods=["GET"])
@login_required
@require_module("sales")
@require_permission("sale.read")
def list_stk_requests_route():
    return ok({"mpesa_requests": mpesa_service.list_requests(_business_id())})


@mpesa_bp.route("/requests/<request_id>/cancel", methods=["POST"])
@login_required
@require_module("sales")
@require_permission("payment.write")
def cancel_stk_request_route(request_id):
    stk_request = mpesa_service.cancel_request(
        _business_id(), parse_uuid(request_id)
    )
    return ok({"mpesa_request": stk_request.to_dict()})


@mpesa_bp.route("/callback", methods=["POST"])
def mpesa_callback_route():
    expected = current_app.config.get("MPESA_CALLBACK_TOKEN")
    if expected:
        supplied = request.args.get("token", "")
        if not hmac.compare_digest(str(supplied), str(expected)):
            logger.warning("Rejected M-Pesa callback with a bad token")
            return ok({"ResultCode": "0", "ResultDesc": "Ignored"}), 401

    mpesa_service.handle_callback(request.get_json(silent=True))
    # Safaricom stops retrying as soon as it gets a 200, so the response is
    # always a success code even when the callback was for an unknown push.
    return ok({"ResultCode": "0", "ResultDesc": "Accepted"})
