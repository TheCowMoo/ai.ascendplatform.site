"""Restore one draft's content from the page it already published.

Run: .\\venv\\Scripts\\python.exe repair_deliverable.py --id 33 --from-live
     .\\venv\\Scripts\\python.exe repair_deliverable.py --id 33 --from-live --slug index --apply

The QA job in the incident saved its report over draft #33. The live page was already the
real page, so the live file is the last copy. This script reads that file, prints what it
found, and - only with --apply - puts it back into the draft. It does not publish: after
this, open the page and Publish, choosing the home page if that is what it is.

Without --apply it changes nothing. The current (contaminated) content is still kept in
deliverable_versions by the normal update path, so the repair is itself undoable.
"""

import argparse
import re

import deliverables
import provisioner
import sites as sites_service


def _site_for(item):
    if item.get("site_id"):
        return sites_service.get_site(item["site_id"])
    candidates = sites_service.list_sites(item.get("org_id"), limit=100)
    if len(candidates) == 1:
        return candidates[0]
    raise SystemExit(
        "This draft has no site_id and the client has %d sites. Pass a site_id in the "
        "draft first, or copy the content from the live URL by hand." % len(candidates))


def _looks(content):
    title = re.search(r"<title[^>]*>([^<]*)</title>", content or "", re.I)
    heading = re.search(r"<h1[^>]*>([\s\S]*?)</h1>", content or "", re.I)
    plain = re.sub(r"<[^>]+>", " ", heading.group(1)) if heading else ""
    return ((title.group(1).strip() if title else ""),
            " ".join(plain.split())[:120])


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Restore a draft from the file it last published.")
    parser.add_argument("--id", type=int, required=True, help="deliverable id")
    parser.add_argument("--from-live", action="store_true",
                        help="read the current published file (required)")
    parser.add_argument("--path", default="",
                        help="file to read, e.g. index.html (default: the draft's slug)")
    parser.add_argument("--slug", default="",
                        help="set the draft slug too, e.g. index for a home page")
    parser.add_argument("--apply", action="store_true",
                        help="write the content back; without it this is a dry run")
    args = parser.parse_args(argv)
    if not args.from_live:
        raise SystemExit("Pass --from-live; this tool only copies the published page.")

    item = deliverables.get_deliverable(args.id)
    if not item:
        raise SystemExit("No deliverable #%d." % args.id)
    site = _site_for(item)
    if not site:
        raise SystemExit("That draft's site no longer exists.")
    target, docroot = provisioner.target_docroot(site)
    relative = args.path or deliverables.page_path(item.get("slug"))
    path = provisioner.join_path(docroot, relative)
    content = target.read_file(path)
    if not content.strip():
        raise SystemExit("The live file %s is empty or unreadable - nothing copied." % path)

    title, heading = _looks(content)
    print("deliverable #%d  %r" % (item["id"], item.get("title") or ""))
    print("  status: %s   slug: %s" % (item.get("status"), item.get("slug")))
    print("  live URL: %s" % (item.get("url") or "(not recorded)"))
    print("  file: %s" % path)
    print("  live content: %d characters" % len(content))
    print("  live title: %s" % (title or "(none)"))
    print("  live h1:    %s" % (heading or "(none)"))
    if not args.apply:
        print("\nDry run - nothing changed. Re-run with --apply to put this in the draft.")
        return

    new_slug = args.slug or item.get("slug")
    updated = deliverables.update_deliverable(item["id"], {
        "content": content,
        "slug": new_slug,
        "status": "draft",
    })
    print("\nRestored #%d (%d characters), slug %r, status %r."
          % (updated["id"], len(updated.get("content") or ""), updated.get("slug"),
             updated.get("status")))
    print("Open Pages, preview it, then Publish and choose Home page or Subpage.")


if __name__ == "__main__":
    main()
