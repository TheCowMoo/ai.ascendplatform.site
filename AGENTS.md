# AGENTS.md — AI entrypoint

Read this first. It is the compact operating guide for this repository. Deep maps live
in [REFERENCE.md](docs/ai/REFERENCE.md), task recipes in
[RECIPES.md](docs/ai/RECIPES.md), the generated inventory in
[INDEX.md](docs/ai/INDEX.md), and the first-session prompt in
[HANDOFF.md](docs/ai/HANDOFF.md).

## What this system is

AscendPlatform is a multi-tenant AI growth platform for an agency and its clients:

- **Client Workspace (`#/app/*`)** is what a client sees: website, content, contacts,
  analytics, chat, media and account settings.
- **Platform Console (`#/platform/*`)** is owner-only agency operations: clients, people,
  bug tracker, domains, usage, email, data sources, servers, agents and settings.
- The AI layer builds content, runs background jobs, uses tools, and can act on hosting
  after an approval gate.
- Production is Caddy -> ASGI (`asgi.py`, FastAPI + Flask via a2wsgi) -> PostgreSQL; Redis
  carries the arq queue when `JOB_BACKEND=redis`.

## Read order

1. This file.
2. `docs/ai/REFERENCE.md` — current architecture and boundaries.
3. `docs/ai/INDEX.md` — generated route/settings/table/test inventory.
4. `docs/ai/HANDOFF.md` when you are starting a fresh agent session.
5. The matching recipe in `docs/ai/RECIPES.md`.
6. Only the relevant source ranges and tests.

Use `CONTEXT.md` and `SYSTEM_PLAN.md` for history and rationale, not as the first map.
They are large and contain stale snapshots.

## Context-budget rules

| File | Approx. size | Rule |
|---|---:|---|
| `admin/admin.js` | ~10,900 lines | Search first, read a range. It contains every console screen. |
| `app.py` | ~4,700 lines | Search route/service names, then read a range. |
| `CONTEXT.md` | ~1,100 lines | Read only the relevant section. |
| `SYSTEM_PLAN.md` | ~1,400 lines | Read only the relevant section. |
| `admin/admin.css` | ~1,700 lines | Search by component/class. |

- Never read a whole large file just to orient yourself.
- Search for a route path, function name, table name or UI label first.
- Trust the source and `docs/ai/INDEX.md` over old line numbers in prose.
- Check `git status` before editing; do not overwrite unrelated work.
- Ignore generated `*.log` files at the repository root.
- Do not commit or push unless the user asks.

## Where things live

| Area | Primary files |
|---|---|
| Flask API, auth, routes | `app.py` |
| FastAPI SSE/providers bridge | `asgi.py` |
| Schema and query adapter | `db.py`, `alembic/versions/` |
| Settings and scopes | `settings.py` |
| Users, clients and roles | `orgs.py`, `user_service.py` |
| Client workspace / Platform UI | `admin/admin.js`, `admin/admin.css`, `admin/index.html` |
| AI roster and team execution | `agents.py`, `llm.py`, `manager.py` |
| Background jobs | `jobs.py`, `worker.py`, `scheduler.py` |
| Agent tools | `tools/` |
| Chat assistants and widget | `bots.py`, `widget_embed.py`, `tools/chat.py` |
| Sites, DNS and hosting | `sites.py`, `provisioner.py`, `servers.py`, `webcheck.py` |
| Content / deliverables | `deliverables.py`, `builds.py`, `build_service.py`, `site_builder.py` |
| CRM and forms | `contacts.py`, `forms.py`, `forms_engine.py`, `tools/forms.py`, `contact_sync.py` |
| Analytics and GA4 | `analytics.py`, `site_logs.py`, `ga4.py`, `analytics_oauth.py` |
| Billing / credits | `usage.py`, `rates.py` |
| Email and invitations | `mail`, `platform_mail.py`, `notifications.py`, `invites.py` |
| Operations docs | `docs/operations.md`, `deploy/` |

## Task-to-file map

| Change | Start here | Also update |
|---|---|---|
| Add or change an API endpoint | `app.py` route | Service module, tests, preflight |
| Add a DB column or table | `db.py` | Alembic revision, dialect/migration tests |
| Add a setting | `settings.py` | UI behavior, settings test, docs |
| Add a client page | `admin/admin.js` `AREAS` + view | CSS, `tests/nav_groups.mjs`, API |
| Add a Platform Console page | `AREAS.platform` + view | CSS, tests, route |
| Add a background job | `jobs.py` | `worker.py` task registry, tests |
| Add an agent tool | `tools/` | `tools/__init__.py`, roster/policy, tests |
| Add an integration | New service module | Settings, routes, UI, docs, tests |
| Change client-facing copy | `admin/admin.js`, `settings.py` | Tests that pin labels |
| Add a test | `tests/` | `tests/preflight.py` if it is a fast suite |

