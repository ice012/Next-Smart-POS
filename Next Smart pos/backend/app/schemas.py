import re
from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

from .utils.security import validate_password_strength

CCY_RE = re.compile(r"^[A-Z]{3}$")
COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
TZ_RE = re.compile(r"^[A-Za-z_/][A-Za-z0-9_/]+$")
PIN_RE = re.compile(r"^[0-9]{4,6}$")
# Lowercase letters, digits, dot, dash and underscore. Must start with a letter
# or digit so a username is always a valid URL/label segment.
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,29}$")


def _str(min_len=1, max_len=255, *, required=True):
    return Field(min_length=min_len, max_length=max_len) if required else Field(
        default=None, min_length=min_len, max_length=max_len
    )


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PaginationSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=25, ge=1, le=100)
    sort: str | None = None
    order: str = Field(default="asc", pattern="^(asc|desc)$")
    q: str | None = None


class RegisterSchema(_Base):
    email: EmailStr
    password: str
    first_name: str = _str(1, 100)
    last_name: str = _str(1, 100)
    phone: str | None = _str(1, 50, required=False)
    business_name: str = _str(1, 150)
    business_country: str = Field(pattern=COUNTRY_RE.pattern)
    business_currency: str = Field(pattern=CCY_RE.pattern)
    timezone: str = Field(pattern=TZ_RE.pattern)
    # Which plan the tenant signs up on. Optional so existing API clients keep
    # working; when omitted the account lands on the default plan. The signup
    # form makes the choice explicit.
    plan_id: str | None = None

    @field_validator("password")
    @classmethod
    def _check_password(cls, v):
        validate_password_strength(v)
        return v


class LoginSchema(_Base):
    # Sign-in identifier: either the account email or its username. `email` is
    # kept as an accepted alias so older clients keep working.
    identifier: str | None = None
    email: str | None = None
    password: str | None = None
    pin: str | None = None

    @model_validator(mode="after")
    def _require_a_credential(self):
        value = (self.identifier or self.email or "").strip()
        if not value:
            raise ValueError("Email or username is required")
        self.identifier = value
        if not self.password and not self.pin:
            raise ValueError("Either password or pin is required")
        if self.pin is not None and not PIN_RE.match(self.pin.strip()):
            raise ValueError("PIN must be 4 to 6 digits")
        return self


class SuperAdminLoginSchema(_Base):
    # Same shape as LoginSchema: a superadmin can sign in with the account
    # password or with the short-form PIN, resolved against email or username.
    identifier: str
    password: str | None = None
    pin: str | None = None

    @model_validator(mode="after")
    def _require_credentials(self):
        self.identifier = (self.identifier or "").strip()
        if not self.identifier:
            raise ValueError("Email or username is required")
        if not self.password and not self.pin:
            raise ValueError("Either password or pin is required")
        if self.pin is not None:
            self.pin = self.pin.strip()
            if not PIN_RE.match(self.pin):
                raise ValueError("PIN must be 4 to 6 digits")
        return self


class SetupPinSchema(_Base):
    pin: str
    # Re-authenticating before a PIN change keeps a stolen access token from
    # being enough to take over short-form sign-in.
    current_password: str

    @field_validator("pin")
    @classmethod
    def _check_pin(cls, v):
        v = v.strip()
        if not PIN_RE.match(v):
            raise ValueError("PIN must be 4 to 6 digits")
        return v

    @field_validator("current_password")
    @classmethod
    def _check_current_password(cls, v):
        if not v:
            raise ValueError("Current password is required")
        return v


class ForgotPasswordSchema(_Base):
    email: EmailStr


class ResetPasswordSchema(_Base):
    token: str
    password: str

    @field_validator("password")
    @classmethod
    def _check_password(cls, v):
        validate_password_strength(v)
        return v


class ChangePasswordSchema(_Base):
    old_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _check_password(cls, v):
        validate_password_strength(v)
        return v


class PrivilegedAuthorizationSchema(_Base):
    """Exchange an owner/admin password or PIN for a short-lived action token."""

    # The action is named by the client, but the server only issues a token for
    # actions it knows about; see `module_catalog.PRIVILEGED_ACTION_LABELS`.
    action: str
    password: str | None = None
    pin: str | None = None

    @model_validator(mode="after")
    def _require_a_credential(self):
        # A PIN is 4-6 digits and a password is longer, so the client can just
        # post whichever the approver typed and the server works out which.
        if not self.pin and not self.password:
            raise ValueError("Either password or pin is required")
        if self.pin is not None:
            self.pin = self.pin.strip()
            if not PIN_RE.match(self.pin):
                raise ValueError("PIN must be 4 to 6 digits")
        if self.password is not None:
            self.password = self.password.strip()
            if not self.password:
                self.password = None
            elif len(self.password) > 128:
                raise ValueError("Password is too long")
        return self

    @field_validator("action")
    @classmethod
    def _check_action(cls, v):
        v = v.strip()
        if not re.match(r"^[a-z_]+\.[a-z_]+$", v):
            raise ValueError("Action must look like 'resource.action'")
        return v


