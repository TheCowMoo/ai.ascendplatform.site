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

# The platform's own bucket for things that are unique per *org* but also belong to
# the platform itself: its Business Profile, and (in profile.py/settings.py) the
# operator's own client-scoped values. Not NULL, because a unique index permits any
# number of NULLs - NULL could not mean "the one platform row", it would silently
# allow duplicates. Org ids start at 1, so 0 is otherwise unused.
PLATFORM_ORG = 0

# Tenants: one row per client. The platform operator is not an org, and the
# ``owner`` role bypasses org scoping entirely.
#
# Domains stay globally unique - two clients cannot both own acme.com - while
# *access* to a site is scoped to its org. Conflating those two ideas is what let
# every logged-in user list every client's sites before this existed.
orgs = Table(
    "orgs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", Text, nullable=False, server_default=_literal("")),
    Column("slug", Text, nullable=False, unique=True),
    Column("plan", Text, nullable=False, server_default=_literal("standard")),
    Column("status", Text, nullable=False, server_default=_literal("active")),
    Column("notes", Text, nullable=False, server_default=_literal("")),
    _created_at(),
    Column("updated_at", Text, nullable=False, server_default=_now_default()),
)

users = Table(
    "users", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("email", Text, nullable=False, unique=True),
    Column("name", Text, nullable=False),
    Column("password_hash", Text, nullable=False),
    # owner = the platform operator (sees everything, belongs to no org);
    # client_admin = a client's admin; member = a client's staff.
    Column("role", Text, nullable=False, server_default=_literal("member")),
    Column("org_id", Integer),
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
    # owner_id = who created it (audit); org_id = the tenant that owns it.
    Column("owner_id", Integer),
    Column("org_id", Integer),
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
    Column("org_id", Integer),
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
    # One profile per org: the brand and voice belong to the client, not to
    # whichever login happened to create it. owner_id is kept as the creator.
    Column("owner_id", Integer, nullable=False),
    Column("org_id", Integer, unique=True),
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
    # AutoGen identifies participants by name and refuses a team with repeats
    # ("The participant names must be unique."), so the database enforces what the
    # runtime requires. Seeding raced on a fresh deploy and produced every agent
    # twice; this is the backstop that makes that impossible, not just unlikely.
    Index("uq_agents_name", "name", unique=True),
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
    Column("org_id", Integer),
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

# What the team produced for a client: a blog post, a funnel, an email. The
# "Websites" tab is the ``sites`` table - these are the things published *onto* a
# site or sent from it.
#
# One table with a ``kind`` rather than three near-identical ones: a blog, a funnel
# step and an email differ in content, not in lifecycle. ``status`` is what the
# approval gate moves (draft -> scheduled -> published), and ``org_id`` is what makes
# them a client's rather than the platform's.
deliverables = Table(
    "deliverables",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Integer),
    Column("site_id", Integer),
    Column("kind", Text, nullable=False, server_default=_literal("blog")),
    Column("title", Text, nullable=False, server_default=_literal("")),
    Column("slug", Text, nullable=False, server_default=_literal("")),
    Column("status", Text, nullable=False, server_default=_literal("draft")),
    Column("summary", Text, nullable=False, server_default=_literal("")),
    Column("content", Text, nullable=False, server_default=_literal("")),
    Column("url", Text, nullable=False, server_default=_literal("")),
    Column("job_id", Integer),
    Column("created_by", Integer),
    _created_at(),
    Column("updated_at", Text, nullable=False, server_default=_now_default()),
)

# The platform's own mail is not the clients' SMTP: invitations and operator notices
# go through GoHighLevel, and a client must never be able to see or spend that token.
# A sender is a *record of who we send as* - GHL remains the only place a from-address
# can be verified - plus the custom-field values stamped onto each contact it creates.
mail_senders = Table(
    "mail_senders",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Integer),  # NULL = the platform's own sender
    Column("name", Text, nullable=False, server_default=_literal("")),
    Column("from_name", Text, nullable=False, server_default=_literal("")),
    Column("from_email", Text, nullable=False, server_default=_literal("")),
    Column("reply_to", Text, nullable=False, server_default=_literal("")),
    Column("signature", Text, nullable=False, server_default=_literal("")),
    Column("logo_url", Text, nullable=False, server_default=_literal("")),
    Column("custom_fields", Text, nullable=False, server_default=_literal("{}")),
    Column("is_default", Integer, nullable=False, server_default=text("0")),
    _created_at(),
    Column("updated_at", Text, nullable=False, server_default=_now_default()),
)

