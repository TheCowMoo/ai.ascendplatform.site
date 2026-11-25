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
  `user_events`.
- `invites.py` — hashed single-use invitations.
- `password_resets.py` — hashed, expiring reset tokens and email.
- `settings.py` — platform/client settings catalogue and resolution.

### AI and jobs

- `agents.py` — roster, team construction, execution.
- `llm.py` — provider clients and usage capture.
- `jobs.py` — job rows/events, task registry, local/redis submission.
- `worker.py`, `scheduler.py` — background execution and scheduled work.
- `automations.py` — validated step graphs, event/schedule/manual triggers, parked
  approval gates, delayed wakes. A proposal carries its own trigger; approving it saves
  and enables the automation. Form triggers are scoped by `form_id`.
- `manager.py`, `assistant_sessions.py`, `studio.py` — manager chat, Studio state and
  assistant sessions.
- `tools/` — agent tools; every side-effecting tool must respect policy and approvals.
  `list_automations`, `propose_automation`, `apply_automation` let the manager build and
  turn on an automation after one user approval.

### Content and website

- `deliverables.py` — blog/funnel/email/website/proposal/asset records.
- `builds.py`, `build_service.py`, `build_guides.py` — guided build sessions.
- `site_builder.py`, `sites.py`, `provisioner.py`, `versions.py` — site records, page
  snapshots, DNS/vhost/provisioning.
- `site_logs.py`, `analytics.py`, `ga4.py`, `analytics_oauth.py` — traffic sources.

### CRM and communication

- `contacts.py`, `contact_sync.py`, `workspace_sync.py`, `opportunity_sync.py`.
- `forms.py`, `forms_engine.py`, `forms_page.py`.
- `meta.py` (Messenger/Instagram/WhatsApp), `platform_mail.py`, `notifications.py`.

### Billing and operations

- `usage.py`, `rates.py` — token/voice/image usage, credits, rate card.
- `servers.py`, `actions.py` — SSH fleet and approval gate.
- `bugs.py` — private agency issue tracker.
- `media_library.py` — uploaded media with validation and thumbnails.

## Data model groups

- **Tenants:** `orgs`, `users`, `user_events`, `client_settings`.
- **AI/runtime:** `agents`, `jobs`, `job_events`, `job_artifacts`, `assistant_sessions`,
  `assistant_messages`, `voice_sessions`, `agent_tools`, `automations`,
  `automation_runs`, `automation_proposals`.
- **Content:** `deliverables`, `deliverable_versions`, `site_versions`, `media`,
  `build_sessions`, `business_profiles`.
- **Hosting:** `sites`, `ssh_hosts`, `action_approvals`.
- **CRM/forms:** `contacts`, `contact_sync_state`, `ghl_object_links`, `forms`,
  `form_responses`.
- **Analytics:** `data_sources`, `oauth_connections`, `metrics_daily`, `sync_runs`.
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

- Platform scope: model/provider keys, Pexels, safety/actions, platform email/GHL,
  OAuth client credentials, hosting, usage policy, infrastructure.
- Client scope: the client-visible SMTP group (`Email (SMTP)`). `GHL_CLIENT_*` is a
  backend-only per-client integration and must never appear in a client panel or write.
- Structural, `.env` only: `DATABASE_URL`, `SECRET_KEY`, `JOB_BACKEND`, `REDIS_URL`,
  `SITE_ROOT`, `CADDYFILE`, `ARTIFACTS_DIR`, `HOST`/`PORT`, bootstrap admin values.

Secrets are never returned by settings APIs; write-only fields never pre-fill. Never put
a secret, token or provider key into a URL, log line or client-facing error.

## Frontend

`admin/admin.js` is a no-build SPA:

- `AREAS.app` and `AREAS.platform` define nav groups and leaves.
- Hash routes map to a `view*` function; preserve old hashes when relabeling.
- Shared helpers: `el`, `api`, `formModal`, `toast`, `empty-state`, badges.
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
- Run `tests/preflight.py` before claiming completion.

## Deployment and operations

- `docker-compose.yml` + `Dockerfile` define the stack; Caddy is the only public edge.
- `deploy.sh` rebuilds, recreates, checks public health and rolls back on failure.
- `deploy/backup.sh` / `deploy/restore.sh` are the encrypted backup and fail-closed restore
  path. `docs/operations.md` is the agency runbook.
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
