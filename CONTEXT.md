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
| `llm.py` | `PROVIDERS` (the single source of truth for capabilities), `get_model_client()`, `_clean_messages()` + `without_empty_blocks()` payload repair for Anthropic. Also wraps the SDK response to capture the raw usage AutoGen folds away, and returns metered client subclasses. |
| `rates.py` | The price list in dollars per 1M tokens / minute / image; `price_model`, `price_unit`, `missing_rates`, `python -m rates import <file>`. |
| `usage.py` | Normalises each provider's usage into four buckets, writes `usage_events` and the credit ledger, `bind()` for job attribution, `guard()` for enforcement, `summary`/`reconcile`/`job_cost`, `python -m usage calibrate`. |
| `agents.py` | Roster (`DEFAULT_ROSTER`), seeding, `build_team()`, `run_team()`, `system_message_for()`, `unique_name()`, `guard_tools_supported()` |
| `tools/` | `register()`/`resolve()` registry: `site`, `pexels`, `media`, `mail`, `deliverable`, `policy`, `ssh` |
| `jobs.py` | Job rows, events, artifacts, `TASKS` registry, runners. `submit()` enqueues to arq from a request thread *and* from inside a running job: a child job submitted by `start_job` or an automation node gets a second loop in a short-lived thread, because `asyncio.run` raises there. A failed enqueue now names its reason in the transcript. |
| `deliverables.py` | Blogs/funnels/emails/websites/proposals; `publish_deliverable()` |
| `sites.py` | Hosted domains: domain, aliases, docroot, content, status/ssl. `POST /api/sites` takes the tenant from the acting client for the operator too, so a site created while "acting as" a client is that client's. |
| `review.py` | Weekly brief (two windows), prompt, `site_review` job, digest |
| `analytics.py` | `data_sources`, `metrics_daily`, `sync_runs`; source CRUD, idempotent metrics |
| `site_logs.py` | Caddy access log → per-org metrics (no IPs stored) |
| `ga4.py` | GA4 service-account adapter (key by path) |
| `versions.py` | `site_versions` — snapshot on publish, one-call restore |
| `platform_mail.py` | The operator's own mail through GHL (contact upsert → conversation message) |
| `invites.py` | Single-use hashed invite tokens, accept → set password |
| `scheduler.py` | Daily source sync, weekly `site_review` queueing, quip-free guards. The hourly DNS sweep (`SITE_VERIFY_MINUTES`, default 60) also re-checks sites with `org_id NULL` — skipping them kept a correctly-pointed domain "pending dns" forever. |
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

