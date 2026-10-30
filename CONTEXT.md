# AscendPlatform — system context

A single-tenant-per-client AI platform: a team of LLM agents that **acts** on
infrastructure (provisions sites, writes files to servers, publishes pages, reads
analytics) on the operator's own model keys, with GoHighLevel as the rails for contacts
and email. Two consoles: the operator's `#/platform/*` and the client's `#/app/*`.

Read `SYSTEM_PLAN.md` for the reasoning behind each fix (§25-33 are the recent ones).
This file is the map: what exists, where it lives, and what is still open.

## Working rules (read this before opening a source file)

**Files too large to read whole — grep first, then read a range:**

| File | Size | Why it matters |
|---|---|---|
| `admin/admin.js` | ~6,200 lines / 271 KB | every console screen in one file; a whole read is ~70k tokens |
| `app.py` | ~3,400 lines / 132 KB | all routes; ~34k tokens |
| `SYSTEM_PLAN.md` | ~1,700 lines / 96 KB | search a section number, do not read it whole |
| `admin/voice.js` | ~950 lines / 35 KB | the Live transport and the voice overlay |
| `tests/*.py` | ~6,600 lines combined | search for the suite you need |

- Read **ranges** (`start_line`/`end_line`); never a whole file above ~300 lines.
- Locate with a bare pattern first; add context lines only once the line number is known.
- Do not re-read a file to verify an edit — the edit diff already showed the change.
- Keep replies short deep into a session; design rationale belongs in the first message.
- Commit at phase boundaries and start a fresh session per phase.

**Two mistakes already paid for, do not repeat them:**

- **`jobs.create_job()` must be followed by `jobs.submit(job_id)`.** A created job that is
  never submitted sits in `queued` forever — this is what silently broke refine.
- **Flask endpoint names are one namespace.** A second `def chat()` raises
  "overwriting an existing endpoint function" and the app will not boot at all.
- **Alembic revision ids are descriptive** (`0011_site_versions`), and a migration creates
  tables from `db.metadata` with `checkfirst=True` rather than retyping DDL. `test_migrations`
  verifies the whole chain from empty and now runs in preflight.


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

## Acting as a client (the workspace switcher)

`current_org()` reads `session["org_id"]`, and the operator's own value is `None` - which
used to mean "no tenant", so a job they launched wrote rows nobody could see. The row under
the logo in the console fixes the cause: `POST /api/session/org` (owner only, org validated)
stores `session["acting_org_id"]`, and `current_org()`/`list_scope()` follow it. Clearing it
returns the operator to the all-clients overview. A client session cannot set it, and its
`GET` never receives the list of other tenants.

With no client selected, work is refused rather than orphaned: `POST /api/jobs`,
`start_job` (manager) and `save_deliverable` all say so instead of writing. Rows that already
have `org_id IS NULL` appear in **Library -> Unowned drafts** for the operator and are
assigned to a client from there (or with `repair_orphans.py`). A draft's kind is canonical:
`canonical_kind()` maps aliases (`landing_page` → `funnel`, `blog_post` → `blog`), and
`save_deliverable` enforces the tab brief in the job prompt, so a job started from Funnels
cannot save as a website. Existing wrong-tab rows are fixed with
`repair_deliverable_kinds.py --apply`.

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

## Forms, surveys and quizzes - state (v1.60)

Built in five commits: c6b2d5a schema + engine, 0f7735c hosted page, da417a7 Forms tab,
aee48ee builder, 3f4aa7d agent tools, f3cfd94 repair.

Working end to end: forms/form_responses tables; forms_engine (walk, visibility, scoring
over only the questions asked, branching that cannot loop, definition validation);
GET /f/<slug> with server-decided navigation and a CSP; /step and submit endpoints with
honeypot, hashed IP and rate limit; preview that runs the real engine and writes nothing;
Forms tab with create, publish, responses and CSV; builder (types, pages, one rule per
question, points and correct answer); agent tools create_form/list_forms with
can_create.form; form.submitted fires on every submission and is named in the trigger
placeholder.

Known gaps, in the order they would bite:
* The builder has never been clicked in a browser. It is node --check-clean and reviewed,
  not verified: add/move/type-change and the rule dropdowns are the likely breakage.
* Cross-client isolation is implemented (404 on another client form) but its test SKIPS
  here because the local database has one org - it needs a second client fixture.
* No partial saves (a response exists only on submit), no file questions, no email on a
  new answer.
* Deleting a form with answers archives it; there is no un-archive button yet.

