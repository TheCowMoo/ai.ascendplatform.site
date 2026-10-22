"""Automations: a named graph of steps plus its run history.

Steps and layout are JSON because a graph is a graph: normalising nodes and edges into
tables buys nothing here and makes every read a join. Validation lives in
automations.validate_graph, which runs before anything is written.
"""

from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "automations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, nullable=False, index=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text, server_default=""),
        sa.Column("enabled", sa.Boolean, server_default=sa.false(), nullable=False),
        sa.Column("trigger_type", sa.String(20), server_default="manual", nullable=False),
        sa.Column("trigger_config", sa.Text, server_default="{}"),
        sa.Column("steps", sa.Text, server_default="[]"),
        sa.Column("layout", sa.Text, server_default="{}"),
        sa.Column("created_by", sa.Integer),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("last_run_at", sa.DateTime),
        sa.Column("next_run_at", sa.DateTime),
    )
    op.create_table(
        "automation_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("automation_id", sa.Integer, sa.ForeignKey("automations.id"),
                  nullable=False, index=True),
        sa.Column("job_id", sa.Integer, index=True),
        sa.Column("status", sa.String(20), server_default="queued", nullable=False),
        sa.Column("resume_at", sa.String(40)),
        sa.Column("started_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime),
        sa.Column("error", sa.Text),
    )


def downgrade():
    op.drop_table("automation_runs")
    op.drop_table("automations")
