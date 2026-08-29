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

## Week 1 — 2026-08-29 (Rafael Okonkwo, Build): sync data-loss + crash on the sync surface

Friction found through the same kind of real use this log exists to
capture, just on the agent-facing side: Dov's (Red Team) R-08
re-verification drove the published 0.2.1 wheel through the documented
`sync_tasks`/`resolve_sync_conflict` recovery flow exactly as its own
docstring names it (docs/ten-step-transcript.md "Step 8 in detail"), and
hit two real defects that would bite this company's own multi-client
dogfooding the moment two people's local `CADENCE_HOME` stores ever
diverged and got synced:

- **Data loss on the documented recovery path.** `sync_tasks` correctly
  reports an id **collision** between two independently-created,
  unrelated tasks (each store assigns ids on its own, so two never-synced
  clients' first tasks both land on id 1) — but `resolve_sync_conflict`
  then handled it exactly like a real **edit conflict** on one shared
  task, permanently deleting the losing side's whole, unrelated task.
  Fixed in `Store.sync()`: a collision (no common prior version on either
  side — `base` has nothing for that id) is now detected and auto-resolved
  within the same `sync()` call, never routed through
  `resolve_sync_conflict` at all. This client's task keeps its id; the
  other client's task is preserved under a freshly assigned id, on both
  stores. Reported in a new `renumbered` field, kept separate from
  `conflicts` (which is now only ever a real, same-task edit conflict).
- **Undesigned crash (`KeyError: 2` leaking past the `{ok:false,...}`
  contract).** `Store._history()`/`Store._resolve_remote()` derived each
  store's on-disk history directory from `Path(db_path).stem`, which only
  strips the *last* dot-suffix — so two different `CADENCE_DB_PATH`
  values that merely share the text before their first dot (e.g. `store`
  and `store.db`, or a real store's path suffixed to build a second one)
  silently collapsed onto the exact same history directory and
  cross-contaminated each other's task history, eventually surfacing as a
  raw `KeyError` instead of a clean error. Fixed by deriving from the full
  `.name` instead of `.stem`, so distinct paths always get distinct
  history dirs; `sync()`'s diff/apply step is also now wrapped so any
  remaining internal inconsistency fails with a clean, two-sentence
  `sync_inconsistent` error instead of a raw exception leaking through.

Both fixes ship as `cadence-todo` 0.2.2 with regression tests
(`test_sync_id_collision_between_unrelated_tasks_preserves_both`,
`test_sync_no_crash_when_second_db_path_shares_stem_with_existing_store`,
and two more in the same class in `tests/test_r08_verbs.py`). Neither
reopens R-08's PASS verdict (the ten-step script itself never exercises
either trigger), but both are exactly the kind of thing dogfooding this
company's own multi-client use of the app would eventually have hit on
its own — this time it was caught first by driving the published package
the way an outside agent actually would.

## Week 1 — 2026-08-29 (Rafael Okonkwo, Build): sync fabricates a phantom duplicate on direct-peer topology

Found by Red Team (Dov) independently re-verifying the just-shipped 0.2.2
sync fix, not backfilled: two clients that have never synced with each
other or any hub, each already holding one task before their first sync
(the natural id-1/id-1 collision), doing a plain peer-to-peer
`sync_tasks(remote=<other's CADENCE_DB_PATH>)` in both directions —
exactly step 8 of the ten-step script, no extra steps, no bare-git hub —
left **both** clients with a permanent duplicate of each side's own
original task (3 rows where there should be 2), `ok: true`, no
conflict/error reported anywhere.

Root cause: `Store.sync`'s id-collision handling auto-resolves a
same-id-no-common-base collision by keeping "mine" at its existing id and
copying "theirs" verbatim into a freshly assigned id, on both stores
(0.2.2's Finding-A fix). On the direct-peer topology, that freshly
assigned id gets pushed straight into the *other* peer's own checked-out
history repo — so the very first time that peer runs its own first-ever
sync, the id shows up in `theirs` as a "brand new" id with no local
counterpart and no base, and got blindly pulled in as if it were new
remote content, even though its content (title/due/priority/created_at/
completed_at/parent_id, everything but the `id` field) is byte-identical
to a task the peer already holds under its own id — because it *is* that
task, echoed back. `sync` now checks, for exactly this "theirs looks
brand new to me, no base, no local id" case, whether the incoming
content-minus-id matches one of my own not-yet-based local rows; if so,
it's a reflection of my own data, not a genuinely new task, so it's
skipped instead of duplicated.

Regression tests added on the direct-peer plain-`CADENCE_DB_PATH`
topology specifically (`test_sync_direct_peer_id_collision_does_not_duplicate_either_side`
+ its MCP sibling), asserting an exact task **count** on both sides after
each sync call — the two existing 0.2.2 regression tests only drove the
bare-git-remote hub topology and used a Python `set` for title
membership, which silently collapses duplicates and could not have
caught a count regression on any topology. Shipped as `cadence-todo`
0.2.3. Does not reopen R-08 (verified MET on 0.2.1's transcript, which
never exercised this path); this sits on step 8 of the finish-line
transcript script for whatever a stranger installs today.

## Week 1 — 2026-08-29 (Rafael Okonkwo, Build): 0.2.3's echo-fix only covered a row's first-ever sync

Found by Rafael re-verifying 0.2.3 against the real published PyPI
package before calling it settled — same discipline as the Red Team
passes above. The 2-call repro that motivated 0.2.3 (two never-before-
synced clients, id-1/id-1 collision, sync A→B then B→A) is genuinely
clean. But continuing with a **3rd** ordinary sync of the now-converged
pair (no new local changes since full convergence — the single most
common thing a periodic sync job or a defensively-syncing agent does)
reintroduced the exact same phantom duplicate, and a 4th sync then
crashed with a raw, unhandled `SyncInconsistent: ... KeyError: 2` reading
history data. Deterministic, reproduced twice against the real 0.2.3
package (`pip install cadence-todo==0.2.3` into a throwaway venv, driven
through `cadence.store.Store` directly — the same code path the CLI/MCP
tools call).

Root cause (per the fix spec Ines wrote for 0.2.2's Finding C,
`concept_notes/r08-sync-finding-c-duplicate-fix-spec.md`): 0.2.3's echo
detection matched *content fingerprints* among rows with no base yet —
true only the first time a row is synced. Once a row has been through one
sync round it becomes "based" and drops out of that check, so the next
time it comes back renumbered from a peer's own earlier sync, there's no
longer a fingerprint to catch it against, and it's pulled in as if new —
same bug, one round later. This is exactly the narrow-patch-vs-general-fix
gap Ines flagged when the 0.2.2 fix first shipped.

Fix: every task now carries an immutable `origin` UUID assigned once at
creation (`Task.origin`, a new `origin` column, backfilled on migration
for existing rows), never touched by renumbering, editing, or undo, and
never exposed on the CLI/MCP contract (`to_dict()` strips it; only the
merge engine and git-history blobs see it via `to_full_dict()`). The sync
diff/merge (`_sync_diff_and_apply`) now identifies a task for merge
purposes purely by `origin`, never by display id and never by a
content-fingerprint proxy — so a row that has been synced any number of
times, edited, renumbered, or undone is still recognized as "the same
task" by every peer, for its whole life, not just its first round. A
peer's earlier direct push writing straight into another peer's checked-
out history repo (the direct-peer topology's actual mechanism) is also
now self-healed on every sync by rewriting every task file from this
store's own sqlite truth before committing, instead of trusting
whatever's already on disk.

Regression tests added asserting exact task **counts** (not set
membership) across 4 full sync rounds per side (8 sync calls total) on
the direct-peer topology, both on the `Store` API and the MCP tool
surface, plus a case exercising an edit after convergence and a case
checking `undo` never disturbs a task's `origin`
(`test_sync_direct_peer_repeated_resync_never_duplicates`,
`test_mcp_sync_tasks_repeated_resync_never_duplicates`,
`test_sync_direct_peer_edit_after_convergence_propagates_without_duplicating`,
`test_undo_preserves_origin_identity_across_sync` in
`tests/test_r08_verbs.py`). Confirmed these fail against the pre-fix code
(reverted `store.py` to the 0.2.3 commit, re-ran just these tests: 3/4
failed with the same duplicate-count / crash / missing-attribute symptoms
described above) before confirming they pass against the fix, so the
tests are known to catch this class rather than merely coexist with it.
Shipped as `cadence-todo` 0.2.4. Does not reopen R-08 (still verified MET
on 0.2.1's transcript); this is the second sync-surface fix in a row to
surface only once someone actually re-synced more than twice, which is
the ordinary case for both the finish-line transcript's step 8 and this
company's own two-client dogfooding — worth treating a real 2-client,
many-round sync loop as a standing check rather than a one-off repro
going forward.
