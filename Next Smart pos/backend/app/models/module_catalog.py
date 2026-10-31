"""The module tree the Permissions screen is built from.

A flat list of ``resource.action`` permission names is not something an owner
can reason about when they are deciding what a till operator may do. What they
can reason about is "can they get at the price list, and does the owner want to
approve changes to it". So the catalog is expressed as five groups of named
modules, each carrying:

``permissions``
    Every permission the module covers. Ticking the module grants exactly
    this set. A permission may appear in more than one module — the till needs
    ``item.read`` and ``payment.write`` to run a checkout, and so does the
    Products screen — and the grant is a union, so ticking either module is
    enough.

``mutations``
    The subset of ``permissions`` that changes data. Derived rather than
    written by hand so a permission cannot quietly become read-only because
    somebody forgot to list it. These are the actions an owner can put behind
    their password: flag a module with ``requires_owner_approval`` and every
    write/delete inside it needs an owner or admin to unlock it, while reading
    stays open.

This is deliberately a *separate* concept from ``SystemModule`` in
``subscription.py``. That one answers "did this business pay for the feature";
this one answers "may this role touch the feature". A business can buy the
whole platform and still hand a cashier nothing but the till.

The catalog is the single source of truth: the API serves it, the role editor
renders it, and ``require_permission_or_privileged`` consults it to decide
whether an action is unlockable by an owner password.
"""

from .iam import PERMISSION_CATALOG

# Every action that is not a plain read is a mutation, and therefore a candidate
# for owner approval. Anything unrecognised is treated as a mutation too: the
# safe direction to fail in is to ask a manager to unlock the action rather than
# to let it through unattended.
MUTATING_ACTIONS = frozenset({"write", "delete", "advance_request", "advance_approve"})

