"""Internal agency bug tracking (migration 0034)."""

from alembic import op

revision = "0034_bug_tracker"
down_revision = "0033_password_reset"
branch_labels = None
depends_on = None


def upgrade():
    from db import metadata

    # Definitions live in db.py, so create_all() and Alembic cannot drift.
    metadata.tables["bugs"].create(bind=op.get_bind(), checkfirst=True)
    metadata.tables["bug_events"].create(bind=op.get_bind(), checkfirst=True)


def downgrade():
    op.drop_table("bug_events")
    op.drop_table("bugs")
