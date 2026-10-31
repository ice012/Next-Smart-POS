from app import create_app
from app.config import TestingConfig
from alembic.config import Config
from alembic import command
import os

# Autogenerate compares the model registry against a real database, so it needs
# one at the current migration head. The development app.db is deliberately not
# reused: it carries hand-applied edits that no migration records, which would
# show up as spurious changes. A scratch file also avoids fighting the running
# dev server for a lock on the real database.
SCRATCH = "app.db.migration-scratch"


class ScratchConfig(TestingConfig):
    SQLALCHEMY_DATABASE_URI = f"sqlite:///{SCRATCH}"


if os.path.exists(SCRATCH):
    os.remove(SCRATCH)

app = create_app(ScratchConfig)

with app.app_context():
    cfg = Config('migrations/alembic.ini')
    cfg.set_main_option('script_location', 'migrations')
    # The scratch DB path is baked into the migration env from the app config.
    command.upgrade(cfg, 'head')
    command.revision(cfg, autogenerate=True, message='add earnings advance requests')
    # Prove the new revision applies to an already-migrated database.
    command.downgrade(cfg, '-1')
    command.upgrade(cfg, 'head')

if os.path.exists(SCRATCH):
    os.remove(SCRATCH)
