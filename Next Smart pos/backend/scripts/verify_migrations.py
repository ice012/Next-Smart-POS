"""Verify the Alembic chain can build, seed and tear down a fresh database."""

import pathlib
import sys
import tempfile

import sqlalchemy as sa

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from flask_migrate import downgrade, upgrade  # noqa: E402

from app import create_app  # noqa: E402
from app.config import DevelopmentConfig  # noqa: E402
from app.extensions import db  # noqa: E402

tmp = pathlib.Path(tempfile.gettempdir()) / "nsp_alembic_verify.sqlite"
if tmp.exists():
    tmp.unlink()


class VerifyConfig(DevelopmentConfig):
    SQLALCHEMY_DATABASE_URI = "sqlite:///" + str(tmp).replace("\\", "/")


def scalar(sql):
    return db.session.execute(sa.text(sql)).scalar()


app = create_app(VerifyConfig)
with app.app_context():
    upgrade(directory="migrations")
    print("tables", scalar("SELECT count(*) FROM sqlite_master WHERE type='table'"))
    print("permissions", scalar("SELECT count(*) FROM permissions"))
    print("plans", scalar("SELECT count(*) FROM subscription_plans"))
    print("modules", scalar("SELECT count(*) FROM system_modules"))
    print("alembic head", scalar("SELECT version_num FROM alembic_version"))
    downgrade(directory="migrations", revision="base")
    print("tables after downgrade", scalar("SELECT count(*) FROM sqlite_master WHERE type='table'"))
print("OK")
