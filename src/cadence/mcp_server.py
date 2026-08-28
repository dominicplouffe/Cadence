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

from cadence.store import CadenceError, Store

mcp = FastMCP(
    "cadence",
    instructions=(
        "Cadence is a local-first todo store. Use add_task to create work, "
        "list_tasks to see it, schedule_task to set/change a due date, and "
        "complete_task to mark it done. All tools return {ok, ...}; on "
        "ok=false, read `error` and `hint` and retry with corrected input "
        "rather than giving up."
    ),
)


def _err(exc: CadenceError) -> dict:
    return {"ok": False, "error": exc.code, "message": exc.message, "hint": exc.hint}


@mcp.tool()
def add_task(
    title: str, due: Optional[str] = None, priority: Optional[str] = None
) -> dict:
    """Create a new task.

    Args:
        title: Non-empty task title.
        due: Optional ISO date string, e.g. "2026-09-01".
        priority: Optional, one of "low", "med", "high". Omit for no priority.

    Returns:
        {"ok": true, "task": {id, title, status, priority, due,
        created_at, completed_at}} on success, or {"ok": false, "error",
        "message", "hint"} if title is empty or priority is invalid.
    """
    try:
        task = Store().add(title, due=due, priority=priority)
        return {"ok": True, "task": task.to_dict()}
    except CadenceError as exc:
        return _err(exc)


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


def run() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    run()
