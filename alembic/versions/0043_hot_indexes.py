"""Hot-path indexes for tenant lists and scheduler sweeps (migration 0043).

These are query-shape indexes, not new state: the tenant list pages order by
``updated_at``/``created_at``, the scheduler asks for a task by org and date, and
``wake_due`` scans waiting runs by status and wake time. Declared in ``db.metadata``
too, so a fresh ``create_all`` and an Alembic upgrade produce the same schema.
"""

from alembic import op

revision = "0043_hot_indexes"
down_revision = "0042_studio_tour"
branch_labels = None
depends_on = None

INDEXES = (
    "idx_jobs_org_created",
    "idx_jobs_task_org_created",
    "idx_deliverables_org_updated",
    "idx_deliverables_job",
    "idx_sites_org",
    "idx_automation_runs_wake",
)


def upgrade():
    op.execute("CREATE INDEX IF NOT EXISTS idx_jobs_org_created "
               "ON jobs (org_id, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_jobs_task_org_created "
               "ON jobs (task, org_id, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_deliverables_org_updated "
               "ON deliverables (org_id, updated_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_deliverables_job "
               "ON deliverables (job_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_sites_org ON sites (org_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_automation_runs_wake "
               "ON automation_runs (status, wake_at)")


def downgrade():
    for name in INDEXES:
        op.execute("DROP INDEX IF EXISTS %s" % name)
