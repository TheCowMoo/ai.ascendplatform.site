"""Recommendation batch cache (migration 0044).

One JSON payload per client. The AI-refined wording is cached here so a dashboard
or Studio chip does not spend a model call on every page view; the fingerprint
invalidates the batch when the underlying client facts change.
"""

from alembic import op
import sqlalchemy as sa

revision = "0044_recommendations"
down_revision = "0043_hot_indexes"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if "recommendation_batches" in sa.inspect(bind).get_table_names():
        return
    now_default = (sa.text("(to_char(now(), 'YYYY-MM-DD HH24:MI:SS'))")
                   if bind.dialect.name == "postgresql"
                   else sa.text("(datetime('now'))"))
    op.create_table(
        "recommendation_batches",
        sa.Column("org_id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("source", sa.Text(), nullable=False, server_default=sa.text("'rules'")),
        sa.Column("payload", sa.Text(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("generated_at", sa.Text(), nullable=False, server_default=now_default),
        sa.Column("ai_at", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=now_default),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=now_default),
    )


def downgrade():
    op.drop_table("recommendation_batches")
