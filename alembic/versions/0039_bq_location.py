"""BigQuery dataset location for GA4 exports (migration 0039).

The location is part of the BigQuery query job, not a preference: an EU dataset queried
from the default US location fails. Existing properties start blank and use BigQuery's
default until they are linked or edited.
"""

from alembic import op
import sqlalchemy as sa

revision = "0039_bq_location"
down_revision = "0038_analytics_actions"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in
               sa.inspect(op.get_bind()).get_columns("analytics_properties")}
    if "bigquery_location" not in columns:
        op.add_column(
            "analytics_properties",
            sa.Column("bigquery_location", sa.Text(), nullable=False,
                      server_default=sa.text("''")),
        )


def downgrade():
    columns = {column["name"] for column in
               sa.inspect(op.get_bind()).get_columns("analytics_properties")}
    if "bigquery_location" in columns:
        op.drop_column("analytics_properties", "bigquery_location")
