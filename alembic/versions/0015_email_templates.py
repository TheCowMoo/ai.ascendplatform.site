"""Reusable email templates.

A send step points at a template id rather than carrying a whole email in its config, so
wording can change without re-approving an automation, and the same email is not written
twice in two graphs.
"""

from alembic import op

revision = "0015_email_templates"
down_revision = "0014_contacts"
branch_labels = None
depends_on = None


def upgrade():
    from db import metadata

    metadata.tables["email_templates"].create(bind=op.get_bind(), checkfirst=True)


def downgrade():
    from db import metadata

    metadata.tables["email_templates"].drop(bind=op.get_bind(), checkfirst=True)