class SwitchBusinessSchema(_Base):
    business_id: str


class UpdateProfileSchema(_Base):
    username: str | None = None
    first_name: str | None = _str(1, 100, required=False)
    last_name: str | None = _str(1, 100, required=False)
    phone: str | None = _str(1, 50, required=False)

    @field_validator("phone", mode="before")
    @classmethod
    def _blank_phone_is_absent(cls, v):
        # Profile forms submit their text inputs whether or not the user filled
        # them, so an untouched phone arrives as "". Treat that as "leave the
        # stored value alone" instead of failing the 1-character minimum.
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("username")
    @classmethod
    def _check_username(cls, v):
        if v is None:
            return v
        v = v.strip().lower()
        if not v:
            raise ValueError("Username cannot be blank")
        if not USERNAME_RE.match(v):
            raise ValueError(
                "Username must be 3-30 characters using letters, numbers, dot, dash or underscore"
            )
        return v


class BusinessUpdateSchema(_Base):
    name: str | None = _str(1, 150, required=False)
    logo: str | None = _str(0, 2048, required=False)


class BusinessConfigSchema(_Base):
    country: str = Field(pattern=COUNTRY_RE.pattern)
    currency: str = Field(pattern=CCY_RE.pattern)
    timezone: str = Field(pattern=TZ_RE.pattern)
    financial_year_start_month: int = Field(default=1, ge=1, le=12)
    invoice_prefix: str = _str(1, 20)
    invoice_next_number: int = Field(default=1, ge=1)
    invoice_terms: str | None = _str(0, 1000, required=False)
    invoice_due_days: int = Field(default=30, ge=0)
    tax_rules: list = Field(default_factory=list)

    @field_validator("tax_rules")
    @classmethod
    def _validate_tax_rules(cls, v):
        if not isinstance(v, list):
            raise ValueError("tax_rules must be a list")
        for rule in v:
            if not isinstance(rule, dict):
                raise ValueError("each tax rule must be an object")
            name = rule.get("name")
            rate = rule.get("rate")
            if not isinstance(name, str) or len(name) < 1:
                raise ValueError("tax rule name required")
            if not isinstance(rate, (int, float)) or rate < 0 or rate > 100:
                raise ValueError("tax rate must be between 0 and 100")
        return v


class BranchSchema(_Base):
    name: str = _str(1, 150)
    code: str = _str(1, 50)
    phone: str | None = _str(1, 50, required=False)
    email: EmailStr | None = None
    address: dict | None = None
    is_head: bool | None = None


class BranchUpdateSchema(_Base):
    name: str | None = _str(1, 150, required=False)
    code: str | None = _str(1, 50, required=False)
    phone: str | None = _str(1, 50, required=False)
    email: EmailStr | None = None
    address: dict | None = None
    is_head: bool | None = None


class _RoleBase(_Base):
    name: str | None = _str(1, 100, required=False)
    description: str | None = _str(0, 500, required=False)
    permission_names: list[str] | None = None
    # Module codes whose write/delete actions need an owner or admin to unlock
    # them. Unknown codes are rejected rather than stored: a typo in a key that
    # silently guards nothing is worse than a failed save.
    requires_owner_approval: dict[str, bool] | None = None

    @field_validator("permission_names")
    @classmethod
    def _dedupe(cls, v):
        if v is None:
            return v
        return list(dict.fromkeys(v))

    @field_validator("requires_owner_approval")
    @classmethod
    def _check_modules(cls, v):
        if v is None:
            return v
        from .models.module_catalog import MODULES_BY_CODE

        unknown = [code for code in v if code not in MODULES_BY_CODE]
        if unknown:
            raise ValueError(f"Unknown modules: {', '.join(sorted(unknown))}")
        return {code: bool(flag) for code, flag in v.items() if flag}


class RoleSchema(_RoleBase):
    name: str = _str(1, 100)
    description: str | None = _str(0, 500, required=False)
    permission_names: list[str] = Field(default_factory=list)


class RoleUpdateSchema(_RoleBase):
    pass


