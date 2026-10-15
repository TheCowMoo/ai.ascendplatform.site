"""agents roster

Revision ID: 0002_agents
Revises: 0001_initial
Create Date: 2026-10-14

Adds the ``agents`` table (designatable agent roster). Reuses the SQLAlchemy
Table object from ``db`` so the definition stays in one place.
"""

from alembic import op

revision = "0002_agents"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    from db import agents

    agents.create(bind=op.get_bind())


def downgrade():
    from db import agents

    agents.drop(bind=op.get_bind())
