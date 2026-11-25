"""Analytics write actions (migration 0038).

GA4/GTM setup and tag installations are live writes. This table records who asked, what
was sent, and what Google or the site publisher returned.
"""

from alembic import op
from sqlalchemy import inspect

revision = "0038_analytics_actions"
down_revision = "0037_analytics_google"
branch_labels = None
depends_on = None


def upgrade():
    from db import metadata

    metadata.tables["analytics_actions"].create(bind=op.get_bind(), checkfirst=True)


def downgrade():
    if "analytics_actions" in set(inspect(op.get_bind()).get_table_names()):
        op.drop_table("analytics_actions")
