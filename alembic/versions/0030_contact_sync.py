"""Two-way contact sync state and the fields the mirror needs.

`contacts` gains the last agreed snapshot, arbitrary custom fields, and the remote
modified timestamp. `contact_sync_state` records the workspace-level last run so the
Contacts banner can be honest even when there are no contacts.
"""

import sqlalchemy as sa
from alembic import op

revision = "0030_contact_sync"
down_revision = "0029_build_sessions"
branch_labels = None
depends_on = None

COLUMNS = (
    ("custom_fields", sa.Column("custom_fields", sa.Text(), nullable=False,
                               server_default=sa.text("'{}'"))),
    ("ghl_remote_updated_at", sa.Column("ghl_remote_updated_at", sa.Text(), nullable=True)),
    ("ghl_snapshot", sa.Column("ghl_snapshot", sa.Text(), nullable=False,
                              server_default=sa.text("'{}'"))),
)


def upgrade():
    bind = op.get_bind()
    existing = {column["name"] for column in sa.inspect(bind).get_columns("contacts")}
    for name, column in COLUMNS:
        if name not in existing:
            op.add_column("contacts", column)

    from db import metadata

    metadata.tables["contact_sync_state"].create(bind=bind, checkfirst=True)


def downgrade():
    bind = op.get_bind()
    op.drop_table("contact_sync_state")
    existing = {column["name"] for column in sa.inspect(bind).get_columns("contacts")}
    for name, _column in reversed(COLUMNS):
        if name in existing:
            op.drop_column("contacts", name)
