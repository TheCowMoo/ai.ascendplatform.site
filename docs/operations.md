# Operations runbook — first paying clients

This is the operator's checklist for the agency-managed pilot. It assumes the Compose
stack in this repository and a Linux VPS with Docker.

## Daily backups

Set a long, random passphrase in a root-only file (for example
`/etc/ascendplatform-backup.pass`, mode 600), not in cron or the repository. The archive
contains the Postgres dump, `artifacts/` (media), `sites/`, `sites-conf/`, `data/` when
present, `Caddyfile`, `.env` and `.secret_key` when present. It is encrypted with AES-256
before being written.

```bash
cd /opt/ascendplatform
BACKUP_PASSPHRASE_FILE=/etc/ascendplatform-backup.pass \
  BACKUP_DIR=/var/backups/ascendplatform bash ./deploy/backup.sh
```

Suggested root cron entry:

```cron
15 2 * * * cd /opt/ascendplatform && BACKUP_PASSPHRASE_FILE=/etc/ascendplatform-backup.pass BACKUP_DIR=/var/backups/ascendplatform bash ./deploy/backup.sh >>/var/log/ascend-backup.log 2>&1
```

Then copy the newest archive and its `.sha256` to encrypted off-site storage. The
script keeps 30 days by default (`BACKUP_RETENTION_DAYS`). A backup that has never been
restored is only a hope; run the restore drill below before the first client and then
quarterly.

## Restore drill

Do this on a staging copy first. It stops the writers, restores Postgres and the client
files, then waits for the public health endpoint.

```bash
cd /opt/ascendplatform
RESTORE_CONFIRM=yes BACKUP_PASSPHRASE_FILE=/etc/ascendplatform-backup.pass \
  bash ./deploy/restore.sh /var/backups/ascendplatform/ascendplatform-YYYY...tar.gz.enc
```

If the script reports a failure, the stack is intentionally left stopped. Read
`docker compose logs --tail=100 app worker db caddy` and fix the cause before starting
writers again. Do not accept new work until a restored client can sign in and load one
of their pages.

## What to alert on

At minimum, monitor these from outside the VPS:

- `https://<ADMIN_DOMAIN>/api/health` answers 200.
- `/admin/` answers 200 and its `data-build` changes after a deploy.
- TLS certificate expiry is more than 14 days away.
- The `db` and `redis` containers are running; the worker is processing jobs.
- Disk usage stays below 80% (media and backups grow).
- A scheduled backup file exists for the current day and its checksum verifies.

The app's `/api/health` is intentionally public but only reports counts and the
configured backend. Keep provider keys, client data and queue details on the operator
side.

## Suspension and recovery

A client's `status` is now enforced at login and on every authenticated request. To
pause a client:

1. Platform → Clients → set status to `suspended`.
2. Existing browser sessions receive a 403 on their next request and are cleared.
3. The client's login is refused until status is set back to `active`; public chatbots,
   Meta replies and hosted forms also stop. Published websites remain live until removed.

The platform owner is never blocked, so support can still inspect the account. A client
can use **Forgot password?** on the sign-in page. The link is single-use, expires in 60
minutes and is sent through the platform's GoHighLevel sender. If platform email is not
configured, the request still receives the generic success message and support can reset
the password from the client's Team page. Keep that support path in the onboarding email.

## Working on one client

Open **Platform -> Clients** and choose a client. The page keeps the client in context
and is split into six tabs:

- **Overview** shows plan, status, the primary admin, notes, and the important counts:
  logins, websites, open support issues and available credits. The quick actions add a
  login, report an internal issue or add credits.
- **Team** lists the client's logins with role, status and last sign-in. Add a login or
  send an invitation; reset a password, enable/disable or remove a person. A person
  edited here cannot be moved to another client or made an agency owner.
- **Websites** shows that client's domains, DNS address, SSL state and live link.
  Destructive domain removal stays in **Platform -> Domains**.
- **Usage** shows the client's balance, allowance, month-to-date spend and recent
  activity. **Add credits** is the same operation as Platform -> Usage.
- **Support** is the internal bug list filtered to this client. Reporting here keeps the
  issue attached to the client; the client never sees it.
- **Settings** edits the client profile, manages the client's own email-sending details
  (SMTP), tests the connection and exposes the permanent-delete action. GoHighLevel
  remains a backend-only integration.

**Open workspace** switches the operator's acting client and opens that client's
dashboard. Use the workspace switcher under the logo to return to the all-client view.
The owner-only domain API accepts `?org_id=N`; leaving it off returns every client's
domains.

## People and Team

**Platform -> People** is the agency-wide directory. Add an agency owner (no client),
a client admin or a member; filter by client, role or status; edit, move, reset a
password, disable/enable or remove. **Workspace -> Account -> Team** is the same table
for a client admin, permanently scoped to their own workspace. A member cannot open it.

A new client gets its first `client_admin` when it is created under Platform -> Clients.
A client admin can add teammates or invite them from Team; invitations create only
client admins/members, never an agency owner. If platform email is not configured, the
invitation is still created and the one-time link is shown so it can be copied by hand.

