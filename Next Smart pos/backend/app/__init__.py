import logging
import os

import click
from pathlib import Path

from flask import Flask, send_from_directory

from .config import Config, DevelopmentConfig, ProductionConfig, TestingConfig
from .errors import register_error_handlers
from .extensions import cors, db, limiter, migrate
from .api import register_api
from .seed import init_default_permissions


def _configure_logging(app):
    if not app.config.get("TESTING"):
        logging.basicConfig(level=logging.INFO)
    return logging.getLogger("nsp")


def _log_database_target(app):
    """Make the resolved database explicit at startup.

    A wrong SQLite path only surfaces later as an OperationalError on the first
    query, which is easy to misread as an unrelated failure.
    """
    if app.config.get("TESTING"):
        return
    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    logger = logging.getLogger("nsp")
    logger.info("Database target: %s", uri)
    prefix = "sqlite:///"
    if uri.startswith(prefix):
        raw = uri[len(prefix):]
        if raw and raw != ":memory:":
            path = Path(raw)
            if not path.exists():
                logger.warning(
                    "SQLite database file is missing: %s (create it with: flask db upgrade)",
                    path,
                )


def create_app(config_class=None):
    if config_class is None:
        env = os.environ.get("FLASK_ENV", "development").lower()
        config_class = {
            "production": ProductionConfig,
            "testing": TestingConfig,
        }.get(env, DevelopmentConfig)

    app = Flask(__name__)
    app.config.from_object(config_class)
    # .env is loaded by app.config at import time, before the config classes
    # read the environment.
    _configure_logging(app)

    if not app.config.get("INTERACTIVE_DEBUGGER", False):
        # Keep error responses on the normal Flask path so they are rendered as
        # JSON by the registered handlers and still receive CORS headers.
        # Tracebacks are logged by the 500 handler instead of being swallowed
        # by the debugger page.
        app.debug = False
        app.config["PROPAGATE_EXCEPTIONS"] = False

    cors.init_app(app, origins=app.config["CORS_ORIGINS"], supports_credentials=True)
    db.init_app(app)
    migrate.init_app(app, db)
    if app.config.get("RATELIMIT_ENABLED") and app.config.get("TESTING") is not True:
        limiter.init_app(app)

    register_error_handlers(app)
    register_api(app)
    _log_database_target(app)

    upload_folder = app.config["UPLOAD_FOLDER"]
    Path(upload_folder).mkdir(parents=True, exist_ok=True)

    @app.route("/uploads/<path:filename>")
    def uploaded_file(filename):
        # Business logos are rendered in an <img>, so the response must be a
        # real image rather than an attachment.
        return send_from_directory(upload_folder, filename, max_age=86400)

    @app.after_request
    def _set_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-XSS-Protection"] = "0"
        if app.config.get("SESSION_COOKIE_SECURE"):
            response.headers["Strict-Transport-Security"] = (
                "max-age=63072000; includeSubDomains"
            )
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; frame-ancestors 'none'"
            )
        return response

    @app.shell_context_processor
    def shell_context():
        from . import models
        return {"db": db, "models": models}

    @app.cli.command("create-superadmin")
    @click.option("--email", required=True, help="Superadmin email address.")
    @click.option("--password", required=True, help="Superadmin password.")
    @click.option("--first-name", default="Super", help="First name.")
    @click.option("--last-name", default="Admin", help="Last name.")
    @click.option("--username", default=None, help="Optional sign-in username.")
    @click.option("--pin", default=None, help="Optional 4-6 digit sign-in PIN.")
    def create_superadmin(email, password, first_name, last_name, username, pin):
        """Create or update the platform superadmin user."""
        from .seed import create_superadmin_user

        with app.app_context():
            user = create_superadmin_user(
                email,
                password,
                first_name,
                last_name,
                username=username,
                pin=pin,
            )
            db.session.commit()
            print(f"Superadmin ready: {user.email} (id: {user.id})")
            if user.username:
                print(f"  username: {user.username}")
            print(f"  pin set: {'yes' if user.pin_hash else 'no'}")


    @app.cli.command("sync-roles")
    def sync_roles():
        """Backfill newly-added system roles and permissions onto every business."""
        from .seed import sync_system_roles_for_all_businesses

        with app.app_context():
            count = sync_system_roles_for_all_businesses()
            print(f"Synced system roles across {count} business(es).")

    @app.cli.command("reset-db")
    def reset_db():
        from .seed import reset_db as reset_database
        reset_database(app)

    @app.cli.command("seed")
    def seed_cmd():
        from .seed import seed_database
        seed_database(app)

    return app