class UserInviteSchema(_Base):
    email: EmailStr
    first_name: str = _str(1, 100)
    last_name: str = _str(1, 100)
    phone: str | None = _str(1, 50, required=False)
    role_id: str
    branch_id: str | None = None
    payment_mode: str | None = Field(default=None, pattern="^(salary|commission|salary_plus_commission)$")
    commission_rate: float | None = Field(default=None, ge=0)
    base_salary: float | None = Field(default=None, ge=0)
    # Optional sign-in handle and/or temporary password an owner can set when
    # inviting a staff member so they can sign in without the invite reset flow.
    username: str | None = None
    password: str | None = None

    @field_validator("phone", mode="before")
    @classmethod
    def _blank_phone_is_absent(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("username")
    @classmethod
    def _check_username(cls, v):
        if v is None:
            return v
        v = v.strip().lower()
        if not v:
            return None
        if not USERNAME_RE.match(v):
            raise ValueError(
                "Username must be 3-30 characters using letters, numbers, dot, dash or underscore"
            )
        return v

    @field_validator("password")
    @classmethod
    def _check_password(cls, v):
        if v is None:
            return v
        validate_password_strength(v)
        return v


class UserUpdateSchema(_Base):
    first_name: str | None = _str(1, 100, required=False)
    last_name: str | None = _str(1, 100, required=False)
    phone: str | None = _str(1, 50, required=False)
    role_id: str | None = None
    branch_id: str | None = None
    is_active: bool | None = None
    payment_mode: str | None = Field(default=None, pattern="^(salary|commission|salary_plus_commission)$")
    commission_rate: float | None = Field(default=None, ge=0)
    base_salary: float | None = Field(default=None, ge=0)
    username: str | None = None
    password: str | None = None

    @field_validator("phone", mode="before")
    @classmethod
    def _blank_phone_is_absent(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("username")
    @classmethod
    def _check_username(cls, v):
        if v is None:
            return v
        v = v.strip().lower()
        if not v:
            return None
        if not USERNAME_RE.match(v):
            raise ValueError(
                "Username must be 3-30 characters using letters, numbers, dot, dash or underscore"
            )
        return v

    @field_validator("password")
    @classmethod
    def _check_password(cls, v):
        if v is None:
            return v
        validate_password_strength(v)
        return v


class BusinessPatchSchema(_Base):
    name: str | None = _str(1, 150, required=False)
    logo: str | None = _str(0, 2048, required=False)
    default_country: str | None = Field(default=None, pattern=COUNTRY_RE.pattern)
    default_currency: str | None = Field(default=None, pattern=CCY_RE.pattern)
    default_timezone: str | None = Field(default=None, pattern=TZ_RE.pattern)
    email: EmailStr | None = None
    phone: str | None = _str(0, 50, required=False)
    website: str | None = _str(0, 255, required=False)
    address_line1: str | None = _str(0, 255, required=False)
    address_line2: str | None = _str(0, 255, required=False)
    city: str | None = _str(0, 100, required=False)
    state: str | None = _str(0, 100, required=False)
    postal_code: str | None = _str(0, 32, required=False)


class BusinessConfigPatchSchema(_Base):
    country: str | None = Field(default=None, pattern=COUNTRY_RE.pattern)
    currency: str | None = Field(default=None, pattern=CCY_RE.pattern)
    timezone: str | None = Field(default=None, pattern=TZ_RE.pattern)
    financial_year_start_month: int | None = Field(default=None, ge=1, le=12)
    invoice_prefix: str | None = _str(1, 20, required=False)
    invoice_next_number: int | None = Field(default=None, ge=1)
    invoice_terms: str | None = _str(0, 1000, required=False)
    invoice_due_days: int | None = Field(default=None, ge=0)
    tax_rules: list | None = None
    # Deliberately untyped: M-Pesa has its own schema in app.mpesa, and the
    # core should not have to know the shape of a payment integration's
    # settings. The mpesa package validates it when the object is saved.
    mpesa: dict | None = None

    @field_validator("tax_rules")
    @classmethod
    def _validate_tax_rules(cls, v):
        if v is None:
            return v
        if not isinstance(v, list):
            raise ValueError("tax_rules must be a list")
        for rule in v:
            if not isinstance(rule, dict):
                raise ValueError("each tax rule must be an object")
            name = rule.get("name")
            rate = rule.get("rate")
            if not isinstance(name, str) or len(name) < 1:
                raise ValueError("tax rule name required")
            if not isinstance(rate, (int, float)) or rate < 0 or rate > 100:
                raise ValueError("tax rate must be between 0 and 100")
        return v


class RolePatchSchema(_RoleBase):
    pass


class MembershipPatchSchema(_Base):
    role_id: str | None = None
    branch_id: str | None = None
    is_active: bool | None = None
    payment_mode: str | None = Field(default=None, pattern="^(salary|commission|salary_plus_commission)$")
    commission_rate: float | None = Field(default=None, ge=0)
    base_salary: float | None = Field(default=None, ge=0)


class CustomerSchema(_Base):
    customer_type: str = Field(default="individual", pattern="^(individual|corporate)$")
    display_name: str = _str(1, 200)
    first_name: str | None = _str(1, 100, required=False)
    last_name: str | None = _str(1, 100, required=False)
    company_name: str | None = _str(1, 200, required=False)
    contact_person: str | None = _str(1, 100, required=False)
    tax_id: str | None = _str(1, 100, required=False)
    email: EmailStr | None = None
    phone: str | None = _str(1, 50, required=False)
    address: dict | None = None
    notes: str | None = _str(0, 2000, required=False)


class CustomerUpdateSchema(_Base):
    customer_type: str | None = Field(default=None, pattern="^(individual|corporate)$")
    display_name: str | None = _str(1, 200, required=False)
    first_name: str | None = _str(1, 100, required=False)
    last_name: str | None = _str(1, 100, required=False)
    company_name: str | None = _str(1, 200, required=False)
    contact_person: str | None = _str(1, 100, required=False)
    tax_id: str | None = _str(1, 100, required=False)
    email: EmailStr | None = None
    phone: str | None = _str(1, 50, required=False)
    address: dict | None = None
    notes: str | None = _str(0, 2000, required=False)
    is_active: bool | None = None


class CustomerSearchSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=25, ge=1, le=100)
    q: str | None = None
    customer_type: str | None = Field(default=None, pattern="^(individual|corporate)$")
    is_active: bool | None = None


class CustomerResolveSchema(_Base):
    """Look up a customer by exact name, creating one if none matches.

    Lets flows such as quotations accept a free-text customer name without
    forcing the user to pre-create the customer first.
    """
    display_name: str = _str(1, 200)
    customer_type: str = Field(default="individual", pattern="^(individual|corporate)$")


class SupplierSchema(_Base):
    display_name: str = _str(1, 200)
    company_name: str | None = _str(1, 200, required=False)
    contact_person: str | None = _str(1, 100, required=False)
    tax_id: str | None = _str(1, 100, required=False)
    email: EmailStr | None = None
    phone: str | None = _str(1, 50, required=False)
    address: dict | None = None
    notes: str | None = _str(0, 2000, required=False)


class SupplierUpdateSchema(_Base):
    display_name: str | None = _str(1, 200, required=False)
    company_name: str | None = _str(1, 200, required=False)
    contact_person: str | None = _str(1, 100, required=False)
    tax_id: str | None = _str(1, 100, required=False)
    email: EmailStr | None = None
    phone: str | None = _str(1, 50, required=False)
    address: dict | None = None
    notes: str | None = _str(0, 2000, required=False)
    is_active: bool | None = None


class SupplierSearchSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=25, ge=1, le=100)
    q: str | None = None
    is_active: bool | None = None


