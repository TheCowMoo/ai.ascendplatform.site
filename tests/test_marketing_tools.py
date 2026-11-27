"""The Manager's marketing tools: forms, contacts, templates and capabilities.

Run: .\\venv\\Scripts\\python.exe tests\\test_marketing_tools.py

The missing pieces were not only names in the grant list: a form could be created
but never read or edited, contacts could be created but not listed, and a template
could be written but not revised. These tests use the real job rows and service
layer so tenancy and the client-admin boundary are exercised, not just the happy
path.
"""

import asyncio
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="marketing-tools-"), "test.db").replace("\\", "/")
os.environ["SCHEDULER_ENABLED"] = "0"
os.environ["PLATFORM_URL"] = "https://platform.test"
os.environ.pop("ALLOW_LIVE_ACTIONS", None)
os.environ.pop("JOB_BACKEND", None)
os.environ.pop("REDIS_URL", None)

import app as appmod  # noqa: E402,F401
import contacts as contacts_service  # noqa: E402
import db  # noqa: E402
import forms as forms_service  # noqa: E402
import jobs  # noqa: E402
import manager as manager_mod  # noqa: E402
import orgs as orgs_service  # noqa: E402
import templates_service  # noqa: E402
import tools as registry  # noqa: E402
import tools.forms as form_tools  # noqa: E402
import tools.manager as manager_tools  # noqa: E402
import user_service  # noqa: E402

