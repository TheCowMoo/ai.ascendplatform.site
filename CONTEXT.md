# AscendPlatform — system context

A single-tenant-per-client AI platform: a team of LLM agents that **acts** on
infrastructure (provisions sites, writes files to servers, publishes pages, reads
analytics) on the operator's own model keys, with GoHighLevel as the rails for contacts
and email. Two consoles: the operator's `#/platform/*` and the client's `#/app/*`.

Read `SYSTEM_PLAN.md` for the reasoning behind each fix (§25-33 are the recent ones).
This file is the map: what exists, where it lives, and what is still open.

## Runtime shape

```
caddy ──▶ app (Flask + FastAPI via a2wsgi, uvicorn --workers 2)
            ├─▶ db (Postgres 16)          SQLAlchemy Core + Alembic
            ├─▶ redis                     arq queue when JOB_BACKEND=redis
            ├─▶ worker (python -m worker) runs agent jobs
            └─▶ litellm (optional gateway)
```

- `app.py` — Flask: auth, clients/orgs, sites, deliverables, settings, invites, jobs API.
- `asgi.py` — FastAPI for SSE (`/api/jobs/<id>/stream`) and `/api/providers` (owner-only),
  everything else mounted through to Flask.
- `worker.py` — arq worker; the in-process runner is used when `JOB_BACKEND=local`.
- Startup order matters: `alembic upgrade head` runs in the app container, `init_db()`
  (`metadata.create_all`) runs wherever `db` is imported. Migrations therefore use
  `checkfirst=True` — see §34 in `SYSTEM_PLAN.md` and the 502 incident.

## Module map

| Module | Responsibility |
|---|---|
| `db.py` | Engine, `Connection` adapter (`?` placeholders, `RETURNING id` on Postgres), all `Table` definitions, `init_db()`, `seed_lock()` |
| `settings.py` | SPEC of every setting, panel→env→default resolution, `all_for_ui(org_id)` (client sees only `scope=client`), write guard, audit history, per-group live tests |
| `orgs.py` | Tenants (clients) + their users; `client_admin` / `client_member` / `owner` |
| `profile.py` | Business Profile per org — the brand facts the agents write from |
| `llm.py` | `PROVIDERS` (the single source of truth for capabilities), `get_model_client()`, `_clean_messages()` + `without_empty_blocks()` payload repair for Anthropic |
| `agents.py` | Roster (`DEFAULT_ROSTER`), seeding, `build_team()`, `run_team()`, `system_message_for()`, `unique_name()`, `guard_tools_supported()` |
| `tools/` | `register()`/`resolve()` registry: `site`, `pexels`, `media`, `mail`, `deliverable`, `policy`, `ssh` |
| `jobs.py` | Job rows, events, artifacts, `TASKS` registry, runners |
| `deliverables.py` | Blogs/funnels/emails/websites/proposals; `publish_deliverable()` |
| `review.py` | Weekly brief (two windows), prompt, `site_review` job, digest |
| `analytics.py` | `data_sources`, `metrics_daily`, `sync_runs`; source CRUD, idempotent metrics |
| `site_logs.py` | Caddy access log → per-org metrics (no IPs stored) |
| `ga4.py` | GA4 service-account adapter (key by path) |
| `versions.py` | `site_versions` — snapshot on publish, one-call restore |
| `platform_mail.py` | The operator's own mail through GHL (contact upsert → conversation message) |
| `invites.py` | Single-use hashed invite tokens, accept → set password |
| `scheduler.py` | Daily source sync, weekly `site_review` queueing, quip-free guards |
| `admin/admin.js` | Single-page console: `#/platform/*`, `#/app/*`, `#/accept/<token>` |



## Data model (migrations 0001-0011)

| Table | Notes |
|---|---|
| `users` | `role` ∈ owner / client_admin / client_member, `org_id` |
| `orgs` | the clients; `slug` unique |
| `business_profiles` | one per org |
| `bots`, `messages`, `channels`, `meta_events` | chat widget |
| `jobs`, `job_events`, `job_artifacts` | every run, its transcript and files |
| `agents` | roster, **no `org_id`** — one team for the whole platform; `name` unique |
| `ssh_hosts`, `sites`, `action_approvals` | infrastructure the Operator may touch |
| `settings`, `settings_events` | operator panel + audit |
| `client_settings` | per-org overrides (only `scope=client` fields) |
| `deliverables` | kind ∈ blog/funnel/email/website/proposal/asset; `content` is HTML; `job_id`, `created_by` |
| `mail_senders`, `invites` | platform mail identity; invites hold a SHA-256 token |
| `data_sources`, `metrics_daily`, `sync_runs` | analytics spine; metrics have a unique point key |
| `site_versions` | one row per publish, restorable |

Postgres `RETURNING id` is appended only for tables that actually have an `id` column
(`settings`, `client_settings`, `meta_events` do not — that was a live 500).

## The three core flows