class ItemSchema(_Base):
    type: str = Field(default="product", pattern="^(product|service)$")
    name: str = _str(1, 200)
    description: str | None = _str(0, 2000, required=False)
    sku: str | None = _str(1, 100, required=False)
    barcode: str | None = _str(1, 200, required=False)
    category_id: str | None = None
    cost_price: float = Field(default=0, ge=0)
    selling_price: float = Field(default=0, ge=0)
    minimum_stock: float = Field(default=0, ge=0)
    is_inventory_tracked: bool | None = None
    uom_id: str | None = None
    tax_config: dict | None = None


class ItemUpdateSchema(_Base):
    type: str | None = Field(default=None, pattern="^(product|service)$")
    name: str | None = _str(1, 200, required=False)
    description: str | None = _str(0, 2000, required=False)
    sku: str | None = _str(1, 100, required=False)
    barcode: str | None = _str(1, 200, required=False)
    category_id: str | None = None
    cost_price: float | None = Field(default=None, ge=0)
    selling_price: float | None = Field(default=None, ge=0)
    minimum_stock: float | None = Field(default=None, ge=0)
    is_inventory_tracked: bool | None = None
    uom_id: str | None = None
    tax_config: dict | None = None
    is_active: bool | None = None


class ItemSearchSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=50, ge=1, le=100)
    q: str | None = None
    type: str | None = Field(default=None, pattern="^(product|service)$")
    category_id: str | None = None
    is_active: bool | None = None


class CategorySchema(_Base):
    name: str = _str(1, 100)
    description: str | None = _str(0, 500, required=False)
    parent_id: str | None = None


class CategoryUpdateSchema(_Base):
    name: str | None = _str(1, 100, required=False)
    description: str | None = _str(0, 500, required=False)
    parent_id: str | None = None
    is_active: bool | None = None


class UOMSchema(_Base):
    name: str = _str(1, 50)
    code: str = _str(1, 20)


class UOMUpdateSchema(_Base):
    name: str | None = _str(1, 50, required=False)
    code: str | None = _str(1, 20, required=False)
    is_active: bool | None = None


class WarehouseSchema(_Base):
    branch_id: str | None = None
    code: str = _str(1, 50)
    name: str = _str(1, 150)
    address: dict | None = None
    is_default: bool | None = None


class WarehouseUpdateSchema(_Base):
    branch_id: str | None = None
    code: str | None = _str(1, 50, required=False)
    name: str | None = _str(1, 150, required=False)
    address: dict | None = None
    is_default: bool | None = None
    is_active: bool | None = None


class StockAdjustmentSchema(_Base):
    item_id: str | None = None
    warehouse_id: str
    quantity: float
    note: str | None = _str(0, 1000, required=False)
    movement_type: str = Field(default="adjustment", pattern="^(adjustment|stock_in|stock_out)$")


class SaleSearchSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=25, ge=1, le=100)
    q: str | None = None
    status: str | None = None
    sale_type: str | None = None
    customer_id: str | None = None
    branch_id: str | None = None
    from_date: str | None = None
    to_date: str | None = None


class SaleItemInputSchema(_Base):
    item_id: str
    quantity: float = Field(gt=0)
    discount_amount: float = Field(default=0, ge=0)


class PaymentInputSchema(_Base):
    payment_method: str = Field(pattern="^(cash|mpesa|bank|card|credit|other)$")
    amount: float = Field(gt=0)
    reference: str | None = _str(0, 100, required=False)
    date: str | None = _str(1, 100, required=False)


