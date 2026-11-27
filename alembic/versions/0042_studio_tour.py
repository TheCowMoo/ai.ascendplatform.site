"""Versioned Studio walkthrough completion (migration 0042).

The first login already has a per-account welcome. The Studio needs its own versioned
completion flag so a new device does not replay it and a new walkthrough can re-arm it.
"""

from alembic import op
import sqlalchemy as sa

revision = "0042_studio_tour"
down_revision = "0041_analytics_consents"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in
               sa.inspect(op.get_bind()).get_columns("users")}
    if "studio_tour_at" not in columns:
        op.add_column("users", sa.Column("studio_tour_at", sa.Text(), nullable=True))
    if "studio_tour_version" not in columns:
        op.add_column(
            "users",
            sa.Column("studio_tour_version", sa.Text(), nullable=False,
                      server_default=sa.text("''")),
        )


def downgrade():
    columns = {column["name"] for column in
               sa.inspect(op.get_bind()).get_columns("users")}
    if "studio_tour_version" in columns:
        op.drop_column("users", "studio_tour_version")
    if "studio_tour_at" in columns:
        op.drop_column("users", "studio_tour_at")
