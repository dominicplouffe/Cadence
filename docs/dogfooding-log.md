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