Detailed recipes and pitfalls are in `docs/ai/RECIPES.md`.

## Non-negotiable invariants

1. **Tenant scope comes from the signed session, never request input.** `current_org()`,
   `list_scope()` and `can_access_org()` are the boundary. Cross-tenant reads should look
   like 404, not 403, so ids cannot be probed.
2. **Owner-only and client-admin-only routes stay guarded.** Use `@owner_required`,
   `@client_admin_required`, or `@login_required`; the FastAPI side uses
   `require_owner`/`require_session`.
3. **Client roles cannot become agency owners from a client page.** Fixed-org user flows
   must exclude the owner role and never accept a client-supplied org change.
4. **Side effects go through `actions.gate()` / the approval policy.** Dry-run is the
   default; `ALLOW_LIVE_ACTIONS=1` is an explicit operator decision.
5. **A created job must be submitted.** `jobs.create_job()` followed by `jobs.submit(id)`.
   A queued job that is never submitted is a silent failure.
6. **Flask endpoint function names are one namespace.** Do not add a duplicate `def name()`.
7. **Migrations create tables from `db.metadata` with `checkfirst=True`.** Keep
   `test_migrations.py` green through a full downgrade/upgrade chain.
8. **Settings are scoped.** Platform fields are operator-only; client fields use
   `scope=client`; never expose platform keys or secrets to a client payload.
9. **OAuth/provider tokens stay server-side.** Never return refresh tokens or secrets in
   API responses, logs, or URLs. Redirect state is random, session-bound and one-use.
10. **Use the existing service-error pattern.** Raise a named error with an actionable
    sentence; the route maps it to a 400/403/404.
11. **A site may only embed a chat assistant from the same org.** `linked_bot_id` is
    validated by `sites.create_site`/`update_site`; injection uses
    `widget_embed.inject_for_site`, never a raw client-supplied id.
12. **Agent marketing writes inherit the job's role.** Forms, site writes and chat
    assistant attachments use `tools/access.py`; a member cannot bypass a
    client-admin-only screen by asking the Manager.

## Frontend rules

- `admin/admin.js` is a no-build SPA. `AREAS` defines both sidebars; view functions render
  screens. Keep old hashes working when moving labels/groups.
- Use the shared helpers (`el`, `api`, `formModal`, `toast`, `empty-state`, badges) instead
  of adding a second pattern.
- Respect both audiences: a client must not see Platform-only debug data, provider names,
  servers, agents or internal bug tooling.
- Client-admin-only pages must be hidden for members and guarded on the server.
- Use the design tokens in `admin/admin.css`; do not introduce a new colour system.
- `tests/nav_groups.mjs` pins the nav shape; `tests/audit_ui.py` pins role visibility.

## Commands

```powershell
# Local app
.\venv\Scripts\python.exe -m uvicorn asgi:app --reload

# New AI index
.\venv\Scripts\python.exe tools\ai_reference.py
.\venv\Scripts\python.exe tools\ai_reference.py --check

# Focused suite
.\venv\Scripts\python.exe tests\test_ga4_oauth.py

# Full gate before claiming completion
.\venv\Scripts\python.exe tests\preflight.py
```

`tests/preflight.py` runs the fast Python suites, JS parsers, migration checks and the
index freshness check. A change is not complete until preflight is green, unless the user
explicitly asks for a narrower validation and understands the gap.

## Definition of done

- The smallest relevant suite passes.
- The generated index is current (`tools/ai_reference.py --check`).
- `tests/preflight.py` passes.
- New routes/settings/tables have tests and documentation when applicable.
- No tenant, role, secret or approval boundary was weakened.
- The final summary names files changed, behavior changed, tests run, and anything not
  verified (for example a real external OAuth consent flow).

## Documentation hierarchy

1. `AGENTS.md` — how an agent should work.
2. `docs/ai/REFERENCE.md` — current architecture and boundaries.
3. `docs/ai/RECIPES.md` — safe change procedures.
4. `docs/ai/INDEX.md` — generated inventory; never edit by hand.
5. `CONTEXT.md` / `SYSTEM_PLAN.md` — history and design rationale.
6. `README.md` / `docs/operations.md` — user setup and operator runbook.

If two sources disagree, the current source code and `docs/ai/INDEX.md` win; fix the prose
that is stale.
