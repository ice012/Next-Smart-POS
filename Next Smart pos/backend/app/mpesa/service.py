"""M-Pesa service: Safaricom Daraja STK push, callback handling and config.

The sale is deliberately *not* written when the push is sent. This service
prices the cart, stores the draft on an :class:`~app.mpesa.models.MpesaStkRequest`,
and only replays that draft into a real sale once Safaricom's callback reports
success. A customer who declines, or who ignores the prompt, therefore leaves no
sale, no stock movement and no revenue behind.

The only outbound HTTP in the codebase lives here. It uses ``urllib`` from the
standard library rather than adding a dependency, and every call is wrapped so
a Daraja outage surfaces as a normal API error instead of a 500.
"""
import base64
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid as uuidlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from flask import current_app
from sqlalchemy import select

from ..exceptions import BusinessError, NotFoundError, ValidationError
from ..extensions import db
from ..schemas import PaymentInputSchema, SaleCreateSchema
from ..utils.db import commit
from ..utils.security import to_uuid
from .models import MpesaStkRequest

logger = logging.getLogger("nsp")

# Daraja rejects anything longer, so the values are truncated rather than
# letting the whole push fail on a long business name.
ACCOUNT_REFERENCE_MAX = 13
TRANSACTION_DESC_MAX = 13

# Daraja keys STK tokens off these; anything else fails authentication.
REQUIRED_CONFIG_FIELDS = ("consumer_key", "consumer_secret", "shortcode", "passkey")

DARAJA_ENVIRONMENTS = {
    "live": "https://api.safaricom.co.ke",
    "sandbox": "https://sandbox.safaricom.co.ke",
}

# Access tokens are cached per business so a busy till does not request a fresh
# one on every single sale. Values are (token, expiry_monotonic).
_token_cache = {}


# --------------------------------------------------------------------------
# Phone numbers
# --------------------------------------------------------------------------
def normalize_msisdn(phone):
    """Turn a locally formatted Kenyan number into the 2547XXXXXXXX form.

    Accepts ``0712345678``, ``+254 712 345 678``, ``254712345678`` and
    ``712345678``. Anything else raises rather than guessing, because a
    malformed MSISDN would be rejected by Safaricom after the sale is already
    sitting in the pending state.
    """
    if phone is None:
        raise ValidationError("M-Pesa phone number is required")
    raw = re.sub(r"[\s()\-.]", "", str(phone)).strip()
    if not raw:
        raise ValidationError("M-Pesa phone number is required")
    if raw.startswith("+"):
        raw = raw[1:]
    if not raw.isdigit():
        raise ValidationError("M-Pesa phone number must contain digits only")

    default_cc = str(current_app.config.get("MPESA_DEFAULT_COUNTRY_CODE") or "254")
    if raw.startswith("0"):
        raw = default_cc + raw[1:]
    elif raw.startswith(default_cc):
        pass
    elif len(raw) == 9:
        # A bare subscriber number with no trunk code.
        raw = default_cc + raw

    if len(raw) < 9 or len(raw) > 15:
        raise ValidationError(
            f"{phone} does not look like a valid M-Pesa phone number"
        )
    return raw


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
def get_mpesa_config(business_id):
    """Read the business M-Pesa settings, defaulting to disabled."""
    from ..services.business_service import get_config

    config = get_config(business_id)
    stored = (config.extra_settings or {}).get("mpesa") or {}
    return {
        "enabled": bool(stored.get("enabled", False)),
        "environment": stored.get("environment") or "live",
        "consumer_key": stored.get("consumer_key") or "",
        # Never leaves the server. redact_mpesa_config() strips it on the way out.
        "consumer_secret": stored.get("consumer_secret") or "",
        "shortcode": stored.get("shortcode") or "",
        "passkey": stored.get("passkey") or "",
        "callback_url": stored.get("callback_url") or "",
        "account_reference": stored.get("account_reference") or "",
        "transaction_description": stored.get("transaction_description") or "",
    }


def redact_mpesa_config(mpesa):
    """Strip the consumer secret so it is never serialised to a client."""
    safe = dict(mpesa)
    safe["consumer_secret"] = ""
    safe["has_consumer_secret"] = bool(mpesa.get("consumer_secret"))
    return safe


def save_mpesa_config(business_id, data):
    """Merge M-Pesa settings into extra_settings without losing other keys."""
    from ..services.business_service import get_config

    config = get_config(business_id)
    extra = dict(config.extra_settings or {})
    current = dict(extra.get("mpesa") or {})

    for field in (
        "enabled", "environment", "consumer_key", "shortcode",
        "passkey", "callback_url", "account_reference",
        "transaction_description",
    ):
        value = getattr(data, field, None)
        if value is not None:
            current[field] = value

    # An empty secret on PATCH means "leave the stored one alone", because the
    # client never receives it back and so always submits a blank field.
    secret = getattr(data, "consumer_secret", None)
    if secret:
        current["consumer_secret"] = secret

    extra["mpesa"] = current
    config.extra_settings = extra
    commit()
    return get_mpesa_config(business_id)