Hard-won rule: never chain `git push` with `;` after a test. It pushed a broken test file
twice (f3cfd94 is the repair). Gate on the preflight result, never on the semicolon.

EMAIL TEMPLATE PREVIEW (v1.61): the Emails tab had list/New/Delete and no way to read a
template. GET /api/templates/<id>/preview renders subject and body through
templates_service.render - the same function a send uses - with a sample contact, and
returns sandboxed HTML for an iframe. Nothing is sent.

TRIGGER, VERIFIED (2026-10-29): form.submitted does start an automation run end to
end - POST /api/public/forms/<slug> on a published form creates a row in
automation_runs for an enabled automation whose trigger event matches. It briefly
looked broken because the test created the automation under one client and
submitted a form from another; start_for_event correctly scopes by client, so the
empty result was the test being wrong, not the product. Two traps for next time: a
public submission counts against the ten-a-minute rate limit before validation (pass
a distinct X-Forwarded-For in tests), and automation_runs has no org_id column -
scope through automation_id. Assertion now lives at the end of tests/smoke_forms.py.

## Media - uploaded images, video and PDFs (2026-10-30)

A client's own files for emails, pages and posts. The Media tab is in the Workspace
nav at `#/app/media`; owner and client admins upload/edit/delete, members view and
copy. One file per upload.

**Data.** Table `media` (migration `0019_media`, after `0018_forms`): `org_id`,
`kind` (`image` / `video` / `document`), `filename`, `thumb_filename`, `original_name`,
`mime`, `bytes`, `width`, `height`, `pages`, `alt_text`, `description`, `created_by`,
timestamps. Bytes live under `artifacts/media/<org_id>/`; `MEDIA_DIR` moves the root
and `MEDIA_MAX_BYTES` (default 50 MB) caps an upload.

**The rule.** Alt text and description are mandatory in `media_library.create_media`
and `update_media`; the UI disables the upload button, but the server is the guarantee:
a blank/whitespace field is a 400 before the file is read or written. Alt text is the
image `alt` attribute, the video's accessible label/link text, and the PDF link text.

**Validation.** Images are opened and verified by Pillow (PNG/JPEG/WebP/GIF; SVG is
rejected), with a decompression-bomb limit and a 480px JPEG thumbnail. PDFs are opened
by pypdf (page count; encrypted files refused) and served as an attachment. Video is
MP4 (`ftyp`) or WebM (EBML) only; MOV is refused. No ffmpeg, so no duration/poster.
Refused uploads leave no file and no row.

**Routes.** `GET/POST /api/media`, `GET/PUT/DELETE /api/media/<id>`; uploads require a
selected client and `client_admin_required`, reads use `list_scope()`. Public
`GET /media/<id>/<uuid>.<ext>` and `/media/<id>/thumb/<uuid>.jpg` serve the bytes with
`nosniff`, immutable caching and no login; the UUID is the capability. The public
route verifies the filename against the row.

**For the agents.** `tools/library.py` registers `list_media(query)`: it reads the
job's org, returns id, kind, dimensions/pages, alt text, description, URL and the
right snippet (image `<img>`; video page `<video>` and email `<a>`; PDF `<a>`). It is
granted to Copywriter, Operator and the Manager, and `sync_default_tools()` adds it to
existing rosters. `CONTENT_RULE`, `MEDIA_RULE` and the Manager prompt say: use
`list_media` for the client's own files; if Media is empty or has no suitable shot,
`search_photos`, then `import_photo_to_media` to download the chosen Pexels photo into
this client's Media. Use the returned local URL/HTML and alt text, keep the credit in
the description, and never paste a remote Pexels URL or a disk path into content.
Absolute URLs use `PLATFORM_URL`, then `https://ADMIN_DOMAIN`.

`import_photo_to_media(url, alt, description, credit, filename)` is registered in
`tools/pexels.py`, validates the Pexels image host, downloads through the same
`media_library.create_media` path as an upload, and returns
`{media_id, url, path, alt, description, credit, html}`. It is internal storage, so it
has **no approval gate and no dry-run**; `fetch_photo` remains the artifact-only
download. The importer is granted to Copywriter, Image Artist, Operator and the
Manager. A key-less `search_photos` returns the exact `PEXELS_API_KEY` message; the
Manager must not treat an empty Media tab as a dead end.

**Guards.** `tests/smoke_media.py` covers mandatory fields, image/PDF/video storage,
public serving, thumbnails, scope, edit/delete and the tool;
`tests/smoke_stock_media.py` covers the Pexels import, local URL/HTML, credit, Media
row/file and Manager/Image Artist wiring; `tests/test_db_dialect.py` includes the
table. Gaps: no video captions, no bulk upload, no file replacement under the same
URL, and generated job artifacts are still not imported automatically.

