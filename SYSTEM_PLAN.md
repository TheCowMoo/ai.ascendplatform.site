# CMBGrowthAI — System Plan

> Multi-agent AI growth platform (AutoGen) that also hosts its own websites.
> Living document: outline, planned updates, and known deficiencies.

Last updated: 2026-10-14
Status: Stage 1 delivered — rebrand, left-sidebar shell, Business Profile.
Stack: staged migration to FastAPI + Postgres + arq/Redis + Caddy (see §15).

---

## 1. Purpose

Extend the existing Chatbot Manager from "one DeepSeek chatbot per client" into a
platform where:

1. **Multiple AI providers** (Claude, DeepSeek, Gemini) are used together.
2. **Agents are designatable** — each role (Planner, Coder, Image Artist,
   Reviewer, Operator) is bound to a provider + model + tool set via the UI.
3. An **AutoGen team** orchestrates those agents to produce real artefacts
   (code, websites, emails, images).
4. The system can **host websites itself** — a user points their DNS at our
   server, configures the site in our admin UI, and we provision the vhost +
   SSL and hand back the live URL.
5. Actions with side effects (file writes, provisioning, sending) run behind a
   **policy engine + human approval gate** with a full audit trail.

---

## 2. Current system (as-built)

| File | Role |
|------|------|
| `app.py` | Flask backend: auth, bots CRUD, chat, Meta webhooks, static serving |
| `db.py` | SQLite schema + helpers (`users`, `bots`, `messages`, `channels`, `meta_events`) |
| `meta.py` | Meta Graph API + webhook signature verification (urllib only) |
| `admin/` | Vanilla-JS SPA, hash router, no build step |
| `widget/` | Embeddable chat widget |
| `deploy/` | systemd unit, nginx snippets, `install.sh` |
| `knowledge/` | Legacy KB seeded into the first bot |

**Runtime:** Flask + Gunicorn on `127.0.0.1:5000`, SQLite, one LLM (DeepSeek via
the OpenAI SDK). No agents, no tools, no job system.

**Existing "action" precedent:** the model emits `WEBHOOK_PAYLOAD: {...}` and the
server executes it (`_extract_webhook_payload` -> `_send_webhook_async`). The new
tool system generalises this idea.

---

## 3. Target architecture

```
[ Widget / Meta DMs ] --> /api/chat   (unchanged: fast single-shot DeepSeek)
                                  |
[ Admin: #/agents, #/jobs ] --> /api/agents, /api/jobs
                                  |
                            JobRunner (background asyncio thread)
                                  |
                             agents.py  (AutoGen team built from the roster)
                               Planner(Claude) -> Coder(DeepSeek)
                               -> Image Artist(Gemini) -> Reviewer(Gemini)
                               -> Operator(Claude, holds the action tools)
                                  |
                             tools/ registry  (granted per agent)
                               |- site_provision  -> provisioner.py -> Target
                               |- image_generate  -> Gemini native image
                               |- send_email      -> smtplib
                               |- publish         -> Meta Graph (meta.py)
                               `- ssh_run/ssh_write (remote Target)
                                  |
                             SQLite: agents, jobs, job_events, job_artifacts,
                                     sites, ssh_hosts, action_approvals
```

### Provenance during generation

Prompt/system text in this project is authored in-repo and carries no hidden
instructions. All external content (web pages, Meta messages, uploaded docs) is
treated as untrusted and must never be able to directly trigger a write/exec
tool without human approval.

---

## 4. Subsystems

### 4.1 Multi-provider LLM (`llm.py`)
- `anthropic` -> `autogen_ext.models.anthropic.AnthropicChatCompletionClient`
  (`ANTHROPIC_API_KEY`)
- `deepseek` -> `autogen_ext.models.openai.OpenAIChatCompletionClient`
  (`base_url=https://api.deepseek.com`, explicit `model_info`)
- `gemini` -> `OpenAIChatCompletionClient` against Gemini's OpenAI-compatible
  endpoint, with explicit `model_info` (tool-calling capability).
- `GET /api/providers` reports which providers are configured (never leaks keys).

### 4.2 Designatable agents (`agents.py` + `agents` table + `#/agents`)
Each row = one AutoGen `AssistantAgent`: name, role/system prompt, provider,
model, tools (JSON allowlist), turn order, enabled, temperature, max_tokens.
Team assembly: `SelectorGroupChat` (router picks speakers) or
`RoundRobinGroupChat`; stop with `TextMentionTermination("APPROVE") |
MaxMessageTermination(n)`.

Seeded roster:

| Agent | Provider / Model | Job | Tools |
|-------|------------------|-----|-------|
| Planner | Claude | Decompose task -> steps + file manifest | - |
| Coder | DeepSeek | Write code / markup / config | - |
| Image Artist | Gemini (image model) | Logos, hero images | `image_generate` |
| Copywriter | Claude | Emails, landing copy | - |
| Reviewer | Gemini | Critique; must end with `APPROVE` | read-only |
| Operator | Claude | Execute on the target | `site_provision`, `ssh_run`, `ssh_write`, `send_email`, `publish` |

### 4.3 Job engine (`jobs.py`)
AutoGen 0.4+ is async; Flask is sync WSGI. Bridge: one daemon thread hosting an
asyncio loop. `POST /api/jobs` returns immediately; the team's events are
persisted to `job_events`; the UI polls `GET /api/jobs/<id>/events?after=n`.
Optional separate `agent-worker.service` so runs survive app restarts.

### 4.4 Sites - self-hosted website hosting (`provisioner.py`, `sites` table, `#/sites`)
**No HestiaCP dependency.** Our system is the host.

Flow:
1. User adds a site in `#/sites` (domain, aliases, template, content, optional
   linked bot) -> row with `status = pending_dns`.
2. UI shows the exact DNS records to create (A -> `SERVER_IP`, and `www`).
3. Background checker resolves the domain and compares to `SERVER_IP`.
4. On match -> write nginx vhost -> `nginx -t` -> reload -> `certbot` (SSL).
5. `status = live`; UI shows `https://<domain>` with a copy button.

`provisioner.py` API: `suggest_dns`, `check_dns`, `add_vhost`, `issue_ssl`,
`remove_vhost`.

### 4.5 Target abstraction (key decision)
`provisioner` and the SSH tools run commands through a `Target`:

```
Target.run(argv)
  |- LocalTarget -> subprocess + narrow sudoers   (default: testing / now)
  `- SshTarget   -> paramiko to a remote web VPS  (future)
```

- **Now:** `PROVISION_TARGET=local` - sites hosted on the app's own server.
- **Later:** `PROVISION_TARGET=ssh://web-vps` - switch without a rewrite.
- `SERVER_IP` is per-target (the box that actually serves the site), so the
  "which A record to create" hint always reports the correct IP.

### 4.6 Privileged helper (security linchpin)
The app runs unprivileged (`nathan`) and must never get blanket root.
- `/usr/local/bin/site-provisioner` - root-owned script, fixed subcommands
  (`add <domain> <docroot>`, `ssl <domain>`, `remove <domain>`, `reload`),
  strict domain-regex validation, fixed docroot base, no shell interpolation.
- `/etc/sudoers.d/ai-chatbot-sites` - narrow sudo entry for that one script.
- The LLM never receives a general root shell for provisioning.

### 4.7 Action tools
- `tools/image.py` -> `image_generate(prompt, size)` via `google-genai`
  (`from google import genai`, `GEMINI_API_KEY`), model id configurable
  (default `gemini-2.5-flash-image`); saves PNG under
  `artifacts/images/<job>/`, returns the path for the Operator to deploy.