def missing_requirements(mpesa):
    """List what still has to be filled in before a push can be sent."""
    if not mpesa.get("enabled"):
        return ["enable M-Pesa in Settings"]
    missing = [f.replace("_", " ") for f in REQUIRED_CONFIG_FIELDS if not mpesa.get(f)]
    if not mpesa.get("callback_url"):
        missing.append("callback url")
    return missing


def require_mpesa_ready(business_id):
    """Return the live config, or explain precisely what is missing."""
    mpesa = get_mpesa_config(business_id)
    if mpesa["environment"] not in DARAJA_ENVIRONMENTS:
        raise BusinessError(f"Unknown M-Pesa environment: {mpesa['environment']}")
    missing = missing_requirements(mpesa)
    if missing:
        raise BusinessError(
            "M-Pesa is not ready: " + ", ".join(missing) + ". Finish setting it up in Settings."
        )
    return mpesa


# --------------------------------------------------------------------------
# Daraja HTTP
# --------------------------------------------------------------------------
def _post_json(url, payload, headers=None, timeout=None, basic_auth=None, form=False):
    """POST to Daraja and return the decoded JSON body.

    Daraja reports problems (wrong passkey, unknown shortcode, declined
    callback URL) as a JSON error body on a 4xx, so the response body is read
    and surfaced rather than collapsed into a bare HTTP error.
    """
    headers = dict(headers or {})
    body = payload
    if form:
        body = urllib.parse.urlencode(payload).encode()
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    else:
        body = json.dumps(payload).encode()
        headers.setdefault("Content-Type", "application/json")
    if basic_auth:
        headers["Authorization"] = "Basic " + base64.b64encode(
            basic_auth.encode()
        ).decode()

    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    timeout = timeout or current_app.config.get("MPESA_HTTP_TIMEOUT", 30)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode() or "{}"
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode()[:400]
        except Exception:  # noqa: BLE001 - the body is best-effort context only
            pass
        logger.warning("M-Pesa request to %s failed: HTTP %s %s", url, exc.code, detail)
        raise BusinessError(
            f"Safaricom rejected the request (HTTP {exc.code}). {detail}".strip()
        ) from exc
    except urllib.error.URLError as exc:
        logger.warning("M-Pesa request to %s failed: %s", url, exc)
        raise BusinessError(
            "Could not reach Safaricom. Check the network connection and try again."
        ) from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BusinessError("Safaricom returned an unreadable response") from exc


def _base_url(environment):
    return DARAJA_ENVIRONMENTS[environment]


def _access_token(mpesa, business_id):
    """Fetch (and cache) a Daraja OAuth token for this business."""
    key = (str(business_id), mpesa["consumer_key"])
    cached = _token_cache.get(key)
    now = time.monotonic()
    if cached and cached[1] > now:
        return cached[0]

    margin = current_app.config.get("MPESA_TOKEN_EXPIRY_MARGIN", 60)
    data = _post_json(
        f"{_base_url(mpesa['environment'])}/oauth/v1/generate"
        "?grant_type=client_credentials",
        {},
        basic_auth=f"{mpesa['consumer_key']}:{mpesa['consumer_secret']}",
        form=True,
    )
    token = data.get("access_token")
    if not token:
        raise BusinessError(
            "Safaricom did not return an access token. Check the consumer key "
            "and consumer secret."
        )
    _token_cache[key] = (token, now + max(3600 - margin, 60))
    return token


def clear_token_cache(business_id=None):
    """Forget cached tokens, e.g. after credentials change."""
    if business_id is None:
        _token_cache.clear()
        return
    for key in [k for k in _token_cache if str(k[0]) == str(business_id)]:
        _token_cache.pop(key, None)


# --------------------------------------------------------------------------
# STK push
# --------------------------------------------------------------------------
def _daraja_timestamp():
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def _sale_total(business_id, items_data):
    """Price a cart exactly the way create_sale() will, without writing it."""
    from ..services.sale_service import line_totals, price_lines

    lines = price_lines(business_id, items_data)
    subtotal, discount_total, tax_total = line_totals(lines)
    return (subtotal - discount_total + tax_total).quantize(Decimal("0.01"))


