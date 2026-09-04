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

## Week 1 — 2026-08-29 (Rafael Okonkwo, Build)

R-08 re-verify Finding D (Dov Ferreira, `redteam_run7_0224/REDTEAM_PASS_0.2.4_sync_deep.md`),
against the real published 0.2.4: pushing from a client with data into a
peer whose store file exists but has never been written to or synced
(only ever ran `cadence list`, which creates the sqlite file but never
`.history`) returned `Error: no Cadence store found at '<path>'. Check
the path is... correct` — but the path and the store both are correct;
the real cause is uninitialized sync history, which the client doing the
push has no way to fix from its own side. Not a data-loss or crash bug —
Findings A/B/C's merge/diff logic was never in question here — but a
legibility gap on exactly step 8's normal "set up a second device" order
(push toward the new device, not pull-first from it), deterministic 3/3
per Dov's repro.

Fix (`Store._maybe_init_peer_history`, `store.py`): before `sync`
resolves/fetches a given `--remote`, if it's a plain local `.db`-style
path (not a git URL, not already pointing at a repo) whose file exists on
disk but whose derived `.history` dir has no git history yet, initialize
an empty history there now — exactly what a first write on that peer
would have done, before this push ever tries to fetch it. A push into
such a peer now just succeeds instead of erroring; a genuinely
nonexistent path (no file on disk at all) still errors exactly as
before, unchanged. Deliberately scoped to this bootstrap only — no change
to `_sync_diff_and_apply`, the origin-identity merge engine Findings
A/B/C fixed. The peer's own store still only picks up the pushed data
once that peer runs its own `sync` (same as any already-initialized
peer, e.g. `test_sync_remote_accepts_plain_db_path_and_converges`) — this
fix bootstraps the transport, not the pull/apply path.

Regression tests added (`tests/test_r08_verbs.py`):
`test_sync_push_into_peer_that_only_ever_listed_bootstraps_and_succeeds`
(Store API, asserts the push succeeds, the peer's `.history/.git` now
exists, and the peer's own subsequent sync pulls the task in),
`test_sync_into_genuinely_missing_peer_path_still_errors` (confirms a
truly-absent path is unaffected), and
`test_cli_sync_push_into_freshly_listed_peer_succeeds` (real CLI,
reproducing Dov's exact repro end to end: `add` on A, `list` on B, `sync
--remote` from A into B with exit code 0 and no "no Cadence store found"
in the output). All three fail against the pre-fix 0.2.4 code (confirmed
by reverting just `store.py`'s `sync()`/the new helper and re-running:
the two Store-level tests raise `InvalidTask: no Cadence store found`
exactly as Dov's repro describes) and pass against the fix. Full suite:
79 passed. Shipped as `cadence-todo` 0.2.5. Lowest-priority of the R-08
re-verify findings (A/B/C were correctness bugs; this is presentation
only) — taken because nothing higher-priority was queued for Build.

## Week 1 — 2026-08-29 (Noor Halvorsen, Surface)

Chairman-discovered friction on first real use of the CLI, not an internal
nit: he tried it and pushed back with "we created a cli todo app, there are
thousands available for me to install," and separately flagged that the
README doesn't sell the value or give clear use cases. Both findings land
on the same root cause — the README's first screen answered "what is this"
but never "why this one," and its status section was stale (still said "Not
yet published to a package registry" and led with `git clone` +
`pip install -e .` even though `cadence-todo` has been live on PyPI since
0.1.0, currently 0.2.5).

This is the sharpest kind of dogfooding finding: it didn't take a bug or a
crash to lose a real user, just a first screen that read like an internal
build log instead of a pitch. A person deciding whether to trust an
unfamiliar CLI decides in the first few lines, and ours spent them on
"early build" and a from-source install path that isn't even the
recommended one anymore.

Fix, scoped to the top of `README.md` only (status blurb, a new value
section, and install instructions — left `docs/bakeoff.md`,
`docs/human-surface.md`, and the finish-line section untouched):

- New opening section naming the actual differentiator — Cadence is built
  for an agent to run as a first-class user, with git (already installed,
  already trusted) as the real undo/history/audit/sync layer instead of a
  bolted-on feature — followed by four concrete use cases, each grounded in
  a real shipped verb: `decompose` (messy brain-dump → tracked subtasks),
  `reprioritise`/`schedule` plus `git log` (a real answer to "why did this
  change," not a guess), `undo` (a real git revert, not a bespoke undo
  stack), and `sync` (two devices converging over a git remote, no server,
  no account).
- Status section now says what's true: published on PyPI as `cadence-todo`,
  install with `pip install cadence-todo`, CI green on a clean runner.
- Install instructions now lead with `pip install cadence-todo` as the
  primary path; the `git clone` + `pip install -e .` path is kept only
  under a secondary "Building from source" heading.

No code change, no version bump — this is a docs-only fix and ships
straight to `main`.

## Week 1 — 2026-08-29 (Dov Ferreira, Red Team)

Chairman said "hit the MCP hard" right after being pointed at connecting
his own Claude to `cadence mcp` and talking to it naturally. This pass is
scoped to exactly that: not the scripted ten-step transcript (already
verified 10/10 against published releases), but ~90 adversarial,
malformed, and out-of-order MCP tool calls an unscripted agent could
plausibly make, driven against the real published `cadence-todo` 0.2.5
from a fresh venv (`pip install cadence-todo mcp` into a clean
virtualenv, `mcp.client.stdio` talking to `cadence mcp` as a real
subprocess — no repo checkout on the path). Full raw log of every call
and response: `/workspace/redteam_mcp_stress/results.jsonl` and
`stress.py` (harness), `coerce_check.py` and `unknown_tool_check.py`
(follow-up probes), all in the shared workspace.

**Finding 1 (highest priority — files as a Build task under this
requirement): a wrong-JSON-type argument to any MCP tool leaks a raw
pydantic validation dump instead of the designed `{ok:false, error,
message, hint}` shape.** FastMCP validates a call's arguments against the
tool's JSON schema *before* the tool function body (and therefore
`mcp_server.py`'s own `_err_unexpected` net) ever runs, so a
type-category mismatch — string vs int vs bool vs list, or a missing
required field — never reaches cadence's error handling at all. Confirmed
6 ways:
```
add_task(title=12345)            -> isError=true: "Error executing tool add_task: 1 validation
                                     error for add_taskArguments\ntitle\n  Input should be a valid
                                     string [type=string_type, input_value=12345, input_type=int]
                                     \n    For further information visit
                                     https://errors.pydantic.dev/2.13/v/string_type"
add_task(title=True)             -> same shape, bool
add_task({})                     -> "...title\n  Field required [type=missing, input_value={}, ...]"
schedule_task(id="abc", due=...) -> "...id\n  Input should be a valid integer, unable to parse
                                     string as an integer [type=int_parsing, ...]"
schedule_task(id=1.5, due=...)   -> "...id\n  Input should be a valid integer, got a number with
                                     a fractional part [type=int_from_float, ...]"
decompose_task(id=2, into="x")   -> "...into\n  Input should be a valid list [type=list_type, ...]"
```
This is exactly the shape `_err_unexpected`'s own docstring names as the
thing that must never reach an agent ("a shape no agent's `ok` branch is
written to expect") — it just arrives one layer earlier than that net
catches. Numeric-string ids (`id="1"`) *do* coerce fine, so this only
bites on genuine type-category slips — but that is precisely the kind of
slip a free-form conversational agent makes without ever reading the
JSON schema literally (e.g. passing a single string to `into` because it
decided there was only one subtask, or a bare number for `priority`).
Given the chairman may hit this live, this is the one finding on this
pass worth fixing before anything else. Filed to Build (message to
leadership, since only the CEO can create team tasks) tied to this same
requirement.

**Finding 2 (medium): `sync_tasks(remote=<a plain directory, not a `.db`
file and not a git repo>)` is silently accepted instead of rejected.**
`Store._resolve_remote`/`_maybe_init_peer_history` only special-case a
directory that already has `.git`/`HEAD` in it (a real repo); any other
directory falls through to the same "treat it as a stem, append
`.history`" logic used for a plain `.db` path. Repro:
```
$ # CADENCE_DB_PATH=/workspace/redteam_mcp_stress/scratch/cadence.db, 28 tasks already in the store
sync_tasks(remote="/workspace/redteam_mcp_stress/scratch")   # scratch/ is a real dir, no .git inside
-> {"ok": true, "pulled": 0, "pushed": 28, "conflicts": [], "renumbered": [], "already_synced": false}
```
and a brand-new git-backed history repo appears on disk at
`/workspace/redteam_mcp_stress/scratch.history/` — a sibling of the
directory the caller pointed at, holding a full copy of all 28 tasks —
with nothing in the response indicating anything unusual happened. The
`sync_tasks` docstring's own contract says `remote` must be "the OTHER
client's own CADENCE_DB_PATH value (its plain `.db` file path) ... A git
URL also works" — a bare non-repo directory is neither, and
docs/human-surface.md §4.10 promises the exact §4.4 two-sentence error
shape ("naming the one thing to check") for exactly this case ("If a
first connection can't be made from that value..."). Instead it silently
"succeeds" against a location nobody will ever read from, and writes
unexpected files next to wherever the caller pointed. Lower severity than
Finding 1 (no data loss — nothing was there to lose — and it takes a
directory-shaped typo specifically, not any malformed input), but still
a real gap in the "never silently drops data" / "never a raw path
detail leaks" promises this exact code section makes. Filed to Build
alongside Finding 1.

**What held (tried, no defect found):**
- Out-of-order calls against a completely fresh store — `complete_task`,
  `undo`, `resolve_sync_conflict`, `schedule_task`, `reprioritise_task`,
  `decompose_task`, `sync_tasks` all called before any task exists — every
  one returned the clean `{ok:false, error, message, hint}` shape with no
  leak (`task_not_found`, `nothing_to_undo`, `no_such_conflict`,
  `invalid_task` as appropriate).
- `add_task` boundaries: empty title, whitespace-only title, title at
  exactly 200 chars (succeeds) vs 201 (clean reject), a 100k-char title
  (clean reject, same message shape, not a hang or truncated dump), a
  title with emoji/RTL/an embedded quote (`buy milk "for the café" —
  emergency 🥛❤️ مرحبا`, stored and round-tripped byte-correct), and a
  SQL-injection-shaped title (`SQLi'); DROP TABLE tasks;--`, stored
  inertly as a literal string — sqlite3 parameter binding, not string
  interpolation, confirmed by the table still being queryable afterward).
- Malformed `due` values on both `add_task` and `schedule_task`:
  unparsable string, empty string, an impossible calendar date
  (`2026-13-40`), and a full ISO datetime (`2026-09-01T10:30:00`, which
  the docstring's "ISO date/time string" phrasing might suggest works but
  does not — `_validate_due` only accepts a bare date). All four reject
  cleanly with the same `{ok:false, error:"invalid_task", ...}` shape and
  a correct hint; nothing crashes. Worth a docstring tweak (say "date
  string, e.g. 2026-09-01" rather than "date/time string") but the error
  itself is clean and actionable, so not filed as a defect.
- `id` edge cases on `complete_task`/`schedule_task`: nonexistent,
  negative, zero, and an id overflowing sqlite's 64-bit bound
  (`99999999999999999999`) — all return the same clean `task_not_found`,
  including the overflow case (confirms the existing `OverflowError`
  guard in `Store.get` still holds on the real published package).
- Calling `complete_task` twice on the same id: idempotent in practice —
  second call returns `ok:true` with the task already `done`, and because
  both calls landed in the same second the git snapshot is byte-identical
  so no duplicate "Done #1" commit was created (verified via `git log` on
  the store's own `.history` — only one `Done #1` entry exists after two
  calls). Not chasing a same-second race further; a call spaced further
  apart would legitimately re-stamp `completed_at`, which is a reasonable
  "re-affirm completion" outcome, not a data-loss one.
- `reprioritise_task`/`decompose_task` on an already-completed task: both
  succeed rather than reject. Nothing in the docs restricts either against
  a done task, so not filed as a defect, but worth an explicit product
  decision later — a done parent gaining a new open subtask is a real
  state `list_tasks(status="pending")` should render sensibly for an
  agent (each returned task already carries `parent_id`, so an agent can
  reconstruct the relationship itself either way).
- `decompose_task`: empty `into` list, an `into` of only blank/whitespace
  strings, exactly 20 subtasks (the cap — succeeds), 21 in one call (clean
  reject naming the cap), a 21st added via a second call after already at
  20 (clean reject), a nonexistent parent id, and a depth-4 chain
  (parent → child → grandchild → great-grandchild) — the first three
  levels succeed and the 4th is cleanly rejected as "already at max
  decomposition depth (3)", exactly per docs/human-surface.md §4.7.
- `undo`: the documented double-undo symmetry holds, and pushed further —
  ran 15+ consecutive `undo` calls in a row (an "undo storm") walking all
  the way back through the entire history to the initial empty store and
  past it; every call returned a clean `{ok:true, summary}` with no crash
  and no `nothing_to_undo` misfire until history was genuinely exhausted.
- Two-client sync end to end, including a genuine edit conflict: peer
  creates a task and seeds the main store; main pulls it; both sides then
  edit the *same* synced task differently (main reprioritises it, peer
  reschedules it) before re-syncing; the next `sync_tasks` on main
  correctly reported the pair in `conflicts`, and `resolve_sync_conflict`
  settled it as designed. Self-sync (`remote` pointed at the store's own
  db path) is a safe no-op (`already_synced: true, pulled 0, pushed 0`).
- Calling a tool name that doesn't exist (`delete_task`, which nothing
  documents but a chairman-invented "just delete it" instruction could
  plausibly produce) returns a short, clean `isError=true: "Unknown tool:
  delete_task"` from the MCP SDK itself — no stack trace, no schema dump.
- Extra/unrecognized keyword arguments on a valid call (`add_task(title=
  "x", urgent=True)`) and an explicit `null` for an optional field
  (`add_task(title="x", due=None)`) are both silently accepted and
  ignored/treated-as-omitted respectively — reasonable, forgiving
  behavior for a model that includes a field out of over-caution.

Ranked by consequence: Finding 1 first (an agent — including the
chairman's own live session — hits it on an ordinary type slip, and the
result looks exactly like an unhandled crash rather than a designed
error); Finding 2 second (needs a specific directory-shaped mistake, and
loses no data, but still silently does the wrong thing and pollutes the
filesystem). Neither is fixed in this pass — findings only, filed to
Build under this requirement per the task's instructions; Red Team does
not edit the code under test.

## Week 1 — 2026-08-29 (Rafael Okonkwo, Build): both MCP-stress-pass findings fixed

Dov's two findings above (`d00f9ca`, `/workspace/redteam_mcp_stress/results.jsonl`),
both fixed against the real published 0.2.5.

**Finding 1** (`mcp_server.py`): confirmed root cause — FastMCP's
`Tool.run` validates raw JSON args against a pydantic model it derives
from each tool's signature (via `fn_metadata.call_fn_with_arg_validation`)
*before* the tool function, and its own `_err_unexpected` net, ever run.
`add_task(title=12345)` never reached `add_task`'s body at all; the
`ValidationError` (with a `https://errors.pydantic.dev/...` URL baked
into its text) escaped as `mcp.server.fastmcp.exceptions.ToolError`,
which the SDK renders as `isError=true` with that raw dump as the only
content — one layer earlier than the net built for exactly this shape.
Fix: wrap the `FastMCP` instance's own `_tool_manager.call_tool` (an
instance-level override in `mcp_server.py`, not a change to the
installed `mcp` package) so a `ToolError` whose `__cause__` is a
`pydantic.ValidationError` is re-rendered through
`_humanize_arg_validation_error` into the same
`{ok:false, error:"invalid_argument", message, hint}` shape every other
bad-input path already uses, plain language, no pydantic URL. Any other
`ToolError` (e.g. a genuinely unknown tool name) still raises exactly as
before — the net is scoped to the arg-coercion case only.

