from sqlalchemy import insert, select

from .extensions import db
from .models import (
    Permission,
    Role,
    RolePermission,
    SYSTEM_ROLES,
    PERMISSION_CATALOG,
    SubscriptionPlan,
    SystemModule,
    SYSTEM_MODULES,
    get_default_plan_modules,
    User,
)
from werkzeug.security import generate_password_hash

# The expense categories every business uses. Kept deliberately small so the
# New Expense form stays a short, unambiguous list. A business can still rename,
# deactivate or add to these from the Manage categories screen.
DEFAULT_EXPENSE_CATEGORIES = [
    ("Utilities", "Electricity, water, internet and phone"),
    ("Rent", "Premises rent and service charge"),
    ("Equipment", "Tools, machines and IT equipment"),
    ("Salaries and Wages", "Staff salaries, wages and statutory benefits"),
    ("Transport", "Fuel, fares, deliveries and vehicle upkeep"),
    ("Marketing", "Advertising, print and online promotion"),
    ("Other", "Anything that does not fit another category"),
]

# Categories that represent a recurring bill. An expense in one of these with a
# due date inside the reminder window notifies the business owner.
RECURRING_EXPENSE_CATEGORIES = {
    "Utilities",
    "Rent",
    "Salaries and Wages",
    "Transport",
}


def init_default_permissions():
    """Seed the global permission catalog (idempotent)."""
    existing = {p.name for p in db.session.scalars(select(Permission)).all()}
    for name, resource, action, desc in PERMISSION_CATALOG:
        if name not in existing:
            db.session.add(
                Permission(name=name, resource=resource, action=action, description=desc)
            )
    db.session.flush()


def _table_has_column(table_name: str, column_name: str) -> bool:
    """Does the connected database really have this column?

    Seed helpers run both from the CLI (against a migrated schema) and from
    inside Alembic migrations (against whatever exists at that point in the
    chain), so they cannot assume the model definition matches the table.
    """
    from sqlalchemy import inspect
    from sqlalchemy.exc import NoSuchTableError

    # Inspect the session's own connection, not get_bind(): during a migration
    # the inspector must see the very schema the next statement will hit.
    inspector = inspect(db.session.connection())
    try:
        return column_name in {col["name"] for col in inspector.get_columns(table_name)}
    except NoSuchTableError:
        return False


def seed_platform_defaults():
    """Idempotently seed global subscription plans and system modules."""
    from datetime import datetime, timezone

    # Select the code column directly rather than the model: a migration that
    # calls this before every model column exists would otherwise emit a
    # SELECT naming a column the table does not have yet.
    existing_plans = {
        code for (code,) in db.session.execute(select(SubscriptionPlan.__table__.c.code)).all()
    }
    # name, code, tier, price_cents, cycle, max_users, max_branches,
    # max_invoices, storage_mb, is_default, description
    # Starter is the default so a new signup lands on a real paid tier with a
    # trial attached. There is no seeded Free plan: an operator who wants a
    # free tier creates it in the super-admin plan editor, which keeps the
    # catalogue free to shape without a hard-coded freebie nobody can remove.
    plan_defs = [
        ("Starter", "starter", "starter", 2900, "monthly", 5, 1, 500, 1000, True, "Starter plan"),
        ("Professional", "professional", "professional", 7900, "monthly", 15, 5, 5000, 10000, False, "Professional plan"),
        ("Enterprise", "enterprise", "enterprise", 19900, "monthly", None, None, None, None, False, "Enterprise plan"),
    ]
    # Every tier starts a new signup on the same 14-day free trial. Change a
    # plan's trial length from the super-admin plan editor; the column default
    # keeps this value for any plan added later.
    default_trial_days = 14
    # seed_platform_defaults is also called by the c2f7a1b93d45 migration, which
    # runs before the trial_days column is added. Only pass the field once the
    # live table actually has it, or a fresh `upgrade` from base fails.
    has_trial_days = _table_has_column("subscription_plans", "trial_days")
    for name, code, tier, price, cycle, mu, mb, mi, st, is_default, desc in plan_defs:
        if code not in existing_plans:
            values = dict(
                name=name,
                code=code,
                description=desc,
                tier=tier,
                price_cents=price,
                currency="USD",
                billing_cycle=cycle,
                max_users=mu,
                max_branches=mb,
                max_invoices=mi,
                # Enterprise seeds None; the column is NOT NULL, and a Core
                # insert no longer picks up the model default for us.
                storage_mb=0 if st is None else st,
                is_default=is_default,
                modules=get_default_plan_modules(tier),
            )
            if has_trial_days:
                values["trial_days"] = default_trial_days
            # A Core insert, not the ORM: the ORM would apply the column-level
            # default and name trial_days even when the table predates it.
            db.session.execute(insert(SubscriptionPlan.__table__).values(**values))

    existing_modules = {m.code for m in db.session.scalars(select(SystemModule)).all()}
    for m in SYSTEM_MODULES:
        if m["code"] not in existing_modules:
            db.session.add(
                SystemModule(
                    code=m["code"],
                    name=m["name"],
                    description=m["description"],
                    is_enabled=True,
                    is_core=m["is_core"],
                    min_plan_tier=None,
                )
            )
    db.session.flush()


def seed_database(app):
    with app.app_context():
        init_default_permissions()
        seed_platform_defaults()
        db.session.commit()


