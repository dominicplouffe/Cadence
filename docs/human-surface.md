# Cadence — Human Surface Design

Owner: Noor Halvorsen (Surface team, designer). Status: v1, governs all
CLI/TUI output. Change this file first when the direction changes; every
other doc and every line of CLI code restates it rather than reinventing it.

Cadence's human surface is a terminal. There is no window chrome, no
brand canvas — the "product" a person judges is monospaced text in a
terminal emulator they already have open, in whatever theme they picked.
That constrains everything below.

## Mood, in concrete adjectives

**Quiet, precise, unhurried.** Not playful, not corporate, not loud with
color. Output reads like a well-kept ledger: every line answers "what
changed and what do I do next," nothing decorative, no spinners for
operations that finish in under 50ms (nearly all of them, since the store
is local). Silence is a valid state — a command that succeeds says so in
one line and stops talking.

## Why no GUI/web view yet

The bake-off's Concept 1 human surface calls for "a fast local CLI/TUI...
plus an optional minimal web view" later. This doc scopes v1 to the CLI
because that's what add/list/complete/schedule need today, and because a
web view before the CLI conventions are proven would mean redesigning it
once the wording/format below inevitably changes. TUI decision below.

---

## 1. Type

Terminal-native: whatever monospace font the user's terminal is configured
with. Cadence sets none of its own — a CLI that overrides the user's font
choice is a CLI that has misunderstood its medium. The only typographic
levers Cadence controls are **weight** (via ANSI bold) and **emphasis**
(via ANSI dim), used exactly as specified per-component below, never
decoratively.

## 2. Color palette — roles, not vibes

Terminal color must work on whatever background the user has (light or
dark, and increasingly common "solarized"-style mid-contrast themes), so
Cadence uses the terminal's own 16-color ANSI palette (never truecolor hex)
and never relies on color alone to carry meaning — every colored element
also carries a distinct glyph or word. Cadence respects `NO_COLOR` and any
non-TTY output (piped/redirected) by disabling all ANSI codes and
substituting the bracketed text tags shown in the "no-color fallback"
column.

| Role | ANSI code | Used for | No-color fallback |
|---|---|---|---|
| surface | (terminal default) | normal text | — |
| muted | `\x1b[2m` (dim) | ids, timestamps, secondary metadata | plain text, no tag |
| primary | (terminal default, bold for emphasis) | task titles | plain text |
| accent | `\x1b[33m` (yellow) | `high` priority marker | `(high)` |
| success | `\x1b[32m` (green) | completed-task glyph/word | `[done]` |
| danger | `\x1b[31m` (red) | overdue marker, errors | `[overdue]`, `Error:` prefix |
| border | `\x1b[2m` (dim) | column separators (`·`) | `·` (unstyled) |

Rationale for the restricted 16-color palette over hex: this is the one
place "own the palette" is not the designer's call to make — the terminal
owner already chose their background, and only the 16 portable ANSI codes
degrade safely across light/dark/solarized themes without contrast
surprises. Hex/truecolor stays reserved for a future web view, where
Cadence *does* own the canvas (light/dark values to be specified when that
view is built).

## 3. Spacing

Base unit = 1 character cell (there is no sub-cell spacing in a terminal).
- One blank line between the command's result and the next shell prompt —
  never zero (cramped), never more than one (wastes scrollback).
- Two-space indent per nesting level (a subtask under a parent task).
- Columns in `list` output are separated by two spaces plus a `·` divider
  (dim), not tabs — tabs render inconsistently across terminal widths.

## 4. Components

### 4.1 Task row (`cadence list`)

```
  ○  4   Buy milk                              ·  due 2026-09-01  · high
  ✓  2   Ship the bake-off doc                 ·  done 2026-08-28
  !  7   Renew passport                        ·  overdue 3d
```

Layout, left to right: status glyph (1 char) — id (right-pad to 3) — title
(left-aligned, wraps to a hanging indent at the title column if the
terminal is too narrow to fit metadata on one line; **never truncated with
an ellipsis** — an agent or human silently losing the end of a task title
is a correctness bug, not a cosmetic one) — a dim `·` divider — right-hand
metadata (due date, priority, or done date; at most one primary metadata
field colored, the rest muted).

Glyphs (always paired with the word they represent, never color alone):
- Open: `○` (unstyled)
- Done: `✓` (green) + the word `done <date>`
- Overdue: `!` (red) + the word `overdue <Nd>`

