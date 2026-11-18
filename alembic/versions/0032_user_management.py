"""User administration, per-user status and the audit trail (migration 0032)."""

import sqlalchemy as sa
from alembic import op

revision = "0032_user_management"
down_revision = "0031_object_mirror"
branch_labels = None
depends_on = None

COLUMNS = (
    ("status", sa.Column("status", sa.Text(), nullable=False,
                         server_default=sa.text("'active'"))),
    ("last_login_at", sa.Column("last_login_at", sa.Text(), nullable=True)),
    ("updated_at", sa.Column("updated_at", sa.Text(), nullable=True)),
)


def upgrade():
    bind = op.get_bind()
    existing = {column["name"] for column in sa.inspect(bind).get_columns("users")}
    for name, column in COLUMNS:
        if name not in existing:
            op.add_column("users", column)

    from db import metadata

    metadata.tables["user_events"].create(bind=bind, checkfirst=True)
    # invitations used the old spelling before the user-management screen landed.
    op.execute("UPDATE invites SET role = 'member' WHERE role = 'client_member'")


def downgrade():
    bind = op.get_bind()
    op.drop_table("user_events")
    existing = {column["name"] for column in sa.inspect(bind).get_columns("users")}
    for name, _column in reversed(COLUMNS):
        if name in existing:
            op.drop_column("users", name)
