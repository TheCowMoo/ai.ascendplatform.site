# AI Recipes — safe changes in AscendPlatform

Use this after `AGENTS.md` and `REFERENCE.md`. Every recipe ends with the same rule:
run the focused test, refresh the generated index, then run preflight.

## Recipe 1 — Add or change an API endpoint

1. Find the nearest route block in `app.py` with `@app.route`.
2. Put the route in the matching section, not at the end of the file.
3. Use the correct decorator:
   - `@login_required` — any signed-in workspace user
   - `@client_admin_required` — client admin or owner
   - `@owner_required` — agency platform only
4. Resolve the tenant with `current_org()`; never read `org_id` from client input.
5. Put business logic in the service module; the route should validate, scope, call the
   service, map its named error to a 400/403/404, and return JSON.
6. Add a test under `tests/` and register a fast suite in `tests/preflight.py`.
7. Refresh `docs/ai/INDEX.md`.

Endpoint function names must be unique in the Flask namespace. A duplicate `def chat()`
is a startup failure.

## Recipe 2 — Add a database column or table

1. Declare it in `db.py` using SQLAlchemy Core `Table`/`Column` definitions.
2. Add an Alembic revision under `alembic/versions/`; the revision should call
   `metadata.tables["name"].create(bind=op.get_bind(), checkfirst=True)` for a new table or
   inspect/add the column for an existing table.
3. Make the migration safe for a database already touched by `metadata.create_all()`.
4. Update `tests/test_db_dialect.py`'s expected table set.
5. Run `tests/test_migrations.py` — it tests upgrade, downgrade and a pre-created table.
6. Update `CONTEXT.md` data-model summary if the table is user-visible.

Never edit an old migration that has shipped. Add a new revision.

## Recipe 3 — Add a settings field

1. Add `_f("KEY", "Label", ...)` to the correct group in `settings.py`.
2. Choose `scope=PLATFORM` or `scope=CLIENT` deliberately. Client-visible settings must
   not carry platform credentials.
3. Mark secrets with `secret=True`; they are write-only and masked in the panel.
4. Decide `applies=LIVE` or `applies=RESTART`; structural values are `.env`-only.
5. Wire the value where it is used; do not cache it at import time if it should apply
   without a restart.
6. Update the relevant settings test and `.env.example` if it is deployment-level.
7. Run `tests/smoke_settings.py` and refresh the index.

## Recipe 4 — Add a client workspace page

1. Add or extend a group in `AREAS.app` in `admin/admin.js`; every leaf needs a unique
   `id` and a `#/app/...` hash.
2. Add the view function and route dispatch in `render()`.
3. Use existing `el`, `api`, `formModal`, `toast`, `empty-state` and badge helpers.
4. Add server scope checks. Hiding the nav item is not enough.
5. Add CSS near the matching component section in `admin/admin.css`.
6. Update `tests/nav_groups.mjs` and any UI audit/preflight harness.
7. Run `node --check admin/admin.js` and the focused Python suite.

Keep old hashes working when renaming a page. Preserve role gates (`adminOnly`,
`ownerOnly`, `clientHidden`).

## Recipe 5 — Add a Platform Console page or tab

1. Add the item to `AREAS.platform`; the Platform console is owner-only.
2. Add the view and the `render()` dispatch branch.
3. Reuse the platform client-detail tabs when the page is client-scoped.
4. Add an owner-only API and test, or reuse an existing owner-only API.
5. Update `tests/nav_groups.mjs` (it pins the Platform nav shape).
6. Run `tests/audit_ui.py` to confirm client sessions cannot render it.

## Recipe 6 — Add a background job

1. Define the task in `jobs.py` and add it to the `TASKS` registry.
2. Register it in `worker.py` so the arq worker knows the task.
3. If a request starts it, call `jobs.create_job()` and then `jobs.submit(job_id)`.
4. Emit events through the job event API so the transcript and SSE stream stay useful.
5. Add a test that covers enqueueing, tenant scoping and terminal status.
6. Run preflight; `check_worker_tasks()` is designed to catch missing registration.

## Recipe 7 — Add an agent tool

1. Create or extend a module under `tools/`.
2. Register it through `tools/__init__.py` with an explicit schema and policy.
3. Add the tool to the appropriate agent roster/defaults; do not rewrite existing seeded
   prompts blindly.
4. Put side effects behind `actions.gate()` and policy checks.
5. Add tests for valid input, invalid input, tenant scope and dry-run/live behavior.
6. Update `docs/ai/REFERENCE.md` if the capability is user-visible.

## Recipe 8 — Add an external integration

1. Create a service module that owns the HTTP transport and token handling.
2. Add platform credentials to `settings.py`; never expose them to a client.
3. Inject or replace the transport in tests; never require live network in a suite.
4. Add a scoped data record or source binding; never trust a client-supplied account id.
5. Add a sync/refresh failure path that records a clear error on the source/run.
6. Add tests for success, missing credentials, expired token, malformed response and
   cross-tenant access.
7. Update `.env.example`, operations docs and the generated index.

OAuth examples: `analytics_oauth.py` (state, code exchange, refresh, source marker) and
`ga4.py` (choose OAuth vs service-account credential).

## Recipe 9 — Change client-facing copy or navigation

1. Search both `admin/admin.js` and server-side settings/error strings; copy can live in
   either place.
2. Prefer plain language: say what the user will do, not how the subsystem works.
3. Keep technical/provider terms inside the Platform Console or an Advanced disclosure.
4. Preserve hashes and role gates; update nav/UI tests if labels are pinned.
5. Do not expose `agent`, `provider`, `org_id`, token or infrastructure terminology to a
   client unless it is already part of the product vocabulary.

## Recipe 10 — Add a test and register it

1. Copy the structure of a nearby test under `tests/`.
2. Use a per-run suffix and clean up every fixture in `finally`.
3. Print one `... OK` line at the end; tests are scripts, not pytest functions.
4. Add a fast suite to `FAST_SUITES` in `tests/preflight.py` if it is quick and stable.
5. Add JS harnesses to `check_js()` in preflight when they are pure Node checks.
6. Run the focused suite and then full preflight.

## Recipe 11 — Review a tenancy or permission change

1. Identify the route/API and every service call it reaches.
2. Confirm the tenant comes from the session (`current_org()`, `list_scope()`), not input.
3. Confirm cross-tenant access returns 404 where ids are user-controlled.
4. Confirm client admins cannot create owners or move users between clients.
5. Confirm members cannot reach admin-only pages or mutations.
6. Add an API-level negative test, not only a UI/static check.
7. Run `tests/audit_ui.py`, the focused suite and preflight.

## Common traps

- A created job that is not submitted stays queued forever.
- A route function name collision stops Flask from booting.
- `metadata.create_all()` can run before Alembic; every migration needs `checkfirst`.
- A client response must not include provider names, model keys, server details, tokens or
  agency-only bug data.
- A nav label is not a permission check. Guard the API.
- Do not read whole huge files; search and read ranges.
- After changing routes/settings/tables/tests, regenerate `docs/ai/INDEX.md`.

## Final validation

```powershell
.\venv\Scripts\python.exe tools\ai_reference.py --check
.\venv\Scripts\python.exe tests\preflight.py
```

If a real external provider is involved and credentials are unavailable, say exactly what
was mocked or left unverified rather than claiming an end-to-end success.