Per-user `suspended` status is separate from the client's own status: suspending one
person clears their existing session on the next request and blocks their next login
without pausing the whole workspace. Re-enable them from the same row; they sign in
again. The last active owner and the last active client admin cannot be removed,
disabled or demoted, so a workspace is never left with nobody able to administer it.
Every change is visible in `user_events` for support and audit.

## Internal bug tracker

Platform -> **Bug tracker** is the agency's private issue list; client admins and members
cannot see it. Report a bug with a title, description, priority, optional affected client,
optional agency assignee and the page or screen where it happened. Open a row to change
its status/priority/assignee or add a comment. Every change and comment is kept in the
activity timeline; use **Closed** instead of deleting a bug so its history survives.
Bugs never create a client-facing notification.

## Publishing pages and subpages

A website can be one page or a multi-page site. The website build asks how many pages
are needed; choosing a few creates Home plus the selected About/Services/Contact/Blog
pages, and **Publish site** writes them together under one approval.

When adding a page later, create a new page with a distinct URL name (for example
`about`) and choose **Subpage** in Publish. The target is independent of the draft slug,
so even a draft whose slug is `home` can be published to `/about.html` without replacing
the live front page. Use the home-page choice only when the page really is the new root;
the server still asks for confirmation before replacing a different live page.

## Analytics sources

Analytics is workspace-scoped. A client admin can connect a Google Analytics 4
property from Workspace → Analytics → Traffic sources; a member sees the numbers and
source labels read-only. The operator can do the same for any client from Platform →
Data sources, or while acting as that client.

The recommended path is Google OAuth. Create a **Web application** OAuth client in
Google Cloud, enable the Google Analytics Data API and the Tag Manager API, and register
this exact redirect URI under Platform → Analytics:

```text
https://<ADMIN_DOMAIN>/api/analytics/oauth/google/callback
```

Add all six Analytics and Tag Manager scopes to the OAuth consent screen:
`analytics.readonly`, `analytics.edit`, `tagmanager.readonly`,
`tagmanager.edit.containers`, `tagmanager.edit.containerversions` and
`tagmanager.publish`. Add `bigquery.readonly` too if you will use the BigQuery export.
The first connection asks for read access only. **Enable setup & automation** on the
Google connection card asks for the write scopes again; **Enable BigQuery** asks for
BigQuery read access. Google shows the new permissions, and the existing refresh token
is replaced with the broader grant.

Set `GA4_OAUTH_CLIENT_ID`, `GA4_OAUTH_CLIENT_SECRET` and, if needed,
`GA4_OAUTH_REDIRECT_URI`. Set a stable `OAUTH_TOKEN_KEY` (or `SECRET_KEY`) in the
production environment before connecting: tokens are Fernet-encrypted with it, and
changing it makes every stored grant unreadable. The client enters their numeric property
ID and clicks **Connect with Google**; the callback stores the refresh token for that
client only. Existing service-account sources remain supported through
`GA4_SERVICE_ACCOUNT_KEY_PATH`. The daily scheduler refreshes GA4 sources; the per-source
**Refresh** action is for a client admin or the operator. Site logs are platform
infrastructure and are refreshed by the operator's global sync, not by a client.

With write access, **Create GA4 property** creates a property and web stream, marks the
chosen key events, creates a GTM web container with a GA4 configuration tag and one event
tag per key event, publishes the container, and installs the GTM snippet on the site's
live pages and hosted forms. **Install GTM snippet** repeats the page installation for an
existing container. Every step is recorded in `analytics_actions` and in the Google
connection card's Recent Google setup actions. The site write still obeys
`ALLOW_LIVE_ACTIONS` and is a dry-run until live actions are enabled.

The AI can also run the setup through `setup_analytics`. When
`ANALYTICS_AUTONOMY` is off (the default), it drafts a pending approval that a person
approves in Approvals; approving executes the same setup. When the client turns
`ANALYTICS_AUTONOMY` on, the AI can execute directly, still behind
`ALLOW_LIVE_ACTIONS`, a three-per-hour-per-client setup limit, and the audit trail. Turn
the setting off to require a person for every Google write.

### BigQuery export

The BigQuery path uses the GA4 event export, not the Data API. In Google Cloud, enable
the BigQuery API, add `bigquery.readonly` to the OAuth consent screen, and give the
Google user **BigQuery Job User** plus read access to the export dataset. Then:

1. Click **Enable BigQuery** on the GA4 source in Analytics and approve the consent.
2. Click **Link BigQuery**, enter the Google Cloud project ID and dataset location, and
   choose daily (and optionally streaming) export. The dataset is created by GA4 as
   `analytics_<property_id>`; it is not user-named.
3. GA4 can take up to 24 hours to create the first table. Click **Sync BigQuery** after
   that; it reads the last 7 or 30 days into daily aggregate facts.