class SaleCreateSchema(_Base):
    customer_id: str | None = None
    sale_type: str = Field(default="cash", pattern="^(cash|credit)$")
    notes: str | None = _str(0, 2000, required=False)
    items: list[SaleItemInputSchema] = Field(min_length=1)
    payments: list[PaymentInputSchema] = Field(default_factory=list)


class QuotationSearchSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=25, ge=1, le=100)
    q: str | None = None
    status: str | None = None
    customer_id: str | None = None
    from_date: str | None = None
    to_date: str | None = None


class QuotationStatusSchema(_Base):
    status: str = Field(pattern="^(draft|sent|accepted|rejected|expired|converted)$")


class QuotationItemInputSchema(_Base):
    item_id: str
    quantity: float = Field(gt=0)
    discount_amount: float = Field(default=0, ge=0)


class QuotationCreateSchema(_Base):
    customer_id: str | None = None
    date: str | None = _str(1, 100, required=False)
    expiry_date: str | None = _str(1, 100, required=False)
    notes: str | None = _str(0, 2000, required=False)
    items: list[QuotationItemInputSchema] = Field(min_length=1)


class QuotationUpdateSchema(_Base):
    customer_id: str | None = None
    date: str | None = _str(1, 100, required=False)
    expiry_date: str | None = _str(1, 100, required=False)
    status: str | None = Field(default=None, pattern="^(draft|sent|accepted|rejected|expired|converted)$")
    notes: str | None = _str(0, 2000, required=False)
    items: list[QuotationItemInputSchema] | None = None


class PaymentSchema(_Base):
    customer_id: str | None = None
    sale_invoice_id: str | None = None
    payment_method: str = Field(pattern="^(cash|mpesa|bank|card|credit|other)$")
    amount: float = Field(gt=0)
    reference: str | None = _str(0, 100, required=False)
    notes: str | None = _str(0, 1000, required=False)
    date: str | None = None


class PaymentUpdateSchema(_Base):
    customer_id: str | None = None
    sale_invoice_id: str | None = None
    payment_method: str | None = Field(default=None, pattern="^(cash|mpesa|bank|card|credit|other)$")
    amount: float | None = Field(default=None, gt=0)
    reference: str | None = _str(0, 100, required=False)
    notes: str | None = _str(0, 1000, required=False)
    status: str | None = Field(default=None, pattern="^(pending|completed|failed|refunded)$")
    date: str | None = None


class PaymentSearchSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=25, ge=1, le=100)
    q: str | None = None
    customer_id: str | None = None
    sale_invoice_id: str | None = None
    payment_method: str | None = None
    status: str | None = None


class StockAdjustmentUpdateSchema(_Base):
    quantity: float | None = None
    note: str | None = _str(0, 1000, required=False)
    movement_type: str | None = Field(default=None, pattern="^(adjustment|stock_in|stock_out)$")


class StockMovementSearchSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=25, ge=1, le=100)
    item_id: str | None = None
    warehouse_id: str | None = None
    movement_type: str | None = None
    reference_type: str | None = None


class InventoryBalanceSearchSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=25, ge=1, le=100)
    q: str | None = None
    warehouse_id: str | None = None
    low_stock: bool | None = None


class SaleUpdateSchema(_Base):
    customer_id: str | None = None
    sale_type: str | None = Field(default=None, pattern="^(cash|credit)$")
    notes: str | None = _str(0, 2000, required=False)
    status: str | None = Field(default=None, pattern="^(draft|posted|cancelled)$")


class SaleCancelSchema(_Base):
    reason: str | None = _str(0, 500, required=False)


class QuotationConvertSchema(_Base):
    sale_type: str = Field(default="credit", pattern="^(cash|credit)$")
    notes: str | None = _str(0, 2000, required=False)


class CustomerStatementSchema(_Base):
    customer_id: str
    from_date: str | None = None
    to_date: str | None = None


class SupplierStatementSchema(_Base):
    supplier_id: str
    from_date: str | None = None
    to_date: str | None = None


class CustomerPaymentSchema(_Base):
    customer_id: str
    amount: float = Field(gt=0)
    payment_method: str = Field(pattern="^(cash|mpesa|bank|card|credit|other)$")
    reference: str | None = _str(0, 100, required=False)
    notes: str | None = _str(0, 1000, required=False)
    date: str | None = None


class SupplierPaymentSchema(_Base):
    supplier_id: str
    amount: float = Field(gt=0)
    payment_method: str = Field(pattern="^(cash|mpesa|bank|card|credit|other)$")
    reference: str | None = _str(0, 100, required=False)
    notes: str | None = _str(0, 1000, required=False)
    date: str | None = None


class PurchaseOrderSearchSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=25, ge=1, le=100)
    q: str | None = None
    status: str | None = None
    supplier_id: str | None = None
    from_date: str | None = None
    to_date: str | None = None


class PurchaseOrderStatusSchema(_Base):
    status: str = Field(pattern="^(draft|ordered|received|cancelled)$")