def reset_db(app):
    """Drop and recreate all tables, then seed permissions (dev only)."""
    if app.config.get("TESTING"):
        with app.app_context():
            db.drop_all()
            db.create_all()
            init_default_permissions()
            seed_platform_defaults()
            db.session.commit()
        return
    with app.app_context():
        db.drop_all()
        db.create_all()
        seed_database(app)


def bootstrap_business_roles(business):
    """Ensure the system roles (Owner/Admin/Accountant/Employee/Cashier) exist.

    Ensures the permission catalog exists first so role assignments resolve.

    Subscription provisioning deliberately does NOT live here: it is owned by
    the registration workflow (``auth_service.register``) so that a failure to
    grant the free trial aborts signup instead of silently leaving a business
    with no subscription, which would lock every request out of the API.

    Safe to call repeatedly against an existing business. It is *additive*: a
    system role that already exists gains any newly-added permission from its
    definition, but permissions an operator has since removed from a system role
    are left alone rather than being force-restored on every boot.
    """
    init_default_permissions()
    seed_platform_defaults()
    perm_map = {p.name: p for p in db.session.scalars(select(Permission)).all()}
    existing = {
        role.name: role
        for role in db.session.scalars(
            select(Role).where(Role.business_id == business.id, Role.is_system.is_(True))
        ).all()
    }
    for role_name, perm_names in SYSTEM_ROLES.items():
        role = existing.get(role_name)
        if role is None:
            role = Role(
                business_id=business.id,
                name=role_name,
                description=f"System role: {role_name}",
                is_system=True,
            )
            db.session.add(role)
            db.session.flush()
            existing[role_name] = role

        held = {
            p.name
            for p in db.session.scalars(
                select(Permission)
                .join(RolePermission, RolePermission.permission_id == Permission.id)
                .where(RolePermission.role_id == role.id)
            ).all()
        }
        for pname in perm_names:
            perm = perm_map.get(pname)
            if perm is None or pname in held:
                continue
            db.session.add(RolePermission(role_id=role.id, permission_id=perm.id))
            held.add(pname)
    db.session.flush()


def sync_system_roles_for_all_businesses():
    """Backfill new system roles/permissions onto every existing business.

    Run after adding a permission or a system role: businesses registered
    before that change would otherwise never see it.
    """
    from .models import Business

    businesses = db.session.scalars(select(Business)).all()
    for business in businesses:
        bootstrap_business_roles(business)
    db.session.commit()
    return len(businesses)


def bootstrap_expense_categories(business):
    """Give a business the starter expense category list (idempotent).

    Only fills gaps: a category that already exists is left alone, so calling
    this again after a business renamed or extended the list changes nothing.
    """
    from .models import ExpenseCategory

    existing = {
        name
        for (name,) in db.session.execute(
            select(ExpenseCategory.name).where(
                ExpenseCategory.business_id == business.id
            )
        )
    }
    for name, description in DEFAULT_EXPENSE_CATEGORIES:
        if name in existing:
            continue
        db.session.add(
            ExpenseCategory(
                business_id=business.id,
                name=name,
                description=description,
                created_by=getattr(business, "created_by", None),
            )
        )
    db.session.flush()


__all__ = [
    "init_default_permissions",
    "seed_platform_defaults",
    "seed_database",
    "reset_db",
    "bootstrap_business_roles",
    "sync_system_roles_for_all_businesses",
    "bootstrap_expense_categories",
    "create_superadmin_user",
    "DEFAULT_EXPENSE_CATEGORIES",
    "RECURRING_EXPENSE_CATEGORIES",
]


def create_superadmin_user(
    email: str,
    password: str,
    first_name: str = "Super",
    last_name: str = "Admin",
    username: str | None = None,
    pin: str | None = None,
) -> User:
    """Create or update the platform superadmin user (idempotent).

    The superadmin has no business membership and uses the global
    SUPERADMIN_API_TOKEN for cross-tenant administration.

    ``username`` and ``pin`` are only applied when supplied, so re-running this
    to fix the password does not silently wipe a PIN an operator set by hand.
    """
    existing = db.session.scalar(select(User).where(User.email == email))
    if existing:
        if not existing.is_superadmin:
            existing.is_superadmin = True
        _apply_superadmin_credentials(existing, password, username, pin)
        db.session.flush()
        return existing

    user = User(
        email=email,
        password_hash=generate_password_hash(password, method="scrypt"),
        first_name=first_name,
        last_name=last_name,
        is_email_verified=True,
        is_superadmin=True,
        is_active=True,
    )
    if username:
        user.username = username.strip().lower()
    if pin:
        user.pin_hash = generate_password_hash(pin.strip(), method="scrypt")
    db.session.add(user)
    db.session.flush()
    return user


def _apply_superadmin_credentials(user, password, username, pin):
    """Set whichever of the superadmin's alternate credentials were supplied."""
    from werkzeug.security import check_password_hash

    if password and not check_password_hash(user.password_hash, password):
        user.password_hash = generate_password_hash(password, method="scrypt")
    if username:
        normalized = username.strip().lower()
        clash = db.session.scalar(
            select(User).where(User.username == normalized, User.id != user.id)
        )
        if clash is not None:
            from .exceptions import ConflictError

            raise ConflictError(f"Username '{normalized}' is already taken")
        user.username = normalized
    if pin:
        user.pin_hash = generate_password_hash(pin.strip(), method="scrypt")