No-color / non-TTY fallback for the same three rows:
```
  [ ] 4   Buy milk                             | due 2026-09-01 | (high)
  [x] 2   Ship the bake-off doc                | done 2026-08-08
  [!] 7   Renew passport                       | overdue 3d
```

### 4.2 Empty state

```
No tasks yet. Add one:
  cadence add "Buy milk"
```
Never a bare "No tasks." — the empty state always includes the exact next
command to run, copy-pasteable as-is.

### 4.3 Confirmation (add / done / schedule)

Every mutating command echoes the concrete result on one line, never just
"OK":
```
Added #4: Buy milk
Done #4: Buy milk
Scheduled #4 for 2026-09-01: Buy milk
```

`add` and `decompose` can, as a side effect, absorb a stray task file this
store was silently carrying on disk for another client (sync's passive-
relay case — see docs/dogfooding-log.md 2026-09-04). When that happens the
recovered task gets its own line, first, never folded into or confused
with the confirmation for the task actually asked for:
```
Recovered #2 (was orphaned on disk): task from X
Added #3: new native on A
```

### 4.4 Field error (malformed input)

Exactly two sentences, always: **(1)** what was wrong, quoting the user's
actual input back at them, **(2)** the exact command to run instead. Never
a stack trace, never a bare exit code, never generic "invalid input."

```
$ cadence add
Error: 'add' needs a task description. Try: cadence add "Buy milk"

$ cadence schedule 3 tomorrow-ish
Error: can't parse 'tomorrow-ish' as a date. Try: cadence schedule 3 2026-09-01

$ cadence done 99
Error: no task with id 99. Run 'cadence list' to see valid ids.
```
Exit code `1` for every user-input error (malformed request), reserved
`2` for internal/store errors (disk full, corrupt file) so a script can
tell "you asked wrong" from "we broke" apart programmatically — this is
the same legibility bar the agent surface holds itself to, extended to
shell scripts and humans reading exit codes.

**Internal / server-side error — must never wear a field error's clothes.**
Added 2026-09-02, driven by a Red Team finding (docs/dogfooding-log.md):
a field error above means the caller's request was wrong; an internal or
server-side error means Cadence's own code broke, or the request never
reached one — the caller did nothing wrong, and editing the request
cannot fix it. The two must never look, or read, alike, on any surface:

Agent surface, MCP tool call (an uncaught exception inside a tool, e.g. a
corrupt store file — `error: "internal_error"`):
```
{"ok": false, "error": "internal_error", "message": "KeyError: 2",
 "hint": "Run list_tasks to check current state, or check CADENCE_DB_PATH."}
```

Agent surface, remote `--http` transport (the server itself faults on a
5xx before any tool runs — `error: "server_error"`, added in 0.2.18):
```
{"ok": false, "error": "server_error",
 "message": "Error handling POST request",
 "hint": "This failed on the server's side, not because of anything
 wrong with your request -- sending the identical request again will
 fail the same way. Wait and try again later, or report it; editing
 the request will not help."}
```

Human surface (CLI, any command):
```
$ cadence list
Error: something went wrong on Cadence's end (KeyError: 2). Run 'cadence
list' to check your tasks, or check CADENCE_DB_PATH.
```
exit code `2`.

Three signals separate this from a field error at a glance, and all three
agree on every surface — never rely on just one:
1. **The `error` field never carries a client-fault name.**
   `internal_error` and `server_error` are the only two codes that mean
   "not your request"; every other code (`malformed_request`,
   `malformed_json`, `invalid_request`, `request_too_large`,
   `not_acceptable`, `session_error`) means the opposite.
2. **The hint never asks for a corrected request.** A field-error hint is
   always "try `<corrected command>`" — an edit. An internal/server hint
   is always wait-or-report ("wait and try again later, or report it",
   "run list_tasks to check current state") and, for `server_error`, says
   outright that editing the request will not help.
3. **CLI exit code.** `1` for a field error, `2` for internal/server —
   scriptable without parsing any text at all.

This is what the ten-step script's malformed-request step actually
checks: that an agent (or a human reading a log) can tell "I asked
wrong" from "it broke on its own" from the response's shape alone, never
by guessing at message wording.

