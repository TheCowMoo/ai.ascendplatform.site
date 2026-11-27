"""Analytics setup consent (migration 0041).

The one-click setup flow performs recurring external writes. This table records which
person granted that authority, for which scopes and consent text version, and when it
was revoked. A version bump deliberately requires a fresh grant.
"""

from alembic import op
from sqlalchemy import inspect

revision = "0041_analytics_consents"
down_revision = "0040_analytics_alerts"
branch_labels = None
depends_on = None


def upgrade():
    from db import metadata

    metadata.tables["analytics_consents"].create(bind=op.get_bind(), checkfirst=True)


def downgrade():
    if "analytics_consents" in set(inspect(op.get_bind()).get_table_names()):
        op.drop_table("analytics_consents")
