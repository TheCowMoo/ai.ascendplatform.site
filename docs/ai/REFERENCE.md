# AI Reference — AscendPlatform

This is the current-state map. For how to start work, read `AGENTS.md` first. For task
procedures, use `RECIPES.md`. For the generated inventory, use `INDEX.md`.

## Product in one page

AscendPlatform is an agency-managed AI growth platform. The agency operates it and each
client gets an isolated workspace. The product combines:

- Business Profile -> AI knowledge base and tone
- AI Studio and background agent jobs
- Websites, landing pages, blogs, emails and media
- Forms, contacts, conversations and chatbots
- Analytics, hosting, credits and approvals
- A private agency console for onboarding, support and infrastructure

## Two surfaces

| Surface | Route prefix | Audience | Rule |
|---|---|---|---|
| Client Workspace | `#/app/*` | client_admin / member | Never show agency-only data or controls |
| Platform Console | `#/platform/*` | owner only | Router redirects clients to their workspace |

Roles: `owner` (agency), `client_admin` (workspace admin), `member` (read/use). A user is
attached to one `org_id`; an owner is attached to none and can act as a client through
the workspace switcher (`POST /api/session/org`).

## Runtime

```text
browser -> Caddy (TLS)
          -> ASGI: FastAPI (SSE/providers) + Flask via a2wsgi
             -> PostgreSQL through SQLAlchemy Core / db.Connection
             -> Redis + arq worker when JOB_BACKEND=redis
             -> Caddy admin API / SSH hosts for site provisioning
             -> external providers (LLMs, Pexels, Meta, GHL, Google, SMTP)
```

Entrypoints:

- `asgi.py` — production ASGI app; FastAPI owns `/api/jobs/<id>/stream` and
  `/api/providers`, then mounts Flask at `/`.
- `app.py` — almost all HTTP routes, auth, tenancy, operations and admin API.
- `worker.py` — arq task registration; `runner_loop.py` / `jobs.py` for local execution.
- `alembic/` + `db.metadata` — schema and migrations.

Startup order: `alembic upgrade head` in the app container; `db.init_db()`
(`metadata.create_all`) wherever `db` is imported. Migrations must use
`checkfirst=True` because either path can create tables first.

## Request flow

1. Browser calls `/api/*` with the signed session cookie.
2. Flask decorator (`login_required`, `client_admin_required`, `owner_required`) calls
   `_session_account()`, which reloads the user row.
3. `current_org()` reads only the session; it never trusts a request `org_id`.
4. Service modules query through `db.get_connection()` and enforce `can_access_org()`.
5. Side effects go through policy/approval or a scoped external client.
6. Errors are raised as named service errors and mapped to 400/403/404; cross-tenant
   resources normally answer 404.

## Domain modules

The generated `INDEX.md` has the full route/settings/table inventory. This is the map by
responsibility:

### Identity and tenancy

- `orgs.py` — clients (tenants), plans, status, first admin.
- `user_service.py` — users, roles, status, password, last-owner/last-admin guards,
  `user_events`. Sign-in has a per-IP and per-(IP, email) failure throttle and pays the
  password-hash cost even for an unknown address, so response time is not an
  account-enumeration oracle.
- `invites.py` — hashed single-use invitations.
- `password_resets.py` — hashed, expiring reset tokens and email; the request route
  is also capped per caller address, on top of the per-account throttle.
- `settings.py` — platform/client settings catalogue and resolution.

### AI and jobs

- `agents.py` — roster, team construction, execution.
- `llm.py` — provider clients, live provider probes and usage capture.
- `jobs.py` — job rows/events, task registry, local/redis submission. Event appends
  are serialised per job (PostgreSQL advisory lock) so concurrent writers cannot
  produce the same `seq`; `cancel_job` reports whether it actually changed a job.
- `worker.py`, `scheduler.py` — background execution and scheduled work. The
  coordinator tick runs due automations, delayed wakes, alert evaluation, weekly
  reviews, DNS re-checks and the monthly credit grant. It is independent of
  `SITE_LOG_SYNC_HOURS`, takes a PostgreSQL advisory lock so only one uvicorn worker
  performs a tick, and `SCHEDULER_ENABLED=0` disables all of it. Every due/wake/grant
  claim is atomic, so two workers cannot start or grant the same thing twice.
