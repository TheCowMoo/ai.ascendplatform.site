"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-14

Creates every table for CMBGrowthAI.

The schema is defined once in ``db.metadata`` (SQLAlchemy Core) and reused here,
so there is a single source of truth rather than duplicated DDL. Add future
changes as explicit revisions on top of this one.
"""

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    from db import metadata

    metadata.create_all(bind=op.get_bind())


def downgrade():
    from db import metadata

    metadata.drop_all(bind=op.get_bind())
