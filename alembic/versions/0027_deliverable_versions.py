"""Draft history: a save cannot destroy the only copy of a page.

One row per content change, written before the change. The live page is not an undo:
when an agent replaces a draft with a report, the previous draft exists nowhere else
unless this table kept it.
"""

from alembic import op
import sqlalchemy as sa

revision = "0027_deliverable_versions"
down_revision = "0026_job_read_only"
branch_labels = None
depends_on = None


def upgrade():
    # checkfirst: 0001 builds tables from the live metadata, so on a fresh database this
    # table already exists. The migration must be a no-op there, not a duplicate create.
    from db import metadata

    metadata.tables["deliverable_versions"].create(bind=op.get_bind(), checkfirst=True)


def downgrade():
    op.drop_table("deliverable_versions")