MODULE_GROUPS = [
    {
        "code": "sales",
        "label": "Sales",
        "description": "What the till does day to day: selling, customers and quotes.",
        "modules": [
            {
                "code": "pos",
                "label": "Point of Sale",
                "description": "Run a checkout: key in a sale, take payment, print a receipt.",
                "permissions": [
                    "sale.read", "sale.write",
                    "payment.read", "payment.write",
                    "customer.read", "customer.write",
                    "item.read", "category.read", "uom.read",
                    "inventory.read",
                    "business.read", "branch.read", "profile.read",
                ],
            },
            {
                "code": "invoices",
                "label": "Sales Invoices",
                "description": "Browse completed sales and their receipts.",
                "permissions": ["sale.read"],
            },
            {
                "code": "quotations",
                "label": "Quotations",
                "description": "Price lists sent to customers before they buy.",
                "permissions": ["quotation.read", "quotation.write", "quotation.delete"],
            },
            {
                "code": "customers",
                "label": "Customers",
                "description": "The customer list, balances and contact details.",
                "permissions": ["customer.read", "customer.write", "customer.delete"],
            },
            {
                "code": "customer_payments",
                "label": "Customer Payments",
                "description": "Record money received from a customer against their account.",
                "permissions": ["customer_payment.read", "customer_payment.write"],
            },
        ],
    },
    {
        "code": "products",
        "label": "Products",
        "description": "The catalog and the stock behind it.",
        "modules": [
            {
                "code": "items",
                "label": "Products & Services",
                "description": "The price list. Deleting from here is destructive, so it is a natural thing to put behind an owner password.",
                "permissions": ["item.read", "item.write", "item.delete"],
            },
            {
                "code": "categories",
                "label": "Categories",
                "description": "How products are grouped on the till.",
                "permissions": ["category.read", "category.write", "category.delete"],
            },
            {
                "code": "units",
                "label": "Units of Measure",
                "description": "Pieces, kilos, crates and the conversions between them.",
                "permissions": ["uom.read", "uom.write", "uom.delete"],
            },
            {
                "code": "inventory",
                "label": "Stock & Adjustments",
                "description": "Stock balances and the manual adjustments that move them.",
                "permissions": ["inventory.read", "inventory.write"],
            },
            {
                "code": "warehouses",
                "label": "Warehouses",
                "description": "Where stock is physically kept.",
                "permissions": ["warehouse.read", "warehouse.write", "warehouse.delete"],
            },
            {
                "code": "suppliers",
                "label": "Suppliers",
                "description": "Who the business buys from, and their terms.",
                "permissions": ["supplier.read", "supplier.write", "supplier.delete"],
            },
            {
                "code": "purchases",
                "label": "Purchases",
                "description": "Purchase orders raised to restock.",
                "permissions": ["purchase.read", "purchase.write"],
            },
            {
                "code": "supplier_payments",
                "label": "Supplier Payments",
                "description": "Money paid out to suppliers.",
                "permissions": ["supplier_payment.read", "supplier_payment.write"],
            },
        ],
    },
    {
        "code": "finance",
        "label": "Finance",
        "description": "Money owed, money spent and the books behind them.",
        "modules": [
            {
                "code": "invoices",
                "label": "Invoices",
                "description": "Customer invoices raised outside the till.",
                "permissions": ["invoice.read", "invoice.write"],
            },
            {
                "code": "payments",
                "label": "Payments & Receipts",
                "description": "The full payment ledger across every channel.",
                "permissions": ["payment.read", "payment.write"],
            },
            {
                "code": "expenses",
                "label": "Expenses",
                "description": "Rent, utilities, wages and everything else the business spends on.",
                "permissions": ["expense.read", "expense.write"],
            },
            {
                "code": "accounts",
                "label": "Chart of Accounts",
                "description": "The accounts that postings land in.",
                "permissions": ["account.read", "account.write"],
            },
            {
                "code": "journals",
                "label": "Journal Entries",
                "description": "Manual postings between accounts, and posting them to the ledger.",
                "permissions": ["journal.read", "journal.write"],
            },
            {
                "code": "taxes",
                "label": "Taxes",
                "description": "Tax rates applied at the till and on invoices.",
                "permissions": ["tax.read", "tax.write"],
            },
        ],
    },
    {
        "code": "business",
        "label": "Business",
        "description": "The business itself and the people who work in it.",
        "modules": [
            {
                "code": "profile",
                "label": "Business Profile",
                "description": "Name, address, receipt details and tax identity.",
                "permissions": ["business.read", "business.write"],
            },
            {
                "code": "branches",
                "label": "Branches",
                "description": "Outlets, counters and warehouses the business trades from.",
                "permissions": ["branch.read", "branch.write"],
            },
            {
                "code": "staff",
                "label": "Staff",
                "description": "Inviting staff, changing what they can do, and removing them.",
                "permissions": [
                    "user.read", "user.write", "user.delete",
                    "profile.read", "profile.write",
                ],
            },
            {
                "code": "roles",
                "label": "Roles & Permissions",
                "description": "Creating roles and deciding what each one can reach.",
                "permissions": ["role.read", "role.write", "role.delete", "permission.read"],
            },
        ],
    },
    {
        "code": "reports_admin",
        "label": "Reports & Admin",
        "description": "The company-wide view: figures, pay and the audit trail.",
        "modules": [
            {
                "code": "dashboard",
                "label": "Dashboard",
                "description": "Today's takings and the headline numbers.",
                "permissions": ["dashboard.read"],
            },
            {
                "code": "reports",
                "label": "Reports",
                "description": "Sales, stock and profit reports.",
                "permissions": ["report.read"],
            },
            {
                "code": "earnings",
                "label": "Earnings & Advances",
                "description": "Commission, salary and the advance requests staff raise against them.",
                "permissions": [
                    "earnings.read",
                    "earnings.advance_request",
                    "earnings.advance_approve",
                ],
            },
            {
                "code": "tasks",
                "label": "To-do Tasks",
                "description": "The shared task list for the business.",
                "permissions": ["todo.read", "todo.write", "todo.delete"],
            },
            {
                "code": "notifications",
                "label": "Notifications",
                "description": "Low-stock warnings, expense reminders and the in-app inbox.",
                "permissions": ["notification.read", "notification.write"],
            },
            {
                "code": "audit",
                "label": "Audit Trail",
                "description": "Who changed what, and when.",
                "permissions": ["audit.read"],
            },
        ],
    },
]

# Human wording for the approval prompt, derived from the permission's own
# resource/action so a new permission gets a sensible label without a second
# list to keep in step.
_ACTION_PHRASES = {
    "write": "create or update",
    "delete": "delete",
    "advance_request": "request an advance against",
    "advance_approve": "approve advances for",
}

_RESOURCE_NOUNS = {
    "business": "business settings",
    "branch": "branches",
    "user": "staff accounts",
    "role": "roles",
    "customer": "customers",
    "supplier": "suppliers",
    "item": "products",
    "category": "product categories",
    "uom": "units of measure",
    "warehouse": "warehouses",
    "inventory": "stock levels",
    "sale": "sales",
    "quotation": "quotations",
    "payment": "payments",
    "purchase": "purchases",
    "supplier_payment": "supplier payments",
    "customer_payment": "customer payments",
    "todo": "to-do tasks",
    "earnings": "staff earnings",
    "notification": "notifications",
    "invoice": "invoices",
    "expense": "expenses",
    "account": "accounts",
    "journal": "journal entries",
    "tax": "tax settings",
}


def _resource_of(permission_name):
    return permission_name.split(".", 1)[0]


