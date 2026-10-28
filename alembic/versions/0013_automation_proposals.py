"""Automation proposals: a graph an agent drafted, waiting for a human to apply it.

Kept out of `automations` on purpose. The model can write here as often as it likes; the
only thing that changes a saved automation is `apply`, which re-validates first.
"""

from alembic import op

revision = "0013_automation_proposals"
down_revision = "0012_automations"
branch_labels = None
depends_on = None


def upgrade():
    from db import metadata

    metadata.tables["automation_proposals"].create(bind=op.get_bind(), checkfirst=True)


def downgrade():
    from db import metadata

    metadata.tables["automation_proposals"].drop(bind=op.get_bind(), checkfirst=True)