- `automations.py` — validated step graphs, event/schedule/manual triggers, parked
  approval gates, delayed wakes. A proposal carries its own trigger; approving it saves
  and enables the automation. Form triggers are scoped by `form_id`.
- `manager.py`, `assistant_sessions.py`, `studio.py` — manager chat, Studio state and
  assistant sessions.
- `recommendations.py` — deterministic, evidence-backed next steps from tenant-scoped
  signals (site/form/CRM/chat/analytics/content), a 12-hour cached batch, a manual
  refresh, and an optional `recommendations` job that lets the model rank/reword only
  real candidates. The deterministic batch is the fallback, so a dashboard still works
  with no provider. A manual refresh is coalesced for one minute per client, and an
  open refinement job is not duplicated. `GET /api/recommendations` and the
  refresh/dismiss/done routes are login-scoped; the batch is shared by all users of the
  client, and member responses exclude `audience=admin` cards.
- `tools/` — agent tools; every side-effecting tool must respect policy and approvals.
  An agent-initiated `publish_page`/`publish_site` asks for the same approval card as
  the Publish button; the button and an explicit voice "yes" record the operator's
  decision instead of waiting, which is why they pass `preapproved`.
  `list_automations`, `propose_automation`, `apply_automation` let the manager build and
  turn on an automation after one user approval. `fetch_page` returns a structured page
  check, `fetch_page_html` returns capped raw HTML from an allowed client domain, and
  `read_deliverable` returns a draft's source only when the job's `org_id` owns it. The
  last two are read-only and in the roster for markup inspection; neither can write.
  Marketing tools are split by surface: `tools/forms.py` reads/updates forms and form
  responses; `tools/chat.py` lists/creates/updates chat assistants and attaches one to a
  site; `tools/manager.py` lists contacts and reads/updates email templates; and
  `tools/recommendations.py` exposes the read-only `recommend_next_steps` tool so a
  Manager question uses the same cached evidence instead of inventing ideas. The shared
  `tools/access.py` gate keeps client-admin writes aligned with the console screens, and
  every tool takes its org from the job row.

### Content and website

- `deliverables.py` — blog/funnel/email/website/proposal/asset records.
- `builds.py`, `build_service.py`, `build_guides.py` — guided build sessions.
- `site_builder.py`, `sites.py`, `provisioner.py`, `versions.py` — site records, page
  snapshots, DNS/vhost/provisioning. `domain_rules.py` is the one hostname rule used by
  the UI, `sites.create_site`/`update_site` and the agent tools; a new entry point must
  call it rather than trusting a dotted string. A site may only link a chat assistant
  owned by the same org; `widget_embed.py` injects that bot's `<script>` before
  `</body>` when provisioning, publishing or writing an index page.
- `site_logs.py`, `analytics.py`, `ga4.py`, `analytics_oauth.py` — traffic sources.
  `analytics_oauth.py` is the unified Google grant (client or agency, encrypted tokens);
  `ga4_admin.py` discovers properties/accounts and creates properties/streams/key
  events/audiences, plus the Google account-provisioning ticket; `gtm_admin.py` discovers
  containers and creates/publishes GA4 tags; `analytics_setup.py` orchestrates the
  idempotent setup and reuses an existing property/container; `analytics_consent.py`
  records the versioned, revocable one-click waiver. `tag_install.py` injects the GTM
  snippet at publish time and into already-live pages. `bigquery.py` reads the GA4 export
  into daily aggregate
  `bq_events`, `bq_pages` and `bq_acquisition` facts. `analytics_alerts.py` produces
  explainable period-over-period insights, per-client alert rules and the alert inbox; a
  scheduled `analytics_alerts` job evaluates it; the scheduler also runs the 180-day
  audit/alert housekeeping. The Manager's `setup_analytics` tool either drafts an approval
  or runs the setup when the client's `ANALYTICS_AUTONOMY` setting is on. `ga4.py` writes
  curated daily reports into `analytics_facts` plus the legacy `metrics_daily` series.

