from flask import Blueprint, g, request

from ...schemas import (
    InvoiceCreateSchema,
    InvoiceUpdateSchema,
    InvoiceSearchSchema,
    ExpenseCategorySchema,
    ExpenseCategoryUpdateSchema,
    ExpenseCreateSchema,
    ExpenseUpdateSchema,
    ExpenseSearchSchema,
    ExpenseAttachmentSchema,
    PaymentCreateSchema,
    PaymentUpdateSchema,
    PaymentSearchSchema,
    PaymentAllocationSchema,
    FinancialAccountSchema,
    FinancialAccountUpdateSchema,
    FinancialAccountSearchSchema,
    JournalEntryCreateSchema,
    JournalEntryUpdateSchema,
    JournalEntrySearchSchema,
    TaxConfigSchema,
    TaxConfigUpdateSchema,
    PaginationSchema,
)
from ...services import (
    get_invoice_service,
    get_expense_service,
    get_payment_service,
    get_accounting_service,
    get_tax_service,
    get_pdf_service,
)
from ...utils.security import parse_uuid
from ...validation import validate_body, validate_query
from ..permissions import gate_module, login_required, require_permission
from ..responses import no_content, ok, created

finance_bp = Blueprint("finance", __name__)
finance_bp.before_request(gate_module("finance"))


def _business_id():
    return g.ctx.business_id


# ==================== Invoices ====================

@finance_bp.route("/invoices", methods=["GET"])
@require_permission("invoice.read")
@validate_query(InvoiceSearchSchema)
def list_invoices():
    pagination = request.validated_query
    items, total, pages = get_invoice_service(_business_id()).list_invoices(
        pagination,
        customer_id=pagination.q,  # We'll use q for search
    )
    return ok(
        {"invoices": [i.to_dict() for i in items]},
        meta={"page": pagination.page, "per_page": pagination.per_page, "total": total, "pages": pages},
    )


@finance_bp.route("/invoices", methods=["POST"])
@require_permission("invoice.write")
@validate_body(InvoiceCreateSchema)
def create_invoice():
    invoice = get_invoice_service(_business_id()).create_invoice(
        request.validated_body, g.ctx.user_id
    )
    return created({"invoice": invoice.to_dict()})


@finance_bp.route("/invoices/<invoice_id>", methods=["GET"])
@require_permission("invoice.read")
def get_invoice(invoice_id):
    invoice = get_invoice_service(_business_id()).get_invoice(parse_uuid(invoice_id))
    return ok({"invoice": invoice.to_dict()})


@finance_bp.route("/invoices/<invoice_id>", methods=["PATCH"])
@require_permission("invoice.write")
@validate_body(InvoiceUpdateSchema)
def update_invoice(invoice_id):
    invoice = get_invoice_service(_business_id()).update_invoice(
        parse_uuid(invoice_id), request.validated_body, g.ctx.user_id
    )
    return ok({"invoice": invoice.to_dict()})


@finance_bp.route("/invoices/<invoice_id>/send", methods=["POST"])
@require_permission("invoice.write")
def send_invoice(invoice_id):
    invoice = get_invoice_service(_business_id()).send_invoice(parse_uuid(invoice_id), g.ctx.user_id)
    return ok({"invoice": invoice.to_dict()})


@finance_bp.route("/invoices/<invoice_id>/cancel", methods=["POST"])
@require_permission("invoice.write")
@validate_body(__import__("pydantic").BaseModel)  # Empty body for reason
def cancel_invoice(invoice_id):
    # Note: reason would come from request.json
    reason = request.json.get("reason") if request.json else None
    invoice = get_invoice_service(_business_id()).cancel_invoice(parse_uuid(invoice_id), g.ctx.user_id, reason)
    return ok({"invoice": invoice.to_dict()})


@finance_bp.route("/invoices/<invoice_id>/pdf", methods=["GET"])
@require_permission("invoice.read")
def get_invoice_pdf(invoice_id):
    invoice = get_invoice_service(_business_id()).get_invoice(parse_uuid(invoice_id))
    business = g.ctx.business
    customer = invoice.customer
    from ...services import business_service
    config = business_service.get_config(_business_id())
    
    pdf_service = get_pdf_service(_business_id())
    try:
        pdf_bytes = pdf_service.generate_invoice_pdf(invoice, business, customer, config)
    except Exception as e:
        from ...exceptions import BusinessError
        raise BusinessError(f"PDF generation failed: {e}")
    
    from flask import send_file
    return send_file(
        __import__("io").BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"invoice-{invoice.invoice_number}.pdf",
    )


