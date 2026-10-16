"""client settings and the platform's own Business Profile

Revision ID: 0006_client_scope
Revises: 0005_tenancy
Create Date: 2026-10-17

Two things a client owns that used to be platform-wide:

* ``client_settings`` - per-client overrides for settings whose scope is "client"
  (the SMTP credentials a client sends mail with). Additive: ``settings`` keeps its
  key-name primary key, so no credentials table is rebuilt.
* The platform's own Business Profile moves to org 0. Before multi-tenancy every
  profile belonged to the user who created it; the earliest one is the operator's,
  and org 0 is where ``profile.get_profile`` looks for it.
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_client_scope"
down_revision = "0005_tenancy"
branch_labels = None
depends_on = None

# The platform's own bucket. Not NULL: a unique index allows any number of NULLs,
# so NULL could not mean "the one platform profile" - it would silently permit
# duplicates. Org ids start at 1, so 0 is otherwise unused.
PLATFORM_ORG = 0


def upgrade():
    from db import _drop_legacy_owner_unique, metadata

    bind = op.get_bind()
    metadata.tables["client_settings"].create(bind=bind)

    # An upgraded database still carries 0001's UNIQUE(owner_id) inside the column
    # definition. A profile belongs to the org now, so that constraint has to go -
    # and it is removed here rather than in 0005 because it only starts to hurt once
    # profiles are looked up by org. Idempotent, and a no-op on a fresh database.
    _drop_legacy_owner_unique(bind)

    # The earliest pre-tenancy profile is the operator's. Others keep a NULL org and
    # stay reachable through the user that created them, which is the most a
    # migration can safely assume about data it cannot interpret. A no-op on a fresh
    # install and on any deployment that has already run.
    op.execute(
        "UPDATE business_profiles SET org_id = %d "
        "WHERE org_id IS NULL AND id = ("
        "  SELECT MIN(id) FROM business_profiles WHERE org_id IS NULL)"
        % PLATFORM_ORG
    )


def downgrade():
    from db import metadata

    bind = op.get_bind()
    # Leaving org_id set would make an older revision read a profile it cannot see
    # (it looks profiles up by owner_id), so put the platform profile back.
    op.execute(
        "UPDATE business_profiles SET org_id = NULL WHERE org_id = %d" % PLATFORM_ORG
    )
    metadata.tables["client_settings"].drop(bind=bind)
