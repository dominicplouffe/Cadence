"""MCP server exposing Cadence's task operations as tools for an agent.

Every tool operates on the exact same SQLite store the `cadence` CLI uses
(see cadence.store.Store), so a human using the CLI and an agent using MCP
are always looking at one task list, never two.

Every tool returns a JSON-serializable dict shaped either:
    {"ok": true, "task": {...}}          on success (single task)
    {"ok": true, "tasks": [...], "count": N}   on success (list)
    {"ok": false, "error": "<code>", "message": "<why>", "hint": "<what to try>"}

so an agent can branch on `ok` without parsing prose, and a malformed
request comes back as data, not a stack trace.
"""
from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP

from cadence.store import CadenceError, InvalidTask, MAX_TITLE_LEN, Store

mcp = FastMCP(
    "cadence",
    instructions=(
        "Cadence is a local-first todo store. Use add_task to create work, "
        "list_tasks to see it, schedule_task to set/change a due date, and "
        "complete_task to mark it done. decompose_task links subtask titles "
        "you already wrote to a parent task (it does not invent the "
        "breakdown itself). reprioritise_task changes an existing task's "
        "priority. undo reverts the single most recent mutation from any "
        "surface (running it twice returns to the pre-undo state). "
        "sync_tasks syncs this store with a shared remote history and "
        "reports any per-task conflicts, which resolve_sync_conflict "
        "settles. export_tasks returns every task, open and done. All "
        "tools return {ok, ...}; on ok=false, read `error` and `hint` and "
        "retry with corrected input rather than giving up."
    ),
)


def _err(exc: CadenceError) -> dict:
    return {"ok": False, "error": exc.code, "message": exc.message, "hint": exc.hint}


def _err_unexpected(exc: Exception) -> dict:
    """Last-resort net: turn anything CadenceError doesn't already cover
    into the same {ok, error, message, hint} shape instead of letting it
    escape the tool call, where FastMCP would otherwise return it as
    isError=true with the raw exception text -- a shape no agent's `ok`
    branch is written to expect (Red Team pass-1 finding #2, case d)."""
    return {
        "ok": False,
        "error": "internal_error",
        "message": f"{type(exc).__name__}: {exc}",
        "hint": "Run list_tasks to check current state, or check CADENCE_DB_PATH.",
    }


@mcp.tool()
def add_task(
    title: str, due: Optional[str] = None, priority: Optional[str] = None
) -> dict:
    """Create a new task.

    Args:
        title: Non-empty task title, max 200 characters.
        due: Optional ISO date string, e.g. "2026-09-01".
        priority: Optional, one of "low", "med", "high". Omit for no priority.

    Returns:
        {"ok": true, "task": {id, title, status, priority, due,
        created_at, completed_at}} on success, or {"ok": false, "error",
        "message", "hint"} if title is empty, over 200 characters, or
        priority is invalid.
    """
    try:
        if len(title or "") > MAX_TITLE_LEN:
            # Fast-path pre-check, matching cli.py's cmd_add: reject before
            # hitting the store, same wording store.add() would raise
            # anyway (Red Team pass-3 finding #5).
            raise InvalidTask(
                f"title is {len(title)} characters, max {MAX_TITLE_LEN}",
                hint="Try a shorter one.",
            )
        task = Store().add(title, due=due, priority=priority)
        return {"ok": True, "task": task.to_dict()}
    except CadenceError as exc:
        return _err(exc)
    except Exception as exc:
        return _err_unexpected(exc)


@mcp.tool()
def list_tasks(status: str = "pending") -> dict:
    """List tasks, ordered high-priority first then by id.

    Args:
        status: One of "pending" (default), "done", or "all".

    Returns:
        {"ok": true, "tasks": [task, ...], "count": N} on success, or
        {"ok": false, "error", "message", "hint"} if status is invalid.
    """
    try:
        tasks = Store().list(status=status)
        return {"ok": True, "tasks": [t.to_dict() for t in tasks], "count": len(tasks)}
    except CadenceError as exc:
        return _err(exc)
    except Exception as exc:
        return _err_unexpected(exc)


@mcp.tool()
def complete_task(id: int) -> dict:
    """Mark a task done.

    Args:
        id: Numeric task id, as returned by add_task or list_tasks.

    Returns:
        {"ok": true, "task": {...}} with status "done" on success, or
        {"ok": false, "error": "task_not_found", "message", "hint"} if the
        id does not exist.
    """
    try:
        task = Store().complete(id)
        return {"ok": True, "task": task.to_dict()}
    except CadenceError as exc:
        return _err(exc)
    except Exception as exc:
        return _err_unexpected(exc)


@mcp.tool()
def schedule_task(id: int, due: str) -> dict:
    """Set or change a task's due date.

    Args:
        id: Numeric task id, as returned by add_task or list_tasks.
        due: Non-empty ISO date/time string, e.g. "2026-09-01".

    Returns:
        {"ok": true, "task": {...}} with the new due date on success, or
        {"ok": false, "error", "message", "hint"} if id is unknown or due
        is empty.
    """
    try:
        task = Store().schedule(id, due)
        return {"ok": True, "task": task.to_dict()}
    except CadenceError as exc:
        return _err(exc)
    except Exception as exc:
        return _err_unexpected(exc)


