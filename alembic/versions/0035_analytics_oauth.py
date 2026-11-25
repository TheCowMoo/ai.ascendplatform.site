"""Google Analytics OAuth connections (migration 0035)."""

from alembic import op

revision = "0035_analytics_oauth"
down_revision = "0034_bug_tracker"
branch_labels = None
depends_on = None


def upgrade():
    from db import metadata

    # Declared once in db.py, so create_all() and Alembic cannot drift.
    metadata.tables["oauth_connections"].create(bind=op.get_bind(), checkfirst=True)


def downgrade():
    op.drop_table("oauth_connections")
