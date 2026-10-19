"""agents: one row per name

Revision ID: 0008_agents_unique
Revises: 0007_deliverables
Create Date: 2026-10-20

``ensure_roster()`` seeds the default roster at import time, and the app serves with
``uvicorn --workers 2``. On a fresh database both workers read ``COUNT(*) = 0`` at the
same instant and both inserted the whole roster, and nothing prevented it: ``agents.name``
had no unique constraint. A deploy therefore came up with every agent twice, and every
team build died inside AutoGen with "The participant names must be unique." from
``SelectorGroupChat`` - after all seven model clients had been built, so the error
pointed at the wrong layer.

This removes the duplicates and makes a repeat impossible. ``db.seed_lock()`` stops the
race; the index created here means it cannot corrupt the table even if the lock is
missed.
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_agents_unique"
down_revision = "0007_deliverables"
branch_labels = None
depends_on = None

INDEX = "uq_agents_name"

# A seeded copy is identical in every field. Deleting one is provably lossless: the
# lower-id row keeps the same name, provider, prompt and tool list.
_DELETE_EXACT_COPIES = """
DELETE FROM agents
WHERE EXISTS (
    SELECT 1 FROM agents AS keeper
    WHERE keeper.name = agents.name
      AND keeper.id < agents.id
      AND keeper.role = agents.role
      AND keeper.provider = agents.provider
      AND keeper.model = agents.model
      AND keeper.system_prompt = agents.system_prompt
      AND keeper.tools = agents.tools
)
"""

# Anything still duplicated differs in content, so it was written by hand: keep the
# lowest id under the original name and suffix the rest rather than losing an agent.
_RENAME_OTHERS = """
UPDATE agents
SET name = name || ' (' || CAST(id AS TEXT) || ')'
WHERE id NOT IN (SELECT MIN(id) FROM agents GROUP BY name)
"""


def _duplicate_names(bind):
    rows = bind.execute(
        sa.text("SELECT name, COUNT(*) AS n FROM agents GROUP BY name HAVING COUNT(*) > 1")
    ).fetchall()
    return [(row[0], row[1]) for row in rows]


def upgrade():
    bind = op.get_bind()

    removed = bind.execute(sa.text(_DELETE_EXACT_COPIES)).rowcount or 0
    renamed = bind.execute(sa.text(_RENAME_OTHERS)).rowcount or 0

    clashes = _duplicate_names(bind)
    if clashes:
        # A hand-typed "Operator (8)" can collide with the rename above. Stop with
        # something actionable instead of an opaque "could not create unique index".
        raise RuntimeError(
            "agents still share a name after the rename: %s. Rename or delete them, "
            "then run 'alembic upgrade head' again." % clashes
        )

    bind.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS %s ON agents (name)" % INDEX))

    if removed or renamed:
        print(
            "  agents: removed %d duplicated copy(ies), renamed %d conflicting row(s)"
            % (removed, renamed)
        )


def downgrade():
    bind = op.get_bind()
    bind.execute(sa.text("DROP INDEX IF EXISTS %s" % INDEX))
