"""Voice settled when it ends, and provider invoices checked against our own meter.

`voice_sessions` is the token a browser holds for one spoken session: the reservation is
recorded at mint, and the seconds actually used are billed when it closes. A session
that never closes (closed tab) is settled by the scheduler at its reservation.
`usage_reconciliations` stores one row per provider invoice so the operator can see how
far our computed cost drifted from what was actually billed.
"""

from alembic import op

revision = "0022_voice_and_reconciliation"
down_revision = "0021_usage_credits"
branch_labels = None
depends_on = None

TABLES = ("voice_sessions", "usage_reconciliations")


def upgrade():
    from db import metadata

    bind = op.get_bind()
    for name in TABLES:
        metadata.tables[name].create(bind=bind, checkfirst=True)


def downgrade():
    from db import metadata

    bind = op.get_bind()
    for name in reversed(TABLES):
        metadata.tables[name].drop(bind=bind, checkfirst=True)
