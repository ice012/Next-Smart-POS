"""seed reference data

Creates the global permission catalog plus the default subscription plans and
system modules. The definitions are imported from ``app.seed`` so the
database and the running application cannot drift apart.

Revision ID: c2f7a1b93d45
Revises: 8ae8c6a43f22
Create Date: 2026-09-25

"""

revision = "c2f7a1b93d45"
down_revision = "8ae8c6a43f22"
branch_labels = None
depends_on = None


def upgrade():
    from app.seed import init_default_permissions, seed_platform_defaults
    from app.extensions import db

    init_default_permissions()
    seed_platform_defaults()
    db.session.commit()


def downgrade():
    from app.extensions import db
    from app.models import (
        PERMISSION_CATALOG,
        Permission,
        RolePermission,
        SubscriptionPlan,
        SystemModule,
    )

    names = [row[0] for row in PERMISSION_CATALOG]
    db.session.query(RolePermission).filter(
        RolePermission.permission_id.in_(
            db.session.query(Permission.id).filter(Permission.name.in_(names))
        )
    ).delete(synchronize_session=False)
    db.session.query(Permission).filter(Permission.name.in_(names)).delete(
        synchronize_session=False
    )
    db.session.query(SubscriptionPlan).delete(synchronize_session=False)
    db.session.query(SystemModule).delete(synchronize_session=False)
    db.session.commit()
