"""Uploaded media for a client: images, video and PDFs.

The row is the index; the file bytes live under artifacts/media/<org_id>/. The
public URL is the row id plus an unguessable filename, so moving the row's org or
editing its alt text never changes a URL already in an email or a page.
"""

from alembic import op

revision = "0019_media"
down_revision = "0018_forms"
branch_labels = None
depends_on = None


def upgrade():
    from db import metadata

    bind = op.get_bind()
    metadata.tables["media"].create(bind=bind, checkfirst=True)


def downgrade():
    from db import metadata

    bind = op.get_bind()
    metadata.tables["media"].drop(bind=bind, checkfirst=True)
