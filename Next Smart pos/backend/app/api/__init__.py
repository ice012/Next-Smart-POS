from flask import Blueprint, current_app, jsonify
from sqlalchemy import text

from ..extensions import db
# The M-Pesa blueprint lives in its own package rather than in routes/, but is
# mounted here so every /api blueprint is registered in one place.
from ..mpesa.routes import mpesa_bp
from .routes import (
    auth_bp, business_bp, iam_bp, todos_bp, finance_bp,
    customers_bp, suppliers_bp, items_bp, inventory_bp,
    sales_bp, quotations_bp, purchases_bp,
    dashboard_bp, reports_bp, audit_bp, notifications_bp,
    subscription_bp, superadmin_bp, earnings_bp,
)


def register_api(app):
    api = Blueprint("api", __name__, url_prefix="/api")

    @api.route("/health")
    def health():
        payload = {"status": "ok", "service": "next-smart-pos-api"}
        try:
            db.session.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001 - health must never raise
            db.session.rollback()
            current_app.logger.error("Database health check failed: %s", exc)
            payload["status"] = "degraded"
            payload["database"] = f"unavailable: {exc.__class__.__name__}"
            return jsonify(payload), 503
        payload["database"] = "ok"
        return jsonify(payload)

    api.register_blueprint(auth_bp, url_prefix="/auth")
    api.register_blueprint(business_bp, url_prefix="/business")
    api.register_blueprint(iam_bp, url_prefix="/iam")
    api.register_blueprint(todos_bp, url_prefix="/todos")
    api.register_blueprint(finance_bp, url_prefix="/finance")
    api.register_blueprint(customers_bp, url_prefix="/customers")
    api.register_blueprint(suppliers_bp, url_prefix="/suppliers")
    api.register_blueprint(items_bp, url_prefix="/items")
    api.register_blueprint(inventory_bp, url_prefix="/inventory")
    api.register_blueprint(sales_bp, url_prefix="/sales")
    api.register_blueprint(quotations_bp, url_prefix="/quotations")
    api.register_blueprint(purchases_bp, url_prefix="/purchases")
    api.register_blueprint(mpesa_bp, url_prefix="/mpesa")
    api.register_blueprint(dashboard_bp, url_prefix="/dashboard")
    api.register_blueprint(reports_bp, url_prefix="/reports")
    api.register_blueprint(audit_bp, url_prefix="/audit")
    api.register_blueprint(earnings_bp, url_prefix="/earnings")
    api.register_blueprint(notifications_bp, url_prefix="/notifications")
    api.register_blueprint(subscription_bp, url_prefix="/subscription")
    api.register_blueprint(superadmin_bp, url_prefix="/superadmin")

    app.register_blueprint(api)