**Internal errors split into two safety classes — added 2026-09-04,
shipped in 0.2.29 (see dogfooding-log.md same date).**
0.2.27 made undo's and sync's "Nothing was changed" hints literally true
— sqlite now rolls back if the matching git-history write fails — but
the catch-all last-resort net (cli.py's generic handler, mcp_server.py's
`_err_unexpected`) sits behind every command, not just undo/sync, and
cannot make the same promise: it has no idea which command failed or at
what point, so it must not claim safety it cannot guarantee. Reading
any one of the three hints alone, today, gives no way to tell which
class you're in without opening the source. The fix is not new
information — the code already knows the difference — it's saying the
difference in the hint itself, with a marker phrase repeated verbatim
wherever it applies so an agent can pattern-match it across all four
sites instead of inferring it per-message.

**Class A — guaranteed rolled back.** Hint opens with `Rolled back
automatically:` every time, no exceptions, because both sites in this
class already wrap the failure in an explicit sqlite `rollback()`
before raising:

`src/cadence/store.py` ~L960, `UndoFailed` hint:
```
Rolled back automatically: nothing was changed -- the task list is
exactly what it was before this undo. Run 'cadence list' to confirm,
or file a bug.
```
(was: `"Nothing was changed -- the task list is exactly what it was
before this undo. Run 'cadence list' to confirm, or file a bug."`)

`src/cadence/store.py` ~L1095, `SyncInconsistent` hint:
```
Rolled back automatically: nothing was changed. This usually means a
task file in the history store is corrupted or in an unexpected shape
-- inspect <store>.history/tasks/*.json for a bad file, or file a bug.
(Distinct clients sharing one CADENCE_DB_PATH is a different,
already-guarded case.)
```
(was: `"Nothing was changed. This usually means a task file in the
history store is corrupted or in an unexpected shape -- inspect
<store>.history/tasks/*.json for a bad file, or file a bug. (Distinct
clients sharing one CADENCE_DB_PATH is a different, already-guarded
case.)"`)

**Class B — unconfirmed.** The two last-resort nets catch anything from
any command, at any point, so they can never promise a rollback
happened — the honest move is to say so explicitly, by name, rather
than stay silent about it and let the reader assume either way:

`src/cadence/cli.py` ~L964, the last-resort generic handler:
```
something went wrong on Cadence's end ({type}: {exc}). Unlike a
failed sync or undo, this is not guaranteed to have rolled back -- run
'cadence list' to check your tasks before retrying, or check
CADENCE_DB_PATH.
```
(was: `f"something went wrong on Cadence's end ({type(exc).__name__}:
{exc}). Run 'cadence list' to check your tasks, or check
CADENCE_DB_PATH."`)

`src/cadence/mcp_server.py` ~L272, `_err_unexpected`'s hint (the agent
surface's equivalent net, same shape problem, same fix):
```
Unlike a failed sync_tasks or undo, this is not guaranteed to have
rolled back -- run list_tasks to check current state before retrying,
or check CADENCE_DB_PATH.
```
(was: `"Run list_tasks to check current state, or check
CADENCE_DB_PATH."`)

Nothing here re-promises safety the code doesn't already deliver: Class
A's marker only ever wraps a hint whose sqlite call site already has an
explicit `rollback()` on that exact path (`store.py` L953 and L1554); a
new failure mode added later that skips the rollback must not borrow
the "rolled back automatically" phrase just because it's the same
exception type. That check belongs to whoever lands the code change,
same as any other claim in this file.

### 4.5 Loading / in-progress state