**A domain.** `sites.status` is `pending_dns` → `dns_ok` → `live`. The hourly sweep
(`scheduler.verify_pending`, `SITE_VERIFY_MINUTES`, default 60) re-resolves the records
and probes HTTPS. When DNS points here but HTTPS does not answer, it queues
`sites.queue_provision` — the same helper `POST /check` calls — so "point it here and SSL
issues itself" is true without a second button. Queueing is deduplicated (one open
`site_provision` per domain) and capped at 3 automatic attempts per 24h, so a closed port
80 cannot burn the ACME rate limit; pressing Provision is always allowed. A failed run
leaves `error`, and the sweep includes that status too, so a deployment that has since been
fixed retries without anyone pressing a button. The run asks **one** approval ("publish
<domain>: page, site block, reload") and passes `preapproved=True` down to every write and
the reload — policy is still enforced, so an approval is not a licence to run anything.
The reload is not a shell command: the app image has no caddy binary and the command policy
(rightly) refuses `curl`, so it POSTs `CADDYFILE` to `CADDY_ADMIN_URL` (Caddy's admin API).
A remote `ssh_hosts` target keeps `CADDY_RELOAD_CMD`, since the admin API is not reachable
from there. `www` only exists for an apex name
(`provisioner.is_apex`; `example.co.uk` counts as apex), is never required, and is added
to the Caddy site block only when it actually resolves here. Before provisioning, a
browser's `ERR_SSL_PROTOCOL_ERROR` is expected: Caddy has no site block for the name, and
`describe_probe_error` now says exactly that. Once the block is loaded, an
`alert internal error` is a different failure — certificate issuance — and the hint says so;
the Caddy log names the ACME error. `ACME_EMAIL` must be a real address: Let's Encrypt
refuses contacts at `example.com` (`invalidContact`) and Caddy then silently issues from
ZeroSSL instead. On the VPS, `python -m provisioner check <domain>` prints the same verdict
without the UI.

**A deliverable.** Copywriter/Operator calls `save_deliverable(kind, title, content,
summary, deliverable_id)`. Ownership comes from the job row, never the model. Images:
placeholders are filled from `artifacts/images/job-<id>/` → `/artifacts/...`; if the job
has no photos the placeholder `<img>` is stripped so the email still saves. Same
kind+title from the same job updates that draft. `proposal` is never publishable.
Publishing writes through `provisioner`, snapshots the previous HTML into
`site_versions`, and records an approved approval row carrying the operator's name —
the click *is* the decision. The write then runs ``preapproved``: the gate must not ask a
second time, because this handler is synchronous Flask on the uvicorn event loop and the
blocking gate held a worker for ``APPROVAL_TIMEOUT`` per click - two clicks starved both
workers and every request answered 502. The response carries a warning when the site is not
live yet, since a page written into a docroot nobody serves looks exactly like a publish
that did nothing.

Removing a domain is a disconnect, not a row delete. `sites.disconnect_site()` records
one approved `site_delete` row, removes the Caddy block and reloads the web server
(`preapproved=True`), and only then deletes the `sites` row. Website files,
`site_versions`, and `deliverables` are kept; a page attached to the site is detached,
returned to `draft`, and its stale live URL cleared. A failure leaves the row in place
for a retry. `GET /api/platform/domains` is owner-only and lists every domain, including
rows whose `org_id` no longer resolves; those read as **Deleted client**. A client
delete is refused while any domain remains. The old row-only `delete_site()` stays for
test cleanup only.

**The rest of a launch.** `publish_page` (and its alias `publish_site`, which was advertised
in `OUTWARD_TOOLS` but never registered) publishes the run's bound draft, an explicit id, or
the newest website page to the draft's site, an explicit site, or the client's only site -
asking which domain when there are several. It is outward, so an automation needs a gate
before it. After a *new* site the Manager offers the pieces around it (a published form with
a button on the page, a follow-up template, an automation), builds them only on a yes, and
explains an automation and waits for confirmation before `apply_automation`. The voice
publishes the canvas page itself after saying which page and domain and hearing an explicit
yes; the audit row carries the speaker's name. Client email can go through that client's own
GoHighLevel sub-account (`client_email_ghl`: token, location, from address are client-scoped);
`send_email` prefers it and falls back to the client's SMTP when it is unset. The platform's
invitations stay on the operator's GHL group.

**How the AI works for a client.** `business_profiles.preferences` holds short rules, one per
line, and `signature` the block a written piece ends with - instructions, not business facts.
Every `chat` turn builds its system prompt through `manager.system_for(org)` and every roster
agent gets the same brief appended in `agents.build_team` (1500 characters per agent, 3000 for
the Manager), so tone and rules reach the Studio, the Assistant page, the floating sheet, voice
and the team - the Business Profile used to reach only the chatbot. `build_knowledge` carries
the rules too, so bots follow them. The Manager learns them itself: `remember_preference`
(granted tool, no approval - it writes the client's own profile) appends one deduplicated line
and emits `Preference: Saved ...` into the transcript, and the system prompt tells it to call
that when a lasting rule is stated, without waiting for "remember". The same block is editable
on the Business Profile screen, so a wrong rule is one deletion away.

**The first login.** `users.onboarded_at` (migration `0025`) is per account, not per
browser, so a second device does not replay the welcome. Boot and the login handler call
`firstRun()`, which marks the account through `POST /api/auth/onboarded` (idempotent) and
asks `/api/voice/status`. With voice configured it opens the overlay with `intro: true`: the
server adds a thirty-second, role-aware script to `voice.instruction(intro=True)` and
`voice.js` sends one kickoff `clientContent` after `setupComplete`, so the voice speaks
first. A browser that suspended audio waits for any tap, which resumes the context and
sends it once. With no voice key the same words appear as a tour-style card. Closing the
welcome starts the existing five-stop tour.

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
- `tests/preflight.py` imports the app and runs the fast suites. Run it before every
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

**After the redis healthcheck change (2026-11-01):** `app` now waits for
`condition: service_healthy` on redis, but `deploy.sh` recreates only `caddy app worker`.
An existing redis container has no healthcheck configured, so the first `up` after this
pull must recreate it once:

```bash
docker compose up -d --build --force-recreate redis app worker caddy
```

Do not add redis to `deploy.sh`'s recreate list: restarting it on every deploy drops the
arq queue, and a job that was waiting stays `queued` in the database with nothing to pick
it up. Recreate it by hand only when its own configuration changes.

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

## Forms on a client's own domain (2026-11-13)

A published site's vhost used to be only `root` + `file_server`, so `/f/<slug>` 404'd on
the client's domain while working on the platform. `provisioner.VHOST_TEMPLATE` now wraps
the static handler in a `handle` block and puts `@forms path /f/* /api/public/forms/*`
first, proxying those paths to the app (`app:8000` locally; `PLATFORM_URL`/`ADMIN_DOMAIN`
for a remote target; `FORMS_UPSTREAM` overrides either). Everything else stays static - a
publishing problem must never take down a client's whole site.

`forms.public_url(form, site)` is the one link decision: the client's live domain first,
then `PLATFORM_URL`/`ADMIN_DOMAIN`, then relative. The Forms API adds `public_url` to every
form it returns; the Forms tab, builder, response header, Manager (`create_form`,
`list_forms`, `add_form_link`) and publish toasts all use it. `add_form_link` prefers the
page's site, falls back to the client's live site, and treats the old relative `/f/<slug>`
button as already present.

For a site that is already live, **Domains -> Refresh web config**
(`POST /api/sites/<id>/refresh-config`) re-renders only `<domain>.caddy` (preserving hosts
already in the file, including `www`) and reloads Caddy. It never writes `index.html` or
the page row. The click records one approved `file_write` approval naming the vhost, then
writes/reloads `preapproved` so a synchronous Flask handler cannot block the event loop.
`tests/test_form_hosting.py` pins the vhost, the refresh-only behavior, the approval row,
the route and the link choice. The public page remains on the platform too, which is what
the logged-in `?preview=1` uses.

## Guided builds - plain questions, safe drafts (2026-11-13, phase 1)

The Manager no longer invents a website interview. `build_guides.py` owns four small,
deterministic playbooks (website, blog post, sign-up form, newsletter) with plain labels,
examples, choices, "not sure" defaults and conditional questions. `build_sessions`
(migration 0029) stores the answers server-side, so a half-finished interview survives a
reload and belongs to one client. The API is `POST/GET /api/builds`, `POST
/api/builds/<id>/answers`, `/build` and `/cancel`; the build itself runs as the `build` job
task. `tools.manager.start_build` opens the card from chat; a Create button opens it from
the Studio and Content tabs.

The executor (`build_service.py`) is template-first: `site_builder.py` renders a safe,
responsive one-page site, a clean article page and plain-text email. It updates the
Business Profile, creates published forms, saves email templates and drafts a welcome
automation **switched off**. It never publishes a site page and never sends email; those
remain the normal explicit Publish/Send actions. A form build returns its live link; a
site/blog build returns a draft plus a Publish button on the result card. Blog posts can
now be published through the same `publish_deliverable` path as website pages.

The console card (`showBuildCard`) renders text, long text, choices, multi-choice,
yes/no, email/phone/domain and colour inputs, a review of what will be built/assumed, and
a result with Open in Studio, Publish, Open/Copy form link, Open emails and Review
automation. `tests/test_build_guides.py`, `test_build_service.py`, `test_build_api.py`,
`test_build_tool.py` and `build_card.mjs` are in preflight.

Phase 2 adds the full-site path. The website guide now asks "How much do you need?"
(one page or Home/About/Services/Contact/Blog) and, when multi-page, which pages.
`site_builder.render_site_page` adds a consistent navigation bar and page-specific
content; the build creates one website draft per page. The result offers **Publish site**
(`POST /api/builds/<id>/publish`), which writes every page under **one** approved audit row
using `publish_deliverable(..., preapproved=True)`, with the normal per-page version
snapshots. A **blog index** (`blog.html`) lists published blog posts and has a **Refresh blog
page** action (`POST /api/builds/<id>/refresh-blog`) for after a new post is published.
Blog URLs remain flat (`/<slug>.html`) in this release; nested `/blog/<slug>` paths are not
yet used.

The newsletter guide can now build a three-email welcome series (welcome + two follow-ups,
3- or 7-day spacing) as a single automation saved **switched off**, with a gate before each
send. Newsletter templates are now designed HTML; `tools/mail` sends multipart
(text + HTML) for SMTP and raw HTML through GHL, while the template preview renders HTML in
the existing sandboxed iframe. Plain-text templates keep their previous `<pre>` preview.
Mass broadcast to an entire contact list, a drag-and-drop editor, multilingual copy and
voice-driven build answers are still deliberately out of scope.

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

## Console navigation (grouped, 2026-11-03)

`AREAS.*.nav` in `admin.js` is the whole sidebar. The workspace is seven rows, not twenty:
**Studio** (leaf), **Overview** (Dashboard, Analytics, Usage, Jobs), **Sites** (Websites,
Domains, Funnels, Blogs, Emails, Library), **Media** (leaf), **Customers** (Contacts,
Forms, Conversations, Chatbots), **Automations** (leaf), **Account** (Business Profile,
Team, Settings - the last two admin-only). Every child keeps its old hash, so deep links,
dashboard tiles, the tour and Studio links are unchanged.

A group is a `<button class="side-group">` that expands its children in place. The group
holding the current tab is open on every render and carries a dot; other groups remember
open/closed per user in `localStorage["nav:open:<user>"]`. `flattenNav(nav)` feeds the flat
`NAV` that `isSoonTab` / `viewSoon` read, so a child is a first-class tab;
`tests/nav_groups.mjs` (in preflight) pins seven rows, twenty leaves, unique ids and
hashes, and the exact Sites/Customers/Account children. The Platform console stays flat
(eight rows; Domains is the owner-wide cleanup page).

## Provider configuration is the agency's (2026-11-03)

The model stack is white-labelled. `settings.all_for_ui(org)` returns only client-scoped
groups to a client - `email` and nothing else - so `DEEPSEEK_API_KEY` and friends never
appear on their page, and `_writable_field` refuses a client write to a platform key.
`POST /api/settings/test/<group>` answers 403 for a group a client cannot see (it used to
be a 200 with a message). `/api/health` is anonymous and no longer returns `model`,
`api_key_configured` or `providers_configured`. `/api/voice/status` and the voice session
redact model, voice, API version and endpoint for non-owners (`app._voice_public`); a
client who actually starts a Live session still receives the model inside the setup, which
the browser-to-Google socket requires. `GET /api/usage` strips provider/model from rows
and recent calls for a client (`app._hide_provider_names`), and the Usage table only shows
a Model column when the payload has one. The manager's "not configured" 503 is
role-aware; the Platform screens keep every name.

## Tenant isolation (2026-11-03)

Rows are isolated by `org_id` from the signed session (`current_org`, `list_scope`, `can_access_org`); a non-owner with no org gets scope 0, never `None`. Cross-tenant ids return 404, platform keys are unreadable and unwritable for a client, and `smoke_tenancy` walks those paths. Logical tenancy, one database, not a box per client. Uploaded media is served by a UUID name plus a row check. Generated and stock images live under the public `/artifacts/` route (a published page embeds them without a session), so their file names carry a random 16-hex token (`tools/media._safe_name`, `tools/pexels._destination`); `tests/test_artifact_paths.py` pins it. The public `/api/health` exposes only counts.

## Studio — the workspace's main tool (2026-11-01, merged with the Assistant 2026-11-02)

The front door of the workspace (`#/app/studio`, and `AREAS.app.home`): chat sessions on
the left, the conversation in the middle, the **artifact** on the right. It is not a
second Assistant - the old Assistant page became this one, because the same manager with a
canvas is strictly more useful than the same manager without one. Entry points: `Open in
Studio` in a job's header, in the preview modal, on Pages rows and on every
Library/Blogs/Funnels/Emails row. Deep links are `#/app/studio/deliverable/<id>` and
`#/app/studio/job/<id>`.

**The artifact protocol.** The browser sends only `{"kind": "deliverable", "id": 42}` -
never a title or a content blob. `app._studio_artifact()` normalises it with
`studio.normalise`, loads the row and requires `item["org_id"] == current_org()`. That
is deliberately **stricter than `_deliverable_for_request`**: `can_access_org` lets the
operator read any client's draft, but a Manager job belongs to one client, so another
client's content in its prompt would copy across tenants. No client picked -> 400; any
mismatch -> 404 (never 403, never a confirmation). `studio.deliverable_block()` builds
the prompt block: id, kind, title, status, the `save_deliverable(deliverable_id=...)`
instruction, then the current content capped at 12000 characters.

**Where the context goes.** It is the *first* thing in the prompt for
`POST /api/manager/chat` (before history and before `automation_id`'s context) and for
`POST /api/voice/tool`'s `delegate_to_team`. That order is load-bearing:
`answer_team_question` copies the previous prompt with a 4000-character cap, so the id
must never be the part that is cut; the voice block therefore caps content at 2600
characters. `manager.py`'s SYSTEM has the matching rule (edit that draft with
`deliverable_id`, never a duplicate), and `start_job(prompt, title, deliverable_id=)` puts
the id in the child prompt so delegated work edits the open page too.

**The target is bound to the job.** A prompt instruction is advice, not a guarantee:
`save_deliverable(deliverable_id=...)` only lands on that row if the model repeats the id,
and a delegated child used to save a sibling when it didn't. `jobs.deliverable_id`
(migration `0023_studio_targets`) is set by `POST /api/manager/chat` from the validated
artifact (else the chat's remembered draft) and by `/api/voice/tool`; `start_job` inherits
the parent's when the model passes none, and the child job carries it; and
`tools/deliverable.save_deliverable` falls back to it when the argument is missing. A
foreign id is still refused - it falls back to the bound draft, never to a sibling.
`assistant_sessions.deliverable_id` records which page a chat is about, written by the
same route and by `POST /api/assistant/sessions/<id>/draft` (validated through
`_studio_artifact`), so reopening the chat can put the draft back on the canvas.

**Finding what a run wrote.** `GET /api/deliverables?job_id=N` (scoped by `list_scope()`)
reuses the deliverables list rather than a new outputs endpoint. `update_deliverable` now
records the writing `job_id` too, so an *edited* draft is found by the same query - that
is what lets the canvas follow an in-place refinement, not only a newly created page. The
canvas watches every job id it is given plus every `Manager/child_job` link, polls
`/api/jobs/<id>/events?after=`, scans outputs when a `save_deliverable` result appears and
again on completion, and re-reads the open draft by id.

**The page** (`viewStudio` in `admin.js`, `.studio-*` in `admin.css`): three panes. The
left **rail** is the chat list (`GET /api/assistant/sessions`, newest first, "+ New"),
reusing the `.asst-*` classes the old Assistant page had - below 1180px it becomes a
horizontal strip above the panes. The middle pane has the "Working on: Deliverable #42 ·
title" chip and a recent-drafts picker, the thread (same
`bubble`/`thinking`/question-card shapes), the composer and Talk. The right pane has
**Live job** (events, question card, approvals for the watched ids) and **Preview**
(sandboxed iframe, desktop/mobile width, refresh, Open full) with automatic switching:
Live when a job starts (unless it asked a question), Preview when one saves.

**One tool.** The Assistant tab is gone: the Studio is `AREAS.app.home`, `#/app/assistant`
redirects to `#/app/studio` (an old bookmark, the tour, the sheet's "Full screen" link),
and the floating orb hides on the Studio because the Studio has its own composer and Talk.
A chat is created on its first message and remembered in `studio:session:<org>:<user>`;
**+ New** clears the thread *and the canvas* - a new chat is a new piece of work - while
the previous draft stays in the Library. Opening a chat restores its `deliverable_id`
(a deleted draft clears the canvas). Switching chats or starting one is blocked while the
team is working, so a running job cannot be orphaned mid-answer.

**The canvas follows the chat.** `setArtifact` posts the draft to
`POST /api/assistant/sessions/<id>/draft` whenever it changes, and a session created for a
page that was already open records it as it is created. While a job runs, `pollOnce` also
re-reads the open draft (`refreshArtifact`), so an in-place save flashes on the canvas
before the job finishes; `?job_id=` covers the same ground for a draft the run created.

**The Studio lands where it was.** Both the open draft and the current chat are remembered
per user *and acting client* (`studioOrgKey`), so one operator login keeps a separate slot
for every client instead of sharing one and wiping it on a workspace switch. Mount order is:
deep link -> the chat's own `deliverable_id` ->
`localStorage["studio:draft:<client>:<user>"]` -> the newest chat for this client -> nothing.
`openSession` resolves only after the canvas decision, a chat that names no page no longer
clears a draft that is being restored, and a remembered chat that has since been deleted
falls back to the newest one rather than a blank thread. `setArtifact` links the draft to
the chat even on a restore, so opening a page from Library and reloading keeps both. The
voice's `getContext` falls back to the draft store; **+ New** clears it.

**Kept across app tabs.** The Studio owns the voice socket and the job pollers, so
`render()` no longer destroys it when another app tab opens: the `.app-shell` node is
*moved* into a hidden `#studio-kept` holder (listeners, closures and the voice panel
intact), its cleanups are parked on `studioKept.cleanups`, and a `.studio-strip` in the
corner shows the voice phase and the last message with **Talk** and **Open**. Returning to
`#/app/studio` puts the same node back (`restoreStudio`) and rewrites the URL to the
remembered draft; a deeper Studio link (a specific draft or job) and the Platform console
discard the kept session (`discardStudio` runs its cleanups, which stops the voice). The
Ask orb hides while a Studio is open or kept, so there is one door, not two.

**In-page artifact switches use `history.replaceState`, never `location.hash`.** A
`hashchange` calls `render()`, which rebuilds the shell and would destroy the thread and
the docked voice host mid-sentence. Mobile collapses the two panes to Chat/Live/Preview
buttons (`data-pane`).

**Docked voice.** `AscendVoice.open({host, compact: true, getContext})` moves the one
existing `.voice-panel` into the Studio's host and adds `.voice-docked`; the overlay,
pill, Esc-to-close and focus trap are skipped while hosted, and `stop()` puts the panel
back. `getContext` is read at call time and its value is sent as `artifact` on
`delegate_to_team` only. One channel owns a question at a time: while the voice is on and
the team has asked, Send is disabled with a `Type the answer` escape hatch; while a typed
turn or any watched job runs, Talk is disabled.

**Guards.** `tests/smoke_studio.py` (in preflight) pins tenant rejection with no job
created, the prompt's id/title/content and its refusal of browser-supplied content, the
voice block fitting the follow-up cap, `?job_id=` scoping, `start_job`'s id hand-off, a
job bound to a draft editing it **without** the model passing an id, a delegated child
inheriting the parent's target, an in-place edit being found by `?job_id=`, and a chat
remembering its draft (including refusing another client's), plus the static wiring across
all four client files. `tests/voice_ui.mjs` mounts the docked panel and proves the artifact
rides the delegated call. `tests/smoke_deliverables.py` proves a foreign `deliverable_id`
leaves that draft untouched.

**Limits.** Generated drafts and live jobs only; `write_site_content`, forms and
automations are not on this canvas yet. The preview refreshes when a save lands or a job
finishes - it is not a token stream. A rename through `save_deliverable` does not stick
(the update path ignores `title`), and neither does a kind change: the block's example
names the draft's current kind and `save_deliverable` prefers the kind the prompt asked
for. Typing an answer to a *voice* question starts a new
manager turn with the question and the draft, not the original brief.

## Reviews are read-only; publishing is explicit (2026-11-12)

The QA incident is the reason this section exists. A Manager QA request was delegated with
`start_job(deliverable_id=33)`, which appended "Save the finished document..." to the child
prompt; the team had no way to fetch the live page, so it wrote a checklist and saved it over
draft #33. The live page survived only because nobody republished. Four locks now make that
path impossible:

1. **Intent at the entry point.** `intent.looks_read_only()` is conservative: explicit
   "do not change/republish" wording, "read-only", or a review verb on a page/site with no
   edit verb. `POST /api/manager/chat`, `POST /api/voice/tool`'s `delegate_to_team`, and
   `POST /api/jobs` record the answer in `jobs.read_only` (migration `0026_job_read_only`).
   `start_job(..., edit=False)` sets it explicitly; a read-only parent forces every child
   read-only; a strong no-write prompt forces it even when the caller forgot.
2. **No write tools.** `manager.granted_for()` and `agents.build_team()` filter a read-only
   turn through `tools.READ_ONLY_TOOLS`; the roster's `CONTENT_RULE` is replaced by
   `agents.READ_ONLY_RULE`, so no agent is told to "finish by calling save_deliverable".
   `start_job` no longer appends the save line; it prepends `READ-ONLY REVIEW`, names the
   target URL, and tells the team to say what it could not check.
3. **Tool-level refusal.** `save_deliverable`, `publish_page`, `provision_site` and
   `write_site_content` check the job row and refuse a read-only job even if a stale roster
   or leaked tool markup reaches them. The Manager's leaked-markup path (`_run_named`) uses
   the same allowlist as the structured tool list.
4. **Real verification.** `webcheck.inspect()` + `tools/web.fetch_page()` fetch one page on
   this client's site (redirect hosts and private IPs checked), and report status,
   title/meta, H1/H2, links/images with HTTP status, viewport/media-query markup,
   placeholders, and whether it is the starter page. `rendered: false` is explicit: it does
   not claim a visual desktop/mobile check. It is granted to the Manager and the Operator,
   so the Manager no longer says "I can't load the URL".

**Recovery is still layered.** `deliverable_versions` (migration `0027`) snapshots the
previous title/content/status before every content change; `GET/POST
/api/deliverables/<id>/versions...` plus History/Restore in the preview put one back. A
content change to a published page sets it back to `draft` - publishing is only the publish
route, and the Pages badge says "draft changes" while the old file is still live.
`site_versions.path` (migration `0028`) now names the file a snapshot came from; a subpage
publish records that subpage, not the home page, and `versions.restore()` writes the file
through the provisioner with an approval row instead of only updating `sites.content_html`.

**Publishing names its target.** `publish_deliverable(..., as_home=False, replace=False)`:
`as_home` writes `index.html` and records the slug as `index`; otherwise the slug decides
`/<slug>.html`. Before writing it reads the target. A non-empty, non-starter target with
different content needs `replace=True`; the console's Publish dialog names the URL and is
the confirmation, while the voice/Manager tool returns "ask the user" and only proceeds
after a yes. The starter root (`provisioner.is_starter_content`) may be replaced by the first
home publish. A subpage publish on a starter root warns that the root is still the starter.
The dialog defaults to Home page when the site root is the starter; the Manager and voice
must say the exact URL, root or subpage, not just "live".

`repair_deliverable.py --id N --from-live` is the one-off for a draft whose content was
destroyed after the page was already live: dry-run by default, prints the live title/H1 and
size, `--apply` puts the live content back (the contaminated content stays in History).
Tests: `test_read_only_jobs.py` replays the incident; `test_publish_home.py` covers
home/subpage, replacement and per-file restore; `test_fetch_page.py` runs a local server;
`tests/publish_state.mjs` pins the console wiring. Migration head is `0028_site_version_path`.


## One chat thread across typing, the Ask sheet and voice (2026-11-12)

Before this, the Studio typed chat used `assistant_sessions`, the floating Ask sheet kept
its own last-8 array (`history: history.slice(-8)`), and the voice sent no session at all.
Closing the sheet started a blank conversation, the voice Manager never saw the typed
turns, and the sheet's voice turns never reached the Manager. The thread is now one server
session per user + acting client:

* `admin.js` has `studioSessionKey()` / `readStudioSession()` / `writeStudioSession()` next
  to the draft store, plus `ensureChatSession()` creating the session on first use. The key
  is still `studio:session:<acting-client>:<user>`, so existing Studio sessions survive.
  The Studio starts from it, `sendTurn` adopts it if a sheet made it, and **+ New** clears
  it. The sheet has a **New** control with the same meaning.
* The Ask sheet loads the active session's messages when it opens, renders the last 30,
  and sends `session_id` on every typed turn instead of a browser `history`. Its screen
  hint goes in a `context` field to `POST /api/manager/chat`; the server puts that in the
  Manager prompt but stores only the user's words, so the chat title is not
  `[context: ...]`. A stale session is cleared and retried once, in the Studio and the
  sheet.
* `voice.js` sends `session_id` on `/api/voice/tool` calls and persists each final
  user/assistant voice turn to `/api/assistant/sessions/<id>/messages`. The callers'
  `onTurn` is only the visible copy. The Studio no longer double-persists.
* `POST /api/voice/tool` validates the session against the signed-in user and acting
  client, adds a compact `Conversation so far:` transcript to the Manager prompt (newest
  turns first when trimming; the draft block stays first, the instruction last, inside the
  4000-character follow-up cap), and returns `session_id`. A supplied but unknown id is a
  404 for the client to retry; a missing id is the scripted welcome.
* The welcome and tour are **not** a chat. They open with `persistTurns: false` and no
  session. If the user asks for real work there, the route creates the first session,
  stores that request as a user turn linked to the job (so `record_reply_for_job` stores
  the detailed Manager answer), returns the id, and the voice client persists subsequent
  turns. The scripted welcome/tour turns never enter the thread.
* `studio:session` has no schema change; `assistant_sessions` / `assistant_messages`
  already held the data. Past sheet-only turns were never stored and cannot be recovered.

Tests: `tests/test_chat_thread.py` (typed history, voice job history, welcome first
request, foreign/stale session), `tests/studio_session.mjs` (shared store and sheet/Studio
wiring), and `tests/voice_ui.mjs` (`session_id` on the tool call and both spoken turns
posted to the session). The full preflight includes all three.


## Usage & credits (2026-11-01, phase 1: meter only)

Every billable call is recorded with the token split the provider actually billed, and
priced from a rate card in **dollars**. Phase 1 is shadow mode: nothing is blocked, no
UI yet. The point is to see real numbers for a week before anyone is charged.

**Units.** `1 credit = $0.001`; the database stores **milli-credits** ($0.000001) so a
cheap call never rounds to zero. Rates are dollars per 1M tokens / per minute / per
image, because that is what the provider dashboard shows. With tokens and dollars both
per million, `milli = tokens x dollars_per_1M`.

**The sale price.** A credit is a cost unit; what it sells for is `CREDIT_PRICE_USD`
(Settings -> Usage & credits, default **$0.05**), the only place credits become money.
`usage.credit_price()` / `credit_markup_percent()` / `credit_margin_percent()` feed
`_org_usage(..., pricing=...)`, and those fields are added **only for the operator** - a
client's `/api/usage` carries no `credit_usd`, no markup and no margin, and the client
Usage page shows credits only. The charge never depends on the price: `credits_milli` is
cost, so reconciliation against provider invoices stays exact. At $0.05 a credit is a
4,900% markup / 98% margin; `docs/credits-and-pricing.md` has the arithmetic.

**Which tokens.** Providers disagree, so `usage.normalise_openai` / `normalise_anthropic`
produce four buckets - `input_uncached`, `input_cached`, `cache_write`, `output` -
plus `reasoning` (recorded, already inside output for OpenAI). Anthropic's
`input_tokens` excludes cache reads/writes; OpenAI's `prompt_tokens` includes the cached
subset; DeepSeek reports `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`. Folding
them into one total overcharges every cache-read call.

**Where the raw numbers come from.** AutoGen 0.7.5 keeps only
`RequestUsage(prompt_tokens, completion_tokens)` and discards cache fields, reasoning and
the returned model name. So `llm.py` wraps the SDK resource itself:

* `_instrument_openai_client` wraps `client._client.chat.completions.create` (and the
  beta `parse`); a stream is teed through `_tee_openai_stream` to read the final chunk's
  `usage` and `model`.
* `_patch_anthropic_client` (which already sanitised empty blocks) now also reads
  `message_start.message.usage` (input + both cache fields) and `message_delta.usage`
  (output) on a stream, or `response.usage`/`response.model` otherwise.
* Metered subclasses (`_MeteredMixin` + `_metered_openai_class` /
  `_metered_anthropic_class`) are built inside `get_model_client`, so every agent, the
  Manager and the selector are covered without touching their code. `agents.build_team`
  passes `usage_label=<agent name>`; the Manager passes `manager`; the selector `selector`.
* OpenAI-compatible streams force `stream_options.include_usage`; if a provider rejects
  it the request is retried once without it and the event is marked `autogen-fallback`.
  A missing capture also falls back to AutoGen's pair and is marked the same way, so a
  gap is visible instead of silent (`usage_source`: raw | autogen-fallback | missing).

**Attribution.** `jobs.run_job` wraps the task in `usage.bind(org_id, requester_id,
job_id)`, so a model call inherits the client and job without the tool knowing either.
The job transcript ends with `Usage: N credits` (and how many calls are unpriced). Widget
replies (`app.py`, plain OpenAI SDK) record themselves against the bot's org; voice is
billed for the seconds its session was held open (see phase 3 below); `generate_image`
records one image.

**Money.** `usage_events` is the record; `credit_ledger` is append-only truth;
`credit_balances` caches the sum (updated in the same transaction, with an
IntegrityError fallback for two workers creating the row at once); `rate_card` is the
price list. The credits a call cost are snapshotted on the event, so editing a rate
never rewrites history. `usage.reconcile(org)` asserts ledger == cached balance.

**Operating it.**

```bash
python -m usage calibrate                 # one tiny live call per provider: prints the raw split
python -m rates import rates.json         # fill the card from the dashboard (dollars)
python -m usage show [org_id]             # balance + spend by kind/provider/model/agent
python -m usage grant <org_id> <credits> [note]
```

The full arithmetic - units, the four token buckets, voice and images, rounding, and a
pricing worksheet with margin formulas - is in `docs/credits-and-pricing.md`.

`USAGE_ENFORCE` is `shadow` (default) | `warn` | `hard`. A client with **no balance row**
is not on the credit system and is never blocked; `hard` only refuses at zero for a
client that has a ledger. An unpriced model records `credits_milli NULL` and appears in
`rates.missing_rates()` - it is never priced at a default.

**Guards.** `tests/test_usage.py` (preflight) pins the three provider shapes, the
four-bucket price, cache-aware totals, the zero-rate fallback, missing-rate flagging,
the ledger/balance agreement, attribution via `bind`, enforcement modes, the voice/image
units, and the SDK wrappers (non-stream, stream, Anthropic cache fields, meter
fallback). `test_db_dialect` and `test_migrations` include migrations
`0021_usage_credits` and `0022_voice_and_reconciliation`; `smoke_llm_gateway` accepts the
metered client type. Phase 3 adds voice settlement (nothing at mint, elapsed on close,
sweep for abandoned tokens, one settle per token, owner only, text/plain beacons) and the
invoice check (zero variance when the numbers match, a negative variance when they do
not, newest first, malformed months refused) to `test_usage` and `smoke_usage_api`.

**The screens (phase 2).** Workspace → **Usage** (`viewUsage`) shows the client its
balance, plan, allowance, enforcement mode, a low-balance banner, spend by
kind/provider/model/agent (input, cached input, output, credits), recent calls, and an
"Unpriced models" warning. Platform → **Usage** (`viewPlatformUsage`) lists every
client with plan/allowance/balance and a **Credits** action (positive = top-up,
negative = adjustment), a **Grant monthly now** button, and the **rate card** in dollars
with add/edit/delete plus the unpriced-model list. Settings → **Usage & credits** holds
`USAGE_ENFORCE` (shadow/warn/hard), the per-plan monthly credits and `USAGE_LOW_WATER`.
`usage.grant_monthly()` runs in the scheduler tick and is idempotent per
`(org, reason='grant', period='YYYY-MM')`, so an hourly sweep or a restart grants once.

API: `GET /api/usage` (own meter; the operator with no client picked gets every client),
`GET /api/orgs/<id>/usage`, `POST /api/orgs/<id>/credits`, `POST /api/usage/grant-monthly`,
`GET|POST /api/usage/rates`, `DELETE /api/usage/rates/<id>`. Credits and rates are
`owner_required`; a client cannot read another client's usage.

**The sidebar bar.** `creditMeter` (under the username, in the sidebar foot) shows the
acting client's credits used this calendar month against the plan's credits -
`1,240 / 5,000 credits used` - and the track fills as the month is consumed, turning red
at 100% or at `LOW_WATER`. No plan: the tank is what was added this month (grants,
top-ups); neither: the usage number alone. `usage.spent_this_month` reads the ledger's
`reason='usage'` rows for the current month, so a refund does not lower usage. A client
with no ledger row hides the bar; the operator on *All clients* gets a muted
`Usage - pick a client` row, because the number is per client. It reads `GET /api/usage`, caches
for 15 seconds so tab clicks do not each fire a request, and clears that cache when
`POST /api/session/org` switches client. Clicking it opens the Usage tab.
`tests/credit_bar.mjs` (in preflight) evaluates the real `creditMeter` and pins the
width, the fallbacks, the over-budget red, the operator hint and the cache.

**Voice accuracy (phase 3).** The reservation is no longer charged at mint. `POST
/api/voice/session` inserts a `voice_sessions` row (token, org, user, reserved seconds,
provider, model) and returns its id; nothing is billed yet. When the user stops,
`admin/voice.js` posts the token to `POST /api/voice/usage` (sendBeacon, else keepalive
fetch, plus a `beforeunload` for a closing tab). The route bills **elapsed seconds measured
by the server from the mint**, capped by the reservation: a browser cannot claim a shorter
call, and a tab left open overnight is not a surprise invoice. A session that never comes
back is settled by `usage.sweep_voice_sessions()` in the scheduler tick at its full
reservation, so a crash is never free. A closed token cannot be settled twice (claimed by
`UPDATE ... WHERE status='open'`), and only the user who minted it may settle it.

**Invoices (phase 3).** `usage_reconciliations` stores one row per provider invoice:
provider, `YYYY-MM`, the dashboard total in dollars, and our computed cost for the same
month (`SUM(credits_milli)` over `usage_events`). `variance = computed - billed`, so a
positive number means the meter charges more than the invoice. Platform → Usage →
**Reconcile against the invoice** files one per month and lists the history, with a
negative variance in red. API: `GET|POST /api/usage/reconciliation` (`owner_required`).

```bash
python -m usage reconcile deepseek 2026-11 12.34 "invoice #123"
```

**Next:** a top-up flow with a payment processor, and turning `hard` on once a real
invoice reconciles within a few percent.

## Voice — the spoken assistant (2026-10-30)

Browser-direct Live socket, one-use token minted by `voice.py`, key never leaves the
server. `POST /api/voice/session` returns the token, the setup JSON, the constrained
endpoint, the model and the API version. `POST /api/voice/tool` exposes two tools:
`delegate_to_team` starts a normal `chat` job after `jobs.submit`, and
`answer_team_question` continues the job this voice session started when the team asked
a question. The model never supplies a job id; the server reads it from the signed
session and checks the requester and tenant.

The six things that must stay true (each has a guard in `tests/smoke_voice.py`):

1. **Model and API:** default `gemini-3.8-live` on **v1beta**. Ephemeral Live
   tokens are v1beta-only; the v1alpha endpoint could mint a token but never complete
   a constrained session. `gemini-2.5-flash-live` and `gemini-live-2.5-flash-preview`
   are retired; `voice.model()` maps them to the default and `voice.model_note()` says
   so. `GEMINI_LIVE_MODEL` overrides. `GEMINI_LIVE_API_VERSION` is fixed to v1beta.
2. **Setup:** `inputAudioTranscription: {}` and `outputAudioTranscription: {}` are not
   optional - without them a working session has no text at all. So is
   `realtimeInputConfig.automaticActivityDetection.disabled: false`.
3. **Order:** `getUserMedia` first, then `/api/voice/session`. The token's
   `newSessionExpireTime` is 60 seconds; a permission prompt read slowly used to spend it.
4. **Gate:** "listening" is set only by `handleSetupComplete`; `ws.onopen` says
   "connecting". A 12-second setup timer turns a silent open socket into a visible error.
5. **Failure is visible:** `message.error`, `goAway`, and `onclose` code/reason all render
   the server's words, with Copy diagnostics. A close before setup reads "rejected".
6. **The team bridge is one-way at a time:** after a tool response, `admin/voice.js` polls
   `/api/jobs/<id>/events`. A `Manager/question` becomes an internal `clientContent`
   update, which the voice reads to the user. The answer calls `answer_team_question`;
   the server builds the next manager turn from the previous prompt plus the Q/A. A
   `Manager/child_job` event is followed the same way, so background team work gets a
   completion. A pending approval is announced, never approved by the voice. The
   browser waits for `turnComplete` and the audio queue to drain before injecting an
   update, so it does not talk over "on it" or the question itself.
   `tests/voice_ui.mjs` drives the question and completion through a fake socket.

The overlay (`admin/voice.js` + `.voice-*` in `admin.css`) is opened by the sheet's Talk
button, and by the Assistant page and tour through the same `AscendVoice.open`. It is a
mode: orb scaled from mic/output RMS, live transcripts coalesced by turn, Minimise keeps
the session alive, Stop/Esc end it. The same panel can be **docked**:
`AscendVoice.open({host, compact: true})` moves it into a host (the Studio), where the
overlay, pill, Esc and the focus trap are skipped and `stop()` returns it.
`Settings -> Voice -> Test` checks the v1beta Live
models and then runs `voice.probe_session()`: it mints a token with
`bidiGenerateContentSetup` (the complete setup: model, audio modalities, tools and
transcription), opens the exact constrained socket server-side, sends the same
`voice.setup()` frame the browser uses, and waits for `setupComplete`. The Test reports
Google's actual setup error or `setupComplete received`; it no longer calls a bare
token mint "ready".
`POST /api/voice/session` calls `voice.model_availability()` before minting and sends
the same setup object into `voice.mint(live_setup=...)`, so the token constraints and
the browser setup cannot drift. A network failure of the model list does not block the
session (`checked=False`). `tests/voice_ui.mjs` drives the real client against a fake
DOM, AudioContext and WebSocket, so the gate and the failure paths are tested without a
browser or a key.

The text model in **AI providers** (`GEMINI_MODEL`, e.g. `gemini-3.8-flash`) is not the
voice model; voice uses `GEMINI_LIVE_MODEL` (`gemini-3.8-live` by default). The Test
button is the source of truth for whether the key can complete a constrained Live
session with that model.

**Transport details.** The Live server sends `setupComplete` as `{}` and the frames as
binary. Python checks `"setupComplete" in message`, not the truthiness of the empty
object. `admin/voice.js` sets `ws.binaryType = 'arraybuffer'` and decodes the
`ArrayBuffer` with `TextDecoder` before `JSON.parse`; otherwise a browser receives a
valid success frame, drops it as unparseable, and waits for the 12-second timeout.

Known gaps, in the order they would bite:

* `_USED` minutes and `_VOICE_TURNS` dedupe are per process; uvicorn's two workers can
  each allow a daily cap and can miss a duplicate turn across workers.
* No session resumption: a network blip ends the turn and the one-use token needs a new
  mint. The overlay offers Try again; it does not resume.
* `MUTE_WHILE_SPEAKING` is false because cutting mic frames also cuts barge-in. Switch it
  only if echo is measured on a real device.
* Ephemeral tokens are v1beta-only in the current Gemini docs; the installed
  `google-genai` SDK still carries an older v1alpha warning. This build uses the raw
  v1beta WebSocket path with a constrained token and no SDK Live session.
* The real handshake is now exercised by `Settings -> Voice -> Test`, which opens the
  constrained socket server-side and waits for `setupComplete`. If it fails, the Test
  prints Google's error; the browser's Talk path uses the same setup and token.

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
   Settings -> Voice -> Test checks the v1beta models and opens the real constrained
   socket; it reports `setupComplete` or Google's exact setup error.
4. If `gemini-3.8-live` is not listed, set Settings -> Voice -> Gemini live voice model
   to one that is. Do not switch to v1alpha: ephemeral Live tokens are v1beta-only.
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
