"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-14

Creates the tables that existed at this revision.

The schema is defined once in ``db.metadata`` (SQLAlchemy Core) and reused here,
so there is a single source of truth rather than duplicated DDL. Add future
changes as explicit revisions on top of this one.

This used to call ``metadata.create_all()``, which is a snapshot of the metadata
*as it is now*, not as it was at this revision. Every table added later (agents,
ssh_hosts, sites, action_approvals, settings, settings_events) was therefore
created here and then again by its own revision, so ``alembic upgrade head`` died
on any fresh database with "relation ... already exists" and the containers
crash-looped. The list below is explicit: each table belongs to exactly one
revision, and ``tests/test_migrations.py`` now runs the whole chain from empty.
"""

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

TABLES = (
    "users", "bots", "messages", "channels", "meta_events",
    "jobs", "job_events", "job_artifacts", "business_profiles",
)


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
