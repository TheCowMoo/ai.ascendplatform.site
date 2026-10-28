"""Forms, surveys and quizzes - definitions and the answers they collect.

The questions are a JSON document rather than rows: a form is edited as a whole, its
branching rules point at question ids inside that document, and a submission is validated
against exactly the version the visitor was shown.
"""

from alembic import op

revision = "0018_forms"
down_revision = "0017_run_wake"
branch_labels = None
depends_on = None


def upgrade():
    from db import metadata

    bind = op.get_bind()
    metadata.tables["forms"].create(bind=bind, checkfirst=True)
    metadata.tables["form_responses"].create(bind=bind, checkfirst=True)


def downgrade():
    from db import metadata

    bind = op.get_bind()
    metadata.tables["form_responses"].drop(bind=bind, checkfirst=True)
    metadata.tables["forms"].drop(bind=bind, checkfirst=True)