db.init_db()
FAILED = []


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name
          + ("" if ok else " - got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


def admin_id():
    with db.get_connection() as conn:
        row = conn.execute("SELECT id FROM users WHERE email = ?",
                           ("admin@example.com",)).fetchone()
    return row["id"] if row else None


def new_org(name):
    return orgs_service.create_org({"name": name})["id"]


def new_job(org_id, requester=None, read_only=False):
    return jobs.create_job("Marketing test", "test", requester or admin_id(),
                           "selector", "chat", org_id=org_id, read_only=read_only)


def run(builder, job_id, **kwargs):
    return asyncio.run(builder(None, job_id=job_id)(**kwargs))


def make_form(org_id, name="Enquiry"):
    return forms_service.create_form({
        "name": name,
        "kind": "form",
        "questions": [
            {"id": "name", "label": "Your name", "type": "text", "page": 0,
             "required": True},
            {"id": "email", "label": "Your email", "type": "email", "page": 0,
             "required": True},
        ],
        "settings": {"description": "Tell us about your trip", "brand": "#123456"},
    }, org_id)


def test_forms_can_be_read_updated_and_read_back():
    org_id = new_org("Forms Client")
    form = make_form(org_id)
    form = forms_service.update_form(form["id"], org_id, {"status": "published"})
    job = new_job(org_id)
    payload = json.loads(run(form_tools._build_read_form, job, form_id=form["id"]))
    check("read_form returns the real questions",
          [q["id"] for q in payload["questions"]], ["name", "email"])
    check("read_form includes settings", payload["settings"].get("brand"), "#123456")
    check("read_form gives the public link", "/f/" in payload["public_url"], True)

    forms_service.submit(form, {"name": "Ada", "email": "ada@example.test"})
    revised = json.loads(run(
        form_tools._build_update_form, job, form_id=form["id"],
        questions=json.dumps([
            {"id": "name", "label": "What should we call you?", "type": "text",
             "page": 0, "required": True},
            {"id": "email", "label": "Where can we reply?", "type": "email",
             "page": 0, "required": True},
        ]),
        settings=json.dumps({"brand": "#abcdef", "submit_label": "Send enquiry"})))
    check("update_form saves a label rewrite", revised.get("ok"), True)
    saved = forms_service.get_form(form["id"], org_id)
    check("the new label is stored", saved["questions"][0]["label"],
          "What should we call you?")
    check("the accent is stored", saved["settings"].get("brand"), "#abcdef")
    check("the button copy is stored", saved["settings"].get("submit_label"),
          "Send enquiry")
    check("old answers remain readable",
          len(forms_service.list_responses(form["id"], org_id)), 1)

    refused = run(form_tools._build_update_form, job, form_id=form["id"],
                  questions=json.dumps([
                      {"id": "renamed", "label": "New name", "type": "text"},
                      {"id": "email", "label": "Email", "type": "email"},
                  ]))
    check("a form with answers keeps its ids", "keep these question ids" in refused, True)

    responses = json.loads(run(form_tools._build_list_form_responses, job,
                               form_id=form["id"]))
    check("responses include the answer", responses["responses"][0]["answers"]["email"],
          "ada@example.test")
    check("responses do not expose the ip hash",
          "ip_hash" in json.dumps(responses), False)
    check("a second client cannot read the form",
          "no form" in run(form_tools._build_read_form,
                           new_job(new_org("Other Forms")), form_id=form["id"]), True)


def test_contacts_and_templates_can_be_worked_with():
    org_id = new_org("CRM Client")
    contacts_service.create_contact({"name": "Ada Lovelace", "email": "ada@example.test",
                                     "tags": ["enquiry", "tour"]}, org_id, source="manual")
    contacts_service.create_contact({"name": "Grace Hopper", "email": "grace@example.test",
                                     "tags": ["newsletter"]}, org_id, source="manual")
    job = new_job(org_id)
    listing = json.loads(run(manager_tools._build_list_contacts, job, tag="enquiry"))
    check("list_contacts filters by tag", [row["name"] for row in listing],
          ["Ada Lovelace"])
    check("list_contacts carries the tags", listing[0]["tags"], ["enquiry", "tour"])
    check("a second client cannot list these contacts",
          "No contacts" in run(manager_tools._build_list_contacts,
                               new_job(new_org("Other CRM"))), True)

    template = templates_service.save_template(
        {"name": "Follow-up", "subject": "Thanks {{contact.name}}",
         "body": "Hello {{contact.name}}"}, org_id)
    read = json.loads(run(manager_tools._build_read_template, job,
                          template_id=template["id"]))
    check("read_template returns the body", read["body"], "Hello {{contact.name}}")
    changed = run(manager_tools._build_update_template, job,
                  template_id=template["id"], subject="A warmer thanks")
    check("update_template saves a subject", "Updated template" in changed, True)
    saved = templates_service.get_template(template["id"], org_id)
    check("the old body is preserved", saved["body"], "Hello {{contact.name}}")
    check("the new subject is stored", saved["subject"], "A warmer thanks")


def test_member_cannot_use_client_admin_writes():
    org_id = new_org("Member Client")
    member = user_service.create_user({
        "email": "member-marketing@example.test", "name": "Member",
        "password": "member-pass-123", "role": "member", "org_id": org_id,
    }, admin_id(), True, None)
    job = new_job(org_id, requester=member["id"])
    check("a member cannot create a form",
          "Only a client admin" in run(manager_tools._build_create_form, job,
                                       name="Blocked", questions=[]), True)
    check("a member cannot read form answers",
          "Only a client admin" in run(form_tools._build_list_form_responses, job,
                                       form_id=1), True)
    check("a member can read a form definition",
          "no form" in run(form_tools._build_read_form, job, form_id=1), True)


def test_the_capability_list_names_the_new_tools():
    org_id = new_org("Capabilities Client")
    job = new_job(org_id)
    data = json.loads(run(manager_tools._build_describe_capabilities, job))
    check("forms are in the capability list", "forms" in data, True)
    check("chat assistants are in the capability list", "chat_assistants" in data, True)
    check("contacts are in the capability list", "contacts" in data, True)
    check("the chat readiness flag is present", "ready" in data["chat_assistants"], True)
    check("the Manager can read a form", "read_form" in manager_mod.GRANTED, True)
    check("the Manager can update a form", "update_form" in manager_mod.GRANTED, True)
    check("the Manager can list contacts", "list_contacts" in manager_mod.GRANTED, True)
    check("the Manager can tag contacts", "tag_contact" in manager_mod.GRANTED, True)
    check("the Manager can attach a chat assistant",
          "attach_chat_assistant" in manager_mod.GRANTED, True)
    check("the Manager can read a template", "read_template" in manager_mod.GRANTED, True)
    check("read_form is read-only", "read_form" in registry.READ_ONLY_TOOLS, True)
    check("update_form is not read-only", "update_form" in registry.READ_ONLY_TOOLS, False)
    check("attach_chat_assistant is not read-only",
          "attach_chat_assistant" in registry.READ_ONLY_TOOLS, False)


if __name__ == "__main__":
    test_forms_can_be_read_updated_and_read_back()
    test_contacts_and_templates_can_be_worked_with()
    test_member_cannot_use_client_admin_writes()
    test_the_capability_list_names_the_new_tools()
    if FAILED:
        print("\n%d FAILED" % len(FAILED))
        raise SystemExit(1)
    print("\nMARKETING TOOLS OK - forms, contacts, templates and client-admin boundaries")