@finance_bp.route("/invoices/<invoice_id>", methods=["DELETE"])
@require_permission("invoice.write")
def delete_invoice(invoice_id):
    get_invoice_service(_business_id()).delete_invoice(parse_uuid(invoice_id))
    return no_content()


# ==================== Expense Categories ====================

@finance_bp.route("/expense-categories", methods=["GET"])
@require_permission("expense.read")
def list_expense_categories():
    items = get_expense_service(_business_id()).list_categories()
    return ok({"categories": [c.to_dict() for c in items]})


@finance_bp.route("/expense-categories", methods=["POST"])
@require_permission("expense.write")
@validate_body(ExpenseCategorySchema)
def create_expense_category():
    cat = get_expense_service(_business_id()).create_category(request.validated_body, g.ctx.user_id)
    return created({"category": cat.to_dict()})


@finance_bp.route("/expense-categories/<category_id>", methods=["GET"])
@require_permission("expense.read")
def get_expense_category(category_id):
    cat = get_expense_service(_business_id()).get_category(parse_uuid(category_id))
    return ok({"category": cat.to_dict()})


@finance_bp.route("/expense-categories/<category_id>", methods=["PATCH"])
@require_permission("expense.write")
@validate_body(ExpenseCategoryUpdateSchema)
def update_expense_category(category_id):
    cat = get_expense_service(_business_id()).update_category(parse_uuid(category_id), request.validated_body, g.ctx.user_id)
    return ok({"category": cat.to_dict()})


@finance_bp.route("/expense-categories/<category_id>", methods=["DELETE"])
@require_permission("expense.write")
def delete_expense_category(category_id):
    get_expense_service(_business_id()).delete_category(parse_uuid(category_id))
    return no_content()


# ==================== Expenses ====================

@finance_bp.route("/expenses", methods=["GET"])
@require_permission("expense.read")
@validate_query(ExpenseSearchSchema)
def list_expenses():
    pagination = request.validated_query
    items, total, pages = get_expense_service(_business_id()).list_expenses(
        pagination,
        supplier_id=pagination.supplier_id,
        category_id=pagination.category_id,
        status=pagination.status,
        payment_method=pagination.payment_method,
        from_date=pagination.from_date,
        to_date=pagination.to_date,
    )
    return ok(
        {"expenses": [e.to_dict() for e in items]},
        meta={"page": pagination.page, "per_page": pagination.per_page, "total": total, "pages": pages},
    )


@finance_bp.route("/expenses", methods=["POST"])
@require_permission("expense.write")
@validate_body(ExpenseCreateSchema)
def create_expense():
    expense = get_expense_service(_business_id()).create_expense(request.validated_body, g.ctx.user_id)
    return created({"expense": expense.to_dict()})


@finance_bp.route("/expenses/<expense_id>", methods=["GET"])
@require_permission("expense.read")
def get_expense(expense_id):
    expense = get_expense_service(_business_id()).get_expense(parse_uuid(expense_id))
    return ok({"expense": expense.to_dict()})


@finance_bp.route("/expenses/<expense_id>", methods=["PATCH"])
@require_permission("expense.write")
@validate_body(ExpenseUpdateSchema)
def update_expense(expense_id):
    expense = get_expense_service(_business_id()).update_expense(parse_uuid(expense_id), request.validated_body, g.ctx.user_id)
    return ok({"expense": expense.to_dict()})


@finance_bp.route("/expenses/<expense_id>/approve", methods=["POST"])
@require_permission("expense.write")
def approve_expense(expense_id):
    expense = get_expense_service(_business_id()).approve_expense(parse_uuid(expense_id), g.ctx.user_id)
    return ok({"expense": expense.to_dict()})


@finance_bp.route("/expenses/<expense_id>/mark-paid", methods=["POST"])
@require_permission("expense.write")
def mark_expense_paid(expense_id):
    expense = get_expense_service(_business_id()).mark_expense_paid(parse_uuid(expense_id), g.ctx.user_id)
    return ok({"expense": expense.to_dict()})