@mcp.tool()
def decompose_task(id: int, into: list[str]) -> dict:
    """Split a task into subtasks by linking titles you already wrote.

    This is a structural primitive, not a planner: Cadence does not invent
    the breakdown -- the caller decides what the subtasks are and passes
    their titles. Bounded so a looping agent can't decompose forever: max
    depth 3, max 20 subtasks per parent (across all decompose calls).

    Args:
        id: Numeric id of the parent task.
        into: Non-empty list of subtask titles (each max 200 characters).

    Returns:
        {"ok": true, "parent": {...}, "subtasks": [task, ...]} on success,
        or {"ok": false, "error", "message", "hint"} if `into` is empty, the
        parent is already at max depth, or the count would exceed the
        20-per-parent cap.
    """
    try:
        parent, children = Store().decompose(id, into)
        return {
            "ok": True,
            "parent": parent.to_dict(),
            "subtasks": [c.to_dict() for c in children],
        }
    except CadenceError as exc:
        return _err(exc)
    except Exception as exc:
        return _err_unexpected(exc)


@mcp.tool()
def reprioritise_task(id: int, priority: str) -> dict:
    """Change an existing task's priority.

    Distinct from setting priority at creation (add_task's `priority` arg):
    re-prioritising a task that already exists is its own auditable event
    and what `undo` reverts back to the prior priority.

    Args:
        id: Numeric task id, as returned by add_task or list_tasks.
        priority: One of "low", "med", "high".

    Returns:
        {"ok": true, "task": {...}} with the new priority on success, or
        {"ok": false, "error": "invalid_task", "message", "hint"} if id is
        unknown or priority isn't one of the three values.
    """
    try:
        task = Store().reprioritise(id, priority)
        return {"ok": True, "task": task.to_dict()}
    except CadenceError as exc:
        return _err(exc)
    except Exception as exc:
        return _err_unexpected(exc)


@mcp.tool()
def undo() -> dict:
    """Revert the single most recent mutation, on any surface (CLI or MCP).

    There is no per-task argument: "whatever happened most recently" is the
    one unambiguous target. Reverting is itself a new mutation, so undo is
    symmetric -- calling it twice in a row returns to the pre-undo state
    (the second undo reverts the first); there is no separate redo tool.

    Returns:
        {"ok": true, "summary": "Undid: <what changed>"} on success, or
        {"ok": false, "error": "nothing_to_undo", "message", "hint"} if
        nothing has been done yet.
    """
    try:
        summary = Store().undo()
        return {"ok": True, "summary": summary}
    except CadenceError as exc:
        return _err(exc)
    except Exception as exc:
        return _err_unexpected(exc)


@mcp.tool()
def sync_tasks(remote: Optional[str] = None) -> dict:
    """Sync this store with a shared remote (another client's history).

    Never silently drops data: a task edited on both sides since the last
    clean sync is left untouched on both this store and the remote and is
    reported in `conflicts` instead of being overwritten; everything else
    in the same sync still lands.

    Args:
        remote: Path/URL of the shared history to sync with. Only needed
            the first time (or to change it) -- omit on later calls to
            reuse the remote already configured.

    Returns:
        {"ok": true, "pulled": N, "pushed": N, "already_synced": bool,
        "conflicts": [{"id", "mine", "theirs"}, ...]}. If `conflicts` is
        non-empty, call resolve_sync_conflict(id, keep="mine"|"theirs") for
        each one, then call sync_tasks again. {"ok": false, ...} if no
        remote is configured or it can't be reached.
    """
    try:
        result = Store().sync(remote=remote)
        return {"ok": True, **result}
    except CadenceError as exc:
        return _err(exc)
    except Exception as exc:
        return _err_unexpected(exc)


@mcp.tool()
def resolve_sync_conflict(id: int, keep: str) -> dict:
    """Resolve one conflict reported by sync_tasks.

    Args:
        id: Task id from sync_tasks's `conflicts` list.
        keep: "mine" (this client's edit) or "theirs" (the remote's edit).

    Returns:
        {"ok": true, "task": {...}} with the resolved task on success, or
        {"ok": false, "error": "no_such_conflict", "message", "hint"} if
        there is no pending conflict for that id.
    """
    try:
        task = Store().resolve_conflict(id, keep)
        return {"ok": True, "task": task.to_dict()}
    except CadenceError as exc:
        return _err(exc)
    except Exception as exc:
        return _err_unexpected(exc)


@mcp.tool()
def export_tasks(format: str = "json") -> dict:
    """Export every task, open and done, unfiltered.

    Args:
        format: "json" (default) -- the raw task records -- or "table",
            the same row shape list_tasks/`cadence list` use, as an array
            of one rendered string per row.

    Returns:
        {"ok": true, "tasks": [...], "count": N} for format="json", or
        {"ok": true, "rows": [...], "count": N} for format="table", or
        {"ok": false, "error": "invalid_task", "message", "hint"} if format
        isn't "json" or "table".
    """
    if format not in ("json", "table"):
        return _err(
            InvalidTask(
                f"'{format}' isn't a supported export format",
                hint="Use 'json' or 'table'.",
            )
        )
    try:
        tasks = Store().export()
    except CadenceError as exc:
        return _err(exc)
    except Exception as exc:
        return _err_unexpected(exc)
    if format == "table":
        from cadence.cli import _render_row
        from cadence.store import Task as _Task

        rows = [_render_row(_Task(**t), 80) for t in tasks]
        return {"ok": True, "rows": rows, "count": len(tasks)}
    return {"ok": True, "tasks": tasks, "count": len(tasks)}


def run() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    run()
