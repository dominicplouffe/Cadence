# The wow spec — one moment, made concrete

**Status:** design contract, not yet implemented. Written by Concept
(Ines) in response to the chairman's verdict after using the real CLI
himself: *"the idea is good, the execution is very poor... you haven't
come close to a wow."* He is right, and this document is not a rebuttal
of that — it is an honest diagnosis of why, followed by the smallest
concrete change that fixes it.

Per the constitution, **Noor (Surface) holds binding veto on this
document** before Build treats any of it as final. See the sign-off
section at the bottom.

---

# Part I — Positioning: does anyone need this, now that Claude Code has native Tasks?

**Added 2026-08-29, second pass, in response to a sharper version of the
same challenge: *"Do they even need a todo list? Give me proof they
do."*** In January 2026 Anthropic shipped a native, disk-persistent
"Tasks" system inside Claude Code itself (v2.1.16+): tasks live on disk
at `~/.claude/tasks/<task_list_id>/`, survive a terminal close, a
machine restart, or a crash, and multiple sessions can share one list by
setting `CLAUDE_CODE_TASK_LIST_ID` — "a developer can shut down their
terminal, switch machines, or recover from a system crash, and the agent
reloads the exact state of the project" ([VentureBeat, Jan 2026](https://venturebeat.com/orchestration/claude-codes-tasks-update-lets-agents-work-longer-and-coordinate-across)).
That is, plainly, the naive version of Cadence's pitch — "an agent that
remembers what it was doing" — shipped for free, by the vendor whose CLI
most of our own users already run. If that's all Cadence is, it's not
needed. It has to be more than that or it isn't real.

## I.1 What's actually true about Claude Code's Tasks (verified against primary sources, not inferred)

- **Disk-persistent, survives restarts:** true. Confirmed above.
- **Multi-session coordination on one machine:** true, via
  `CLAUDE_CODE_TASK_LIST_ID` — "when a session updates a task, the change
  broadcasts to all other sessions watching that task list"
  ([VentureBeat](https://venturebeat.com/orchestration/claude-codes-tasks-update-lets-agents-work-longer-and-coordinate-across)).
- **Scoped to one project at a time.** There is no dispute about this —
  it's the subject of open, unresolved feature requests against Claude
  Code itself: *"When working on projects that span multiple
  repositories, there's no way to open a single Claude Code session that
  has context across all of them"* ([anthropics/claude-code#35362](https://github.com/anthropics/claude-code/issues/35362),
  open); *"When working across multiple unrelated repositories in
  parallel... there's no way to monitor all instances from one central
  place"* ([anthropics/claude-code#26394](https://github.com/anthropics/claude-code/issues/26394),
  closed **not planned** — Anthropic looked at this and declined it).
- **No cross-device sync of task state beyond manual dotfile copying.**
  Multiple open, unresolved issues ask for exactly this and Anthropic has
  shipped nothing native: *"Cross-machine session sync: connect Claude
  Code instances across multiple machines"* ([#45358](https://github.com/anthropics/claude-code/issues/45358));
  *"no native way to sync Claude Code config and skills across
  machines"*, whose reporter's own workaround is "Git repos and
  symlinks" ([#36693](https://github.com/anthropics/claude-code/issues/36693));
  *"Claude Code memory and settings don't sync across devices — no
  supported path to enable this"* ([#64081](https://github.com/anthropics/claude-code/issues/64081)).
  Third-party projects exist to fill exactly this hole (e.g.
  `tawanorg/claude-sync`), which is itself evidence the gap is real and
  felt, not theoretical.
