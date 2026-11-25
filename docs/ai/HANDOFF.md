# AI Handoff Prompt

Copy this into a fresh agent session before describing the task:

```text
You are working on AscendPlatform. Read AGENTS.md first, then docs/ai/REFERENCE.md.
For the task, read only the matching recipe in docs/ai/RECIPES.md. Run
`python tools/ai_reference.py --check` and check `git status` before editing.
Search before reading large files; never read admin/admin.js, app.py, CONTEXT.md or
SYSTEM_PLAN.md whole. Preserve tenant isolation, role gates, settings scopes and the
approval gate. Make the smallest correct change, run the focused test and full preflight,
and report anything that could not be verified.
```

## First ten minutes

1. Read `AGENTS.md`.
2. Read `docs/ai/REFERENCE.md`.
3. Run `python tools/ai_reference.py --check`.
4. Check `git status` and do not overwrite unrelated work.
5. Search `docs/ai/INDEX.md` for the relevant route, setting, table or test.
6. Read only the matching source range and one nearby test.
7. State the files you expect to change and the tests you will run.
8. Make the change.
9. Refresh `docs/ai/INDEX.md` if routes/settings/tables/tests changed.
10. Run focused tests and `tests/preflight.py`.

## What the agent must report back

- Files changed
- Behavior changed
- Tests run and result
- External services mocked or not verified
- Any migration or deployment step
- Any remaining risk or follow-up

## Good first questions for the agent

- Which surface does this affect: Client Workspace, Platform Console, or both?
- Is the data scoped to one client?
- Does this need a new route, service, table, setting, UI page, or job?
- Which existing test suite is closest?
- Is this a code change, a data migration, or only a label/copy change?

## Windows commands

```powershell
.\venv\Scripts\python.exe tools\ai_reference.py --check
.\venv\Scripts\python.exe tests\preflight.py
```

On Linux/macOS use `.venv/bin/python` instead.
