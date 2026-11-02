"""A Studio session remembers its draft, and a job is bound to the draft it edits.

Two nullable columns, no data movement:

* ``jobs.deliverable_id`` - the draft this run (and any child it delegates) must update.
  ``save_deliverable`` falls back to it when the model does not repeat the id, which is
  what stopped a delegated edit from saving a duplicate.
* ``assistant_sessions.deliverable_id`` - the page a conversation is about, so reopening
  the chat puts it back on the canvas.
"""

from alembic import op
import sqlalchemy as sa

revision = "0023_studio_targets"
down_revision = "0022_voice_and_reconciliation"
branch_labels = None
depends_on = None

COLUMNS = (
    ("jobs", "deliverable_id"),
    ("assistant_sessions", "deliverable_id"),
)


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table, column in COLUMNS:
        # A fresh database is created from db.metadata, which already has both columns;
        # an existing one needs them added. checkfirst for both paths.
        present = {col["name"] for col in inspector.get_columns(table)}
        if column not in present:
            op.add_column(table, sa.Column(column, sa.Integer(), nullable=True))


def downgrade():
    for table, column in reversed(COLUMNS):
        op.drop_column(table, column)