- **No undo, no version history — task state is overwritten, not
  versioned.** This is not a hypothetical risk; it has already caused
  reported data loss: *"entire task history was unexpectedly deleted"*
  after an unrelated error, reappearing only ~20 minutes later with no
  user action ([#28208](https://github.com/anthropics/claude-code/issues/28208),
  closed as duplicate — i.e. Anthropic confirms this is a known class of
  bug, not a one-off). The feature request for a fix is open and unmet:
  *"[FEATURE] Undo Last Action"* ([#13038](https://github.com/anthropics/claude-code/issues/13038));
  *"maintain a recoverable edit history / snapshot of all file changes
  for 24 hours"* ([#36542](https://github.com/anthropics/claude-code/issues/36542)).
  Related: plan-mode files (a sibling feature, same persistence model)
  have the identical failure shape — *"Plan mode silently overwrites
  plan files across projects — no project scoping, no backup"*
  ([#35943](https://github.com/anthropics/claude-code/issues/35943)).
- **Locked to Claude Code.** `TaskCreate`/`TaskUpdate`/`TaskList`/
  `TaskGet` are Claude-Code-internal tools, not a portable standard — and
  not even a stable one: as of the most recent update, *"Task-list tools
  ...no longer exposed to the model after recent update"*
  ([#80015](https://github.com/anthropics/claude-code/issues/80015)),
  meaning the same person's Cursor or Codex session, or even a future
  Claude Code session after a silent tooling change, has no access to
  this state at all. This mirrors the wider, well-documented split
  between Anthropic's proprietary `CLAUDE.md` and the open,
  multi-vendor `AGENTS.md` standard adopted by Codex, Cursor, Copilot,
  Windsurf and Gemini CLI — Claude Code's own task memory follows the
  same proprietary pattern.

## I.2 The honest positioning

Cadence is not "a todo app that duplicates what Claude Code already does
for free." Claude Code's Tasks is a **session continuity** feature: it
keeps one agent from forgetting its own plan inside one project while
that terminal is open, or coordinated across a couple of sessions on one
machine, on one vendor's CLI. Cadence is not competing there — it would
lose, and duplicating a free vendor feature is not a business.

What Cadence is: **the cross-project, cross-device, cross-tool, audited
layer that sits above any single agent session**, specifically the four
things §I.1 shows Claude Code's own Tasks does not do and does not have
open, credible plans to do (one request in this exact space was closed
"not planned" by Anthropic itself — #26394):

1. **Cross-project.** One person's overdue/priority picture spans
   however many repos they work in, not one project's `~/.claude/tasks`
   directory.
2. **Cross-device, for real** — not "copy a dotfile and hope," a
   designed sync verb with conflict resolution. Cadence already ships
   this (§II below is honest about exactly how much).
3. **Audited, git-backed, undoable** — every change is a commit, not an
   overwrite, so "why did this change" and "put it back" are both real
   answers instead of the exact failure mode #28208 and #13038 describe.
4. **Cross-tool.** Cadence is a plain CLI plus a standard MCP server —
   any MCP-speaking agent (Claude Code, Cursor, Codex-via-MCP, a custom
   script) reads and writes the same store the same way. It doesn't
   evaporate if one vendor changes its internal tool set, because it was
   never that vendor's internal tool set.

## I.3 The honest self-check this task explicitly asked for

Is the narrower framing itself real, or are we rescuing the thesis by
narrowing the target until something survives? Checked against the same
evidence bar as the rest of the bake-off:

- Gaps 2 ("cross-device sync") and 3 ("undo/audit") are **verified real
  pain** (§I.1: multiple open issues, one confirmed data-loss report, a
  cottage industry of third-party sync tools) **and Cadence already
  ships working code for both** — this is not a spec waiting on
  evidence, it's a built capability that happens to also be a real,
  cited gap in the incumbent. That's the strongest part of the case.
- Gap 4 ("locked to one tool") is real as a fact (§I.1, #80015) but is
  the weakest *motivator* — most people asking "do I need this" are not
  thinking about vendor lock-in, they're thinking about whether their
  list works. It's a legitimate secondary argument, not the wow moment.
- Gap 1 ("cross-project unified view") is where this reframing is most
  honestly at risk, and the finding has to be stated plainly rather than
  smoothed over: **Cadence does not have this today either.** Verified
  directly against the installed 0.2.5 CLI this run — `cadence list
  --help` takes no flags, and every command reads exactly one
  `CADENCE_DB_PATH`, one project, same as Claude Code's Tasks. So the
  strongest single differentiator in this pitch is currently **spec, not
  built**, exactly like gap 1's Claude Code counterpart (#35362, #26394)
  is spec/declined on their side too. The difference — and the reason
  this is not "just narrowing until something survives" — is that
  Cadence's architecture makes it cheap to build honestly: each
  project's tasks already live in one plain SQLite file plus a plain git
  history, so scanning N known files and merging their rows is a read-
  only aggregation on data that already exists in the right shape, not a
  redesign. Whether a working person actually wants a unified cross-repo
  view (as opposed to just `cd`-ing to the one repo with the fire) is
  itself unverified — the two open, unresolved Claude Code issues asking
  for exactly this ([#35362](https://github.com/anthropics/claude-code/issues/35362),
  [#26394](https://github.com/anthropics/claude-code/issues/26394)) are
  the closest thing to evidence of real demand this doc has, and one of
  them was declined by the vendor rather than built — so treat gap 1 as
  a plausible, cheaply-buildable bet with real-but-thin demand evidence,
  not a proven need. §II below is explicit about which lines are real
  today and which are this bet.

**Bottom line:** the reframe holds, but on the strength of gaps 2+3
(already real and already shipped), not gap 1 (plausible, unbuilt,
thin evidence). If Build has to sequence, the audited-sync layer is the
proven bet; cross-project aggregation is the speculative one and should
be scoped small and validated in dogfooding (this company's own five-ish
repos) before it's treated as the headline claim.

---

# Part II — The cross-project wow walkthrough

Same honesty discipline as the rest of this file: every line below is
tagged **TODAY** (real, run against the actual installed 0.2.5 CLI this
run, 2026-08-29, `NO_COLOR=1`) or **SPEC** (does not exist yet, per §I.3's
finding that this is the speculative half of the pitch).

**Setup — five real project repos, one person, `cd`-ing between them
over a week, an agent creating/touching tasks in each as it works:**

```
$ export CADENCE_DB_PATH=~/proj-alpha/cadence.db
$ cadence add "Ship the auth fix" --priority med
Added #1: Ship the auth fix
$ cadence add "Write onboarding docs" --due 2026-08-20
Added #2: Write onboarding docs
$ cadence list
  [ ]    1   Ship the auth fix                          |  med
  [!]    2   Write onboarding docs                      |  overdue 9d
```
*(TODAY — verified real output, this run, `cadence-todo` 0.2.5, five
separate `CADENCE_DB_PATH` stores set up the same way for proj-beta
through proj-epsilon, omitted here for length.)*

**The moment: "what's overdue across all my projects" —**
```
$ cadence overdue --all-projects
proj-alpha    #2  Write onboarding docs        overdue 9d
proj-gamma    #4  Renew TLS cert                overdue 3d
proj-epsilon  #1  Reply to security disclosure  overdue 1d
3 overdue across 5 registered projects. Run 'cadence register' in a
project directory to add it to this list.
```
*(SPEC — does not exist yet. Requires two new pieces, both scoped
narrowly per §I.3's "cheap to build" argument: (a) `cadence register`,
which appends the current directory's resolved `CADENCE_DB_PATH` to a
plain-text registry file at `~/.config/cadence/projects.txt` — one path
per line, no new format; (b) `cadence overdue --all-projects`, which
opens every registered store read-only with the existing `Store` class
unmodified and merges the existing per-store overdue query. No schema
change, no new database, no change to `sync`'s merge logic — this is
additive read-only tooling over stores that already work exactly this
way today.)*

**Surface review note (2026-08-30, binding when this is built):** the
row format above drops §4.1's `!` (red) overdue glyph — when this ships,
lead each row with it, same word pairing as single-project `overdue`
(`!  proj-alpha    #2  Write onboarding docs        overdue 9d`,
no-color fallback `[!]`), so "overdue" is never carried by the word
"overdue" alone in one view and by glyph+word in another. Not a blocker
on this document's approval — Part II is sequenced after Part III per
the Sign-off below and this can land with the implementation.

**The moment: "why did the auth-fix task jump to top priority three days
ago" — the answer Claude Code's Tasks structurally cannot give (§I.1:
no version history, task state is overwritten):**
```
$ cadence why 1 --project proj-alpha
#1 Ship the auth fix — history (newest first):

  • high     3d ago   Reprioritised (med → high)
                       "customer escalation came in, this blocks their
                       release" — agent, via MCP
  • med      6d ago   Created
                       — you, via CLI
```
*(SPEC — `why` and the `reason` field are the same unshipped pieces
already specified in Part III (§1) of this document, applied here across
a registered project rather than the current directory's own store. The
underlying git-backed history this reads from is real today —
`<db-path>.history/.git` already records every change as a commit,
confirmed by reading `store.py`/`history.py` — the gap is entirely
`why`'s CLI/MCP surface not existing yet, not the data being absent.)*

**The moment: sync that whole picture to a second device —**
```
# on device B, after cloning ~/.config/cadence/projects.txt or running
# 'cadence register' locally against the same five project directories:
$ cadence sync --all-projects --remote alice@device-a:~/.config/cadence/projects.txt
proj-alpha    synced: pulled 2, pushed 1. Up to date.
proj-beta     synced: pulled 0, pushed 0. Up to date.
proj-gamma    synced: pulled 1, pushed 0. Up to date.
proj-delta    synced: pulled 1, pushed 3. Up to date.
proj-epsilon  synced: pulled 0, pushed 1. 1 conflict needs you — run
              'cadence sync --project proj-epsilon --keep-mine <id>' or
              '--keep-theirs <id>', then sync again.
$ cadence overdue --all-projects
proj-alpha    #2  Write onboarding docs        overdue 9d
proj-gamma    #4  Renew TLS cert                overdue 3d
proj-epsilon  #1  Reply to security disclosure  overdue 1d
```
*(Mixed. The per-project sync verb itself is **TODAY** — two-client sync
with conflict resolution already works and is verified end-to-end in
`docs/ten-step-transcript.md` step 8 (10/10 against the published
0.2.1 package). What's **SPEC** is the `--all-projects` convenience
flag: today a person would run `cadence sync --remote ...` five times,
once per `CADENCE_DB_PATH`, which is real but tedious — `--all-projects`
is a thin loop over the registry from the first moment above, not new
sync logic, so it inherits step 8's already-verified correctness rather
than reopening it.)*

**Surface review note (2026-08-30, binding when this is built):** the
original draft collapsed each `--all-projects` row to "synced, no
conflicts," dropping the pulled/pushed counts and the conflict-recovery
line §4.10 requires for single-project `sync` — a fleet view is not an
excuse to say less than the single-project command already says per
project. Fixed above to carry the same counts and the same
`--keep-mine`/`--keep-theirs` recovery instruction, one line per project,
never silently summarized past a conflict. Same non-blocking status as
the `overdue --all-projects` note above.

## II.1 Honest gap list for Part II

| Piece | Status (verified 2026-08-29, cadence-todo 0.2.5) |
|---|---|
| Single-project add/list/schedule/overdue display | **TODAY** — real, shown above |
| Two-client sync with conflict resolution, per project | **TODAY** — real, shipped, 10/10 in `docs/ten-step-transcript.md` step 8 |
| Git-backed per-task history existing on disk | **TODAY** — real, confirmed in `store.py`/`history.py`, just not surfaced (same finding as Part III §0) |
| `cadence register` / project registry file | **SPEC** — does not exist |
| `cadence overdue --all-projects` | **SPEC** — does not exist |
| `cadence why` / `reason` field | **SPEC** — does not exist (same as Part III §1) |
| `cadence sync --all-projects` | **SPEC** — does not exist; the underlying per-project `sync` it would loop does exist |
| Evidence that people actually want a unified cross-repo todo view (vs. just `cd`-ing) | **THIN** — two open/declined Claude Code issues asking for the code-context version of this ([#35362](https://github.com/anthropics/claude-code/issues/35362), [#26394](https://github.com/anthropics/claude-code/issues/26394)); no direct evidence yet for the task-list version specifically. Recommend validating via this company's own dogfooding (five-ish repos) before treating this as the headline wow moment rather than the audited-sync layer (§I.3). |

---

# Part III — the single-project "why did this change" wow moment (prior pass, unchanged below)

This part predates Part I/II above and answers a narrower, earlier
version of the same challenge (*"you haven't come close to a wow"*
rather than *"do they even need this"*). It stands on its own — the
`reason`/`why` mechanism it specifies is what Part II's cross-project
`why --project` walkthrough reuses — and is left as originally written
and committed; Noor's pending review (see Sign-off) covers this part.

## 0. The diagnosis, stated plainly

Everything shipped so far (0.1.0 → 0.2.5) is real and correctly built:
`add`/`list`/`done`/`schedule`/`decompose`/`reprioritise`/`undo`/`sync`/
`export` all work, are tested, and are honestly documented in
`docs/human-surface.md` and `docs/ten-step-transcript.md`. But none of
that, on its own, is a reason for a working person to switch away from
Todoist or Apple Reminders. Every one of those verbs exists in some form
in a tool they already have.

The bake-off (`docs/bakeoff.md`) staked Cadence's entire differentiation
on one claim: *turning git into an undo/history/audit/sync layer means
you can ask "why did this change" and get a real answer instead of a
guess.* That claim is currently **false in practice**, for two compounding
reasons, both verified against the real 0.2.5 CLI on 2026-08-29:

1. **The audit trail is real but unreachable.** It lives in a directory
   (`<db-path>.history/.git`) nobody is told exists, and reading it
   requires running `git log` inside it — a skill most of Cadence's
   target users (people who'd otherwise use Todoist) do not have and
   should never need. Verified: a fresh store's `cadence.db.history/.git`
   log after add → decompose → reprioritise → done → undo reads as
   ```
   7a6dd2f Undo: Done #2: Book a venue
   e4ab3c7 Done #2: Book a venue
   f66408d Reprioritised #2 (none → high): Book a venue
   2b8e0ef Decomposed #1 into 3 subtasks: #2, #3, #4
   da4f15b Added #1: Plan Mara's 30th birthday party
   db5c8a6 init: empty task store
   ```
   That is genuinely a clean, human-readable one-liner per change — the
   problem is 100% discoverability, not legibility of the text itself.
   No shipped command shows this to anyone. `docs/human-surface.md` §4.9
   even says undo is "the git-log-as-audit-trail wow capability... made
   concrete as a command, not just a `git log` party trick" — but that
   command doesn't exist yet. This spec's job is to make that sentence
   true.

2. **The one thing the chairman actually wants to watch — the agent's
   reasoning — is never captured at all.** Look at the real, current
   ten-step transcript (`docs/ten-step-transcript.md`, step 3): the
   agent's breakdown of a vague request is logged by the *test harness*
   as `"Agent's own breakdown (not produced by the tool): [...]"` — a
   side note about what happened in the calling agent's own context,
   which Cadence never sees and never stores. The tool call itself,
   `decompose_task({"id": 1, "into": [...]})`, carries zero reasoning.
   Same for `reprioritise_task({"id": 2, "priority": "high"})` — no
   record of *why* #2 outranks #3 and #4. The moment that session ends,
   the reasoning is gone forever, on both the CLI and MCP surfaces. You
   cannot "watch the agent reason about priority" through Cadence today,
   full stop — you can only watch it change a number.

Fixing display of an audit trail that doesn't contain the "why" would be
polish on an empty claim. Fixing #2 without fixing #1 would put real
reasoning in a place nobody will ever read. **Both have to ship together,
and both are small.**

## 1. The fix: one new optional field, one new verb

### 1a. `reason` — optional, on every verb that changes something

Add an optional `reason: str | None` parameter to `decompose`,
`reprioritise`, and `schedule` (both `Store` methods and their CLI/MCP
surfaces). When given, it is appended as a second paragraph in the git
commit `_snapshot_and_commit` already makes for that change — no new
storage, no schema change, no new file format. When omitted (a human
using the CLI who doesn't feel like typing one, or an agent that didn't
reason about it), the commit looks exactly as it does today. This is
strictly additive and backward compatible with every existing test and
the sync/merge engine (commit *bodies* are never diffed by the merge
logic, which operates on task-file JSON — confirmed by reading
`store.py`/`history.py`: `_snapshot_and_commit` takes a plain message
string, `GitHistory.commit` passes it straight to `git commit -m`, and
`sync`'s merge diff never reads commit messages at all).

CLI:
```
cadence decompose 1 --into "Book a venue" "Order a cake" --reason "..."
cadence reprioritise 2 high --reason "..."
cadence schedule 3 2026-09-10 --reason "..."
```
MCP: `decompose_task(id, into, reason=None)`,
`reprioritise_task(id, priority, reason=None)`,
`schedule_task(id, due, reason=None)` — same optionality, so no existing
call in `docs/ten-step-transcript.md` breaks.

`add`, `done`, `undo`, `sync`, `export` do **not** get a `reason` field:
`add`/`done` are usually self-explanatory from the title alone, `undo`
reverting is its own explanation, and `sync`/`export` aren't decisions
about a task's priority or shape. Adding `reason` everywhere would be
scope creep for no legibility gain — the three verbs above are exactly
the ones a person asks "why" about.

### 1b. `cadence why <id>` / `why_task(id)` — the missing verb

Renders that task's git-backed history as a plain-language timeline,
newest first, in the same visual language as `list` (muted metadata, no
raw git anywhere in the output — a user should never learn the word
"commit" to use this). Implementation is a thin read layer on top of
what already exists: each task is one file at `tasks/<id>.json` inside
the `.history` repo (confirmed in `history.py`), so `git log --pretty=...
-- tasks/<id>.json` already returns exactly the commits that touched
*this* task, in order, with the `reason` paragraph (if any) in the
commit body — no new index or table needed.

**Glyph note (Surface review, 2026-08-30):** each history line leads
with a dim `•` (border role, `\x1b[2m`, no-color fallback `-`), **not**
`list`'s `○`. `○`/`✓`/`!` are reserved by §4.1 for a task's *current*
status (open/done/overdue); a `why` line reports a *past event*, and
several event types (e.g. "Reprioritised ... undone") aren't a status at
all, so reusing `○` would silently teach the reader a glyph means two
different things depending on which command printed it. `•` carries no
status meaning by itself — the resulting priority word right after it
(`high`/`med`/`low`/`none`) keeps its existing accent color per §2's
palette table when it's `high`, same as everywhere else. This is the one
binding edit from Surface review; everything else in §1b/§2's `why`
output is approved as drafted.

**Real example, this task's actual history** (content from §2 below):
```
$ cadence why 2
#2 Book a venue — history (newest first):

  • high     2m ago   Reprioritised (med → high)
                       "venues this size book up fast, and the date's
                       only 3 weeks out" — agent, via MCP
  • none     3m ago   Created as subtask of #1 (Plan Mara's 30th
                       birthday party)
                       "breaking the vague ask into things I can
                       actually check off" — agent, via MCP

Run 'cadence undo' to revert the most recent change above, or
'cadence why 1' to see the parent task's own history.
```
Relative time (`2m ago`) with the absolute ISO timestamp available via
`--iso` for scripts/CI, matching the "legible first, precise on request"
pattern `human-surface.md` already uses elsewhere. `"— agent, via MCP"` /
`"— you, via CLI"` distinguishes which surface made the change (already
determinable: the CLI and MCP server are the only two callers of
`Store`, so threading a `source` tag through the same call takes no new
design, just a parameter Build adds alongside `reason`).

When no reason was recorded for a change:
```
  • none     3m ago   Created as subtask of #1

No reason was recorded for this change. Reasons are optional — pass
--reason "..." (CLI) or a `reason` argument (MCP tool call) to leave one
next time.
```
This is not a punishment for the honest "I don't know" case — it tells
the reader exactly what to do differently, per the same two-sentence
error-shape discipline `human-surface.md` §4.4 already applies to actual
errors.

Error case, same shape as every other field error:
```
$ cadence why 99
Error: no task with id 99. Run 'cadence list' to see valid ids.
```

This is the fix to the #1 gap named in the task brief: **`cadence why`
replaces "go find a hidden `.git` directory and run `git log`" outright.**
A person never needs to know Cadence uses git for this to work — `why`
is the product surface; git is the implementation detail underneath it,
exactly the way `sync` already hides `.history`'s existence per
`human-surface.md` §4.10.

## 2. The exact end-to-end sequence (the wow moment, written to actually read on screen)

Real content, not "task X." A person is planning a birthday party and
hands the vague part to their agent.

**Setup — the person, directly, in under 15 seconds:**
```
$ cadence add "Plan Mara's 30th birthday party"
Added #1: Plan Mara's 30th birthday party
```
*(TODAY — verified real output, 2026-08-29, `cadence-todo` main @ `d00f9ca`, `NO_COLOR=1` for this transcript.)*

**The person, to their agent, in their own words (out of band — this
prompt goes to the agent, not to Cadence):**
> "Sort out the birthday party somehow, I don't want to think about it.
> Figure out what actually needs booking first, and just pick what's
> most urgent."

**The agent, via MCP, working — this is the part that has to become
watchable:**
```
decompose_task(id=1, into=["Book a venue", "Order a cake",
  "Send invites to the group chat"],
  reason="breaking the vague ask into things I can actually check off")
```
*(SPEC — `reason` param does not exist yet; every other part of this
call is real and works today, verified against `decompose_task` in
`docs/ten-step-transcript.md` step 3.)*
```
$ cadence list
  ○   1   Plan Mara's 30th birthday party         · 3 open subtasks
    ○   2   Book a venue
    ○   3   Order a cake
    ○   4   Send invites to the group chat
```
*(TODAY — verified real output, this run, `NO_COLOR=1`.)*
```
reprioritise_task(id=2, priority="high",
  reason="venues this size book up fast, and the date's only 3 weeks out")
```
*(SPEC for the `reason` argument — `reprioritise_task(id=2,
priority="high")` without it is real and verified today.)*
```
$ cadence list
  ○   1   Plan Mara's 30th birthday party         · 3 open subtasks
    ○   2   Book a venue                          · (high)
    ○   3   Order a cake
    ○   4   Send invites to the group chat
```
*(TODAY — verified real output.)*

**The person, wanting to know why their to-do list just changed under
them without asking — this is the moment that has to be there and isn't:**
```
$ cadence why 2
#2 Book a venue — history (newest first):

  • high     just now   Reprioritised (med → high)
                         "venues this size book up fast, and the date's
                         only 3 weeks out" — agent, via MCP
  • none     just now   Created as subtask of #1 (Plan Mara's 30th
                         birthday party)
                         "breaking the vague ask into things I can
                         actually check off" — agent, via MCP
```
*(SPEC — `why` does not exist yet. This exact output is what closes the
chairman's "you haven't come close to a wow" gap: a person who never
touched git, never opened a hidden folder, and never asked their agent
to explain itself gets a straight answer.)*

**The person changes their mind and undoes it — real, works today:**
```
$ cadence undo
Undid: Reprioritised #2 (high → med) undone: Book a venue
$ cadence why 2
#2 Book a venue — history (newest first):

  • med      just now   Reprioritised (high → med) undone
  • high     1m ago     Reprioritised (med → high)
                         "venues this size book up fast, and the date's
                         only 3 weeks out" — agent, via MCP
  • none     2m ago     Created as subtask of #1 ...
```
*(`cadence undo`'s output line is TODAY/real, verified this run against
the actual reprioritise-then-undo case; the `why` re-render after undo is
SPEC, following directly from §1b — undo is itself a commit like any
other, so it needs no special-casing in `why`.)*

This is the whole bet: **decompose → reprioritise → why → undo**, four
commands, under 90 seconds, with real content a person recognizes as
their own problem, not a demo abstraction.

## 3. The opening moment — under 60 seconds, before any agent is involved

The current README quickstart (`add`, `list`, `done`) is correct but not
differentiating — every todo CLI has that exact three-command shape. The
opening moment has to *set up* the payoff above, not try to be the
payoff itself, and it has to work for a person alone at a terminal with
no agent handy yet (most first contact with an OSS CLI is a human
skimming a README, not already holding an agent session). Proposed
replacement quickstart (a person can run every line themselves, no LLM
required, because `decompose`/`reprioritise`/`why`/`undo` are just CLI
verbs with literal string arguments):

```
pip install cadence-todo
cadence add "Plan Mara's 30th birthday party"
cadence decompose 1 --into "Book a venue" "Order a cake" "Send invites"
cadence reprioritise 2 high --reason "venue books up fastest"
cadence why 2
cadence undo
```
Six lines, one idea proven: *every change to this list — yours or an
agent's — comes with a legible reason and a clean undo, because it's
backed by the same git you already trust for code, not a bespoke
history feature Cadence invented and might get wrong.* That is the "I
have never seen a todo app do that" line from the bake-off's own
criterion, made literal, in the first 60 seconds, before an agent is
ever mentioned.

This replaces the `add`/`list`/`done` block in the README's *Install*
section once `reason`/`why` ship — not before, since shipping a
quickstart that doesn't run against the published package would be
exactly the kind of "README describing a capability the script doesn't
exercise" the constitution calls marketing, not evidence.

## 4. The remote-reachability spike — separate track, not a gate

Rafael's spike (`task_01a04ea6c1c161496acaa52d`) is done: `cadence mcp
--http` works end-to-end on branch `experimental/http-mcp-transport`
(commit `d5caed4`, not on `main`, not published), with his own
recommendation to proceed narrowly-scoped and his note that "none of the
three finish-line tests depend on it." I agree with that read for the
wow moment specifically, for an additional reason: the wow moment
described in §2 is **local, single-machine, single-user** — one person,
one agent, one store. Two-client sync (the thing HTTP transport would
extend to a real network) is a real, already-shipped, already-tested
capability (`docs/ten-step-transcript.md` step 8, 10/10), but it is a
*different* moment — "my two devices agree" — not the one the chairman
reacted to. Shipping remote HTTP transport does nothing to make
decompose/reprioritise/why/undo more watchable or more legible. Sequence
it after §1/§2 land, as its own track, exactly as Rafael proposed.

## 5. What this deliberately does not do (scope discipline)

- No TUI/full-screen view. `docs/human-surface.md` §5 already
  considered and declined this; nothing here changes that call.
- No new storage engine, no schema migration beyond one new column
  (`reason`, nullable) on the existing git-JSON-file model.
- No change to `sync`'s merge/diff logic — commit message bodies are
  never read by the merge engine today and this spec doesn't change
  that, so Finding-C-class risk is not reopened.
- No changes to `add`, `done`, `sync`, `export`, or the malformed-input
  handling Red Team is already fixing under `reqt_01a048d43764d6a83accad03`
  — that work (the pydantic-leak fix) is independent of and should land
  either before or alongside this, not instead of it.
- Does not touch the ten-step script's fixed ten steps — `why` is not
  one of the ten and isn't required for the finish-line CI/registry/
  transcript tests to pass. It exists solely to satisfy the chairman's
  wow gate, which the constitution is explicit is *on top of*, not a
  substitute for, those three tests.

## 6. Honest gap list — what's real today vs. what this spec requires

| Piece | Status today (0.2.5, verified 2026-08-29) |
|---|---|
| `add` / `list` / `done` / `schedule` | Real, shipped, tested |
| `decompose` / `reprioritise` (no reason) | Real, shipped, tested |
| `undo` | Real, shipped, tested, verified in this doc's own transcript |
| git-backed audit trail existing on disk | Real, but hidden in `<db>.history/.git`, zero shipped commands surface it |
| Agent's reasoning captured anywhere in Cadence | **Does not exist.** Reasoning lives only in the calling agent's own context and is lost when that session ends |
| `reason` argument on decompose/reprioritise/schedule | **Does not exist.** Spec'd in §1a |
| `cadence why` / `why_task` MCP tool | **Does not exist.** Spec'd in §1b |
| `source` (CLI vs MCP) tag on each change | **Does not exist.** Needed for `why`'s "— agent, via MCP" / "— you, via CLI" line |
| Revised 60-second README quickstart | **Does not exist.** Spec'd in §3, blocked on §1a/§1b shipping first |
| Remote/HTTP MCP transport | Spiked, working, unpublished, correctly out of scope for this moment (§4) |

## Sign-off

This is a design contract; per the constitution the designer owns the
human surface and holds veto here. Build should not implement Part
I/II/III's SPEC pieces against this document until this section reads
**REVIEWED** for the relevant part.

- **Concept (Ines Whitlock):** Part III written and committed
  2026-08-29 (first pass); Part I/II written and committed 2026-08-29
  (second pass, in response to the chairman's "do they even need a todo
  list" challenge), based on reading the real shipped
  `store.py`/`history.py`/`cli.py`/`mcp_server.py`, running the real
  installed CLI (transcripts are actual output, not invented — see each
  section's TODAY/SPEC annotations), and verifying every external claim
  about Claude Code's Tasks against a primary source or a live GitHub
  issue (linked inline in Part I).
- **Surface (Noor Halvorsen): REVIEWED, approved with edits, 2026-08-30.**
  Reviewed against `docs/human-surface.md` (the governing system file —
  §4.1 status glyphs, §4.4 error shape, §4.9 undo, §4.10 sync). One
  binding fix made directly to this file: every `why`-timeline line used
  `list`'s `○` (reserved by §4.1 for a task's current open/done/overdue
  status) for what is actually a past *event*, several of which aren't a
  status at all ("Reprioritised ... undone") — changed to a dim `•`
  (border role) throughout §1b and §2, with the reasoning inline at §1b.
  Also fixed: `why 99`'s error copy now matches §4.4's established "no
  task with id N" wording instead of inventing new phrasing for the same
  case. Both are mechanical, restore-the-system-file fixes, not new
  design — Part III (`reason` + `why`) is **cleared for Build to
  implement as written above (post-edit)**.

  Part I/II (cross-project `register`/`overdue --all-projects`/
  `sync --all-projects`) are **approved in principle, with two
  non-blocking binding fixes already applied inline** (the `overdue
  --all-projects` row needs §4.1's `!` overdue glyph, and
  `sync --all-projects` needs to carry per-project pulled/pushed counts
  and the §4.10 conflict-recovery line instead of collapsing to "synced,
  no conflicts") — land them when Build actually implements Part II, not
  before, since I agree with Ines's own sequencing call: gaps 2+3
  (sync, audit/undo) are the proven bet, Part III's `reason`/`why` ships
  first, and Part II's registry/aggregation pieces wait for dogfooding to
  confirm real demand for a unified cross-repo view (§II.1's last row)
  before Build spends time on them. No veto on any part; nothing here
  blocks Build starting on Part III today.
