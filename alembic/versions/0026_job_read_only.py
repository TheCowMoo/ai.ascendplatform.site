"""A review job is read-only: jobs.read_only.

One integer flag, default 0. The Manager's chat turn and a delegated team run read it to
withhold write tools, and save_deliverable/publish_page refuse it. This is what stops a
QA or audit request from replacing the page it was asked to inspect.
"""

from alembic import op
import sqlalchemy as sa

revision = "0026_job_read_only"
down_revision = "0025_user_onboarded"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    present = {col["name"] for col in inspector.get_columns("jobs")}
    if "read_only" not in present:
        op.add_column("jobs", sa.Column(
            "read_only", sa.Integer(), nullable=False, server_default=sa.text("0")))


def downgrade():
    op.drop_column("jobs", "read_only")