- `tools/email.py` -> `smtplib` (stdlib, matching the repo's urllib preference).
- `tools/publish.py` -> reuses `meta.py` Graph helpers.
- `tools/ssh.py` -> paramiko executor (`run`, `put_file`, `read_file`,
  `test_connection`) with host-key pinning.
- `tools/policy.py` -> command allowlist/blocklist, path confinement, timeouts,
  output caps.

---

## 5. Planned updates (build order)

| Step | Deliverable | Risk |
|------|-------------|------|
| 0 | Verify AutoGen installs (DONE: 0.7.5 resolves on Python 3.14; no 3.12 venv needed) | done |
| 1 | `llm.py` multi-provider + `/api/providers` (DONE - all 3 clients construct) | done |
| 2 | Job engine: `jobs.py` + `jobs`/`job_events`/`job_artifacts` + `#/jobs` (DONE - echo job runs end-to-end) | done |
| 3 | Agent roster: `agents.py` + `agents` table + `#/agents` | med |
| 4 | Sites: `provisioner.py` + `sites` + `#/sites` + helper + sudoers + auto-DNS/SSL (local target) | med |
| 5 | SSH tool + `ssh_hosts` + `#/servers` (enables the future remote target) | med |
| 6 | Specialist tools: image (Gemini), email, publish | med |
| 7 | Hardening, tests, `requirements.txt`/`.env.example`/`deploy`/README | - |

---

## 6. Data model additions (SQLite, additive migrations)

- `agents` - id, name, provider, model, system_prompt, tools(JSON), turn_order,
  enabled, temperature, max_tokens, timestamps
- `jobs` - id, title, prompt, requester_id, status, team_mode, timestamps, error
- `job_events` - id, job_id, seq, source, type, content, created_at
- `job_artifacts` - id, job_id, kind, path, mime, created_at
- `sites` - id, owner_id, domain, aliases, docroot, template, content_html,
  linked_bot_id, status, ssl_status, dns_last_check, last_error, timestamps
- `ssh_hosts` - id, label, host, port, username, private_key_path,
  host_key_fingerprint, allowed_root, enabled, created_at
- `action_approvals` - id, job_id, tool, args_json, status, requested_at,
  decided_at, decided_by

Migration style: `CREATE TABLE IF NOT EXISTS` + `PRAGMA table_info` checks,
matching `db.py`.

## 7. API additions

```
GET  /api/providers
GET/POST /api/agents            PUT/DELETE /api/agents/<id>
GET/POST /api/jobs              GET /api/jobs/<id>
GET  /api/jobs/<id>/events      POST /api/jobs/<id>/{cancel,approve,deny}
GET/POST /api/sites             PUT/DELETE /api/sites/<id>
POST /api/sites/<id>/check      POST /api/sites/<id>/provision
GET  /api/sites/<id>/dns-hint
GET/POST/DELETE /api/ssh-hosts  POST /api/ssh-hosts/<id>/test
```

All behind the existing `@login_required` / `@admin_required` decorators.

---

## 8. Admin UI additions (`admin/admin.js` hash routes)

- `#/agents` - roster editor (designate role -> provider/model/tools)
- `#/jobs` and `#/jobs/<id>` - live transcript with approve/deny
- `#/sites` - website manager with DNS hints + status timeline
- `#/servers` - SSH host manager

Reuse existing `el()`, `api()`, `toast()`, `.panel-card`, `.bot-card` CSS classes
(no build step).

---

## 9. Config / env reference (new keys)

```
ANTHROPIC_API_KEY=            # Claude
GEMINI_API_KEY=               # Gemini (chat + image)
GEMINI_IMAGE_MODEL=gemini-2.5-flash-image
AGENT_ROUTER_PROVIDER=anthropic
AGENT_ROUTER_MODEL=
SERVER_IP=                    # public IP the sites' DNS must point to
PROVISION_TARGET=local        # local | ssh://<host>
SITE_ROOT=/var/www/sites
ACME_EMAIL=
SITE_CHECK_INTERVAL=300
PROVISION_HELPER=/usr/local/bin/site-provisioner
SSH_ENABLED=0
SSH_AUTO_APPROVE_READONLY=1
SSH_CMD_TIMEOUT=30
SSH_MAX_OUTPUT=20000
SMTP_HOST= / SMTP_PORT= / SMTP_USER= / SMTP_PASS= / SMTP_FROM= / SMTP_TLS=
ARTIFACTS_DIR=artifacts
CLOUDFLARE_API_TOKEN=         # optional: auto-create DNS records
PEXELS_API_KEY=               # stock photography (search_photos / fetch_photo)
PEXELS_PER_PAGE=5 / PEXELS_TIMEOUT=20 / PEXELS_MAX_BYTES=8000000
```

Anything marked `live` in `settings.SPEC` can also be set from the admin
**Settings** tab (§22), which stores it in the `settings` table and takes
precedence over the environment — no restart required.

```

---

## 10. Security model

- Dedicated unprivileged account; key-based SSH only; host-key pinning.
- Narrow sudoers entry for the single provisioning helper (no blanket root).
- Path confinement for all file writes; docroot base fixed.
- Command allowlist + hard blocklist; per-command timeout + output cap.
- **Approval gate:** write/exec actions create an `action_approvals` row and wait
  for a human decision in `#/jobs`; read-only commands may auto-approve.
- `issue_ssl` is blocked unless `check_dns` resolved to the target's IP (protects
  Let's Encrypt rate limits), mirroring Hestia's behaviour.
- Full audit: every command + stdout/stderr + exit code persisted.
- Untrusted content (web/Meta/email) can never auto-trigger SSH/provisioning.
- Secrets (API keys, Meta tokens, SSH keys) never sent to a model; redacted in
  logs and artefacts.

## 11. Deficiencies / known gaps / risks

**Resolved during Step 0**
- ~~Local Python 3.14 unsupported by AutoGen~~ **RESOLVED**: `autogen-agentchat`
  **0.7.5**, `autogen-ext[openai,anthropic]` 0.7.5, `paramiko` 5.0.0 and
  `google-genai` 2.30.1 all resolve and install on Python **3.14.3**. No 3.12
  venv required.
- Note: `requirements.txt` contains `gunicorn`, which does **not** install on
  Windows. Local dev installs should exclude it (or use `requirements-dev.txt`);
  the VPS install path keeps it.

**Prerequisite / to verify at runtime**
- Gemini has **no native AutoGen client** in `autogen-ext`; we rely on the
  OpenAI-compatible endpoint + hand-set `ModelInfo`. Must confirm the chosen
  Gemini chat model accepts tool calls, and that DeepSeek's function calling
  behaves inside AutoGen.
- Google's image API surface has recently changed (Nano Banana / Interactions
  API, shifting model IDs) -> the image model id must stay configurable and be
  verified at runtime.
- `autogen-ext` needs **both** extras: `[anthropic]` for the Claude client and
  `[openai]` for `tiktoken`, which `OpenAIChatCompletionClient` (DeepSeek +
  Gemini) requires. Encountered during Step 1; pinned in `requirements.txt` as
  `autogen-ext[anthropic,openai]`.

**Architecture / debt**
- `app.py` is already ~900 lines and will grow; new subsystems must live in
  modules (`llm.py`, `agents.py`, `jobs.py`, `provisioner.py`, `tools/`) to avoid
  a monolith.
- SQLite is single-writer; long agent jobs + web traffic may contend. Acceptable
  now; revisit (WAL mode / Postgres) if concurrency grows.
- No automated tests exist yet -> add pytest + Flask test client + a dry-run SSH
  executor for CI. (First smoke test added: `tests/smoke_jobs.py`.)
- Jobs are queued **in-process**: under gunicorn's `--workers 2`, a job is run by
  the worker that received the POST. Jobs left `queued`/`running` by a crash are
  not auto-resumed yet (future: a dedicated `agent-worker` service polling the
  DB). The runner marshals work with `call_soon_threadsafe` onto its own asyncio
  loop; an earlier `asyncio.to_thread(queue.get)` design leaked a non-daemon
  executor thread and hung interpreter exit — fixed during Step 2.
