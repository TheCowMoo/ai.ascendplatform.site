"""Workspace-object mirror mappings (Phase 2).

Custom-object records are external; the local row needs to remember which remote record
it owns so a rerun updates instead of duplicating. The hash skips unchanged records.
"""

import sqlalchemy as sa
from alembic import op

revision = "0031_object_mirror"
down_revision = "0030_contact_sync"
branch_labels = None
depends_on = None


def upgrade():
    from db import metadata

    metadata.tables["ghl_object_links"].create(bind=op.get_bind(), checkfirst=True)


def downgrade():
    op.drop_table("ghl_object_links")
