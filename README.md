# AscendPlatform

A self-hosted **AI growth platform** for agency-managed clients: a Business Profile
that becomes the AI's knowledge base, a Studio for building pages and content, forms
and lead capture, website hosting with automatic TLS, multi-bot chat, and background
agent jobs with live transcripts and approvals.

> The production topology is Docker Compose: Caddy + ASGI app + PostgreSQL + Redis.
> For launch operations, backups, restore drills and suspension, read
> [`docs/operations.md`](docs/operations.md).

Each chatbot has its own:
- **Name, assistant name, brand color, logo** — fully rebrandable widget
- **Welcome message + suggested questions** — quick-reply chips
- **Knowledge base** — the AI answers *only* from this text (RAG)
- **AI personality** — custom system prompt
- **Analytics** — message counts + most-asked questions

Powered by the **DeepSeek** model (`deepseek-chat`) via its OpenAI-compatible API.

---

## Features

- 🔐 **Multi-user accounts** (admin + member roles, password login)
- 🤖 **Unlimited chatbots** — create / edit / delete from a web dashboard
- 📊 **Per-bot analytics** — conversations, messages over time, top questions
- 💬 **Meta channels** — answer DMs on Facebook Messenger, Instagram, and WhatsApp (webhook-driven)
- 🧩 **Embeddable widget** — one `<script>` tag per bot
- 🎨 **Live preview** while you build a bot
- 🎙️ **Voice mode** — talk to the assistant; Gemini Live answers and delegates, with live transcripts
- 🖼️ **Media library** — upload images, video and PDFs for emails and pages; alt text and a description are required
- 💬 **Assistant chats** — start a new chat, reopen an earlier one, and keep voice turns in the same history
- 🗄️ **PostgreSQL** in production; SQLite remains available for local development
- ⚙️ Runs as a **Docker Compose** stack (Caddy + app + PostgreSQL + Redis)
- 🔔 **Lead notifications** — a form submission emails the client's admins
- 🧾 **Operator controls** — credits, plans, suspension and approval history

---

## Architecture

```
[ Admin panel  /admin ]   +   [ Public widget  /widget.js ]
                │
                ▼
[ ASGI: FastAPI + Flask ]   container, proxied by Caddy
      ├── PostgreSQL (SQLAlchemy + Alembic)  — users, bots, messages, channels,
      │                                         jobs, business_profiles
      ├── Redis                     — reserved for the S3 agent worker (arq)
      ├── DeepSeek / Claude / Gemini APIs
      └── Meta Graph API        — Messenger / Instagram / WhatsApp replies

[ Meta webhook  /webhooks/meta ]  ◀── Messenger / Instagram / WhatsApp events
```

Caddy is the edge: it terminates TLS automatically (no certbot), reverse-proxies
the app, and will serve hosted customer sites in stage S5.

The app is served over **ASGI**: `asgi.py` runs FastAPI and mounts the Flask app
beneath it, so route groups can move to async one at a time. Both halves share the
same signed session cookie, and FastAPI publishes an OpenAPI schema at `/api/docs`.

---

## Repository structure

