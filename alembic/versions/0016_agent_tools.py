"""Agent-written tools: proposed by a model, approved by a human, executed by nothing yet.
"""

from alembic import op

revision = "0016_agent_tools"
down_revision = "0015_email_templates"
branch_labels = None
depends_on = None


def upgrade():
    from db import metadata

    metadata.tables["agent_tools"].create(bind=op.get_bind(), checkfirst=True)


def downgrade():
    from db import metadata

    metadata.tables["agent_tools"].drop(bind=op.get_bind(), checkfirst=True)
