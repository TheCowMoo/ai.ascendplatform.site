"""servers, sites and the approval gate

Revision ID: 0003_sites_ssh
Revises: 0002_agents
Create Date: 2026-10-14

Adds ``ssh_hosts`` (remote targets), ``sites`` (hosted customer sites) and
``action_approvals`` (the human approval gate for write/exec actions).
"""

from alembic import op

revision = "0003_sites_ssh"
down_revision = "0002_agents"
branch_labels = None
depends_on = None

TABLES = ("ssh_hosts", "sites", "action_approvals")


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
