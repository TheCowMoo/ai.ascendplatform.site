"""The logged-in Assistant's private session history.

`assistant_sessions` is one conversation per user and acting client;
`assistant_messages` is its transcript, with `job_id` linking each turn to the
manager job that produced it. The public chatbot's `messages` table is untouched.
"""

from alembic import op

revision = "0020_assistant_sessions"
down_revision = "0019_media"
branch_labels = None
depends_on = None


def upgrade():
    from db import metadata

    bind = op.get_bind()
    metadata.tables["assistant_sessions"].create(bind=bind, checkfirst=True)
    metadata.tables["assistant_messages"].create(bind=bind, checkfirst=True)


def downgrade():
    from db import metadata

    bind = op.get_bind()
    metadata.tables["assistant_messages"].drop(bind=bind, checkfirst=True)
    metadata.tables["assistant_sessions"].drop(bind=bind, checkfirst=True)