**Finding 2** (`store.py`, `Store._resolve_remote`): confirmed —
`_resolve_remote` recognized a git URL, `git@` remote, and an existing
git/bare repo directory, but for anything else (including a plain,
already-existing, non-repo directory) it fell through to the
"assume this is a peer's `.db` file path" derivation, silently pushing
into a freshly created sibling `<dirname>.history` repo nobody would
ever sync from again — and `Store.sync`'s `_maybe_init_peer_history`
call ran *before* `_resolve_remote`, so that sibling repo actually got
created as a side effect even before resolution. Fix: `_resolve_remote`
now raises `InvalidTask` for an existing directory that is not itself a
git/bare repo, naming the two legitimate shapes (a peer's own
`CADENCE_DB_PATH` file, or a git URL/bare-repo path) in the hint; and
`sync()` now calls `_resolve_remote` (validate) before
`_maybe_init_peer_history` (the side-effecting bootstrap), so the reject
happens before anything is created on disk. A genuinely nonexistent
path is untouched (still resolves to a future peer `.history` location,
matching Finding D's fix from the entry above).

Regression tests added (`tests/test_smoke.py`), both confirmed failing
against the pre-fix code first (`git stash` on just `mcp_server.py` +
`store.py`, reran the new tests: all 7 failed — the 6 type-mismatch
cases raised the raw `ToolError`/pydantic dump exactly as Dov's repro
shows, and the bare-directory sync case asserted `ok:false` but got
`ok:true`; `git stash pop` restored the fix before trusting them):
`test_mcp_type_mismatched_args_return_structured_error_not_pydantic_dump`
(parametrized over all 6 of Dov's exact repro cases — `add_task(title=
12345)`, `add_task(title=True)`, `add_task({})`, `schedule_task(id=
"abc"/1.5, ...)`, `decompose_task(id=2, into="not-a-list")` — driven
through the real `mcp._tool_manager.call_tool` path, not the bare Python
function, since calling `add_task(...)` directly never exercises
FastMCP's own schema validation) and
`test_mcp_sync_remote_bare_non_git_directory_is_rejected` (asserts
`ok:false, error:"invalid_task"` and that no sibling `<dir>.history`
repo gets created). Full suite: 86 passed (79 + 7 new). Shipped as
`cadence-todo` 0.2.6; CI green on main; re-verified against the real
published 0.2.6 package in a fresh no-repo venv, both via the in-process
tool_manager path and a real `mcp` stdio `ClientSession` subprocess
(matching Dov's own harness shape) — `add_task(title=12345)` came back
`isError=False` with the clean `{ok:false, error:"invalid_argument",
...}` JSON as the tool's text content, no pydantic dump, no URL.

## 2026-08-30 (Rafael Okonkwo, Build) — wow-spec Part III: `reason` + `cadence why`

Chairman-feedback-driven (R-07: "haven't come close to a wow"). Ines's
docs/wow-spec.md Part III diagnosed the actual gap: the git audit trail is
real and legible, but hidden in a directory nobody is told exists, and an
agent's *reasoning* for a decompose/reprioritise was never captured
anywhere in Cadence — only in the calling agent's own context, gone the
moment the session ends. Noor cleared the spec (455ac93) with one binding
fix (dim `•` instead of `list`'s `○`, so a `why` line — a past event, not
a current status — never overloads a glyph `list` already owns).

Shipped exactly as specced: an optional `reason` (and internal `source`,
`"cli"`/`"mcp"`) argument on `decompose`/`reprioritise`/`schedule` on all
three surfaces (store, CLI, MCP) — additive only, rides as a `Reason:`/
`Source:` trailer in the same commit `_snapshot_and_commit` already makes,
so a task with a reason syncs byte-identically to one without (confirmed:
`test_reason_does_not_change_sync_merge_behavior`, and manually — commit
bodies were already confirmed never diffed by the merge engine before
this spec was written). New `Store.why(id)` / `cadence why <id>` /
MCP `why_task(id)` render that task's existing per-task git log
(`tasks/<id>.json`, one file per task, already there) as a plain-language
timeline, newest first — no new storage, no git ever exposed to the
person. Missing id uses the exact §4.4 "no task with id N" wording on
both CLI and MCP, matching every other verb.

Bug caught in manual verification, not by the inherited test suite: the
`--iso` timestamp column was fixed-width (`{when:<12}`) so a full
ISO-8601 string (always >12 chars) glued directly onto the event text
with zero separator — e.g. `2026-08-30T00:20:21+00:00Reprioritised
(none → high)`. `--relative` values ("just now", "2h ago") are always
under 12 chars so this never showed up until `--iso` was actually run by
hand. Fixed with a guaranteed trailing literal space regardless of
padding. Filed here rather than as a Red Team finding since it was
caught and fixed before any commit landed.

Added `tests/test_wow_part3.py` (17 new cases across store/CLI/MCP,
including the sync-merge-untouched guarantee and the `--iso` timestamp
case). Full suite: 103 passed (86 pre-existing + 17 new). Shipped as
`cadence-todo` 0.2.7; CI green on main; verified against the real
published 0.2.7 package (fresh venv, no repo on path) via both the CLI
console script and a real MCP stdio `ClientSession` — `cadence why <id>`
and `why_task` both matched local behavior exactly, including the
reason/no-reason and missing-id paths.

## 2026-08-30 (Dov Ferreira, Red Team) — adversarial pass on 0.2.7 wow-spec Part III (`reason` + `cadence why`)

Fresh venv (`/workspace/redteam_027`, no local checkout on path), real
`cadence-todo` 0.2.7 wheel from PyPI, both the CLI console script and a
real MCP stdio `ClientSession` subprocess. Two real findings, one of them
severe; everything else tried held.

**Finding 1 (high — data integrity, both surfaces, all three verbs).** A
`reason` value containing an embedded newline is silently truncated to
its first line by `Store.why` (so both `cadence why` and MCP `why_task`
show the truncated value — confirmed via `why_task`'s raw JSON, this is
a store-level bug, not a CLI-formatting one). Minimal repro, no
adversarial framing needed:
```
$ cadence add "Test plain multiline reason"
$ cadence reprioritise 9 med --reason "First consideration: budget is tight.
Second consideration: timeline is short.
Third: stakeholder prefers Tuesday."
$ cadence why 9
  ...  "First consideration: budget is tight." — you, via CLI
```
"Second consideration..." and "Third: ..." are gone from the display —
no ellipsis, no `[truncated]` marker, nothing. `git log --pretty=%B` on
the same commit shows the *full* three-line reason was written
correctly (`Reason: First consideration: budget is tight.\nSecond
consideration: timeline is short.\nThird: stakeholder prefers
Tuesday.\nSource: cli`), so this is a read-side parsing bug: the
commit-body parser appears to treat only lines matching `^Reason: ` /
`^Source: ` as data (last match wins if the key repeats) and silently
drops every other line, including legitimate continuation lines of the
one reason paragraph the commit body was designed to hold. Confirmed
identically via MCP (`reprioritise_task(id=1, priority="high",
reason="First point via MCP.\nSecond point via MCP.\nThird point via
MCP.")` → `why_task` returns `"reason": "First point via MCP."` in the
raw JSON, second and third points gone) and on `schedule` in addition to
`reprioritise` (same code path). A sharper variant of the same bug: a
reason whose second line happens to start with literal `Reason: ` or
`Source: ` text (very plausible from an LLM writing free-form reasoning)
gets *silently reattributed* — `--reason "$(printf 'line one\nline
two\nReason: fake injected trailer\nSource: cli-spoofed')"` displays as
just `"fake injected trailer"`, discarding the real first line entirely;
the actual `Source:` attribution stayed correct in every case I tried
(the code always appends the real `Source:` trailer *after* the
caller-supplied reason text, so it wins the last-match), so this is not
an exploitable attribution spoof today, but it is the same underlying
fragility and I'd want a second look once someone touches this code.
**Consequence:** the entire pitch of `why` is "an honest answer to why
this changed"; a silently-incomplete answer that looks complete is worse
than `why` not existing, because nothing about the output signals that
anything is missing. Single-line reasons of any length (tried 6 KB, one
line, no embedded newline) round-trip correctly — this is specifically
about embedded newlines. Fix belongs in the history-read path (`why`'s
commit-body parser), not in write-side escaping, since the write side
already stores the full text losslessly.

**Finding 2 (medium — legibility, `why` reason wrapping).** `list`'s
title wrapping is terminal-width aware (confirmed with a real pty:
`cadence list` wraps a long title differently at 20-col, 40-col and
200-col widths — narrower wraps into more/shorter hanging-indent lines,
200-col fits the title on one line). `why`'s `reason` paragraph wrap is
not: run the exact same task's `why` output through the same pty helper
at 20, 40 and 200 columns and the wrapped text is byte-identical at all
three widths — a fixed ~55-60 char wrap regardless of actual terminal
size. At a narrow terminal (20-40 cols, a real if uncommon width) each
wrapped line is 55-60 chars, well past the terminal edge, so the
terminal itself hard-wraps mid-word and the intended hanging indent is
destroyed; at a wide terminal it leaves most of the line unused. This
violates the "wrap, no truncation, at any terminal width" contract
`human-surface.md` states for the surface generally (and tests
explicitly for `list`'s titles at 40-col/120-col) — `why`'s prose didn't
inherit that behavior. Repro: `python3` with `pty.fork()` +
`fcntl.ioctl(fd, termios.TIOCSWINSZ, ...)` at cols=20/40/200, run
`cadence why <id>` on a task with a reason longer than one line-width in
each, diff the outputs (identical apart from ANSI codes echoing size).

**Held (tried, did not break):**
- `why` with a malformed id: `why 99` (out-of-range) → `Error: no task
  with id 99. Run 'cadence list' to see valid ids.`; `why abc`, `why
  -1`, `why 1.5` → `Error: 'X' is not a task id. Run 'cadence list' to
  see valid ids.`; `why 0` → same "no task with id 0" shape. All exit 1,
  all match §4.4 wording exactly, and match the same wording pattern
  `done`/`schedule` already use for the identical bad-id shapes (`done
  abc`, `done -5` — same text). MCP `why_task(id=-1)` and
  `why_task(id=9999)` both return the structured `{ok:false,
  error:"task_not_found", message:"no task with id N", hint:"Run
  'cadence list' to see valid ids."}` shape, no raw exception.
- `reason` omitted, `--reason ""` (empty string): both surfaces treat
  empty-string the same as omitted — `why` prints "No reason was
  recorded for this change..." either way, MCP returns `"reason": null`
  either way. Consistent, not a silent-truncation case (an empty string
  carries no information to lose).
- `reason` with unicode/emoji (`"venues 🎉 ... naïve café ... déjà vu ...
  中文测试"`): preserved verbatim on both surfaces, no crash, no mangled
  bytes.
- `source` tag: CLI-originated changes show "— you, via CLI", MCP shows
  "— agent, via MCP", correctly, every time. Not spoofable through any
  exposed surface — no `--source` CLI flag exists (checked `--help` on
  `decompose`/`reprioritise`/`schedule`), and passing an extra
  `"source": "cli"` argument to the MCP tool call (which has no
  `source` field in its schema) is silently ignored rather than
  honored — the real caller-surface tag wins regardless.
- Glyph collision: `why`'s dim `•` (`\x1b[2m`, no-color fallback `-`,
  confirmed in a non-tty pipe) never appears where `list` uses `○`, and
  vice versa, checked in a real pty at 20/40/80/200 columns plus the
  non-tty fallback path. No visual or semantic overlap.
- `undo` interaction: reprioritise-with-reason then `undo` then `why` —
  the original event keeps its own reason intact ("escalating because
  deadline moved up" — you, via CLI) and the undo itself appears as a
  new, separate, reason-less event ("Reprioritised (high → none) undone"
  + the standard "No reason was recorded" nudge, correct since `undo`
  takes no `--reason` arg at all). Nothing about the undo overwrites or
  hides the original reason.

**Adjacent, not new to 0.2.7 (noted, not filed as a Part-III defect):**
omitting a required id entirely (`cadence why` with no argument at all)
falls through to argparse's own usage banner and exit code 2, not the
§4.4 two-sentence shape with exit code 1 — `human-surface.md` §4.4
itself reserves exit 2 for internal/store errors. Confirmed this is
pre-existing and cross-cutting, not introduced by this feature: `cadence
schedule`/`done`/`reprioritise`/`decompose` with no arguments at all do
the exact same thing. `why` just inherited the existing gap rather than
closing it. Worth a small separate cleanup pass across all five verbs
if/when Build has a slot; not blocking, not part of this pass's scope.

**Ranking:** Finding 1 (multi-line reason truncation) first if only one
gets fixed — it is silent, it is in the store layer so both surfaces
inherit it, and it directly undermines the specific promise this
release shipped to keep ("why did this change" — an honest, complete
answer). Finding 2 (fixed-width wrap) second — real and reproducible,
but cosmetic/legibility, not data loss.

## 2026-08-30 (Rafael Okonkwo, Build) — both 0.2.7 `why` findings fixed

Both of Dov's findings above, confirmed fix-not-spec-gap by Noor (§6's
"no truncation, wraps at any terminal width" already covers any
text-rendering surface, §7).

Finding 1 (`history.GitHistory.parse_trailers`, commit
[to be filled]): the read-side trailer parser only ever captured the one
line immediately following `Reason: `/`Source: `, so a `--reason` with
embedded newlines lost every line after the first on the way back out —
even though the write side (`Store._snapshot_and_commit`) always
committed the full text and `git log --pretty=%B` on the raw commit
showed it intact. Root cause was read-side only. Fixed by collecting
every line after a recognized trailer key up to the next recognized key
(`Reason: ` / `Source: `) or end-of-message, instead of stopping after
one line — applies to all three reason-capable verbs
(decompose/reprioritise/schedule) on both surfaces (CLI, MCP) at once,
since both go through the same `Store.why` → `parse_trailers` path.
Caught one more bug while fixing it: `git log`'s trailing newline turned
into a spurious empty continuation line via `str.splitlines()`, silently
appending `"\n"` onto whichever trailer was still open (usually
`source`, since it's always last) — fixed by `rstrip("\n")`-ing the raw
message before splitting.

Finding 2 (`cli.cmd_why`'s reason-quote wrap): swapped the hardcoded
`textwrap.wrap(quote, width=56)` for the same
`shutil.get_terminal_size(fallback=(80, 24)).columns` call `cmd_list`
already uses (with a 20-column floor so a pathologically narrow
`COLUMNS` doesn't collapse to nothing), plus `break_long_words=False`
to match `cmd_list`'s title wrap for the same reason (a word overflowing
its own line beats one sliced mid-word). Confirmed `COLUMNS=40 cadence
why <id>` and `COLUMNS=200 cadence why <id>` now produce visibly
different wrapping on a reason longer than one line.

6 new regression tests added to `tests/test_wow_part3.py` (multi-line
reason preserved end-to-end on all three verbs × store/CLI/MCP, plus the
COLUMNS-width-changes-the-wrap case) — confirmed failing against
pre-fix code before trusting them. Full suite: 109 passed (103
pre-existing + 6 new). Shipped as `cadence-todo` 0.2.8.

## 2026-08-30 (Dov Ferreira, Red Team) — adversarial pass on 0.2.8 (`why` trailer-parser + terminal-width wrap fix), 2 new findings

Fresh venv, real published wheel only (`python3 -m venv redteam_028 && pip
install cadence-todo==0.2.8`, confirmed `pip show cadence-todo` → 0.2.8,
no repo on `sys.path`), CLI and MCP surfaces both driven directly.
Re-verified this release's own claims first, then went adversarial per the
task's four angles.

**Held (both 0.2.7 findings genuinely fixed, no regression):**
- 3-line `--reason` through `decompose`/`reprioritise`/`schedule` on CLI:
  every line survives intact in `why`'s output (verified with distinct
  wording per line so a drop or reorder would be visible), no spurious
  blank continuation line from git's trailing newline.
- Same on MCP (`why_task`'s JSON `history[].reason` returns the full
  `"First point via MCP.\nSecond point via MCP.\nThird point via MCP."`
  with embedded `\n` intact — checked the raw JSON, not just the
  pretty-printed CLI rendering).
- `Reason:` paragraph immediately followed by `Source:` with **no** blank
  line (the normal, always-produced shape — confirmed in every raw `git
  log --pretty=%B` body below) parses correctly whenever the reason text
  itself contains no line that looks like a recognized trailer key.
- Empty reason (`--reason ""`) and whitespace-only reason (`--reason
  "   "`) both correctly collapse to "no reason was recorded" — no crash,
  no stray quote-block.
- `undo` interaction: reprioritise-with-3-line-reason → `undo` → `why`
  still renders the original reason intact and unwrapped-differently; the
  undo itself appears as its own reason-less event, nothing overwritten.

**New finding 1 (real, severe — silent data loss): a `--reason` whose
text contains a line starting with the literal `"Reason: "` or `"Source:
"` (i.e. looks like a trailer key) truncates and can permanently drop
earlier reason content, on both CLI and MCP, because both go through the
same `Store.why` → `History.parse_trailers` path.**

Root cause (`cadence/history.py::parse_trailers`, still present in
0.2.8): the loop tests **every** line — including lines already inside an
open trailer's continuation — against `line.startswith("Reason: ")` /
`line.startswith("Source: ")`. Any such line inside the reason body is
misread as the start of a *new* trailer, which reassigns `reason_lines`
(or `source_lines`) to a fresh list, orphaning whatever had been
collected so far with nothing left referencing it.

Repro A — self-collision on `Reason:` (worse case, unrecoverable):
```
cadence add "Repro A"
cadence reprioritise 1 high --reason "$(printf 'Kickoff notes below.\nReason: client asked for it verbally.\nFollow up next week.')"
cadence why 1
```
Displayed reason: `"client asked for it verbally. Follow up next week."`
— the first line, `"Kickoff notes below."`, is gone from every parsed
view. Raw commit body (`git log --pretty=%B -1` in
`cadence.db.history`) confirms the full text was written correctly:
```
Reason: Kickoff notes below.
Reason: client asked for it verbally.
Follow up next week.
Source: cli
```
`Kickoff notes below.` exists nowhere except this raw git plumbing — no
surface (`why` on CLI, `why_task` on MCP, `export`) can ever show it
again. This is real, silent, permanent data loss of exactly the content
this release's fix was written to protect.

Repro B — collision on `Source:` (content misfiled, then usually masked):
```
cadence add "Repro B"
cadence reprioritise 2 low --reason "$(printf 'Note before collision.\nSource: fake trailer text.\nTail line.')"
cadence why 2
```
Displayed reason: `"Note before collision."` only — lines 2 and 3 are
silently dropped from the reason. They get misfiled into `source_lines`
instead (confirmed by re-running with `Source:` as the *last* line of the
reason paragraph: the fake `source_lines` capture is then overwritten by
the real trailing `Source: cli`/`Source: agent` trailer, so the displayed
`source` still happens to read correctly by coincidence of ordering —
but the misfiled reason text itself is gone either way). Reproduced
identically via MCP (`why_task` on the same fixture returns
`"reason": "Note before collision."`, JSON `history[0]`).

This is a content-triggered instance of the same underlying weakness
0.2.7's finding #1 was about (a naive prefix-matching trailer parser),
just triggered by what the reason *says* instead of how many lines it
has. A person writing a reason like `"Source: unclear, following up"` or
`"Reason: was unclear at signup"` — plausible task-management language —
loses part of their own note with no error, no warning, nothing in any
UI to suggest anything was dropped.

**New finding 2 (real, cosmetic/legibility, not data loss): `why`'s
COLUMNS-aware reason wrap does not account for its own indent, unlike
`list`, so at narrow terminals the printed line overflows the terminal
width — the opposite of "matches list's wrapping behavior" this release
claimed.**

`cli.py::cmd_why`: `reason_wrap_width = max(20,
shutil.get_terminal_size(fallback=(80, 24)).columns)` — uses the raw
`COLUMNS` value directly as `textwrap.wrap`'s width, then prepends a
23-character fixed indent (`" " * 23`) to every wrapped line before
printing. `cmd_list`'s `_render_row`, by contrast, computes
`title_col_width = max(10, width - 3 - 2 - 4 - 30 - len(level_indent))`
— it subtracts its own layout overhead from the terminal width *before*
wrapping, so its printed lines stay inside the terminal. `why` skips that
subtraction entirely.

Repro:
```
cadence add "Repro B width test task"
cadence reprioritise 2 high --reason "This reason is long enough that it must wrap across several lines when the terminal is narrow, which is exactly what we are testing here for overflow."
COLUMNS=40 cadence why 2 | awk '{print length($0)": "$0}'
```
Printed reason lines measure 57–63 characters wide at `COLUMNS=40` (up to
57.5% over the 40-column terminal) — e.g. `"This reason is long enough
that it must"` alone is 40 chars before the 23-char indent is added, for
a 63-char physical line. `cmd_list`'s continuation lines at the same
`COLUMNS=40`, by comparison, stay ≤21 characters. COLUMNS=40 vs
COLUMNS=200 do genuinely produce different wrapping (confirmed, so the
core "is it terminal-width-sensitive at all" claim holds) — the defect
is that the computed width is wrong, not that it's static.

**What I could not find a problem with:** the trailer-immediately-follows-
no-blank-line case in isolation (item 2 of the task, non-colliding
content); unicode/whitespace-only reasons; `undo` rendering; MCP JSON
`\n`-preservation for well-formed multi-line reasons.

**Ranking:** Finding 1 (trailer-key collision → silent, sometimes
permanent, reason data loss) first if only one gets fixed — same-severity
class as 0.2.7's original finding #1 (silent loss of the exact data this
feature exists to preserve), and more insidious because the trigger is
ordinary language ("Source: ...", "Reason: ...") rather than something a
person would think to avoid. Finding 2 (width overflow) second — real,
reproducible, and a direct contradiction of this release's own
"matches `list`'s wrapping" claim, but cosmetic, not data loss.

Suggested direction for Build (not prescribing the fix): the read side
needs a parser that isn't fooled by trailer-key-shaped text inside a
value it's already inside — e.g. only recognize `Reason:`/`Source:` as a
new trailer when it appears as the *first* line after the commit
subject's blank-line separator (trailers block), not anywhere a
continuation is still open; or store/join reason text through a format
that can't collide with the trailer grammar at all (e.g. a length-
prefixed or fenced body) rather than continuing to pattern-match line
prefixes. For finding 2, `cmd_why` needs the equivalent of `cmd_list`'s
overhead subtraction: `reason_wrap_width = max(20, columns - len(indent))`
using the same 23-char (or whatever it resolves to) indent string it
already prints with.

---

## 2026-08-30 (Rafael Okonkwo, Build) — fixed Dov's 0.2.8 findings, shipping 0.2.9

Both issues from the 0.2.8 adversarial pass (commit ef51538) fixed in this
pass:

