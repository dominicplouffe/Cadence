"""Cadence CLI: the human surface, over the same store the MCP server uses.

Every string, glyph, color rule, and error message below is ported
verbatim from docs/human-surface.md and its reference prototype at
tools/human-surface-prototype/cadence.py (owned by the Surface designer,
who holds veto on this surface) -- diff against that doc before changing
any wording here; do not restate it from memory.

Usage:
    cadence add "Buy milk" [--due 2026-09-01] [--priority high|med|low]
    cadence list
    cadence done <id>
    cadence schedule <id> <due-date>
    cadence decompose <id> --into "Subtask A" "Subtask B"
    cadence reprioritise <id> <low|med|high>
    cadence undo
    cadence sync [--remote PATH] [--keep-mine ID | --keep-theirs ID]
    cadence export [--format json|table] [--out PATH]
    cadence mcp                     # start the MCP server over stdio (agent surface)
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import sys
import textwrap

from cadence.store import (
    CadenceError,
    MAX_TITLE_LEN,
    Store,
    StoreUnavailable,
    VALID_PRIORITIES,
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
    except CadenceError as exc:
        # §4.4: code 2 is reserved for internal/store failures; everything
        # store.add() can otherwise raise (bad title/priority/due that slipped
        # past the fast-path pre-checks above, e.g. an empty --priority "")
        # is a user-input error and must exit 1, matching cmd_done/cmd_schedule.
        _err(_format_err(exc), code=2 if isinstance(exc, StoreUnavailable) else 1)
    print(f"Added #{task.id}: {task.title}")
    return 0


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


def cmd_done(args: argparse.Namespace) -> int:
    task_id = _require_id(args.id)
    store = Store()
    try:
        task = store.complete(task_id)
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
        task = store.schedule(task_id, due)
    except CadenceError as exc:
        _err(_format_err(exc))
    print(f"Scheduled #{task.id} for {due}: {task.title}")
    return 0


def cmd_decompose(args: argparse.Namespace) -> int:
    task_id = _require_id(args.id)
    store = Store()
    try:
        parent, children = store.decompose(task_id, args.into or [])
    except CadenceError as exc:
        _err(_format_err(exc))
    ids = ", ".join(f"#{c.id}" for c in children)
    print(f"Decomposed #{parent.id} into {len(children)} subtasks: {ids}")
    return 0


def cmd_reprioritise(args: argparse.Namespace) -> int:
    task_id = _require_id(args.id)
    store = Store()
    try:
        old = store.get(task_id)
        task = store.reprioritise(task_id, args.priority)
    except CadenceError as exc:
        _err(_format_err(exc))
    print(f"Reprioritised #{task.id} ({old.priority or 'none'} → {task.priority}): {task.title}")
    return 0


def cmd_undo(args: argparse.Namespace) -> int:
    store = Store()
    try:
        summary = store.undo()
    except CadenceError as exc:
        _err(_format_err(exc))
    print(summary)
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    store = Store()
    if args.keep_mine is not None or args.keep_theirs is not None:
        task_id = _require_id(args.keep_mine or args.keep_theirs)
        keep = "mine" if args.keep_mine is not None else "theirs"
        try:
            task = store.resolve_conflict(task_id, keep)
        except CadenceError as exc:
            _err(_format_err(exc))
        print(f"Resolved #{task.id} (kept {keep}): {task.title}")
        return 0
    try:
        result = store.sync(remote=args.remote)
    except CadenceError as exc:
        _err(_format_err(exc))
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

    p_done = sub.add_parser("done", help="Complete a task. Example: cadence done 3")
    p_done.add_argument("id")
    p_done.set_defaults(func=cmd_done)

    p_sched = sub.add_parser(
        "schedule", help="Set a due date. Example: cadence schedule 3 2026-09-01"
    )
    p_sched.add_argument("id")
    p_sched.add_argument("date")
    p_sched.set_defaults(func=cmd_schedule)

    p_decompose = sub.add_parser(
        "decompose",
        help='Split a task into subtasks. Example: cadence decompose 4 --into "Buy flour" "Buy eggs"',
    )
    p_decompose.add_argument("id")
    p_decompose.add_argument("--into", nargs="+", metavar="TITLE")
    p_decompose.set_defaults(func=cmd_decompose)

    p_repri = sub.add_parser(
        "reprioritise",
        help="Change an existing task's priority. Example: cadence reprioritise 4 high",
    )
    p_repri.add_argument("id")
    p_repri.add_argument("priority")
    p_repri.set_defaults(func=cmd_reprioritise)

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
    p_sync.set_defaults(func=cmd_sync)

    p_export = sub.add_parser(
        "export", help="Export all tasks. Example: cadence export --format table"
    )
    p_export.add_argument("--format", help="json (default) or table")
    p_export.add_argument("--out", help="Write JSON to this path instead of a timestamped file")
    p_export.set_defaults(func=cmd_export)

    p_mcp = sub.add_parser("mcp", help="Start the MCP server over stdio (agent surface)")
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
            "Run 'cadence list' to check your tasks, or check CADENCE_DB_PATH.",
            code=2,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