class PurchaseOrderCancelSchema(_Base):
    reason: str | None = _str(0, 500, required=False)


class PurchaseOrderSchema(_Base):
    supplier_id: str
    warehouse_id: str | None = None
    expected_date: str | None = None
    notes: str | None = _str(0, 2000, required=False)
    items: list[QuotationItemInputSchema] = Field(min_length=1)


class PurchaseOrderUpdateSchema(_Base):
    warehouse_id: str | None = None
    expected_date: str | None = None
    notes: str | None = _str(0, 2000, required=False)
    status: str | None = Field(default=None, pattern="^(draft|ordered|received|cancelled)$")
    items: list[QuotationItemInputSchema] | None = None


class PurchaseReceiveSchema(_Base):
    warehouse_id: str | None = None
    items: list[dict] = Field(min_length=1)
    note: str | None = _str(0, 1000, required=False)


class TodoSearchSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=25, ge=1, le=100)
    q: str | None = None
    completed: bool | None = None
    overdue: bool | None = None


class TodoCreateSchema(_Base):
    title: str = _str(1, 200)
    description: str | None = _str(0, 2000, required=False)
    due_at: datetime | None = None
    position: int | None = None


class TodoUpdateSchema(_Base):
    title: str | None = _str(1, 200, required=False)
    description: str | None = _str(0, 2000, required=False)
    due_at: datetime | None = None
    completed: bool | None = None
    position: int | None = None


INVOICE_STATUSES = {"draft", "sent", "partially_paid", "paid", "overdue", "cancelled"}
EXPENSE_STATUSES = {"draft", "approved", "paid", "cancelled"}
PAYMENT_METHODS = {"cash", "mpesa", "bank", "card", "cheque", "other"}
PAYMENT_TYPES = {"customer_receipt", "supplier_payment", "expense_payment", "refund", "other"}
ACCOUNT_TYPES = {"asset", "liability", "equity", "revenue", "cogs", "expense", "ar", "ap", "cash", "bank"}
JOURNAL_STATUSES = {"draft", "posted", "reversed", "cancelled"}
JOURNAL_LINE_TYPES = {"debit", "credit"}


class InvoiceItemInputSchema(_Base):
    item_id: str | None = None
    description: str = _str(1, 500)
    quantity: float = Field(gt=0)
    unit_price: float = Field(ge=0)
    cost_price: float = Field(default=0, ge=0)
    discount_amount: float = Field(default=0, ge=0)
    tax_rate: float = Field(default=0, ge=0, le=100)
    is_service: bool | None = None


class InvoiceCreateSchema(_Base):
    customer_id: str | None = None
    branch_id: str | None = None
    date: str | None = None
    due_date: str | None = None
    notes: str | None = _str(0, 2000, required=False)
    terms: str | None = _str(0, 2000, required=False)
    items: list[InvoiceItemInputSchema] = Field(min_length=1)


class InvoiceUpdateSchema(_Base):
    customer_id: str | None = None
    branch_id: str | None = None
    date: str | None = None
    due_date: str | None = None
    notes: str | None = _str(0, 2000, required=False)
    terms: str | None = _str(0, 2000, required=False)
    status: str | None = Field(default=None, pattern="^(draft|sent|partially_paid|paid|overdue|cancelled)$")
    items: list[InvoiceItemInputSchema] | None = None


class InvoiceSearchSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=25, ge=1, le=100)
    q: str | None = None
    customer_id: str | None = None
    status: str | None = Field(default=None, pattern="^(draft|sent|partially_paid|paid|overdue|cancelled)$")
    from_date: str | None = None
    to_date: str | None = None


class ExpenseCategorySchema(_Base):
    name: str = _str(1, 100)
    description: str | None = _str(0, 500, required=False)
    parent_id: str | None = None


class ExpenseCategoryUpdateSchema(_Base):
    name: str | None = _str(1, 100, required=False)
    description: str | None = _str(0, 500, required=False)
    parent_id: str | None = None
    is_active: bool | None = None


class ExpenseItemInputSchema(_Base):
    description: str = _str(1, 500)
    amount: float = Field(gt=0)
    tax_rate: float = Field(default=0, ge=0, le=100)


class ExpenseCreateSchema(_Base):
    supplier_id: str | None = None
    category_id: str | None = None
    financial_account_id: str | None = None
    branch_id: str | None = None
    date: str | None = None
    due_date: str | None = None
    description: str = _str(1, 2000)
    amount: float = Field(gt=0)
    tax_amount: float = Field(default=0, ge=0)
    payment_method: str | None = Field(default=None, pattern="^(cash|mpesa|bank|card|cheque|other)$")
    reference: str | None = _str(0, 100, required=False)
    notes: str | None = _str(0, 2000, required=False)
    status: str = Field(default="draft", pattern="^(draft|approved|paid|cancelled)$")
    items: list[ExpenseItemInputSchema] | None = None