```
asgi.py                       # ASGI entry: FastAPI (SSE + providers) over Flask  [production]
app.py                        # Flask backend (API, auth, chat, admin static, Meta webhooks)
db.py                         # Database layer (SQLite or PostgreSQL via DATABASE_URL)
profile.py                    # Business Profile -> the AI's knowledge base
agents.py                     # Designatable agent roster -> AutoGen team
tools/__init__.py             # Tool registry (FunctionTools granted per agent)
tools/policy.py               # Command + path policy (deny-by-default)
tools/ssh.py                  # SSH transport with host-key pinning
tools/site.py / media.py / mail.py   # Hosting, image (Gemini) and email tools
actions.py                    # Approval gate (dry-run unless ALLOW_LIVE_ACTIONS=1)
provisioner.py                # Sites: DNS check, vhost, content, reload (local/SSH targets)
sites.py / servers.py         # Site and SSH-host records
llm.py                        # Multi-provider model clients (DeepSeek / Claude / Gemini)
jobs.py                       # Job engine (in-process or arq/Redis) + SSE-friendly events
worker.py                     # arq worker entry point (`python -m worker`)
meta.py                       # Meta (Messenger / Instagram / WhatsApp) webhook + Graph API helpers
alembic/  + alembic.ini       # Schema migrations
admin/
  index.html / admin.css / admin.js   # admin panel (vanilla JS, no build step)
widget/
  index.html / widget.js              # embeddable per-bot widget
knowledge/
  cheryl_ai_knowledgebase.txt         # sample document (seeds the first Business Profile)
tests/                                # smoke, SSE, agent and dialect tests
deploy/
  install.sh                          # one-command VPS installer (Docker + Caddy)
  backup.sh / restore.sh              # encrypted backup and fail-closed restore
Dockerfile / docker-compose.yml / Caddyfile   # production stack
litellm-config.yaml                           # optional LiteLLM gateway models
```

---

## Local development

```bash
python -m venv venv
venv\Scripts\activate              # Windows   (Linux/macOS: source venv/bin/activate)
pip install -r requirements.txt

# Run the full app (FastAPI + Flask). This is also the production entry point.
uvicorn asgi:app --reload          # http://127.0.0.1:8000/admin/  (API docs: /api/docs)
```

No `.env` is needed. Everything except the first-run bootstrap below is set in the
admin **Settings** tab, which stores values in the database and overrides `.env`
(and applies without a restart).

`.env` remains the right place for the two things that cannot come from the database:
- `ADMIN_EMAIL` / `ADMIN_PASSWORD` — used once, to seed the first admin account
  (defaults `admin@example.com` / `admin123`).
- `SECRET_KEY` — otherwise generated into `.secret_key` on first run.

Add `DEEPSEEK_API_KEY` (plus `PEXELS_API_KEY`, `GEMINI_API_KEY`, SMTP, …) in the
Settings tab and press **Test** to confirm each one.

> `python app.py` still works for quick Flask-only work, but it does **not** serve
> the routes that have moved to FastAPI (currently the SSE job stream and
> `/api/providers`).

On first run it seeds:
- an **admin account** from `ADMIN_EMAIL` / `ADMIN_PASSWORD`,
- a first sample bot from `knowledge/cheryl_ai_knowledgebase.txt`,
- a **Business Profile** for that account from the same sample document.

Migrations (needed for PostgreSQL; SQLite is created automatically):

```bash
python -m alembic upgrade head
```

The app itself never runs this — it calls `db.init_db()`. The container does, which
is why `tests/test_migrations.py` runs the whole chain against an empty database.

---

## Configuration (.env)

