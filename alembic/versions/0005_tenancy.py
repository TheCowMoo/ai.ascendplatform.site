"""tenancy: orgs, org_id scoping and the three-role split

Revision ID: 0005_tenancy
Revises: 0004_settings
Create Date: 2026-10-17

Introduces clients-as-tenants. Adds ``orgs``, an ``org_id`` on every table that
belongs to a client, and moves the Business Profile's unique key from the creating
user to the org (the brand belongs to the client, not to a login).

Existing rows: every ``admin`` becomes ``owner`` (the platform operator), and any
non-owner user is attached to a default org so nothing ends up unreachable.

Note this is the first revision that *alters* tables rather than creating them, so
``tests/test_migrations.py`` upgrades a populated 0004 database as well as an empty
one - adding columns and backfilling is where the earlier create/drop bugs hid.
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_tenancy"
down_revision = "0004_settings"
branch_labels = None
depends_on = None

# (table, column, ddl) - added only when missing, so the revision is re-runnable
# against a database that db._ensure_columns() already touched.
COLUMNS = (
    ("users", "org_id", "org_id INTEGER"),
    ("bots", "org_id", "org_id INTEGER"),
    ("sites", "org_id", "org_id INTEGER"),
    ("jobs", "org_id", "org_id INTEGER"),
    ("business_profiles", "org_id", "org_id INTEGER"),
)

# Where each table records who created a row, so the backfill can follow it back
# to that user's org. Note jobs uses requester_id, not owner_id.
CREATOR_COLUMNS = (
    ("bots", "owner_id"),
    ("sites", "owner_id"),
    ("jobs", "requester_id"),
    ("business_profiles", "owner_id"),
)

PROFILE_ORG_INDEX = "uq_business_profiles_org"


def upgrade():
    from db import metadata

    bind = op.get_bind()
    metadata.tables["orgs"].create(bind=bind)

    inspector = sa.inspect(bind)
    for table, column, ddl in COLUMNS:
        existing = {c["name"] for c in inspector.get_columns(table)}
        if column not in existing:
            op.execute("ALTER TABLE %s ADD COLUMN %s" % (table, ddl))

    # The profile's unique key moves from the creator to the tenant.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS %s ON business_profiles (org_id)"
        % PROFILE_ORG_INDEX
    )

    # Platform operators become 'owner'; they belong to no org and see everything.
    op.execute("UPDATE users SET role = 'owner' WHERE role = 'admin'")

    # Anything left is a client, so give it an org and hang the existing rows off
    # it - otherwise client data from before this revision becomes unreachable.
    # A no-op on a fresh install.
    remaining = bind.execute(
        sa.text("SELECT COUNT(*) FROM users WHERE role <> 'owner'")
    ).scalar()
    if not remaining:
        return

    if not bind.execute(
        sa.text("SELECT COUNT(*) FROM orgs WHERE slug = 'default'")
    ).scalar():
        op.execute(
            "INSERT INTO orgs (name, slug, plan, status) "
            "VALUES ('Default', 'default', 'standard', 'active')"
        )
    org_id = bind.execute(
        sa.text("SELECT id FROM orgs WHERE slug = 'default'")
    ).scalar()

    bind.execute(
        sa.text("UPDATE users SET org_id = :org WHERE role <> 'owner' AND org_id IS NULL"),
        {"org": org_id},
    )
    for table, creator in CREATOR_COLUMNS:
        bind.execute(
            sa.text(
                "UPDATE {table} SET org_id = "
                "(SELECT u.org_id FROM users u WHERE u.id = {table}.{creator}) "
                "WHERE org_id IS NULL".format(table=table, creator=creator)
            )
        )


def downgrade():
    from db import metadata

    bind = op.get_bind()
    # The index has to go first, and SQLite cannot DROP COLUMN at all while the
    # column carries a UNIQUE constraint (0001 builds business_profiles from the
    # current metadata, so org_id is declared unique inline). batch_alter_table
    # does the table rebuild that SQLite needs, and a plain ALTER on PostgreSQL.
    op.execute("DROP INDEX IF EXISTS %s" % PROFILE_ORG_INDEX)
    for table, column, _ddl in reversed(COLUMNS):
        existing = {c["name"] for c in sa.inspect(bind).get_columns(table)}
        if column in existing:
            with op.batch_alter_table(table) as batch:
                batch.drop_column(column)
    metadata.tables["orgs"].drop(bind=bind)
