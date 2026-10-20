"""data sources: where a client's numbers come from

Revision ID: 0010_data_sources
Revises: 0009_platform_email
Create Date: 2026-10-21

GHL is always the operator's (one sub-account per client); GA4 and Meta belong to the
client when they have them. ``data_sources`` binds an external account to an org so
every later adapter addresses the right tenant, and ``metrics_daily`` normalises what
they return so a site log, a GA4 report and an ad account answer the same questions.
``sync_runs`` exists because third-party pulls fail quietly: stale numbers look
identical to fresh ones unless the attempt is recorded.
"""

from alembic import op

revision = "0010_data_sources"
down_revision = "0009_platform_email"
branch_labels = None
depends_on = None

TABLES = ("data_sources", "metrics_daily", "sync_runs")


def upgrade():
    from db import metadata

    bind = op.get_bind()
    for name in TABLES:
        metadata.tables[name].create(bind=bind)


def downgrade():
    from db import metadata

    bind = op.get_bind()
    for name in reversed(TABLES):
        metadata.tables[name].drop(bind=bind)
