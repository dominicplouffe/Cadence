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
