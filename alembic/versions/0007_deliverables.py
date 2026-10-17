"""deliverables: the content a client publishes

Revision ID: 0007_deliverables
Revises: 0006_client_scope
Create Date: 2026-10-17

One table for blog posts, funnel steps and emails - the things the team produces
and the agent publishes onto a client's site. Kept separate from ``sites`` because
a site outlives the content on it, and separate from ``job_artifacts`` because an
artifact is a file a run emitted while a deliverable is the thing that goes live.
"""

from alembic import op

revision = "0007_deliverables"
down_revision = "0006_client_scope"
branch_labels = None
depends_on = None

TABLES = ("deliverables",)


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
