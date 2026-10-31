import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = Path(os.environ["ENV_FILE"]) if os.environ.get("ENV_FILE") else BASE_DIR / ".env"

# Loaded before the config classes below are evaluated: every setting is read
# from os.environ at class-definition time, so a later load_dotenv() would be
# too late to influence them.
if ENV_FILE.is_file():
    load_dotenv(ENV_FILE, override=False)


def _split_csv(value, default):
    if not value:
        return default
    return [v.strip() for v in value.split(",") if v.strip()]


def _database_url() -> str:
    """Resolve DATABASE_URL, anchoring relative SQLite paths to BASE_DIR.

    A bare ``sqlite:///app.db`` would otherwise be interpreted relative to the
    Flask instance folder, silently pointing the API at an empty database.
    """
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        return f"sqlite:///{(BASE_DIR / 'app.db').as_posix()}"

    prefix = "sqlite:///"
    if url.startswith(prefix):
        raw = url[len(prefix):]
        if raw and raw != ":memory:" and not Path(raw).is_absolute():
            return prefix + (BASE_DIR / raw).as_posix()
    return url


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"echo": os.environ.get("SQL_ECHO", "0") == "1"}

    _DATABASE_URL = _database_url()
    SQLALCHEMY_DATABASE_URI = _DATABASE_URL

    JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "dev-jwt-secret-change-me")
    JWT_ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(
        minutes=float(os.environ.get("JWT_ACCESS_MINUTES", "15"))
    )
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(
        days=float(os.environ.get("JWT_REFRESH_DAYS", "7"))
    )

    CORS_ORIGINS = _split_csv(
        os.environ.get("CORS_ORIGINS"),
        ["http://localhost:5173", "http://127.0.0.1:5173"],
    )

    RATELIMIT_ENABLED = os.environ.get("RATELIMIT_ENABLED", "1") == "1"
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")
    AUTH_RATELIMIT = os.environ.get("AUTH_RATELIMIT", "30 per minute")
    REGISTER_RATELIMIT = os.environ.get("REGISTER_RATELIMIT", "10 per minute")
    LOGIN_RATELIMIT = os.environ.get("LOGIN_RATELIMIT", "20 per minute")
    REFRESH_RATELIMIT = os.environ.get("REFRESH_RATELIMIT", "30 per minute")
    FORGOT_PASSWORD_RATELIMIT = os.environ.get(
        "FORGOT_PASSWORD_RATELIMIT", "10 per minute"
    )
    # Guarded separately from sign-in: this endpoint is where a lesser role
    # exchanges a manager's password for a delete authorization, so it is a
    # password oracle if it is not throttled as hard as the login form.
    PRIVILEGED_AUTH_RATELIMIT = os.environ.get(
        "PRIVILEGED_AUTH_RATELIMIT", "10 per minute"
    )

    PASSWORD_MIN_LENGTH = int(os.environ.get("PASSWORD_MIN_LENGTH", "8"))
    PASSWORD_MAX_LENGTH = int(os.environ.get("PASSWORD_MAX_LENGTH", "128"))

    # User-supplied files (business logos) are stored outside the package and
    # served read-only through the /uploads endpoint.
    UPLOAD_FOLDER = os.environ.get(
        "UPLOAD_FOLDER", str(BASE_DIR / "uploads")
    )
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH", str(8 * 1024 * 1024)))
    ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
    LOGO_MAX_BYTES = int(os.environ.get("LOGO_MAX_BYTES", str(2 * 1024 * 1024)))
    IMAGE_MAX_BYTES = int(os.environ.get("IMAGE_MAX_BYTES", str(2 * 1024 * 1024)))

    # --- Super Admin (cross-tenant) access ---
    # Required to enable /api/superadmin routes. Set to a strong secret in prod.
    SUPERADMIN_API_TOKEN = os.environ.get("SUPERADMIN_API_TOKEN")
    SUPERADMIN_ALLOWED_IPS = _split_csv(os.environ.get("SUPERADMIN_ALLOWED_IPS"), [])

    # --- M-Pesa (Safaricom Daraja) ---
    # Per-business credentials live in business_configs.extra_settings["mpesa"].
    # These are the cross-cutting settings for talking to Daraja itself.
    MPESA_HTTP_TIMEOUT = int(os.environ.get("MPESA_HTTP_TIMEOUT", "30"))
    # Daraja's callback is an unauthenticated POST that creates sales, so when
    # this is set the callback route only accepts requests carrying ?token=...
    # Append the same value to the callback URL registered with Safaricom.
    MPESA_CALLBACK_TOKEN = os.environ.get("MPESA_CALLBACK_TOKEN")
    # Used to normalise a locally formatted MSISDN (0712...) to the
    # international form Safaricom expects (254712...).
    MPESA_DEFAULT_COUNTRY_CODE = os.environ.get("MPESA_DEFAULT_COUNTRY_CODE", "254")
    # Daraja access tokens last an hour; refresh this many seconds early.
    MPESA_TOKEN_EXPIRY_MARGIN = int(os.environ.get("MPESA_TOKEN_EXPIRY_MARGIN", "60"))


class DevelopmentConfig(Config):
    DEBUG = True
    # The Werkzeug interactive debugger answers with its own HTML page, which
    # bypasses after_request handlers (and therefore CORS headers). Browsers
    # then report "blocked by CORS policy" instead of the real server error.
    INTERACTIVE_DEBUGGER = os.environ.get("INTERACTIVE_DEBUGGER", "0") == "1"
    SUPERADMIN_API_TOKEN = os.environ.get("SUPERADMIN_API_TOKEN", "dev-superadmin-token")


class ProductionConfig(Config):
    DEBUG = False
    INTERACTIVE_DEBUGGER = False
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True


class TestingConfig(Config):
    TESTING = True
    INTERACTIVE_DEBUGGER = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {"echo": False}
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(minutes=15)
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(days=7)
    RATELIMIT_ENABLED = False
    RATELIMIT_STORAGE_URI = "memory://"
    SUPERADMIN_API_TOKEN = "test-superadmin-token"
