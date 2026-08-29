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
newest first, in the same visual language as `list` (glyph, muted
metadata, no raw git anywhere in the output — a user should never learn
the word "commit" to use this). Implementation is a thin read layer on
top of what already exists: each task is one file at `tasks/<id>.json`
inside the `.history` repo (confirmed in `history.py`), so
`git log --pretty=... -- tasks/<id>.json` already returns exactly the
commits that touched *this* task, in order, with the `reason` paragraph
(if any) in the commit body — no new index or table needed.

**Real example, this task's actual history** (content from §2 below):
```
$ cadence why 2
#2 Book a venue — history (newest first):

  ○ high     2m ago   Reprioritised (med → high)
                       "venues this size book up fast, and the date's
                       only 3 weeks out" — agent, via MCP
  ○ none     3m ago   Created as subtask of #1 (Plan Mara's 30th
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
  ○ none     3m ago   Created as subtask of #1

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
Error: task #99 doesn't exist. Run 'cadence list' to see valid ids.
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

  ○ high     just now   Reprioritised (med → high)
                         "venues this size book up fast, and the date's
                         only 3 weeks out" — agent, via MCP
  ○ none     just now   Created as subtask of #1 (Plan Mara's 30th
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

  ○ med      just now   Reprioritised (high → med) undone
  ○ high     1m ago     Reprioritised (med → high)
                         "venues this size book up fast, and the date's
                         only 3 weeks out" — agent, via MCP
  ○ none     2m ago     Created as subtask of #1 ...
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
human surface and holds veto here. Build should not implement §1–3
against this document until this section reads **REVIEWED**.

- **Concept (Ines Whitlock):** written and committed 2026-08-29, based on
  reading the real shipped `store.py`/`history.py`/`cli.py`/`mcp_server.py`
  and running the real installed CLI (transcripts above are actual
  output, not invented — see §2/§0 annotations for exactly which lines
  are real vs. spec).
- **Surface (Noor Halvorsen):** **PENDING** — requested via leadership
  channel 2026-08-29. Veto or approval, and any wording/glyph/column
  changes to the `why` output in §1b, land as a follow-up commit to this
  file before Build starts, per the same review pattern already used for
  §4.7–4.10 of `docs/human-surface.md`.
