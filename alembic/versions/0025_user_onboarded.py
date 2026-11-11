"""A first login is a first login on every device: users.onboarded_at.

One nullable timestamp. The first-run voice introduction marks it, so clearing a
browser\'s localStorage no longer replays the welcome and a second device does not
repeat it.
"""

from alembic import op
import sqlalchemy as sa

revision = "0025_user_onboarded"
down_revision = "0024_client_preferences"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    present = {col["name"] for col in inspector.get_columns("users")}
    if "onboarded_at" not in present:
        op.add_column("users", sa.Column("onboarded_at", sa.Text(), nullable=True))
        # Anyone who already has an account is not a first-time user: the welcome is for
        # accounts created from now on. Clearing the timestamp replays it for one account.
        op.execute("UPDATE users SET onboarded_at = created_at WHERE onboarded_at IS NULL")


def downgrade():
    op.drop_column("users", "onboarded_at")