def _whole_shillings(value):
    """Daraja only accepts whole shillings, so round to the nearest one.

    Raises when the rounded figure would be zero, because a push for less than
    a shilling is rejected by Safaricom after the sale is already pending.
    """
    amount = int(Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if amount < 1:
        raise ValidationError("M-Pesa payments must be at least 1")
    return amount


def initiate_stk_push(business_id, data, actor_id):
    """Price the cart, send the STK push and park the sale as pending.

    Returns the stored request. No sale exists yet: ``create_sale`` only runs
    from the callback, once the money has been confirmed.
    """
    mpesa = require_mpesa_ready(business_id)
    phone = normalize_msisdn(data.phone)

    if data.sale.customer_id:
        from ..models import Customer

        customer = db.session.get(Customer, to_uuid(data.sale.customer_id))
        if customer is None or customer.business_id != business_id:
            raise NotFoundError("Customer not found")

    # The till may push a figure other than the exact total, so the requested
    # amount wins when given. Either way it is rounded to whole shillings
    # before the push, which is what the sale is then recorded at.
    if data.amount is not None:
        amount = _whole_shillings(data.amount)
    else:
        amount = _whole_shillings(_sale_total(business_id, data.sale.items))

    account_reference = (
        data.account_reference or mpesa["account_reference"] or "SmartBiz POS"
    )[:ACCOUNT_REFERENCE_MAX]
    description = (
        data.transaction_description
        or mpesa["transaction_description"]
        or "Payment"
    )[:TRANSACTION_DESC_MAX]

    timestamp = _daraja_timestamp()
    request = MpesaStkRequest(
        business_id=business_id,
        customer_id=(
            to_uuid(data.sale.customer_id) if data.sale.customer_id else None
        ),
        requested_by=to_uuid(actor_id) if actor_id else None,
        merchant_request_id=uuidlib.uuid4().hex[:30],
        phone=phone,
        amount=Decimal(amount),
        account_reference=account_reference,
        transaction_description=description,
        status="pending",
        # Store the draft as plain JSON so it can be replayed by the callback
        # handler, which runs outside any request that built this object.
        sale_payload=data.sale.model_dump(mode="json"),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=90),
    )
    request.created_by = to_uuid(actor_id) if actor_id else None
    request.updated_by = to_uuid(actor_id) if actor_id else None
    db.session.add(request)

    payload = {
        "BusinessShortCode": mpesa["shortcode"],
        "Password": base64.b64encode(
            f"{mpesa['shortcode']}{mpesa['passkey']}{timestamp}".encode()
        ).decode(),
        "Timestamp": timestamp,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": amount,
        "PartyA": phone,
        "PartyB": mpesa["shortcode"],
        "PhoneNumber": phone,
        "CallBackURL": mpesa["callback_url"],
        "AccountReference": account_reference,
        "TransactionDesc": description,
        "ReferenceCode": request.merchant_request_id,
    }
    result = _post_json(
        f"{_base_url(mpesa['environment'])}/mpesa/stkpush/v1/processrequest",
        payload,
        headers={"Authorization": f"Bearer {_access_token(mpesa, business_id)}"},
    )

    # A non-zero ResponseCode here is Safaricom saying "I refused the push",
    # e.g. a bad passkey or an unapproved callback URL. Nothing was sent to
    # the customer, so the request must not linger as pending.
    response_code = str(result.get("ResponseCode", ""))
    if response_code != "0":
        request.status = "failed"
        request.result_code = response_code or None
        request.result_desc = str(result.get("ResponseDescription") or "STK push rejected")[:255]
        request.checkout_request_id = str(result.get("RequestID") or "")[:64] or None
        db.session.flush()
        commit()
        raise BusinessError(request.result_desc)

    request.checkout_request_id = str(result.get("RequestID") or "")[:64] or None
    commit()
    return request


# --------------------------------------------------------------------------
# Callback
# --------------------------------------------------------------------------
def _callback_metadata(stk):
    items = ((stk.get("CallbackMetadata") or {}).get("Item")) or []
    return {
        str(i.get("Name")): i.get("Value")
        for i in items
        if isinstance(i, dict)
    }


def _find_request(stk):
    checkout_id = stk.get("CheckoutRequestID")
    merchant_id = stk.get("MerchantRequestID")
    if checkout_id:
        found = db.session.scalar(
            select(MpesaStkRequest).where(
                MpesaStkRequest.checkout_request_id == str(checkout_id)
            )
        )
        if found:
            return found
    if merchant_id:
        return db.session.scalar(
            select(MpesaStkRequest).where(
                MpesaStkRequest.merchant_request_id == str(merchant_id)
            )
        )
    return None