def _action_of(permission_name):
    return permission_name.split(".", 1)[1] if "." in permission_name else ""


def _module_code(group_code, module_code):
    """Globally unique key for a module, used in ``Role.requires_owner_approval``."""
    return f"{group_code}.{module_code}"


def _build():
    groups = []
    modules_by_code = {}
    permission_module = {}
    mutations = {}
    labels = {}

    for group in MODULE_GROUPS:
        built_modules = []
        for module in group["modules"]:
            code = _module_code(group["code"], module["code"])
            names = list(dict.fromkeys(module["permissions"]))
            module_mutations = [n for n in names if _action_of(n) not in ("read", "")]
            for name in module_mutations:
                mutations.setdefault(name, code)
            # First module to claim a permission owns the approval flag; a
            # permission granted by two modules is still one permission, and
            # asking for approval twice would be nonsense.
            for name in names:
                permission_module.setdefault(name, code)
            built_modules.append(
                {
                    "code": code,
                    "label": module["label"],
                    "description": module["description"],
                    "permissions": names,
                    "mutations": module_mutations,
                }
            )
            modules_by_code[code] = built_modules[-1]
        groups.append(
            {
                "code": group["code"],
                "label": group["label"],
                "description": group["description"],
                "modules": built_modules,
            }
        )

    for name, _, action, _desc in PERMISSION_CATALOG:
        phrase = _ACTION_PHRASES.get(action)
        noun = _RESOURCE_NOUNS.get(_resource_of(name), _resource_of(name))
        labels[name] = f"{phrase} {noun}" if phrase else noun

    return groups, modules_by_code, permission_module, mutations, labels


(
    MODULE_TREE,
    MODULES_BY_CODE,
    PERMISSION_MODULE,
    MUTATION_MODULE,
    PRIVILEGED_ACTION_LABELS,
) = _build()

# Every permission name, in catalog order. Used by the drift test to prove the
# module tree and the permission catalog describe the same thing.
CATALOG_PERMISSION_NAMES = tuple(name for name, *_rest in PERMISSION_CATALOG)

MUTATION_PERMISSION_NAMES = frozenset(MUTATION_MODULE)


def module_code_for(permission_name):
    """Which module a permission belongs to, or ``None`` if it is unclaimed."""
    return PERMISSION_MODULE.get(permission_name)


def modules_for_permissions(permission_names):
    """The module codes a holder of *permission_names* has been given.

    This is what the interface asks when it needs to know which modules a
    person was allocated, so it answers with the same rule the Roles editor
    ticks checkboxes by: a module is granted when the holder has at least one
    of the permissions it covers. Requiring the whole set instead would tell a
    role that can read the price list but not edit it that it has no products
    module, which is not a state the owner can express in the editor either.

    The answer is deliberately allowed to be a superset. ``profile.read``
    appears in both ``business.staff`` and ``business.branches``, because the
    catalog groups it with "the person who runs the team" — so a cashier's
    module list mentions staff management even though they hold none of
    ``user.read``. That is fine for a question about *which modules to render*,
    which is what this is for; anything that gates access still checks the
    specific permission, so the extra codes never widen what is reachable.
    """
    held = set(permission_names or ())
    return sorted(
        code
        for code, module in MODULES_BY_CODE.items()
        if held.intersection(module["permissions"])
    )


def is_privileged_action(permission_name):
    """Can an owner/admin password unlock this action?

    Only mutations can. A read is never worth a second signature, and allowing
    one would widen the set of things an approval token can be spent on.
    """
    return permission_name in MUTATION_MODULE


def privileged_label(permission_name):
    return PRIVILEGED_ACTION_LABELS.get(permission_name, permission_name)


def module_permissions(codes):
    """Union of the permissions granted by *codes*, in stable order."""
    names = []
    seen = set()
    for code in codes:
        module = MODULES_BY_CODE.get(code)
        if not module:
            continue
        for name in module["permissions"]:
            if name not in seen:
                seen.add(name)
                names.append(name)
    return names


def module_mutations(codes):
    """Union of the mutations covered by *codes*."""
    names = []
    seen = set()
    for code in codes:
        module = MODULES_BY_CODE.get(code)
        if not module:
            continue
        for name in module["mutations"]:
            if name not in seen:
                seen.add(name)
                names.append(name)
    return names


def serialize_tree():
    """The catalog as the API serves it, with mutability precomputed."""
    return [
        {
            "code": group["code"],
            "label": group["label"],
            "description": group["description"],
            "modules": [
                {
                    "code": module["code"],
                    "label": module["label"],
                    "description": module["description"],
                    "permissions": module["permissions"],
                    "mutations": module["mutations"],
                }
                for module in group["modules"]
            ],
        }
        for group in MODULE_TREE
    ]
