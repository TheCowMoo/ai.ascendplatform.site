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
    # init_db()'s create_all() builds automation_runs from db.metadata, which already has
    # these columns, and db.py's additive-column pass adds them to existing tables. So the
    # ALTER must be conditional or a fresh database fails with "duplicate column".
    from sqlalchemy import inspect

    existing = {column["name"] for column in inspect(op.get_bind()).get_columns("automation_runs")}
    if "context" not in existing:
        op.add_column("automation_runs", Column("context", Text, nullable=False,
                                                 server_default="{}"))
    if "wake_at" not in existing:
        op.add_column("automation_runs", Column("wake_at", Text))


def downgrade():
    from sqlalchemy import inspect

    existing = {column["name"] for column in inspect(op.get_bind()).get_columns("automation_runs")}
    if "wake_at" in existing:
        op.drop_column("automation_runs", "wake_at")
    if "context" in existing:
        op.drop_column("automation_runs", "context")
