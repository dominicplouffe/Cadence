# Usability walkthrough — real production CLI

**Captured:** 2026-08-28T16:29:17Z
**Against commit:** `29db474` (`main`)
**Binary exercised:** `cadence` — the real entry point installed by
`pip install -e .` from `pyproject.toml` (`cadence = "cadence.cli:main"`),
running `src/cadence/cli.py` against `src/cadence/store.py`. This is **not**
`tools/human-surface-prototype/cadence.py` — that file is the throwaway
reference implementation used to design the human surface before Build
ported it into production; this document verifies what actually ships.

## How this was captured

```
python3 -m venv /tmp/cadence_venv
/tmp/cadence_venv/bin/pip install -e .
```

Each `cadence list` run below was driven through a real pty via
`script -qec "<cmd>" /dev/null` (not a redirected pipe) so `sys.stdout.isatty()`
is `True` and the color path in `cli.py` actually executes — a pipe would
silently fall back to the no-color branch and prove nothing about the color
path. `add`/`done`/`schedule` calls (no interactive output difference) are
captured directly. Each run used a fresh, isolated `CADENCE_DB_PATH` so the
transcript is reproducible from a clean install, matching what a first-time
user or CI would see. Escape sequences below are copied byte-for-byte
(literal ESC `\x1b`, not a printable transcription) from the raw capture at
[`tools/human-surface-prototype/real-cli-transcripts.txt`](../tools/human-surface-prototype/real-cli-transcripts.txt)
in this repo — open that file in a terminal (`cat` it) to see the actual
colored/wrapped output render.

The matrix below is the same one specified in `docs/human-surface.md` §7
("How this gets checked"), re-run here against the real CLI instead of the
prototype: empty state · wide (120-col) list · narrow (40-col) list ·
color-capable TTY · `NO_COLOR=1` fallback · a 200-character title (confirms
wrap, not truncation) · all three §4.4 field-error cases.

## Transcript

### Empty state (fresh install, no tasks)

```
$ cadence list
No tasks yet. Add one:
  cadence add "Buy milk"
```

### Add (happy path), including a 200-character title

The 4th task's title is exactly 200 characters (`wc -c` confirmed before
the run): *"This is a two-hundred character task title that a real person
might paste in verbatim from a meeting note instead of summarizing it,
because summarizing takes a moment they did not feel like spendingg"*.

```
$ cadence add "Buy milk" --due 2026-09-01
Added #1: Buy milk
$ cadence add "Renegotiate the lease"
Added #2: Renegotiate the lease
$ cadence add "Renew passport" --due 2026-08-01
Added #3: Renew passport
$ cadence add "<200-char title>"
Added #4: This is a two-hundred character task title that a real person might paste in verbatim from a meeting note instead of summarizing it, because summarizing takes a moment they did not feel like spendingg
$ cadence done 2
Done #2: Renegotiate the lease
```

### List, WIDE (120 cols), color-capable TTY

Raw bytes (ESC sequences literal) in `real-cli-transcripts.txt` lines 13–19.
Rendered, this shows: an unfilled circle `○` for the open task, a green `✓`
for the done task, a red `!` with red `overdue 27d` text for the overdue
task (due date 2026-08-01, "today" is 2026-08-28 in this run), a dim id
column, a dim `·` divider — and the 200-char title wraps across three lines
at the title-column width with **no truncation**.

### List, NARROW (40 cols), color-capable TTY — 200-char title wrap check

Same data, `COLUMNS=40`. The 200-char title wraps across 25 short lines;
every wrap point is a real word/hyphen boundary (`break_long_words=False`
in `cli.py` — confirmed no mid-word slicing anywhere in the 25 lines), and
nothing is cut with an ellipsis or dropped. This is the direct evidence for
"confirm wrap not truncation."

### List, WIDE (120 cols), `NO_COLOR=1` fallback (still a real TTY)

```
  [ ]    1   Buy milk                                                                           |  due 2026-09-01
  [x]    2   Renegotiate the lease                                                              |  done 2026-08-28
  [!]    3   Renew passport                                                                     |  overdue 27d
  [ ]    4   This is a two-hundred character task title that a real person might paste in
           verbatim from a meeting note instead of summarizing it, because summarizing takes
           a moment they did not feel like spendingg
```

No ANSI escapes anywhere in this block (checked byte-for-byte) even though
the process is attached to a real pty — `NO_COLOR=1` overrides `isatty()`
as designed (`cli.py` line 27: `USE_COLOR = sys.stdout.isatty() and
os.environ.get("NO_COLOR") is None`). Glyphs fall back to ASCII (`[ ]`,
`[x]`, `[!]`) and the divider falls back to `|`, matching §3/§4 of
`docs/human-surface.md`.

### Field errors — all three §4.4 cases

```
$ cadence add
Error: 'add' needs a task description. Try: cadence add "Buy milk"
[exit code: 1]

$ cadence schedule 3 tomorrow-ish
Error: can't parse 'tomorrow-ish' as a date. Try: cadence schedule 3 2026-09-01
[exit code: 1]

$ cadence done 99
Error: no task with id 99. Run 'cadence list' to see valid ids.
[exit code: 1]
```

Each is exactly the two-sentence shape §4.4 requires (what was wrong,
quoting the actual bad input back; the exact command to run instead), no
stack trace, no bare exit code, exit code `1` reserved for user-input
errors as specified.

Full literal capture (including all ANSI escapes, run verbatim through a
pty): [`tools/human-surface-prototype/real-cli-transcripts.txt`](../tools/human-surface-prototype/real-cli-transcripts.txt).

## Agent/human parity check

The MCP server (`src/cadence/mcp_server.py`) currently exposes four tools
over the same `Store`: `add`, `list`, `complete`, `schedule`. The CLI verbs
above map onto them 1:1 — `cadence add` / `cadence list` / `cadence done`
(the human verb for the same `Store.complete()` the MCP tool calls) /
`cadence schedule` — same fields, same validation, same store, no verb or
field on either surface that the other lacks. There is no agent-only
capability today that has no CLI equivalent, and no CLI capability an agent
can't reach through MCP.

## Designer sign-off

I ran this walkthrough myself against the real, installed `cadence` binary
(not the prototype), on 2026-08-28, at commit `29db474`.

**(a) Quality bar.** This meets "a working person would switch to this,"
not merely functional-but-ugly: color and glyphs carry status without being
the only carrier (glyph shape and text both change — `○`/`[ ]`,
`✓`/`[x]`/"done ...", `!`/`[!]`/"overdue ..."); the divider, dimming, and
column alignment hold at both 120 and 40 columns; long content wraps
instead of truncating or breaking words; errors are two plain sentences
that tell you what to type next instead of a stack trace; and every one of
these degrades correctly under `NO_COLOR=1` on a real TTY, not just when
piped. I have no unresolved objection to what is currently shipped in
`src/cadence/cli.py` at this commit.

**(b) Agent/human parity.** Confirmed above — `add`/`list`/`done`/`schedule`
each have an equally-capable, same-validation, same-store counterpart on
both the CLI and MCP surfaces. No agent-only or human-only feature exists
in the shipped scaffold today.

**(c) No unresolved veto.** None. The two prior findings from the prototype
review (terminal-width detection via `COLUMNS`/`LINES`, and
`break_long_words=False` to stop mid-word slicing at narrow widths) are
both landed in this commit (`29db474`) and re-verified against the real CLI
in this walkthrough, not just reviewed as a diff.

— Noor Halvorsen, designer, Surface team, 2026-08-28
