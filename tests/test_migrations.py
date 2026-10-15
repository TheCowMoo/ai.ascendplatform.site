"""Alembic migrations must build the schema from empty.

Run: .\\venv\\Scripts\\python.exe tests\\test_migrations.py

This runs the real ``alembic upgrade head`` against a throwaway database, which is
the only place the migration path is exercised: the application and every other
suite use ``db.init_db()`` -> ``metadata.create_all()``. A broken revision could
therefore ship unnoticed.

It did. Revision 0001 called ``metadata.create_all()``, which creates every table
in the *current* metadata, so each table added later (agents, ssh_hosts, sites,
action_approvals, settings, settings_events) was created twice and a fresh deploy
died with "relation agents already exists" while the app container crash-looped.
"""

import os
import subprocess
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

# Point the run at a throwaway database BEFORE importing db, which reads
# DATABASE_URL at import time.
TMP_DIR = tempfile.mkdtemp(prefix="ascendplatform-migrations-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP_DIR, "migrated.db").replace("\\", "/")

import sqlalchemy as sa  # noqa: E402

import db  # noqa: E402


def _alembic(*args):
    """Run an alembic command in a subprocess, so it sees this DATABASE_URL."""
    result = subprocess.run(
        [sys.executable, "-m", "alembic"] + list(args),
        cwd=REPO_ROOT,
        env=dict(os.environ),
        capture_output=True,
        text=True,
    )
    output = (result.stdout or "") + (result.stderr or "")
    for line in output.splitlines():
        if "Running upgrade" in line or "Running downgrade" in line:
            print("  " + line.strip())
    return result, output


def _tables():
    engine = sa.create_engine(os.environ["DATABASE_URL"])
    return set(sa.inspect(engine).get_table_names()) - {"alembic_version"}


def test_upgrade_head_on_an_empty_database():
    """Every revision must apply cleanly to a database with no tables."""
    result, output = _alembic("upgrade", "head")
    if result.returncode != 0:
        print(output[-2000:])
    assert result.returncode == 0, "alembic upgrade head failed (see above)"
    print("upgrade head: OK")


def test_schema_matches_metadata():
    """Every table in db.metadata exists, and nothing extra was created."""
    present = _tables()
    expected = {table.name for table in db.metadata.sorted_tables}
    assert present == expected, (
        "missing: %s | extra: %s"
        % (sorted(expected - present), sorted(present - expected))
    )
    print("tables: %d, exactly matching db.metadata" % len(present))


def test_downgrade_is_clean():
    """The chain reverses fully: each revision drops only its own tables."""
    result, output = _alembic("downgrade", "base")
    if result.returncode != 0:
        print(output[-2000:])
    assert result.returncode == 0, "alembic downgrade base failed (see above)"
    left = _tables()
    assert left == set(), "tables survived downgrade: %s" % sorted(left)
    print("downgrade to base: no application tables left")


if __name__ == "__main__":
    test_upgrade_head_on_an_empty_database()
    test_schema_matches_metadata()
    test_downgrade_is_clean()
    print("MIGRATIONS OK")