@finance_bp.route("/expenses/<expense_id>/cancel", methods=["POST"])
@require_permission("expense.write")
def cancel_expense(expense_id):
    reason = request.json.get("reason") if request.json else None
    expense = get_expense_service(_business_id()).cancel_expense(parse_uuid(expense_id), g.ctx.user_id, reason)
    return ok({"expense": expense.to_dict()})


@finance_bp.route("/expenses/<expense_id>/attachments", methods=["POST"])
@require_permission("expense.write")
@validate_body(ExpenseAttachmentSchema)
def add_expense_attachment(expense_id):
    attachment = get_expense_service(_business_id()).add_attachment(parse_uuid(expense_id), request.validated_body, g.ctx.user_id)
    return created({"attachment": attachment.to_dict()})


@finance_bp.route("/expenses/<expense_id>/attachments/<attachment_id>", methods=["DELETE"])
@require_permission("expense.write")
def remove_expense_attachment(expense_id, attachment_id):
    get_expense_service(_business_id()).remove_attachment(parse_uuid(attachment_id))
    return no_content()


@finance_bp.route("/expenses/<expense_id>", methods=["DELETE"])
@require_permission("expense.write")
def delete_expense(expense_id):
    get_expense_service(_business_id()).delete_expense(parse_uuid(expense_id))
    return no_content()


# ==================== Payments ====================

@finance_bp.route("/payments", methods=["GET"])
@require_permission("payment.read")
@validate_query(PaymentSearchSchema)
def list_payments():
    pagination = request.validated_query
    items, total, pages = get_payment_service(_business_id()).list_payments(
        pagination,
        customer_id=pagination.customer_id,
        supplier_id=pagination.supplier_id,
        invoice_id=pagination.invoice_id,
        expense_id=pagination.expense_id,
        payment_type=pagination.payment_type,
        payment_method=pagination.payment_method,
        status=pagination.status,
        from_date=pagination.from_date,
        to_date=pagination.to_date,
    )
    return ok(
        {"payments": [p.to_dict() for p in items]},
        meta={"page": pagination.page, "per_page": pagination.per_page, "total": total, "pages": pages},
    )


@finance_bp.route("/payments", methods=["POST"])
@require_permission("payment.write")
@validate_body(PaymentCreateSchema)
def create_payment():
    payment = get_payment_service(_business_id()).create_payment(request.validated_body, g.ctx.user_id)
    return created({"payment": payment.to_dict()})


@finance_bp.route("/payments/<payment_id>", methods=["GET"])
@require_permission("payment.read")
def get_payment(payment_id):
    payment = get_payment_service(_business_id()).get_payment(parse_uuid(payment_id))
    return ok({"payment": payment.to_dict()})


@finance_bp.route("/payments/<payment_id>", methods=["PATCH"])
@require_permission("payment.write")
@validate_body(PaymentUpdateSchema)
def update_payment(payment_id):
    payment = get_payment_service(_business_id()).update_payment(parse_uuid(payment_id), request.validated_body, g.ctx.user_id)
    return ok({"payment": payment.to_dict()})


@finance_bp.route("/payments/<payment_id>/reverse", methods=["POST"])
@require_permission("payment.write")
def reverse_payment(payment_id):
    reason = request.json.get("reason") if request.json else None
    reversal = get_payment_service(_business_id()).reverse_payment(parse_uuid(payment_id), g.ctx.user_id, reason)
    return created({"reversal": reversal.to_dict()})


@finance_bp.route("/payments/<payment_id>", methods=["DELETE"])
@require_permission("payment.write")
def delete_payment(payment_id):
    get_payment_service(_business_id()).delete_payment(parse_uuid(payment_id))
    return no_content()


# ==================== Financial Accounts (Chart of Accounts) ====================

@finance_bp.route("/accounts", methods=["GET"])
@require_permission("account.read")
@validate_query(FinancialAccountSearchSchema)
def list_accounts():
    pagination = request.validated_query
    items = get_accounting_service(_business_id()).list_accounts(
        account_type=pagination.account_type,
        is_bank_account=pagination.is_bank_account,
        is_active=True,
    )
    return ok({"accounts": [a.to_dict() for a in items]})


@finance_bp.route("/accounts", methods=["POST"])
@require_permission("account.write")
@validate_body(FinancialAccountSchema)
def create_account():
    account = get_accounting_service(_business_id()).create_account(request.validated_body, g.ctx.user_id)
    return created({"account": account.to_dict()})