| Key | Purpose |
|-----|---------|
| `DATABASE_URL` | `sqlite:///data/app.db` (dev) or `postgresql+psycopg://...` (production) |
| `BRAND_NAME` | Product name shown in the UI (default `AscendPlatform`) |
| `ADMIN_DOMAIN` | Public hostname Caddy serves and issues a TLS certificate for |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | Postgres credentials used by docker compose |
| `REDIS_URL` | Redis connection (used when `JOB_BACKEND=redis`) |
| `JOB_BACKEND` | `local` (default, in-process) or `redis` (arq worker) |
| `AGENT_ROUTER_PROVIDER` / `AGENT_ROUTER_MODEL` | Model that routes the team in selector mode |
| `AGENT_MAX_MESSAGES` | Hard stop for a team run (default `20`) |
| `SSE_MAX_SECONDS` | Cap on one SSE stream (default `600`) |
| `SERVER_IP` | Public IP the Sites tab tells users to point their DNS at |
| `SITE_ROOT` / `CADDY_SITES_DIR` / `CADDYFILE` / `CADDY_RELOAD_CMD` | Where site content and per-site web-server config live, and how Caddy is reloaded |
| `ALLOW_LIVE_ACTIONS` | **Blank = dry-run.** Set to `1` to let actions really execute (they then need approval) |
| `APPROVAL_TIMEOUT` | How long a live action waits for approval (seconds) |
| `SSH_TIMEOUT` / `SSH_CMD_TIMEOUT` / `SSH_MAX_OUTPUT` | SSH connect timeout, command timeout, output cap |
| `SMTP_*` | Outbound email for the `send_email` tool |
| `NOTIFICATIONS_ENABLED` | Email client admins about new leads and automation notices (`1` by default) |
| `ARTIFACTS_DIR` | Where generated images/files are written |
| `PEXELS_API_KEY` | Pexels stock photography for `search_photos`, `fetch_photo` and `import_photo_to_media` (free key at pexels.com/api) |
| `PEXELS_PER_PAGE` / `PEXELS_TIMEOUT` / `PEXELS_MAX_BYTES` | Search page size, request timeout, download size cap |
| `DEEPSEEK_API_KEY` | DeepSeek API key |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | Claude (optional) |
| `GEMINI_API_KEY` / `GEMINI_MODEL` / `GEMINI_IMAGE_MODEL` | Gemini chat + image (optional) |
| `LLM_GATEWAY_URL` / `LLM_GATEWAY_KEY` | Route every provider through a LiteLLM gateway (optional) |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | Seeded admin login (first run only) |
| `SESSION_COOKIE_SECURE` / `SESSION_HOURS` | Secure session cookie (on in Compose) and session lifetime |
| `HOST` / `PORT` | Bind address (default `127.0.0.1:5000`) |
| `MAX_KB_CHARS` | Cap on knowledge-base size injected into the prompt |
| `GRAPH_API_VERSION` | Meta Graph API version (default `v21.0`) |
| `META_APP_SECRET` | Optional global Meta app secret for webhook signature checks |
| `META_VERIFY_TOKEN` | Optional global Meta webhook verify token |

Most of these can also be set from the admin **Settings** tab (see *Using the admin
panel*), which stores them in the database and takes precedence over `.env` — no
restart needed. Values that choose the topology (`DATABASE_URL`, `REDIS_URL`,
`JOB_BACKEND`, `ARTIFACTS_DIR`, bind host/port, `LLM_GATEWAY_URL`) stay `.env`-only
and appear read-only there.

## Deploy to the VPS (Ubuntu, Docker Compose)

The stack runs under Docker Compose: **Caddy** (reverse proxy + automatic TLS), the
ASGI app (uvicorn), **PostgreSQL**, **Redis** and the worker. Caddy replaces nginx and
certbot — nothing else to install, and no control panel required.

### Prerequisites
- Ubuntu 22.04 / 24.04 VPS with ports **80** and **443** open.
- Your domain's **A/AAAA record pointed at this server** (Caddy issues the TLS
  certificate on the first request, so DNS must resolve first).
- At least one LLM key: DeepSeek, Anthropic and/or Google Gemini.

### One-command install

```bash
sudo git clone https://github.com/TheCowMoo/ai.ascendplatform.site.git /opt/ascendplatform
cd /opt/ascendplatform
sudo ADMIN_DOMAIN=ai.ascendplatform.site bash deploy/install.sh
```

The installer installs Docker, copies the stack to `/opt/ascendplatform` (skipped
when you cloned straight there), prompts for your keys, generates the Postgres
password and session secret, then runs `docker compose up -d --build`.

Non-interactively:

```bash
sudo ADMIN_DOMAIN=ai.ascendplatform.site \
     ADMIN_EMAIL=you@example.com ADMIN_PASSWORD=secret \
     DEEPSEEK_API_KEY=sk-xxx \
     bash deploy/install.sh
```

