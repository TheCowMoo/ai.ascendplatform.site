"""Domain validation belongs in the service, not only in the form.

Run: .\\venv\\Scripts\\python.exe tests\\test_site_validation.py

The UI and the Manager tool share ``sites.create_site``. Putting the rule there
means an agent cannot store a string the rest of the hosting code cannot use.
"""

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="site-validate-"), "test.db").replace("\\", "/")
os.environ["SCHEDULER_ENABLED"] = "0"

import db  # noqa: E402
import sites  # noqa: E402
from domain_rules import valid_domain  # noqa: E402

db.init_db()

FAILED = []


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


def refused(domain, aliases=None):
    try:
        sites.create_site({"domain": domain, "aliases": aliases or []})
        return False
    except sites.SiteError:
        return True


def test_bad_domains_are_refused():
    check("spaces are refused", refused("not a domain"), True)
    check("a missing dot is refused", refused("localhost"), True)
    check("path traversal is refused", refused("../evil.test"), True)
    check("a leading hyphen is refused", refused("-bad.test"), True)
    check("a bad alias is refused",
          refused("good.test", aliases=["bad alias"]), True)
    check("the shared rule agrees", valid_domain("good.test"), True)


def test_a_valid_domain_is_normalized_and_saved():
    site = sites.create_site({"domain": "Example.COM",
                              "aliases": ["WWW.Example.com"]})
    check("the domain is lower-cased", site["domain"], "example.com")
    check("the alias is lower-cased", site["aliases"], ["www.example.com"])


def test_an_update_cannot_bypass_the_rule():
    site = sites.create_site({"domain": "update.test"})
    try:
        sites.update_site(site["id"], {"domain": "bad domain"})
        check("a bad update is refused", False, True)
    except sites.SiteError:
        check("a bad update is refused", True, True)
    check("the stored domain is unchanged", sites.get_site(site["id"])["domain"],
          "update.test")


if __name__ == "__main__":
    test_bad_domains_are_refused()
    test_a_valid_domain_is_normalized_and_saved()
    test_an_update_cannot_bypass_the_rule()
    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nSITE VALIDATION OK - one rule for the form, the service and the agent")
