"""settings and settings_events

Revision ID: 0004_settings
Revises: 0003_sites_ssh
Create Date: 2026-10-16

Adds ``settings`` (operator-managed configuration written from the admin Settings
tab) and ``settings_events`` (the audit trail of those changes - which key moved,
by whom, and when, but never a secret value).
"""

from alembic import op

revision = "0004_settings"
down_revision = "0003_sites_ssh"
branch_labels = None
depends_on = None

TABLES = ("settings", "settings_events")


def upgrade():
    from db import metadata

    bind = op.get_bind()
    for name in TABLES:
        metadata.tables[name].create(bind=bind)


def downgrade():
    from db import metadata

    bind = op.get_bind()
    for name in reversed(TABLES):
        metadata.tables[name].drop(bind=bind)