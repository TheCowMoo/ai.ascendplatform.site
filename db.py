"""
Database layer for CMBGrowthAI.

One ``DATABASE_URL`` selects the backend:

    DATABASE_URL=sqlite:///data/app.db                               (dev / small)
    DATABASE_URL=postgresql+psycopg://user:pass@host:5432/ascendplatform (production)

Queries keep the app's existing style — ``conn.execute(sql, params)`` with ``?``
placeholders — and this module adapts them to SQLAlchemy, so the rest of the
codebase does not care which database is in use. Rows behave like both mappings
and tuples (``row["name"]`` and ``row[0]`` both work).

The schema is defined once with SQLAlchemy Core metadata, so ``create_all()``
emits the right DDL for either dialect and Alembic can reuse the same metadata.
"""

import os
import re
import secrets
import datetime

import sqlalchemy as sa
from sqlalchemy import Column, Float, Index, Integer, MetaData, Table, Text, UniqueConstraint, text

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Legacy path, kept for backwards compatibility (SQLite default).
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "data", "app.db"))

DEFAULT_DATABASE_URL = "sqlite:///" + os.path.join(BASE_DIR, "data", "app.db").replace("\\", "/")
DATABASE_URL = (os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL).strip()


def now_str():
    """UTC 'YYYY-MM-DD HH:MM:SS' — the format SQLite's datetime('now') produced."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def days_ago(days):
    """UTC timestamp `days` days in the past, in the same format as now_str()."""
    moment = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _create_engine():
    kwargs = {"pool_pre_ping": True}
    if DATABASE_URL.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        path = DATABASE_URL.split("sqlite:///", 1)[-1]
        if path and path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    return sa.create_engine(DATABASE_URL, **kwargs)


engine = _create_engine()
DIALECT = engine.dialect.name
IS_POSTGRES = DIALECT == "postgresql"

# IntegrityError from either backend (used by the Meta channel unique constraint).
try:
    import sqlite3

    IntegrityError = (sqlite3.IntegrityError, sa.exc.IntegrityError)
except Exception:  # pragma: no cover
    IntegrityError = (sa.exc.IntegrityError,)

def _now_default():
    """Portable server default yielding 'YYYY-MM-DD HH:MM:SS' on both backends."""
    if IS_POSTGRES:
        return text("(to_char(now(), 'YYYY-MM-DD HH24:MI:SS'))")
    return text("(datetime('now'))")


def _literal(value):
    """A server_default that is a quoted SQL string literal."""
    return text("'%s'" % value)


def _created_at():
    return Column("created_at", Text, nullable=False, server_default=_now_default())


metadata = MetaData()

users = Table(
    "users", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("email", Text, nullable=False, unique=True),
    Column("name", Text, nullable=False),
    Column("password_hash", Text, nullable=False),
    Column("role", Text, nullable=False, server_default=_literal("member")),
    _created_at(),
)

bots = Table(
    "bots", metadata,
    Column("id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("assistant_name", Text, nullable=False, server_default=_literal("Assistant")),
    Column("brand_color", Text, nullable=False, server_default=_literal("#4f46e5")),
    Column("logo_url", Text, nullable=False, server_default=_literal("")),
    Column("welcome_message", Text, nullable=False, server_default=_literal("")),
    Column("quick_replies", Text, nullable=False, server_default=_literal("[]")),
    Column("system_prompt", Text, nullable=False, server_default=_literal("")),
    Column("knowledge_base", Text, nullable=False, server_default=_literal("")),
    Column("webhook_url", Text, nullable=False, server_default=_literal("")),
    Column("profile_id", Integer),
    Column("use_profile_kb", Integer, nullable=False, server_default=text("1")),
    Column("owner_id", Integer),
    _created_at(),
    Column("updated_at", Text, nullable=False, server_default=_now_default()),
)

messages = Table(
    "messages", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("bot_id", Text, nullable=False),
    Column("conversation_id", Text, nullable=False),
    Column("role", Text, nullable=False),
    Column("content", Text, nullable=False),
    _created_at(),
)

Index("idx_messages_bot", messages.c.bot_id)
Index("idx_messages_conv", messages.c.conversation_id)

# External messaging channels (Meta: Messenger / Instagram / WhatsApp).
# One bot can be linked to several channels/platform accounts.
channels = Table(
    "channels", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("bot_id", Text, nullable=False),
    Column("platform", Text, nullable=False),
    Column("external_id", Text, nullable=False),
    Column("access_token", Text, nullable=False, server_default=_literal("")),
    Column("app_secret", Text, nullable=False, server_default=_literal("")),
    Column("verify_token", Text, nullable=False, server_default=_literal("")),
    Column("enabled", Integer, nullable=False, server_default=text("1")),
    _created_at(),
    UniqueConstraint("platform", "external_id", name="uq_channels_platform_external"),
)

Index("idx_channels_bot", channels.c.bot_id)

# Idempotency ledger: Meta redelivers events, so we de-duplicate by message id.
meta_events = Table(
    "meta_events", metadata,
    Column("mid", Text, primary_key=True),
    _created_at(),
)

# Agent jobs: a long-running, audited AutoGen run.
jobs = Table(
    "jobs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("title", Text, nullable=False, server_default=_literal("")),
    Column("prompt", Text, nullable=False, server_default=_literal("")),
    Column("task", Text, nullable=False, server_default=_literal("echo")),
    Column("team_mode", Text, nullable=False, server_default=_literal("selector")),
    Column("requester_id", Integer),
    Column("status", Text, nullable=False, server_default=_literal("queued")),
    Column("result", Text, nullable=False, server_default=_literal("")),
    Column("error", Text, nullable=False, server_default=_literal("")),
    _created_at(),
    Column("started_at", Text),
    Column("finished_at", Text),
)

Index("idx_jobs_status", jobs.c.status)

# Append-only transcript: agent messages, tool calls and command output.
job_events = Table(
    "job_events", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("job_id", Integer, nullable=False),
    Column("seq", Integer, nullable=False),
    Column("source", Text, nullable=False, server_default=_literal("")),
    Column("type", Text, nullable=False, server_default=_literal("message")),
    Column("content", Text, nullable=False, server_default=_literal("")),
    _created_at(),
)

Index("idx_job_events_job", job_events.c.job_id, job_events.c.seq)

# Files/images produced by a job.
job_artifacts = Table(
    "job_artifacts", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("job_id", Integer, nullable=False),
    Column("kind", Text, nullable=False, server_default=_literal("file")),
    Column("path", Text, nullable=False, server_default=_literal("")),
    Column("mime", Text, nullable=False, server_default=_literal("")),
    _created_at(),
)

Index("idx_job_artifacts_job", job_artifacts.c.job_id)

# Business Profile: the single place a business describes itself. Its content is
# rendered into the knowledge base the AI answers from, and supplies the
# branding + tone of voice for bots and the widget.
business_profiles = Table(
    "business_profiles", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("owner_id", Integer, nullable=False, unique=True),
    Column("name", Text, nullable=False, server_default=_literal("")),
    Column("tagline", Text, nullable=False, server_default=_literal("")),
    Column("description", Text, nullable=False, server_default=_literal("")),
    Column("industry", Text, nullable=False, server_default=_literal("")),
    Column("website", Text, nullable=False, server_default=_literal("")),
    Column("logo_url", Text, nullable=False, server_default=_literal("")),
    Column("brand_color", Text, nullable=False, server_default=_literal("#4f46e5")),
    Column("brand_font", Text, nullable=False, server_default=_literal("")),
    Column("tone_of_voice", Text, nullable=False, server_default=_literal("")),
    Column("target_audience", Text, nullable=False, server_default=_literal("")),
    Column("services", Text, nullable=False, server_default=_literal("[]")),
    Column("pricing_notes", Text, nullable=False, server_default=_literal("")),
    Column("support_email", Text, nullable=False, server_default=_literal("")),
    Column("support_phone", Text, nullable=False, server_default=_literal("")),
    Column("address", Text, nullable=False, server_default=_literal("")),
    Column("business_hours", Text, nullable=False, server_default=_literal("")),
    Column("social", Text, nullable=False, server_default=_literal("{}")),
    Column("policies", Text, nullable=False, server_default=_literal("")),
    Column("faq", Text, nullable=False, server_default=_literal("[]")),
    Column("extra_notes", Text, nullable=False, server_default=_literal("")),
    Column("updated_at", Text, nullable=False, server_default=_now_default()),
)

# Designatable agents: each row becomes one AutoGen AssistantAgent, so roles can
# be bound to a provider + model + tool set from the admin UI ("Gemini = image
# creator") without code changes.
agents = Table(
    "agents",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", Text, nullable=False),
    Column("role", Text, nullable=False, server_default=_literal("")),
    Column("provider", Text, nullable=False, server_default=_literal("deepseek")),
    Column("model", Text, nullable=False, server_default=_literal("")),
    Column("system_prompt", Text, nullable=False, server_default=_literal("")),
    Column("tools", Text, nullable=False, server_default=_literal("[]")),
    Column("turn_order", Integer, nullable=False, server_default=text("0")),
    Column("enabled", Integer, nullable=False, server_default=text("1")),
    Column("temperature", Float),
    Column("max_tokens", Integer),
    _created_at(),
    Column("updated_at", Text, nullable=False, server_default=_now_default()),
)

# Remote servers the Operator agent may act on. The key never leaves the box;
# only its path is stored, and host-key pinning is enforced on connect.
ssh_hosts = Table(
    "ssh_hosts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("label", Text, nullable=False, server_default=_literal("")),
    Column("host", Text, nullable=False, server_default=_literal("")),
    Column("port", Integer, nullable=False, server_default=text("22")),
    Column("username", Text, nullable=False, server_default=_literal("")),
    Column("private_key_path", Text, nullable=False, server_default=_literal("")),
    Column("host_key_fingerprint", Text, nullable=False, server_default=_literal("")),
    Column("allowed_root", Text, nullable=False, server_default=_literal("")),
    Column("sites_root", Text, nullable=False, server_default=_literal("")),
    Column("enabled", Integer, nullable=False, server_default=text("1")),
    _created_at(),
)

# Hosted customer sites. The row is the desired state; the provisioner makes the
# target match it. status: pending_dns -> dns_ok -> live (or error).
sites = Table(
    "sites",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("owner_id", Integer),
    Column("domain", Text, nullable=False, unique=True),
    Column("aliases", Text, nullable=False, server_default=_literal("[]")),
    Column("docroot", Text, nullable=False, server_default=_literal("")),
    Column("content_html", Text, nullable=False, server_default=_literal("")),
    Column("linked_bot_id", Text, nullable=False, server_default=_literal("")),
    Column("target", Text, nullable=False, server_default=_literal("local")),
    Column("status", Text, nullable=False, server_default=_literal("pending_dns")),
    Column("ssl_status", Text, nullable=False, server_default=_literal("pending")),
    Column("dns_last_check", Text, nullable=False, server_default=_literal("")),
    Column("dns_last_ip", Text, nullable=False, server_default=_literal("")),
    Column("url", Text, nullable=False, server_default=_literal("")),
    Column("last_error", Text, nullable=False, server_default=_literal("")),
    _created_at(),
    Column("updated_at", Text, nullable=False, server_default=_now_default()),
)

# Approval gate: write/exec actions requested by an agent while in live mode.
action_approvals = Table(
    "action_approvals",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("job_id", Integer),
    Column("tool", Text, nullable=False, server_default=_literal("")),
    Column("summary", Text, nullable=False, server_default=_literal("")),
    Column("args_json", Text, nullable=False, server_default=_literal("{}")),
    Column("status", Text, nullable=False, server_default=_literal("pending")),
    Column("result", Text, nullable=False, server_default=_literal("")),
    Column("requested_at", Text, nullable=False, server_default=_now_default()),
    Column("decided_at", Text),
    Column("decided_by", Text, nullable=False, server_default=_literal("")),
)

# Operator-managed configuration, written from the admin Settings tab. Values
# here are layered over the process environment by ``settings.get`` (panel wins),
# which is also what lets a change take effect without a restart. Only keys
# listed in ``settings.SPEC`` may be stored - letting a request handler write
# arbitrary environment names would be a code-execution vector.
settings = Table(
    "settings",
    metadata,
    Column("key", Text, primary_key=True),
    Column("value", Text, nullable=False, server_default=_literal("")),
    Column("is_secret", Integer, nullable=False, server_default=text("0")),
    Column("updated_at", Text, nullable=False, server_default=_now_default()),
    Column("updated_by", Text, nullable=False, server_default=_literal("")),
)

# Audit trail for settings changes: which key changed, by whom, and when. Secret
# values are never recorded here - only the fact that the value changed.
settings_events = Table(
    "settings_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("key", Text, nullable=False, server_default=_literal("")),
    Column("action", Text, nullable=False, server_default=_literal("set")),
    Column("actor", Text, nullable=False, server_default=_literal("")),
    Column("detail", Text, nullable=False, server_default=_literal("")),
    _created_at(),
)

Index("idx_settings_events_key", settings_events.c.key)


# ---------------------------------------------------------------------------
# Connection / result adapters
# ---------------------------------------------------------------------------
class Row(dict):
    """A result row supporting both mapping and positional access.

    ``row["name"]`` and ``row[0]`` both work, and ``row.keys()`` behaves like
    ``sqlite3.Row.keys()`` so existing helpers such as ``as_dict`` keep working.
    """

    def __init__(self, mapping, values):
        super().__init__(mapping)
        self._values = tuple(values)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


class Result:
    """Minimal cursor stand-in: fetchone / fetchall / rowcount / lastrowid."""

    def __init__(self, rows, rowcount=0, lastrowid=None):
        self._rows = rows
        self.rowcount = rowcount
        self.lastrowid = lastrowid

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)

    def __len__(self):
        return len(self._rows)


_QUESTION = re.compile(r"\?")


def _bind(sql, params):
    """Rewrite ``?`` placeholders into SQLAlchemy named binds.

    A placeholder/parameter count mismatch raises instead of silently binding
    ``None``: a mismatch means a broken statement, and quietly forcing
    ``WHERE id = NULL`` turns a bug into a no-op that is very hard to spot.
    """
    values = tuple(params or ())
    binds = {}

    def replace(_match):
        index = len(binds)
        binds["p%d" % index] = values[index]
        return ":p%d" % index

    def mismatch(found):
        raise ValueError(
            "parameter count mismatch: %d placeholder(s) but %d value(s) for: %s"
            % (found, len(values), " ".join(sql.split())[:160])
        )

    try:
        rewritten = _QUESTION.sub(replace, sql)
    except IndexError:
        mismatch(len(binds) + 1)

    if len(binds) != len(values):
        mismatch(len(binds))
    return rewritten, binds


def _prepare(statement, params, is_postgres):
    """Rewrite a ``?``-style statement for the active dialect.

    Returns ``(sql, binds, kind)`` where kind is ``'insert'``, ``'dml'`` or
    ``'select'``. Kept as a pure function so it can be unit-tested without a
    live database.
    """
    statement = statement.strip()
    upper = statement.upper()
    is_insert = upper.startswith("INSERT")
    suffix = ""

    # SQLite's INSERT OR IGNORE -> PostgreSQL's ON CONFLICT DO NOTHING.
    if is_postgres and upper.startswith("INSERT OR IGNORE"):
        statement = re.sub(r"(?i)^INSERT\s+OR\s+IGNORE", "INSERT", statement, count=1)
        suffix = " ON CONFLICT DO NOTHING"

    sql, binds = _bind(statement, params)

    if is_postgres and is_insert and "RETURNING" not in sql.upper():
        suffix += " RETURNING id"

    if suffix:
        sql = sql.rstrip().rstrip(";") + suffix

    if is_insert:
        kind = "insert"
    elif upper.startswith("UPDATE") or upper.startswith("DELETE"):
        kind = "dml"
    else:
        kind = "select"
    return sql, binds, kind


class Connection:
    """Thin adapter giving a SQLAlchemy connection the sqlite3-style API."""

    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        # Mirrors `with sqlite3.connect(...)`: commit on success, rollback on error.
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()
        self._conn.close()
        return False

    def close(self):
        self._conn.close()

    def execute(self, sql, params=()):
        prepared, binds, kind = _prepare(sql, params, IS_POSTGRES)
        raw = self._conn.execute(sa.text(prepared), binds)

        if kind == "insert":
            if IS_POSTGRES:
                row = raw.fetchone()
                lastrowid = row[0] if row else None
                return Result([], rowcount=1 if lastrowid is not None else 0, lastrowid=lastrowid)
            return Result([], rowcount=raw.rowcount, lastrowid=raw.lastrowid)

        if kind == "dml":
            return Result([], rowcount=raw.rowcount)

        rows = [Row(r._mapping.items(), tuple(r)) for r in raw.fetchall()]
        return Result(rows, rowcount=len(rows))


def get_connection():
    return Connection(engine.connect())


def _ensure_columns():
    """Additive migrations for databases created by earlier versions."""
    try:
        inspector = sa.inspect(engine)
        columns = {c["name"] for c in inspector.get_columns("bots")}
    except Exception:
        return
    additions = []
    if "webhook_url" not in columns:
        additions.append("webhook_url TEXT NOT NULL DEFAULT ''")
    if "profile_id" not in columns:
        additions.append("profile_id INTEGER")
    if "use_profile_kb" not in columns:
        additions.append("use_profile_kb INTEGER NOT NULL DEFAULT 1")
    if not additions:
        return
    with get_connection() as conn:
        for ddl in additions:
            conn.execute("ALTER TABLE bots ADD COLUMN " + ddl)


def init_db():
    metadata.create_all(engine)
    _ensure_columns()


def new_bot_id():
    return secrets.token_urlsafe(8)
