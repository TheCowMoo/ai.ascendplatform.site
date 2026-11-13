"""A client's own domain serves its forms, and links say so.

Run: .\\venv\\Scripts\\python.exe tests\\test_form_hosting.py

The proxy is the part that was missing: a published site's vhost was static, so
``/f/<slug>`` 404'd on the client's domain and every link had to fall back to the
platform. These checks pin the vhost, the config-only refresh, and the link choice.
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="form-hosting-"), "test.db").replace("\\", "/")
os.environ.pop("ALLOW_LIVE_ACTIONS", None)
os.environ.pop("FORMS_UPSTREAM", None)
os.environ["SERVER_IP"] = "203.0.113.12"
os.environ["PLATFORM_URL"] = "https://platform.test"

import actions  # noqa: E402
import app as appmod  # noqa: E402
import db  # noqa: E402
import deliverables  # noqa: E402
import forms as forms_service  # noqa: E402
import jobs  # noqa: E402
import orgs as orgs_service  # noqa: E402
import provisioner  # noqa: E402
import sites as sites_service  # noqa: E402
import tools.manager as manager_tools  # noqa: E402

db.init_db()
FAILED = []


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


def user_id():
    with db.get_connection() as conn:
        row = conn.execute("SELECT id FROM users WHERE email = ?",
                           ("admin@example.com",)).fetchone()
    return row["id"]


def new_org(name):
    return orgs_service.create_org({"name": name})["id"]


def new_job(org):
    return jobs.create_job("Form hosting", "put forms on the client domain", user_id(),
                           "selector", "chat", org_id=org)


def run(builder, **kwargs):
    return asyncio.run(builder(None, job_id=JOB)(**kwargs))


def test_vhost():
    text = provisioner.render_vhost(
        "client.test", "/srv/sites/client.test/public_html", ["www.client.test"],
        forms_upstream="app:8000")
    check("the form page is proxied", "@forms path /f/* /api/public/forms/*" in text, True)
    check("to the app", "reverse_proxy app:8000" in text, True)
    check("with the visitor's address", "header_up X-Real-IP {remote_host}" in text, True)
    check("only the form routes", "handle @forms" in text, True)
    check("the rest stays static", "handle {" in text and "file_server" in text, True)
    check("both hosts are served", "client.test, www.client.test" in text, True)

    static = provisioner.render_vhost("client.test", "/srv/sites/client.test/public_html")
    check("no upstream leaves a static block", "reverse_proxy" in static, False)
    check("a local site points at the app", provisioner.forms_upstream_for(
        provisioner.LocalTarget()), "app:8000")

    class Remote:
        is_remote = True

    check("a remote site points at the platform",
          provisioner.forms_upstream_for(Remote()), "https://platform.test")
    os.environ["FORMS_UPSTREAM"] = "https://forms.internal:8443"
    try:
        check("an explicit upstream wins", provisioner.forms_upstream_for(Remote()),
              "https://forms.internal:8443")
    finally:
        os.environ.pop("FORMS_UPSTREAM", None)


def test_links():
    slug = FORM["slug"]
    check("a live client domain wins", forms_service.public_url(FORM, SITE),
          "https://client.test/f/" + slug)
    check("the org lookup finds it", forms_service.link_for_org(FORM, ORG),
          "https://client.test/f/" + slug)
    check("the platform is the fallback", forms_service.public_url(FORM),
          "https://platform.test/f/" + slug)

    reply = run(manager_tools._build_create_form, name="Callback",
                questions=[{"label": "Phone", "type": "phone"}])
    check("the Manager hands back the client link",
          "https://client.test/f/" in reply, True)

    page = deliverables.create_deliverable(
        {"kind": "website", "title": "Home", "slug": "home", "site_id": SITE["id"],
         "content": "<html><body><main><h1>Hi</h1></main></body></html>"}, org_id=ORG)
    reply = run(manager_tools._build_add_form_link, deliverable_id=page["id"],
                form_id=FORM["id"], label="Get a quote")
    saved = deliverables.get_deliverable(page["id"])["content"]
    check("the button points at the client domain",
          "https://client.test/f/" + slug in saved, True)
    check("and sits inside main", saved.index("/f/" + slug) < saved.index("</main>"), True)
    again = run(manager_tools._build_add_form_link, deliverable_id=page["id"],
                form_id=FORM["id"])
    check("the relative button is not duplicated", "already links" in again, True)

    client = appmod.app.test_client()
    check("the forms API needs a login", client.get("/api/forms").status_code, 401)
    client.post("/api/auth/login", json={"email": "admin@example.com",
                                         "password": "admin123"})
    client.post("/api/session/org", json={"org_id": ORG})
    rows = (client.get("/api/forms").get_json() or {}).get("forms") or []
    row = [item for item in rows if item["id"] == FORM["id"]][0]
    check("the Forms tab gets the client link", row["public_url"],
          "https://client.test/f/" + slug)
    one = client.get("/api/forms/%d" % FORM["id"]).get_json().get("form") or {}
    check("the builder gets it too", one.get("public_url"),
          "https://client.test/f/" + slug)
    made = client.post("/api/forms", json={
        "name": "Domain form", "kind": "form",
        "questions": [{"id": "q1", "label": "Email", "type": "email", "page": 0}],
    }).get_json().get("form") or {}
    check("a new form is born on the client domain",
          (made.get("public_url") or "").startswith("https://client.test/f/"), True)


def test_refresh_only_touches_the_vhost():
    root = tempfile.mkdtemp(prefix="form-vhost-")
    sites_root = os.path.join(root, "sites")
    caddy_dir = os.path.join(root, "caddy")
    docroot = os.path.join(sites_root, "client.test", "public_html")
    os.makedirs(docroot)
    os.makedirs(caddy_dir)
    index_path = os.path.join(docroot, "index.html")
    with open(index_path, "w", encoding="utf-8") as handle:
        handle.write("original page")
    vhost_path = provisioner.join_path(caddy_dir, "client.test.caddy")
    with open(vhost_path, "w", encoding="utf-8") as handle:
        handle.write("# old block\nclient.test, www.client.test {\n"
                     "\troot * %s\n\tfile_server\n}\n" % docroot)

    site = dict(SITE)
    site.update({"docroot": docroot, "aliases": [], "target": "local"})
    target = provisioner.LocalTarget(sites_root=sites_root, caddy_sites_dir=caddy_dir)
    original_target = provisioner.get_target
    original_reload = provisioner.reload_web_server
    original_dry = actions.is_dry_run
    reloads = []

    def fake_reload(target, job_id=None, emit=None, preapproved=False):
        reloads.append(preapproved)
        return {"ok": True, "output": "reloaded", "dry_run": False}

    provisioner.get_target = lambda value=None: target
    provisioner.reload_web_server = fake_reload
    actions.is_dry_run = lambda: False
    try:
        result = provisioner.refresh_vhost(site, actor="tester")
        with open(vhost_path, encoding="utf-8") as handle:
            written = handle.read()
        with open(index_path, encoding="utf-8") as handle:
            page = handle.read()
        check("the refresh succeeds", result["ok"], True)
        check("it proxies forms", "reverse_proxy app:8000" in written, True)
        check("it preserves www from the old block", "www.client.test" in written, True)
        check("it keeps the docroot", "root * " + docroot in written, True)
        check("the page is byte-for-byte untouched", page, "original page")
        check("the reload was covered by the click", reloads[-1], True)
        check("the forms URL is reported", result["forms_url"], "https://client.test/f/")
        check("only the two config steps ran",
              [step["step"] for step in result["steps"]], ["vhost", "reload"])

        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM action_approvals WHERE tool = 'file_write' "
                "ORDER BY id DESC LIMIT 1").fetchone()
        check("the click is recorded as approved", row["status"], "approved")
        check("with the actor", row["decided_by"], "tester")
        check("naming the vhost", json.loads(row["args_json"] or "{}").get("path"),
              vhost_path)
    finally:
        provisioner.get_target = original_target
        provisioner.reload_web_server = original_reload
        actions.is_dry_run = original_dry
        shutil.rmtree(root, ignore_errors=True)


def test_refresh_route():
    original = provisioner.refresh_vhost
    calls = []

    def fake_refresh(site, actor=""):
        calls.append((site["id"], actor))
        return {"ok": True, "dry_run": True, "steps": []}

    provisioner.refresh_vhost = fake_refresh
    try:
        client = appmod.app.test_client()
        check("the route needs a login",
              client.post("/api/sites/%d/refresh-config" % SITE["id"]).status_code, 401)
        client.post("/api/auth/login", json={"email": "admin@example.com",
                                             "password": "admin123"})
        client.post("/api/session/org", json={"org_id": ORG})
        check("a missing site is 404",
              client.post("/api/sites/999999/refresh-config").status_code, 404)
        response = client.post("/api/sites/%d/refresh-config" % SITE["id"])
        check("the owner can refresh their site", response.status_code, 200)
        check("it calls the config-only refresh", calls[0][0], SITE["id"])
        check("with the signed-in actor", calls[0][1], "admin@example.com")
    finally:
        provisioner.refresh_vhost = original


def test_the_console_and_server_agree():
    script = open(os.path.join(ROOT, "admin", "admin.js"), encoding="utf-8").read()
    forms_source = open(os.path.join(ROOT, "forms.py"), encoding="utf-8").read()
    vhost_source = open(os.path.join(ROOT, "provisioner.py"), encoding="utf-8").read()
    check("the Domains row has the action", "'Refresh web config'" in script, True)
    check("wired to the refresh route", "'/refresh-config'" in script, True)
    check("the Forms tab uses the API link", "item.public_url" in script, True)
    check("it no longer hardcodes the platform origin",
          "var link = location.origin + '/f/' + item.slug" in script, False)
    check("the server owns the link choice", "def public_url(form, site=None)" in forms_source, True)
    check("the generated vhost has the proxy", "@forms path /f/*" in vhost_source, True)


if __name__ == "__main__":
    ORG = new_org("Hosting Client")
    JOB = new_job(ORG)
    SITE = sites_service.create_site({"domain": "client.test"}, org_id=ORG)
    SITE = sites_service.update_site(SITE["id"], {"status": "live"})
    FORM = forms_service.create_form(
        {"name": "Contact us", "kind": "form",
         "questions": [{"id": "q1", "label": "Email", "type": "email", "page": 0,
                        "required": True}]}, ORG)
    FORM = forms_service.update_form(FORM["id"], ORG, {"status": "published"})

    test_vhost()
    test_links()
    test_refresh_only_touches_the_vhost()
    test_refresh_route()
    test_the_console_and_server_agree()
    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nFORM HOSTING OK - the client domain serves the form, and every link says so")
