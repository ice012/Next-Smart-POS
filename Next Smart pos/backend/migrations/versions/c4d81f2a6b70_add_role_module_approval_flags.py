"""add role module approval flags, and the notification permissions behind them

Two things, because they are the same change seen from two ends.

1. ``roles.requires_owner_approval`` records which modules an owner has put
   behind their password. An empty object is the same thing as never having
   been configured, and both must behave the way roles behaved before.

2. The Notifications module needs permissions of its own to be switchable from
   the Roles screen. Those routes were previously permission-free, so the
   permissions are new to the catalog — which means every business registered
   before this migration has roles that do not hold them. Enforcing
   ``notification.read`` without backfilling would 403 the inbox for every
   existing staff member immediately after deploy, which reads as "the app is
   broken after upgrade" rather than as a missing grant.

So the new permissions are seeded and backfilled here, onto existing system
roles only. Custom roles are left alone: their permission set is an operator's
decision, and adding rows to it behind their back is the same class of surprise
as force-restoring a permission they deliberately removed.

Revision ID: c4d81f2a6b70
Revises: 86c0bd00b575
Create Date: 2026-09-30

"""

import uuid as uuidlib

import sqlalchemy as sa
from alembic import op

revision = "c4d81f2a6b70"
down_revision = "86c0bd00b575"
branch_labels = None
depends_on = None

NEW_PERMISSIONS = [
    (
        "notification.read",
        "notification",
        "read",
        "View the in-app notification inbox",
    ),
    (
        "notification.write",
        "notification",
        "write",
        "Raise notifications and run the alert checks",
    ),
]

# Only roles that can actually use an inbox. `notification.write` stays with
# owner and admin: raising low-stock and expense-reminder alerts is theirs.
SYSTEM_ROLES_GAINING_READ = [
    "Business Owner",
    "Admin",
    "Accountant",
    "Employee",
    "Cashier",
]
SYSTEM_ROLES_GAINING_WRITE = ["Business Owner", "Admin"]


def upgrade():
    with op.batch_alter_table("roles", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "requires_owner_approval",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )

    connection = op.get_bind()
    existing = {
        row[0]
        for row in connection.execute(sa.text("SELECT name FROM permissions")).fetchall()
    }
    for name, resource, action, description in NEW_PERMISSIONS:
        if name in existing:
            continue
        connection.execute(
            sa.text(
                "INSERT INTO permissions"
                " (id, name, resource, action, description, is_active)"
                " VALUES (:id, :name, :resource, :action, :description, 1)"
            ),
            {
                "id": uuidlib.uuid4().hex,
                "name": name,
                "resource": resource,
                "action": action,
                "description": description,
            },
        )

    wanted = {name: name for name, *_rest in NEW_PERMISSIONS}
    for role_name in SYSTEM_ROLES_GAINING_READ:
        wanted_for_role = {"notification.read"}
        if role_name in SYSTEM_ROLES_GAINING_WRITE:
            wanted_for_role.add("notification.write")
        _grant(connection, role_name, wanted, wanted_for_role)


def _grant(connection, role_name, wanted, names):
    """Give the named permissions to system roles called *role_name*.

    Idempotent, and scoped to `is_system` rows so an operator's custom role is
    never touched by a schema migration.
    """
    role_rows = connection.execute(
        sa.text("SELECT id FROM roles WHERE name = :name AND is_system = 1"),
        {"name": role_name},
    ).fetchall()
    for (role_id,) in role_rows:
        for permission in names:
            perm_row = connection.execute(
                sa.text("SELECT id FROM permissions WHERE name = :name"),
                {"name": wanted[permission]},
            ).fetchone()
            if perm_row is None:
                continue
            already = connection.execute(
                sa.text(
                    "SELECT 1 FROM role_permissions"
                    " WHERE role_id = :role_id AND permission_id = :permission_id"
                ),
                {"role_id": role_id, "permission_id": perm_row[0]},
            ).fetchone()
            if already:
                continue
            connection.execute(
                sa.text(
                    "INSERT INTO role_permissions"
                    " (id, role_id, permission_id, is_active)"
                    " VALUES (:id, :role_id, :permission_id, 1)"
                ),
                {
                    "id": uuidlib.uuid4().hex,
                    "role_id": role_id,
                    "permission_id": perm_row[0],
                },
            )


def downgrade():
    with op.batch_alter_table("roles", schema=None) as batch_op:
        batch_op.drop_column("requires_owner_approval")