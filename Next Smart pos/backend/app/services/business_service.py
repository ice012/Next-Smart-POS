import uuid as uuidlib

from sqlalchemy import select

from ..exceptions import ForbiddenError, NotFoundError, ConflictError
from ..extensions import db
from ..models import Business, BusinessConfig, Branch
from ..utils.db import commit
from ..utils.pagination import paginate


def get_config(business_id):
    config = db.session.scalar(
        select(BusinessConfig).where(BusinessConfig.business_id == business_id)
    )
    if config is None:
        raise NotFoundError("Business configuration not found")
    return config


def update_config(business_id, data, actor_id):
    config = get_config(business_id)
    for field in (
        "country", "currency", "timezone",
        "financial_year_start_month", "invoice_prefix", "invoice_next_number",
        "invoice_terms", "invoice_due_days", "tax_rules",
    ):
        value = getattr(data, field, None)
        if value is not None:
            setattr(config, field, value)
    config.updated_by = actor_id
    commit()
    # M-Pesa credentials are nested under extra_settings, which has no column
    # of its own to assign, so the mpesa package merges them itself. The
    # business schema only carries an opaque dict, so validation happens here.
    mpesa = getattr(data, "mpesa", None)
    if mpesa is not None:
        from pydantic import ValidationError as PydanticValidationError

        from ..exceptions import ValidationError
        from ..mpesa.schemas import MpesaConfigSchema
        from ..mpesa.service import save_mpesa_config
        from ..validation import format_validation_errors

        try:
            settings = MpesaConfigSchema.model_validate(mpesa)
        except PydanticValidationError as exc:
            raise ValidationError(
                "Validation failed", payload={"errors": format_validation_errors(exc)}
            ) from exc
        save_mpesa_config(business_id, settings)
    return get_config(business_id)


BUSINESS_PATCH_FIELDS = (
    "name",
    "logo",
    "default_country",
    "default_currency",
    "default_timezone",
    "email",
    "phone",
    "website",
    "address_line1",
    "address_line2",
    "city",
    "state",
    "postal_code",
)


def update_business(business_id, data, actor_id):
    business = db.session.get(Business, business_id)
    if business is None:
        raise NotFoundError("Business not found")
    # Only fields the client actually sent are applied, so an explicit null
    # (for example clearing the logo) is honoured while omitted keys are left
    # untouched. Accepts either a validated schema or a plain dict.
    if isinstance(data, dict):
        sent = set(data)
        read = data.get
    else:
        sent = getattr(data, "model_fields_set", set())
        read = lambda field: getattr(data, field, None)  # noqa: E731
    for field in BUSINESS_PATCH_FIELDS:
        if field in sent:
            setattr(business, field, read(field))
    business.updated_by = actor_id
    commit()
    return business


def list_branches(business_id, pagination):
    query = (
        select(Branch)
        .where(Branch.business_id == business_id, Branch.is_active.is_(True))
        .order_by(Branch.is_head.desc(), Branch.name.asc())
    )
    items, total, pages = paginate(query, pagination.page, pagination.per_page)
    return items, total, pages


def get_branch(business_id, branch_id):
    branch = db.session.get(Branch, branch_id)
    if branch is None or branch.business_id != business_id:
        raise NotFoundError("Branch not found")
    return branch


def create_branch(business_id, data, actor_id):
    from .subscription_service import check_plan_limit

    check_plan_limit(business_id, "branches")

    branch = Branch(
        business_id=business_id,
        name=data.name,
        code=data.code,
        phone=data.phone,
        email=data.email,
        address=data.address,
        is_head=bool(data.is_head),
    )
    branch.created_by = actor_id
    branch.updated_by = actor_id
    if branch.is_head:
        db.session.execute(
            Branch.__table__.update()
            .where(Branch.business_id == business_id, Branch.is_head.is_(True))
            .values(is_head=False)
        )
    db.session.add(branch)
    commit()
    return branch


def update_branch(business_id, branch_id, data, actor_id):
    branch = get_branch(business_id, branch_id)
    if data.is_head is True:
        db.session.execute(
            Branch.__table__.update()
            .where(Branch.business_id == business_id, Branch.is_head.is_(True))
            .values(is_head=False)
        )
    for field in ("name", "code", "phone", "email", "address", "is_head"):
        value = getattr(data, field, None)
        if value is not None:
            setattr(branch, field, value)
    branch.updated_by = actor_id
    commit()
    return branch


def delete_branch(business_id, branch_id):
    branch = get_branch(business_id, branch_id)
    if branch.is_head:
        raise ForbiddenError("The head office cannot be deleted")
    db.session.delete(branch)
    commit()
