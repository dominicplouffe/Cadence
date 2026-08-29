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
