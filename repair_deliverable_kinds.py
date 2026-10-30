"""Move drafts saved under the wrong tab into the tab that asked for them.

Run: .\\venv\\Scripts\\python.exe repair_deliverable_kinds.py
     .\\venv\\Scripts\\python.exe repair_deliverable_kinds.py --apply

A tab composer prepends a brief such as ``save_deliverable using kind "funnel"``, but a
model can still pass ``kind="website"``. New saves are enforced by
``tools/deliverable.py``; this script fixes rows written before that rule existed. It
also canonicalises old aliases (``landing_page``, ``blog_post``) so old rows reach the
canonical tab.

Without ``--apply`` it only prints what would change.
"""

import argparse
import sys

import deliverables
from db import get_connection, now_str


def candidates():
    """Rows whose kind differs from the job brief or from the canonical spelling."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT d.id, d.kind, d.title, d.org_id, j.prompt "
            "FROM deliverables d LEFT JOIN jobs j ON j.id = d.job_id "
            "ORDER BY d.id"
        ).fetchall()
    changes = []
    for row in rows:
        current = row["kind"] or ""
        wanted = deliverables.requested_kind(row["prompt"] or "")
        canonical = deliverables.canonical_kind(current)
        target = wanted or canonical
        if target != current:
            changes.append({
                "id": row["id"], "kind": current, "target": target,
                "title": row["title"] or "", "org_id": row["org_id"],
                "reason": "tab brief" if wanted else "alias",
            })
    return changes


def apply_changes(changes):
    if not changes:
        return 0
    with get_connection() as conn:
        for change in changes:
            conn.execute(
                "UPDATE deliverables SET kind = ?, updated_at = ? WHERE id = ?",
                (change["target"], now_str(), change["id"]),
            )
    return len(changes)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Move drafts into the tab their job asked for.")
    parser.add_argument("--apply", action="store_true",
                        help="write the changes (default: print only)")
    args = parser.parse_args(argv)

    changes = candidates()
    if not changes:
        print("Every draft is already in the right tab. Nothing to repair.")
        return 0

    print("%d draft(s) can be moved:" % len(changes))
    for change in changes:
        print("  #%-5d %-8s -> %-8s  %-42s  (%s)" % (
            change["id"], change["kind"], change["target"],
            change["title"][:42], change["reason"],
        ))
    if not args.apply:
        print("\nDry run. Pass --apply to move them.")
        return 0
    print("updated %d row(s)" % apply_changes(changes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
