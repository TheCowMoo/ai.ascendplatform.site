"""Delays: a parked run needs its trigger and its wake time.

An automation with three one-day delays is alive for days, so `automation_runs` must be
able to say *what* it is waiting about (the contact that signed up) and *when* to wake.
Both are nullable in effect: existing rows get '{}' and NULL, which reads as "not parked".
"""

from alembic import op
from sqlalchemy import Column, Text

revision = "0017_run_wake"
down_revision = "0016_agent_tools"
branch_labels = None
depends_on = None


def upgrade():
    for column in (Column("context", Text, nullable=False, server_default="{}"),
                   Column("wake_at", Text)):
        op.add_column("automation_runs", column)


def downgrade():
    op.drop_column("automation_runs", "wake_at")
    op.drop_column("automation_runs", "context")
