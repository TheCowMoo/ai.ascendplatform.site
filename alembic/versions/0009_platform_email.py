"""platform email: the platform's own sender record and invitations

Revision ID: 0009_platform_email
Revises: 0008_agents_unique
Create Date: 2026-10-20

Two tables, both for the operator rather than for clients:

``mail_senders`` remembers who the platform sends as. GoHighLevel is the only place a
from-address can be verified, so this is a record plus the custom-field values we stamp
onto the contacts an invitation creates.

``invites`` holds a single-use, expiring token (as a hash) for someone who has been
invited to the platform. The user row itself is created at invite time with an
unusable password, so an invite cannot collide with an existing email or land in the
wrong org; accepting simply sets the real password.
"""

from alembic import op

revision = "0009_platform_email"
down_revision = "0008_agents_unique"
branch_labels = None
depends_on = None

TABLES = ("mail_senders", "invites")


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
