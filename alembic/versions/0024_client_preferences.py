"""Client preferences: how the AI should work for one business.

Two text columns on ``business_profiles``, no data movement.

* ``preferences`` - short rules, one per line, that the Manager adds as it learns them
  from the conversation ("keep emails under 150 words"). The owner can edit or delete any
  line in Business Profile; a line the AI wrote is not privileged.
* ``signature`` - the block a written piece ends with.

They are instructions, not business facts, which is why they are not another FAQ row:
every agent turn and every bot reply receives them.
"""

from alembic import op
import sqlalchemy as sa

revision = "0024_client_preferences"
down_revision = "0023_studio_targets"
branch_labels = None
depends_on = None

COLUMNS = ("preferences", "signature")


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    present = {col["name"] for col in inspector.get_columns("business_profiles")}
    for column in COLUMNS:
        # A fresh database is built from db.metadata, which already has both columns; an
        # existing one needs them added. The check makes both paths safe.
        if column not in present:
            op.add_column("business_profiles", sa.Column(
                column, sa.Text(), nullable=False, server_default=sa.text("''")))


def downgrade():
    for column in reversed(COLUMNS):
        op.drop_column("business_profiles", column)