### What the installer does
1. Installs Docker Engine + the compose plugin (if missing), plus `rsync`.
2. Copies the whole tree to `/opt/ascendplatform`, and creates `artifacts/`, `sites/`
   and `sites-conf/` owned by uid `10001` — the user the container runs as, so the
   app can actually write the images and Caddy vhosts it generates.
3. Writes `/opt/ascendplatform/.env` (domain, Postgres credentials, API keys, admin login).
4. `docker compose up -d --build` → starts Caddy, the app, PostgreSQL and Redis.

At startup the app container runs `python -m alembic upgrade head` before uvicorn,
so the database schema is created/migrated automatically.

### Verify

```bash
curl https://ai.example.com/api/health
# {"status":"ok","brand":"AscendPlatform","bots":1,"users":1,...}
```

Then open `https://ai.example.com/admin/` and log in. Before taking a paying client,
follow [`docs/operations.md`](docs/operations.md): encrypted off-site backups, a
restore drill, monitoring and a support path.

> **Local development** keeps using SQLite (`DATABASE_URL=sqlite:///data/app.db`),
> so none of the above is needed to run the app on your own machine.

---

## Using the admin panel

The dashboard is organised as left-sidebar tabs:

1. **Log in** with your admin email/password.
2. **Business details** → describe the business. This becomes the AI's knowledge
   base and the widget's branding; use *Show exact text* to inspect what the model
   reads. Rules can be edited one per row; AI-proposed facts wait for approval.
3. **Chatbots** → **+ New Chatbot** → name, assistant name, colour, welcome
   message, suggested questions, knowledge base, personality. Click **Embed** for
   the `<script>` tag.
4. **Agents** → designate who does what (see below).
5. **Media** → upload images, video and PDFs for emails and pages. **Alt text and a
   description are required before the upload can be saved.**
6. **Jobs** → run an `agent_team` or `echo` task and watch the transcript stream live.
7. **Conversations** → visitor chats and captured leads. **Stats** → message
   counts, activity and most-asked questions.
8. **Users** (admin) → add/remove team members.

### Agents

**Agents** is the roster: one row per AutoGen `AssistantAgent`, seeded with
Planner, Researcher, Copywriter, Coder, Image Artist, Reviewer and Operator —
each bound to the provider best suited to that role (Claude, Gemini or DeepSeek).

Editing a row changes the team immediately — provider, model, system prompt, turn
order and which tools the agent may call. No code changes, no redeploy.

- A run stops when an agent says `APPROVE`, or after `AGENT_MAX_MESSAGES`.
- **selector** mode uses a router model to pick the next speaker; **round_robin**
  uses a fixed order.
- Agents can only call the tools you grant them; unknown names are reported in the
  job transcript.

### Jobs

**Jobs** creates a run and streams its transcript live over Server-Sent Events
(with an automatic polling fallback). Runs execute in-process by default; set
`JOB_BACKEND=redis` and run the `worker` service for durable runs that survive
app restarts and scale separately from the web process. Write/exec actions
awaiting your approval appear on the job page.

### Sites

**Sites** is where a domain becomes a live HTTPS website, with no control panel.

1. **+ New site** and type the domain. Choose a target: *this server* or one of
   your servers from the Servers tab.
2. Click **DNS setup** to see the exact records to create at your DNS provider.
3. **Check DNS** until it resolves here, then **Provision**.

Provisioning writes the content, installs a per-site Caddy config and reloads
Caddy, which then issues the TLS certificate automatically. It runs as a job, so
you watch the transcript (and any approval requests) live. Leave the content blank
to generate a landing page from your Business Profile.

### Servers

**Servers** are remote hosts the Operator agent may deploy to over SSH. The
private key stays on this machine — only its path is stored — and host keys are
pinned, so the app can never be silently redirected to another machine.

### Safety

Nothing with a side effect happens by accident:

