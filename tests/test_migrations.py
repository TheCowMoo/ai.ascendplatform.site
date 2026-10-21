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


def _rows(sql):
    engine = sa.create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as conn:
        return conn.execute(sa.text(sql)).fetchall()


def test_upgrade_dedupes_a_raced_roster():
    """Upgrading a populated table must repair the double seed, not fail on it.

    The seed race left two identical copies of every agent, and revision 0008 cannot
    create its unique index while they are there. This is the "upgrade a database that
    already has data" path - the half of the migration story this file never covered.
    """
    result, output = _alembic("downgrade", "0007_deliverables")
    assert result.returncode == 0, output[-2000:]

    engine = sa.create_engine(os.environ["DATABASE_URL"])
    with engine.begin() as conn:
        for _copy in range(2):  # the two racing uvicorn workers
            for order, name in enumerate(("Planner", "Operator")):
                conn.execute(
                    sa.text(
                        "INSERT INTO agents (name, role, provider, model, system_prompt, "
                        "tools, turn_order, enabled) VALUES (:n, 'role', 'deepseek', '', "
                        "'prompt', '[]', :t, 1)"
                    ),
                    {"n": name, "t": order},
                )
        # A hand-written agent that merely shares a name differs in content, so it
        # must be renamed rather than deleted.
        conn.execute(
            sa.text(
                "INSERT INTO agents (name, role, provider, model, system_prompt, "
                "tools, turn_order, enabled) VALUES ('Operator', 'different job', "
                "'deepseek', '', 'other prompt', '[]', 9, 1)"
            )
        )
    assert len(_rows("SELECT id FROM agents")) == 5

    result, output = _alembic("upgrade", "head")
    if result.returncode != 0:
        print(output[-2000:])
    assert result.returncode == 0, "upgrade over populated agents failed"

    names = [row[0] for row in _rows("SELECT name FROM agents ORDER BY id")]
    print("agents after upgrade:", names)
    assert names.count("Planner") == 1, names
    assert names.count("Operator") == 1, names
    assert len(names) == 3, names
    assert any(n.startswith("Operator (") for n in names), names

    indexes = sa.inspect(sa.create_engine(os.environ["DATABASE_URL"])).get_indexes("agents")
    unique = [i["name"] for i in indexes if i.get("unique") and i["column_names"] == ["name"]]
    assert "uq_agents_name" in unique, indexes
    print("duplicate roster repaired, unique index on agents.name present")


def test_downgrade_is_clean():
    """The chain reverses fully: each revision drops only its own tables."""
    result, output = _alembic("downgrade", "base")
    if result.returncode != 0:
        print(output[-2000:])
    assert result.returncode == 0, "alembic downgrade base failed (see above)"
    left = _tables()
    assert left == set(), "tables survived downgrade: %s" % sorted(left)
    print("downgrade to base: no application tables left")


def test_an_upgrade_survives_tables_that_already_exist():
    """The production outage, reproduced.

    ``init_db()`` calls ``metadata.create_all()`` and the worker runs it at startup, so
    on a cold start the tables can exist before Alembic arrives. The upgrade must skip
    them rather than die on 'relation "data_sources" already exists' - which crash-looped
    the app and served 502s.
    """
    engine = sa.create_engine(os.environ["DATABASE_URL"])
    # The production state: everything up to 0009 already applied, then the worker's
    # create_all() lands the new tables before the app's upgrade runs.
    result, output = _alembic("upgrade", "0009_platform_email")
    assert result.returncode == 0, output[-1500:]
    db.metadata.create_all(bind=engine)

    result, output = _alembic("upgrade", "head")
    if result.returncode != 0:
        print(output[-2000:])
    assert result.returncode == 0, "upgrade failed against pre-created tables"
    print("upgrade over create_all'd tables: OK")


if __name__ == "__main__":
    test_upgrade_head_on_an_empty_database()
    test_schema_matches_metadata()
    test_upgrade_dedupes_a_raced_roster()
    test_downgrade_is_clean()
    test_an_upgrade_survives_tables_that_already_exist()
    print("MIGRATIONS OK")
