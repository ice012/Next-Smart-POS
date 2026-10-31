"""Assert the Alembic chain reproduces the model schema with no drift.

Runs the migration chain against a throwaway SQLite database, then asks Alembic
to autogenerate against it. An empty diff means the migrations are the source
of truth for the schema.
"""

import os
import pathlib
import sys
import tempfile

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("FLASK_APP", "wsgi.py")
# Alembic resolves the directory argument against the process cwd, so pin it to
# the backend root. Otherwise the check aborts with "Path doesn't exist:
# migrations" whenever the script is launched from anywhere else.
os.chdir(BACKEND_DIR)

from alembic.autogenerate import compare_metadata  # noqa: E402
from alembic.migration import MigrationContext  # noqa: E402
from flask_migrate import upgrade  # noqa: E402

from app import create_app  # noqa: E402
from app.config import DevelopmentConfig  # noqa: E402
from app.extensions import db  # noqa: E402

tmp = pathlib.Path(tempfile.gettempdir()) / "nsp_alembic_drift.sqlite"
if tmp.exists():
    tmp.unlink()


class DriftConfig(DevelopmentConfig):
    SQLALCHEMY_DATABASE_URI = "sqlite:///" + str(tmp).replace("\\", "/")


def main():
    app = create_app(DriftConfig)
    with app.app_context():
        upgrade(directory="migrations")
        with db.engine.connect() as connection:
            context = MigrationContext.configure(connection)
            diff = compare_metadata(context, db.metadata)
    if diff:
        print(f"Schema drift detected ({len(diff)} difference(s)):")
        for entry in diff:
            print(" ", entry)
        return 1
    print("No schema drift: migrations match the models")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
