"""site versions: an undo for every published page

Revision ID: 0011_site_versions
Revises: 0010_data_sources
Create Date: 2026-10-21

The weekly review job edits live pages, so each publish has to leave the previous
content behind. One row per publish, restored in a single call.
"""

from alembic import op

revision = "0011_site_versions"
down_revision = "0010_data_sources"
branch_labels = None
depends_on = None


def upgrade():
    from db import metadata

    metadata.tables["site_versions"].create(bind=op.get_bind())


def downgrade():
    from db import metadata

    metadata.tables["site_versions"].drop(bind=op.get_bind())