**Finding 1 (severe, trailer self-collision):** `GitHistory.parse_trailers`
in `src/cadence/history.py` now only opens a new `Reason:` trailer when none
is currently open (`current is None`), and only opens `Source:` on the
message's structurally-guaranteed last line — not on any line that merely
starts with those words. `_snapshot_and_commit` (store.py) only ever emits
one shape (`Reason:` opens once right after the subject's blank line;
`Source:`, when present, is always the message's single final line), so that
invariant is what lets the parser tell "trailers-block boundary" apart from
"continuation line that happens to start the same way" without changing the
storage format at all. Regression tests: `test_why_reprioritise_reason_surviving_its_own_reason_prefix_collision`,
`test_why_reprioritise_reason_surviving_a_source_prefix_collision`,
`test_cli_why_shows_every_line_despite_self_colliding_reason_prefix`,
`test_mcp_why_task_survives_self_colliding_reason_prefix` — confirmed all
four fail against pre-fix `history.py` (ran with `git stash push -- src/cadence/cli.py src/cadence/history.py`,
keeping only the new tests) before restoring the fix.

**Finding 2 (cosmetic, COLUMNS overflow):** `cmd_why` (`src/cadence/cli.py`)
now subtracts its own rendered indent — 23 cols for the reason quote, plus
the id-prefix width for the header line and the full row-prefix width for
each event line — from `COLUMNS` before calling `textwrap.wrap`, the same
pattern `cmd_list` already used. All three text surfaces in `why`'s output
(header, event row, reason quote) are now wrapped this way; previously only
the reason quote wrapped at all, and even that used the raw terminal width
instead of `width - indent`. Regression test:
`test_cli_why_output_never_exceeds_narrow_columns` (confirmed failing
pre-fix: 63 > 40).

Full suite: `pytest -q` → 114 passed. Shipped as 0.2.9 (commit to follow this
entry), verified against the real published PyPI wheel in a fresh
no-repo venv per house discipline before closing the task.

## 2026-08-30 (Noor Halvorsen, Surface) — README quickstart replaced with wow-spec §3's decompose/why/undo sequence

`docs/wow-spec.md` §3 named the actual gap: the README's Install section
still led with `add`/`list`/`done`, which the spec itself calls "correct
but not differentiating — every todo CLI has that exact shape," while the
`reason`/`why` mechanism that closes the chairman's "you haven't come
close to a wow" verdict shipped in 0.2.7–0.2.9 and was never reflected in
the first thing a person actually reads. That's the literal gap between
what we told the chairman we'd design and what a stranger sees first.

Verified before touching `README.md`, not after: fresh venv
(`/tmp/readme_verify_venv`, no repo checkout on `sys.path`),
`pip install cadence-todo` (resolved to the real published 0.2.9), ran
the exact six-line sequence — `add`, `decompose --into`,
`reprioritise ... --reason`, `why`, `undo` — against an isolated
`CADENCE_DB_PATH` with `NO_COLOR=1`, output captured verbatim to a file
(`cat -A` checked for hidden whitespace/wrapping) before pasting a single
character into the README. No invented output.

Replaced the Install section's quickstart with that verified sequence and
its real output (the `why` payoff — a legible reason, no git required, is
now the first thing a reader sees rather than the fourth doc down). Left
the value-proposition bullets above Install (`task_01a04e99282faae1665156b1`,
already chairman-tested) and every section below Install (MCP server,
building from source, "what agentic-first means," the finish line,
contributing, license) untouched — this was scoped to Install only.

This closes the wow-spec §3 gap: the blocker it named ("not before
reason/why ship") is cleared, and the README now shows the actual
differentiator the bake-off staked the whole pitch on, in the first 60
seconds, before any agent is mentioned.

---

## 2026-08-30 (Rafael Okonkwo, Build) — 0.2.10: fixed a CI-only Python 3.10 regression the 0.2.9 fix introduced

Shipping 0.2.9 (the fix for Dov's 0.2.8 findings) triggered a real Actions
failure on the "Install + test (Python 3.10)" CI leg only — 3.11 and 3.12
stayed green. `test_cli_why_output_never_exceeds_narrow_columns` failed
with `assert 48 <= 40`, twice, reproducibly on GitHub's hosted runner but
never locally across 7 attempts on a real `cpython-3.10.21` interpreter —
so this was investigated with a temporary debug print (commit 60a26d7,
harmless: `pyproject.toml`'s version was unchanged so `Publish` no-op'd on
that push, confirmed via the Actions API) rather than guessed at.

Root cause: `git log --pretty=%aI` (history.py's `commit_time`, feeding
`why`'s relative-time display) prints a trailing `Z` for a UTC offset on
the newer git installed on GitHub-hosted `ubuntu-latest` runners (git
2.39.5 in this sandbox prints `+00:00` instead — that's why it never
reproduced locally). `datetime.fromisoformat` only learned to parse a bare
`Z` suffix in Python 3.11 (this project's own `requires-python` floor is
3.10); on 3.10, the unparsed `Z` timestamp fell through `_relative_time`'s
except-clause fallback and returned verbatim (~20 chars) instead of "just
now". That long `when` value shrank `cmd_why`'s event-text wrap budget (the
COLUMNS-overhead-subtraction fix shipped in 0.2.9) below the width of the
word "Reprioritised" (13 chars); `break_long_words=False` (by design,
matching `list`) let that whole word overflow onto its own line rather
than slicing it, which is what actually produced the 48-char line.

Fix: `_relative_time` now normalizes a trailing `Z` to `+00:00` before
parsing, so it behaves identically regardless of which git version wrote
the timestamp or which supported Python interpreter (3.10-3.12) reads it.
Regression test `test_relative_time_accepts_git_z_suffix_offset` confirmed
failing against pre-fix `cli.py` on a real Python 3.10.21 interpreter
(`AssertionError: assert '...Z' == 'just now'`) before the fix, passing
after. Full suite (`pytest -q`): 115 passed on both 3.10 and 3.11 locally.

Lesson for the team: this project's floor is Python 3.10, but the sandbox
only has 3.11 installed — `uv python install 3.10` (no root needed) pulls
a real standalone interpreter in seconds and is now the way to validate
anything version-floor-sensitive before shipping, rather than trusting
"passes locally" when locally is a newer interpreter than the floor.
Recorded in team memory.

Shipped as 0.2.10 (commit to follow this entry).

---

## 2026-08-30 (Rafael Okonkwo, Build) — 0.2.11: wow-spec Part II shipped — `cadence register` / `overdue --all-projects` / `sync --all-projects`, the direct fix for "I work on multiple projects"

This is the chairman's own actual working setup (multiple project repos,
one person), staked out in `docs/wow-spec.md` Part I/II as the honest,
cheaply-buildable half of the cross-project pitch (§I.3: "each project's
tasks already live in one plain SQLite file plus a plain git history,
so scanning N known files and merging their rows is a read-only
aggregation on data that already exists in the right shape"). Built
narrowly, exactly as spec'd: no new storage engine, no schema change,
read-only aggregation over stores that already work.

- `cadence register` / `register_project` MCP tool — appends the
  current CADENCE_DB_PATH to a plain-text registry at
  `~/.config/cadence/projects.txt` (or `$CADENCE_CONFIG_HOME`), one path
  per line, idempotent.
- `cadence overdue [--all-projects]` / `overdue_tasks(all_projects=)` —
  merges every registered store's overdue tasks into one project-
  labeled view, leading each row with the `!`/`[!]` overdue glyph per
  Surface's binding note on wow-spec.md Part II (2026-08-30). A
  registered store that can't be opened is reported inline rather than
  failing the whole call.
- `cadence sync --all-projects [--remote <projects-file>]` /
  `sync_tasks(all_projects=)` — a thin loop over the registry calling
  the existing, already-tested per-project `Store.sync` for each entry;
  never touches the merge/diff engine. Carries the same pulled/pushed
  counts and the same `--keep-mine`/`--keep-theirs` conflict-recovery
  line the single-project `sync` already gives, one line per project,
  per Surface's binding note — never silently collapsed to "synced, no
  conflicts."

12 new regression tests (`tests/test_wow_part2.py`), confirmed 9/12 fail
pre-fix (`git stash` on `cli.py`/`mcp_server.py`, the 3 pure-registry
tests pass either way since `registry.py` is additive-only). Full suite:
127 passed. Shipped as 0.2.11, commit 550f8ba — CI
(https://github.com/dominicplouffe/Cadence/actions/runs/33309589608) and
Publish (https://github.com/dominicplouffe/Cadence/actions/runs/33309589577)
both green.

**Verified against the real published PyPI wheel**, fresh venv, no repo
on path (`pip install cadence-todo==0.2.11` from a clean
`/workspace/verify_part2/venv`), with two real registered project
directories (not mocked) — full transcript below, `NO_COLOR=1`,
timestamps/dates as run:

```
$ export CADENCE_CONFIG_HOME=/workspace/verify_part2/config
$ cd /workspace/verify_part2/proj-alpha
$ export CADENCE_DB_PATH=/workspace/verify_part2/proj-alpha/cadence.db
$ cadence register
Registered /workspace/verify_part2/proj-alpha/cadence.db (as 'proj-alpha').
$ cadence add "Write onboarding docs" --due 2026-08-21
Added #1: Write onboarding docs
$ cadence add "Ship the auth fix" --due 2028-08-30
Added #2: Ship the auth fix
$ export CADENCE_DB_PATH=/workspace/verify_part2/proj-beta/cadence.db
$ cadence register
Registered /workspace/verify_part2/proj-beta/cadence.db (as 'proj-beta').
$ cadence add "Renew TLS cert" --due 2026-08-27
Added #1: Renew TLS cert
$ cadence register  (again in proj-alpha, idempotency check)
Already registered: /workspace/verify_part2/proj-alpha/cadence.db (as 'proj-alpha').
$ cat /workspace/verify_part2/config/projects.txt
/workspace/verify_part2/proj-alpha/cadence.db
/workspace/verify_part2/proj-beta/cadence.db
$ cadence overdue --all-projects
[!]  proj-alpha  #1   Write onboarding docs                |  overdue 9d
[!]  proj-beta   #1   Renew TLS cert                       |  overdue 3d
2 overdue across 2 registered projects. Run 'cadence register' in a project directory to add another.
```

Two-client `sync --all-projects`, set up so proj-alpha has real work to
pull and proj-beta has a real, unresolved conflict (not invented):

```
$ cadence sync --remote /workspace/verify_part2/remote-alpha.git   (proj-alpha, first-time remote config)
Synced with origin: pulled 0, pushed 2. Up to date.
$ cadence sync --remote /workspace/verify_part2/remote-beta.git   (proj-beta, first-time remote config)
Synced with origin: pulled 0, pushed 1. Up to date.
--- device B: separate peer stores, same two remotes ---
$ (peer-alpha) cadence sync --remote /workspace/verify_part2/remote-alpha.git
Synced with origin: pulled 2, pushed 0. Up to date.
$ (peer-alpha) cadence add "From device B"
Added #3: From device B
$ (peer-alpha) cadence sync
Synced with origin: pulled 0, pushed 1. Up to date.
$ (peer-beta) cadence sync --remote /workspace/verify_part2/remote-beta.git
Synced with origin: pulled 1, pushed 0. Up to date.
$ (peer-beta) cadence reprioritise 1 high --reason "needs review before renewal"
Reprioritised #1 (none → high): Renew TLS cert
$ (peer-beta) cadence sync
Synced with origin: pulled 0, pushed 1. Up to date.
$ (device A, proj-beta) cadence reprioritise 1 low   (unsynced local edit -> real conflict)
Reprioritised #1 (none → low): Renew TLS cert
$ cadence sync --all-projects   (device A, both registered projects)
proj-alpha  synced: pulled 1, pushed 0. Up to date.
proj-beta   synced: pulled 0, pushed 0. 1 conflict needs you.
proj-beta   Error: #1 was edited on both this client and the remote since the last sync. Nothing was overwritten. Run 'cadence sync --keep-mine 1' or 'cadence sync --keep-theirs 1' with CADENCE_DB_PATH=/workspace/verify_part2/proj-beta/cadence.db, then sync again.
$ echo $?
1
```

Exit code 1 on an unresolved conflict, same convention as single-project
`sync` — a fleet run doesn't report success while one project needs
attention.

MCP surface, same two registered stores, real installed 0.2.11 package
(not a mock of the tool interface):

```
>>> from cadence.mcp_server import overdue_tasks, register_project
>>> register_project()
{'ok': True, 'path': '/workspace/verify_part2/proj-alpha/cadence.db', 'already_registered': True}
>>> overdue_tasks(all_projects=True)
{
  "ok": true,
  "tasks": [
    {"id": 1, "title": "Write onboarding docs", "due": "2026-08-21", "project": "proj-alpha", "overdue_days": 9, ...},
    {"id": 1, "title": "Renew TLS cert", "due": "2026-08-27", "project": "proj-beta", "overdue_days": 3, ...}
  ],
  "count": 2,
  "projects": 2
}
```

Everything above matches wow-spec.md Part II's format: the `!`/`[!]`
overdue glyph leading every merged row, pulled/pushed counts and the
`--keep-mine`/`--keep-theirs` recovery line surviving into the fleet
view instead of collapsing to "synced, no conflicts," and idempotent
`register`. `cadence why --project` (Part II's third moment, reusing
Part III's already-shipped `why`) and the `overdue`/`sync --all-projects`
pieces above were the two `SPEC` rows in Part II's II.1 gap table still
open going into this task; `why --project` was not in this task's scope
and remains open for a follow-on.

## 2026-08-31 (Rafael Okonkwo, Build) — 0.2.12: remote HTTP MCP transport shipped — the direct fix for "VSCode/Claude Code works, Claude web and my phone can't reach it at all"

This is the chairman's exact, repeated complaint in the boardroom
(2026-08-29 17:49–17:52): he works from VSCode/Claude Code, Claude web,
and Claude on his phone, across multiple projects, and only the first of
those could reach cadence's MCP server, because it only spoke stdio (a
local child process on the same machine). He named this as the single
concrete thing blocking him from actually using Cadence day to day and
funded fixing it. A spike proving the approach existed since
2026-08-29 on branch `experimental/http-mcp-transport` (commit d5caed4,
deliberately not merged); this entry ships it.

`cadence mcp --http` starts a second, additive transport option next to
the existing `cadence mcp` (stdio) — same store, same tool functions,
nothing about the sync/merge engine or the stdio path touched. It stays
local-first per the constitution's bias: the user runs it on **their own
machine**, there is no hosted backend and no account system. A 32-byte
hex bearer token is generated on first use and stored at
`~/.config/cadence/mcp_http_token` (mode 0600, same directory as the
rest of Cadence's config); every request must carry
`Authorization: Bearer <token>` or it gets a clean 401 in the same
`{ok, error, message, hint}` shape every other Cadence error uses — not
a stack trace, not a silent accept. `cadence mcp --show-token` prints it;
`--host`/`--port`/`--token`/`CADENCE_MCP_TOKEN` are available for a fixed
token or non-default bind. README's new "Remote access" section documents
how to start it and how to point a remote client (Claude web/mobile, or
an agent on another machine) at it, including that Cadence does not add
TLS itself — an SSH tunnel, Tailscale, or a TLS-terminating reverse proxy
are the suggested ways to expose the port off the local machine.

5 new regression tests (`tests/test_http_transport.py`), confirmed to
fail pre-fix (`ImportError` on `_make_http_app`/`get_or_create_http_token`,
which don't exist before this change). Full suite: 132 passed. Shipped as
0.2.12, commit 8708ed3 —
CI (https://github.com/dominicplouffe/Cadence/actions/runs/33352184224,
green after a rerun of the one job that lost a race with PyPI's index
propagating the just-published 0.2.12 — see note below, not a code bug)
and Publish (https://github.com/dominicplouffe/Cadence/actions/runs/33352184249)
both green; https://pypi.org/pypi/cadence-todo/0.2.12/json confirms
0.2.12 live.

**Verified as a real round trip against the real published PyPI package**
— not local, not mocked. Two separate OS processes in a fresh venv with
`cadence-todo==0.2.12` installed from PyPI (`pip show cadence-todo` →
`/workspace/pypi_e2e_venv_0212/.../site-packages/cadence`, no repo
checkout on `sys.path`): one process running `cadence mcp --http`, a
second, genuinely separate process using the `mcp` SDK's own
streamable-HTTP client (not the stdio path) to add/decompose/reprioritise/
why/undo, then the local `cadence` CLI on the same machine, same
`CADENCE_DB_PATH`, reading the result back — proving one shared store,
not a divergent copy. Timestamps and commands as run:

```
$ pip install cadence-todo==0.2.12
Successfully installed cadence-todo-0.2.12
$ export CADENCE_DB_PATH=/workspace/pypi_e2e_env/cadence.db
$ export CADENCE_CONFIG_HOME=/workspace/pypi_e2e_env/config
$ cadence mcp --show-token
c90b94a9206c6970753292b9c891638fc7de413b7c779391382b8dcceb7f6617
$ cadence mcp --http --port 8791 &
[cadence mcp --http] listening on http://127.0.0.1:8791/mcp -- bearer token required on every request (see `cadence mcp --http --show-token` to print it).
StreamableHTTP session manager started

--- separate process: mcp SDK streamable-http client, Authorization: Bearer <token> ---
>>> add_task({'title': 'Plan Q4 offsite', 'priority': 'med'})
<<< {"ok": true, "task": {"id": 1, "title": "Plan Q4 offsite", "status": "pending", "priority": "med", ...}}
>>> decompose_task({'id': 1, 'into': ['Book venue', 'Send invites']})
<<< {"ok": true, "parent": {"id": 1, ...}, "subtasks": [{"id": 2, "title": "Book venue", ...}, {"id": 3, "title": "Send invites", ...}]}
>>> reprioritise_task({'id': 1, 'priority': 'high', 'reason': 'deadline moved up'})
<<< {"ok": true, "task": {"id": 1, "priority": "high", ...}}
>>> why_task({'id': 1})
<<< {"ok": true, "task": {...}, "history": [{"event": "Reprioritised (med → high)", "reason": "deadline moved up", "source": "mcp", ...}, {"event": "Created", "priority": "med", ...}]}
>>> undo({})
<<< {"ok": true, "summary": "Undid: Reprioritised #1 (med → high) undone: Plan Q4 offsite"}

--- local CLI, same machine, same CADENCE_DB_PATH ---
$ cadence list
  [ ]    1   Plan Q4 offsite                            |  2 open subtasks
    [ ]    2   Book venue
    [ ]    3   Send invites
$ cadence why 1
#1 Plan Q4 offsite — history (newest first):
  -  med      just now     Reprioritised (high → med) undone
  -  high     just now     Reprioritised (med → high)
                       "deadline moved up" — agent, via MCP
  -  med      just now     Created

--- unauthenticated / wrong-token requests, same live server ---
$ curl -i -X POST http://127.0.0.1:8791/mcp -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'
HTTP/1.1 401 Unauthorized
{"ok":false,"error":"unauthorized","message":"Missing or wrong bearer token.","hint":"Send 'Authorization: Bearer <token>' matching the token this server was started with (see `cadence mcp --http --show-token`)."}
$ curl -i ... -H "Authorization: Bearer wrong-token-xyz" ...
HTTP/1.1 401 Unauthorized
{"ok":false,"error":"unauthorized","message":"Missing or wrong bearer token.","hint":"..."}
```

The local CLI shows the exact same task IDs, titles, subtask structure,
the "deadline moved up" reason written in over the remote HTTP client,
and undo's effect — proof of one shared store reached from two genuinely
separate transports, not a mock of either interface.

Note on the CI rerun: the first run of the new "Install from PyPI
registry + drive end-to-end" job failed with `No matching distribution
found for cadence-todo==0.2.12` even though its own preceding step had
just confirmed 0.2.12 was live via PyPI's JSON API — PyPI's JSON API and
its pip-facing simple index propagate on different schedules, and pip
lost that race by about a minute. Rerunning the same failed job (no code
change) went green the moment the simple index caught up; the other
three (3.10/3.11/3.12 install+test) jobs were green on the first try.
Filed as an observation, not a fix, since it's an inherent PyPI
propagation race any "wait, then install" step will occasionally hit,
not a bug in this change.

This closes the chairman's named gap: Claude web and Claude on his phone
can now reach the same task store VSCode/Claude Code does, self-hosted,
token-gated, no account and no hosted backend added.

## 2026-08-31 (Dov Ferreira, Red Team) — Adversarial pass on 0.2.12: HTTP MCP transport + multi-project — 5 real findings, 2 severe

First adversarial pass on the two surfaces shipped in 0.2.12/0.2.11 that
had not yet had one: `cadence mcp --http` (0.2.12, commit 8708ed3) and
`cadence register`/`overdue --all-projects`/`sync --all-projects`
(0.2.11, commit 550f8ba). Every case below was run against the real
published `cadence-todo==0.2.12` wheel from PyPI in a venv with no repo
checkout on `sys.path` (`pip show cadence-todo` →
`/workspace/pypi_e2e_venv_0212/.../site-packages/cadence`), not the
local editable checkout. Do not fix here — this is the find, not the
fix; each item below is precise enough for Build to reproduce on the
first try.

### Findings, worst first

**1. SEVERE — concurrent writes to the same store can report a hard
failure while the write actually succeeded, and permanently lose that
task's audit trail.** `Store.add()` (and every other mutator) commits
the SQLite row first (`store.py:388 conn.commit()`), *then* does a
separate git-backed history commit
(`store.py:390 self._snapshot_and_commit(...)`) — the two are not one
transaction. When two writers touch the same store at once (proven with
plain local CLI processes; MCP `add_task` calls the identical
`Store.add()` so the same race applies to a stdio/HTTP writer racing a
CLI writer), the git step can lose the race for its own
`store.db.history/.git/index.lock` and raise `HistoryError`, which
propagates all the way to the caller as `Error: something went wrong on
Cadence's end (HistoryError: git commit failed: fatal: Unable to create
'.../index.lock': File exists...)`, exit code 2 — but the SQLite row was
already durably committed and is visible in `cadence list`. The task
exists; its "Created" history entry is gone forever (`cadence why <id>`
on it says "No history recorded for this task yet.", permanently, even
after the lock clears). An agent that trusts the failure signal will
plausibly retry the identical `add`, creating a silent duplicate.
Repro (fresh store, real 0.2.12):
```
export CADENCE_DB_PATH=/tmp/x/store.db CADENCE_CONFIG_HOME=/tmp/x/config
for i in $(seq 1 10); do ( cadence add "cli-$i" > out_$i.txt 2>&1; echo "exit=$?" >> out_$i.txt ) & done; wait
grep -L '^Added' out_*.txt   # 3/10 in our run: exit=2, HistoryError
cadence list                 # all 10 tasks present anyway, including the 3 "failed" ones
cadence why <id-of-a-failed-one>   # "No history recorded for this task yet."
```
No leftover `.git/index.lock` after the race — a subsequent single
`cadence add` works fine, so this is a lossy-error/lost-history bug
under concurrency, not permanent store corruption.

**2. SEVERE — `overdue --all-projects` silently recreates a deleted or
moved registered project's store as a fresh, empty database and reports
"0 overdue" with no error, permanently hiding that the data is gone.**
Root cause: `Store.__init__` does
`self.db_path.parent.mkdir(parents=True, exist_ok=True)`
(`store.py:179`) unconditionally, before anything checks whether the
file previously existed, so opening a registered store whose directory
was deleted just fabricates a brand-new empty one instead of erroring.
Repro:
```
cd proj-a && CADENCE_DB_PATH=$PWD/cadence.db cadence add "real task" --due 2020-01-01  # (or schedule)
cadence register
rm -rf proj-a                       # simulate deleted/moved project
cadence overdue --all-projects      # from anywhere else
# -> "0 overdue across 2 registered projects." exit 0, no warning
ls proj-a                           # proj-a/ and a fresh empty cadence.db now exist again, silently
```
A person who deleted a stale project directory gets no signal at all
that their "0 overdue" answer is a phantom, not a real absence of
overdue work.

**3. MODERATE-SEVERE — a single registry line whose content raises
something other than `sqlite3.Error` (e.g. an embedded null byte)
crashes the *entire* `overdue --all-projects` / `sync --all-projects`
call, contradicting the documented per-project-error contract
(`overdue_tasks`'s own docstring: "A registered store that can't be
opened is reported as {project, error, message, hint} instead of a
task, alongside the rest, rather than failing the whole call.").** Same
root cause as #2: the `mkdir` call in `Store.__init__` sits outside its
own `except sqlite3.Error` net, so a `ValueError` from it is never
wrapped into a `CadenceError` — and `cmd_overdue`, `overdue_tasks` (MCP),
and `_cmd_sync_all_projects` only catch `CadenceError` around
`Store(db_path=...)`, so the raw exception escapes to the top-level
handler and aborts the whole command, printing nothing for any other
registered project. Repro:
```
python3 -c "open(os.path.expandvars('$CADENCE_CONFIG_HOME/projects.txt'),'ab').write(b'/tmp/gar\x00bage/cadence.db\n')"
cadence overdue --all-projects
# Error: something went wrong on Cadence's end (ValueError: embedded null byte).
# exit 2 -- ZERO output for the other, perfectly valid registered projects
cadence sync --all-projects
# prints per-project lines fine for entries before the bad one, then:
# Error: something went wrong on Cadence's end (ValueError: embedded null byte).
# exit 2 -- any entries after the bad line in the file are never reached or reported
```

**4. MODERATE — a syntactically-valid-but-wrong (relative) registry
line makes `overdue --all-projects` silently create a real, new SQLite
file in whatever directory the command happens to be run from, with no
warning that it just wrote a file into the caller's cwd.** A hand-edit
that drops the leading `/` (an easy typo) is enough. Repro:
```
printf 'not-a-real-path-relative\n' >> "$CADENCE_CONFIG_HOME/projects.txt"
cd /some/unrelated/dir && cadence overdue --all-projects
ls /some/unrelated/dir   # -> a new file literally named `not-a-real-path-relative` now exists here
```

**5. MODERATE, legibility — `cadence register` keys on
`CADENCE_DB_PATH` (or the single global default store when unset), not
on the current directory, so two different project directories that
never set `CADENCE_DB_PATH` silently collapse into one shared store and
one registry entry, contradicting the tool's own "call once per project"
framing.** `register_project`'s docstring and `cadence mcp`'s
`initialize` instructions both say "call once per project (e.g. once
per repo an agent works in)", which reads as cwd-scoped; it is not.
Repro:
```
cd proj-x && cadence add "task from proj-x" && cadence register   # no CADENCE_DB_PATH set
cd proj-y && cadence add "task from proj-y" && cadence register   # no CADENCE_DB_PATH set, different dir
cat ~/.config/cadence/projects.txt   # -> ONE line, not two
cadence list                          # both "separate" projects' tasks in the same list
```
An agent or person who hasn't already learned to set `CADENCE_DB_PATH`
per project before calling `register` gets silent single-project
behavior with no indication the multi-project feature never engaged.

**6. MINOR, legibility — HTTP-transport-envelope-level malformed
requests do not use cadence's own `{ok, error, message, hint}` contract;
only auth failures and in-session tool-call errors do.** Once inside a
valid MCP session, a genuine tool-level error (over-long title, wrong
arg type, unknown id) correctly returns cadence's shape even over HTTP
— confirmed working. But anything malformed enough to be rejected by
the underlying `mcp` SDK's own request handling, before a tool is ever
invoked, returns the SDK's raw JSON-RPC/plain-text error instead:
```
# bad JSON body:
{"jsonrpc":"2.0","id":"server-error","error":{"code":-32700,"message":"Parse error: Expecting property name enclosed in double quotes: line 1 column 62 (char 61)"}}
# missing 'method' field:
{"jsonrpc":"2.0","id":"server-error","error":{"code":-32602,"message":"Validation error: 4 validation errors for JSONRPCMessage\n...For further information visit https://errors.pydantic.dev/2.13/v/missing..."}}
# missing Accept header:
{"jsonrpc":"2.0","id":"server-error","error":{"code":-32600,"message":"Not Acceptable: Client must accept both application/json and text/event-stream"}}
# 25MB oversized body:
HTTP 413, plain-text body "Request body too large" (not even JSON)
```
The server's own `initialize` response tells every client "All tools
return {ok, ...}; on ok=false, read error and hint" — a remote agent
that takes that literally will not recognise any of the shapes above as
the same contract. (Auth failures at the same layer, handled by
cadence's own `BearerAuth` wrapper, are correctly cadence-shaped —
confirmed with no-header, wrong-token, empty-string-token, and a
5000-char garbage-token request, all cleanly 401 with the standard
shape.)

**7. MINOR, legibility — `initialize`'s `serverInfo.version` reports the
`mcp` SDK's own version ("1.29.1"), not Cadence's ("0.2.12"), because
`FastMCP(...)` is constructed with no `version=` kwarg.** An agent
trying to detect which Cadence feature set it's talking to via the
standard MCP handshake gets a number that has nothing to do with
Cadence's release cadence.

**Hunch, not independently verified this pass:** the bearer-token check
(`mcp_server.py`'s `BearerAuth.__call__`) compares
`presented != self.expected_token` with plain string `!=` rather than
`hmac.compare_digest`, a timing side-channel in principle. Low priority
for a single-operator local-first tool and I did not attempt an actual
timing attack (would need many thousands of samples over a real network
to say anything conclusive) — flagging for awareness, not filing as a
proven finding.

### What held (tried, did not break)

- HTTP auth: missing header, wrong token, empty-string token
  (`Authorization: Bearer ` with nothing after), and a 5000-character
  garbage token were all cleanly rejected with the standard 401
  `{ok:false,"error":"unauthorized",...}` shape, never a stack trace or
  silent accept.
- `--http` binds `127.0.0.1` by default (confirmed in `cli.py`'s
  argparse default), not `0.0.0.0` — no unintended network exposure out
  of the box for a "local-first, your own machine" tool.
- Oversized payload (25MB tool-call body): rejected fast (413, ~25ms),
  no hang, no crash (error shape itself is finding #6 above).
- Once inside a valid MCP session, genuine tool-level errors (over-long
  title, wrong-typed id, unknown task id) over HTTP correctly return
  cadence's own `{ok, error, message, hint}` shape, matching stdio.
- `cadence register` run twice in the exact same directory/`CADENCE_DB_PATH`
  is properly idempotent — no duplicate registry entry.
- No leftover git lock file after a concurrency-induced `HistoryError`;
  the store is usable again immediately, bounding finding #1 to a
  lossy-error/lost-history bug rather than lasting corruption.
- `sync --all-projects` correctly prints a per-project error line and
  keeps going for entries that raise a normal `CadenceError` (e.g. "no
  remote configured for sync") — it only aborts the whole run on the
  non-`CadenceError` case in finding #3.

### Not tested this pass

- Timing-attack feasibility on the bearer-token comparison (see hunch
  above).
- `sync --all-projects` with a genuine cross-client conflict among N
  registered projects was not re-run this pass: already verified
  end-to-end in this log's 2026-08-30 (0.2.11) entry (per-project
  pulled/pushed/conflict line, exit 1, loop does not abort on a real
  conflict), and 0.2.12 (commit 8708ed3) touched only the new HTTP
  transport, not the sync/registry code path, so there is no plausible
  regression surface to re-check there.
- Did not attempt to reach the HTTP port from a genuinely separate host
  (sandbox has no second network-addressable machine available); the
  `127.0.0.1`-default finding above is a static-config confirmation, not
  a live cross-host probe.

If only one of the above gets fixed first: **#1 (concurrent-write false
failure + lost history)** — it is the one that actively lies about
whether an agent's write happened, which is worse than any crash, and it
is the exact scenario ("local stdio CLI and a remote HTTP client both
touching the same store at once") this task was scoped to check.

## 2026-08-31 (Rafael Okonkwo, Build) — 0.2.13: fixed all 5 of Dov's 0.2.12 findings, re-verified against the real published package

Fixed all 5 findings from the entry above (commit `1af1f06`, on top of
`b80ea8e`), worst first:

1. **SEVERE, history-lock race.** `history.py`'s git commit step now
   retries transient `index.lock` contention with bounded backoff +
   jitter. If contention outlasts the whole retry budget, `store.py`
   raises the new `HistoryDegraded` (never a plain `HistoryError`
   failure) and CLI/MCP report **success** with an explicit "Do not
   retry this call — it already succeeded" warning instead of a hard
   failure, since the SQLite row is already durably committed.
2. **SEVERE, phantom empty store.** `Store` now takes
   `must_exist=True` for registered-store opens (`overdue
   --all-projects`, `sync --all-projects`) and refuses with a clear
   hint instead of fabricating a new empty db when the registered path
   is gone.
3. **Registry corruption aborts the whole multi-project call.** The
   `mkdir` call that could raise a non-`sqlite3.Error` (e.g. embedded
   null byte) now lives inside the same try/except as the rest of
   `Store.__init__`, so `cmd_overdue`/`overdue_tasks`/`_cmd_sync_all_projects`
   wrap it into a per-project error line and keep going, per the
   docstring's existing promise.
4. **Relative-path registry typo writes a stray db into cwd.**
   Registry entries are now validated as absolute paths before use;
   a relative entry is reported as an invalid-registry-line error
   instead of being opened verbatim.
5. **`register` silently merges projects when `CADENCE_DB_PATH` is
   unset.** `register_project` now raises `AmbiguousProject` with a
   concrete next step (set `CADENCE_DB_PATH` first) instead of
   registering the single global default store.

Regression tests for all 5 in `tests/test_0212_severe_findings.py`,
confirmed failing pre-fix (house convention). Full suite: `python -m
pytest -q` → **141 passed**, run in this session against the same
`/workspace/cadence_push` checkout at `1af1f06`. Version bumped to
0.2.13; CI green on `1af1f06` (both `CI` and `Publish` workflows,
`https://github.com/dominicplouffe/Cadence/actions`, run for commit
`1af1f06`, conclusion `success`); `cadence-todo==0.2.13` confirmed live
on PyPI (`https://pypi.org/pypi/cadence-todo/json` → `info.version`
`"0.2.13"`, `releases` includes `"0.2.13"`).

### Re-verification: Dov's exact repro steps, re-run against the real published `cadence-todo==0.2.13` wheel

Fresh venv (`python3 -m venv /workspace/pypi_e2e_venv_0213 && pip
install cadence-todo==0.2.13`), no repo checkout on `sys.path` —
`pip show cadence-todo` confirms `Version: 0.2.13` installed from
PyPI, not editable.

**Finding #1 repro (10 concurrent `cadence add`s), verbatim:**
```
$ for i in $(seq 1 10); do ( cadence add "cli-$i" > out_$i.txt 2>&1; echo "exit=$?" >> out_$i.txt ) & done; wait
$ grep -L '^Added' out_*.txt        # files that did NOT start with "Added"
(no output — all 10 started with "Added")
$ cadence list
  [ ]    1   cli-6
  [ ]    2   cli-7
  [ ]    3   cli-2
  [ ]    4   cli-3
  [ ]    5   cli-5
  [ ]    6   cli-10
  [ ]    7   cli-8
  [ ]    8   cli-4
  [ ]    9   cli-1
  [ ]   10   cli-9
$ cadence why 1
#1 cli-6 — history (newest first):
  -  none     just now     Created
$ find /tmp/x1 -iname "*.lock"
(none — no leftover lock)
```
All 10 succeeded (`exit=0`), all 10 have real history — no repeat of
the original bug at this contention level. Pushed harder (30 more
concurrent adds against the same store, 40 total) to force the retry
budget to actually exhaust at least once, and got the new
success-with-degraded-history path live, verbatim from one of the 30
output files:
```
Added #25: stress-16
Warning: Task #25 was created; its history entry failed to record (git commit failed: fatal: cannot lock ref 'HEAD': is at 41a7eee7483a18e5aa73461fa05013944cb4444a but expected 04dfdd371baa2e791958b9c91ee33a133fd89245). Do not retry this call -- it already succeeded. Run 'cadence why 25' to check, or file a bug.
exit=0
```
5 of the 30 hit this path under real contention; **all 30 exited 0**
(`grep -l "exit=[^0]" out2_*.txt` → no matches), and each degraded one
says explicitly "it already succeeded" and "do not retry" — the exact
fix the finding asked for: never tell the caller a successful write
failed.

**Finding #2 repro (deleted registered project), verbatim:**
```
$ cd proj-a && CADENCE_DB_PATH=$PWD/cadence.db cadence add "real task" --due 2020-01-01
Added #1: real task
$ CADENCE_DB_PATH=$PWD/cadence.db cadence register
Registered /tmp/x2/proj-a/cadence.db (as 'proj-a').
$ cd .. && rm -rf proj-a
$ cadence overdue --all-projects
proj-a      Error: no store found at registered path '/tmp/x2/proj-a/cadence.db'. The project directory may have been deleted or moved. Re-run 'cadence register' from its new location, or remove the stale entry from the registry by hand.
0 overdue across 0 of 1 registered project checked (1 could not be opened; see errors above).
exit=0
$ ls /tmp/x2
config
```
No phantom `proj-a/cadence.db` recreated (was silently recreated
pre-fix); explicit per-project warning; the "0 of 1 registered project
checked" wording makes the phantom-zero impossible to mistake for a
real zero.

**Finding #3 repro (embedded-null-byte registry line), verbatim:**
```
$ python3 -c "open('$CADENCE_CONFIG_HOME/projects.txt','ab').write(b'/tmp/gar\x00bage/cadence.db\n')"
$ cadence overdue --all-projects
gar bage    Error: no store found at registered path '/tmp/gar bage/cadence.db'. The project directory may have been deleted or moved. Re-run 'cadence register' from its new location, or remove the stale entry from the registry by hand.
0 overdue across 1 of 2 registered projects checked (1 could not be opened; see errors above).
exit=0
$ cadence sync --all-projects
proj-good   Error: no remote configured for sync. Run: cadence sync --remote <path>
gar bage    Error: no store found at registered path '/tmp/gar bage/cadence.db'. The project directory may have been deleted or moved. Re-run 'cadence register' from its new location, or remove the stale entry from the registry by hand.
exit=0
```
Both commands now report the bad line as one per-project error and
keep going for the good project (`proj-good`), instead of aborting the
whole call with zero output for anything else, as documented.

**Finding #4 repro (relative-path registry line), verbatim:**
```
$ printf 'not-a-real-path-relative\n' >> "$CADENCE_CONFIG_HOME/projects.txt"
$ cd /tmp/x4/rundir && cadence overdue --all-projects
not-a-real-path-relative  Error: registered path 'not-a-real-path-relative' is not absolute. This registry entry is invalid. Re-run 'cadence register' from that project directory, or edit the registry file by hand to remove the bad line.
0 overdue across 0 of 1 registered project checked (1 could not be opened; see errors above).
exit=0
$ ls -la /tmp/x4/rundir
total 8
drwxr-xr-x 2 agent agent 4096 Aug 31 09:22 .
drwxr-xr-x 4 agent agent 4096 Aug 31 09:22 ..
```
No stray file written into `rundir` (pre-fix, a file literally named
`not-a-real-path-relative` appeared here); clear "is not absolute"
error instead.

**Finding #5 repro (register from two dirs, no `CADENCE_DB_PATH`), verbatim:**
```
$ cd proj-x && cadence add "task from proj-x" && cadence register
Added #1: task from proj-x
Error: CADENCE_DB_PATH is not set, so there's no per-project store path to register. Registering the single global default store (/home/agent/.cadence/cadence.db) here would silently merge with any other directory that also runs 'cadence register' without CADENCE_DB_PATH set. Set CADENCE_DB_PATH to a path inside this project first, e.g. 'export CADENCE_DB_PATH=$PWD/cadence.db', then run 'cadence register' again.
$ cd ../proj-y && cadence add "task from proj-y" && cadence register
Added #2: task from proj-y
Error: CADENCE_DB_PATH is not set, so there's no per-project store path to register. Registering the single global default store (/home/agent/.cadence/cadence.db) here would silently merge with any other directory that also runs 'cadence register' without CADENCE_DB_PATH set. Set CADENCE_DB_PATH to a path inside this project first, e.g. 'export CADENCE_DB_PATH=$PWD/cadence.db', then run 'cadence register' again.
$ cat "$CADENCE_CONFIG_HOME/projects.txt"
cat: /tmp/x5/config/projects.txt: No such file or directory
```
`register` now refuses (`AmbiguousProject`, exit 1) with a concrete
next step instead of silently collapsing both directories onto the
one global store — the registry file isn't even created, vs. pre-fix
getting one merged entry for both.

### Not re-checked this pass

Findings #6 (HTTP-envelope-level errors bypass cadence's own
`{ok,error,message,hint}` shape) and #7 (`serverInfo.version` reports
the `mcp` SDK's version, not Cadence's) were flagged MINOR/legibility
and explicitly marked "not blocking" on the task that produced this
fix — left open for a future pass, not silently dropped.

**Result: all 5 findings from the 2026-08-31 Red Team entry above are
fixed and independently re-confirmed against the real
`cadence-todo==0.2.13` package installed from PyPI in a fresh venv, not
the local checkout.**

## 2026-08-31 (Rafael Okonkwo, Build) — 0.2.14: fixed `--all-projects` always exiting 0, even on total per-project failure (Dov's independent 0.2.13 re-verification finding)

**The bug.** Dov's independent re-verification pass of 0.2.13 (fresh
venv, fresh `$HOME`, real PyPI wheel — not trusting the 0.2.13
transcript) found one new MODERATE defect on top of confirming the 5
prior findings held: `cadence overdue --all-projects` and `cadence
sync --all-projects` always exited `0`, even when **every** registered
project failed to open. The per-project error text lines the 0.2.13 fix
added were real, but nothing fed them into the exit code, so a
script/agent following docs/human-surface.md §4.4's exit-code contract
("so a script can tell 'you asked wrong' from 'we broke' apart
programmatically") got zero signal from `--all-projects` even in a
total-failure case — it would have to fall back to parsing free text,
which nothing in `--help` documents as the check to perform.

**The fix (commit `a186474`).** `cmd_overdue`'s `--all-projects` branch
and `_cmd_sync_all_projects` (`src/cadence/cli.py`) now return `2` —
the same store-error class exit code single-project commands already
use — when at least one registered entry couldn't be opened, while
still printing every per-project line and not aborting early (the
0.2.13 "keep going" behavior is unchanged, just no longer silent at the
exit-code level). For `sync --all-projects`, a store-open error takes
precedence over an unresolved conflict (exit `2` beats exit `1`) since
"a project is unreachable" is more severe than "a project synced but
needs a human for one conflict." Two 0.2.12-finding regression tests in
`tests/test_0212_severe_findings.py` had their exit-code assertions
updated from `0` to `2` — they were incidentally testing the exact
old (buggy) contract this fix replaces. New regression tests in
`tests/test_0213_all_projects_exit_code.py` (6 cases: all-bad, mixed
good+bad, and all-healthy/no-registry, on both commands), confirmed
failing pre-fix via `git stash` on `src/cadence/cli.py` alone (house
convention).

Full suite: `python -m pytest -q` → **148 passed**, run in
`/workspace/cadence_push` at commit `a186474`. Version bumped to
0.2.14; pushed to `main` at `a186474`. `Publish` workflow completed
`success` for `a186474`
(`https://github.com/dominicplouffe/Cadence/actions/runs/33385269755`).
`CI`'s `pypi-install-and-drive` job failed on the first attempt purely
on the documented publish/CI race (its 65s wait loop timed out before
PyPI's CDN had propagated `0.2.14`, which was already `Publish`-green
by then — `pip index versions` lagged the raw `/simple/` index by about
a minute); re-ran via
`POST /repos/dominicplouffe/Cadence/actions/runs/33385269751/rerun-failed-jobs`
once `pip install cadence-todo==0.2.14` succeeded locally, and `CI` run
`33385269751` is `completed`/`success` for `a186474`
(`https://github.com/dominicplouffe/Cadence/actions/runs/33385269751`)
— not a regression from this fix, the same race 0.2.11/0.2.12/0.2.13
also raced against, just the first time it lost.

**Re-verification against the real published `cadence-todo==0.2.14`
wheel**, fresh venv (`python3 -m venv /tmp/verify_venv && pip install
cadence-todo==0.2.14` — `pip show cadence-todo` confirms `Version:
0.2.14`), fresh `$HOME`/`CADENCE_CONFIG_HOME`, hand-corrupted registry,
verbatim:

```
$ cat "$CADENCE_CONFIG_HOME/projects.txt"
/tmp/gar bage-1/cadence.db
not-a-real-path-relative
$ cadence overdue --all-projects; echo "exit=$?"
gar bage-1                Error: no store found at registered path '/tmp/gar bage-1/cadence.db'. ...
not-a-real-path-relative  Error: registered path 'not-a-real-path-relative' is not absolute. ...
0 overdue across 0 of 2 registered projects checked (2 could not be opened; see errors above).
exit=2
$ cadence sync --all-projects; echo "exit=$?"
gar bage-1                Error: no store found at registered path '/tmp/gar bage-1/cadence.db'. ...
not-a-real-path-relative  Error: registered path 'not-a-real-path-relative' is not absolute. ...
exit=2
```
Both went from `exit=0` (Dov's pre-fix repro) to `exit=2` (store-error
class) for the same all-bad registry.

Mixed case (one good, one bad entry) — good project's row still prints
in full, exit code still goes non-zero:
```
$ cadence overdue --all-projects; echo "exit=$?"
[!]  proj-good   #1   Overdue thing                        |  overdue 2434d
gar bage    Error: no store found at registered path '/tmp/gar bage/cadence.db'. ...
1 overdue across 1 of 2 registered projects checked (1 could not be opened; see errors above).
exit=2
```

All-healthy registry (regression guard — must stay exit 0):
```
$ cadence overdue --all-projects; echo "exit=$?"
0 overdue across 1 registered project. Run 'cadence register' in a project directory to add another.
exit=0
```

**Result: the finding is fixed and independently re-confirmed against
the real `cadence-todo==0.2.14` package installed from PyPI in a fresh
venv, not the local checkout — all-bad, mixed, and all-healthy
registries each produce the exit code the fix's own contract
promises.**

## 2026-08-31: `cadence mcp --http` 421'd every tunneled request regardless of token, fixed in 0.2.15

Found by Noor (Surface) while verifying the README's "expose to Claude
web/phone via a tunnel" claim before writing it down as fact: a real
Cloudflare Quick Tunnel request with the correct bearer token got a bare
`421 Invalid Host header`, not an MCP response. Root cause: the MCP SDK
auto-attaches DNS-rebinding Host-header protection (scoped to
`127.0.0.1`/`localhost`/`[::1]`) to `FastMCP` unless `transport_security`
is passed explicitly, and that check runs ahead of Cadence's own
`BearerAuth` wrapper -- so any non-localhost `Host` 421'd no matter what
token it carried. This made the documented tunnel path non-functional as
written.

Fix: `mcp_server.py` now passes
`transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)`
explicitly, on the documented basis that `_make_http_app`'s own docstring
already states the bearer token is the security boundary for `--http`
mode -- every request passes through `BearerAuth` regardless of `Host`,
so the SDK's Host check was redundant, not load-bearing. Reasoning is in
a code comment at the `FastMCP(...)` construction.

Shipped as `cadence-todo` 0.2.15 (commit `4a31a87`). `Publish` completed
`success`
(https://github.com/dominicplouffe/Cadence/actions/runs/33415351524).
`CI`'s `pypi-install-and-drive` job hit the same documented publish/CI
race as 0.2.11-0.2.14 (its own PyPI-JSON-API poll said "live" before
`pip install` could actually resolve it) on the first attempt; re-ran via
`rerun-failed-jobs` once `pip install cadence-todo==0.2.15` succeeded
locally, and CI run `33415351518` is `completed`/`success` for `4a31a87`.

Re-verified against the real published `cadence-todo==0.2.15` wheel in a
fresh venv, a real live Cloudflare Quick Tunnel
(`cloudflared` v2026.8.3, `https://component-towns-postal-passes.trycloudflare.com`),
and a real bearer token: an `initialize` call through the tunnel returned
`HTTP 200` with a valid MCP `initialize` response (was `421` before the
fix), and a follow-up `add_task` tool call through the same tunnel
session landed in the on-disk store, confirmed by a local `cadence list`.
A request with no token, and a request with a wrong token plus the same
tunnel-shaped `Host` header, both still 401 cleanly -- disabling the
DNS-rebinding check did not weaken `BearerAuth`. Full transcript:
`docs/tunnel-fix-verified-0.2.15.md`.

Regression test: `tests/test_http_transport.py`'s existing single
live-server HTTP transport test now repeats its full authorized round
trip and its wrong-token check with a `Host: some-name.trycloudflare.com`
header, asserting neither 421s (folded into the existing test rather than
a new one, since FastMCP's `StreamableHTTPSessionManager.run()` can only
be called once per process).

Noor's README "Get Started" tunnel path is unblocked by this fix.

## 2026-08-31 (Noor Halvorsen, Surface) — README "Get Started" section closes the VSCode/web/phone setup gap

Now that Rafael's 0.2.15 tunnel fix is shipped and verified, wrote up the
setup gap this whole thread traces back to: someone installing Cadence had
the stdio path (`cadence mcp`) and the multi-project commands documented,
but nothing telling them how to reach it from Claude web or a phone, where
stdio doesn't apply. Added a `## Get Started` section to `README.md`
(after the existing Install/Quickstart section, which is unchanged) with
three parts: (1) `.mcp.json`/`claude mcp add` snippet for Claude Code/
VSCode over stdio, checked against Claude Code's current MCP config docs;
(2) `cadence mcp --http` plus a Cloudflare Quick Tunnel as the concrete
path to a real HTTPS URL for Claude web/phone, with Tailscale Funnel named
as an alternative, and an explicit "don't use `--host 0.0.0.0`" warning;
(3) `cadence register` / `overdue --all-projects` / `sync --all-projects`
for more than one project store.

The tunnel section is not written from Rafael's transcript — captured my
own, since this is the exact text that ships to a stranger: fresh venv,
`pip install cadence-todo==0.2.15` from PyPI, a real live Cloudflare Quick
Tunnel (`cloudflared` v2026.8.3, `https://fastest-david-remedies-absent.
trycloudflare.com`), a request with no token (clean 401), a request with
the correct token (real MCP `initialize` response, 200, not 421), and a
follow-up `add_task` tool call through the same tunnel session confirmed
against a local `cadence list` afterward — same store, reached a second
way. Tunnel and server processes torn down after capture; nothing left
running.

This closes the "how do I set this up across VSCode/web/phone" gap that
had been open since Cadence first grew an HTTP transport — until today
there was no single place a stranger could go from `pip install` to a
working Claude-web connection.

## 2026-08-31 (Dov Ferreira, Red Team) — independent verification of the 0.2.15 DNS-rebinding-protection-off fix

Ran a separate adversarial pass against the real `cadence-todo==0.2.15`
wheel — fresh venv, fresh store, fresh token, no local checkout on
`PATH`, independent of Rafael's own verification — to check the fix
didn't trade the 421 bug for a quieter hole. Five checks, all pass:

1. Correct/wrong token with a spoofed `Host: evil-attacker.example.com`,
   over a real Cloudflare Quick Tunnel and direct-to-origin. Over the
   tunnel, Cloudflare's own edge 403s a mismatched Host before Cadence
   ever sees the request — that's Cloudflare's protection, not the
   app's, and it wouldn't hold behind a different proxy. Direct to
   `127.0.0.1`, bypassing that edge, is the honest test: correct token
   gets a real 200 regardless of Host, wrong token still 401s regardless
   of Host. BearerAuth alone decides, as designed.
2. Wrong, missing, and empty bearer token over the tunnel: clean 401
   JSON in all three cases, no stack trace, no HTML error page.
3. Unicode (`attacker😈.example.com`) and an 8000-character Host header,
   both tokens, tunnel and direct: no crash, no 500, no bypass either
   way. `server.log` clean of any traceback after the whole pass; a
   sanity request afterward still got a normal 200, and the on-disk
   store was unaffected.
4. Local `127.0.0.1` path: bad token still 401s, correct token still
   works — disabling the Host check did not widen the local attack
   surface.
5. Re-ran Noor's exact original 421 repro against a fresh independent
   tunnel: no-token request is a clean 401 (not 421), correct-token
   request is a real 200 MCP response (not 421). Does not reproduce.

Full transcript: `docs/redteam-0215-independent-verify.md`. No new
finding against the fix itself. One process note: adversarial Host-header
testing against a `trycloudflare.com` tunnel alone undersells the app's
real exposure, because Cloudflare's edge filters malformed/mismatched
Host before Cadence sees it — testing must also go direct-to-origin to
exercise `transport_security` itself. Tunnel and server torn down after
capture.

## 2026-09-01 (Rafael Okonkwo, Build) — first-ever-sync false conflict, found live with the chairman

The chairman ran a real two-client sync demo on 2026-08-31: add a task on
the laptop, sync it to the phone, mark it done on the phone, sync back to
the laptop. `cadence sync` on the laptop reported "1 conflict needs you" —
even though the laptop had never touched that task since creating it; only
the phone had edited it. This is exactly the failure the constitution's
dogfooding clause exists to surface: it did not show up in any test we had
written, only in a real session with a real second client.

Root cause: `_sync_diff_and_apply` only knows a client's own prior sync base
once it has completed a sync before. On a client's very *first* sync there
is no prior base, so the code fell back to treating the base as empty for
every task — which makes any task known to both sides look like it changed
on both sides, even when one side is byte-identical to what it created and
never touched again. Two untouched-on-one-side edits got flagged as a
conflict needing a human referee, when only genuine both-sides-since-a-
shared-point edits should ever require that.

Fix (`src/cadence/store.py`): on a first-ever sync, reconstruct each shared
task's own creation-time content from this client's own git history (the
oldest commit touching that task) and diff against that instead of an empty
snapshot — scoped narrowly to the "both sides already know this task"
branch so brand-new-to-this-side tasks still push/pull unconditionally.
New regression test `test_sync_first_ever_sync_does_not_false_conflict_on_untouched_task`
reproduces the chairman's exact transcript and fails against the old code.

Shipped as cadence-todo 0.2.16 (commit 6a6a4dc). Re-ran the chairman's exact
sequence live against the real published PyPI wheel (fresh venv, two
independent `CADENCE_DB_PATH` stores as device A/device B, no local checkout
on `PATH`): add on A, first sync from B pulls it, B marks done, first sync
from A pulls the completion. Result: `Synced with origin: pulled 1, pushed
0. Up to date.` both times — 0 conflicts, and A shows the task done after
its own first sync. Full transcript captured at the time of this entry.
CI run 33502764390 (commit 6a6a4dc) green on all 4 jobs including the
install-from-PyPI end-to-end job.

## 2026-09-02 (Rafael Okonkwo, Build) — 0.2.12 findings #6 and #7 fixed

The two legibility gaps from the 0.2.12 Red Team pass, left open at the
time as "for a future pass" (see above): HTTP-transport-envelope-level
malformed requests bypassing cadence's own error contract (#6), and
`serverInfo.version` reporting the `mcp` SDK's version instead of
Cadence's (#7). That future pass is this one.

**Fix.** An ASGI shim (`_EnvelopeErrorShim` in `mcp_server.py`) now sits
between `BearerAuth` and the raw `mcp` SDK's streamable-HTTP app. It
buffers only error responses (status >= 400) and reshapes the SDK's raw
JSON-RPC/plain-text error into cadence's `{ok, error, message, hint}`
contract; success and SSE responses pass through untouched and
unbuffered, so no live streaming reply is held up. `cadence.__version__`
now reads back from the installed package's own metadata
(`importlib.metadata.version("cadence-todo")`) instead of a hardcoded
string, and `mcp._mcp_server.version` is set from it, so `initialize`'s
`serverInfo.version` can no longer drift from the real release the way
the old hardcoded "0.2.4" had since 0.2.5. Picked up the unverified
`BearerAuth` timing hunch in the same pass: `hmac.compare_digest` instead
of `!=`.

New regression tests in `test_http_transport.py` cover all four malformed
envelope cases and the version assertion; confirmed failing pre-fix by
stashing the source change and rerunning. Full suite: 149 passed.

Shipped as cadence-todo 0.2.17 (commit eeb2df5). Re-ran Dov's exact
0.2.12 repro steps live against the real published PyPI wheel — fresh
venv, `pip install cadence-todo==0.2.17` (nothing local/editable on
`PATH`), `cadence mcp --http --host 127.0.0.1 --port 8917 --token
verify-token-0217`:

```
$ curl -s -X POST http://127.0.0.1:8917/mcp -H "Authorization: Bearer verify-token-0217" \
    -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize", ...}'
...
"serverInfo":{"name":"cadence","version":"0.2.17"}    # was "1.29.1" (mcp SDK's version) pre-fix

$ curl -s -i ... -d '{not valid json'
{"ok": false, "error": "malformed_json", "message": "Parse error: Expecting property name
enclosed in double quotes: line 1 column 2 (char 1)", "hint": "Send a single well-formed
JSON object as the request body."}

$ curl -s -i ... -d '{"jsonrpc":"2.0","id":2}'          # missing method
{"ok": false, "error": "invalid_request", "message": "Validation error: 4 validation errors
for JSONRPCMessage JSONRPCRequest.method Field required ...", "hint": "Include the required
JSON-RPC fields ('jsonrpc', 'id', 'method') in the request body."}

$ curl -s -i ... (no Accept header) -d '{"jsonrpc":"2.0","id":3,"method":"initialize",...}'
{"ok": false, "error": "not_acceptable", "message": "Not Acceptable: Client must accept both
application/json and text/event-stream", "hint": "Send an 'Accept: application/json,
text/event-stream' header on every request."}

$ curl -s -i ... --data-binary @big.json   # 6MB body
{"ok": false, "error": "request_too_large", "message": "Request body too large", "hint":
"Send a smaller request body -- split it into multiple calls if needed."}

$ curl -s -i -X POST ... -H "Authorization: Bearer wrong-token" -d '{"jsonrpc":"2.0",...}'
{"ok":false,"error":"unauthorized","message":"Missing or wrong bearer token.",...}   # still correct
```

All four envelope-level cases now come back cadence-shaped; the version
handshake reports Cadence's own release; wrong-token auth is still
correctly rejected (hmac.compare_digest change did not break auth).
Server torn down after capture, `server.log` clean of tracebacks
throughout.

## 2026-09-02 (Dov Ferreira, Red Team) — independent adversarial pass on 0.2.17: envelope shim + version fix + hmac hardening — 1 real finding

Separate from Rafael's own verification above: fresh venv, `pip install
cadence-todo==0.2.17` (confirmed `cadence.__file__` resolves inside that
venv's `site-packages`, nothing on `sys.path` under `/workspace`), fresh
token, fresh store. Went beyond his repro on all five points the task
asked for.

### 1. Envelope shim edge cases he did not try — all shaped correctly, one real gap found (see finding below)

- Chunked-encoded request body (no `Content-Length`, generator-fed):
  works identically to a normal body on both the error path (`ping`
  without a session → `400 session_error`, correctly shaped) and the
  success path (`initialize` sent chunked → `200`, real SSE stream, same
  as unchunked). The shim only cares about response status, never
  request framing.
- Bare JSON array top-level (`[1,2,3]`) and bare JSON string top-level
  (`"hello"`): both `400 invalid_request`, correctly shaped, message
  correctly reports pydantic's "Input should be a valid dictionary"
  error.
- `Content-Type: text/plain` with an otherwise-valid JSON-RPC body, and
  `Content-Type` header omitted entirely: both `400 malformed_request`,
  `"message": "Invalid Content-Type header"` — correctly shaped, falls
  into the shim's generic bucket because `_classify_envelope_error` has
  no pattern for this SDK message string, but message+hint are still
  honest and actionable.
- `"jsonrpc": "9.9"` (unsupported version string) and `jsonrpc` field
  missing entirely: both `400 invalid_request`, pydantic's literal-value
  error correctly surfaced and shaped.
- Successful (2xx) and SSE responses: full real round trip via the
  actual `mcp` client SDK (`streamablehttp_client` + `ClientSession`) —
  `initialize`, `add_task`, `list_tasks` — every response arrived
  unbuffered and unmangled, exactly the JSON-RPC shape the SDK expects,
  not reshaped into cadence's envelope. Confirms the code comment's
  claim (`state["status"] < 400` bypasses buffering entirely) by
  behavior, not just by reading it: the `mcp` client library itself
  parses SSE incrementally and would hang/timeout if the shim held the
  stream to buffer it, and it didn't.

### 2. Honest-severity check — real finding, see below. A genuine unhandled server-side exception (not a client mistake) comes back wearing the exact same `"malformed_request"` clothing and "check your request and retry" hint as an ordinary bad request.

### 3. serverInfo.version

Matches `pip show cadence-todo` exactly: both report `0.2.17`. Confirmed
the informational editable-install caveat by reproducing it, not just
reasoning about it: `pip install -e .` into a scratch venv against a
throwaway copy of the repo captures `0.2.17` into that install's static
metadata; editing the throwaway copy's `pyproject.toml` to `9.9.9`
*without* rerunning `pip install -e .` leaves `cadence.__version__`
reporting the stale `0.2.17` — it freezes at install time, it does not
silently drift to track live source edits. Matches what the task
predicted. Informational only, not a defect: this is inherent to
`importlib.metadata` reading static setuptools metadata, not something
this fix could have avoided, and it only affects contributors running an
editable install who then edit `pyproject.toml` without reinstalling —
not the published wheel a real user gets.

### 4. hmac.compare_digest inspection

Read `_make_http_app`'s `BearerAuth.__call__` directly (not just the
diff): the comparison is
`presented is None or not hmac.compare_digest(presented, self.expected_token)`.
Both operands are `str` — `presented` is a slice of the latin-1-decoded
header value, `self.expected_token` is the `str` token `BearerAuth` was
constructed with — so there is no mixed str/bytes `TypeError` path that
could leak a stack trace. The `presented is None` branch (no `Bearer `
prefix at all: missing header, wrong scheme, etc.) short-circuits before
`compare_digest` is ever called, but that is a structurally different
request class with no token content to time against, not a live
byte-by-byte oracle — consistent with the fix's intent. `compare_digest`
itself does not raise on a length mismatch (Python stdlib guarantee); its
one documented residual side-channel (length-based, not content-based)
is the accepted trade-off of using it and is not something this fix
introduced or could avoid. No exception path found.

### 5. Auth re-confirmation — right/wrong token both correct; two new edge cases surfaced one non-issue and one point worth knowing, no defect

- Right token: `200`, real handshake. Wrong token: `401`, standard
  envelope (`error: "unauthorized"`), no stack trace.
- Empty-string token (`Authorization: Bearer ` with nothing after) vs.
  `Authorization` header omitted entirely: byte-for-byte identical `401`
  response both times (same message, same hint, same content-length).
  They are intentionally *not* distinguished, and neither leaks a stack
  trace — correct, uniform-denial design; a distinguishable response
  here would be a minor oracle for "is a client even attempting auth."
- Token with trailing whitespace: sent over a raw socket (bypassing
  `httpx`'s own header-value validation, which refuses to send this at
  all) as `Authorization: Bearer rt0217-secret \r\n` — one literal space
  before the line terminator. Result: `200`, accepted. This is **not** a
  BearerAuth bug: `h11`/`uvicorn`'s HTTP parser strips leading/trailing
  optional whitespace (OWS) from header field values per RFC 9110 §5.5
  before the ASGI app ever sees them, so `presented` is already
  `"rt0217-secret"` with no trailing space by the time `compare_digest`
  runs. Confirmed this is standard HTTP-layer normalization, not a loose
  comparison, by contrast: extra whitespace placed *inside* the value
  (`Authorization: Bearer  rt0217-secret`, two spaces after the scheme,
  raw socket, so genuinely present in `presented`) correctly `401`s —
  internal bytes are compared exactly, only the RFC-mandated edge OWS is
  gone before cadence's code runs. No finding.
- Wrong scheme (`Basic <token>`) and lowercase scheme (`bearer <token>`,
  raw socket): both `401`, same envelope. Correct — the code's
  `auth.startswith("Bearer ")` check is deliberately case-sensitive and
  scheme-sensitive.

### Finding — real, one, worth fixing, moderate

**A genuine unhandled server exception (RecursionError, not a client
input-shape mistake) is shaped identically to an ordinary malformed
request, with a hint that tells the caller to do the one thing that
cannot help: retry.** Repro against the real published wheel:

```
python3 -c "print('[' * 1000 + '1' + ']' * 1000)" > deep.json
curl -s -i -X POST http://127.0.0.1:<port>/mcp \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" --data-binary @deep.json
```
→ `HTTP/1.1 500 Internal Server Error`, body
`{"ok": false, "error": "malformed_request", "message": "Error handling
POST request", "hint": "Check the request against the MCP Streamable
HTTP spec (headers, JSON-RPC body shape) and retry."}`. `server.log`
shows the real cause is an uncaught `RecursionError` inside the `mcp`
SDK's own `json.loads(body)` at `streamable_http.py:494` — Python's
`json` decoder recurses per nesting level and CPython's default
recursion limit (~1000) is exceeded, well below any size limit
(`request_too_large` triggers separately, at ~5MB; this triggers at
~2KB of `[[[...]]]`). Deterministic at any nesting depth ≥ ~1000
(1000/1500/3000 all reproduce; 200/500/800 do not — SDK's normal
`400 invalid_request` instead). Confirmed the process itself survives —
a pre-existing session on the same server answered a normal `tools/list`
correctly immediately after — so this is not a full-server crash, and no
traceback or internal detail reaches the client (`message` is the SDK's
generic "Error handling POST request", nothing more). The bug is purely
in how `_classify_envelope_error`'s fallback bucket works: it pattern-
matches on message text only and never looks at `status_code`, so a
`500` and a `400` that both fail every named pattern land in the exact
same `"malformed_request"` / "check your request and retry" bucket. A
client — human or agent — cannot tell "you made a mistake, fix your
request" from "the server broke processing your request, retrying
identically will fail identically again," and nothing distinguishes this
class of error for anyone watching server health from an ordinary bad
request. Consequence: wasted agent retries (the hint's own advice
cannot succeed) and a real reliability signal (a cheap, ~2KB,
deterministic way to make the server 500) reads exactly like routine
client noise in any log or metric keyed off the `error` field, so it
won't get noticed or tracked as the input-driven crash it is. Not
severe — contained to the one request, no data loss, no info leak — but
real and precisely what the task's honest-severity check was checking
for. Only finding from this pass; it is the one to fix first.
**Fix direction, not prescribed**: have `_classify_envelope_error` (or
its caller) branch on `status_code >= 500` before falling into the
generic client-fault bucket — a distinct `error` code (e.g.
`"internal_error"`) and a hint that does not tell the caller to retry
the identical request would be honest about what actually happened.

### What held

All five task items pass except the one finding above. No other new
finding. Full transcript (scripts + raw output) in
`/workspace/redteam_0217_indep/` (`test_envelope.py`,
`test_success_and_auth.py`, `raw_auth_probe.py`, `server.log`, and their
`*_output.txt` captures). Server torn down after capture.

## 2026-09-02 (Rafael Okonkwo, Build) — 0.2.18: server 5xx no longer mislabeled as client malformed_request

Direct fix for the one finding above. Two changes in `mcp_server.py`:
`_classify_envelope_error` now checks `status_code >= 500` before any
4xx pattern match and returns a distinct `"server_error"` code with a
hint that says plainly this is not the caller's fault and retrying the
identical request will not help; and the RecursionError itself is
closed at the root — a shim scoped only to the `json` name as looked up
inside `mcp.server.streamable_http` bounds JSON nesting depth (limit
200, comfortably under where Dov's repro showed the crash starts, at
1000, and comfortably over any real cadence message's nesting) before
`json.loads` ever runs, so a too-deep body now degrades to the same
clean 400 `malformed_json` path ordinary bad JSON already takes instead
of reaching CPython's recursion limit at all.

Confirmed locally against Dov's exact repro (depth 1000, same server
code path used by the published package) before publishing:

```
depth 1000 -> 400 {"ok": false, "error": "malformed_json", "message":
"Parse error: JSON nesting exceeds this server's 200-level limit: line 1
column 1 (char 0)", "hint": "Send a single well-formed JSON object as the
request body."}
```

versus the pre-fix behaviour (confirmed by stashing the source change
and rerunning the same request): `500`,
`{"ok": false, "error": "malformed_request", ...}`. New regression
tests in `test_http_transport.py` cover both the classifier directly
and this exact live repro; both confirmed failing pre-fix. Full suite:
150 passed.

Shipped as cadence-todo 0.2.18 (commit 8c96e47), confirmed live on PyPI
(`https://pypi.org/pypi/cadence-todo/0.2.18/json` returns the release).

**New finding, out of scope for this task, left for a follow-up**: the
repo's own CI (`Install from PyPI registry ... end-to-end` job in
`ci.yml`) failed on this push (`pip install cadence-todo==0.2.18` inside
the CI runner: "No matching distribution found") even though the
Publish workflow had already succeeded and `pypi.org/pypi/.../json`
already reported the new version. Root cause: the job's wait-loop polls
PyPI's JSON API, which updates fast, but `pip install` resolves against
the Simple index, which sits behind a CDN whose cache can lag the JSON
API by several minutes and by edge location -- confirmed independently
(`pip index versions cadence-todo` still showed only up to 0.2.17 from
one location well after the JSON API and a different location both
already showed 0.2.18). A re-run shortly after failed again for the same
reason before eventually succeeding. This is a pre-existing race in
`ci.yml`'s wait condition (present for every prior release too, just not
hit here before), not something introduced by this fix. Worth a
dedicated fix: wait on the Simple index (what `pip` actually uses)
rather than the JSON API, or retry the install step itself with backoff.

## 2026-09-02 (Noor Halvorsen, Surface) — human-surface.md §4.4: internal/server error wording

Follow-on to 0.2.18 above. The code now tells a `server_error` (5xx,
HTTP transport) apart from `malformed_request` (4xx) internally, but
nothing in the design doc said what a person or agent should actually
see, or how to tell it apart from an ordinary field error at a glance —
so the fix was correct but undocumented, which is its own legibility
gap (an agent reading only the docs, not the source, would not know
`internal_error`/`server_error` exist as a distinct class, or what
their hints promise).

Added a subsection to §4.4 specifying, verbatim against the shipped
code (`src/cadence/mcp_server.py` `_err_unexpected` and
`_classify_envelope_error`, `src/cadence/cli.py` `main()`): the exact
JSON shape for `internal_error` (MCP tool call) and `server_error`
(HTTP-transport 5xx), the exact CLI wording and exit code (`2`), and
the three signals — error-field name, hint never asking for a
corrected request, exit code — that must all agree to separate this
class from a field error. No code change needed: checked the wording
against the two functions above and both already match what's
documented (no drift to fix, no PR to Rafael for this one).

Docs published: `docs/human-surface.md` §4.4.

## 2026-09-02 (Rafael Okonkwo, Build) — 0.2.19: fix CI PyPI-install race against the Simple-index CDN

Direct fix for the finding logged under the 0.2.18 entry above: the
`pypi-install-and-drive` job in `ci.yml` waited for the version to
appear on PyPI's JSON API, then ran `pip install`, which resolves
against the Simple index — a separate CDN path that can lag the JSON
API by several minutes. First CI run after 0.2.18 failed with "No
matching distribution found"; a retry succeeded once the Simple index
caught up.

Two changes in `ci.yml`, both scoped to the `pypi-install-and-drive`
job:

1. The wait step now polls `pip index versions cadence-todo` instead
   of the JSON API. `pip index versions` walks the same Simple index
   `pip install` resolves against, through pip's own resolver, so a
   pass here means the data the install step is about to query has
   actually landed — not a different, faster-updating API that can
   disagree with it.
2. The install step itself now retries up to 5 times with a 15s
   backoff, because the Simple index is a CDN with multiple edge
   nodes: a resolve that succeeds against one edge in the wait step
   can still occasionally miss on whichever edge the install request
   happens to land on next.

Verified locally before shipping: `pip index versions cadence-todo`
against the live index returns `Available versions: 0.2.18, 0.2.17,
...` and the exact-match parsing (`grep -qx`) picks the right version
out of that list; confirmed the false-negative case too (querying a
version that isn't published reports NOMATCH, not a false pass).
`ci.yml` re-parsed clean with PyYAML after the edit.

Shipped as cadence-todo 0.2.19 (no functional/runtime code change,
CI-only).

**Update, same day: 0.2.19's own verification run failed too, with a
different root cause.** The `pypi-install-and-drive` job for 0.2.19
(run `33626808819`, commit `3bd607a`) ran the wait loop for the full
600 seconds (40 attempts × 15s) and never saw `0.2.19` in `pip index
versions`' output, even though the version was resolvable well within
that window (confirmed manually via `curl` and `pip index versions`
run standalone). The loop's own timeout — 40 × 15s = exactly 600s —
was the tell.

Root cause: PyPI serves the Simple index with `Cache-Control:
max-age=600` (confirmed via `curl -sI https://pypi.org/simple/
cadence-todo/`). pip's *local* HTTP cache (`~/.cache/pip/http`) honors
that header. The wait loop's first `pip index versions` call populated
that local cache; every call after it, for the entire 600-second loop,
was served straight from pip's own stale local cache instead of ever
touching the network again. The loop wasn't racing PyPI's CDN at all —
it was reading a snapshot of the index taken at second zero and never
refreshing it, so it was mathematically incapable of passing unless
the version happened to already be live at the very first poll.

Fix, in `ci.yml`, `pypi-install-and-drive` job: added `--no-cache-dir`
to both the polling command (`pip --no-cache-dir index versions
cadence-todo`) and the install step (`pip install --no-cache-dir
"cadence-todo==$VERSION"`), forcing every attempt to be a live network
request instead of a local cache hit. Verified the flag's placement
and effect locally: `pip --no-cache-dir index versions cadence-todo`
and `pip install --dry-run --no-cache-dir --no-deps
"cadence-todo==0.2.19"` both resolve correctly against the live index.
`ci.yml` re-parsed clean with PyYAML after the edit.

Shipped as cadence-todo 0.2.20 (again no functional/runtime code
change, CI-only) to trigger a real publish + CI run against the fix.

**Confirmed same day.** CI run `33628043435` (commit `390e495`) for
0.2.20: `pypi-install-and-drive` passed on the first attempt, no
manual retry. The wait step resolved `cadence-todo==0.2.20` on the
Simple index in ~64 seconds (12:05:45 → 12:06:49 UTC) instead of
burning the full 600-second timeout. All 4 jobs (3 Python-version
matrix + the PyPI-install job) went green:
https://github.com/dominicplouffe/Cadence/actions/runs/33628043435

## 2026-09-02 (Rafael Okonkwo, Build) — 0.2.21: oversized bare JSON integer no longer crashes to a misleading-hint 500

Dov's independent verify of 0.2.20 (fresh venv, real PyPI, not the
local checkout) confirmed the 0.2.18 5xx-classification fix holds, and
found a narrower sibling: a JSON-RPC body with a bare (unquoted)
integer literal over 4300 digits -- Python's
`sys.get_int_max_str_digits()` -- makes stdlib `json.loads` raise
`ValueError` inside `int()`, a different crash than the nesting-depth
RecursionError 0.2.18 already guards (this input has no nesting at
all). It correctly classified as a 500 `server_error` (no crash, no
leaked traceback -- the 0.2.18 fix generalizes right), but every 5xx's
hint says "editing the request will not help," which is false here:
the request genuinely is malformed and shrinking the number fixes it
every time.

Fix in `mcp_server.py`: extended the same pre-validation approach the
nesting guard already uses. `_json_number_too_long` does a single-pass
scan of the raw body (same string/escape-tracking technique as
`_json_nesting_too_deep`, so it cannot itself recurse or build the
oversized int) for a run of digits outside any string literal longer
than the interpreter's digit limit, and `_DepthBoundedJSONForStream
ableHTTP.loads` now raises `json.JSONDecodeError` on it before handing
off to the real `json.loads` -- so this degrades to the same clean 400
`malformed_json` path ordinary bad JSON already takes, with the
existing accurate hint ("send a single well-formed JSON object"), same
as Noor's read: no new wording needed, this is a second trigger for an
existing, already-correct 4xx path, not a new error shape.

New regression test in `test_http_transport.py`, alongside the nesting
round-trip: a 4301-digit integer now gets 400 `malformed_json` with no
"will not help" wording; the exact boundary (4300 digits, one under
the limit) still parses fine and falls through to an ordinary
`session_error` (this request never called `initialize`), confirming
the new check doesn't over-trigger. Full suite: 150 passed.

Shipped as cadence-todo 0.2.21 (commit `9829d3d`), confirmed live on
PyPI (`https://pypi.org/pypi/cadence-todo/0.2.21/json` returns the
release). CI green same push, `pypi-install-and-drive` first try:
https://github.com/dominicplouffe/Cadence/actions/runs/33631782231

**External verify, not just local unit tests.** Added
`scripts/verify_live/oversized_int_hint.py`: pip installs cadence-todo
fresh from real PyPI into a brand-new temp venv (never the local
checkout), starts the installed `cadence mcp --http` console script,
and POSTs a live 4301-digit-integer JSON-RPC body at it over a real
HTTP connection. Run against the published 0.2.21:

```
Installed cadence-todo==0.2.21 from real PyPI into /tmp/cadence-verify-oversized-int-.../venv
4301-digit integer -> HTTP 400, error='malformed_json', hint='Send a single well-formed JSON object as the request body.'
4300-digit integer (boundary, must still parse) -> HTTP 400, error='session_error'
PASS: cadence-todo==0.2.21 (real PyPI) rejects an oversized bare JSON integer with a clean 4xx and an accurate hint; the boundary (4300 digits) still parses fine.
```

## 2026-09-02 (Dov Ferreira, Red Team) — independent verification of the 0.2.21 numeric guard, 5 checks, no new finding

Separate pass against the real `cadence-todo==0.2.21` wheel — fresh
venv (`python3 -m venv`), `pip install cadence-todo` from live PyPI, no
local checkout on `PATH`, own token, own port. Confirmed installed
version first: `pip show cadence-todo` → `Version: 0.2.21`. Server
started from the installed `cadence` console script
(`cadence mcp --http --port 8811`); every request below sent with
`urllib`/`curl` as real bearer-authenticated HTTP POSTs to
`http://127.0.0.1:8811/mcp`. Five checks, all pass:

1. **Threshold constant, boundary at all three points.** Confirmed the
   real constant first: `python -c "import sys;
   print(sys.get_int_max_str_digits())"` inside the installed venv →
   `4300`, matching the code comment in `mcp_server.py`
   (`_MAX_JSON_INT_DIGITS = sys.get_int_max_str_digits()`). Bare
   top-level int, digit run of 4299 (one under), 4300 (exact), 4301
   (one over):
   ```
   bare-int-4299-digits -> HTTP 400, error='session_error'   (parses fine)
   bare-int-4300-digits -> HTTP 400, error='session_error'   (parses fine, exact boundary)
   bare-int-4301-digits -> HTTP 400, error='malformed_json'  (blocked)
   ```
   `session_error` here just means the JSON itself parsed and the
   server correctly complained the session was never initialized —
   the point is neither 4299 nor 4300 hit the numeric guard. Matches
   the 4300/4301 split already in `test_http_transport.py`, now
   confirmed against the shipped artifact instead of the local suite,
   plus the previously-untested 4299 point.

2. **Not just bare top-level ints.** The guard is a single-pass scan
   over the *whole* raw request body for any run of digit characters
   outside a string literal — it has no notion of JSON position, sign,
   or number type, so it catches all of these, not only the one shape
   already tested:
   ```
   negative-int-4301-digits (leading minus)        -> malformed_json
   negative-int-4300-boundary (leading minus)       -> session_error (correctly not blocked)
   nested-in-array-4301 ({"x":[1,2,999...9,3]})     -> malformed_json
   nested-in-object-4301 ({"x":{"a":{"b":999...9}}})-> malformed_json
   oversized-float-4301-int-part (999...9.5)        -> malformed_json
   oversized-float-4301-frac-part (1.999...9)       -> malformed_json
   oversized-sci-notation-exponent (1e999...9)      -> malformed_json
   oversized-sci-notation-mantissa (999...9e1)      -> malformed_json
   ```
   One thing worth naming, not a defect: the float and
   scientific-notation cases above are actually harmless even without
   the guard — Python's `float()` has no int-conversion digit limit,
   so an oversized float literal never hit the original crash. The
   guard blocks them anyway because it can't tell a huge float's digit
   run from a huge int's; that's over-inclusive by one bug class, not
   under-inclusive, and the cost is a clean 400 on an input no real
   client sends (a JSON number with 4000+ literal digits). Safe
   direction to be wrong in.

3. **Regression: ordinary large-but-legal numbers.** A real unix
   timestamp, an int64-max-sized id, and an ordinary decimal all
   parse fine, unaffected by the guard:
   ```
   ordinary-unix-timestamp (1735689600)               -> session_error
   ordinary-int64-max (9223372036854775807)            -> session_error
   ordinary-float (3.14159265358979)                   -> session_error
   ```
   No false-positive rejection.

4. **`scripts/verify_live/oversized_int_hint.py` is real and would
   catch a reversion.** Ran it unmodified against live PyPI (picks up
   latest, which is 0.2.21): `PASS`, confirming §"External verify"
   above. Then proved it isn't a script that would pass regardless of
   what's published: made a throwaway copy that pins the install to
   `cadence-todo==0.2.20` — the last version *without* this fix — and
   ran that. It correctly fails:
   ```
   Installed cadence-todo==0.2.20 from real PyPI into /tmp/.../venv
   4301-digit integer -> HTTP 500, error='server_error', hint="... editing the request will not help."
   FAIL: 4301-digit integer literal still crashes to a 500 -- fix not present in cadence-todo==0.2.20: {...}
   ```
   Exit code 1. If the guard were ever reverted and republished, this
   script would fail CI's `pypi-install-and-drive` job the same way,
   not silently pass.

5. **Original 0.2.20 repros re-confirmed against 0.2.21, not the local
   suite.** Both of Dov's original findings, replayed as raw HTTP
   against the live installed server:
   ```
   depth-1000-nesting  -> HTTP 400, error='malformed_json', hint='Send a single well-formed JSON object as the request body.'
   oversized-int-4301-repro -> HTTP 400, error='malformed_json', hint='Send a single well-formed JSON object as the request body.'
   ```
   Neither is a `server_error` any more; both get the accurate
   "send a well-formed JSON object" hint instead of the old "editing
   won't help" 500 wording. `server.log` for the whole session has no
   traceback — the guard pre-empts before the SDK's own parser ever
   sees the bad input, exactly as designed. A bonus nesting-depth
   boundary check (199/200/201, not required by this pass but free
   once the server was up) also lined up with `_MAX_JSON_NESTING_DEPTH
   = 200` once the JSON-RPC envelope's own outer `{` is counted as one
   level: 199 parses, 200 and 201 are blocked.

No new finding. The fix holds against all five checks, generalizes
correctly beyond the one case already tested, and the live-PyPI verify
script is a real regression trap, not decoration. Server and venv torn
down after capture; nothing left running.

## 2026-09-03 (Dov Ferreira, Red Team) — Week-2 dogfooding: real company queue on cadence-todo 0.2.21, one real sync bug found

Fresh venv, `pip install cadence-todo` from live PyPI (no local
checkout, no editable install) → confirmed `Version: 0.2.21`. Set
`CADENCE_DB_PATH` to a new db and actually ran this week's queue
through it, not staged fixtures:

- `cadence add "Finish bake-off ranking doc: rank 5 concepts, write why the other 4 lost" --priority high`
- `cadence add "Watch CI pypi-install-and-drive job for flakiness after 0.2.21" --priority low`
- `cadence add "Red Team: keep dated weekly dogfooding entries going, not one-time backfill" --priority med`
- `cadence schedule 1 ...` decomposed into 3 real subtasks (`cadence decompose 1 --into "Draft the 5-concept comparison table" "Score each concept against the three finish-line tests" "Write the ranking with the losing-4 rationale"`)
- scheduled, reprioritised, completed, queried (`list`, `overdue`), undid a reprioritise, exported to JSON, and synced the queue across two clients (a laptop db and a phone db) — 8 of the 10 script-shaped operations, not just the required 2-3.

### Minor friction, not a defect

`cadence add --priority medium` is rejected — only `high`/`med`/`low`
are accepted, and "medium" is the natural word a person types. The
error message is good (`Try: --priority high, --priority med, or
--priority low`), so this cost seconds, not minutes, but it's real
friction on the very first command of the session.

`add` takes priority/due as flags (`--priority`, `--due`) but
`reprioritise`/`schedule` take the equivalent value as a bare
positional (`cadence reprioritise <id> <priority>`, `cadence schedule
<id> <date>`). I reached for `--priority`/`--when` on both by habit
from `add` and got a clean argparse usage error both times, never a
crash — so legibility held — but the surface itself is inconsistent
about flag vs. positional for the same kind of value across sibling
commands. Worth a design pass, not urgent.

### Real bug: false sync conflict when a task was edited locally before its owner's own first-ever sync

Severity: moderate. Consequence: sync refuses to converge and reports
"1 conflict needs you" on a task nobody actually edited concurrently;
recovery works (`--keep-mine`/`--keep-theirs`, nothing is silently
overwritten) but costs a manual step every time this shape occurs, and
an agent picking `--keep-mine` on reflex (favouring its own state)
would genuinely destroy the other side's real edit, because the tool
cannot itself tell this apart from a real conflict.

This hit live, in the ordinary flow above: on client A I created task
#4 then scheduled it (`cadence schedule 4 2026-09-08`), both before A
had ever run `cadence sync`. Client B pulled it (already scheduled)
and completed it. When A ran its own first-ever `cadence sync` to pull
B's completion:

```
Synced with origin: pulled 0, pushed 1. 1 conflict needs you.
Error: #4 was edited on both this client and the remote since the
last sync. Nothing was overwritten. Run 'cadence sync --keep-mine 4'
or 'cadence sync --keep-theirs 4', then sync again.
```

Neither side made a real concurrent edit — A's "edit" was the
schedule, already fully present in what B pulled; B's only edit was
the completion, made against A's already-scheduled state. Minimal,
isolated repro (`/workspace/dogfood_week2/repro_conflict2`, not staged
against the real queue db):

```
CADENCE_DB_PATH=a/db.sqlite cadence add "task edited on A before A ever syncs"
CADENCE_DB_PATH=a/db.sqlite cadence schedule 1 2026-09-08 --reason "pre-sync edit on A"
CADENCE_DB_PATH=b/db.sqlite cadence sync --remote a/db.sqlite      # B's first sync, pulls #1 already scheduled
CADENCE_DB_PATH=b/db.sqlite cadence done 1                         # B's only edit, post-pull
CADENCE_DB_PATH=a/db.sqlite cadence add "unrelated new task on A"  # A never touches #1 again
CADENCE_DB_PATH=a/db.sqlite cadence sync --remote b/db.sqlite      # A's first-ever sync call
# -> "1 conflict needs you" on #1, false positive
```

A control run with the schedule step removed (A creates #1 and never
touches it again before A's first sync) does **not** conflict — pulls
cleanly, matching the existing regression test
`tests/test_r08_verbs.py::test_sync_first_ever_sync_does_not_false_conflict_on_untouched_task`.
That test's own docstring says "A: create, never touched again" —
exactly the gap: it covers zero pre-sync local edits, not one or more.

Root cause, read from `src/cadence/store.py`
(`Store._first_sync_task_base`, used from `_sync_diff_and_apply` when
`base_ref is None`, i.e. a client's first-ever sync): for a row this
client already held before its first sync, the merge base is
reconstructed from `hist.log_for_file(relpath)` and taken as
`commits[-1]` — documented in the function's own docstring as "the
OLDEST commit that ever touched tasks/<id>.json", i.e. the row's
content at **creation**. If the client edited that row (schedule,
reprioritise, decompose — anything that adds a second local commit for
that file) at any point before its own first sync, the true base
should be the row's content at the **most recent** pre-sync commit,
not the oldest. Diffing against the stale creation-time base makes
`mine_changed` true for an edit the remote already has in full, and if
the remote independently changed the row afterward, `theirs_changed`
is also true — both look changed since a base neither side actually
shares, producing a false "edited on both sides" conflict. Filed here
for Build to pick up; not fixed by Red Team.

### What held

Malformed/edge requests against the real installed package, all clean
40x-shaped CLI errors, no crash, no stack trace: `cadence done 999`
(no such id), `cadence add ""` (empty title), `cadence schedule -5
2026-09-08` (negative id), `cadence schedule 4 "next tuesday"`
(unparseable date). `undo` correctly reverted a reprioritise and
`list` reflected it immediately. `export` produced valid JSON with the
full real task set. The sync-conflict recovery path itself
(`--keep-theirs`) resolved cleanly to the correct converged state once
invoked.

Company queue and both dogfooding-log entries above are the real
week-2 usage; nothing here was staged purely to fail.

## 2026-09-03 (later): 0.2.22 fix task went BLOCKED on 3 straight check
## failures right after publish — PyPI propagation race, not a
## regression

The task that shipped the 0.2.22 fix above (false conflict on a
pre-sync local edit, commit 46e04fe) had its own success_test — the
exact repro command re-run against the live published wheel — fail
with exit 1 three times in a row, ~10:47–10:48 UTC, right after
`cadence-todo==0.2.22` was published to PyPI. The platform's truncated
"Last output" cut off right after `Synced with origin: pulled 1,
pushed 1. Up to date.`, with no visible "conflict" text, which was
confusing since the check only exits 1 if the word "conflict" appears
somewhere in that output.

Re-ran the identical success_test command three times in a row against
the live published PyPI package (fresh venv each time, no cache
reuse), full untruncated stdout+stderr captured to a file:

```
$ bash /tmp/verify_cmd.sh   # the exact success_test body, byte for byte
VERSION=0.2.22
Added #1: task edited on A before A ever syncs
Scheduled #1 for 2026-09-08: task edited on A before A ever syncs
Synced with origin: pulled 1, pushed 0. Up to date.
Done #1: task edited on A before A ever syncs
Added #2: unrelated new task on A
Synced with origin: pulled 1, pushed 1. Up to date.
EXIT_CODE=0
```

All 3 runs identical, exit 0, no "conflict" anywhere in the full
output. This matches the CEO's earlier manual repro (11:00 UTC) and
confirms 0.2.22 has no regression. The 3 platform-check failures right
after publish are the same pattern already seen on 0.2.19/0.2.20: a
fresh `pip install` right after `twine upload` can hit a stale/slow
PyPI Simple-index CDN edge and either install a cached older wheel or
race the index update, producing failures that clear once the CDN
catches up (typically within a few minutes). No code change needed.
Lesson already applied in `scripts/verify_live/`, but the platform's
own check runner doesn't use `--no-cache-dir`/retry the way that
script does — worth remembering that a check firing immediately after
a publish can be a false negative on propagation lag alone, not a
regression signal.

## 2026-09-03 (Rafael Okonkwo, Build) — 0.2.23: fixed silent data loss on a client used as a passive sync hub

Dov's independent Red Team pass found a real, deterministic data-loss
bug in sync's self-heal step (see
`redteam_verify0222_indep/findings/2026-09-03-sync-0.2.22-pass.md`): a
client used as a passive remote for someone else's `cadence sync
--remote <this>` gets that peer's task written straight into its own
git tree without its own sqlite ever learning about it. The next time
this client ran its own sync against a third, unrelated peer,
self-heal treated "not in my own sqlite" as drift and deleted the
file — permanently, with zero conflict, warning, or nonzero exit, even
though the CLI's own text says "Nothing was lost or overwritten." Any
star/hub sync topology hits this, not just a contrived ordering.

Fix (`src/cadence/store.py`, `_absorb_orphan_task_files`): before
self-heal runs, any on-disk task file whose id is unknown to this
client's own sqlite AND whose origin isn't already accounted for by
this sync call's own peer or this client's own known tasks gets
absorbed into a real sqlite row first, at its own id. It becomes
genuinely known content — diffed and pushed like any other row —
instead of drift to erase. An origin the peer already reports is left
to the ordinary pull branch, which adopts it correctly with a real
`pulled` count; absorbing it twice would hide a real pull as a no-op.

Added `test_sync_passive_relay_task_survives_hosts_own_later_sync`
(tests/test_r08_verbs.py), the exact A2/X2/C2 sequence from the
findings doc. Confirmed it fails on pre-fix `store.py` (stashed the
fix, reran: `AssertionError: X2's passively-relayed task was silently
purged by A2's own self-heal: ["A2's own task", "C2's own task"]`) and
passes after. Full suite: 152 passed (was 151; +1 new test), 0 failed.

Published `cadence-todo==0.2.23` to PyPI via the existing
version-bump-on-push CI workflow (commit a8c0680, GitHub Actions run
33752412915, conclusion success). Verified against the live wheel, not
local install — fresh venv, `pip install --no-cache-dir cadence-todo`,
confirmed `importlib.metadata.version('cadence-todo') == '0.2.23'`,
then ran the task's exact success_test command end to end:

```
$ CADENCE_DB_PATH=a2/db.sqlite cadence list
  [ ]    1   A2's own task
  [ ]    2   X2's own task
  [ ]    3   C2's own task
```

X2's task present, exit 0. Note for next time: the first install
attempt right after publish landed 0.2.22 (JSON API already showed
0.2.23, but `pip install` still resolved the old wheel) — same
Simple-index CDN propagation lag as 0.2.19/0.2.20/0.2.22 before it.
Waiting ~2 minutes and retrying with `--no-cache-dir` got a clean
0.2.23 install. Not a regression, just the same known lag; this is now
the fourth time it's shown up, worth building the retry directly into
whatever eventually re-runs the ten-step finish-line script rather
than re-diagnosing it by hand each time.

## 2026-09-03 (Dov Ferreira, Red Team) — independent pass on 0.2.23: the passive-relay fix has a second, unguarded hole

Verified 0.2.23 installs clean (6th independent install across three
engineers/environments today, all clean; PyPI CDN lag is fully settled
by now). The original A2/X2/C2 sequence from the findings doc — a
task orphaned on a passive relay survives that relay's own later sync
with a third client — passes exactly as Rafael's transcript and test
claim. That part of the fix holds.

But `_absorb_orphan_task_files` only runs inside `_sync_diff_and_apply`,
i.e. only when the relay client itself runs `cadence sync`. It does
nothing to protect an orphan file from the relay's own plain, everyday
`cadence add` (or any other id-allocating write — `decompose` almost
certainly shares the same code path, not independently tested here) run
*before* that next sync. `Store.add()` allocates its new id purely from
sqlite's own autoincrement and writes `tasks/<id>.json` via
`_snapshot_and_commit`, with zero awareness of what's already sitting in
`hist.tasks_dir` on disk. If that id happens to match an orphan file's
id — which it will, on a freshly-relaying store, since sqlite is empty
and hands out id=1 first — the add silently overwrites the orphan file.
No error, no warning, no conflict, exit 0. This is exactly the failure
mode the fix's own docstring names ("or letting a later alloc() hand out
its on-disk id to some unrelated origin and overwrite it — silently
destroys the only copy of a task nobody told this client to forget"),
just at a different call site than the one 0.2.23 closed.

Repro, real published 0.2.23 wheel, fresh venv (`/workspace/sync_repro_0903/venv`,
upgraded in place with `pip install --upgrade cadence-todo==0.2.23`):

```
$ export CADENCE_DB_PATH=A/a.db
$ cadence add "task on A, pre-relay"          # A's task, id=1, origin o1
$ cadence sync --remote X/x.db                # X has never synced before;
                                               # its .db.history is init'd first with
                                               # a bare `cadence sync` (fails "no remote
                                               # configured" but creates the git history)
Synced with origin: pulled 0, pushed 1. Up to date.
$ CADENCE_DB_PATH=X/x.db cadence list
No tasks yet.                                  # confirms: X's sqlite has no row
$ find X -iname '*.json'
X/x.db.history/tasks/1.json                     # orphan file: A's task, id=1, on disk only

$ export CADENCE_DB_PATH=X/x.db
$ cadence add "task native to X"                # ordinary local add, NOT a sync call
Added #1: task native to X                      # X's sqlite was empty -> allocates id=1
$ cadence sync --remote C/c.db                  # trigger self-heal/absorb on X
Synced with origin: pulled 0, pushed 1. Up to date.
$ cadence list
  [ ]    1   task native to X                   # only 1 task. A's task is gone.
$ cat X/x.db.history/tasks/1.json
{ "title": "task native to X", "origin": "9be514d6...", ... }   # file 1.json now holds
                                                                   # X's task; A's task's
                                                                   # content is not
                                                                   # anywhere on X anymore
```

The overwrite happens at the `cadence add` line, before any sync or
self-heal runs at all — the 0.2.23 fix never gets invoked on this path.
In this run the loss was not permanent project-wide only because A
still held its own copy and happened to sync with X again afterward
(that second sync resolved cleanly via the id-collision/renumber path,
landing A's task back on X as id=2 — that part works correctly and is
a separate, healthy code path). But nothing in the design guarantees a
second sync happens, or that the original owner still has their copy —
a relay that is someone's only path to a task (e.g. the origin device
was wiped after the first push) loses that task for good, silently,
with no exit-code or message signal anything happened.

Root cause: `Store.add()` (`src/cadence/store.py` ~line 479) allocates
ids from sqlite alone and `_snapshot_and_commit` writes
`tasks/<id>.json` unconditionally, with no check against
`hist.tasks_dir` for a same-id file sqlite doesn't know about. Fix
would need either (a) `add`'s id allocation to also skip ids with an
existing on-disk file sqlite hasn't absorbed, or (b) run
`_absorb_orphan_task_files` (or an equivalent scan) before any local
id allocation, not just inside sync. `decompose`'s subtask creation
should be checked for the same pattern before calling this closed —
not tested in this pass.

Severity: high. Silent, undetectable data loss on completely ordinary
use (add a task on a client that has ever been the target of someone
else's `--remote`), no adversarial input needed, no error surfaced —
the exact class of bug the 0.2.23 fix set out to close, just not
closed all the way. This is the one I'd fix first if only one thing
from today's pass gets picked up.

What held: the original A2/X2/C2 scenario (relay survives its OWN next
sync without an intervening local add) — clean, confirmed independently
again. The id-collision renumber path on a second sync (`"#1 was
independently created on both clients ... kept #1 as this client's
version and gave the other client's task a new id, #2. Nothing was lost
or overwritten"`) — also clean, and its own message is accurate for
that case, in contrast to the misleading identical message the original
finding cited against 0.2.22.

Not tested this pass: `decompose` sharing the same alloc pattern;
concurrent relay writes (two peers pushing to the same passive relay
between its syncs); whether a corrupted/malformed orphan file on disk
(present but unreadable JSON) is silently dropped by
`_absorb_orphan_task_files`'s `except (OSError, ValueError): continue`
in a way that also loses data with no signal — plausible from reading
the code, not independently reproduced.

## 2026-09-03 (Rafael Okonkwo, Build) — 0.2.24: fixed local add/decompose overwriting an orphan task file; found the task's own success_test can never pass

Fixed the bug from the entry above. `add()` and `decompose()`
(`src/cadence/store.py`) both allocated their new row's id from
sqlite's own AUTOINCREMENT counter alone, with no check against
`hist.tasks_dir` for a same-id file sqlite hasn't absorbed yet. On a
client that has only ever been a passive sync relay, that counter
starts empty and hands out id=1 first, colliding with an on-disk
orphan and silently overwriting it via `_snapshot_and_commit` -- no
sync involved, no error, no exit code signal. `decompose`'s subtask
id allocation is the identical pattern and had the identical bug
(confirmed, not just suspected -- see test below).

Fix: both now call `_absorb_orphan_task_files` (the helper 0.2.23
already introduced for the sync path) before opening the connection
that allocates new ids. Absorbing an orphan inserts it into sqlite
with its own explicit id, which advances sqlite's AUTOINCREMENT
high-water mark past it, so the next plain INSERT can never reuse it.

Added `test_local_add_on_passive_relay_does_not_overwrite_orphan_task_file`
and `test_decompose_on_passive_relay_does_not_overwrite_orphan_task_file`
(tests/test_r08_verbs.py). Confirmed both fail on pre-fix `store.py`
(checked out the pre-fix file, reran:
`AssertionError: X's decompose silently overwrote A's
passively-relayed orphan task file: ["X's parent task", 'sub one', 'sub
two']`, same shape for the `add` test) and pass after. Full suite: 154
passed (was 152; +2 new tests), 0 failed.

Published `cadence-todo==0.2.24` to PyPI via the version-bump-on-push
workflow (commit 752eddc, GitHub Actions runs 33763233790 (Publish)
and 33763233712 (CI), both conclusion success, including the
`pypi-install-and-drive` job that installs the just-published wheel
into a clean venv and drives it). PyPI JSON API confirms
`cadence-todo/0.2.24` live with a wheel file.

Independently re-verified against the live wheel with a CLI-driven
repro of Dov's exact scenario, in a fresh venv (`pip install
--no-cache-dir --upgrade cadence-todo`, one retry needed for the
now-familiar Simple-index CDN lag, landed 0.2.24 at attempt 1 of the
retry loop):

```
$ CADENCE_DB_PATH=X/x.db cadence sync            # prime X: first-ever touch
                                                  # creates x.db and (failing
                                                  # "no remote configured") is
                                                  # a harmless no-op otherwise
$ export CADENCE_DB_PATH=A/a.db
$ cadence add "task from A"
$ cadence sync --remote X/x.db
Synced with origin: pulled 0, pushed 1. Up to date.
$ export CADENCE_DB_PATH=X/x.db
$ cadence add "task native to X"
Added #2: task native to X                       # id 2, not 1 -- A's task at
                                                   # id 1 was absorbed first
$ cadence list
  [ ]    1   task from A
  [ ]    2   task native to X
```

Both tasks present, distinct ids, exit 0. Ran the identical script
against `cadence-todo==0.2.23` in a separate fresh venv as a control:
`Added #1: task native to X` (collision), final list shows only `task
native to X` -- confirms the fix, not an environment artifact.

**Finding: this task's own `success_test` command can never pass, on
any version, fixed or not.** It syncs `A -> --remote X/x.db` without
ever first touching `X/x.db` (no `mkdir`, no prior `cadence` call
under `CADENCE_DB_PATH=X/x.db`). `Store._maybe_init_peer_history`
only bootstraps a peer's git history when the peer's `.db` file
already exists on disk (`if not p.exists(): return` --
deliberately narrow, R-08 re-verify Finding D); a `.db` file is only
ever created by *constructing a `Store`* against that path (its
`__init__` runs `CREATE TABLE IF NOT EXISTS` unconditionally), which
the CLI only does when a command actually runs with that
`CADENCE_DB_PATH`. Nothing in the given script does that for X before
A's `sync --remote X/x.db`, so that line always exits 1 ("no Cadence
store found at ...", under the script's own `set -e`) before the
add-on-X / orphan-overwrite check it exists to test is ever reached.
Verified directly: ran the task's literal repro fragment verbatim,
`echo $?` was `1`, on both 0.2.23 and 0.2.24 identically -- the
failure is unrelated to the fix. Adding one priming line
(`CADENCE_DB_PATH=X/x.db cadence sync`, matching the "bare `cadence
sync` first" step the original 0.2.23 finding's own repro already
used) is the only change needed, and with it the script passes on
0.2.24 and correctly fails on 0.2.23 above. Flagging this to the CEO
rather than resubmitting the same evidence against a checker that
cannot pass by construction.

---

## 2026-09-03 (Dov Ferreira, Red Team) — independent pass on 0.2.24: orphan-overwrite fix holds for the reported case, three new orphan shapes reopen it

Fresh venv, `pip install cadence-todo==0.2.24`, no local repo on path.

Re-ran the exact A→X passive-relay scenario from the 0.2.23 finding
against both `add()` and `decompose()`: both hold now. `X`'s local
`add` after receiving A's orphaned push gets id=2, not a colliding 1;
`decompose()` on a relay client absorbs a pending orphan before
allocating subtask ids, same result. Fix does what it says for the
reported case.

Went looking for orphan file shapes the fix's absorb helper does not
handle, since `_absorb_orphan_task_files` only absorbs a file it can
both parse as JSON *and* find a real `origin` key in — anything else
just `continue`s past it without reserving its id. Found three:

1. **Truncated/unparseable JSON orphan → silently overwritten by the
   next `add`, exit 0.** `history.write_task_file` uses plain
   `write_text`, no atomic temp+rename (`history.py:131`), so a
   process killed mid-write leaves exactly this shape on disk — no
   relay, no second client, no adversarial input needed, just an
   ordinary crash during any mutating command followed by one more
   `add`. Repro: seed a store with one task, hand-truncate a
   `tasks/2.json` to simulate the interrupted write, `cadence add`
   again — the truncated file is silently replaced, `echo $?` is `0`,
   nothing warns that anything was lost. Worst finding in this pass:
   fully ordinary, silent, real data gone.
2. **Well-formed JSON task file with no `origin` key → same silent
   overwrite, exit 0.** Same absorb loop, different guard
   (`if not origin: continue`). Narrower trigger (needs a hand-placed
   or pre-`origin`-schema file) but identical silent-loss shape.
3. **Valid JSON that isn't an object (e.g. a bare JSON array) →
   uncaught `AttributeError` on `add`/`decompose`, and both stay
   permanently broken on that store afterward.** `data.get("origin")`
   assumes `data` is a dict; `json.loads` doesn't guarantee that.
   Repro: `echo '[1,2,3]' > .../tasks/2.json`, then `cadence add` →
   `Error: something went wrong on Cadence's end (AttributeError:
   'list' object has no attribute 'get'). Run 'cadence list' to check
   your tasks, or check CADENCE_DB_PATH.` `cadence list` still works
   and shows nothing wrong (list() never calls the absorb helper), so
   that suggested next step is a dead end; every subsequent `add` or
   `decompose` on that store fails identically, no data lost but two
   core commands wedged with no diagnosis. The same exception is
   reachable through `sync` (shares the helper) but `sync`'s call site
   catches it into an honest "nothing changed" message — whose hint
   text ("Check that CADENCE_DB_PATH for every client is a distinct
   path...") is nonetheless wrong for this cause.

Full transcripts, exact commands and outputs for all three, plus what
held (multi-orphan absorption, ordinary sequencing, both original
relay cases) in
/workspace/redteam_0224_indep/findings/2026-09-03-0224-orphan-absorb-gap.md.

Root cause is one thing, not three: the absorb loop's `continue` on an
unparseable/wrong-shape/no-origin file doesn't reserve that file's id
anywhere, so it's still "free" as far as the next INSERT is concerned.
Fixing the `continue` to still register the id (or having the id
allocator check for *any* `tasks/<id>.json` on disk regardless of
whether its contents were understood) closes all three at once rather
than needing a new special case per bad-file shape. Separately, #3's
`AttributeError` needs an actual `except` and taxonomy-shaped error
message the way `sync`'s does, and that `sync` hint needs to stop
misdiagnosing the cause. Not filing a task for this myself — flagging
to Build/CEO; if picked up, worth an independent re-pass on
`_absorb_orphan_task_files` specifically once fixed, same as this one.

Severity, worst first: #1 (silent, ordinary, ships today) > #2 (silent,
narrower trigger) > #3 (loud, no data lost, but wedges two core
commands with a misleading hint). Fix #1 first if only one gets fixed.

---

## 2026-09-03 (Rafael Okonkwo, Build) — 0.2.25: fix all three orphan-absorb shapes Dov found

Fix, as scoped: `_absorb_orphan_task_files` (store.py) now calls a new
`_reserve_orphan_ids` first, before touching any file's contents. It
scans `tasks/*.json` for every filename that parses as an int and
bumps sqlite's own `sqlite_sequence` row for `tasks` up to that
highest id — no fake row inserted, just the AUTOINCREMENT high-water
mark moved past it — so a later plain `add()`/`decompose()` INSERT can
never be handed an id a file already occupies on disk, no matter
whether that file's contents can be parsed or understood. This closes
shape #1 (truncated/unparseable write) and shape #2 (well-formed
object, no `origin` key) with the one change, exactly as Dov's finding
predicted it would. Separately, the absorb loop gained an
`isinstance(data, dict)` guard right after the JSON parse, so shape #3
(valid JSON that isn't an object) no longer raises an uncaught
`AttributeError` out of `add`/`decompose` — it's skipped like any
other file the loop can't turn into a row, id already reserved by the
step above, no wedge. `sync`'s catch-all hint text no longer names a
shared `CADENCE_DB_PATH` as the presumed cause; it now points at
inspecting the history store's task files instead, since that
generic handler catches ANY internal inconsistency, not only the one
CADENCE_DB_PATH-collision case it used to assume.

Added 7 regression tests to test_r08_verbs.py: the three shapes x
both `add()` and `decompose()` (parametrized, 6 tests), plus one for
`sync`'s corrected hint text (asserts the old "distinct path ending in
'.db'" wording is gone). Full suite: 161 passed (154 pre-existing +
7 new), `python -m pytest tests/ -q`.

Published 0.2.25 to PyPI: https://pypi.org/project/cadence-todo/0.2.25/
Verified against the live published wheel, not local source — fresh
venv, `pip install -U cadence-todo` (needed one propagation-delay
retry, `0.2.24` on the first poll then `0.2.25` ~20s later, same PyPI
lag noted in the 0.2.24 entry, not a regression), then re-ran all
three of Dov's exact repro cases against the installed CLI: truncated
orphan untouched by the next `add`, no-origin orphan untouched, and
the non-object-JSON store survived two `add`s in a row (`Added #3`,
`Added #4`) instead of wedging.

Not independently re-verified by Red Team yet — flagging for a pass
per Dov's own note ("worth an independent re-pass on
`_absorb_orphan_task_files` specifically once fixed").

## 2026-09-04 (Dov Ferreira, Red Team) — independent pass on 0.2.25: 3 orphan-absorb shapes confirmed fixed; new unreadable-file finding

Fresh venv, `pip install cadence-todo==0.2.25` from real PyPI, no local repo
on path. Fix commit 6a1cd8a.

**Part A — the 3 reported shapes, re-verified fixed.** Re-ran each exact
repro: truncated/unparseable JSON orphan, valid-JSON-no-`origin` orphan, and
valid-JSON-non-object orphan. All three now correctly reserve their on-disk
id — next `add`/`decompose` skips past it instead of overwriting or
crashing, files on disk left untouched. Pushed further than the original
repro: 4 simultaneous bad-shape orphans (ids 2/3/4/5) in one store all
reserved correctly in one pass, next two adds land on 6 and 7 in order; a
0-byte orphan file reserved correctly; the happy-path real passive-relay
absorb (genuine `origin`-tagged file) still works, unbroken by the
id-reservation change. Clean pass, no regressions.

**Part B — new finding, not one of the 3 reported shapes.** A 4th orphan
shape — a file that *exists but can't be read* (`chmod 000`, e.g. a
restrictive shared volume or ACL mismatch, not an ordinary crash) — is
handled two different, inconsistent ways depending on incidental prior
operation history, and one of those ways is silently destructive:

- Sync alone, nothing else pending: fails honestly (`Error: sync hit an
  internal inconsistency reading history data (PermissionError...).
  Nothing was changed.`), file survives on disk. Good.
- Sync after one or more local `add`/`decompose` calls already warned about
  the same file (`Warning: ... its history entry failed to record (git
  add -A failed: ... Permission denied ...)`): the next `sync` call
  succeeds cleanly (`Synced with origin: pulled 1, pushed 3. Up to date.`)
  and the unreadable file is silently gone afterward — `ls` on it returns
  "No such file or directory," nothing in the CLI output or the git commit
  log ever mentions a file was removed, and "Nothing was lost or
  overwritten" (printed on an unrelated independently-created-task note the
  same call) is not true of what just happened to tasks/2.json.

Confirmed on both `add` and `decompose` (identical warning shape). Reading,
not fixing: the id-reservation fix (glob for existing ids, don't require
reading them) correctly stops `add`/`decompose` from overwriting this file
— that holds. It doesn't stop `sync`'s self-heal rewrite step from later
unlinking the same file once self-heal has other pending writes to do
anyway (unlink only needs write permission on the parent directory, not
read permission on the file).

Severity: lower than the 3 original shapes — this needs a file to become
unreadable, not just malformed, which is hostile-but-plausible rather than
"any process that dies mid-write." Real consequence: a file an operator
could have recovered by fixing its permissions is instead gone with zero
trace, the moment any sync happens to have other work pending. Fix I'd
want, ranked: (1) make self-heal treat an unreadable file the same safe way
plain sync-with-nothing-else-pending does — skip and leave in place, or
abort honestly, never silently unlink; (2) if discarding an unreadable file
is ever intentional, say so in the sync output instead of under a message
that says the opposite.

Full transcripts and root-cause notes:
/workspace/redteam_0225_indep/findings/2026-09-04-0225-orphan-absorb-3-shapes-verified-plus-unreadable-file.md
(workspace-local, not committed — repro commands are all in this entry).
Not fixed by me. Worst-first if only one gets picked up: this Part B
finding, since it's the only open item — Part A is closed.

## 2026-09-04 (Rafael Okonkwo, Build) — 0.2.26: fix sync self-heal silently deleting an unreadable orphan task file

Fix, as scoped: `sync()`'s self-heal rewrite step used to treat any
on-disk task id absent from sqlite as stale drift and unlink it
unconditionally — `remove_task_file` only needs write permission on
the parent directory, never read permission on the file itself, so a
file that exists but can't be read (chmod 000 / restrictive ACL, one
`_absorb_orphan_task_files` had already correctly left alone) got
silently erased the moment self-heal had any other genuine pull/push
work to do in the same sync call. A plain sync with nothing else
pending already failed honestly and left the file in place; the two
paths now agree. Self-heal reads each stale candidate first: an
`OSError` there means "unknown," not "stale" — the file stays on
disk and a warning naming it and the underlying error is added to
`sync()`'s result (`warnings: [str, ...]`) instead of silence. CLI and
MCP both print these warnings. `advance_local()` gained an `exclude`
param so the self-heal commit's `git add -A` can skip that same
unreadable path by pathspec instead of the whole commit tripping over
one file it can't hash.

Added 1 regression test (`test_r08_verbs.py`,
`test_sync_self_heal_never_deletes_unreadable_orphan_task_file`),
same Store-API-direct style as the two orphan tests just above it:
chmod 000 an orphan file, sync against a genuinely different peer
with real push work pending so self-heal actually runs, assert the
file survives and a warning names it. Full suite: 162 passed (161
pre-existing + 1 new), `python -m pytest tests/ -q`.

Published 0.2.26 to PyPI: https://pypi.org/project/cadence-todo/0.2.26/
Verified against the live published wheel, not local source — fresh
venv (run from outside the repo, to avoid shadowing by the local
`cadence/` source dir), `pip install -U cadence-todo` (one
propagation-delay retry, same CDN-lag pattern noted since 0.2.19).
Re-ran Dov's exact repro against the installed CLI with two real,
separate `CADENCE_DB_PATH` clients: client A gets its own task plus a
`chmod 000` orphan file at id 2 (never absorbed, matching the
finding), client B is a genuinely empty peer so A's sync has real push
work and self-heal actually runs. Result: `Warning: #2: on-disk task
file '.../tasks/2.json' could not be read (Permission denied) so it
was left in place instead of being treated as stale drift. ... fix its
permissions and sync again ...` printed, then `Synced with origin:
pulled 0, pushed 1. Up to date.` — no more silent "Nothing was lost or
overwritten" alongside a real loss. `ls` on the file afterward: still
present, still `----------` permissions, untouched.

One thing worth a note for whoever picks up sync work next, not itself
a bug in this fix: while building this repro I first tried a peer that
also had its own task, which let a genuinely *new pulled* task get
allocated the exact same numeric id as the pre-existing unreadable
file (id space is client-local and reused, not reserved for on-disk
orphans regardless of readability) — self-heal correctly tried to
*write* the newly pulled task to that filename, hit the same
`PermissionError` writing this time rather than reading, and surfaced
it honestly via the existing catch-all (`sync hit an internal
inconsistency ... Nothing was changed`), no data lost either way. Not
the shape Dov reported or this fix's success test, and not a
regression — flagging only because it's the same underlying fact
(chmod 000 files aren't fully invisible to id allocation) surfacing a
second way; not filing a task for it absent a concrete failure mode
that loses something.

Not independently re-verified by Red Team yet.

## 2026-09-04 (Dov Ferreira, Red Team) — independent pass on 0.2.26: targeted fix holds; new CRITICAL finding on sync-then-undo

Fresh venv, `pip install cadence-todo==0.2.26`, run from outside the repo, no
local source on path. Fix commit: a750283.

**Part A — the reported fix (self-heal warn-and-leave-in-place): holds.** Re-ran
the exact 0.2.25 repro (two real `CADENCE_DB_PATH` clients, one carrying a
`chmod 000` orphan task file, peer with genuine push work so self-heal actually
runs, not the early-return-nothing-pending path). CLI:

```
Warning: #2: on-disk task file 'P4/p.db.history/tasks/2.json' could not be read
(Permission denied) so it was left in place instead of being treated as stale
drift. It is NOT tracked by this client -- fix its permissions and sync again
to have it either absorbed or cleaned up.
Note: #1 was independently created on both clients ... Nothing was lost or
overwritten.
Synced with origin: pulled 1, pushed 3. Up to date.
exit=0
```
File survived on disk afterward. The "Nothing was lost or overwritten" note is
now scoped to a genuinely different, correctly-resolved event (an independent
#1-vs-#1 id collision) and makes no claim about the unreadable file, which gets
its own separate honest warning. Confirmed same warning shape via `decompose`
+ sync, and via the MCP path (`Store().sync()`'s returned `warnings: [...]`
list carries the identical text). Regression check: plain sync with nothing
else pending still aborts honestly (`Nothing was changed`, exit=1) and leaves
the file untouched, same as before. Part A is a clean pass, closed.

**Part B — NEW finding, CRITICAL, not one of the shapes I was asked to check:**
sync's honest-looking abort silently mutates local sqlite state, and the
single most natural recovery step (`undo`) then destroys a real task while
itself claiming failure. Trigger: an ordinary case where a peer's own task
lands, on pull, at the same numeric id as the local unreadable orphan file (ids
are assigned per-client and are not reserved across peers for an id that only
exists as an on-disk, unabsorbed orphan — routine once two clients have synced
a few times).

Clean, single-attempt repro, every command run exactly once:
```
$ CADENCE_DB_PATH=RY/p.db cadence add "ry task1"        # Added #1
$ echo '{"bad":true}' > RY/p.db.history/tasks/2.json && chmod 000 RY/p.db.history/tasks/2.json
$ CADENCE_DB_PATH=RY2/q.db cadence add "ry2 taskA"       # Added #1 (own client)
$ CADENCE_DB_PATH=RY2/q.db cadence add "ry2 taskB"       # Added #2 (own client)
$ CADENCE_DB_PATH=RY/p.db cadence list
  [ ]    1   ry task1

$ CADENCE_DB_PATH=RY/p.db cadence sync --remote RY2/q.db
Error: sync hit an internal inconsistency reading history data (PermissionError:
... 'RY/p.db.history/tasks/2.json'). Nothing was changed. ...
exit=1

$ CADENCE_DB_PATH=RY/p.db cadence list
  [ ]    1   ry task1
  [ ]    2   ry2 taskB
  [ ]    3   ry2 taskA
```
"Nothing was changed" is false: two peer tasks were pulled straight into local
sqlite on a call that reported failure. Then the natural next move:
```
$ CADENCE_DB_PATH=RY/p.db cadence undo
Error: something went wrong on Cadence's end (HistoryError: git add -A failed:
... open("tasks/2.json"): Permission denied ...). Run 'cadence list' to check
your tasks, or check CADENCE_DB_PATH.
exit=2

$ CADENCE_DB_PATH=RY/p.db cadence list
  [ ]    2   ry2 taskB
  [ ]    3   ry2 taskA
```
Task #1 ("ry task1") — the one real task this client had before any of this,
created cleanly with no warning — is gone (`cadence why 1` → "no task with id
1"; sqlite table only has ids 2, 3; `1.json` removed from disk). `undo`
reported an error implying nothing happened; the actual effect was to delete
the client's own real task, while the two erroneously-pulled, never-committed
tasks (2, 3) stay behind, permanently disconnected from git history (`git log`
on the history repo still shows only `Added #1: ry task1` — it doesn't even
match sqlite anymore).

Root cause: `sync()`'s pull step commits to local sqlite (`conn.commit()`)
*before* the self-heal file-rewrite step runs; when self-heal's write phase
hits the unreadable path (a genuinely new id landing on that filename, not the
read-first check Part A's fix added), the `OSError` is caught by the generic
handler at the bottom of `sync()` and reported as "Nothing was changed" with no
rollback of the sqlite commit that already happened. `undo` has the same
shape: it mutates sqlite first, then tries to record the reversion via
`git add -A`, and when that fails on the same unreadable file, the sqlite
mutation isn't rolled back and the command surfaces a generic error while
having already done something. Same root defect as the one 0.2.26 fixed
(an unreadable on-disk file breaking the assumption that "no file error" ==
"safe to touch sqlite"), on two paths the fix didn't touch.

Severity: highest in this series. Trigger conditions are ordinary — no
malice, no exotic timing, just one unreadable file plus a peer whose next task
happens to land at that id. The exact sequence a person or agent would take in
good faith (see an error, run `undo` to get back to safety) is the one that
causes real, silent, permanent data loss, and both error messages actively
mislead about what already happened. Ranked fix: (1) make `sync`'s sqlite
commit conditional on self-heal succeeding, or wrap both in one transaction;
(2) same for `undo` — don't commit the sqlite-side reversion until the
matching history write succeeds; (3) until then, stop the two error paths from
claiming "nothing changed" when a rollback isn't actually guaranteed.

Full transcript: /workspace/redteam_0226_indep/findings/2026-09-04-0226-fix-holds-plus-sync-then-undo-dataloss.md
(workspace-local, not committed — repro commands are all in this entry).
Not fixed by me. Worst-first if only one gets picked up: Part B — real data
loss on a plain sync-then-undo sequence, not an edge case needing malice.

## 2026-09-04 (Rafael Okonkwo, Build) — 0.2.27: fix sync/undo committing sqlite before the matching git write, no rollback

Fixed Dov's 0226 Part B finding above: the worst bug in the series. Both
`sync()`'s pull-apply step and `undo()` called `conn.commit()` on local
sqlite before the matching git history write, with no rollback if that
write failed — so a git-side failure (an unrelated unreadable file
breaking `git add -A`, or a pulled task colliding with an unreadable
orphan's id) left sqlite permanently out of sync with history while the
error text claimed "Nothing was changed" / "something went wrong."

Fix: both now hold the sqlite connection open in one transaction across
the matching history write and only call `conn.commit()` once that write
has actually succeeded; any exception anywhere in the block rolls sqlite
back (`conn.rollback()`) before the error is raised, so the claim in the
hint text is now literally true instead of an assumption. Added a new
`UndoFailed` error class for undo's history-write failure path. `list()`
takes an optional `_conn` so a caller mid-transaction (sync's self-heal
step) reads its own pending writes instead of a fresh connection that
would only ever see the last *committed* state.

Two regression tests added (`tests/test_r08_verbs.py`,
`test_sync_git_write_failure_leaves_local_sqlite_untouched` and
`test_undo_git_write_failure_leaves_sqlite_task_intact`), confirmed to
fail against pre-fix `store.py` and pass against the fix. Full suite:
164 passed.

Re-ran Dov's exact repro from his 0226 finding against the fix, both
locally and against the freshly `pip install`-ed 0.2.27 wheel in a clean
venv outside the repo:

```
$ CADENCE_DB_PATH=RY/p.db cadence sync --remote RY2/q.db
Error: sync hit an internal inconsistency reading history data
(PermissionError: ... tasks/2.json). Nothing was changed. ...  exit=1
$ CADENCE_DB_PATH=RY/p.db cadence list
  [ ]    1   ry task1        <- still here, exactly as before the sync

$ CADENCE_DB_PATH=RY/p.db cadence undo
Error: undo's history entry failed to record (HistoryError: git add -A
failed ... tasks/2.json Permission denied ...). Nothing was changed --
the task list is exactly what it was before this undo. Run 'cadence
list' to confirm, or file a bug.  exit=1
$ CADENCE_DB_PATH=RY/p.db cadence list
  [ ]    1   ry task1        <- still here after undo too
```

Task #1 survives both the failed sync and the failed undo; sqlite and
git history stay in agreement; the error text's claim of "nothing
changed" is now true rather than a guess. Published to PyPI
(https://pypi.org/project/cadence-todo/0.2.27/), pushed to origin/main
at 19dab34.

## 2026-09-04: independent Red Team re-verify of 0.2.27 (sync/undo commit-order fix)

Second, independent confirmation of Rafael's fix above, from a separate
environment (`/workspace/redteam_0227_indep`, fresh venv, `pip install
cadence-todo==0.2.27`, no local repo on path, no board task open — going
straight to the log per usual). Re-ran my exact 0226 repro (client `RY`
with 1 task + a `chmod 000` orphan at id 2; peer `RY2` with 2 tasks) and
pushed past it with three checks Rafael's own transcript did not cover.

**Original repro, re-confirmed:**
```
$ CADENCE_DB_PATH=RY/p.db cadence sync --remote RY2/q.db
Error: sync hit an internal inconsistency ... Nothing was changed. ... exit=1
$ CADENCE_DB_PATH=RY/p.db cadence list
  [ ]    1   ry task1                     <- unchanged
$ CADENCE_DB_PATH=RY/p.db cadence undo
Error: undo's history entry failed to record ... Nothing was changed --
the task list is exactly what it was before this undo. ... exit=1
$ CADENCE_DB_PATH=RY/p.db cadence list
  [ ]    1   ry task1                     <- unchanged
$ git -C RY/p.db.history log --oneline
75a0143 Added #1: ry task1
5e4545c init: empty task store            <- no phantom commits either
```
Matches Rafael's transcript exactly. Confirms the fix independently, not
just re-trusting his run.

**1. MCP path** (`Store().sync()` called directly, the same call
`mcp_server.sync_tasks` makes) — the original finding specifically called
out that an agent driving this via MCP would see `{"ok": false}` and have
no way to know local state had mutated. Now it raises `SyncInconsistent`
cleanly and `cadence list` afterward shows only the pre-existing task —
no silent mutation on the programmatic path either:
```
SYNC EXC TYPE: SyncInconsistent
SYNC EXC MSG: sync hit an internal inconsistency reading history data
(PermissionError: [Errno 13] Permission denied: '.../tasks/2.json')
LIST AFTER FAILED MCP-PATH SYNC:
  [ ]    1   my task1
```

**2. Idempotency under repeated failure** — ran the same failing sync
twice more, then the same failing undo twice more, same broken file left
in place throughout. Every attempt produced the identical honest error
and left the identical unchanged state; no accumulating drift or
corruption across repeated failed attempts.

**3. Recovery and happy-path regression** — `chmod 644`'d the file back
and reran sync: it now succeeds, pulls the 2 peer tasks, resolves the
#1 independent-creation collision correctly (renumbers to #3), and both
sqlite and git history agree afterward (`git log` shows a real sync
commit this time, none of the earlier failed attempts left phantom
commits). Separately, a plain two-client sync with no unreadable files
at all (no orphan, no permission issue) still completes and merges
correctly, and `undo` right after still behaves as before ("no mutation
to undo yet" — sync itself isn't own-client-undoable, unchanged from
prior versions, not a new gap).

**Verdict: 0.2.27's fix holds under independent re-verification.** No
new defects found on this pass. This closes out the sync-then-undo
CRITICAL finding from 0226 (msg 2026-09-04T01:06 UTC) — nothing further
needed on this specific bug. Noor's planned wording follow-up
(human-surface.md §4.4) is worth checking against the *current* text
before writing new copy: the undo error message observed here already
reads "Nothing was changed -- the task list is exactly what it was
before this undo. Run 'cadence list' to confirm, or file a bug," which
already states what happened rather than reassuring — that part may
already be done as a side effect of the transactional fix, worth a
quick diff read rather than assuming the old wording is still there.

## 2026-09-04: sync/undo/internal-error wording — spec for the safety-class marker (human-surface.md §4.4)

Rafael's re-verify note above was right that undo's and sync's hints
already read "Nothing was changed" truthfully, not just reassuringly —
that part didn't need touching. What still needed touching: a reader
seeing only the third message in this family, cli.py's last-resort
`except Exception` net (or its MCP twin, `_err_unexpected`), has no way
to tell whether they're looking at the same guaranteed-safe class as
undo/sync or something worse — the text is silent on it either way. I
checked, and the answer is genuinely different for the third message:
it catches an uncaught exception from *any* command at *any* point, not
just undo/sync's git-history step, so unlike the other two it cannot
promise a rollback happened. That's not a wording gap that invents new
behavior — it's a gap where the code already knows something the
message doesn't say.

**Before → after, the four sites:**

Guaranteed-safe class (`store.py`'s `UndoFailed`/`SyncInconsistent`
hints, both already wrapped in an explicit `rollback()` before they're
raised): prefixed with `Rolled back automatically:` — a marker phrase,
not new content, so it reads the same as a category label the moment an
agent has seen it once.

Unconfirmed class (`cli.py`'s generic handler, `mcp_server.py`'s
`_err_unexpected`): now says outright `Unlike a failed sync or undo,
this is not guaranteed to have rolled back` — naming the contrast
instead of leaving it to be inferred (or missed) by a reader who has
only ever seen one of the four messages.

Full before/after text for all four sites is in
`docs/human-surface.md` §4.4 ("Internal errors split into two safety
classes"), written verbatim against the current shipped strings in
`store.py` (~L960, ~L1095) and `cli.py` (~L964)/`mcp_server.py` (~L272).

**Why this reads more clearly:** before, "nothing changed" (undo/sync)
and "run list to check" (the generic net) looked like two different
registers of the same uncertainty — softer versus vaguer wording for
what could have been the identical situation. An agent that has only
ever seen the generic net's message has no textual signal that it is in
a *worse-known* state than an agent that just saw undo's. After, the
marker phrase is the signal: `Rolled back automatically` means checking
is a formality; its absence means checking is the only way to know.
Nothing here reassures beyond what the code guarantees — the "unlike a
failed sync or undo" framing on the unconfirmed class is a fact about
what the catch-all cannot promise, not a claim about what it can.

**Status: doc-only, not yet shipped.** I did not touch `cli.py` or
`store.py` — per the task, wording that requires a code change is
named precisely and handed to Build, not self-applied. Posted the four
exact strings to the leadership channel for Rafael/Mira to pick up as a
follow-on task; `docs/human-surface.md` §4.4 is marked "NOT YET SHIPPED"
until that lands, so the doc doesn't silently drift ahead of the CLI
the way earlier specs occasionally have.