- AutoGen pulls a heavy dependency graph (~opentelemetry, tiktoken, protobuf,
  anthropic, cryptography) vs the current 5-line requirements; contained to the
  agent subsystem (widget/Meta path stays on the openai SDK).

**Security**
- Hosting sites on the app's own box (the chosen test setup) means a compromised
  site shares a host with `.env` (all provider keys + client Meta tokens) and
  `data/app.db`. Acceptable for local testing; **move to a dedicated web VPS
  before hosting any client/untrusted site** (the Target abstraction makes this a
  config change).
- Unattended full-auto mode would make prompt injection a deployment risk; the
  approval gate is the mitigation.
- Multi-agent loops cost several API calls per job -> need `MaxMessageTermination`
  and per-job command/step caps.

**Product gaps (not yet designed)**
- Multi-tenant isolation for sites (per-site OS user vs shared `www-data`).
- Site content model: static HTML only, or templating/CMS?
- Email deliverability (SPF/DKIM/DMARC) for the email tool.
- Backup/restore for `sites` + `artifacts` + SQLite.

---

## 12. Decision log

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | Runtime = **AutoGen** (`autogen-agentchat` + `autogen-ext`) | Requested; gives agents, teams, tools, HITL out of the box |
| 2 | Agents are **designatable** in the DB/UI | Let roles map to providers (e.g. "Gemini = image creator") without code changes |
| 3 | Image generation = **Gemini native** | Keeps the Gemini -> image mapping; tool-based (not a chat completion) |
| 4 | **No HestiaCP** - our system hosts sites itself | Hestia was only an example; we own provisioning + config UI |
| 5 | **Target abstraction**: local now, SSH web-VPS later | Start fast for testing, migrate without a rewrite |
| 6 | Actions **approval-gated** by default | SSH/provisioning by an LLM is the highest-risk surface |
| 7 | Existing widget + Meta chat path **unchanged** | Fast, cheap, already working; agents are additive |
| 8 | Local dev on **Python 3.14** (no 3.12 venv) | Verified: full stack resolves/installs on 3.14.3 |

---

## 13. Open questions

- Site content: static HTML only, or a template/CMS layer?
- Sites multi-tenant isolation strategy (shared `www-data` vs per-site user)?
- Enable DNS auto-creation (Cloudflare token) or keep manual DNS pointing?
- Which model IDs to pin for Claude/Gemini at launch, and which model routes the
  agent team?
- Separate `agent-worker` systemd unit, or run jobs in-process?

---

## 14. Progress log

| Step | Status | Evidence |
|------|--------|----------|
| 0 | DONE | `venv` created on Python 3.14.3; `autogen-agentchat` 0.7.5, `autogen-ext[anthropic,openai]` 0.7.5, `paramiko` 5.0.0, `google-genai` 2.30.1 all install. No 3.12 venv needed. |
| 1 | DONE | `llm.py` added; `GET /api/providers` -> 200 with DeepSeek/Anthropic/Gemini; all three clients construct (`OpenAIChatCompletionClient`, `AnthropicChatCompletionClient`, `OpenAIChatCompletionClient`). `/api/health` reports `providers_configured`. |
| 2 | DONE | `jobs`/`job_events`/`job_artifacts` tables; `jobs.py` runner; `/api/jobs` (+`/<id>`, `/events`, `/cancel`); admin `#/jobs` and `#/jobs/<id>` with live polling and cleanup hooks. `tests/smoke_jobs.py` passes: job created -> run -> `done` -> 6 ordered events. `node --check admin/admin.js` clean. |
| S1 | DONE | Rebrand -> **CMBGrowthAI**; left-sidebar tab shell; **Business Profile** (table, `profile.py`, `/api/profile*`, `#/profile`, KB inheritance + widget branding). `tests/smoke_profile.py` + `tests/smoke_jobs.py` pass. |
| S2 | DONE | Dual-dialect DB layer (`DATABASE_URL`: SQLite or PostgreSQL), SQLAlchemy Core metadata, Alembic baseline, Docker Compose (caddy/app/db/redis), Caddyfile, Hestia-free `install.sh`. New tests: `test_db_dialect.py`, `smoke_stats.py`. |
| S3 | DONE | Redis + arq worker (`worker.py`, compose `worker` service, graceful in-process fallback); SSE job streaming (`/api/jobs/<id>/stream`); AutoGen `agent_team` + designatable roster (`agents.py`, `agents` table, `#/agents`, `tools/` registry). New tests: `smoke_agents.py`, `smoke_sse.py`, `smoke_backend_fallback.py`. |
| S4 | DONE | ASGI entry point (`asgi.py`: FastAPI + `a2wsgi` mount of Flask, shared signed-session bridge); SSE stream and `/api/providers` migrated to async FastAPI; LiteLLM gateway support in `llm.py` (+ optional compose `litellm` service and `litellm-config.yaml`); uvicorn replaces gunicorn. New tests: `smoke_asgi.py`, `smoke_llm_gateway.py`. |
| S5 | DONE | **Sites** (`sites.py`, `provisioner.py`, `#/sites`, DNS hints, Caddy vhosts + auto-TLS, local/SSH targets); **Servers** (`servers.py`, `tools/ssh.py` with host-key pinning, `#/servers`); **policy engine** (`tools/policy.py`) + **approval gate** (`actions.py`) + `#/jobs` approvals panel; agent tools `provision_site` / `write_site_content` / `check_site_dns` / `list_sites` / `generate_image` (Gemini) / `send_email`. Safe by default: dry-run until `ALLOW_LIVE_ACTIONS=1`. New tests: `test_policy.py`, `smoke_provisioner.py`, `smoke_sites.py`. |
| S6 | DONE | **Pexels** stock photography (`tools/pexels.py`: `search_photos` + `fetch_photo`, SSRF-locked to `images.pexels.com` over HTTPS, downloads approval-gated and path-confined, granted to Image Artist + Operator); **Settings tab** (§22): `settings` + `settings_events` tables, `settings.py` catalogue resolving panel → environment → default, `/api/settings` + `PUT` + `/test/<group>`, `#/settings` UI, and late-binding so a saved value applies with **no restart**. Fixed a real bug found against the live API: Pexels sits behind Cloudflare, which answers the default `Python-urllib` User-Agent with error 1010 (HTTP 403) — indistinguishable from a rejected key. Also fixed `worker.py` never loading `.env`. New tests: `smoke_pexels.py`, `smoke_settings.py`, `test_migrations.py`. All 15 suites green. |
| S7a | DONE | **Multi-tenant clients** (§24): `orgs` table + `org_id` on users/bots/sites/jobs/business_profiles; three roles (`owner` / `client_admin` / `member`) with `admin` kept as a migration alias; `current_org()` read only from the signed session; migration `0005_tenancy` (first ALTER + backfill revision; `test_migrations.py` still only covers an empty database — see §24); `db._prepare()` classifies DDL correctly, fixing a latent `ResourceClosedError` in `_ensure_columns`. Closed 8 cross-tenant leaks across sites, bots, jobs, the SSE stream, approvals, users, servers and the agent roster; cross-tenant reads answer 404 rather than 403. New test `smoke_tenancy.py` (two clients, attacks on every surface, plus the no-org login). All 16 suites green. |
| S7b | DONE | **Per-client profiles, settings and passwords** (§24 part 2): Business Profile keyed on `org_id` with the platform's own brand at org 0; `settings` fields gain a `platform`/`client` **scope** with client overrides in the new `client_settings` table, resolved client → panel → env → default and omitted from a client's panel entirely (`settings.get(..., org_id=)`, `all_for_ui(org_id)`); `tools/mail.py` sends through the job's client's SMTP; `POST /api/auth/password` + `POST /api/users/<id>/password`; migration `0006_client_scope`, which also drops the legacy `UNIQUE(owner_id)` that only *upgraded* databases carried (`db._drop_legacy_owner_unique`, table rebuild on SQLite). All 16 suites green. |

