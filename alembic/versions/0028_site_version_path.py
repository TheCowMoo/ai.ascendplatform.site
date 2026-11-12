"""An undo for every page, not only the home page: site_versions.path.

Publishing about.html used to record the home page's content. Restoring that snapshot
put the home page back and left about.html exactly as broken as it was. The path column
makes the snapshot describe the file it actually came from.
"""

from alembic import op
import sqlalchemy as sa

revision = "0028_site_version_path"
down_revision = "0027_deliverable_versions"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    present = {col["name"] for col in inspector.get_columns("site_versions")}
    if "path" not in present:
        op.add_column("site_versions", sa.Column(
            "path", sa.Text(), nullable=False, server_default=sa.text("'index.html'")))
        op.execute("UPDATE site_versions SET path = 'index.html' WHERE path IS NULL OR path = ''")


def downgrade():
    op.drop_column("site_versions", "path")