@finance_bp.route("/accounts/<account_id>", methods=["GET"])
@require_permission("account.read")
def get_account(account_id):
    account = get_accounting_service(_business_id()).get_account(parse_uuid(account_id))
    return ok({"account": account.to_dict()})


@finance_bp.route("/accounts/<account_id>", methods=["PATCH"])
@require_permission("account.write")
@validate_body(FinancialAccountUpdateSchema)
def update_account(account_id):
    account = get_accounting_service(_business_id()).update_account(parse_uuid(account_id), request.validated_body, g.ctx.user_id)
    return ok({"account": account.to_dict()})


@finance_bp.route("/accounts/<account_id>", methods=["DELETE"])
@require_permission("account.write")
def delete_account(account_id):
    get_accounting_service(_business_id()).delete_account(parse_uuid(account_id))
    return no_content()


@finance_bp.route("/accounts/<account_id>/balance", methods=["GET"])
@require_permission("account.read")
def get_account_balance(account_id):
    as_of = request.args.get("as_of")
    from datetime import datetime
    as_of_date = datetime.fromisoformat(as_of) if as_of else None
    balance = get_accounting_service(_business_id()).get_account_balance(parse_uuid(account_id), as_of_date)
    return ok({"balance": float(balance)})


@finance_bp.route("/trial-balance", methods=["GET"])
@require_permission("account.read")
def get_trial_balance():
    as_of = request.args.get("as_of")
    from datetime import datetime
    as_of_date = datetime.fromisoformat(as_of) if as_of else None
    tb = get_accounting_service(_business_id()).get_trial_balance(as_of_date)
    return ok({"trial_balance": tb})


# ==================== Journal Entries ====================

@finance_bp.route("/journal-entries", methods=["GET"])
@require_permission("journal.read")
@validate_query(JournalEntrySearchSchema)
def list_journal_entries():
    pagination = request.validated_query
    items, total, pages = get_accounting_service(_business_id()).list_journal_entries(
        pagination,
        status=pagination.status,
        from_date=pagination.from_date,
        to_date=pagination.to_date,
        reference_type=pagination.reference_type,
        reference_id=pagination.reference_id,
    )
    return ok(
        {"journal_entries": [e.to_dict() for e in items]},
        meta={"page": pagination.page, "per_page": pagination.per_page, "total": total, "pages": pages},
    )


@finance_bp.route("/journal-entries", methods=["POST"])
@require_permission("journal.write")
@validate_body(JournalEntryCreateSchema)
def create_journal_entry():
    entry = get_accounting_service(_business_id()).create_journal_entry(request.validated_body, g.ctx.user_id)
    return created({"journal_entry": entry.to_dict()})


@finance_bp.route("/journal-entries/<entry_id>", methods=["GET"])
@require_permission("journal.read")
def get_journal_entry(entry_id):
    entry = get_accounting_service(_business_id()).get_journal_entry(parse_uuid(entry_id))
    return ok({"journal_entry": entry.to_dict()})


@finance_bp.route("/journal-entries/<entry_id>", methods=["PATCH"])
@require_permission("journal.write")
@validate_body(JournalEntryUpdateSchema)
def update_journal_entry(entry_id):
    entry = get_accounting_service(_business_id()).update_journal_entry(parse_uuid(entry_id), request.validated_body, g.ctx.user_id)
    return ok({"journal_entry": entry.to_dict()})


@finance_bp.route("/journal-entries/<entry_id>/post", methods=["POST"])
@require_permission("journal.write")
def post_journal_entry(entry_id):
    entry = get_accounting_service(_business_id()).post_journal_entry(parse_uuid(entry_id), g.ctx.user_id)
    return ok({"journal_entry": entry.to_dict()})


@finance_bp.route("/journal-entries/<entry_id>/reverse", methods=["POST"])
@require_permission("journal.write")
def reverse_journal_entry(entry_id):
    data = request.json or {}
    reversal_date = data.get("reversal_date")
    reason = data.get("reason")
    from datetime import datetime
    rev_date = datetime.fromisoformat(reversal_date) if reversal_date else None
    entry = get_accounting_service(_business_id()).reverse_journal_entry(parse_uuid(entry_id), g.ctx.user_id, rev_date, reason)
    return created({"journal_entry": entry.to_dict()})