### Files added/changed so far
- S0/S1 added: `llm.py`, `jobs.py`, `profile.py`, `tests/smoke_jobs.py`,
  `tests/smoke_profile.py`, `SYSTEM_PLAN.md`, `venv/`
- S0 changed: `db.py` (3 tables), `app.py` (imports, `/api/jobs*`,
  `/api/providers`, runner start), `admin/admin.js` (Jobs nav + views + router),
  `admin/admin.css`, `requirements.txt`
- S1 changed: `db.py` (`business_profiles` + `bots.profile_id`/`use_profile_kb`),
  `app.py` (BRAND, `/api/profile*`, KB inheritance, widget branding fallback,
  profile seed, `profile_out`), `admin/admin.js` (sidebar shell, NAV, dashboard,
  `#/bots`, `#/profile`, conversations index, placeholders),
  `admin/admin.css` (shell/sidebar/profile styles), `admin/index.html`,
  `widget/index.html`, `README.md`
- S2 added: `Dockerfile`, `docker-compose.yml`, `Caddyfile`, `.dockerignore`,
  `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`,
  `alembic/versions/0001_initial_schema.py`, `tests/test_db_dialect.py`,
  `tests/smoke_stats.py`
- S2 changed: `db.py` (rewritten as the dual-dialect layer), `app.py`
  (portable analytics + timestamps + `IntegrityError`), `jobs.py`/`profile.py`
  (parameterised timestamps), `requirements.txt` (sqlalchemy, alembic, psycopg),
  `.env.example`, `.gitignore`, `deploy/install.sh`, and the three legacy
  `deploy/*` Hestia files (now DEPRECATED stubs)
- S3 added: `agents.py`, `tools/__init__.py`, `worker.py`,
  `alembic/versions/0002_agents_roster.py`, `tests/smoke_agents.py`,
  `tests/smoke_sse.py`, `tests/smoke_backend_fallback.py`
- S3 changed: `db.py` (`agents` table), `jobs.py` (shared `run_job`, `submit`
  dispatch, Redis fallback, traceback into the transcript), `app.py` (agents API,
  SSE endpoint, roster seeding, richer health), `admin/admin.js` (`#/agents`
  roster editor, SSE with polling fallback, team-mode picker),
  `admin/admin.css`, `docker-compose.yml` (`worker` service),
  `requirements.txt` (arq, redis), `.env.example`
- S4 added: `asgi.py`, `litellm-config.yaml`, `tests/smoke_asgi.py`,
  `tests/smoke_llm_gateway.py`
- S4 changed: `llm.py` (LiteLLM gateway), `app.py` (Flask SSE route removed, now
  FastAPI), `requirements.txt` (fastapi, uvicorn, a2wsgi), `Dockerfile` and
  `docker-compose.yml` (uvicorn `asgi:app`), `docker-compose.yml` (optional
  `litellm` service), `.env.example`, `tests/smoke_sse.py` (ASGI client)
- S5 added: `actions.py`, `provisioner.py`, `sites.py`, `servers.py`,
  `tools/policy.py`, `tools/ssh.py`, `tools/site.py`, `tools/media.py`,
  `tools/mail.py`, `alembic/versions/0003_sites_ssh.py`,
  `tests/test_policy.py`, `tests/smoke_provisioner.py`, `tests/smoke_sites.py`
- S5 changed: `db.py` (ssh_hosts / sites / action_approvals; `_bind` now refuses
  a parameter mismatch instead of binding None), `app.py` (sites, servers and
  approvals APIs), `agents.py` (Operator tool grants; `resolve(..., job_id)`),
  `tools/__init__.py` (builder signature + submodule registration),
  `admin/admin.js` (`#/sites`, `#/servers`, approvals panel, `site_provision`
  task), `admin/admin.css`, `Caddyfile` (admin API + site import), `Dockerfile`,
  `docker-compose.yml` (shared site/config volumes), `servers.py` (row-id bug),
  `.env.example`, `.gitignore`

---

## 15. Stack decision (research) & staged migration

**Laravel rejected.** AutoGen is Python-only (no PHP port), so an AI-first
product must keep a Python core. Laravel would force a two-runtime architecture
(PHP UI + Python agent service) with duplicated auth/models and two deployments.

| Layer | Choice | Why |
|-------|--------|-----|
| Web/API | **FastAPI + Uvicorn** | async-native (AutoGen is async), SSE streaming, typed contracts + OpenAPI; can mount Flask during migration |
| Agent | **AutoGen** | required; unchanged |
| Providers | direct clients -> **LiteLLM** | `autogen_ext.models` has no Gemini or LiteLLM client; LiteLLM unifies 100+ providers with retries, fallbacks, spend tracking |
| DB | **Postgres + SQLAlchemy 2.0 + Alembic** | real migrations, multi-writer, JSONB |
| Queue | **Redis + arq** | asyncio-native, restart-safe ("pessimistic execution"), cron |
| Edge/TLS | **Caddy** | automatic HTTPS replaces nginx **and** certbot **and** Hestia; per-site certs make the Sites tab near-free |
| Frontend | keep no-build vanilla JS + SSE | already works; sidebar tabs are a layout change |
| Deploy | **Docker Compose** (caddy, app, worker, redis, postgres) | reproducible; Ubuntu, no Hestia |

**Stages (no big-bang rewrite):** S1 features on the working app (done) -> S2
Caddy + Postgres -> S3 arq worker + SSE + agents -> S4 FastAPI + LiteLLM ->
S5 Sites/SSH/tools.

---

## 16. Business Profile (delivered in S1)

The single place a business describes itself; its content becomes the AI's
knowledge base and its branding/tone flow into bots and the widget.

- **Table** `business_profiles` (one per `owner_id`) — identity, branding, tone,
  audience, services(JSON), pricing, contact/hours, social(JSON), policies,
  faq(JSON), notes.
- **`profile.py`** — `ensure_profile`, `get_profile`, `update_profile`,
  `build_knowledge` (deterministic, sectioned KB text), `seed_sample_profile`.
- **Link** — `bots.profile_id` + `bots.use_profile_kb` (default 1). `generate_reply`
  prepends the profile KB and appends the brand voice; `/api/bots/<id>/public`
  falls back to the profile's `brand_color`/`logo_url`.
- **API** — `GET/PUT /api/profile`, `GET /api/profile/knowledge`,
  `POST /api/profile/seed-sample`.
- **UI** `#/profile` — sectioned form, repeatable Services/FAQ rows, colour
  picker, live widget-branding preview, and a *Knowledge base preview* panel
  showing the exact text the model receives.
- **Seeding** — first run seeds a profile for the first account from the bundled
  sample document.

Note: `new bots` store an EMPTY brand colour to mean "inherit from the Business
Profile"; the widget falls back to the profile colour/logo automatically.

---

## 17. Stage S2 delivered — PostgreSQL, Docker, Caddy (no Hestia)

**Database (`db.py`)**
- One `DATABASE_URL` selects the backend: `sqlite:///data/app.db` (dev) or
  `postgresql+psycopg://...` (production).
- The schema is defined once with **SQLAlchemy Core metadata**; `create_all()`
  emits dialect-correct DDL (`SERIAL` vs `INTEGER PRIMARY KEY`,
  `to_char(now(),'YYYY-MM-DD HH24:MI:SS')` vs `datetime('now')`).
- The existing `conn.execute(sql, params)` + `?`-placeholder style is preserved
  by a thin adapter (`_prepare` / `_bind`), which also translates
  `INSERT OR IGNORE` -> `ON CONFLICT DO NOTHING` and appends `RETURNING id` on
  PostgreSQL. Rows support both `row["col"]` and `row[0]`, so `as_dict` and
  `fetchone()[0]` keep working.
