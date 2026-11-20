"""Password reset tokens (migration 0033)."""

from alembic import op

revision = "0033_password_reset"
down_revision = "0032_user_management"
branch_labels = None
depends_on = None


def upgrade():
    from db import metadata

    # The table is declared once in db.py, so create_all() and Alembic cannot drift.
    metadata.tables["password_resets"].create(bind=op.get_bind(), checkfirst=True)


def downgrade():
    op.drop_table("password_resets")
