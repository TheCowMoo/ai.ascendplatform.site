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

The platform owner is never blocked, so support can still inspect the account. If a
client loses their password, an operator resets it from the client's Team page; there is
no public forgot-password flow yet. Keep that support path in the onboarding email.

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

## Analytics sources

Analytics is workspace-scoped. A client admin can connect a Google Analytics 4
property from Workspace → Analytics → Traffic sources; a member sees the numbers and
source labels read-only. The operator can do the same for any client from Platform →
Data sources, or while acting as that client.

Before clients connect GA4, set `GA4_SERVICE_ACCOUNT_KEY_PATH` and
`GA4_SERVICE_ACCOUNT_EMAIL` under Platform → Analytics. The client shares their GA4
property with that service account's email (Viewer is enough) and enters only the
numeric property ID.
The daily scheduler refreshes GA4 sources; the per-source **Refresh** action is for a
client admin or the operator. Site logs are platform infrastructure and are refreshed
by the operator's global sync, not by a client.

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
