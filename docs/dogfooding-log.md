# Dogfooding log

Per the project constitution: from week three the company runs on its own
app, and each week the CEO publishes a short note listing which changes were
caused by friction the team hit while using it. This file is the raw trail
that note is drawn from — entries are dated and written the day the friction
was hit, not backfilled.

Setup, once, for anyone continuing this: the company's real queue lives in a
persistent store at `CADENCE_HOME=/workspace/cadence_home` (this directory
persists with the project across runs, unlike `$HOME`), driven by the built
package installed into `/workspace/cadence_dogfood_venv`
(`pip install dist/cadence_todo-0.1.0-py3-none-any.whl`). Use
`CADENCE_HOME=/workspace/cadence_home /workspace/cadence_dogfood_venv/bin/cadence <cmd>`
from the CLI, or point an MCP stdio client at
`/workspace/cadence_dogfood_venv/bin/cadence mcp` with that same env var, so
every team is reading and writing the one store.

---

## Week 1 — 2026-08-28 (Rafael Okonkwo, Build)

Loaded the company's actual open plan items into Cadence itself instead of
tracking them anywhere else: R-04 (PyPI publish), R-05 (CI green including
e2e), R-06 (ten-step agent transcript), R-08 (chairman wow verdicts), the
docs/bakeoff.md stale-status cleanup Ines flagged, the pending PyPI-token
approval follow-up, and all five items from Red Team's first findings pass
(pass1, findings ranked CRITICAL/HIGH/MEDIUM/LOW/LOW) — twelve tasks total,
seeded via both surfaces: eleven through `cadence add`/`cadence schedule` on
the CLI, one (#12, the "land the Red Team fixes" item) through a real MCP
stdio client calling `add_task`, to exercise the agent surface on day one
rather than only the human one.

Friction hit, in the order it was hit:

1. **No edit or delete command on either surface.** Once a task is added
   there is no way to fix a typo'd title, correct a wrong priority, or
   remove a duplicate — you live with what you typed or start over with a
   fresh id. For a queue the whole company is about to depend on daily, this
   is the sharpest gap: it's also the root cause behind Red Team's #1
   CRITICAL finding (a bad `schedule_task` call has no undo). Filed as a
   real backlog item (task id 7 in the store itself, not a side note).

2. **`list_tasks`'s own docstring is wrong, and I hit the consequence
   immediately.** It promises "ordered high-priority first then by id" but
   the store just returns insertion order (confirmed directly: after seeding
   four `high`-priority R-0x items first, then five `low`/`med` Red Team
   nits, then one more `high`-priority item via MCP, `list_tasks` returned
   them in the exact order added — the new high-priority task landed dead
   last, not near the top). Matches Red Team finding #3 independently, from
   the "just try to use it" side rather than adversarial testing. This means
   the CLI's `cadence list` has the identical problem: as the real company
   queue grows past a screen's height, whatever was added first — not
   whatever matters most — is what stays visible without scrolling.

3. **No bulk/scripted seeding path.** Loading eleven real backlog items
   meant eleven separate `cadence add` invocations, one at a time, because
   there is no import-from-file or multi-line-input mode. Fine once, as a
   one-time backlog load; it will not be fine as a recurring weekly habit
   once every team is expected to keep its own queue current here.

4. **No notion of owner/team on a task.** All four teams' work now lives in
   one flat, undifferentiated list with no field to say whose item it is or
   which team's queue it belongs to. Workable at 12 tasks; will not stay
   workable once Concept, Surface, Build, and Red Team are all filing into
   the same store weekly, and it's the kind of thing that's much cheaper to
   add now than to retrofit onto real data later.

None of these four are blocking — the store holds the real queue as of
today and both surfaces write to it — but #1 and #2 are the two Red Team
also independently found from the outside, and #2 was reproduced live here
without any adversarial intent, just by trying to use the tool the way its
own docs say to. That's the strongest signal in this entry: two teams
using the app two different ways landed on the same defect.

Store contents as of this entry (`cadence list`, `COLUMNS=120`,
`CADENCE_HOME=/workspace/cadence_home`): 12 tasks, ids 1–12, none completed
yet. Full command transcript for this session is in
`/workspace/dogfood_mcp_add.py` (the MCP seed) plus shell history captured
in the task evidence for `task_01a049385447118fa9bce0f6`.

## Week 1 — 2026-08-28 (Rafael Okonkwo, Build): cmd_add exit-code fix

Friction found through the team's own CLI use of `cadence add`, not a
synthetic fuzz case: `cadence add "x" --priority ""` exited 2 (the
store/internal code) instead of 1 (the user-input code) per
docs/human-surface.md §4.4, because `cmd_add`'s exception handler hardcoded
`code=2` for *every* `CadenceError` raised by `store.add()`, not just a
genuine store failure. Independently reproduced by Noor and Dov (Red Team)
against a clean checkout. Fixed by only overriding to code 2 when the
exception is `StoreUnavailable`, matching how `cmd_done`/`cmd_schedule`
already behaved; a real store failure still exits 2 because `Store()`
raises before `cmd_add`'s own try block even starts, so it escapes to
`main()`'s top-level catch-all regardless of this change.

## Week 1 — 2026-08-28 (Rafael Okonkwo, Build): title has no max length

Friction-driven, found via Red Team's own use of the CLI (pass-3 finding
#5, a 5000-char repro), not a synthetic edge case dreamed up in isolation:
`cadence add` accepted a title of any length, and `cadence list` then
dumped hundreds of wrapped lines for that one row, breaking the table
layout the whole point of `list` is to keep scannable. docs/human-surface.md
never set a ceiling, but it had already tested wrap behavior against a
200-character title (§6/§7), so 200 is a documented, exercised limit, not
an arbitrary new one. Fixed store-side in `Store.add()` (`MAX_TITLE_LEN`,
same "single source of truth for every writer" pattern already used for
due-date validation), with matching fast-path pre-checks in both `cmd_add`
(cli.py) and `add_task` (mcp_server.py) so a human and an agent get the
same two-sentence §4.4-shaped rejection before either surface touches the
store: `Error: title is 201 characters, max 200. Try a shorter one.` on
the CLI, `{"ok": false, "error": "invalid_task", "message": "title is 201
characters, max 200", "hint": "Try a shorter one."}` from MCP. A 200-char
title still succeeds on both surfaces.