# An invitation to join the platform. The token is stored as a SHA-256 hash: until it
# expires it is password-equivalent, so the database must not hold a usable copy.
# The user row is created at invite time (with an unusable random password) so email
# uniqueness and org scoping are enforced up front, and "resend" is only a new token.
invites = Table(
    "invites",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("email", Text, nullable=False, server_default=_literal("")),
    Column("name", Text, nullable=False, server_default=_literal("")),
    Column("org_id", Integer),
    Column("role", Text, nullable=False, server_default=_literal("client_admin")),
    Column("user_id", Integer),
    Column("token_hash", Text, nullable=False, server_default=_literal("")),
    Column("invited_by", Integer),
    Column("expires_at", Text, nullable=False, server_default=_literal("")),
    Column("accepted_at", Text),
    Column("revoked_at", Text),
    _created_at(),
    Index("ix_invites_email", "email"),
    Index("ix_invites_token", "token_hash"),
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

# Per-client overrides for the settings whose scope is "client" - today the SMTP
# credentials a client sends mail with. Platform-scoped settings (model keys,
# Pexels, the SSH limits) stay in ``settings`` above, because they belong to the
# deployment rather than to any one client.
#
# A separate table rather than an ``org_id`` column on ``settings``: that table's
# primary key *is* the key name, so scoping it per client means re-keying it to
# (org_id, key), which rebuilds a table holding live credentials on every existing
# deployment. Additive is the safer shape for that migration.
client_settings = Table(
    "client_settings",
    metadata,
    Column("org_id", Integer, primary_key=True),
    Column("key", Text, primary_key=True),
    Column("value", Text, nullable=False, server_default=_literal("")),
    Column("is_secret", Integer, nullable=False, server_default=text("0")),
    Column("updated_at", Text, nullable=False, server_default=_now_default()),
    Column("updated_by", Text, nullable=False, server_default=_literal("")),
)


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


_INSERT_TARGET = re.compile(
    r"(?is)^\s*INSERT\s+(?:OR\s+IGNORE\s+)?INTO\s+([A-Za-z_][A-Za-z0-9_]*)"
)


def _insert_target(statement):
    """The table an INSERT writes to, or '' when it cannot be parsed."""
    match = _INSERT_TARGET.match(statement or "")
    return match.group(1).lower() if match else ""


def _has_id_column(table_name):
    """True when ``table_name`` actually has an ``id`` column to return.

    ``settings`` has ``key`` as its primary key and ``client_settings`` has a
    composite one, so ``RETURNING id`` on either is a SQL error - which is exactly
    what a first-time "save your API key" did on PostgreSQL (500, "column id does
    not exist") while every SQLite test passed, because SQLite never takes this path.
    """
    table = metadata.tables.get(table_name)
    return table is not None and "id" in table.c


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
        # Only ask for an id when there is one: settings and client_settings are
        # keyed by what they store, not by a surrogate id.
        if _has_id_column(_insert_target(statement)):
            suffix += " RETURNING id"

    if suffix:
        sql = sql.rstrip().rstrip(";") + suffix

    if is_insert:
        kind = "insert"
    elif upper.startswith("UPDATE") or upper.startswith("DELETE"):
        kind = "dml"
    elif upper.startswith(("CREATE", "ALTER", "DROP", "TRUNCATE")):
        # DDL returns no rows. Classifying it as a SELECT meant execute() called
        # fetchall() on a non-row result, which raised ResourceClosedError the first
        # time _ensure_columns actually had a column to add.
        kind = "ddl"
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
            # fetchone() only makes sense when RETURNING was actually appended - on
            # PostgreSQL an insert without it is not a row-returning statement, and
            # calling fetchone() there raises ResourceClosedError.
            if IS_POSTGRES and "RETURNING" in prepared.upper():
                row = raw.fetchone()
                lastrowid = row[0] if row else None
                return Result([], rowcount=1 if lastrowid is not None else 0, lastrowid=lastrowid)
            return Result([], rowcount=raw.rowcount, lastrowid=getattr(raw, "lastrowid", None))

        if kind == "dml":
            return Result([], rowcount=raw.rowcount)

        if kind == "ddl":
            return Result([], rowcount=0)

        rows = [Row(r._mapping.items(), tuple(r)) for r in raw.fetchall()]
        return Result(rows, rowcount=len(rows))


def get_connection():
    return Connection(engine.connect())


# Any bigint works; it only has to be the same in every process that seeds.
SEED_LOCK_KEY = 20261020


def seed_lock(conn):
    """Serialise import-time seeding across worker processes.

    The app seeds at import time and serves with ``uvicorn --workers 2``, so two
    processes reach every "insert it if the table is empty" block at the same moment.
    On a fresh database both read an empty table and both insert - which is how a
    deploy came up with the whole agent roster duplicated, and every team build died
    inside AutoGen with "The participant names must be unique."

    Held until the surrounding transaction commits (``Connection.__exit__``), so the
    count, the inserts and the commit are one atomic step. SQLite needs no lock: the
    writer takes the file lock itself, and the tests are single-process.
    """
    if IS_POSTGRES:
        conn.execute("SELECT pg_advisory_xact_lock(?)", (SEED_LOCK_KEY,))


def _sqlite_unique_on(table, column):
    """The name of a SQLite UNIQUE constraint covering exactly ``column``, or None.

    ``PRAGMA index_list`` reports constraints created by ``Column(unique=True)`` with
    ``origin='u'``; those are the auto-indexes that carry no name of their own, which
    is why dropping one needs a table rebuild rather than a DROP CONSTRAINT.
    """
    try:
        indexes = engine.connect().exec_driver_sql(
            "PRAGMA index_list('%s')" % table
        ).fetchall()
    except Exception:
        return None
    for index in indexes:
        if not index[2] or str(index[3]) != "u":
            continue
        try:
            cols = engine.connect().exec_driver_sql(
                "PRAGMA index_info('%s')" % index[1]
            ).fetchall()
        except Exception:
            continue
        if [str(row[2]) for row in cols] == [column]:
            return str(index[1])
    return None


def _drop_legacy_owner_unique(conn):
    """Remove business_profiles' legacy UNIQUE(owner_id), which now breaks profiles.

    Profiles are keyed by org, so one user no longer owns exactly one profile - and
    ``owner_id`` is an audit field that is 0 for a row created before anyone has
    logged in. A database created by an older release carries this constraint *inside
    the column definition*, and the two dialects cannot drop it the same way:
    PostgreSQL gives it a name, SQLite does not. A fresh database has it neither way,
    because 0001 builds the table from the current metadata, so this is a no-op there.

    ``conn`` is a raw SQLAlchemy connection so both callers can pass what they have:
    ``_ensure_columns`` (the dev SQLite path) and migration 0006 via ``op.get_bind()``.

    Found the hard way: `POST /api/bots` returned 500 with
    ``UNIQUE constraint failed: business_profiles.owner_id`` on any upgraded dev
    database, because the profile lookup resolved to a second INSERT.
    """
    if IS_POSTGRES:
        conn.execute(sa.text(
            "ALTER TABLE business_profiles "
            "DROP CONSTRAINT IF EXISTS business_profiles_owner_id_key"
        ))
        return

    if not _sqlite_unique_on("business_profiles", "owner_id"):
        return

    # SQLite cannot drop a constraint, so the table is rebuilt: the documented
    # procedure, minus the steps this schema does not need (no foreign keys, no
    # triggers, no views). The old table is renamed first so its rows can be copied
    # into the fresh one, which metadata declares with the constraints we do want.
    table = metadata.tables["business_profiles"]
    conn.execute(sa.text("ALTER TABLE business_profiles RENAME TO business_profiles_legacy"))
    table.create(bind=conn)
    columns = ", ".join(c.name for c in table.columns)
    conn.execute(sa.text(
        "INSERT INTO business_profiles (%s) SELECT %s FROM business_profiles_legacy"
        % (columns, columns)
    ))
    conn.execute(sa.text("DROP TABLE business_profiles_legacy"))


# Additive column additions for databases created by an earlier version. The
# container runs `alembic upgrade head`, but a dev SQLite file that predates a
# revision never sees those migrations, so the same additions are declared here.
# Append-only.
_ADDITIVE_COLUMNS = (
    ("bots", "webhook_url", "webhook_url TEXT NOT NULL DEFAULT ''"),
    ("bots", "profile_id", "profile_id INTEGER"),
    ("bots", "use_profile_kb", "use_profile_kb INTEGER NOT NULL DEFAULT 1"),
    ("users", "org_id", "org_id INTEGER"),
    ("bots", "org_id", "org_id INTEGER"),
    ("sites", "org_id", "org_id INTEGER"),
    ("jobs", "org_id", "org_id INTEGER"),
    ("business_profiles", "org_id", "org_id INTEGER"),
)


def _ensure_columns():
    """Additive migrations for databases created by earlier versions."""
    try:
        tables = set(sa.inspect(engine).get_table_names())
    except Exception:
        return

    with get_connection() as conn:
        for table, column, ddl in _ADDITIVE_COLUMNS:
            if table not in tables:
                continue
            try:
                present = {c["name"] for c in sa.inspect(engine).get_columns(table)}
            except Exception:
                continue
            if column not in present:
                conn.execute("ALTER TABLE %s ADD COLUMN %s" % (table, ddl))

        # A Business Profile is unique per *org*, and the creating user is no longer
        # the key (see _drop_legacy_owner_unique for why an upgraded database needs
        # that constraint removed first).
        if "business_profiles" in tables:
            try:
                _drop_legacy_owner_unique(conn._conn)
            except Exception:
                pass
            try:
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_business_profiles_org "
                    "ON business_profiles (org_id)"
                )
            except Exception:
                pass
            # Migration 0006 puts the platform's own Business Profile at org 0; mirror
            # it here so a dev SQLite file behaves like a migrated one. Without this
            # the app finds no profile for org 0 and creates a second row.
            try:
                conn.execute(
                    "UPDATE business_profiles SET org_id = %d "
                    "WHERE org_id IS NULL AND id = ("
                    "  SELECT MIN(id) FROM business_profiles WHERE org_id IS NULL)"
                    % PLATFORM_ORG
                )
            except Exception:
                pass

        # Client-scoped settings overrides (migration 0006). create_all() makes the
        # table on a fresh database; this covers one created before it existed.
        if "client_settings" not in tables:
            try:
                metadata.tables["client_settings"].create(bind=engine)
            except Exception:
                pass


def init_db():
    metadata.create_all(engine)
    _ensure_columns()


def new_bot_id():
    return secrets.token_urlsafe(8)