- Queries are dialect-neutral: `substr(created_at, 1, 10)` for date grouping and
  a Python-computed `days_ago(14)` cutoff replace SQLite-only date functions;
  `MIN(content)` makes the top-questions GROUP BY valid on PostgreSQL.
- `db.IntegrityError` covers both drivers.

**Migrations (Alembic)** — `alembic.ini`, `alembic/env.py` and a baseline
`0001_initial` revision that reuses `db.metadata` (single source of truth).
Verified: `alembic upgrade head` on a fresh database creates all 9 tables and
stamps `0001_initial (head)`.

**Deployment (Ubuntu, no Hestia)** — `Dockerfile`, `docker-compose.yml`
(caddy / app / db / redis) and `Caddyfile`. Caddy replaces nginx **and** certbot,
so TLS is automatic once DNS points here; the app container runs
`alembic upgrade head` before gunicorn starts. `deploy/install.sh` installs
Docker and brings the stack up. The legacy Hestia files are marked DEPRECATED.

**Verification**
- `tests/test_db_dialect.py` — placeholder rewriting, upsert translation, and
  SQLite + PostgreSQL DDL compiled **offline** by importing the layer under a
  PostgreSQL `DATABASE_URL` in a subprocess.
- `tests/smoke_stats.py`, `tests/smoke_profile.py`, `tests/smoke_jobs.py` all pass.
- **Not verified here:** `docker compose up` (Docker is unavailable on this
  machine) and a live PostgreSQL server. Both must be confirmed on the VPS.

---

## 18. Stage S3 delivered — agents, jobs on a real queue, SSE

**Designatable roster (`agents.py`, `agents` table, `#/agents`)**
- Each row becomes one AutoGen `AssistantAgent` with its own provider, model,
  system prompt, turn order and tool allowlist — so "Gemini = image creator" is a
  data change in the UI, not a code change.
- Seven agents are seeded on first run: Planner (Claude), Researcher (Gemini),
  Copywriter (Claude), Coder (DeepSeek), Image Artist (Gemini), Reviewer
  (Gemini, must say `APPROVE`), Operator (Claude, holds the tools).
- API: `GET/POST /api/agents`, `PUT/DELETE /api/agents/<id>`; `GET` also returns
  the tool catalogue, provider status, router config and message cap.
- Team assembly: `RoundRobinGroupChat`, or `SelectorGroupChat` with a router model
  (`AGENT_ROUTER_PROVIDER`). Termination is
  `TextMentionTermination("APPROVE") | MaxMessageTermination(AGENT_MAX_MESSAGES)`
  so a runaway loop cannot burn the API budget.
- `agent_team` is registered as a job task, so it runs on whichever backend the
  engine is using.

**Tool registry (`tools/`)**
- `resolve(names, emit)` turns roster tool names into AutoGen `FunctionTool`s;
  `unknown()` surfaces typos as a warning in the job transcript.
- S3 ships safe built-ins only (`save_note`, `current_time`). The side-effecting
  tools (Sites, SSH, email, publish) plug into the same interface in S5/S6 so the
  command policy and approval gates apply uniformly.

**Job execution backends (`jobs.py`, `worker.py`)**
- `local` (default): the existing daemon-thread runner.
- `redis`: enqueue onto an **arq** worker (`worker.py`, `python -m worker`) for
  durable, restart-safe runs. docker compose adds a `worker` service and sets
  `JOB_BACKEND=redis`.
- `jobs.submit()` degrades gracefully: if Redis is unreachable the job runs
  in-process and the transcript records why, so work is never silently dropped.

**Live transcript over SSE**
- `GET /api/jobs/<id>/stream` emits `text/event-stream` frames (`message`,
  `system`, `error`, `tool_call`, `tool_result`) plus a terminal `end` frame with
  the job status. `X-Accel-Buffering: no` keeps proxies from buffering.
- The UI uses `EventSource` and falls back to polling on error, so the transcript
  is live without breaking if streaming is blocked.

**Verification**
- `tests/smoke_agents.py` — roster CRUD, tool registry, name sanitising, and the
  **real AutoGen team run offline** via `ReplayChatCompletionClient` (5 messages,
  terminated by `APPROVE`), plus a clear failure when no provider keys are set.
- `tests/smoke_sse.py` — SSE content type, frames and terminal `end`; 404 path.
- `tests/smoke_backend_fallback.py` — `JOB_BACKEND=redis` with no Redis still
  completes the job and records the fallback.
- **Not verified here:** a live Redis server and the arq worker round-trip
  (no Docker/Redis on this machine). Each SSE stream also holds a gunicorn worker
  thread, so concurrent streams are bounded by `--threads`.

---

## 19. Stage S4 delivered — FastAPI ASGI + LiteLLM gateway

**ASGI composition (`asgi.py`)** — the migration is incremental by construction:
- A FastAPI app owns the routes that benefit most from async handling, then the
  existing Flask app is mounted at `/` via **`a2wsgi`**
  (`starlette.middleware.wsgi` is deprecated in Starlette 1.8, so a2wsgi is used).
- Starlette matches in order, so FastAPI routes win and the mount catches the
  rest. Route groups can therefore move over one at a time with no big-bang
  rewrite.
- **Session bridge:** FastAPI decodes the same signed session cookie Flask writes
  (`SecureCookieSessionInterface(...).get_signing_serializer(app)`), exposed as a
  `require_session` dependency. One login, two frameworks, no duplicated user store.

**Migrated so far**
| Route | Now served by | Why |
|-------|---------------|-----|
| `GET /api/jobs/{id}/stream` | FastAPI (`StreamingResponse`) | true async SSE; no worker thread parked, DB reads via `asyncio.to_thread` |
| `GET /api/providers` | FastAPI | read-only, gains OpenAPI docs |
| everything else | Flask (mounted) | unchanged |

FastAPI also publishes `/api/docs` and `/api/openapi.json`.

**LiteLLM gateway (`llm.py`)**
- Set `LLM_GATEWAY_URL` (e.g. `http://litellm:4000`) and every provider is reached
  through one OpenAI-compatible endpoint: a single client class, provider keys
  held by the gateway, plus spend tracking, retries and fallbacks.
- Model ids become `provider/model` (`deepseek/deepseek-chat`,
  `anthropic/claude-sonnet-4-5`); already-prefixed ids pass through unchanged.
- Unset (the default) keeps direct calls, and `is_configured()` correctly reports
  all providers ready when the gateway is on, even with no local keys.
- Optional `litellm` compose service behind the `gateway` profile, with
  `litellm-config.yaml`.

**Runtime** — production now serves the ASGI app: `uvicorn asgi:app` in the
Dockerfile and compose. `python app.py` still runs the Flask half for quick local
work, but it does not serve the migrated routes.

