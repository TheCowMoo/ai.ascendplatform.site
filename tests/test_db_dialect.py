"""Offline verification of the dual-dialect database layer.

Run from the repo root:
    .\\venv\\Scripts\\python.exe tests\\test_db_dialect.py

No database server is required: this compiles the schema for PostgreSQL and
checks the ``?``-placeholder / INSERT OR IGNORE translation logic.
"""

import json
import os
import subprocess
import sys
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from sqlalchemy.dialects import postgresql, sqlite  # noqa: E402
from sqlalchemy.schema import CreateTable  # noqa: E402

import db  # noqa: E402

PG_URL = "postgresql+psycopg://ascendplatform:secret@localhost:5432/ascendplatform"


def test_bind():
    sql, binds = db._bind("SELECT * FROM bots WHERE id = ? AND name = ?", ("abc", "x"))
    assert sql == "SELECT * FROM bots WHERE id = :p0 AND name = :p1", sql
    assert binds == {"p0": "abc", "p1": "x"}, binds
    print("bind rewrite OK:", sql)


def test_prepare_sqlite():
    sql, binds, kind = db._prepare("INSERT OR IGNORE INTO meta_events (mid) VALUES (?)", ("m1",), False)
    assert kind == "insert", kind
    assert sql == "INSERT OR IGNORE INTO meta_events (mid) VALUES (:p0)", sql
    assert binds == {"p0": "m1"}, binds
    print("sqlite insert-or-ignore OK:", sql)


def test_prepare_postgres():
    sql, binds, kind = db._prepare("INSERT OR IGNORE INTO meta_events (mid) VALUES (?)", ("m1",), True)
    assert kind == "insert", kind
    assert sql == "INSERT INTO meta_events (mid) VALUES (:p0) ON CONFLICT DO NOTHING RETURNING id", sql
    print("postgres upsert OK:", sql)

    sql2, _, kind2 = db._prepare("INSERT INTO jobs (title) VALUES (?)", ("t",), True)
    assert kind2 == "insert"
    assert sql2.endswith("RETURNING id"), sql2
    print("postgres returning OK:", sql2)

    sql3, _, kind3 = db._prepare("UPDATE bots SET name=? WHERE id=?", ("n", "i"), True)
    assert kind3 == "dml", kind3
    assert "RETURNING" not in sql3.upper(), sql3
    assert sql3 == "UPDATE bots SET name=:p0 WHERE id=:p1", sql3
    print("postgres update OK:", sql3)

    sql4, _, kind4 = db._prepare("SELECT * FROM bots WHERE id = ?", ("i",), True)
    assert kind4 == "select"
    assert "RETURNING" not in sql4.upper()
    print("postgres select OK:", sql4)


def test_bind_mismatch_is_loud():
    """A placeholder/parameter mismatch must raise, never silently bind None.

    Binding None turns `WHERE id = ?` into `WHERE id = NULL`, which matches
    nothing and looks like success — that is how the S5 servers update bug hid.
    """
    for sql, params in (
        ("SELECT * FROM bots WHERE id = ? AND name = ?", ("only-one",)),
        ("SELECT * FROM bots WHERE id = ?", ("a", "b")),
    ):
        try:
            db._bind(sql, params)
        except ValueError as exc:
            print("raised:", str(exc)[:100])
        else:
            raise AssertionError("expected a mismatch error for %r with %r" % (sql, params))
    print("BIND MISMATCH OK")


def test_sqlite_schema():
    lite = sqlite.dialect()
    tables = {t.name for t in db.metadata.sorted_tables}
    expected = {
        "users", "bots", "messages", "channels", "meta_events",
        "jobs", "job_events", "job_artifacts", "business_profiles", "agents",
        "ssh_hosts", "sites", "action_approvals", "settings", "settings_events",
        "orgs", "client_settings",
    }
    assert tables == expected, tables
    print("tables:", sorted(tables))

    ddl = str(CreateTable(db.users).compile(dialect=lite))
    # SQLite auto-increments `INTEGER PRIMARY KEY`; AUTOINCREMENT is only needed
    # to stop ROWID reuse, so its absence is fine.
    assert "PRIMARY KEY (id)" in ddl, ddl
    assert "datetime('now')" in ddl, ddl
    print("sqlite DDL OK (users)")


def test_postgres_schema():
    """Import the layer under a PostgreSQL DATABASE_URL and inspect the DDL.

    ``create_engine`` is lazy, so no server is required — this validates that
    the module really emits PostgreSQL DDL and translations when configured for it.
    """
    code = textwrap.dedent(
        """
        import json, sys
        sys.path.insert(0, r"%(root)s")
        import db
        from sqlalchemy.schema import CreateTable
        from sqlalchemy.dialects import postgresql
        pg = postgresql.dialect()
        print(json.dumps({
            "dialect": db.DIALECT,
            "is_postgres": db.IS_POSTGRES,
            "users_ddl": str(CreateTable(db.users).compile(dialect=pg)),
            "jobs_ddl": str(CreateTable(db.jobs).compile(dialect=pg)),
            "upsert": db._prepare(
                "INSERT OR IGNORE INTO meta_events (mid) VALUES (?)", ("m",), db.IS_POSTGRES
            )[0],
            "insert": db._prepare(
                "INSERT INTO jobs (title) VALUES (?)", ("t",), db.IS_POSTGRES
            )[0],
        }))
        """
    ) % {"root": ROOT}

    env = dict(os.environ, DATABASE_URL=PG_URL)
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=ROOT
    )
    assert proc.returncode == 0, proc.stderr
    info = json.loads(proc.stdout.strip().splitlines()[-1])

    assert info["dialect"] == "postgresql", info["dialect"]
    assert info["is_postgres"] is True

    users = info["users_ddl"]
    assert "SERIAL" in users, users
    assert "AUTOINCREMENT" not in users.upper(), users
    assert "datetime(" not in users, users
    assert "to_char(now()" in users, users
    print("postgres DDL OK (users):")
    print(users)

    assert "SERIAL" in info["jobs_ddl"], info["jobs_ddl"]
    assert info["upsert"].endswith("ON CONFLICT DO NOTHING RETURNING id"), info["upsert"]
    assert info["insert"].endswith("RETURNING id"), info["insert"]
    print("postgres upsert:", info["upsert"])
    print("postgres insert:", info["insert"])

    print("DATABASE LAYER OK")


if __name__ == "__main__":
    test_bind()
    test_prepare_sqlite()
    test_prepare_postgres()
    test_bind_mismatch_is_loud()
    test_sqlite_schema()
    test_postgres_schema()