### CRM and communication

- `contacts.py`, `contact_sync.py`, `workspace_sync.py`, `opportunity_sync.py`.
- `forms.py`, `forms_engine.py`, `forms_page.py` — forms/surveys/quizzes. The form
  builder exposes description, accent, button label, success text and CRM mapping;
  `tools/forms.py` gives the Manager the same surface with an id-preservation rule when
  answers exist.
- `bots.py`, `widget_embed.py` — tenant-scoped chat assistant records and the public
  widget injection used by provisioning and publishing. `tools/chat.py` is the agent
  surface; channel tokens, webhooks and provider keys never reach it.
- `meta.py` (Messenger/Instagram/WhatsApp), `platform_mail.py`, `notifications.py`.

### Billing and operations

- `usage.py`, `rates.py` — token/voice/image usage, credits, rate card.
- `servers.py`, `actions.py` — SSH fleet and approval gate.
- `bugs.py` — private agency issue tracker.
- `media_library.py` — uploaded media with validation and thumbnails.

## Data model groups

- **Tenants:** `orgs`, `users`, `user_events`, `client_settings`. `users` carries the
  per-account onboarding and versioned Studio-tour completion flags.
- **AI/runtime:** `agents`, `jobs`, `job_events`, `job_artifacts`, `assistant_sessions`,
  `assistant_messages`, `voice_sessions`, `agent_tools`, `automations`,
  `automation_runs`, `automation_proposals`, `recommendation_batches`.
- **Content:** `deliverables`, `deliverable_versions`, `site_versions`, `media`,
  `build_sessions`, `business_profiles`.
- **Hosting:** `sites`, `ssh_hosts`, `action_approvals`. Hot-path indexes are
  declared in `db.py` and mirrored by migration `0043_hot_indexes` (`jobs`,
  `deliverables`, `sites`, `automation_runs`).
- **CRM/forms:** `contacts`, `contact_sync_state`, `ghl_object_links`, `forms`,
  `form_responses`.
- **Analytics:** `data_sources`, `oauth_connections`, `analytics_properties`,
  `tag_containers`, `analytics_facts`, `analytics_actions`, `analytics_consents`,
  `analytics_alert_rules`, `analytics_alerts`, `metrics_daily`, `sync_runs`.
- **Billing:** `usage_events`, `credit_ledger`, `credit_balances`, `rate_card`,
  `usage_reconciliations`.
- **Platform:** `settings`, `settings_events`, `mail_senders`, `invites`, `password_resets`,
  `bugs`, `bug_events`.

Queries use `?` placeholders through `db.Connection`; do not introduce an ORM. A new
table must be added to `db.metadata`, a migration, `test_db_dialect` and the migration
chain.

## Settings and secrets

`settings.py` is the source of truth. Resolution is client override (only client-scope
fields) -> panel -> environment -> default. `all_for_ui(org_id)` hides platform groups
from clients entirely.

### Public surfaces

`/api/chat`, `/api/public/*`, `/api/auth/*` and `/api/invites/accept` are capped at
256 KB per request; the widget rejects a turn or history entry over 4,000 characters
and is rate-limited per caller. `_client_ip()` trusts `X-Real-IP` (Caddy overwrites it
with `{remote_host}`) and never the first `X-Forwarded-For` value. `MAX_REQUEST_BYTES`
is the larger global ceiling that media uploads need.

`/api/health` is a liveness probe only (`status`, `brand`). It does not expose counts,
the job backend or the database engine. FastAPI's `/api/docs` and
`/api/openapi.json` are off unless `API_DOCS=1`; the schema describes every route and
parameter and needs no session.

- Platform scope: model/provider keys, Pexels, safety/actions, platform email/GHL,
  OAuth client credentials, hosting, usage policy, infrastructure.
- Client scope: the client-visible SMTP group (`Email (SMTP)`) and
  `ANALYTICS_AUTONOMY` / `ANALYTICS_ALERTS_ENABLED`. `GHL_CLIENT_*` is a
  backend-only per-client integration and must never appear in a client panel or write.