None, by default: local operations complete in well under the ~100ms a
human perceives as "instant," so a spinner would be a lie about latency
that doesn't exist. If a future operation (e.g. git-backed sync to a
remote) can genuinely block past ~300ms, it prints a single line
(`Syncing…`) with no animation, then overwrites it with the result —
never an animated frame stack (respects `prefers-reduced-motion`'s CLI
equivalent: no unnecessary motion, ever, since animation in a terminal
scrollback is actively hostile — it can't be un-seen once scrolled past).

### 4.6 Nav / help

`cadence --help` and `cadence <verb> --help` list every verb in one place,
each with the one-line form and one worked example — this is the CLI's
"nav," and it must be truthful: no verb is documented here that isn't
implemented, and no implemented verb is missing from here (checked by the
prototype's own test in step 6 below).

### 4.7 Decompose (R-08)

```
$ cadence decompose 4 --into "Buy flour" "Buy eggs" "Preheat oven"
Decomposed #4 into 3 subtasks: #12, #13, #14
```
Subtasks render under their parent in `list` output using the existing
two-space-per-level indent rule (§3), never as a separate view:
```
  ○  4   Bake a cake                            ·  3 open subtasks
    ○  12  Buy flour
    ○  13  Buy eggs
    ✓  14  Preheat oven                         ·  done 2026-08-29
```
The parent row's metadata column shows an open-subtask count instead of its
own due/priority once it has children — a parent with subtasks is tracked
through them, not in parallel with them. Cadence does not do the "turn a
vague request into subtasks" reasoning itself — that's the calling agent's
job (per the bake-off's own finding that no existing tool does this) —
`decompose` is purely the structural primitive: it links titles the caller
already wrote to a parent, atomically, as one call instead of N.

Bounded by construction, same rationale as the runner-up concept's spike
(bakeoff.md, Concept 5): **max depth 3, max 20 subtasks per parent**, so a
looping agent can't decompose forever. Both are errors per §4.4's shape,
not silent clamping:
```
$ cadence decompose 4
Error: 'decompose' needs at least one subtask. Try: cadence decompose 4 --into "Buy flour" "Buy eggs"

$ cadence decompose 15 --into "x"
Error: task #15 is already at max decomposition depth (3). Try decomposing a top-level task instead.

$ cadence decompose 4 --into t1 t2 ... t21
Error: 'decompose' takes at most 20 subtasks per call, got 21. Split into two decompose calls.
```

### 4.8 Reprioritise

```
$ cadence reprioritise 4 high
Reprioritised #4 (med → high): Buy milk
```
Distinct verb from setting priority at creation time (`cadence add --priority`)
because re-prioritising an *existing, possibly mid-flight* task is the
ten-step script's own step and deserves its own audit-log entry (§4.9)
rather than being folded into `add`. Same two-sentence error shape as every
other field error:
```
$ cadence reprioritise 4 urgent
Error: 'urgent' isn't a priority. Try: cadence reprioritise 4 high (low, med, or high)
```
**`list` ordering, corrected:** open tasks sort by priority (high → med →
low) then id ascending within a tier; done tasks always sort after open
ones, by done-date descending. This makes `list_tasks`'s docstring true
instead of aspirational — Red Team pass-1 finding #3 was that the docstring
claimed priority ordering while the store actually used insertion order;
this section is the spec Build implements against, not new copy invented
to patch around the bug.

### 4.9 Undo

```
$ cadence undo
Undid: Done #4 → reopened "Buy milk"
```
Undo reverts the single most recent mutation in the store's commit log
(git-backed per the bake-off's chosen storage model), regardless of which
surface — CLI or agent/MCP — made it; there is no per-task undo argument,
because "what happened most recently" is the one unambiguous target and
matches how a person actually thinks about undo. Reverting is itself a new
commit, which makes undo naturally symmetric with no separate redo verb:
running `cadence undo` twice in a row returns to the pre-undo state (the
second undo reverts the first). This is the git-log-as-audit-trail "wow"
capability from bakeoff.md made concrete as a command, not just a
`git log` party trick for people comfortable with git.
```
Error: no mutation to undo yet. Run a command first (add/done/schedule/...).
```

### 4.10 Sync (two clients)

```
$ cadence sync
Synced with origin: pulled 2, pushed 1. Up to date.

$ cadence sync
Already in sync with origin. Nothing to pull or push.
```
**The `--remote` value, made discoverable (Red Team R-08 finding, 2026-08-29):**
the *only* thing another client can hand you is the plain path or URL it
already knows about itself — its own `CADENCE_DB_PATH` (a `.db` file, or a
host it exposes) — never an internal storage-layout detail. So `--remote`
takes exactly that, and `sync` derives whatever internal history location
it needs from it:
```
$ cadence sync --remote /path/to/their/cadence.db
Synced with origin: pulled 2, pushed 1. Up to date. Remote saved — future
'cadence sync' calls will reuse it.
```
This is a *contract*, not an implementation detail: nothing that isn't a
value the caller already legitimately holds (their own configured db path,
or a URL) is ever a valid `--remote` argument, and nothing about a
`.history` directory, or any other on-disk naming Cadence invents for
itself, is ever documented or required as input — an agent or person
reaching for the value one client already has for itself must always be
enough. If a first connection can't be made from that value, the error
follows the exact §4.4 two-sentence shape, quoting what was tried and
naming the one thing to check — never the raw "can't reach remote '<value>'"
with no next step:
```
$ cadence sync --remote /wrong/path.db
Error: no Cadence store found at '/wrong/path.db'. Check the path is the
other client's CADENCE_DB_PATH and that client has run 'cadence sync' at
least once.
```
Never silent data loss on conflict (this was the bake-off's own hardest-risk
spike criterion: "serialized cleanly, or fails loudly with a recoverable
error"). Cadence takes the second branch — a conflicting task is reported,
not overwritten, everything else in the same sync still lands:
```
$ cadence sync
Synced with origin: pulled 3, pushed 2. 1 conflict needs you.
Error: #4 differs between this client and the remote since the last sync (edited on both sides, or independently created with the same id). Nothing was overwritten. Run 'cadence sync --keep-mine 4' or 'cadence sync --keep-theirs 4', then sync again.
```
Exit code `1` while any conflict is unresolved (a script can tell "sync is
done" from "sync needs a human" apart, same contract as §4.4), `0` once
clean.

### 4.11 Export

```
$ cadence export
Exported 14 tasks to cadence-export-2026-08-29.json

$ cadence export --format table
```
prints every task (open and done, unfiltered) in the exact `list` row
format (§4.1), to stdout, for a human to read or pipe onward — no separate
export layout to learn. `json` (default) is for backup/interop/agents and
writes a timestamped file unless `--out PATH` is given, in which case it
writes there and to stdout is suppressed. Same two-sentence error shape for
an unknown format:
```
$ cadence export --format xml
Error: 'xml' isn't a supported export format. Try: cadence export --format json (or table).
```

## 5. Is a TUI (curses/full-screen) view warranted now?

**No, not yet — decided and recorded here, not left open.** A full-screen
TUI (e.g. built later with `textual`) pays for itself once there's enough
simultaneous state to justify a persistent view (a live kanban-style board,
multi-pane filtering) — none of that exists at the add/list/complete/
schedule stage. Building one now would mean designing two output surfaces
in parallel and keeping them in sync through weeks of rapid iteration on
wording alone. Revisit once decompose/reprioritise land and `list` output
routinely exceeds one terminal page — that's the concrete trigger, not a
vibe. Scrollable/interactive `list` (arrow-key select, inline complete) is
the natural v2 TUI candidate when that trigger fires.

## 6. Accessibility checklist (binding, not aspirational)

- **Contrast:** the 16-color ANSI palette is the terminal owner's own
  contrast choice; Cadence never overrides it with truecolor, so contrast
  is inherited correctly for both light and dark themes by construction.
- **Meaning never carried by color alone:** every colored element (done,
  overdue, high-priority) has a paired glyph and word, and a full
  bracketed-text fallback under `NO_COLOR`/non-TTY (tables above).
- **No truncation:** titles wrap with a hanging indent instead of being
  cut with an ellipsis, at any terminal width, including the longest
  string a user will plausibly type (tested with a 200-character title,
  §7).
- **No unnecessary motion:** no spinners, no animated frames, ever (§4.5).
- **Real labels, not placeholder text pretending to be a label:** `--due`
  and `--priority` are named flags with `--help` text, never inferred from
  positional guesswork that could silently misfire.
- **Keyboard/focus:** not applicable to a stateless CLI (no persistent
  focus target exists); becomes binding again the day a TUI ships (§5).

## 7. What was actually looked at (not just specified)

Per the constitution ("judge the rendered thing, never the description of
it"), the reference prototype in `tools/human-surface-prototype/cadence.py`
was run and its literal terminal output inspected for:
- narrow (40-col) and wide (120-col) terminal widths,
- color and `NO_COLOR=1` (fallback tags),
- zero tasks (empty state) and one task with a 200-character title
  (wrap, no truncation),
- all three field-error cases in §4.4.

Raw transcripts of every one of those runs are in
`tools/human-surface-prototype/transcripts.txt` in this repo, captured
from the actual prototype, not written by hand.

## 8. For Build: how this lands in the real CLI

This design doc is normative; `tools/human-surface-prototype/cadence.py` is
a small, dependency-free, throwaway reference implementation (JSON-file
store, not the git-native/SQLite store the bake-off doc specifies) whose
only job is to prove every string and format above against a real
terminal, not to be the production storage engine. Build: port the exact
wording, glyphs, column layout, and error-message shapes verbatim into the
real CLI/MCP scaffold — do not restate them from memory, diff against this
file. If a real constraint (store schema, MCP transport) forces a wording
change, that change lands here first, then in code.