@finance_bp.route("/journal-entries/<entry_id>/cancel", methods=["POST"])
@require_permission("journal.write")
def cancel_journal_entry(entry_id):
    entry = get_accounting_service(_business_id()).cancel_journal_entry(parse_uuid(entry_id), g.ctx.user_id)
    return ok({"journal_entry": entry.to_dict()})


# ==================== Tax Configurations ====================

@finance_bp.route("/tax-configs", methods=["GET"])
@require_permission("tax.read")
def list_tax_configs():
    items = get_tax_service(_business_id()).list_tax_configs()
    return ok({"tax_configs": [t.to_dict() for t in items]})


@finance_bp.route("/tax-configs", methods=["POST"])
@require_permission("tax.write")
@validate_body(TaxConfigSchema)
def create_tax_config():
    tax = get_tax_service(_business_id()).create_tax_config(request.validated_body, g.ctx.user_id)
    return created({"tax_config": tax.to_dict()})


@finance_bp.route("/tax-configs/<tax_id>", methods=["GET"])
@require_permission("tax.read")
def get_tax_config(tax_id):
    tax = get_tax_service(_business_id()).get_tax_config(parse_uuid(tax_id))
    return ok({"tax_config": tax.to_dict()})


@finance_bp.route("/tax-configs/<tax_id>", methods=["PATCH"])
@require_permission("tax.write")
@validate_body(TaxConfigUpdateSchema)
def update_tax_config(tax_id):
    tax = get_tax_service(_business_id()).update_tax_config(parse_uuid(tax_id), request.validated_body, g.ctx.user_id)
    return ok({"tax_config": tax.to_dict()})


@finance_bp.route("/tax-configs/<tax_id>", methods=["DELETE"])
@require_permission("tax.write")
def delete_tax_config(tax_id):
    get_tax_service(_business_id()).delete_tax_config(parse_uuid(tax_id))
    return no_content()


# ==================== Customer/Supplier Statements ====================

@finance_bp.route("/customers/<customer_id>/statement", methods=["GET"])
@require_permission("customer.read")
def get_customer_statement(customer_id):
    from_date = request.args.get("from_date")
    to_date = request.args.get("to_date")
    stmt = get_invoice_service(_business_id()).get_customer_statement(parse_uuid(customer_id), from_date, to_date)
    return ok({"statement": stmt})


@finance_bp.route("/customers/<customer_id>/statement/pdf", methods=["GET"])
@require_permission("customer.read")
def get_customer_statement_pdf(customer_id):
    from_date = request.args.get("from_date")
    to_date = request.args.get("to_date")
    customer, invoices, payments = get_invoice_service(_business_id()).get_customer_statement_documents(
        parse_uuid(customer_id), from_date, to_date
    )

    from flask import send_file
    pdf_bytes = get_pdf_service(_business_id()).generate_statement_pdf(
        customer, invoices, payments, g.ctx.business
    )
    return send_file(
        __import__("io").BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"statement-{customer.customer_number or customer.id}.pdf",
    )


@finance_bp.route("/suppliers/<supplier_id>/statement", methods=["GET"])
@require_permission("supplier.read")
def get_supplier_statement(supplier_id):
    from_date = request.args.get("from_date")
    to_date = request.args.get("to_date")
    stmt = get_expense_service(_business_id()).get_supplier_statement(parse_uuid(supplier_id), from_date, to_date)
    return ok({"statement": stmt})


@finance_bp.route("/suppliers/<supplier_id>/statement/pdf", methods=["GET"])
@require_permission("supplier.read")
def get_supplier_statement_pdf(supplier_id):
    from_date = request.args.get("from_date")
    to_date = request.args.get("to_date")
    supplier, expenses, payments = get_expense_service(_business_id()).get_supplier_statement_documents(
        parse_uuid(supplier_id), from_date, to_date
    )

    from flask import send_file
    pdf_bytes = get_pdf_service(_business_id()).generate_party_statement_pdf(
        supplier, expenses, payments, g.ctx.business,
        party_label="Supplier", document_label="Expense", reference_attr="expense_number",
    )
    return send_file(
        __import__("io").BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"statement-{supplier.supplier_number or supplier.id}.pdf",
    )