- **Dry-run is the default.** Unless you set `ALLOW_LIVE_ACTIONS=1` — in `.env` or
  from the Settings tab — every write and command is described in the transcript but
  never executed, which is why the whole flow is safe to try before you trust it.
- In live mode, each write/exec becomes an **approval request** that appears on
  the job page; it waits for you to approve or deny it.
- Every command must pass a **deny-by-default policy** first: known read-only or
  known mutating only, each chained segment checked, command substitution
  refused, and file writes confined to the site root.

### Media

**Media** is the client's own library of images, video and PDFs — the files that go
into emails, pages and posts. A platform owner or a client admin uploads; every
workspace member can preview, copy the URL and copy the ready-to-use HTML.

- **Alt text and a description are mandatory.** For an image the alt text becomes the
  `<img alt="...">`; for video it is the accessible label (and the email link text);
  for a PDF it is the link text. The upload button stays disabled until both are
  filled, and the server rejects a request without them, so the rule cannot be
  bypassed with curl.
- One file at a time: PNG, JPEG, WebP, GIF, MP4, WebM or PDF, up to
  `MEDIA_MAX_BYTES` (default 50 MB). Images get a thumbnail; PDFs show a page count
  and download rather than render inline; video gets a page snippet and a separate
  email link.
- **Copy URL** or **Copy HTML** puts it into a draft. The **Copywriter**, **Operator**
  and **Assistant** also have a `list_media` tool that returns the URL and snippet,
  including the alt text, so generated emails and pages use the client's own files.
- Stock photography goes through `search_photos` -> `import_photo_to_media`: the chosen
  Pexels photo is downloaded into the same Media library (with its alt text and credit
  in the description) and the local URL/HTML is what drafts use. This needs no approval;
  an empty Media tab is no longer a reason to skip images.
- Files live under `artifacts/media/<client>/`; `MEDIA_DIR` moves the root and
  `MEDIA_MAX_BYTES` changes the limit in **Settings → App & branding**.
- The public URL is `/media/<id>/<uuid>.<ext>`: no login is needed because an email
  or published page loads it directly, and the UUID filename is the unguessable part.
- Video captions/subtitles are not part of this pass; alt text is the accessible
  label, not a caption track. Importing generated job artifacts into Media is also a
  later addition.

### Assistant chats

The **Assistant** page keeps a list of chats instead of one browser thread.

- **+ New** starts a fresh conversation; **Chats** on the left lists earlier ones by
  title and time. Clicking one loads its messages; your next message appends to that
  same conversation.
- Sessions are stored on the server, scoped to your user and the client you are
  acting as. Switching clients in the workspace switcher shows that client's chats;
  another team member cannot see yours. They survive a reload and a different device.
- The server builds the Manager prompt from the stored session (the last ten turns),
  not from whatever the browser sends, so “continue” means the same thing everywhere.
- **Voice** turns are saved to the active session too, so a spoken conversation is in
  the same history as a typed one.
- If you had the old single browser thread, the rail offers a one-time **Import**.
  It is not imported silently because the old thread had no client context.
- The floating **Ask** sheet remains a quick local thread; the Assistant page is the
  one with history. There is no rename/delete in this pass.
- The Manager's final answer is now emitted as a `Manager` message and stored in the
  session; the job result is used as a fallback, so a finished turn no longer shows
  “No answer”.

### Voice

**Talk** in the Assistant sheet (or on the Assistant page) opens voice mode: a full-screen
orb, your words and the assistant's words appearing as you speak. The sheet stays for
typing, and its **Talk** button is only shown when the server says a session can start.

- The orb **follows your voice** — it is scaled from the microphone's RMS, not a timer.
  It is indigo while listening, amber while the team is thinking, and teal while the
  assistant speaks.
- **Stop** ends the session; **Esc** does the same. **Minimise** keeps it running in a
  small pill while you work. If a session is refused, the overlay shows the server's own
  reason, the model it tried, and a **Try again** button; **Copy diagnostics** is there
  for support.