## Assistant sessions - private, server-backed (2026-10-31)

The Assistant page now has a real chat list: **+ New** starts fresh, the left rail
lists earlier chats, and opening one continues it. Sessions survive a reload and a
different device.

**Data.** `assistant_sessions` (`org_id` nullable, `user_id`, `title`, timestamps) and
`assistant_messages` (`session_id`, `role`, `content`, `job_id`, `created_at`);
migration `0020_assistant_sessions` after `0019_media`. Ownership is the pair
`(user_id, current_org())`; `org_id IS NULL` is the operator's no-client view. Another
team member in the same client cannot see a session. `list_sessions` only returns
sessions with a message; the title is the first user turn, trimmed to 90 characters.

**Service.** `assistant_sessions.py`: create/get/list, `list_messages` (last N,
oldest first), `add_message`, `record_reply_for_job`, `import_messages`. A user message
carries the `job_id`; the manager task records the answer against that job, idempotently.
The old browser thread is imported explicitly, never silently, because it had no client
context.

**API.** `GET/POST /api/assistant/sessions`, `GET /api/assistant/sessions/<id>`,
`POST /api/assistant/sessions/<id>/messages` (voice), and
`POST /api/assistant/sessions/import`. `POST /api/manager/chat` accepts `session_id`
or `create_session: true`; when a session is involved it builds history from the
server's last ten messages and inserts the user turn with the new `job_id`. The
floating sheet and Manager page, which send neither, keep their old client-history
behavior.

**The answer bug.** `manager.chat_task` used to return the answer without emitting a
`Manager`/`message` event, so every UI that reads that event showed "No answer" even
when the job succeeded. It now emits the answer for all three return paths and calls
`record_reply_for_job`; the Assistant, Manager page and sheet also fall back to
`job.result` for older jobs.

**UI.** Assistant page: `.asst-layout` (rail + main), `+ New`, session list with
title/time/message count, active highlight, one-time old-thread import, and main
thread. Text sends `session_id` or `create_session`; voice `onTurn` creates/reuses a
session and posts the turn. The floating Ask sheet stays a quick local thread.

**Guards.** `tests/smoke_assistant_sessions.py` covers service roundtrip, reply
idempotence, user/client scoping, voice messages, import, and the manager route's
server-built history; `tests/test_markup_calls.py` asserts the Manager answer event;
`tests/test_db_dialect.py` includes both tables. Gaps: no rename/delete/search; the
old-browser import is per browser; a failed manager job leaves the user turn but no
assistant row; the floating sheet is not session-backed.

## Voice — the spoken assistant (2026-10-30)

Browser-direct Live socket, one-use token minted by `voice.py`, key never leaves the
server. `POST /api/voice/session` returns the token, the setup JSON, the constrained
endpoint, the model and the API version; `POST /api/voice/tool` is the only tool
(`delegate_to_team`) and it starts a normal `chat` job after `jobs.submit`.

The five things that must stay true (each has a guard in `tests/smoke_voice.py`):

1. **Model:** default `gemini-3.8-live`. `gemini-2.5-flash-live` and
   `gemini-live-2.5-flash-preview` are retired; `voice.model()` maps them to the default
   and `voice.model_note()` says so. `GEMINI_LIVE_MODEL` overrides.
2. **Setup:** `inputAudioTranscription: {}` and `outputAudioTranscription: {}` are not
   optional - without them a working session has no text at all. So is
   `realtimeInputConfig.automaticActivityDetection.disabled: false`.
3. **Order:** `getUserMedia` first, then `/api/voice/session`. The token's
   `newSessionExpireTime` is 60 seconds; a permission prompt read slowly used to spend it.
4. **Gate:** "listening" is set only by `handleSetupComplete`; `ws.onopen` says
   "connecting". A 12-second setup timer turns a silent open socket into a visible error.
5. **Failure is visible:** `message.error`, `goAway`, and `onclose` code/reason all render
   the server's words, with Copy diagnostics. A close before setup reads "rejected".

