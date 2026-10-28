"""The mini CRM: contacts captured by a form, the widget, a webhook or by hand.

GoHighLevel is optional per client, so nothing here depends on it: the columns exist, stay
empty, and the CRM is complete without them.
"""

from alembic import op

revision = "0014_contacts"
down_revision = "0013_automation_proposals"
branch_labels = None
depends_on = None


def upgrade():
    from db import metadata

    metadata.tables["contacts"].create(bind=op.get_bind(), checkfirst=True)


def downgrade():
    from db import metadata

    metadata.tables["contacts"].drop(bind=op.get_bind(), checkfirst=True)