class ExpenseUpdateSchema(_Base):
    supplier_id: str | None = None
    category_id: str | None = None
    financial_account_id: str | None = None
    branch_id: str | None = None
    date: str | None = None
    due_date: str | None = None
    description: str | None = _str(1, 2000, required=False)
    amount: float | None = Field(default=None, gt=0)
    tax_amount: float | None = Field(default=None, ge=0)
    payment_method: str | None = Field(default=None, pattern="^(cash|mpesa|bank|card|cheque|other)$")
    reference: str | None = _str(0, 100, required=False)
    notes: str | None = _str(0, 2000, required=False)
    status: str | None = Field(default=None, pattern="^(draft|approved|paid|cancelled)$")
    items: list[ExpenseItemInputSchema] | None = None


class ExpenseSearchSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=25, ge=1, le=100)
    q: str | None = None
    supplier_id: str | None = None
    category_id: str | None = None
    status: str | None = Field(default=None, pattern="^(draft|approved|paid|cancelled)$")
    payment_method: str | None = None
    from_date: str | None = None
    to_date: str | None = None


class ExpenseAttachmentSchema(_Base):
    filename: str = _str(1, 255)
    original_filename: str = _str(1, 255)
    mime_type: str | None = _str(1, 100, required=False)
    file_size: int | None = None
    storage_path: str = _str(1, 500)
    description: str | None = _str(0, 1000, required=False)


class PaymentCreateSchema(_Base):
    payment_type: str = Field(pattern="^(customer_receipt|supplier_payment|expense_payment|refund|other)$")
    customer_id: str | None = None
    supplier_id: str | None = None
    invoice_id: str | None = None
    expense_id: str | None = None
    financial_account_id: str | None = None
    counterparty_id: str | None = None
    related_transaction_id: str | None = None
    branch_id: str | None = None
    amount: float = Field(gt=0)
    payment_method: str = Field(pattern="^(cash|mpesa|bank|card|cheque|other)$")
    date: str | None = None
    reference: str | None = _str(0, 100, required=False)
    notes: str | None = _str(0, 2000, required=False)


class PaymentUpdateSchema(_Base):
    payment_type: str | None = Field(default=None, pattern="^(customer_receipt|supplier_payment|expense_payment|refund|other)$")
    customer_id: str | None = None
    supplier_id: str | None = None
    invoice_id: str | None = None
    expense_id: str | None = None
    financial_account_id: str | None = None
    counterparty_id: str | None = None
    related_transaction_id: str | None = None
    branch_id: str | None = None
    amount: float | None = Field(default=None, gt=0)
    payment_method: str | None = Field(default=None, pattern="^(cash|mpesa|bank|card|cheque|other)$")
    date: str | None = None
    reference: str | None = _str(0, 100, required=False)
    notes: str | None = _str(0, 2000, required=False)
    status: str | None = Field(default=None, pattern="^(pending|completed|failed|refunded|cancelled)$")


class PaymentSearchSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=25, ge=1, le=100)
    q: str | None = None
    customer_id: str | None = None
    supplier_id: str | None = None
    invoice_id: str | None = None
    expense_id: str | None = None
    payment_type: str | None = None
    payment_method: str | None = None
    status: str | None = None
    from_date: str | None = None
    to_date: str | None = None


class PaymentAllocationSchema(_Base):
    payment_id: str
    invoice_id: str
    amount: float = Field(gt=0)


class FinancialAccountSchema(_Base):
    code: str = _str(1, 20)
    name: str = _str(1, 150)
    description: str | None = _str(0, 1000, required=False)
    account_type: str = Field(pattern="^(asset|liability|equity|revenue|cogs|expense|ar|ap|cash|bank)$")
    parent_id: str | None = None
    is_control_account: bool | None = None
    default_currency: str | None = Field(default=None, pattern="^[A-Z]{3}$")
    is_bank_account: bool | None = None
    bank_details: dict | None = None


class FinancialAccountUpdateSchema(_Base):
    code: str | None = _str(1, 20, required=False)
    name: str | None = _str(1, 150, required=False)
    description: str | None = _str(0, 1000, required=False)
    account_type: str | None = Field(default=None, pattern="^(asset|liability|equity|revenue|cogs|expense|ar|ap|cash|bank)$")
    parent_id: str | None = None
    is_control_account: bool | None = None
    default_currency: str | None = Field(default=None, pattern="^[A-Z]{3}$")
    is_bank_account: bool | None = None
    bank_details: dict | None = None
    is_active: bool | None = None


class FinancialAccountSearchSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=25, ge=1, le=100)
    q: str | None = None
    account_type: str | None = Field(default=None, pattern="^(asset|liability|equity|revenue|cogs|expense|ar|ap|cash|bank)$")
    is_bank_account: bool | None = None


class JournalLineInputSchema(_Base):
    account_id: str
    line_type: str = Field(pattern="^(debit|credit)$")
    amount: float = Field(gt=0)
    description: str | None = _str(0, 1000, required=False)


class JournalEntryCreateSchema(_Base):
    date: str
    description: str = _str(1, 2000)
    reference_type: str | None = _str(0, 50, required=False)
    reference_id: str | None = None
    lines: list[JournalLineInputSchema] = Field(min_length=2)


