"""Cadence CLI: the human surface, over the same store the MCP server uses.

Every string, glyph, color rule, and error message below is ported
verbatim from docs/human-surface.md and its reference prototype at
tools/human-surface-prototype/cadence.py (owned by the Surface designer,
who holds veto on this surface) -- diff against that doc before changing
any wording here; do not restate it from memory.

Usage:
    cadence add "Buy milk" [--due 2026-09-01] [--priority high|med|low]
    cadence list
    cadence register                # add this project's store to ~/.config/cadence/projects.txt
    cadence overdue [--all-projects]
    cadence done <id>
    cadence schedule <id> <due-date>
    cadence decompose <id> --into "Subtask A" "Subtask B"
    cadence reprioritise <id> <low|med|high>
    cadence why <id>                # show this task's history, plain language
    cadence undo
    cadence sync [--remote PATH] [--keep-mine ID | --keep-theirs ID]
    cadence sync --all-projects [--remote PROJECTS_FILE]
    cadence export [--format json|table] [--out PATH]
    cadence mcp                     # start the MCP server over stdio (agent surface)
    cadence mcp --http [--host H] [--port P] [--token T]  # same tools, over HTTP for
                                     # remote clients (Claude web/mobile); token is
                                     # generated on first run if not given, see README
    cadence mcp --show-token        # print this machine's remote-MCP bearer token
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import sys
import textwrap
from pathlib import Path

from cadence.registry import (
    project_name,
    read_projects_file,
    read_registry,
    register_project,
    registry_path,
)
from cadence.store import (
    CadenceError,
    HistoryDegraded,
    HistoryRewritten,
    HistoryUnreadable,
    MAX_TITLE_LEN,
    Store,
    StoreUnavailable,
    SyncInconsistent,
    UndoFailed,
    VALID_PRIORITIES,
    history_degraded_warning,
)

# §4.4: code 2 is reserved for internal/store failures raised out of
# store.undo() / store.sync() / store.resolve_conflict() -- everything
# else those calls can raise is a user-input error (exit 1). Shared so
# cmd_undo and cmd_sync can't drift from each other or from cli.py ~L242's
# original add()-only version of this same rule. HistoryRewritten
# (task_01a06bf5) belongs here too: an externally rewritten .history repo
# is store-level tampering, the same family as HistoryUnreadable's
# permission/corruption failures, not a malformed CLI invocation -- the
# command typed was correct, the store underneath it wasn't.
_STORE_CLASS_ERRORS = (
    StoreUnavailable, UndoFailed, HistoryUnreadable, SyncInconsistent, HistoryRewritten,
)

USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

RESET = "\x1b[0m"
DIM = "\x1b[2m"
YELLOW = "\x1b[33m"
GREEN = "\x1b[32m"
RED = "\x1b[31m"


def _c(code: str, text: str) -> str:
    return f"{code}{text}{RESET}" if USE_COLOR else text


def _err(msg: str, code: int = 1) -> "NoReturn":
    print(f"Error: {msg}")
    sys.exit(code)


def _format_err(exc: CadenceError) -> str:
    """Render a CadenceError as the two-sentence shape docs/human-surface.md
    §4.4 specifies: (1) what was wrong, (2) the exact next step -- using the
    message/hint the error was raised with, verbatim, rather than a second
    hand-written copy of the same idea drifting out of sync with it."""
    msg = exc.message
    if exc.hint:
        sep = "" if msg.rstrip().endswith((".", "!", "?")) else "."
        msg = f"{msg}{sep} {exc.hint}"
    return msg


def _parse_date(s: str):
    try:
        return datetime.date.fromisoformat(s).isoformat()
    except ValueError:
        return None


def _require_id(raw_id: str) -> int:
    if not raw_id.isdigit():
        _err(f"'{raw_id}' is not a task id. Run 'cadence list' to see valid ids.")
    return int(raw_id)


def _days_overdue(due: str) -> int:
    d = datetime.date.fromisoformat(due)
    delta = (datetime.date.today() - d).days
    return delta if delta > 0 else 0


def _relative_time(iso_str: str) -> str:
    """"2m ago" / "just now" style rendering for `why` (wow-spec.md Part
    III §1b) -- falls back to the raw string if it isn't parseable, rather
    than crashing a command whose whole point is being legible.

    `iso_str` comes from `git log --pretty=%aI` (history.py's
    `commit_time`), and which literal offset spelling git prints for UTC
    depends on the *git binary's own version*, not on anything this
    process controls: git >= 2.42 or so prints a trailing "Z" for +00:00
    (strict-ISO-8601 Zulu notation); older git prints "+00:00" instead.
    `datetime.fromisoformat` only learned to accept a bare "Z" suffix in
    Python 3.11 (bpo-41827) -- and pyproject.toml's `requires-python`
    is ">=3.10", so this command has to keep working on a 3.10
    interpreter paired with a Z-emitting git, a combination the CI matrix
    genuinely produces (found via a real Actions run failing only on the
    3.10 leg: the un-normalized "Z" string flowed all the way through as
    `when`, wide enough to overflow `cmd_why`'s COLUMNS-aware wrap).
    Normalizing here -- once, before the version-sensitive parse -- keeps
    that entirely internal instead of leaking as a wide fallback string."""
    normalized = iso_str[:-1] + "+00:00" if iso_str.endswith("Z") else iso_str
    try:
        dt = datetime.datetime.fromisoformat(normalized)
    except (ValueError, TypeError):
        return iso_str
    now = datetime.datetime.now(dt.tzinfo) if dt.tzinfo else datetime.datetime.now()
    secs = max(0, (now - dt).total_seconds())
    if secs < 60:
        return "just now"
    mins = int(secs // 60)
    if mins < 60:
        return f"{mins}m ago"
    hours = int(mins // 60)
    if hours < 24:
        return f"{hours}h ago"
    days = int(hours // 24)
    return f"{days}d ago"


def _source_label(source):
    """"you, via CLI" / "agent, via MCP" -- wow-spec.md Part III §1b."""
    if source == "mcp":
        return "agent, via MCP"
    if source == "cli":
        return "you, via CLI"
    return None


def _render_row(task, width: int, meta_override: str = None, level: int = 0) -> str:
    done_date = task.completed_at.split("T")[0] if task.status == "done" else None
    if meta_override is not None:
        # §4.7: a parent with subtasks is tracked through them, not in
        # parallel with them -- its own due/priority yields to a subtask count.
        glyph = (_c(GREEN, "✓") if USE_COLOR else "[x]") if done_date else ("○" if USE_COLOR else "[ ]")
        meta = meta_override
    elif done_date:
        glyph = _c(GREEN, "✓") if USE_COLOR else "[x]"
        meta = f"done {done_date}"
    elif task.due and _days_overdue(task.due) > 0:
        overdue_txt = f"overdue {_days_overdue(task.due)}d"
        glyph = _c(RED, "!") if USE_COLOR else "[!]"
        meta = _c(RED, overdue_txt) if USE_COLOR else overdue_txt
    else:
        glyph = "○" if USE_COLOR else "[ ]"
        parts = []
        if task.due:
            parts.append(f"due {task.due}")
        if task.priority == "high":
            parts.append(_c(YELLOW, "high") if USE_COLOR else "(high)")
        elif task.priority:
            parts.append(task.priority)
        meta = "  ·  ".join(parts) if USE_COLOR else " | ".join(parts)

    # §3: two-space indent per nesting level for a subtask under a parent.
    level_indent = "  " * level
    id_col = f"{task.id:>3}"
    id_col = _c(DIM, id_col) if USE_COLOR else id_col
    title_col_width = max(10, width - 3 - 2 - 4 - 30 - len(level_indent))
    # break_long_words=False: a single word wider than the title column
    # (e.g. "Renegotiate" at a narrow width) overflows its own line rather
    # than being sliced mid-word -- a word cut in half is a worse defect
    # than a line that runs a bit wide.
    wrapped = textwrap.wrap(task.title, width=title_col_width, break_long_words=False) or [""]
    divider = _c(DIM, "·") if USE_COLOR else "|"

    if meta:
        first = f"{level_indent}  {glyph}  {id_col}   {wrapped[0]:<{title_col_width}}  {divider}  {meta}"
    else:
        first = f"{level_indent}  {glyph}  {id_col}   {wrapped[0]}"
    lines = [first]
    indent = " " * (len(level_indent) + 2 + 1 + 2 + 3 + 3)
    for cont in wrapped[1:]:
        lines.append(f"{indent}{cont}")
    return "\n".join(lines)


def cmd_add(args: argparse.Namespace) -> int:
    text = (args.text or "").strip()
    if not text:
        _err('\'add\' needs a task description. Try: cadence add "Buy milk"')
    if len(text) > MAX_TITLE_LEN:
        # Fast-path pre-check, same shape as the due/priority checks below:
        # reject before opening the store, using the exact wording
        # store.add() would raise anyway (Red Team pass-3 finding #5).
        _err(f"title is {len(text)} characters, max {MAX_TITLE_LEN}. Try a shorter one.")
    due = None
    if args.due:
        due = _parse_date(args.due)
        if due is None:
            _err(
                f"can't parse '{args.due}' as a date. "
                f'Try: cadence add "{args.text}" --due 2026-09-01'
            )
    if args.priority and args.priority not in ("high", "med", "low"):
        _err(
            f"'{args.priority}' is not a priority. "
            "Try: --priority high, --priority med, or --priority low"
        )
    store = Store()
    try:
        task = store.add(args.text, due=due, priority=args.priority)
    except HistoryDegraded as exc:
        # 0.2.12 Red Team finding #1: the task WAS created (SQLite already
        # committed before history recording was even attempted) -- this
        # must read as success, with an explicit warning, never as the
        # failure it used to look like (which invited a caller to retry
        # into a silent duplicate).
        task = exc.tasks[0]
        _print_recovered(task)
        print(f"Added #{task.id}: {task.title}")
        print(f"Warning: {history_degraded_warning(task.id, 'created', exc.reason)}")
        return 0
    except CadenceError as exc:
        # §4.4: code 2 is reserved for internal/store failures; everything
        # store.add() can otherwise raise (bad title/priority/due that slipped
        # past the fast-path pre-checks above, e.g. an empty --priority "")
        # is a user-input error and must exit 1, matching cmd_done/cmd_schedule.
        _err(_format_err(exc), code=2 if isinstance(exc, StoreUnavailable) else 1)
    _print_recovered(task)
    print(f"Added #{task.id}: {task.title}")
    return 0


def _print_recovered(task) -> None:
    """docs/dogfooding-log.md 2026-09-04: `add`/`decompose` can silently
    absorb an on-disk orphan task file into sqlite (store.py's
    `_absorb_orphan_task_files` -- safe since 0.2.27, but reported
    nowhere). `task.recovered` (set by `Store.add`/`Store.decompose`,
    never persisted -- see store.py's Task docstring note) is that list;
    printed here BEFORE the "Added"/"Decomposed" line for the task the
    caller actually asked for, so the two are never folded together or
    mistaken for one another."""
    for r in getattr(task, "recovered", None) or []:
        print(f"Recovered #{r.id} (was orphaned on disk): {r.title}")


def _render_tree(tasks, by_parent, width, level=0):
    lines = []
    for task in tasks:
        children = by_parent.get(task.id, [])
        if children:
            open_n = sum(1 for c in children if c.status == "pending")
            meta = f"{open_n} open subtasks"
            lines.append(_render_row(task, width, meta_override=meta, level=level))
            lines.extend(_render_tree(children, by_parent, width, level=level + 1))
        else:
            lines.append(_render_row(task, width, level=level))
    return lines


def cmd_list(args: argparse.Namespace) -> int:
    store = Store()
    tasks = store.list(status="all")
    if not tasks:
        print('No tasks yet. Add one:\n  cadence add "Buy milk"')
        return 0
    # shutil.get_terminal_size() checks the COLUMNS/LINES env vars before
    # falling back to an ioctl query and then to (80, 24) -- os.get_
    # terminal_size() skips the env-var check and can also return an
    # unreliable size off a pty with no window size set, which silently
    # produced wrong (too-narrow) wrapping. This is what makes
    # `COLUMNS=100 cadence list` behave as documented for scripts/tests,
    # and what a real user's terminal-width override should always do.
    width = shutil.get_terminal_size(fallback=(80, 24)).columns
    # §4.7: subtasks render under their parent using the two-space indent
    # rule, never as a separate view -- build the parent->children map and
    # only walk top-level tasks, letting the recursion place the rest.
    by_parent = {}
    for t in tasks:
        if t.parent_id is not None:
            by_parent.setdefault(t.parent_id, []).append(t)
    top_level = [t for t in tasks if t.parent_id is None]
    for line in _render_tree(top_level, by_parent, width):
        print(line)
    return 0


def cmd_register(args: argparse.Namespace) -> int:
    """wow-spec.md Part II §1: register this project's store so
    `overdue --all-projects` / `sync --all-projects` can find it.
    Idempotent -- running this twice in the same directory (same resolved
    CADENCE_DB_PATH) never duplicates the registry entry."""
    try:
        path, already = register_project()
    except CadenceError as exc:
        _err(_format_err(exc))
    name = project_name(path)
    if already:
        print(f"Already registered: {path} (as '{name}').")
    else:
        print(f"Registered {path} (as '{name}').")
    return 0


def _overdue_row(name: str, name_w: int, task) -> str:
    """One `overdue --all-projects` row: §4.1's `!`/red overdue glyph
    (no-color fallback `[!]`), always paired with the word 'overdue',
    leading the row -- same pairing rule `cadence list` uses for a
    single-project overdue row, extended with a project-name column."""
    glyph = _c(RED, "!") if USE_COLOR else "[!]"
    overdue_txt = f"overdue {_days_overdue(task.due)}d"
    meta = _c(RED, overdue_txt) if USE_COLOR else overdue_txt
    divider = _c(DIM, "·") if USE_COLOR else "|"
    id_col = f"#{task.id}"
    return f"{glyph}  {name:<{name_w}}  {id_col:<5}{task.title:<35}  {divider}  {meta}"


def cmd_overdue(args: argparse.Namespace) -> int:
    """wow-spec.md Part II §2: `cadence overdue [--all-projects]`.

    Without --all-projects: overdue tasks in the current CADENCE_DB_PATH
    store only, in `list`'s own row format. With --all-projects: opens
    every registered store read-only with the unmodified Store class and
    merges each store's overdue tasks into one project-labeled view --
    no new storage engine, no schema change.
    """
    if not getattr(args, "all_projects", False):
        store = Store()
        tasks = [t for t in store.list(status="pending") if t.due and _days_overdue(t.due) > 0]
        if not tasks:
            print("No overdue tasks.")
            return 0
        width = shutil.get_terminal_size(fallback=(80, 24)).columns
        for t in tasks:
            print(_render_row(t, width))
        return 0

    entries = read_registry()
    if not entries:
        print(
            "No projects registered yet. Run 'cadence register' in a "
            "project directory first."
        )
        return 0
    rows: list[tuple[str, object, str]] = []  # (name, task_or_None, error_or_None)
    for path in entries:
        name = project_name(path)
        try:
            store = Store(db_path=Path(path), must_exist=True)
        except CadenceError as exc:
            rows.append((name, None, _format_err(exc)))
            continue
        except Exception as exc:
            # 0.2.12 Red Team finding #3: a registry entry that raises
            # something other than CadenceError (e.g. an embedded null
            # byte) must not abort every other, perfectly valid
            # registered project's overdue check -- same per-project-error
            # contract as _cmd_sync_all_projects and the MCP overdue_tasks
            # tool already apply.
            rows.append((
                name, None,
                f"something went wrong opening this project's store "
                f"({type(exc).__name__}: {exc}).",
            ))
            continue
        for t in store.list(status="pending"):
            if t.due and _days_overdue(t.due) > 0:
                rows.append((name, t, None))
    name_w = max([len(r[0]) for r in rows] + [10])
    overdue_count = sum(1 for r in rows if r[1] is not None)
    error_count = sum(1 for r in rows if r[2] is not None)
    for name, task, err in rows:
        if err is not None:
            print(f"{name:<{name_w}}  Error: {err}")
        else:
            print(_overdue_row(name, name_w, task))
    plural = "" if len(entries) == 1 else "s"
    if error_count:
        # 0.2.12 Red Team findings #2/#3: never fold an errored project's
        # unknown overdue state into the same clean "N overdue" summary a
        # fully-successful check would print -- that reads as a real
        # answer when part of it is actually "couldn't check", which is
        # exactly the silent-data-loss shape those findings caught.
        ok_count = len(entries) - error_count
        print(
            f"{overdue_count} overdue across {ok_count} of {len(entries)} "
            f"registered project{plural} checked ({error_count} could not "
            "be opened; see errors above)."
        )
    else:
        print(
            f"{overdue_count} overdue across {len(entries)} registered "
            f"project{plural}. Run 'cadence register' in a project directory "
            "to add another."
        )
    # Red Team 0.2.13-indep finding: at least one registered project that
    # couldn't be opened is a store-class failure (same class single-project
    # commands already exit 2 for, §4.4), not a clean "0 overdue" run -- a
    # script/agent that only checks the exit code must be able to tell
    # "some projects are broken" from "genuinely nothing overdue" apart.
    return 2 if error_count else 0


def cmd_done(args: argparse.Namespace) -> int:
    task_id = _require_id(args.id)
    store = Store()
    try:
        task = store.complete(task_id)
    except HistoryDegraded as exc:
        task = exc.tasks[0]
        print(f"Done #{task.id}: {task.title}")
        print(f"Warning: {history_degraded_warning(task.id, 'marked done', exc.reason)}")
        return 0
    except CadenceError as exc:
        _err(_format_err(exc))
    print(f"Done #{task.id}: {task.title}")
    return 0


def cmd_schedule(args: argparse.Namespace) -> int:
    task_id = _require_id(args.id)
    due = _parse_date(args.date)
    if due is None:
        _err(f"can't parse '{args.date}' as a date. Try: cadence schedule {args.id} 2026-09-01")
    store = Store()
    try:
        task = store.schedule(task_id, due, reason=args.reason, source="cli")
    except HistoryDegraded as exc:
        task = exc.tasks[0]
        print(f"Scheduled #{task.id} for {due}: {task.title}")
        print(f"Warning: {history_degraded_warning(task.id, 'scheduled', exc.reason)}")
        return 0
    except CadenceError as exc:
        _err(_format_err(exc))
    print(f"Scheduled #{task.id} for {due}: {task.title}")
    return 0


def cmd_decompose(args: argparse.Namespace) -> int:
    task_id = _require_id(args.id)
    store = Store()
    try:
        parent, children = store.decompose(task_id, args.into or [], reason=args.reason, source="cli")
    except HistoryDegraded as exc:
        parent, children = exc.tasks[0], exc.tasks[1:]
        _print_recovered(parent)
        ids = ", ".join(f"#{c.id}" for c in children)
        print(f"Decomposed #{parent.id} into {len(children)} subtasks: {ids}")
        print(f"Warning: {history_degraded_warning(parent.id, 'decomposed', exc.reason)}")
        return 0
    except CadenceError as exc:
        _err(_format_err(exc))
    _print_recovered(parent)
    ids = ", ".join(f"#{c.id}" for c in children)
    print(f"Decomposed #{parent.id} into {len(children)} subtasks: {ids}")
    return 0


def cmd_reprioritise(args: argparse.Namespace) -> int:
    task_id = _require_id(args.id)
    store = Store()
    try:
        old = store.get(task_id)
        task = store.reprioritise(task_id, args.priority, reason=args.reason, source="cli")
    except HistoryDegraded as exc:
        task = exc.tasks[0]
        print(f"Reprioritised #{task.id} ({old.priority or 'none'} → {task.priority}): {task.title}")
        print(f"Warning: {history_degraded_warning(task.id, 'reprioritised', exc.reason)}")
        return 0
    except CadenceError as exc:
        _err(_format_err(exc))
    print(f"Reprioritised #{task.id} ({old.priority or 'none'} → {task.priority}): {task.title}")
    return 0


def cmd_why(args: argparse.Namespace) -> int:
    task_id = _require_id(args.id)
    store = Store()
    try:
        result = store.why(task_id)
    except CadenceError as exc:
        _err(_format_err(exc))
    task = result["task"]
    events = result["events"]
    # Same shutil.get_terminal_size() call cmd_list already uses (see its
    # comment above) instead of a hardcoded width -- Red Team 0.2.7 finding
    # #2: wow-spec.md §6's "no truncation, wraps at any terminal width"
    # contract applies to this surface too, and `COLUMNS=N cadence why`
    # must actually respond to N like `cadence list` already does.
    width = shutil.get_terminal_size(fallback=(80, 24)).columns
    # This line's own literal rendered indent (see the reason-quote `print`
    # below) -- `list`'s wrap subtracts ITS layout overhead (id/glyph/
    # divider columns, `_render_row`'s `title_col_width`) before calling
    # `textwrap.wrap`; Red Team 0.2.8 finding #2 is that this one never
    # did, so at narrow COLUMNS the reason text was wrapped to the full
    # terminal width and then printed 23 columns further right, overflowing
    # by exactly that much.
    reason_indent = " " * 23
    # Floor of 1 (not `list`'s 10, nor the pre-fix 20 this replaces): the
    # overhead here (23 cols) can itself exceed a narrow COLUMNS, and §6's
    # "no truncation... at any terminal width" contract means wrapping
    # tighter than feels roomy beats spilling text past the terminal edge.
    reason_wrap_width = max(1, width - len(reason_indent))
    # Header: "#<id> <title> — history (newest first):" is one flowing
    # line, not a fixed-column row like `list`'s -- it never wrapped at
    # all before this fix, so at narrow COLUMNS (e.g. a long title at
    # COLUMNS=40) it overflowed the same way the reason quote did. Wrapped
    # here the same way as `list`'s title: hanging indent under where the
    # title starts, `break_long_words=False` for the same reason as below.
    id_prefix = f"#{task.id} "
    header_tail = f"{task.title} — history (newest first):"
    header_width = max(1, width - len(id_prefix))
    header_lines = (
        textwrap.wrap(header_tail, width=header_width, break_long_words=False)
        or [header_tail]
    )
    print(f"{id_prefix}{header_lines[0]}")
    for cont in header_lines[1:]:
        print(f"{' ' * len(id_prefix)}{cont}")
    if not events:
        # Can't actually happen today (every task has at least a "Created"
        # commit) but a bare header with nothing under it would violate
        # §4.2's "never a dead end" rule if some future path ever got here.
        print()
        print("No history recorded for this task yet.")
        return 0
    for i, ev in enumerate(events):
        print()
        bullet = _c(DIM, "•") if USE_COLOR else "-"
        prio = ev["priority"]
        prio_disp = _c(YELLOW, prio) if (USE_COLOR and prio == "high") else prio
        when = ev["at"] if args.iso else _relative_time(ev["at"])
        pad = 9 + (len(prio_disp) - len(prio))  # account for ANSI codes in the width
        # `when:<12` only pads short ("just now"/"2h ago") values -- a full
        # ISO-8601 timestamp (--iso) is already >12 chars, so the width spec
        # adds no separator and glues it straight onto the event text. A
        # trailing literal space guarantees at least one separator either way.
        when_col = max(12, len(when))
        row_prefix = f"  {bullet}  {prio_disp:<{pad}}{when:<{when_col}} "
        # Same overflow class as the header/reason above: the event text
        # (e.g. "Reprioritised (none → high)") never wrapped either, so it
        # could run past COLUMNS on its own. `row_prefix`'s only ANSI-
        # bearing pieces are `bullet` and `prio_disp`, neither of which is
        # itself width-padded (`pad` already visible-width-compensates the
        # field it sits in), so its *visible* width is exactly the literal
        # layout below -- no separate ANSI-stripping needed.
        row_prefix_visible_len = 2 + 1 + 2 + 9 + when_col + 1
        event_wrap_width = max(1, width - row_prefix_visible_len)
        event_lines = (
            textwrap.wrap(ev["event"], width=event_wrap_width, break_long_words=False)
            or [ev["event"]]
        )
        print(f"{row_prefix}{event_lines[0]}")
        for cont in event_lines[1:]:
            print(f"{' ' * row_prefix_visible_len}{cont}")
        if ev["reason"]:
            label = _source_label(ev["source"])
            suffix = f" — {label}" if label else ""
            # A multi-line `reason` rides verbatim as the commit-body
            # paragraph (wow-spec.md Part III §1a) -- each ORIGINAL line is
            # its own wrap unit here instead of first flattening the whole
            # reason into one reflowed paragraph (textwrap's default
            # whitespace-collapsing behavior) and re-wrapping that. Content
            # is identical either way; this just keeps a line the author
            # deliberately broke off from its neighbor from being glued
            # back onto it by an unrelated word-wrap decision.
            reason_display_lines = ev["reason"].split("\n")
            quote_lines = []
            last = len(reason_display_lines) - 1
            for j, rline in enumerate(reason_display_lines):
                text = f'"{rline}' if j == 0 else rline
                if j == last:
                    text = f'{text}"{suffix}'
                quote_lines.append(text)
            # break_long_words=False for the same reason cmd_list's title
            # wrap sets it: a word wider than the column overflowing its
            # own line is a smaller defect than one sliced mid-word.
            wrapped = []
            for line in quote_lines:
                wrapped.extend(
                    textwrap.wrap(line, width=reason_wrap_width, break_long_words=False)
                    or [line]
                )
            for line in wrapped:
                print(f"{reason_indent}{line}")
        elif ev["reason_capable"]:
            print()
            print(
                "No reason was recorded for this change. Reasons are optional —\n"
                'pass --reason "..." (CLI) or a `reason` argument (MCP tool call)\n'
                "to leave one next time."
            )
    return 0


def cmd_undo(args: argparse.Namespace) -> int:
    store = Store()
    try:
        summary = store.undo()
    except CadenceError as exc:
        _err(_format_err(exc), code=2 if isinstance(exc, _STORE_CLASS_ERRORS) else 1)
    print(summary)
    return 0


def _cmd_sync_all_projects(args: argparse.Namespace) -> int:
    """wow-spec.md Part II §3: `cadence sync --all-projects [--remote
    <projects-file>]` -- a thin loop over the registry calling the
    existing, already-tested per-project `Store.sync` for each entry.
    Never touches the merge/diff engine itself.

    `--remote`, in this mode, is the path to *another* client's own
    projects registry file (same plain one-path-per-line format this
    file already uses -- see docs/wow-spec.md Part II's device-B
    walkthrough). Projects are matched across the two registries by
    project name (the directory each store's .db file lives in), since
    that's the only identifier both sides already share -- neither side
    ever needs to know the other's internal history layout. Omit
    --remote to reuse whatever remote each project already has configured
    from a prior single-project `cadence sync --remote ...`.
    """
    entries = read_registry()
    if not entries:
        print(
            "No projects registered yet. Run 'cadence register' in a "
            "project directory first."
        )
        return 0
    remote_map = {}
    if args.remote:
        for p in read_projects_file(Path(args.remote)):
            remote_map[project_name(p)] = p
    name_w = max([len(project_name(p)) for p in entries] + [10])
    any_conflict = False
    any_store_error = False
    for path in entries:
        name = project_name(path)
        remote_arg = None
        if args.remote:
            remote_arg = remote_map.get(name)
            if remote_arg is None:
                print(
                    f"{name:<{name_w}}  no project named '{name}' in remote "
                    f"registry '{args.remote}' -- skipped."
                )
                continue
        try:
            store = Store(db_path=Path(path), must_exist=True)
            result = store.sync(remote=remote_arg, reset_sync_base=args.reset_sync_base)
        except CadenceError as exc:
            print(f"{name:<{name_w}}  Error: {_format_err(exc)}")
            any_store_error = True
            continue
        except Exception as exc:
            # 0.2.12 Red Team finding #3: a registry entry that raises
            # something other than CadenceError (e.g. an embedded null
            # byte) must not abort every other, perfectly valid
            # registered project's sync -- same per-project-error
            # contract as the CadenceError branch above.
            print(
                f"{name:<{name_w}}  Error: something went wrong opening "
                f"this project's store ({type(exc).__name__}: {exc})."
            )
            any_store_error = True
            continue
        for w in result.get("warnings", []):
            print(f"{name:<{name_w}}  Warning: {w}")
        if result["already_synced"]:
            print(f"{name:<{name_w}}  Already in sync with origin. Nothing to pull or push.")
            continue
        pulled, pushed = result["pulled"], result["pushed"]
        for r in result.get("renumbered", []):
            print(
                f"{name:<{name_w}}  Note: #{r['old_id']} was independently "
                f"created on both clients (not an edit of the same task) -- "
                f"kept #{r['old_id']} as this client's version and gave the "
                f"other client's task a new id, #{r['new_id']}. Nothing was "
                f"lost or overwritten."
            )
        if result["conflicts"]:
            any_conflict = True
            print(
                f"{name:<{name_w}}  synced: pulled {pulled}, pushed {pushed}. "
                f"{len(result['conflicts'])} conflict needs you."
            )
            for c in result["conflicts"]:
                print(
                    f"{name:<{name_w}}  Error: #{c['id']} was edited on both "
                    f"this client and the remote since the last sync. "
                    f"Nothing was overwritten. Run 'cadence sync --keep-mine "
                    f"{c['id']}' or 'cadence sync --keep-theirs {c['id']}' "
                    f"with CADENCE_DB_PATH={path}, then sync again."
                )
        else:
            print(f"{name:<{name_w}}  synced: pulled {pulled}, pushed {pushed}. Up to date.")
    # Red Team 0.2.13-indep finding: a registered project this call couldn't
    # even open is a store-class failure (§4.4's exit-2 class), which is a
    # worse signal than "some conflicts need you" (exit 1) -- an agent
    # scripting `sync --all-projects && ...` must see a non-zero exit for
    # either case, and store errors take precedence when both occur so the
    # more severe condition isn't masked by the milder one.
    if any_store_error:
        return 2
    return 1 if any_conflict else 0


def cmd_sync(args: argparse.Namespace) -> int:
    if getattr(args, "all_projects", False):
        return _cmd_sync_all_projects(args)
    store = Store()
    if args.keep_mine is not None or args.keep_theirs is not None:
        task_id = _require_id(args.keep_mine or args.keep_theirs)
        keep = "mine" if args.keep_mine is not None else "theirs"
        try:
            task = store.resolve_conflict(task_id, keep)
        except CadenceError as exc:
            _err(_format_err(exc), code=2 if isinstance(exc, _STORE_CLASS_ERRORS) else 1)
        print(f"Resolved #{task.id} (kept {keep}): {task.title}")
        return 0
    try:
        result = store.sync(remote=args.remote, reset_sync_base=args.reset_sync_base)
    except CadenceError as exc:
        _err(_format_err(exc), code=2 if isinstance(exc, _STORE_CLASS_ERRORS) else 1)
    for w in result.get("warnings", []):
        print(f"Warning: {w}")
    if result["already_synced"]:
        print("Already in sync with origin. Nothing to pull or push.")
        return 0
    pulled, pushed = result["pulled"], result["pushed"]
    for r in result.get("renumbered", []):
        print(
            f"Note: #{r['old_id']} was independently created on both clients "
            f"(not an edit of the same task) -- kept #{r['old_id']} as this "
            f"client's version and gave the other client's task a new id, "
            f"#{r['new_id']}. Nothing was lost or overwritten."
        )
    if result["conflicts"]:
        print(
            f"Synced with origin: pulled {pulled}, pushed {pushed}. "
            f"{len(result['conflicts'])} conflict needs you."
        )
        for c in result["conflicts"]:
            print(
                f"Error: #{c['id']} was edited on both this client and the "
                f"remote since the last sync. Nothing was overwritten. Run "
                f"'cadence sync --keep-mine {c['id']}' or 'cadence sync "
                f"--keep-theirs {c['id']}', then sync again."
            )
        return 1
    print(f"Synced with origin: pulled {pulled}, pushed {pushed}. Up to date.")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    fmt = args.format or "json"
    if fmt not in ("json", "table"):
        _err(f"'{fmt}' isn't a supported export format. Try: cadence export --format json (or table).")
    store = Store()
    tasks = store.export()
    if fmt == "table":
        width = shutil.get_terminal_size(fallback=(80, 24)).columns
        from cadence.store import Task as _Task

        for t in tasks:
            print(_render_row(_Task(**t), width))
        return 0
    payload = json.dumps(tasks, indent=2)
    if args.out:
        with open(args.out, "w") as f:
            f.write(payload + "\n")
        print(f"Exported {len(tasks)} tasks to {args.out}")
    else:
        default_name = f"cadence-export-{datetime.date.today().isoformat()}.json"
        with open(default_name, "w") as f:
            f.write(payload + "\n")
        print(f"Exported {len(tasks)} tasks to {default_name}")
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    if getattr(args, "http", False):
        from cadence.registry import get_or_create_http_token, http_token_path
        from cadence.mcp_server import run_http

        token = args.token or os.environ.get("CADENCE_MCP_TOKEN") or get_or_create_http_token()
        if getattr(args, "show_token", False):
            print(token)
            print(f"(stored at {http_token_path()})", file=sys.stderr)
            return 0
        run_http(args.host, args.port, token)
        return 0
    if getattr(args, "show_token", False):
        from cadence.registry import get_or_create_http_token, http_token_path

        print(get_or_create_http_token())
        print(f"(stored at {http_token_path()})", file=sys.stderr)
        return 0

    from cadence.mcp_server import run

    run()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cadence", description="Cadence: a todo list for people and agents."
    )
    sub = parser.add_subparsers(dest="cmd")

    p_add = sub.add_parser("add", help='Add a task. Example: cadence add "Buy milk"')
    p_add.add_argument("text", nargs="?", default="")
    p_add.add_argument("--due", help="Due date, e.g. 2026-09-01")
    p_add.add_argument("--priority", help="high, med, or low")
    p_add.set_defaults(func=cmd_add)

    p_list = sub.add_parser("list", help="List tasks. Example: cadence list")
    p_list.set_defaults(func=cmd_list)

    p_register = sub.add_parser(
        "register",
        help=(
            "Register this project's store for cross-project commands "
            "(overdue --all-projects, sync --all-projects). Example: "
            "cadence register"
        ),
    )
    p_register.set_defaults(func=cmd_register)

    p_overdue = sub.add_parser(
        "overdue",
        help=(
            "Show overdue tasks. Example: cadence overdue --all-projects "
            "(across every 'cadence register'-ed project)"
        ),
    )
    p_overdue.add_argument(
        "--all-projects",
        action="store_true",
        help="Merge overdue tasks across every registered project (see 'cadence register')",
    )
    p_overdue.set_defaults(func=cmd_overdue)

    p_done = sub.add_parser("done", help="Complete a task. Example: cadence done 3")
    p_done.add_argument("id")
    p_done.set_defaults(func=cmd_done)

    p_sched = sub.add_parser(
        "schedule", help="Set a due date. Example: cadence schedule 3 2026-09-01"
    )
    p_sched.add_argument("id")
    p_sched.add_argument("date")
    p_sched.add_argument("--reason", help="Optional: why, for 'cadence why' to show later")
    p_sched.set_defaults(func=cmd_schedule)

    p_decompose = sub.add_parser(
        "decompose",
        help='Split a task into subtasks. Example: cadence decompose 4 --into "Buy flour" "Buy eggs"',
    )
    p_decompose.add_argument("id")
    p_decompose.add_argument("--into", nargs="+", metavar="TITLE")
    p_decompose.add_argument("--reason", help="Optional: why, for 'cadence why' to show later")
    p_decompose.set_defaults(func=cmd_decompose)

    p_repri = sub.add_parser(
        "reprioritise",
        help="Change an existing task's priority. Example: cadence reprioritise 4 high",
    )
    p_repri.add_argument("id")
    p_repri.add_argument("priority")
    p_repri.add_argument("--reason", help="Optional: why, for 'cadence why' to show later")
    p_repri.set_defaults(func=cmd_reprioritise)

    p_why = sub.add_parser(
        "why", help="Show why a task changed. Example: cadence why 2"
    )
    p_why.add_argument("id")
    p_why.add_argument(
        "--iso", action="store_true", help="Show absolute ISO timestamps instead of relative time"
    )
    p_why.set_defaults(func=cmd_why)

    p_undo = sub.add_parser(
        "undo", help="Revert the single most recent change. Example: cadence undo"
    )
    p_undo.set_defaults(func=cmd_undo)

    p_sync = sub.add_parser(
        "sync", help="Sync tasks with another Cadence client. Example: cadence sync"
    )
    p_sync.add_argument(
        "--remote",
        help=(
            "The other client's own CADENCE_DB_PATH value (its plain .db "
            "file path), or a git URL -- only needed once. Cadence derives "
            "that client's history location itself."
        ),
    )
    p_sync.add_argument(
        "--keep-mine", metavar="ID", help="Resolve a conflict by keeping this client's version"
    )
    p_sync.add_argument(
        "--keep-theirs", metavar="ID", help="Resolve a conflict by keeping the other side's version"
    )
    p_sync.add_argument(
        "--all-projects",
        action="store_true",
        help=(
            "Sync every registered project (see 'cadence register'), one line per "
            "project. --remote then means the path to another client's own "
            "registry file (its ~/.config/cadence/projects.txt), matched to "
            "this client's projects by project name."
        ),
    )
    p_sync.add_argument(
        "--reset-sync-base",
        action="store_true",
        help=(
            "Recovery only: use after a 'history was rewritten' error confirms "
            "this store's own hidden .history directory was rewritten outside "
            "Cadence (a manual rebase, filter-repo, or forced reset). Drops the "
            "remembered sync-base and syncs fresh -- safe: any row this store "
            "and the remote both know that isn't already identical becomes a "
            "conflict for you to settle, it can never silently drop or "
            "overwrite an edit."
        ),
    )
    p_sync.set_defaults(func=cmd_sync)

    p_export = sub.add_parser(
        "export", help="Export all tasks. Example: cadence export --format table"
    )
    p_export.add_argument("--format", help="json (default) or table")
    p_export.add_argument("--out", help="Write JSON to this path instead of a timestamped file")
    p_export.set_defaults(func=cmd_export)

    p_mcp = sub.add_parser("mcp", help="Start the MCP server over stdio (agent surface)")
    p_mcp.add_argument(
        "--http",
        action="store_true",
        help="Serve over HTTP instead of stdio, so a non-local client (Claude "
        "web/mobile, or an agent on another machine) can reach this store. "
        "Self-hosted, bearer-token-protected -- see README's 'Remote access' "
        "section.",
    )
    p_mcp.add_argument("--host", default="127.0.0.1", help="Bind host for --http (default 127.0.0.1)")
    p_mcp.add_argument("--port", type=int, default=8765, help="Bind port for --http (default 8765)")
    p_mcp.add_argument(
        "--token",
        help="Bearer token --http clients must present (or set CADENCE_MCP_TOKEN). "
        "Default: the token in --show-token, generated and stored on first use.",
    )
    p_mcp.add_argument(
        "--show-token",
        action="store_true",
        help="Print this machine's remote-MCP bearer token (generating it on "
        "first use) and exit, without starting a server.",
    )
    p_mcp.set_defaults(func=cmd_mcp)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    try:
        return args.func(args)
    except CadenceError as exc:
        _err(_format_err(exc), code=2)
    except Exception as exc:  # pragma: no cover -- last-resort net, not a
        # designed path. docs/human-surface.md §4.4: "Never a stack trace,
        # never a bare exit code, never generic invalid input." Anything
        # that reaches here is a bug in Cadence, not the user's request, so
        # it gets the store/internal exit code (2), not the input one (1).
        _err(
            f"something went wrong on Cadence's end ({type(exc).__name__}: {exc}). "
            "Unlike a failed sync or undo, this is not guaranteed to have rolled back -- run 'cadence list' to check your tasks before retrying, or check CADENCE_DB_PATH.",
            code=2,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
