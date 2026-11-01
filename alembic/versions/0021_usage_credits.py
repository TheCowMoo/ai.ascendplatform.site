"""What each call cost: usage events, the credit ledger, balances and the rate card.

`usage_events` stores the provider's token split (cached input separated from
uncached, cache writes, output) because providers price those differently.
`credit_ledger` is append-only money; `credit_balances` caches its sum so a
per-call check is one row read. `rate_card` holds dollars, copied from the
provider dashboard - the credits each call cost are snapshotted on the event, so
editing a rate never rewrites history.
"""

from alembic import op

revision = "0021_usage_credits"
down_revision = "0020_assistant_sessions"
branch_labels = None
depends_on = None

TABLES = ("usage_events", "credit_ledger", "credit_balances", "rate_card")


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