- The voice is the team's **spokesperson**: when the Manager needs a detail it asks the
  user aloud, and the spoken answer is carried back into the same request. The voice also
  announces when the job finishes, follows background jobs started by the Manager, and
  tells the user when a job is waiting in **Approvals** (it never approves anything itself).
- The microphone is asked for **before** a token is minted, so a slow permission prompt
  does not spend the session.
- Configuration lives in **Settings → Voice**. **Test** lists the v1beta Live models,
  opens the real constrained socket server-side with a model-constrained token, and
  reports `setupComplete` or Google's exact setup error. The session refuses before
  opening a socket when the model is not available.
- The voice model is `GEMINI_LIVE_MODEL` (**Settings → Voice**), not the Gemini text
  model in **AI providers**. Ephemeral Live tokens are v1beta-only; if Test returns no
  Live models, the key/project lacks Live access.

### Settings

The **Settings** tab is where configuration actually lives once the app is running —
editing `.env` needs a restart, and inside a container it is read only at start-up.

- Set API keys, the safety switch, SMTP, SSH limits and platform options. Save, and
  it applies to the next request: **no restart**.
- Values saved here **override** `.env`. The badge beside each field shows where the
  value came from (`panel` / `environment` / `unset`); **Clear** falls back to `.env`.
- Secrets are **write-only** — once saved they are never sent back to the browser
  (only a masked tail). Leaving a key box blank on save keeps the stored value.
- **Test** runs a real check for a group: a live Pexels search, which AI providers
  are configured, an SMTP connect, or a constrained Live setup probe for Gemini voice.
- Admin-only, and every change is listed at the bottom of the page (key, action, who
  and when — never a secret value).
- Values that choose the topology (database, Redis, job backend, bind host/port) are
  shown read-only; change those in `.env` and restart.

### Embed a bot on any site

```html
<script src="https://ai.ascendplatform.site/widget.js" data-bot="BOT_ID"></script>
```

The widget auto-loads that bot's name/color/welcome/questions.

---

## Connect to Meta (Messenger / Instagram / WhatsApp)

Each bot can answer DMs on a **Facebook Page (Messenger)**, an **Instagram
professional account**, and/or a **WhatsApp Business number**. All three are
delivered to one endpoint, `POST /webhooks/meta`, which routes each message to
the linked bot, runs it through the same DeepSeek + knowledge-base pipeline as
the web widget, and sends the reply back via the Graph API.

> Note: Meta Business Suite is only the management UI. To drive it with this
> chatbot you connect a **Meta app** to the Graph API using webhooks — there is
> no "connect to Business Suite" button.

### 1. Expose the webhook

The callback URL must be public HTTPS: `https://<your-domain>/webhooks/meta`.
Caddy proxies `/webhooks/` to the app; no nginx snippet or control panel is involved.

### 2. Create the Meta app

1. Go to <https://developers.facebook.com> → **Create App** (type *Business*).
2. Add the **Messenger**, **Instagram**, and/or **WhatsApp** product.
3. Copy the **App secret** (Settings → Basic).
4. In the product's **Webhooks** settings, set:
   - **Callback URL**: `https://<your-domain>/webhooks/meta`
   - **Verify Token**: the token shown in the bot's channel form (or any value
     you set there)
   - Subscribe to the **`messages`** field.
5. Generate a **long-lived Page / System-User access token** (Messenger/Instagram)
   or the WhatsApp **permanent token**.

### 3. Link the channel to a bot

In the admin panel open a bot → **Meta channels** → **Add channel**:

| Platform | Page / Instagram / Phone ID | Access token |
|----------|-----------------------------|--------------|
| Messenger | Facebook **Page ID** | Long-lived **Page** token |
| Instagram | Instagram **account ID** (linked to the Page) | Page token |
| WhatsApp | WhatsApp **phone_number_id** | WhatsApp **permanent** token |