- Structural, `.env` only: `DATABASE_URL`, `SECRET_KEY`, `JOB_BACKEND`, `REDIS_URL`,
  `SITE_ROOT`, `CADDYFILE`, `ARTIFACTS_DIR`, `HOST`/`PORT`, bootstrap admin values.

Secrets are never returned by settings APIs; write-only fields never pre-fill. Never put
a secret, token or provider key into a URL, log line or client-facing error.

## Frontend

`admin/admin.js` is a no-build SPA:

- `AREAS.app` and `AREAS.platform` define nav groups and leaves. Client admins and
  members render a Studio-first workspace menu with one **More** disclosure; the platform
  owner keeps the full grouped workspace nav for cross-client work.
- `/api/deliverables` and `/api/sites` accept `summary=1`: list metadata only, without
  the document/page HTML. Dashboards and pickers use it; the editor and preview fetch
  the one row they open from `/api/deliverables/<id>` or `/api/sites/<id>`.
- Hash routes map to a `view*` function; preserve old hashes when relabeling.
- Shared helpers: `el`, `api`, `formModal`, `toast`, `empty-state`, badges.
- The form builder exposes accent colour and submit-label settings; the site editor
  offers a dropdown of the client's chat assistants instead of a raw bot id.
- The dashboard renders an evidence-backed **Recommended next steps** card when the user
  has a client selected; the Studio welcome chips use the same cached batch. A Studio
  chip only inserts text into the composer. Dashboard **Ask AI** starts a normal Manager
  turn with that action; outward changes still go through the existing approval gates.
- `admin/admin.css` holds the design tokens and component patterns.
- `admin/index.html` holds the static shell and icon sprite.
- JS role gates must mirror server gates; hidden UI is not a security boundary.
- Tests: `tests/nav_groups.mjs`, `tests/audit_ui.py`, plus JS harnesses in preflight.

## Testing and preflight

- Tests are direct Python scripts with their own `main()`, not pytest.
- Each suite must be re-runnable; use per-run suffixes and clean up fixtures.
- `tests/preflight.py` is the release gate: imports, compose/Caddy/deploy checks, JS
  parsing/harnesses, migrations, fast suites and the AI index check.
- Add new fast suites to `FAST_SUITES`; add JS harnesses to `check_js()`.
- Marketing/tool coverage is pinned by `test_marketing_tools`, `test_chat_tools` and
  `test_site_tool_scope`; the last one covers cross-tenant site reads and writes.
  `test_recommendations` pins deterministic evidence, the 12-hour cache/fingerprint,
  tenant isolation, dismiss/done and the AI-output validator.
- Run `tests/preflight.py` before claiming completion.

## Deployment and operations

- `docker-compose.yml` + `Dockerfile` define the stack; Caddy is the only public edge.
- `deploy.sh` rebuilds, recreates, checks public health and rolls back on failure.
- `deploy/backup.sh` / `deploy/restore.sh` are the encrypted backup and fail-closed restore
  path. `docs/operations.md` is the agency runbook.
- Background sweeps are single-owner across the two uvicorn workers via PostgreSQL
  advisory locks. The per-process `_started` flag is not enough on its own; do not
  remove the lock wrapper when adding a new sweep.
- `ALLOW_LIVE_ACTIONS` is blank by default. Live actions still require approval.

## Known high-risk areas

- Cross-tenant access: check every new route and service with `can_access_org()`.
- Client role visibility: members must not receive admin-only data or actions.
- Background jobs: create and submit; do not leave `queued` forever.
- Settings: platform secrets must not leak into client payloads.
- Migrations: fresh `create_all` and Alembic upgrade paths must both work.
- External integrations: network calls must be testable via injected/fake transports.
- UI files are large; search for the view/component before editing.

## Source-of-truth hierarchy

1. `AGENTS.md` — agent rules.
2. This file + `RECIPES.md` — current architecture/procedures.
3. `docs/ai/INDEX.md` — generated inventory.
4. Source + tests — final truth.
5. `CONTEXT.md` / `SYSTEM_PLAN.md` — history and rationale; search, do not read whole.