Only aggregates are stored (`bq_events`, `bq_pages`, `bq_acquisition`): event counts, page
paths, source/medium, sessions and distinct-user counts. Raw event rows and
`user_pseudo_id` are never written to the platform database. The page query strips the
scheme, host, query string and fragment from `page_location` before storing the path.

Each BigQuery query is capped at 2 GB billed bytes, 10,000 result rows and a 60-second
job timeout. BigQuery normally bills the Google Cloud project for bytes scanned; streaming
export has additional cost. A service-account GA4 source can query the export if its key
has BigQuery access; OAuth sources use the `bigquery.readonly` grant. Linking a project is
a live write and still requires `ALLOW_LIVE_ACTIONS`; syncing is read-only. A client can
link at most twice in ten minutes; a human GA4 setup is capped at three attempts per hour.

### Intelligence and alerts

Analytics shows **What changed**: pageviews, sessions and errors against the previous
window, plus the top page and the busiest GA4/BigQuery event. Every insight names the
metric and both windows; there is no opaque score.

The first evaluation creates three in-app defaults: pageviews falling 30%, sessions
falling 30% and errors spiking 100%. Rules are per client and can be added, deleted or
changed from the same panel. One alert is stored per rule per ISO week, so a daily run does
not repeat the same warning. **Run now** evaluates immediately; the scheduler also queues
one `analytics_alerts` job per client at most every 20 hours, on the hourly verifier tick.
If `SITE_VERIFY_MINUTES=0` and the sync loop is off, use **Run now** or re-enable a tick.

Email is off for the defaults. Turn on **Email client admins** for a rule only after client
mail and `NOTIFICATIONS_ENABLED` are configured; delivery goes through
`notifications.notify_org`. Email is skipped while `ALLOW_LIVE_ACTIONS` is off, but the
in-app alert is still stored. Alerts can be marked read or resolved; they never modify a
site or a Google account.

`ANALYTICS_ALERTS_ENABLED` in Settings (default on) is the client-level opt-out: turning it
off stops default/rule evaluation and scheduled runs without deleting the rules. A client
can have at most 50 rules, and one malformed rule cannot stop the others. The scheduler
prunes `analytics_actions` and handled `analytics_alerts` older than 180 days once a day;
open alerts are kept until handled.

All source reads and writes are scoped to the acting workspace. A source update with an
id from another workspace must return 404.

## Contact mirror

The Contacts page has **Sync with AscendPlatform** for a client admin or the operator.
It is a two-way contact mirror: shared fields merge against the last snapshot, and a
conflict where both sides changed the same field is resolved by the newer timestamp
(ties go to AscendPlatform) and listed in the result. Form answers travel outbound as
AscendPlatform activity. The upstream provider is never named in client-facing copy.

The run is a `contact_sync` job. If the workspace has no upstream credentials, the
button returns a plain-language setup message instead of starting a job. A remote
deletion is reported and retained locally; it is not a local delete.

## Workspace object mirror

The same **Sync with AscendPlatform** action also mirrors forms, form responses, content
drafts, sites and automations as records of a GHL custom object named **AscendPlatform
item**. The schema and its fields are created on the first run. `ghl_object_links`
remembers each local row's remote record, and an unchanged record is skipped. This is an
outbound mirror; editing one of those records in GHL does not change the source document.
If the client's token cannot create object schemas, contacts still sync and the result
shows a workspace warning. The native GHL forms/funnels/workflows APIs are read-only, so
the custom object is the supported visible mirror.

## Opportunities

When the client's GHL token can write opportunities, the mirror creates one opportunity
per contact that has a remote contact id. Configure `GHL_CLIENT_OPPORTUNITY_PIPELINE_ID`
and `GHL_CLIENT_OPPORTUNITY_STAGE_ID` in the hidden Client email settings group; when
blank, the first usable pipeline and stage are used. The `GHL_CLIENT_OPPORTUNITIES` switch
turns this off. Customer contacts map to won, archived to lost, and the rest to open. This
is still outbound from AscendPlatform; GHL opportunity edits are not written back to the
contact. Appointments have no Ascend-side source yet, so none are invented.

## Notifications

Form submissions now queue a “New lead” email to the client's `client_admin` users.
Automation `notify` steps send through the same channel. Configure the platform's
GoHighLevel credentials under Platform → Platform email, or the client's own credentials
under their Email settings. If mail is not configured, the lead is still saved and the
form submission still succeeds; the notification is best-effort by design. The
`NOTIFICATIONS_ENABLED` setting (default on) is the switch for a test or maintenance
window.

## Deploy guardrails

- Run `./venv/Scripts/python.exe tests/preflight.py` before pushing (Linux:
  `.venv/bin/python tests/preflight.py`). It runs the fast suites and JS parsers.
- `./deploy.sh` rebuilds, recreates, waits for the public health URL, and rolls back on
  failure. It does not replace a restore drill.
- Keep `ALLOW_LIVE_ACTIONS` unset until the approval flow has been exercised with a
  test site. In live mode every write still needs an approval.
- Keep `/api/health` behind the public domain only; do not expose port 8000 directly.
