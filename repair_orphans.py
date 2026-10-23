"""Find - and, when told exactly which, reassign - deliverables with no client.

Run: .\\venv\\Scripts\\python.exe repair_orphans.py
     .\\venv\\Scripts\\python.exe repair_orphans.py --assign 4 --ids 12,13

A draft with ``org_id IS NULL`` appears in no client's Emails or Funnels tab. That is how
the operator's missing email and funnel behaved: jobs launched from a session with no
tenant attached wrote unowned rows. New work can no longer do this - ``/api/jobs`` and
``save_deliverable`` both refuse - so this script exists only for the rows already there.

It never guesses. Without ``--assign`` it prints the candidates and exits; the ids it shows
are the ids you pass back. ``--dry-run`` shows what the update would touch and rolls back.
"""

import argparse
import sys

import db

COLS = ("id", "kind", "title", "job_id", "created_at")


def orphans(kind=None):
    sql = "SELECT id, org_id, kind, title, job_id, created_at FROM deliverables WHERE org_id IS NULL"
    args = ()
    if kind:
        sql += " AND kind = ?"
        args = (kind,)
    with db.get_connection() as conn:
        rows = conn.execute(sql + " ORDER BY id", args).fetchall()
    return [dict(row) for row in rows]


def jobs_without_an_org(limit=20):
    with db.get_connection() as conn:
        rows = conn.execute(
            "SELECT id, task, status, substr(title, 1, 40) AS title FROM jobs "
            "WHERE org_id IS NULL ORDER BY id DESC LIMIT %d" % max(1, int(limit))
        ).fetchall()
    return [dict(row) for row in rows]


def assign(ids, org_id, dry_run=False):
    if not ids:
        raise SystemExit("--ids is required with --assign, e.g. --ids 12,13")
    marks = ",".join("?" for _ in ids)
    with db.get_connection() as conn:
        org = conn.execute("SELECT id, name FROM orgs WHERE id = ?", (org_id,)).fetchone()
        if not org:
            raise SystemExit("no client with id %s" % org_id)
        found = conn.execute(
            "SELECT id, kind, title FROM deliverables WHERE id IN (%s) AND org_id IS NULL" % marks,
            tuple(ids),
        ).fetchall()
        if not found:
            raise SystemExit("none of those ids are unowned - nothing to do")
        print("reassigning to %s (org %s):" % (org["name"], org["id"]))
        for row in found:
            print("  #%d  %-8s %s" % (row["id"], row["kind"], (row["title"] or "")[:60]))
        conn.execute(
            "UPDATE deliverables SET org_id = ? WHERE id IN (%s) AND org_id IS NULL" % marks,
            tuple([org_id] + list(ids)),
        )
        if dry_run:
            conn.rollback()
            print("dry run - rolled back, nothing changed")
        else:
            conn.commit()
            print("updated %d row(s)" % len(found))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Repair deliverables that belong to no client.")
    parser.add_argument("--kind", help="only this kind (email, funnel, blog...)")
    parser.add_argument("--assign", type=int, metavar="ORG_ID", help="client to give them to")
    parser.add_argument("--ids", help="comma-separated deliverable ids, e.g. 12,13")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    rows = orphans(args.kind)
    if not rows:
        print("No unowned drafts. Nothing to repair.")
    else:
        print("%d unowned draft(s):" % len(rows))
        for row in rows:
            print("  #%-4d %-8s %-42s job %-5s %s" % (
                row["id"], row["kind"], (row["title"] or "")[:42],
                row["job_id"] or "-", row["created_at"],
            ))
        print("\nPass --assign ORG_ID --ids <the ids above> once you know whose they are.")

    stale = jobs_without_an_org()
    if stale:
        print("\nRecent jobs with no client (these produced the rows above):")
        for job in stale:
            print("  #%-5d %-10s %-9s %s" % (job["id"], job["task"], job["status"], job["title"]))

    if args.assign:
        ids = [int(part) for part in (args.ids or "").replace(" ", "").split(",") if part]
        assign(ids, args.assign, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
