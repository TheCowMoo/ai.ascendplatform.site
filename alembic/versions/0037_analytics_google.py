"""Google connections and GA4 report facts (migration 0037).

The Google grant becomes a first-class connection: agency scope, encrypted token columns
and the property/container metadata the picker discovers. The new facts table holds the
multi-dimensional reports without changing the legacy daily series.

The provider rename is data, not schema: existing ``google_analytics`` rows become
``google`` so one connection can serve GA4, Tag Manager and BigQuery discovery.
"""

from alembic import op
from sqlalchemy import Column, Text, inspect

revision = "0037_analytics_google"
down_revision = "0036_proposal_triggers"
branch_labels = None
depends_on = None

COLUMNS = (
    ("connection_scope", "client"),
    ("refresh_token_enc", ""),
    ("access_token_enc", ""),
)
TABLES = ("analytics_properties", "tag_containers", "analytics_facts")


def upgrade():
    bind = op.get_bind()
    existing = {column["name"] for column in
                inspect(bind).get_columns("oauth_connections")}
    for name, default in COLUMNS:
        if name not in existing:
            op.add_column("oauth_connections", Column(
                name, Text, nullable=False, server_default=default))
    op.execute("UPDATE oauth_connections SET provider = 'google' "
               "WHERE provider = 'google_analytics'")

    from db import metadata
    for table in TABLES:
        metadata.tables[table].create(bind=bind, checkfirst=True)


def downgrade():
    bind = op.get_bind()
    present = set(inspect(bind).get_table_names())
    for table in reversed(TABLES):
        if table in present:
            op.drop_table(table)
    existing = {column["name"] for column in
                inspect(bind).get_columns("oauth_connections")}
    for name, _ in reversed(COLUMNS):
        if name in existing:
            op.drop_column("oauth_connections", name)
    op.execute("UPDATE oauth_connections SET provider = 'google_analytics' "
               "WHERE provider = 'google'")
