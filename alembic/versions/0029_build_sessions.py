"""Guided builds: a plain-language interview that survives a reload.

The build questions and answers live server-side so the console can resume an unfinished
website, form, blog post or newsletter. The result column records the drafts and links the
build produced, so the review screen can offer the next step without guessing.
"""

from alembic import op

revision = "0029_build_sessions"
down_revision = "0028_site_version_path"
branch_labels = None
depends_on = None


def upgrade():
    # 0001 creates tables from the live metadata, so a fresh database already has this
    # table. checkfirst keeps both paths working.
    from db import metadata

    metadata.tables["build_sessions"].create(bind=op.get_bind(), checkfirst=True)


def downgrade():
    op.drop_table("build_sessions")
