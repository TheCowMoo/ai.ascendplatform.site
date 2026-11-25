"""Automation proposals carry their trigger (migration 0036).

A draft used to be steps only, so approving one left the client with a second job:
wiring the trigger by hand. These columns let an approved proposal save a complete,
already-enabled automation.

Both additions are conditional: a fresh database builds ``automation_proposals`` from
``db.metadata`` in revision 0013, and that definition already includes the columns.
"""

from alembic import op
from sqlalchemy import Column, Text, inspect

revision = "0036_automation_proposal_triggers"
down_revision = "0035_analytics_oauth"
branch_labels = None
depends_on = None

COLUMNS = (
    ("trigger_type", "manual"),
    ("trigger_config", "{}"),
)


def upgrade():
    existing = {column["name"] for column in
                inspect(op.get_bind()).get_columns("automation_proposals")}
    for name, default in COLUMNS:
        if name not in existing:
            op.add_column("automation_proposals", Column(
                name, Text, nullable=False, server_default=default))


def downgrade():
    existing = {column["name"] for column in
                inspect(op.get_bind()).get_columns("automation_proposals")}
    for name, _ in reversed(COLUMNS):
        if name in existing:
            op.drop_column("automation_proposals", name)
