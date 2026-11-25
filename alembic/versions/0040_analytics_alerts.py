"""Analytics intelligence: alert rules and the alerts they produced (migration 0040).

Rules are per client; alerts are deduplicated per rule and ISO week so a daily
evaluation cannot send the same warning seven times.
"""

from alembic import op
from sqlalchemy import inspect

revision = "0040_analytics_alerts"
down_revision = "0039_bq_location"
branch_labels = None
depends_on = None


def upgrade():
    from db import metadata

    bind = op.get_bind()
    metadata.tables["analytics_alert_rules"].create(bind=bind, checkfirst=True)
    metadata.tables["analytics_alerts"].create(bind=bind, checkfirst=True)


def downgrade():
    tables = set(inspect(op.get_bind()).get_table_names())
    if "analytics_alerts" in tables:
        op.drop_table("analytics_alerts")
    if "analytics_alert_rules" in tables:
        op.drop_table("analytics_alert_rules")
