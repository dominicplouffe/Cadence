#!/usr/bin/env python3
"""Cadence human-surface reference prototype.

This is NOT the production Cadence CLI. It exists to make the design in
docs/human-surface.md real and testable against an actual terminal, with
zero dependencies, so the wording/formatting/color rules can be judged as
rendered output rather than as a spec. Build: port the exact strings and
layout below into the real CLI/MCP scaffold; do not re-derive them.

Store: a plain JSON file (CADENCE_HOME/tasks.json, default ~/.cadence).
That is deliberately NOT the git-native/SQLite store the bake-off doc
picked for production -- swapping the store must never change a single
character of what a human sees, which is the point this prototype proves.

Usage:
    cadence.py add "Buy milk" [--due YYYY-MM-DD] [--priority high|med|low]
    cadence.py list
    cadence.py done <id>
    cadence.py schedule <id> <YYYY-MM-DD>
"""
import argparse
import datetime
import json
import os
import shutil
import sys
import textwrap

CADENCE_HOME = os.environ.get("CADENCE_HOME", os.path.expanduser("~/.cadence"))
STORE_PATH = os.path.join(CADENCE_HOME, "tasks.json")

USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

RESET = "\x1b[0m"
DIM = "\x1b[2m"
BOLD = "\x1b[1m"
YELLOW = "\x1b[33m"
GREEN = "\x1b[32m"
RED = "\x1b[31m"


def c(code, text):
    return f"{code}{text}{RESET}" if USE_COLOR else text


def load():
    if not os.path.exists(STORE_PATH):
        return {"next_id": 1, "tasks": []}
    with open(STORE_PATH) as f:
        return json.load(f)


def save(data):
    os.makedirs(CADENCE_HOME, exist_ok=True)
    with open(STORE_PATH, "w") as f:
        json.dump(data, f, indent=2)


def find(data, task_id):
    for t in data["tasks"]:
        if t["id"] == task_id:
            return t
    return None


def err(msg, code=1):
    print(f"Error: {msg}")
    sys.exit(code)


def today():
    return datetime.date.today().isoformat()


def parse_date(s):
    try:
        return datetime.date.fromisoformat(s).isoformat()
    except ValueError:
        return None


def cmd_add(args):
    if not args.text or not args.text.strip():
        err('\'add\' needs a task description. Try: cadence add "Buy milk"')
    due = None
    if args.due:
        due = parse_date(args.due)
        if due is None:
            err(f"can't parse '{args.due}' as a date. Try: cadence add \"{args.text}\" --due 2026-09-01")
    if args.priority and args.priority not in ("high", "med", "low"):
        err(f"'{args.priority}' is not a priority. Try: --priority high, --priority med, or --priority low")
    data = load()
    t = {
        "id": data["next_id"],
        "title": args.text,
        "due": due,
        "priority": args.priority,
        "done": None,
    }
    data["tasks"].append(t)
    data["next_id"] += 1
    save(data)
    print(f"Added #{t['id']}: {t['title']}")


def cmd_done(args):
    if not args.id.isdigit():
        err(f"'{args.id}' is not a task id. Run 'cadence list' to see valid ids.")
    data = load()
    t = find(data, int(args.id))
    if t is None:
        err(f"no task with id {args.id}. Run 'cadence list' to see valid ids.")
    t["done"] = today()
    save(data)
    print(f"Done #{t['id']}: {t['title']}")


def cmd_schedule(args):
    if not args.id.isdigit():
        err(f"'{args.id}' is not a task id. Run 'cadence list' to see valid ids.")
    data = load()
    t = find(data, int(args.id))
    if t is None:
        err(f"no task with id {args.id}. Run 'cadence list' to see valid ids.")
    due = parse_date(args.date)
    if due is None:
        err(f"can't parse '{args.date}' as a date. Try: cadence schedule {args.id} 2026-09-01")
    t["due"] = due
    save(data)
    print(f"Scheduled #{t['id']} for {due}: {t['title']}")


def days_overdue(due):
    d = datetime.date.fromisoformat(due)
    delta = (datetime.date.today() - d).days
    return delta if delta > 0 else 0


def render_row(t, width):
    if t["done"]:
        glyph = c(GREEN, "✓") if USE_COLOR else "[x]"
        meta = f"done {t['done']}"
    elif t["due"] and days_overdue(t["due"]) > 0:
        glyph = c(RED, "!") if USE_COLOR else "[!]"
        meta = c(RED, f"overdue {days_overdue(t['due'])}d") if USE_COLOR else f"overdue {days_overdue(t['due'])}d"
    else:
        glyph = "○" if USE_COLOR else "[ ]"
        parts = []
        if t["due"]:
            parts.append(f"due {t['due']}")
        if t["priority"] == "high":
            parts.append(c(YELLOW, "high") if USE_COLOR else "(high)")
        elif t["priority"]:
            parts.append(t["priority"])
        meta = " | ".join(parts) if not USE_COLOR else "  ·  ".join(parts)

    id_col = f"{t['id']:>3}"
    id_col = c(DIM, id_col) if USE_COLOR else id_col
    title_col_width = max(10, width - 3 - 2 - 4 - 30)
    # break_long_words=False: a single word wider than the title column
    # (e.g. "Renegotiate" in a very narrow terminal) overflows onto its own
    # line rather than being sliced mid-word -- a word cut in half is a
    # worse defect than a line that runs a bit wide.
    wrapped = textwrap.wrap(t["title"], width=title_col_width, break_long_words=False) or [""]
    divider = c(DIM, "·") if USE_COLOR else "|"

    lines = []
    first = f"  {glyph}  {id_col}   {wrapped[0]:<{title_col_width}}  {divider}  {meta}" if meta else f"  {glyph}  {id_col}   {wrapped[0]}"
    lines.append(first)
    indent = " " * (2 + 1 + 2 + 3 + 3)
    for cont in wrapped[1:]:
        lines.append(f"{indent}{cont}")
    return "\n".join(lines)


def cmd_list(args):
    data = load()
    if not data["tasks"]:
        print('No tasks yet. Add one:\n  cadence add "Buy milk"')
        return
    # shutil.get_terminal_size() checks COLUMNS/LINES env vars first, then
    # falls back to an ioctl query, then to (80, 24) -- this is what makes
    # `COLUMNS=40 cadence list` (used in our own width tests, and by any
    # real user who overrides it) actually take effect.
    width = shutil.get_terminal_size(fallback=(80, 24)).columns
    for t in data["tasks"]:
        print(render_row(t, width))


def build_parser():
    p = argparse.ArgumentParser(prog="cadence", description="Cadence: a todo list for people and agents.")
    sub = p.add_subparsers(dest="cmd")

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

    p_sched = sub.add_parser("schedule", help="Set a due date. Example: cadence schedule 3 2026-09-01")
    p_sched.add_argument("id")
    p_sched.add_argument("date")
    p_sched.set_defaults(func=cmd_schedule)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