Paste the **App secret** too — it is required to verify the
`X-Hub-Signature-256` header on every incoming webhook. Save the channel: the
callback URL and verify token are shown in the panel to copy into Meta.

Use the **Enable/Disable** toggle to pause a channel without deleting it.

### 4. Try it

Send a DM to the Page / Instagram account / WhatsApp number. The bot answers
within the platform's messaging rules. Health check shows the channel count:

```bash
curl https://<your-domain>/api/health
# {"status":"ok",...,"channels":1}
```

### How replies are handled

- Inbound messages are acknowledged **immediately** (Meta requires a fast `200`).
- The reply is generated in a **background thread** and sent via the Send API, so
  a slow model call never causes a Meta retry.
- Every event is de-duplicated by message id (`meta_events`), so Meta's
  at-least-once redelivery cannot produce double replies.
- Conversation history is rebuilt from stored messages (`meta:<platform>:<id>:<sender>`),
  so the bot keeps context per sender.

### Things to know

- **24-hour window** — Messenger/Instagram may only reply within 24h of the
  user's last message; WhatsApp needs pre-approved **message templates** outside
  24h.
- **App review** — for production, Meta must review `pages_messaging` /
  `instagram_manage_messages`. In dev mode only app admins/test users get replies.
- **Instagram** requires an Instagram *professional* account linked to the Page;
  **WhatsApp** requires a WhatsApp Business Account (WABA) and business
  verification.
- **Signatures** — the webhook rejects requests whose signature does not match
  the configured app secret (`META_APP_SECRET` or the channel's own secret).
- **Secrets at rest** — channel tokens are stored in the database. Treat database dumps
  as secrets; the backup script encrypts them. Encrypting individual token columns at
  rest is still on the launch-hardening list.

---

## Updating the app

```bash
cd /opt/ascendplatform
git status -sb          # must be "## main..." before pulling
git pull --ff-only
./deploy.sh             # build, recreate, verify through Caddy, roll back on failure
```

The deploy script stamps `GIT_COMMIT`, `BUILD_TIME` and `APP_VERSION`, waits for the
public health URL, and reverts the commit if the site never answers. Database data lives
in the Postgres volume; media and site files live in the bind-mounted directories.

---

## Operations

```bash
cd /opt/ascendplatform
docker compose ps
docker compose logs -f app worker
curl -fsS https://<ADMIN_DOMAIN>/api/health
```

For backups, restore drills, suspension and notification setup, use the
[operations runbook](docs/operations.md). The worker does not serve HTTP; a worker
healthcheck should use its process/queue status, not port 8000.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `502 Bad Gateway` | `docker compose ps` / `docker compose logs --tail=100 app caddy` |
| Admin shows an old build | Check `data-build` on `/admin/` and `/api/version`; rerun `./deploy.sh` |
| Can't log in | The seeded admin exists only on a fresh database; use an operator password reset, do not delete the database |
| Widget says "Bot not found" | Wrong `data-bot` id — copy it from the Embed modal |
| Domain certificate pending | Check DNS, ports 80/443, then use Website → Domains → Check |

---

## Security notes

- Provider keys, admin credentials and `.env` stay on the server only; `.env` is mode
  600 and git-ignored. The backup archive contains it, so keep the backup encrypted.
- The app is only reachable through Caddy; do not expose port 8000 directly.
- Passwords are stored hashed (Werkzeug PBKDF2). Production data is PostgreSQL; local
  development can use SQLite.
- Suspension is enforced on login and on authenticated requests; the public chatbot and
  hosted-form endpoints also refuse a paused client. Published websites remain available
  until you explicitly disconnect or remove them for abuse/termination.
- Session cookies are HttpOnly/SameSite and Secure in the Compose stack. There is no
  public forgot-password flow yet; an operator resets a client's password from Team.

## License

MIT
