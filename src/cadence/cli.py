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
    cadence mcp                     # start the MCP server over stdio (agent surface)
"""
from __future__ import annotations

import argparse
import datetime
import os
import shutil
import sys
import textwrap

from cadence.store import CadenceError, Store, StoreUnavailable

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


def _render_row(task, width: int) -> str:
    done_date = task.completed_at.split("T")[0] if task.status == "done" else None
    if done_date:
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

    id_col = f"{task.id:>3}"
    id_col = _c(DIM, id_col) if USE_COLOR else id_col
    title_col_width = max(10, width - 3 - 2 - 4 - 30)
    # break_long_words=False: a single word wider than the title column
    # (e.g. "Renegotiate" at a narrow width) overflows its own line rather
    # than being sliced mid-word -- a word cut in half is a worse defect
    # than a line that runs a bit wide.
    wrapped = textwrap.wrap(task.title, width=title_col_width, break_long_words=False) or [""]
    divider = _c(DIM, "·") if USE_COLOR else "|"

    if meta:
        first = f"  {glyph}  {id_col}   {wrapped[0]:<{title_col_width}}  {divider}  {meta}"
    else:
        first = f"  {glyph}  {id_col}   {wrapped[0]}"
    lines = [first]
    indent = " " * (2 + 1 + 2 + 3 + 3)
    for cont in wrapped[1:]:
        lines.append(f"{indent}{cont}")
    return "\n".join(lines)


def cmd_add(args: argparse.Namespace) -> int:
    text = (args.text or "").strip()
    if not text:
        _err('\'add\' needs a task description. Try: cadence add "Buy milk"')
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
    for task in tasks:
        print(_render_row(task, width))
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