**A job.** `POST /api/jobs` → row + events. The worker (or in-process runner) builds the
team from the roster and runs it, emitting events. `agent_team` is the general task;
`site_review` and `site_provision` are specific. Every side-effecting tool goes through
`actions.gate()` (approval rows + blocking wait) **except** writes inside `artifacts/`
(downloads, image generation) — internal, ungated. The UI streams via SSE with a polling
fallback.

**A deliverable.** Copywriter/Operator calls `save_deliverable(kind, title, content,
summary, deliverable_id)`. Ownership comes from the job row, never the model. Images:
placeholders are filled from `artifacts/images/job-<id>/` → `/artifacts/...`; if the job
has no photos the placeholder `<img>` is stripped so the email still saves. Same
kind+title from the same job updates that draft. `proposal` is never publishable.
Publishing writes through `provisioner`, snapshots the previous HTML into
`site_versions`, and records an approved approval row carrying the operator's name —
the click *is* the decision.

**An invitation.** Owner invites → user row with an unusable random password → SHA-256
token stored, plaintext returned once → GHL upserts the contact and sends → `#/accept/
<token>` sets the password and starts the session. Resend rotates the token and kills
the old link. A failed email still leaves a usable invitation (502 *with* the link).

## Model layer rules



## Configuration that matters

Platform scope (operator only): model keys `DEEPSEEK_*`, `ANTHROPIC_*`, `GEMINI_*`,
`LLM_GATEWAY_*`, `PEXELS_*`, `SSH_*`, `ALLOW_LIVE_ACTIONS`, `APPROVAL_TIMEOUT`,
`GHL_*` (token, location, from-address), `INVITE_*`, `PLATFORM_URL`, `CADDY_ACCESS_LOG`,
`SITE_LOG_SYNC_HOURS`, `SITE_REVIEW_WEEKDAY`.

Client scope (per org, the **only** client-scoped group): `SMTP_*`.

Structural, `.env` only, restart required: `DATABASE_URL`, `SECRET_KEY`, `JOB_BACKEND`,
`REDIS_URL`, `SITE_ROOT`, `CADDYFILE`, `ARTIFACTS_DIR`, `HOST`/`PORT`, `ADMIN_*`.

## Conventions in this codebase

- Queries are `conn.execute("… ? …", params)` through `db.Connection`; no ORM.
- Every service raises a named error (`DeliverableError`, `InviteError`, `MailError`,
  `SourceError`) carrying a sentence the operator can act on; routes map them to 400s.
- Tenancy is checked with `can_access_org()` / `list_scope()`; cross-tenant access is
  404, not 403, so ids cannot be probed.
- Tests are `tests/<name>.py` run directly (no pytest), each `main()` printing `X OK` at
  the end, and every suite must be **re-runnable** — generate a per-run suffix rather
  than assuming an empty database.
- `tests/preflight.py` imports the app and runs the six fast suites. Run it before every
  push; three startup-breaking commits were pushed without it.
- Migrations: `metadata.tables[name].create(bind=..., checkfirst=True)`.

## Known gaps

1. **No payload validator.** Malformed Anthropic requests fail as a 400 mid-job. Five
   distinct shapes have been patched reactively; a local validator that raises with the
   message index and block type would end the class.
2. **Animations are text only.** `watchJob` rotates quips; there is no shimmer/pulse, and
   the Jobs tab and transcript view do not use it at all.
3. **Refine chatbox is deliverable-only.** `POST /api/deliverables/<id>/refine` exists;
   a general `POST /api/jobs/<id>/followup` for any job does not.
4. **History/Restore has an API, no button.** `GET /api/sites/<id>/versions`,
   `POST /api/versions/<id>/restore`.
5. **Digest needs a recipient.** `sites` has no contact email, so weekly digests are
   skipped with a message.
6. **GA4's live token exchange is unverified** (signing and report parsing are tested).
7. **GHL and Meta metric adapters** report "no adapter yet".
8. **No backups, alerting or HA.** The largest real risk to running this for clients.
9. **Worker health check is wrong** — it curls port 8000 on an arq process that serves
   no HTTP, so the worker shows `unhealthy` while working.
10. **Caddy must log to a file** (`log { output file /var/log/caddy/access.log }`) and
    that path must be mounted into `app` before site metrics exist.

## Deploying

```bash
cd /opt/ascendplatform
git status -sb                     # must say "## main..." — not "HEAD detached"
git pull --ff-only
docker compose up -d --build       # app runs alembic upgrade head first
docker compose exec app python -c "import app, jobs; print(sorted(jobs.TASKS))"
```

`TASKS` should list `agent_team`, `echo`, `site_provision`, `site_review`.

`PROVIDERS` in `llm.py` is the only source of capability truth (`tools`, `vision`,
`family`). The Anthropic client is given an explicit `ModelInfo` from it, so an unknown
model name can never remove tool support. Before sending, `without_empty_blocks()`
rebuilds the payload: empty text blocks dropped; empty messages replaced with
"(no content)" — never removed, since that orphans tool results; `tool_result` blocks
kept only when a `tool_use` sits in the **immediately previous** message.
