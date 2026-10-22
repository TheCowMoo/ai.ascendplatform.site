"""Automations: a named graph of steps plus its run history.

Steps and layout are JSON because a graph is a graph: normalising nodes and edges into
tables buys nothing here and makes every read a join. Validation lives in
automations.validate_graph, which runs before anything is written.
"""

from alembic import op

revision = "0012_automations"
down_revision = "0011_site_versions"
branch_labels = None
depends_on = None


def upgrade():
    from db import metadata

    # checkfirst: init_db()'s create_all() builds these tables from the same metadata, so
    # whichever starts first must not make the other fail. Creating from db.metadata also
    # means the DDL lives in exactly one place - the columns above are documentation.
    for table in ("automations", "automation_runs"):
        metadata.tables[table].create(bind=op.get_bind(), checkfirst=True)


def downgrade():
    from db import metadata

    for table in ("automation_runs", "automations"):
        metadata.tables[table].drop(bind=op.get_bind())