The overlay (`admin/voice.js` + `.voice-*` in `admin.css`) is opened by the sheet's Talk
button, and by the Assistant page and tour through the same `AscendVoice.open`. It is a
mode: orb scaled from mic/output RMS, live transcripts coalesced by turn, Minimise keeps
the session alive, Stop/Esc end it. `Settings -> Voice -> Test` calls
`voice.live_models_by_version(force=True)`, lists the Live models for **both** `v1alpha`
and `v1beta`, checks a token mint on the configured version, and names a version
mismatch explicitly. `POST /api/voice/session` calls `voice.model_availability()` before
minting; if the configured model is absent from the configured version (or the model
list fails and the other version lists it), it returns a 400 with the version to switch
to instead of opening a socket that sits silent until the 12-second setup timer. A
network failure of both model-list calls does not block the session
(`checked=False`). `tests/voice_ui.mjs` drives the real client against a fake DOM,
AudioContext and WebSocket, so the gate and the failure paths are tested without a
browser or a key.

The text model in **AI providers** (`GEMINI_MODEL`, e.g. `gemini-3.8-flash`) is not the
voice model; voice uses `GEMINI_LIVE_MODEL` (`gemini-3.8-live` by default). The Test
button is the source of truth for which Live model and API version this key can use.

Known gaps, in the order they would bite:

* `_USED` minutes and `_VOICE_TURNS` dedupe are per process; uvicorn's two workers can
  each allow a daily cap and can miss a duplicate turn across workers.
* No session resumption: a network blip ends the turn and the one-use token needs a new
  mint. The overlay offers Try again; it does not resume.
* `MUTE_WHILE_SPEAKING` is false because cutting mic frames also cuts barge-in. Switch it
  only if echo is measured on a real device.
* Ephemeral tokens are v1alpha in `google-genai 2.30.1`; the public docs show v1beta.
  `GEMINI_LIVE_API_VERSION` exists so a key can be switched without a code edit.
* The real handshake has not been exercised in this workspace (no key configured).
  Everything up to the socket and every error path is covered; the first real session
  should be run from Settings -> Voice -> Test, then the sheet's Talk button.

## Voice — current state and runbook (2026-10-30)

Use this section to resume in a new task without replaying a long tool history.

**Where the code is**

* Local `main` is `d7761ad` ("The voice stops pretending it is listening"), one commit
  ahead of `origin/main` (`374bdc6`). The overlay is in `admin/voice.js` and
  `admin/admin.css` (`.voice-overlay`, `.voice-orb`, `.voice-transcript`), opened by
  `AscendVoice.open` from the Assistant page, the floating sheet and the tour.
* Live `https://ai.ascendplatform.site` is on `374bdc6` / version 1.19. Its
  `/admin/voice.js` has no `voice-overlay` and its `admin.css` has no `.voice-orb`, so
  the deployed site is serving the old client. Its `/api/health` reports
  `providers_configured: ["deepseek","anthropic","gemini"]` - the VPS already has a
  Gemini key; it is the local SQLite database that does not.

**See it locally**

1. `python -m uvicorn asgi:app --host 127.0.0.1 --port 8000 --reload`
2. Open `http://127.0.0.1:8000/admin/`; the seeded login is
   `admin@example.com` / `admin123` unless it was changed.
3. Settings -> AI providers -> Google Gemini API key -> Save. Then
   Settings -> Voice -> Test lists the Live models the key can see.
4. If `gemini-3.8-live` is not listed, set Settings -> Voice -> Gemini live voice model
   to one that is. If minting fails, switch Live API version between `v1alpha` and
   `v1beta`.
5. Assistant -> Talk. The overlay appears before the token is minted; the orb is scaled
   from mic/output RMS, and both transcripts are requested in `voice.setup()`.

**Deploy it**

1. `git push origin main`
2. On the VPS: `cd /opt/ascendplatform && ./deploy.sh`
3. Verify `/api/version` says `d7761ad`, `/admin/voice.js` contains `voice-overlay`,
   and `/admin/admin.css` contains `.voice-orb`. The VPS key then only needs the
   `GEMINI_LIVE_MODEL` value that worked locally, if the default is not available.

**If the Assistant says it is not configured**

`manager.py` uses DeepSeek when it is configured and otherwise the first configured
provider (Gemini or Claude), so one key is enough for a local install. With no provider
at all, `/api/manager/chat` and `/api/voice/tool` answer 503 with the reason instead of
creating a job that fails; the console prints the job's own error line rather than
"No answer". `tests/test_markup_calls.py` pins the fallback.

**If the agent session fails with a tool-message error**

Do not retry the same transcript. Start a new task and say "Read CONTEXT.md, Voice
section, and continue." The error is an OpenAI-compatible API rejection of an orphaned
`role: "tool"` message; it is not produced by this app (`app.py:1055-1061` filters chat
history to user/assistant only).