class JournalEntryUpdateSchema(_Base):
    date: str | None = None
    description: str | None = _str(1, 2000, required=False)
    reference_type: str | None = _str(0, 50, required=False)
    reference_id: str | None = None
    status: str | None = Field(default=None, pattern="^(draft|posted|reversed|cancelled)$")
    lines: list[JournalLineInputSchema] | None = None


class JournalEntrySearchSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=25, ge=1, le=100)
    q: str | None = None
    status: str | None = Field(default=None, pattern="^(draft|posted|reversed|cancelled)$")
    from_date: str | None = None
    to_date: str | None = None
    reference_type: str | None = None
    reference_id: str | None = None


class TaxConfigSchema(_Base):
    name: str = _str(1, 100)
    code: str = _str(1, 20)
    rate: float = Field(ge=0, le=100)
    is_inclusive: bool = False
    is_default: bool = False
    applies_to: str = Field(default="both", pattern="^(sales|purchases|both)$")
    description: str | None = _str(0, 1000, required=False)


class TaxConfigUpdateSchema(_Base):
    name: str | None = _str(1, 100, required=False)
    code: str | None = _str(1, 20, required=False)
    rate: float | None = Field(default=None, ge=0, le=100)
    is_inclusive: bool | None = None
    is_default: bool | None = None
    applies_to: str | None = Field(default=None, pattern="^(sales|purchases|both)$")
    description: str | None = _str(0, 1000, required=False)
    is_active: bool | None = None


# ==================== Subscription / Platform (Super Admin) ====================

class SubscriptionPlanSchema(_Base):
    name: str = _str(1, 100)
    code: str = _str(1, 30)
    description: str | None = _str(0, 1000, required=False)
    tier: str = Field(pattern="^(free|starter|professional|enterprise)$")
    price_cents: int = Field(ge=0)
    currency: str = Field(pattern=CCY_RE.pattern)
    billing_cycle: str = Field(pattern="^(monthly|yearly|lifetime)$")
    max_users: int | None = None
    max_branches: int | None = None
    max_invoices: int | None = None
    storage_mb: int = Field(ge=0)
    trial_days: int = Field(default=14, ge=0, le=365)
    is_active: bool = True
    is_default: bool = False
    modules: list[str] = Field(default_factory=list)


class SubscriptionPlanUpdateSchema(_Base):
    name: str | None = _str(1, 100, required=False)
    description: str | None = _str(0, 1000, required=False)
    tier: str | None = Field(default=None, pattern="^(free|starter|professional|enterprise)$")
    price_cents: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, pattern=CCY_RE.pattern)
    billing_cycle: str | None = Field(default=None, pattern="^(monthly|yearly|lifetime)$")
    max_users: int | None = None
    max_branches: int | None = None
    max_invoices: int | None = None
    storage_mb: int | None = Field(default=None, ge=0)
    trial_days: int | None = Field(default=None, ge=0, le=365)
    is_active: bool | None = None
    is_default: bool | None = None
    modules: list[str] | None = None


# ==================== Audit ====================

class AuditLogSearchSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=50, ge=1, le=100)
    q: str | None = None
    user_id: str | None = None
    action: str | None = None
    resource: str | None = None
    resource_id: str | None = None
    from_date: str | None = None
    to_date: str | None = None


# ==================== Notifications ====================

class NotificationMarkReadSchema(_Base):
    notification_ids: list[str] = Field(default_factory=list)


# ==================== Dashboard & Reports ====================

class ReportQuerySchema(BaseModel):
    model_config = ConfigDict(extra="ignore")
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=25, ge=1, le=100)
    from_date: str | None = None
    to_date: str | None = None
    report: str = Field(
        default="profit_and_loss",
        pattern="^(profit_and_loss|pnl|sales|expenses|receivables_aging|receivables|payables_aging|payables|cash_flow|inventory)$",
    )
    aging_date: str | None = None
    export: str | None = Field(default=None, pattern="^(csv|pdf)$")


class BusinessStatusSchema(_Base):
    status: str = Field(pattern="^(active|inactive|suspended|trial|pending)")


class PlanChangeSchema(_Base):
    plan_id: str


class TrialExtensionSchema(_Base):
    """Manually add trial days to a tenant, e.g. once a payment clears."""

    days: int = Field(ge=1, le=365)
    reason: str | None = _str(0, 500, required=False)


class SuperAdminPlanSchema(SubscriptionPlanSchema):
    pass


class SuperAdminPlanUpdateSchema(SubscriptionPlanUpdateSchema):
    pass


class AdvanceRequestSchema(_Base):
    """A staff member asking to be paid out ahead of their earned balance."""

    amount: float = Field(gt=0)
    reason: str = _str(3, 500, required=True)


class AdvanceDecisionSchema(_Base):
    decision: str = Field(pattern="^(approved|rejected)$")
    note: str | None = _str(0, 500, required=False)


class AdvanceRequestSearchSchema(PaginationSchema):
    status: str | None = Field(default=None, pattern="^(pending|approved|rejected|cancelled)$")