**Verification**
- `tests/smoke_asgi.py` — FastAPI route precedence (its `/api/providers` returns
  `gateway`, Flask's does not), Flask routes reachable through the mount, OpenAPI
  contains both paths, 401 without a session, and one cookie authenticating both
  halves.
- `tests/smoke_llm_gateway.py` — direct mode still requires a local key; gateway
  mode makes all three providers usable with no local keys, one client class,
  base URL pointed at the gateway.
- `tests/smoke_sse.py` rewritten against `TestClient(asgi.app)` and now also
  asserts the 401 path.
- Live uvicorn check: `/api/health` (Flask via mount), `/api/providers` (FastAPI),
  `/api/openapi.json`, and a full SSE run (7 frames, terminal `done`).
- **Not verified here:** the LiteLLM container itself (no Docker) and uvicorn's
  multi-worker mode under load.

---

## 20. Stage S5 delivered — Sites, Servers and the action gate

This is the capability the project was originally about: the AI creates a
website, deploys it, and hands back the URL.

**Safety model (the part that matters most)**

Everything with a side effect goes through two layers, in this order:

1. **`tools/policy.py`** — a pure, exhaustively-tested engine. Commands must be
   classifiable (known read-only or known mutating); deny-by-default; every
   chained segment is inspected individually; command substitution and backticks
   are refused outright; a deny-list covers wipes, credential files, user/privilege
   changes, pipe-to-shell and service control. `rm -rf` is matched *precisely*
   against `/` and top-level system directories, so legitimate cleanup inside the
   site root still works. Paths must resolve inside the allowed root, with parent
   traversal refused even when it would stay inside.
2. **`actions.py`** — the approval gate. `ALLOW_LIVE_ACTIONS` **unset means
   dry-run**: actions are described and recorded but never performed. Set to `1`,
   every write/exec becomes a row in `action_approvals` and the tool blocks until
   a human approves or denies it in the UI (or `APPROVAL_TIMEOUT` expires).

Because both modes share one code path, tests exercise exactly what production
runs — only the outcome differs.

**Sites (`provisioner.py`, `sites.py`, `#/sites`)**

The `sites` row is the desired state; the provisioner makes the target match it:

```
dns check  ->  write public_html/index.html  ->  write <domain>.caddy  ->  reload
```

- **Caddy** serves it. Per-site blocks are written to `CADDY_SITES_DIR`; the main
  Caddyfile just needs `import /etc/caddy/sites/*.caddy`. Caddy then issues the
  TLS certificate automatically the moment the domain resolves here — no certbot.
  Reload goes through Caddy's admin API, so the app image needs no `caddy` binary.
- **DNS is the user's job, and that is not an error.** The UI shows the exact A
  records to create; status distinguishes `pending_dns` from `live`, and the URL
  is returned as soon as the site is configured.
- **Pluggable targets:** `LocalTarget` (this server) and `RemoteTarget` (an
  `ssh_hosts` row). `get_target()` picks one from the site's `target` field.
- Provisioning runs as a **job** (`site_provision`), so its transcript and any
  approval requests stream to the UI over SSE.

**Servers (`servers.py`, `tools/ssh.py`, `#/servers`)**

- `ssh_hosts` stores the connection details. The private key never leaves the
  box — only its path is stored.
- `tools/ssh.py` is a *transport only*: policy and approval live above it, so
  there is one place to audit. Host keys are pinned (`RejectPolicy`, plus an
  exact `SHA256:` fingerprint check when recorded). Key-based auth only —
  `look_for_keys` and `allow_agent` are off, so SSH can never silently fall back
  to some other identity.
- The **Test** button runs a read-only probe and is admin-only.

**Agent tools** (granted per agent in the roster)

`provision_site`, `write_site_content`, `check_site_dns`, `list_sites`,
`generate_image` (Gemini native image model), `send_email` (`smtplib`), plus the
original `save_note` / `current_time`. The seeded **Operator** holds the hosting
and delivery tools, so "build the site and put it live" is one job.

S6 adds `search_photos` / `fetch_photo` (Pexels) for real photography — see §21.

**Verification**
- `tests/test_policy.py` — read-only vs mutating classification, 18 dangerous
  commands denied, per-segment chaining inspection, precise `rm -rf` scoping
  (root/system dirs denied, site-root cleanup allowed), path confinement
  (including the `root-evil` prefix trick), domain validation, quoting.
- `tests/smoke_provisioner.py` — dry-run end to end: renders the vhost and index
  (escaping Business Profile HTML), runs all four steps, returns
  `https://acme.test` with status `pending_dns`, refuses a bad domain, refuses an
  unknown target, blocks `curl | sh`, confines writes to both roots.
- `tests/smoke_sites.py` — servers CRUD + dry-run probe, sites CRUD (duplicate and
  invalid domains refused), DNS hints and check, the provisioning **job** through
  to `https://demo.test`, the approvals endpoints, and the agent tools in dry-run.
- **Design flaw the tests caught:** DNS-not-pointing was initially reported as
  `error`; it is the expected state before the user creates records, so `ok` now
  reflects *target configuration* and `status` distinguishes `pending_dns` from
  `live`.
- **Real bug the tests caught:** `servers.update_host` omitted the row id, so the
  statement became `WHERE id = NULL` and silently did nothing. `db._bind` had been
  hiding it by binding `None` for missing parameters — it now refuses a
  placeholder/parameter mismatch outright, and `test_db_dialect` covers that.
- **Not verified here:** any real SSH connection, real Caddy provisioning and real
  email delivery (no server, no Docker, no SMTP on this machine). All of it is
  exercised in dry-run; live mode needs the VPS.

---

## 21. Stage S6 delivered — Pexels stock photography

Content needs real photography as well as generated images: a page about an office
should show an actual office, not a rendered approximation of one.

**Tools (`tools/pexels.py`)**

| Tool | Gated | What it does |
|------|-------|--------------|
| `search_photos` | no — read-only | Queries `/v1/search`; each result carries its download URL, dimensions, average colour, alt text and a `credit` line (photographer + page URL) |
| `fetch_photo` | yes — `actions.gate` | Downloads one result into `artifacts/images/job-<id>/`, path-confined by `tools.policy` |

Granted to **Image Artist** (`search_photos`, `fetch_photo`, `generate_image`) and
**Operator**. The Image Artist's prompt now splits the two jobs: real-world subjects
get stock photography, brand marks and illustrations get `generate_image`.

**The safety call that matters.** A "download this URL" tool handed to a language
model is a ready-made SSRF probe. `check_download_url` therefore requires HTTPS
*and* a host under `images.pexels.com`. The suite asserts the usual attacks fail:

```
allow https://images.pexels.com/photos/1234/nature.jpg
deny  https://169.254.169.254/latest/meta-data/    (cloud metadata endpoint)
deny  https://127.0.0.1:8000/api/health            (localhost)
deny  https://api.pexels.com/v1/search?query=x     (the API host, not an image host)
deny  https://evil.com/images.pexels.com/x.jpg     (hostname-suffix trick)
deny  file:///etc/passwd
```

**A real bug, found by testing against the live API.** The first live search
returned `HTTP Error 403`, which reads exactly like a rejected key. The key was
fine: Cloudflare fronts Pexels and answers the default `Python-urllib/3.x`
User-Agent with **error 1010**. With an honest `AscendPlatform/1.0 (+pexels)` agent the
same key returns 200:

| Request | User-Agent | Result |
|---------|-----------|--------|
| `api.pexels.com/v1/search` | default urllib | **403** (error 1010) |
| `api.pexels.com/v1/search` | `AscendPlatform/1.0` | **200** |
| `images.pexels.com/...` | default urllib | **403** (error 1010) |
| `images.pexels.com/...` | `AscendPlatform/1.0` | **200** |

Both call sites now set the header, a 401/403 is reported as *"check the API key —
and note Cloudflare blocks generic urllib user agents"* rather than a bare status
code, and `test_every_request_identifies_the_app` fails if the header is ever
dropped (it captures the outgoing requests with a stub, offline).

**Attribution.** Pexels' licence does not require it, but every result carries the
photographer and page URL, and the Image Artist is told to report the credit line so
the content can include it.

**Verification** — `tests/smoke_pexels.py`: the URL allowlist above, result shaping,
the dry-run gate, filename-traversal confinement, a graceful failure on a bad key,
and a panel-saved key reaching the tool with no restart. No API key and no network
are needed to run it.

**Not verified here:** a real `fetch_photo` write into the artifacts directory.
Downloads are approval-gated and the suite runs in dry-run, so the write path is
covered by the gate and confinement checks rather than an end-to-end run.

---

## 22. Stage S6 delivered — the Settings tab

Hand-editing `.env` does not work in a running container (`env_file` is read once at
start-up), needs a restart for every change, and cannot reach the arq worker unless
three compose services are edited. The admin **Settings** tab replaces it.

**Resolution order.** `settings.get(key)` resolves left to right:

```
panel value (settings table)  ->  environment variable  ->  SPEC default
```

The panel wins, matching the per-channel Meta override that already existed ("a
secret set in the admin panel overrides the globals"), and it is the only layer that
can change under a running process. Every field reports which layer supplied its
value (`panel` / `environment` / `unset`), so a stale panel value can never silently
shadow `.env` — and **Clear** removes it to fall back.

**Why the panel is truthful.** Almost every environment read in this codebase was a
module-level constant taken once at import (29 of them across 9 modules), so a saved
value would have done nothing until a restart. Those readers are now late-bound —
`pexels.api_key()`, `media.api_key()`, `mail.host()`, `ssh.connect_timeout()`,
`app.deepseek_config()`, `meta.graph_api_base()` and `actions.is_dry_run()` — so a
saved value applies to the *next* call. The DeepSeek client is rebuilt when the key
or base URL changes and re-used otherwise.

**A catalogue, not raw environment access.** Only keys in `settings.SPEC` may be
written. A request handler that can set an arbitrary environment name is a
code-execution vector (`PATH`, `LD_PRELOAD`), so unknown keys are refused. The 33
fields are grouped as the `.env.example` blocks are: AI providers, stock
photography, safety & approvals, email, servers & hosting, app & branding, and
infrastructure.

**Structural settings stay read-only.** `REDIS_URL`, `JOB_BACKEND`, `ARTIFACTS_DIR`,
`DATABASE_URL`, `HOST`/`PORT` and `LLM_GATEWAY_URL` choose the topology itself, so
they are displayed with their effective value but edited in `.env` and marked
`restart`. `DATABASE_URL` and `SECRET_KEY` are deliberately not in the catalogue at
all — they bootstrap the database connection and the session signing.

**Secrets are write-only.** `GET /api/settings` never returns a secret value: it
reports that it is set, where it came from, and a `••••chTB`-style tail so keys can
be told apart. The UI uses a password input, and a blank box on save means
"unchanged" — otherwise a form that never receives the secret would wipe it on every
save. Because the value is never sent to the browser, even a stolen admin session
cannot read the keys back out.

**Audit trail.** Every write records the key, the action, the actor and the time in
`settings_events`. Secrets are recorded as a mask, never in the clear.

**Live checks.** `POST /api/settings/test/<group>` runs a real check: Pexels does a
one-result search, the AI group reports which providers are configured, and SMTP
connects and signs in without sending anything. This is exactly the gap that made the
Pexels 403 confusing — a Test button names the cause immediately.

**Admin-only.** Settings are global rather than per-user, so every verb needs the
`admin` role: 401 when anonymous, 403 for a signed-in member.

**Trade-offs, recorded.** Secrets are stored in SQLite as plaintext, consistent with
how Meta channel tokens are already stored (the DB file is `600` and git-ignored).
Encrypting at rest would need a key supplied at start-up, which is circular for this
deployment — a deliberate decision, not an oversight. The per-process cache has a
`SETTINGS_CACHE_TTL` window (default 3s), so a change made by the web process can
take that long to be seen by a separate arq worker.

**Verification** — `tests/smoke_settings.py`: 401/403 enforcement, the secret never
appearing in the API response or the audit trail, panel-over-environment precedence,
blank-secret preservation, and five rejected writes (unknown key, structural key,
non-numeric, negative, non-boolean) that must leave nothing stored.
`tests/smoke_pexels.py` additionally proves a panel-saved key reaches the tool with
no restart. Both save and restore any pre-existing configured value, so they are safe
to run on a machine that has real keys stored.

**Not verified here:** a real SMTP connection (no mail server on this machine), and
the Settings tab's rendering in a browser — the API it drives is covered by the suite
and by a live HTTP check against the running server.

---

## 23. Fresh-deploy bug — the migration baseline created every table

Found on the first real deployment, when the `app` container crash-looped:

```
alembic Running upgrade  -> 0001_initial, initial schema
alembic Running upgrade 0001_initial -> 0002_agents, agents roster
psycopg.errors.DuplicateTable: relation "agents" already exists
```

**Cause.** `alembic/versions/0001_initial_schema.py` called
`metadata.create_all(bind=...)`. That does not mean "the schema as of revision
0001" — it means every table in `db.metadata` *at the moment the migration runs*.
So the baseline created all 15 tables, and each later revision then tried to create
its own table again: 0002 (`agents`), 0003 (`ssh_hosts`, `sites`, `action_approvals`)
and 0004 (`settings`, `settings_events`) all raised `DuplicateTable` on a fresh
database. Four revisions were latently broken.

**Why it reached production.** Nothing exercised alembic at all. The app calls
`db.init_db()` → `metadata.create_all()` at import — correct for local SQLite — and
every suite tested that path. The only place `alembic upgrade head` runs is the
container command in `docker-compose.yml`, which had never executed before.

**Fix.** Revision 0001 now names its tables explicitly, the same style 0003/0004
already used, so every table belongs to exactly one revision:

| Revision | Tables |
|----------|--------|
| `0001_initial` | users, bots, messages, channels, meta_events, jobs, job_events, job_artifacts, business_profiles |
| `0002_agents` | agents |
| `0003_sites_ssh` | ssh_hosts, sites, action_approvals |
| `0004_settings` | settings, settings_events |

9 + 1 + 3 + 2 = 15 = `db.metadata`, with no overlap and nothing missing. Its
`downgrade()` also stopped calling `drop_all()`, which would have dropped *other*
revisions' tables.

**Regression test.** `tests/test_migrations.py` points `DATABASE_URL` at a throwaway
SQLite file, runs the real `alembic upgrade head` in a subprocess, asserts exit 0 and
that the resulting tables equal `db.metadata`, then runs `downgrade base` and asserts
nothing is left. It reproduced the failure before the fix, and it is the only test
covering the migration path.

**Recovering a database left at 0001.** Drop and recreate
(`docker compose down -v && docker compose up -d --build`) — safe when there is no
data yet — or mark it current with `alembic stamp head`, which is valid precisely
because 0001's `create_all` had already produced the head schema.

**Still open.** The Postgres path is otherwise only exercised in production: the
suites run against SQLite. A CI smoke run of `docker compose up` against the real
Postgres service would close the remaining gap.

---

## 24. Stage S7 (part 1) — multi-tenant clients: orgs, roles, scoped data

**Goal.** Turn a single-tenant install into one deployment that serves many clients,
with hard boundaries between them.

### The model

An **org** is a paying client. The platform operator is *not* an org.

| Role | Belongs to an org | Sees |
|------|-------------------|------|
| `owner` | no | every client's data — the platform console |
| `client_admin` | yes | only their own org; manages their own team |
| `member` | yes | only their own org |

`users.org_id` decides everything. It is written only into the signed session at login
(`app.current_org()`) and **never** read from request input — that is the one value an
attacker controls.

`admin` remains an accepted spelling of `owner`: migration `0005` renames those rows,
and the alias (`app.LEGACY_OWNER_ROLE`) keeps a database that has not been migrated
working. That is not hypothetical — a local dev SQLite file is built by
`db.create_all()`, never by alembic, so it stays on `admin` until someone migrates it.

### Schema

- New `orgs` table: `name`, `slug` (unique), `plan`, `status`, `notes`.
- `org_id` added to `users`, `bots`, `sites`, `jobs`, `business_profiles`.
- `business_profiles`' unique key moved from `owner_id` (one profile per *user*, which
  breaks the moment a client has two staff) to `org_id` — as a unique *index*, which
  both SQLite and PostgreSQL accept.
- Domains stay **globally** unique — two clients cannot both own `acme.com` — while
  *access* to a site is scoped to its org. Conflating those two ideas is what let every
  logged-in user list every client's domains.

`0005_tenancy` is the first revision that `ALTER`s and backfills rather than only
creating, and `0006_client_scope` the first that removes a constraint. Neither is
covered from a *populated* database: `tests/test_migrations.py` only runs
empty → head → base, so a migration that mangles existing rows would pass it. (The
`UNIQUE(owner_id)` divergence two sections down is the shape of bug that gap hides —
and it survived the suite for exactly that reason.) Upgrading a 0004 database with
rows in it is the obvious next test to add.

`db._ensure_columns()` gained the same columns for the SQLite dev path.

### The leaks this closed

| # | Before | After |
|---|--------|-------|
| 1 | `GET /api/sites` returned every client's sites, plus the SSH host list and `SERVER_IP`, to any signed-in user | scoped by org; hosts and the deploy-target list are owner-only, while `server_ip` stays for everyone — a client has to point DNS at it |
| 2 | `GET/PUT/DELETE /api/sites/<id>`, `/dns`, `/check`, `/provision` were `@login_required` only — cross-tenant write by id | ownership checked; another client's row answers **404, not 403**, so ids cannot be probed |
| 3 | `/api/ssh-hosts` **GET/POST/PUT/DELETE** were `@login_required` — any member could read the fleet's credentials and key paths, or delete the servers | `@owner_required` |
| 4 | `/api/agents` GET/POST/PUT/DELETE were `@login_required` — any member could edit or delete the agent roster | `@owner_required`: the roster is the product, not client data |
| 5 | `/api/jobs*` and the FastAPI SSE stream served any client's runs and transcripts | scoped by `jobs.org_id` on both sides |
| 6 | `/api/approvals` listed **and decided** any client's pending writes — i.e. authorised a write to another client's server | scoped through the job; another client's approval is 404 |
| 7 | `DELETE /api/users/<id>` let any admin delete **any** user, including the platform owner's account | a client admin is confined to their own org and can never touch an operator |
| 8 | `/api/bots*` (12 routes) listed every client's bots, conversations and channels | scoped by `bots.org_id`; the widget's public endpoint stays public, by design |

### Three traps worth remembering

- **`None` must never mean "all" for a client.** `list_sites(org_id=None)` and
  `list_jobs(org_id=None)` mean *no filter* — the owner's view — so passing a client's
  `None` straight through would hand them everything. `app.list_scope()` returns `None`
  only for the owner and `0` otherwise, an id no row can have because sequences start
  at 1.
- **`if org_id:` is the wrong test.** `0` is falsy, so the "no access" marker skipped
  the filter and fell through to *no filter* — the owner's view for a client. Both list
  functions now test `is not None`. `smoke_tenancy.py` caught this.
- **Positional arguments against `(limit, org_id)`.** `jobs.list_jobs(current_org())`
  silently passed an org id as the row limit and returned every client's runs. Caught
  by the same test; the call is now keyword-only in intent and commented.

### A latent bug in the DB layer, exposed

`db._prepare()` classified everything that was not INSERT/UPDATE/DELETE as `"select"`,
so `ALTER TABLE … ADD COLUMN` went down the row-returning path and `fetchall()` raised
`ResourceClosedError`. It had never fired, because `_ensure_columns()` only ran its
`ALTER`s on a database *missing* those columns — and no such database existed, since
the only columns it knew about shipped in the same release as the code. Adding `org_id`
made the branch execute for the first time. DDL is now classified as its own kind.

### Tests

`tests/smoke_tenancy.py` — two clients, two logins, and every cross-tenant operation
attempted *as an attack*: sites, bots, jobs, conversations, channels, approvals and the
FastAPI SSE transcript each expected to fail; the owner's cross-client view; the
owner-only surfaces answering 403; and a login with **no** org — which is what every
pre-tenancy login becomes — seeing nothing at all. It creates and removes its own orgs,
users and rows, so it is re-runnable and safe against a live database. **All 16 suites
green.**

### Part 2 — per-client profiles, settings and passwords

- **The Business Profile is per org** (`profile.get_profile` keys on `org_id`). The
  platform's own brand lives at **org 0** — a real bucket, not NULL, because a unique
  index permits any number of NULLs, so NULL could never mean "the one platform
  profile". Two staff at one client now share a profile; they used to get one each.
- **Settings have a scope.** Every `settings.SPEC` field is `platform` (model keys,
  Pexels, the SSH limits — the operator's alone) or `client` (the SMTP group). Client
  overrides live in a new `client_settings` table keyed by `(org_id, key)`, and
  `settings.get(key, org_id=…)` resolves client → panel → environment → default. A
  client still *uses* the platform's `DEEPSEEK_API_KEY`; they cannot read it, and
  `all_for_ui(org_id)` omits platform groups entirely so the key *names* never reach
  their page either. `tools/mail.py` now resolves a job's client, so a send goes
  through that client's SMTP where they have configured one.
- **Passwords.** `POST /api/auth/password` changes your own and requires the current
  one, so a stolen session cannot lock you out. `POST /api/users/<id>/password` lets
  a client admin reset their own team's — refused for another client's user, and
  refused for a platform operator.

Migration `0006_client_scope` adds `client_settings` and moves the platform profile
to org 0. It is deliberately **additive**: `settings` keeps its key-name primary key,
because scoping it per client would mean re-keying a table that holds live credentials
on every deployment.

#### Four more traps, all caught by tests rather than by reasoning

1. **An upgraded database kept `UNIQUE(owner_id)` on `business_profiles`**; a fresh one
   does not, because 0001 builds the table from the current metadata where the key is
   the org. On the upgraded dev database `POST /api/bots` returned
   `500 UNIQUE constraint failed: business_profiles.owner_id`, because the profile
   lookup resolved to a second INSERT. PostgreSQL names that constraint and can drop
   it; SQLite does not, so `db._drop_legacy_owner_unique()` rebuilds the table there.
   Same class of bug as §23 — the migrated and fresh paths had quietly diverged.
2. **A field-level default silently overrode the group's scope.** `_f()` gave every
   field `scope: PLATFORM`, so the `email` group's `CLIENT` scope never applied and no
   client could save their own SMTP. The field default now means "inherit".
3. **`ensure_profile(None)` created orphan rows.** A login attached to no client could
   insert a NULL-org profile, which a later client could be handed. It now returns None.
4. **`org_id=0` is falsy.** Same family as `if org_id:` in §24: `if not owner_id` in
   the old `get_profile` would have treated the platform's own org as "no profile".

`tests/smoke_tenancy.py` grew the matching checks: profiles per client (two logins at
one client share a profile; the platform's is untouched), password change and reset
including the cross-client and operator refusals, per-client settings (A's SMTP
resolves for A — not for B, not for the platform — and its audit trail is A's alone),
a client being refused a platform key both on read and on write, and the mailer
resolving a job's client. **All 16 suites green.**

### Still to do (S7, part 3)

- `/platform/` (operator console) split from `/app/` (client workspace), and the nav
  regrouped into Websites, Blogs, Funnels, Emails, Library. The Settings *API* already
  serves a client's own scope, but the tab is still flagged `adminOnly`, so a client
  has no screen for their SMTP yet — only the operator can set it, with `?org_id=N`.
- The client-onboarding console UI over the `orgs` service (`orgs.py` has the API:
  `create_org` seeds the first `client_admin` in the same call, so a client is never
  created unreachable).
- **`tests/test_migrations.py` should upgrade a populated database**, not just an
  empty one. Both §23's `DuplicateTable` and this stage's `UNIQUE(owner_id)` were
  upgraded-vs-fresh divergences, and the suite covers only the fresh path.