def handle_callback(payload):
    """Apply a Daraja callback: post the sale on success, otherwise release it."""
    body = payload.get("Body") if isinstance(payload, dict) else None
    stk = body.get("stkCallback") if isinstance(body, dict) else None
    if not isinstance(stk, dict) or not stk:
        # Safaricom retries anything it does not get a 200 for, so an
        # unrecognisable body is logged and answered rather than retried.
        logger.warning("M-Pesa callback with no stkCallback body: %s", payload)
        return None

    request = _find_request(stk)
    if request is None:
        # Safaricom retries anything that is not answered, and stale pushes
        # from a previous database will always land here. Ignore quietly.
        logger.warning(
            "M-Pesa callback for an unknown request: %s", stk.get("CheckoutRequestID")
        )
        return None

    if request.is_terminal:
        logger.info(
            "Ignoring repeat M-Pesa callback for %s (already %s)",
            request.merchant_request_id, request.status,
        )
        return request

    result_code = str(stk.get("ResultCode", ""))
    metadata = _callback_metadata(stk)
    receipt_no = metadata.get("MpesaReceiptNumber")
    request.callback_payload = stk
    request.result_code = result_code
    request.result_desc = str(stk.get("ResultDesc") or "")[:255]
    request.mpesa_receipt = str(receipt_no)[:60] if receipt_no else None

    if result_code != "0":
        request.status = "failed"
        commit()
        logger.info(
            "M-Pesa push %s declined: %s (%s)",
            request.merchant_request_id, result_code, request.result_desc,
        )
        return request

    # Safaricom reports the amount it actually charged. A mismatch means the
    # push and the parked sale have diverged, so the sale is not written.
    charged = metadata.get("Amount")
    if charged not in (None, ""):
        try:
            charged_amount = Decimal(str(charged))
        except Exception:  # noqa: BLE001 - treat an unparseable amount as a mismatch
            charged_amount = None
        if charged_amount is not None and charged_amount != Decimal(request.amount):
            request.status = "failed"
            request.result_desc = (
                f"Amount mismatch: charged {charged_amount}, expected {request.amount}"
            )[:255]
            commit()
            logger.error("M-Pesa amount mismatch on %s: %s",
                         request.merchant_request_id, request.result_desc)
            return request

    try:
        sale = _create_paid_sale(request)
    except Exception as exc:  # noqa: BLE001 - the money moved, so never swallow
        db.session.rollback()
        request.callback_payload = stk
        request.result_desc = (
            f"Payment received but the sale could not be created: {exc}"[:255]
        )
        request.status = "failed"
        commit()
        logger.exception(
            "M-Pesa payment received for %s but the sale failed to post",
            request.merchant_request_id,
        )
        return request

    request.sale_id = to_uuid(sale["id"])
    request.status = "completed"
    commit()
    logger.info(
        "M-Pesa push %s completed as sale %s", request.merchant_request_id, sale["id"]
    )
    return request


def _create_paid_sale(request):
    """Write the parked sale, appending the confirmed M-Pesa payment."""
    from ..services.sale_service import create_sale

    payload = dict(request.sale_payload or {})
    # An M-Pesa sale is settled at the till, so it is always a cash sale
    # regardless of what the customer would otherwise have been invoiced.
    payload["sale_type"] = "cash"
    payload["payments"] = [
        PaymentInputSchema(
            payment_method="mpesa",
            amount=float(request.amount),
            # The Daraja receipt number is what a customer quotes if they query
            # the payment, so it belongs on the payment record.
            reference=request.mpesa_receipt or request.merchant_request_id,
        ).model_dump(mode="json")
    ]
    sale_data = SaleCreateSchema.model_validate(payload)
    return create_sale(
        request.business_id, sale_data, request.requested_by or request.created_by
    )


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------
def get_request(business_id, request_id):
    request = db.session.get(MpesaStkRequest, request_id)
    if request is None or request.business_id != business_id:
        raise NotFoundError("M-Pesa request not found")
    _settle_expiry(request)
    return request


def _settle_expiry(request):
    """Fail a push that Safaricom never called back about."""
    if request.status != "pending" or request.expires_at is None:
        return request
    expires_at = request.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires_at:
        request.status = "timeout"
        request.result_desc = "The customer did not complete the payment in time"
        commit()
    return request


def list_requests(business_id, limit=25):
    stmt = (
        select(MpesaStkRequest)
        .where(MpesaStkRequest.business_id == business_id)
        .order_by(MpesaStkRequest.created_at.desc())
        .limit(limit)
    )
    return [r.to_dict() for r in db.session.scalars(stmt)]


def cancel_request(business_id, request_id):
    """Abandon a pending push so the till can be reused immediately."""
    request = get_request(business_id, request_id)
    if request.status == "pending":
        request.status = "cancelled"
        request.result_desc = "Cancelled at the till"
        commit()
    return request
