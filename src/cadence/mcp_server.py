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

import hmac
import json
import sys
from typing import Optional

import mcp.server.streamable_http as _streamable_http_module
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError as _FastMCPToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import ValidationError as _PydanticValidationError

from cadence import __version__ as _cadence_version
from cadence.registry import project_name, read_projects_file, read_registry
from cadence.registry import register_project as _register_project
from cadence.store import (
    CadenceError,
    HistoryDegraded,
    InvalidTask,
    MAX_TITLE_LEN,
    Store,
    history_degraded_warning,
)

# 0.2.17 independent Red Team pass (docs/dogfooding-log.md): a ~2KB request
# body nested >=1000 levels deep (`[[[...]]]`) makes the `mcp` SDK's own
# `json.loads(body)` (streamable_http.py, inside `_handle_post_request`)
# raise an uncaught RecursionError at CPython's default recursion limit --
# not the `json.JSONDecodeError` that function already catches and turns
# into a clean 400. The SDK's own outer handler still catches it (nothing
# crashes), but only as a generic 500, which _classify_envelope_error
# above now reports honestly as a server fault rather than a client
# mistake -- but a request that can make the server raise an exception at
# all, for ~2KB of input, is worth closing outright rather than merely
# labelling correctly after the fact.
#
# Fix: bound JSON nesting *before* it ever reaches the SDK's json.loads,
# so this case degrades to the exact same clean 400 "Parse error" path
# ordinary invalid JSON already takes (tested, classified as
# `malformed_json`) instead of reaching CPython's recursion limit at all.
# Scoped as narrowly as possible: only the `json` name as looked up
# *inside `mcp.server.streamable_http`* is replaced (a fresh shim object,
# not a mutation of the real stdlib `json` module every other importer,
# including the rest of cadence, shares) so this cannot change JSON
# parsing behavior anywhere else in the process. Every other attribute
# (JSONDecodeError, dumps, ...) is delegated straight through to the real
# module, so `except json.JSONDecodeError` inside that file keeps working
# unchanged -- it just also now catches this case.
_MAX_JSON_NESTING_DEPTH = 200  # Dov's repro: 800 does not reproduce the
# RecursionError, 1000 does -- 200 leaves generous headroom under that
# while sitting far above any nesting a real cadence JSON-RPC message
# uses (a handful of levels at most).


def _json_nesting_too_deep(data: object, limit: int) -> bool:
    """Cheap single-pass structural scan for JSON array/object nesting
    depth -- counts `[`/`{` and `]`/`}` outside of string literals,
    without actually parsing (so it cannot itself recurse). Brackets
    inside a JSON string value (e.g. a title containing literal `[[[`)
    are correctly ignored because we track string/escape state."""
    text = data.decode("utf-8", "replace") if isinstance(data, (bytes, bytearray)) else str(data)
    depth = 0
    in_string = False
    escaped = False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "[{":
            depth += 1
            if depth > limit:
                return True
        elif ch in "]}":
            depth -= 1
    return False


# Dov's independent-verify finding on 0.2.20: a bare (unquoted) JSON integer
# literal with more digits than the interpreter's int<->str conversion limit
# makes stdlib json.loads raise ValueError *inside* int() -- a different crash
# than the nesting-depth RecursionError above, but the same family: malformed
# client input reaching the SDK's raw parser before cadence's own error
# handling can shape it into a clean 4xx. Pre-validating it here means it
# degrades to the same malformed_json 400 path as the nesting case, with a
# hint that matches the actual fix (shrink the number) instead of the 500
# "editing the request will not help" hint that _classify_envelope_error
# gives every status_code >= 500, which is simply false for this input.
_MAX_JSON_INT_DIGITS = sys.get_int_max_str_digits()  # 4300 by default; 0 means
# the interpreter's limit has been disabled (PYTHONINTMAXSTRDIGITS=0), in which
# case int() never raises for this reason and there is nothing to pre-empt.


def _json_number_too_long(data: object, limit: int) -> bool:
    """Cheap single-pass scan for a run of digits outside any string literal
    longer than `limit` -- e.g. a bare JSON integer with more digits than
    Python allows int() to convert. A JSON integer literal (no `.` or `e`) is
    always one unbroken run of digits, so this single-pass scan (which cannot
    itself recurse or build the number) catches it before json.loads would
    call int() on it. A long numeric *string* value (quoted) is correctly
    ignored, since that never reaches int()."""
    text = data.decode("utf-8", "replace") if isinstance(data, (bytes, bytearray)) else str(data)
    in_string = False
    escaped = False
    run = 0
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            run = 0
        elif ch.isdigit():
            run += 1
            if run > limit:
                return True
        else:
            run = 0
    return False


class _DepthBoundedJSONForStreamableHTTP:
    """Stand-in for the `json` module, installed only as the `json` name
    inside `mcp.server.streamable_http`'s own namespace (see below) --
    every attribute other than `loads` is the real stdlib `json` module,
    untouched."""

    def __getattr__(self, name):
        return getattr(json, name)

    @staticmethod
    def loads(data, *args, **kwargs):
        if _json_nesting_too_deep(data, _MAX_JSON_NESTING_DEPTH):
            text = data.decode("utf-8", "replace") if isinstance(data, (bytes, bytearray)) else str(data)
            raise json.JSONDecodeError(
                f"JSON nesting exceeds this server's {_MAX_JSON_NESTING_DEPTH}-level limit",
                text,
                0,
            )
        if _MAX_JSON_INT_DIGITS and _json_number_too_long(data, _MAX_JSON_INT_DIGITS):
            text = data.decode("utf-8", "replace") if isinstance(data, (bytes, bytearray)) else str(data)
            raise json.JSONDecodeError(
                f"JSON number exceeds this server's {_MAX_JSON_INT_DIGITS}-digit limit",
                text,
                0,
            )
        return json.loads(data, *args, **kwargs)


_streamable_http_module.json = _DepthBoundedJSONForStreamableHTTP()

mcp = FastMCP(
    "cadence",
    # The MCP SDK auto-attaches DNS-rebinding protection (a Host-header
    # allow-list defaulting to 127.0.0.1/localhost/[::1]) any time no
    # transport_security is given. That check runs inside
    # mcp.streamable_http_app() itself, *before* BearerAuth in
    # _make_http_app below ever sees the request -- so a correct bearer
    # token behind a tunnel or reverse proxy (Host: some-name.example.com)
    # still got a bare 421, unauthenticated or not. DNS rebinding is a
    # browser-JS attack that tricks same-origin checks by resolving an
    # attacker domain to 127.0.0.1 after the fact; it has nothing to
    # defend against here, because `--http` mode's actual boundary is the
    # bearer token (see _make_http_app's docstring), checked on every
    # request regardless of Host. Disabling this SDK-level Host check
    # trades a redundant, transport-only guard for the one the app
    # already documents as authoritative, and is what makes the
    # documented "expose over a tunnel to Claude web/mobile" path work at
    # all. Stdio mode (`cadence mcp`, the default) never goes through
    # this HTTP app and is unaffected either way.
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    instructions=(
        "Cadence is a local-first todo store. Use add_task to create work, "
        "list_tasks to see it, schedule_task to set/change a due date, and "
        "complete_task to mark it done. decompose_task links subtask titles "
        "you already wrote to a parent task (it does not invent the "
        "breakdown itself). reprioritise_task changes an existing task's "
        "priority. decompose_task, reprioritise_task, and schedule_task all "
        "take an optional `reason` string -- pass one whenever you have a "
        "reason, so a person can later ask why_task and see it; it costs "
        "nothing to omit. why_task renders a task's git-backed change "
        "history as a plain-language timeline, including any reasons left "
        "this way. undo reverts the single most recent mutation from any "
        "surface (running it twice returns to the pre-undo state). "
        "sync_tasks syncs this store with a shared remote history and "
        "reports any per-task conflicts, which resolve_sync_conflict "
        "settles; pass all_projects=true to sync every registered project "
        "in one call. export_tasks returns every task, open and done. "
        "register_project adds this store (its current CADENCE_DB_PATH) to "
        "a cross-project registry so overdue_tasks(all_projects=true) and "
        "sync_tasks(all_projects=true) can find it -- call it once per "
        "distinct CADENCE_DB_PATH you use (two projects that never set "
        "CADENCE_DB_PATH share this machine's one default store, so "
        "registering both from there just re-registers that same store) "
        "before using either. All tools return {ok, ...}; on ok=false, "
        "read `error` and `hint` and retry with corrected input rather "
        "than giving up. A write tool (add_task/complete_task/"
        "schedule_task/decompose_task/reprioritise_task) can rarely come "
        "back ok=true with history_recorded=false: the task itself was "
        "saved, only its audit-trail entry wasn't -- read `warning`, do "
        "NOT retry the call (it already succeeded; retrying would create "
        "a duplicate)."
    ),
)

# 0.2.12 Red Team pass, finding #7: this SDK version's FastMCP(...)
# constructor has no `version=` kwarg (only the lower-level mcp.server.
# lowlevel.server.Server it wraps does), so without this line the
# `initialize` handshake's serverInfo.version fell back to the `mcp`
# package's own version ("1.29.1") -- meaningless to an agent trying to
# tell which Cadence feature set it's talking to. Setting the attribute
# `create_initialization_options()` actually reads at request time is the
# only way to reach it through this SDK version's public surface.
mcp._mcp_server.version = _cadence_version


def _err(exc: CadenceError) -> dict:
    return {"ok": False, "error": exc.code, "message": exc.message, "hint": exc.hint}


def _recovered_list(task) -> list:
    """docs/dogfooding-log.md 2026-09-04: `add_task`/`decompose_task` can
    silently absorb an on-disk orphan task file into sqlite (safe since
    0.2.27, but reported nowhere -- the same legibility gap cli.py's
    `_print_recovered` fixes on the human surface). `task.recovered`
    (set by Store.add/Store.decompose, never persisted -- it describes
    this call, not the task) lists what got absorbed, distinct from the
    task/subtasks the caller actually asked for. `[]` for every other
    verb, which never sets it."""
    return [t.to_dict() for t in (getattr(task, "recovered", None) or [])]


def _degraded(task, verb: str, reason: str) -> dict:
    """0.2.12 Red Team finding #1: `task` was already durably written --
    this is SUCCESS with a degraded audit trail, never `ok: false`. An
    agent that only branches on `ok` (per this server's own `initialize`
    instructions) sees success and moves on instead of retrying into a
    silent duplicate; `history_recorded: false` + `warning` are there for
    an agent (or person) that reads further."""
    return {
        "ok": True,
        "task": task.to_dict(),
        "recovered": _recovered_list(task),
        "history_recorded": False,
        "warning": history_degraded_warning(task.id, verb, reason),
    }


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
        "hint": (
            "Unlike a failed sync_tasks or undo, this is not guaranteed to "
            "have rolled back -- run list_tasks to check current state "
            "before retrying, or check CADENCE_DB_PATH."
        ),
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
        created_at, completed_at}, "recovered": [task, ...]} on success,
        or {"ok": false, "error", "message", "hint"} if title is empty,
        over 200 characters, or priority is invalid. `recovered` is
        normally empty; if this call happened to find a stray task file
        already on disk (this store having been used as a passive sync
        relay for another client), it was absorbed into your task list
        as a side effect and is listed here -- it is NOT the task you
        just asked for, that's still `task` above.
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
        return {"ok": True, "task": task.to_dict(), "recovered": _recovered_list(task)}
    except HistoryDegraded as exc:
        return _degraded(exc.tasks[0], "created", exc.reason)
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
def register_project() -> dict:
    """Register this project's store (its current, resolved CADENCE_DB_PATH)
    in the cross-project registry at ~/.config/cadence/projects.txt.

    Call once per project (e.g. once per repo an agent works in) before
    using overdue_tasks(all_projects=true) or sync_tasks(all_projects=true)
    -- both only see stores that have been registered this way. Idempotent:
    calling it again for the same store is a no-op, not a duplicate entry.

    Returns:
        {"ok": true, "path": "<resolved db path>", "already_registered":
        bool}.
    """
    try:
        path, already = _register_project()
        return {"ok": True, "path": path, "already_registered": already}
    except CadenceError as exc:
        return _err(exc)
    except Exception as exc:
        return _err_unexpected(exc)


@mcp.tool()
def overdue_tasks(all_projects: bool = False) -> dict:
    """List overdue (pending, past-due) tasks.

    Args:
        all_projects: If false (default), only this store's own overdue
            tasks. If true, opens every project registered via
            register_project read-only and merges their overdue tasks into
            one list, each tagged with its project name -- no project
            needs to be the "current" one for this to see it.

    Returns:
        With all_projects=false: {"ok": true, "tasks": [task +
        "overdue_days", ...], "count": N}.
        With all_projects=true: {"ok": true, "tasks": [task + "project" +
        "overdue_days", ...], "count": N (tasks only), "projects": M
        (registered project count)}. A registered store that can't be
        opened is reported as {"project", "error", "message", "hint"}
        instead of a task, alongside the rest, rather than failing the
        whole call.
    """
    from cadence.cli import _days_overdue

    try:
        if not all_projects:
            tasks = [t for t in Store().list(status="pending") if t.due and _days_overdue(t.due) > 0]
            out = []
            for t in tasks:
                d = t.to_dict()
                d["overdue_days"] = _days_overdue(t.due)
                out.append(d)
            return {"ok": True, "tasks": out, "count": len(out)}

        entries = read_registry()
        rows = []
        for path in entries:
            name = project_name(path)
            try:
                store = Store(db_path=path, must_exist=True)
            except CadenceError as exc:
                rows.append({"project": name, "error": exc.code, "message": exc.message, "hint": exc.hint})
                continue
            except Exception as exc:
                # 0.2.12 Red Team finding #3: never let one bad registry
                # entry abort the whole multi-project call.
                rows.append({
                    "project": name, "error": "internal_error",
                    "message": f"{type(exc).__name__}: {exc}",
                    "hint": "Check the registry for a corrupt entry and remove it by hand if needed.",
                })
                continue
            for t in store.list(status="pending"):
                if t.due and _days_overdue(t.due) > 0:
                    d = t.to_dict()
                    d["project"] = name
                    d["overdue_days"] = _days_overdue(t.due)
                    rows.append(d)
        count = sum(1 for r in rows if "error" not in r)
        return {"ok": True, "tasks": rows, "count": count, "projects": len(entries)}
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
    except HistoryDegraded as exc:
        return _degraded(exc.tasks[0], "marked done", exc.reason)
    except CadenceError as exc:
        return _err(exc)
    except Exception as exc:
        return _err_unexpected(exc)


@mcp.tool()
def schedule_task(id: int, due: str, reason: Optional[str] = None) -> dict:
    """Set or change a task's due date.

    Args:
        id: Numeric task id, as returned by add_task or list_tasks.
        due: Non-empty ISO date/time string, e.g. "2026-09-01".
        reason: Optional. Why you're setting this due date -- recorded so
            a person can later call why_task(id) and see it. Omit if you
            don't have one; it costs nothing.

    Returns:
        {"ok": true, "task": {...}} with the new due date on success, or
        {"ok": false, "error", "message", "hint"} if id is unknown or due
        is empty.
    """
    try:
        task = Store().schedule(id, due, reason=reason, source="mcp")
        return {"ok": True, "task": task.to_dict()}
    except HistoryDegraded as exc:
        return _degraded(exc.tasks[0], "scheduled", exc.reason)
    except CadenceError as exc:
        return _err(exc)
    except Exception as exc:
        return _err_unexpected(exc)


@mcp.tool()
def decompose_task(id: int, into: list[str], reason: Optional[str] = None) -> dict:
    """Split a task into subtasks by linking titles you already wrote.

    This is a structural primitive, not a planner: Cadence does not invent
    the breakdown -- the caller decides what the subtasks are and passes
    their titles. Bounded so a looping agent can't decompose forever: max
    depth 3, max 20 subtasks per parent (across all decompose calls).

    Args:
        id: Numeric id of the parent task.
        into: Non-empty list of subtask titles (each max 200 characters).
        reason: Optional. Why you're breaking this task down this way --
            recorded so a person can later call why_task(id) (on the parent
            or any subtask) and see it. Omit if you don't have one.

    Returns:
        {"ok": true, "parent": {...}, "subtasks": [task, ...], "recovered":
        [task, ...]} on success, or {"ok": false, "error", "message",
        "hint"} if `into` is empty, the parent is already at max depth, or
        the count would exceed the 20-per-parent cap. `recovered` is
        normally empty; if this call happened to find a stray task file
        already on disk (this store having been used as a passive sync
        relay for another client), it was absorbed into your task list as
        a side effect and is listed here -- it is NOT one of `subtasks`,
        those are still exactly the titles you passed in `into`.
    """
    try:
        parent, children = Store().decompose(id, into, reason=reason, source="mcp")
        return {
            "ok": True,
            "parent": parent.to_dict(),
            "subtasks": [c.to_dict() for c in children],
            "recovered": _recovered_list(parent),
        }
    except HistoryDegraded as exc:
        parent, children = exc.tasks[0], exc.tasks[1:]
        return {
            "ok": True,
            "parent": parent.to_dict(),
            "subtasks": [c.to_dict() for c in children],
            "recovered": _recovered_list(parent),
            "history_recorded": False,
            "warning": history_degraded_warning(parent.id, "decomposed", exc.reason),
        }
    except CadenceError as exc:
        return _err(exc)
    except Exception as exc:
        return _err_unexpected(exc)


@mcp.tool()
def reprioritise_task(id: int, priority: str, reason: Optional[str] = None) -> dict:
    """Change an existing task's priority.

    Distinct from setting priority at creation (add_task's `priority` arg):
    re-prioritising a task that already exists is its own auditable event
    and what `undo` reverts back to the prior priority.

    Args:
        id: Numeric task id, as returned by add_task or list_tasks.
        priority: One of "low", "med", "high".
        reason: Optional. Why this task outranks the others now -- recorded
            so a person can later call why_task(id) and see it instead of
            just watching a number change. Omit if you don't have one.

    Returns:
        {"ok": true, "task": {...}} with the new priority on success, or
        {"ok": false, "error": "invalid_task", "message", "hint"} if id is
        unknown or priority isn't one of the three values.
    """
    try:
        task = Store().reprioritise(id, priority, reason=reason, source="mcp")
        return {"ok": True, "task": task.to_dict()}
    except HistoryDegraded as exc:
        return _degraded(exc.tasks[0], "reprioritised", exc.reason)
    except CadenceError as exc:
        return _err(exc)
    except Exception as exc:
        return _err_unexpected(exc)


@mcp.tool()
def why_task(id: int) -> dict:
    """Show a task's git-backed change history as a plain-language timeline.

    Every mutation (add/decompose/reprioritise/schedule/complete/undo) is
    already a commit; this reads that history back for one task, newest
    first, including any `reason` left on decompose_task/reprioritise_task/
    schedule_task calls and which surface (CLI or MCP) made each change.

    Args:
        id: Numeric task id, as returned by add_task or list_tasks.

    Returns:
        {"ok": true, "task": {...}, "history": [{"event": "<plain-language
        description>", "priority": "<the task's priority right after this
        change>", "at": "<ISO-8601 timestamp>", "reason": str or null,
        "source": "cli"|"mcp"|null}, ...]} newest first, or {"ok": false,
        "error": "task_not_found", "message", "hint"} if the id doesn't
        exist.
    """
    try:
        result = Store().why(id)
        return {
            "ok": True,
            "task": result["task"].to_dict(),
            "history": [
                {
                    "event": ev["event"],
                    "priority": ev["priority"],
                    "at": ev["at"],
                    "reason": ev["reason"],
                    "source": ev["source"],
                }
                for ev in result["events"]
            ],
        }
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
def sync_tasks(remote: Optional[str] = None, all_projects: bool = False) -> dict:
    """Sync this store with a shared remote (another client's history).

    Never silently drops data. Two different things can make a task id
    differ between this store and the remote since the last clean sync:

    - Edited on both sides (same task, changed independently on each
      client): left untouched on both this store and the remote, reported
      by id in `conflicts`. Call resolve_sync_conflict(id, keep="mine"|
      "theirs") for each one, then call sync_tasks again.
    - Independently created with the same id (two unrelated tasks that
      happened to get the same auto-assigned id, since each client
      assigns ids on its own): never a real conflict, so it's resolved
      automatically within this same call -- this client's task keeps its
      id, the other client's task is preserved under a freshly assigned
      id. Reported by id in `renumbered`; nothing to call for these.

    Everything else in the same sync still lands either way.

    Args:
        remote: With all_projects=false (default): the OTHER client's own
            CADENCE_DB_PATH value (its plain .db file path) -- this client
            derives that client's history location itself, so you never
            need to know Cadence's internal storage layout. A git URL also
            works, for a shared server remote. Only needed the first time
            (or to change it) -- omit on later calls to reuse the remote
            already configured.
            With all_projects=true: the path to another client's own
            registry file (its ~/.config/cadence/projects.txt) -- projects
            are matched between the two registries by project name.  Omit
            to reuse whatever remote each project already has configured.
        all_projects: If true, loops over every project registered via
            register_project and syncs each one (see the per-project
            result shape below) instead of just this store.

    Returns (all_projects=false): {"ok": true, "pulled": N, "pushed": N,
        "already_synced": bool, "conflicts": [{"id", "mine", "theirs"}, ...],
        "renumbered": [{"old_id", "new_id", "kept_at_old_id"}, ...],
        "warnings": [str, ...]}.
        {"ok": false, ...} if no remote is configured or it can't be
        reached.

        `warnings`: non-fatal problems noticed but not fatal to this call
        -- e.g. an on-disk task file this store could not read to confirm
        it was safe to clean up, so it was left in place, named here
        instead of silently dropped.

    Returns (all_projects=true): {"ok": true, "projects": M, "results":
        [{"project": name, ...same shape as one non-all_projects sync
        result, or "error"/"message"/"hint" if that project's store
        couldn't be opened, or "skipped": true + "reason" if `remote` was
        given but had no project of that name}, ...]}. Never fails the
        whole call for one project's problem -- read each result's own
        `ok`/`error`/`skipped`.
    """
    if not all_projects:
        try:
            result = Store().sync(remote=remote)
            return {"ok": True, **result}
        except CadenceError as exc:
            return _err(exc)
        except Exception as exc:
            return _err_unexpected(exc)

    entries = read_registry()
    remote_map = {}
    if remote:
        for p in read_projects_file(remote):
            remote_map[project_name(p)] = p
    results = []
    for path in entries:
        name = project_name(path)
        remote_arg = None
        if remote:
            remote_arg = remote_map.get(name)
            if remote_arg is None:
                results.append(
                    {
                        "project": name,
                        "skipped": True,
                        "reason": f"no project named '{name}' in remote registry '{remote}'",
                    }
                )
                continue
        try:
            result = Store(db_path=path, must_exist=True).sync(remote=remote_arg)
            results.append({"project": name, "ok": True, **result})
        except CadenceError as exc:
            results.append({"project": name, "ok": False, "error": exc.code, "message": exc.message, "hint": exc.hint})
        except Exception as exc:
            results.append({"project": name, **_err_unexpected(exc)})
    return {"ok": True, "projects": len(entries), "results": results}


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


def _humanize_arg_validation_error(tool_name: str, exc: _PydanticValidationError) -> dict:
    """Render a pydantic ValidationError raised while FastMCP coerces raw
    JSON args against a tool's schema into the same {ok, error, message,
    hint} shape every other invalid-input path already uses.

    Red Team MCP-stress-pass finding 1: FastMCP validates each tool call's
    arguments against a pydantic model it derives from the function
    signature *before* the tool function (and its own try/except net)
    ever runs -- e.g. add_task(title=12345) never reaches add_task's body
    at all. That validation error used to escape as isError=true with a
    raw pydantic dump (including a https://errors.pydantic.dev/... URL),
    a shape no agent's `ok`-branching code is written to expect.
    """
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ())) or "argument"
        if err.get("type") == "missing":
            parts.append(f"'{loc}' is required")
        else:
            parts.append(f"'{loc}': {err.get('msg', 'is invalid')} (got {err.get('input')!r})")
    detail = "; ".join(parts) if parts else str(exc)
    return {
        "ok": False,
        "error": "invalid_argument",
        "message": f"{tool_name} got a bad argument: {detail}.",
        "hint": (
            "Check each argument's type against the tool's docstring "
            "(e.g. numeric ids as numbers, lists as JSON arrays, not "
            "strings) and retry with corrected input."
        ),
    }


# FastMCP-level net, one layer earlier than _err_unexpected: the tool
# manager's call_tool is what actually invokes arg-schema validation
# (via Tool.run -> fn_metadata.call_fn_with_arg_validation) before any
# tool function body runs, so this is the first point that can see a
# validation failure and re-shape it. Wraps the tool_manager's own
# call_tool rather than monkeypatching the FastMCP/Tool classes
# themselves, so it only changes this server's instance, not the
# installed library.
_orig_manager_call_tool = mcp._tool_manager.call_tool


async def _call_tool_with_validation_net(name, arguments, context=None, convert_result=False):
    try:
        return await _orig_manager_call_tool(
            name, arguments, context=context, convert_result=convert_result
        )
    except _FastMCPToolError as exc:
        cause = exc.__cause__
        if not isinstance(cause, _PydanticValidationError):
            raise  # not the arg-coercion case this net is for -- let it surface as before
        err_dict = _humanize_arg_validation_error(name, cause)
        if convert_result:
            tool = mcp._tool_manager.get_tool(name)
            if tool is not None:
                return tool.fn_metadata.convert_result(err_dict)
        return err_dict


mcp._tool_manager.call_tool = _call_tool_with_validation_net


def run() -> None:
    mcp.run(transport="stdio")


def _classify_envelope_error(status_code: int, message_text: str) -> tuple[str, str]:
    """Map a raw mcp-SDK HTTP-transport-envelope error's status/message to
    a cadence (error_code, hint) pair. `message_text` is the SDK's own
    plain-English message text -- either the `message` field of its raw
    JSON-RPC error object, or (for the 413 case, which isn't JSON at all)
    its bare plain-text body.

    0.2.17 independent Red Team pass (docs/dogfooding-log.md): this used
    to pattern-match on `message_text` only and never looked at
    `status_code`, so a genuine server-side fault (e.g. an uncaught
    exception the SDK's own generic handler turns into a 500 "Error
    handling POST request") fell through every named pattern into the
    same `malformed_request` / "check your request and retry" bucket a
    client's own bad input gets -- wrongly blaming the caller, and
    telling them to retry the one thing (editing their request) that
    cannot help, since the request was never the problem. Any 5xx is
    checked first, before the 4xx pattern matching below, so a server
    fault can never be misread as a client mistake regardless of what
    the SDK's own message text happens to say.
    """
    if status_code >= 500:
        return "server_error", (
            "This failed on the server's side, not because of anything wrong "
            "with your request -- sending the identical request again will "
            "fail the same way. Wait and try again later, or report it; "
            "editing the request will not help."
        )
    lower = message_text.lower()
    if status_code == 413 or "too large" in lower:
        return "request_too_large", (
            "Send a smaller request body -- split it into multiple calls "
            "if needed."
        )
    if "parse error" in lower:
        return "malformed_json", "Send a single well-formed JSON object as the request body."
    if "not acceptable" in lower:
        return "not_acceptable", (
            "Send an 'Accept: application/json, text/event-stream' header on every request."
        )
    if "validation error" in lower or "field required" in lower:
        return "invalid_request", (
            "Include the required JSON-RPC fields ('jsonrpc', 'id', 'method') in the request body."
        )
    if "session" in lower:
        return "session_error", "Start a new session with 'initialize' and retry."
    return "malformed_request", (
        "Check the request against the MCP Streamable HTTP spec (headers, JSON-RPC body shape) and retry."
    )


def _clean_sdk_message(text: str) -> str:
    """Strip the pydantic-errors-URL boilerplate the SDK's own validation
    messages append (e.g. "...For further information visit
    https://errors.pydantic.dev/...") and collapse embedded newlines, so
    the message a client reads is the same plain-English sentence a
    cadence-raised error would give -- without inventing new wording for
    what is still, honestly, the SDK's own message."""
    text = text.split("For further information visit", 1)[0].strip()
    return " ".join(text.split())


def _envelope_error_shape(status_code: int, body: bytes, content_type: str) -> Optional[dict]:
    """Translate a raw mcp-SDK-level HTTP error response -- one rejected
    by the SDK's own request parsing before a tool was ever invoked, so it
    never went through cadence's own {ok, error, message, hint} tool-
    response path -- into that same contract.

    Returns None if `body` is not one of the SDK's raw shapes (e.g. it's
    already cadence-shaped, such as BearerAuth's own 401), so the caller
    knows to forward it untouched rather than double-wrap it.
    """
    text = body.decode("utf-8", errors="replace")
    message = text
    if "json" in content_type:
        try:
            payload = json.loads(text)
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            if "ok" in payload:
                return None
            err = payload.get("error")
            if isinstance(err, dict) and "message" in err:
                message = err["message"]
    message = _clean_sdk_message(message)
    error_code, hint = _classify_envelope_error(status_code, message)
    return {"ok": False, "error": error_code, "message": message, "hint": hint}


def _make_http_app(token: str):
    """Wrap the stock MCP Streamable HTTP ASGI app with a bearer-token
    check, so `cadence mcp --http` is safe to bind on an interface other
    than localhost.

    This is a second transport for the exact same tool surface and the
    exact same Store -- no accounts, no multi-tenant backend, no server we
    run for anyone. The user starts this on their own machine with their
    own token and is responsible for how they expose the port (SSH tunnel,
    Tailscale, a TLS-terminating reverse proxy, etc.); this layer's only
    job is to reject any request that doesn't present that exact token,
    using the same {ok, error, message, hint} contract every other tool
    failure uses, so a remote agent that gets it wrong can tell why.
    """
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.types import ASGIApp, Receive, Scope, Send

    class _EnvelopeErrorShim:
        """Sits between BearerAuth and the raw mcp SDK app. 0.2.12 Red
        Team pass, finding #6 (docs/dogfooding-log.md): once inside a
        valid session, a genuine tool-level error correctly comes back
        cadence-shaped -- confirmed working -- but anything malformed
        enough to be rejected by the SDK's own request handling before a
        tool is ever invoked (bad JSON body, missing `method`, missing
        Accept header, oversized body) returned the SDK's raw JSON-RPC/
        plain-text error instead, a shape this server's own `initialize`
        response never told a client to expect.

        Only error responses (status >= 400) are touched, and only once
        fully buffered -- those are always single Response objects, never
        streams. Success/SSE responses (status < 400) are forwarded
        message-for-message, unbuffered, exactly as they arrive: this
        shim must never hold up or alter a live streaming reply.
        """

        def __init__(self, app: ASGIApp) -> None:
            self.app = app

        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] != "http":
                await self.app(scope, receive, send)
                return

            state: dict = {"status": 200, "headers": [], "buffer": bytearray(), "start": None}

            async def wrapped_send(message: dict) -> None:
                if message["type"] == "http.response.start":
                    state["status"] = message["status"]
                    state["headers"] = message.get("headers", [])
                    if state["status"] < 400:
                        await send(message)
                        return
                    state["start"] = message  # hold until the body is fully seen
                    return
                if message["type"] == "http.response.body":
                    if state["status"] < 400:
                        await send(message)
                        return
                    state["buffer"].extend(message.get("body", b""))
                    if message.get("more_body"):
                        return
                    content_type = next(
                        (v.decode("latin-1") for k, v in state["headers"] if k == b"content-type"),
                        "",
                    )
                    shaped = _envelope_error_shape(state["status"], bytes(state["buffer"]), content_type)
                    if shaped is None:
                        await send(state["start"])
                        await send({"type": "http.response.body", "body": bytes(state["buffer"])})
                        return
                    body_bytes = json.dumps(shaped).encode("utf-8")
                    new_headers = [
                        (k, v) for k, v in state["headers"] if k not in (b"content-type", b"content-length")
                    ]
                    new_headers.append((b"content-type", b"application/json"))
                    new_headers.append((b"content-length", str(len(body_bytes)).encode("latin-1")))
                    await send({"type": "http.response.start", "status": state["status"], "headers": new_headers})
                    await send({"type": "http.response.body", "body": body_bytes})
                    return
                await send(message)

            await self.app(scope, receive, wrapped_send)

    inner: ASGIApp = _EnvelopeErrorShim(mcp.streamable_http_app())

    class BearerAuth:
        def __init__(self, app: ASGIApp, expected_token: str) -> None:
            self.app = app
            self.expected_token = expected_token

        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] != "http":
                await self.app(scope, receive, send)
                return
            headers = dict(scope.get("headers") or [])
            auth = headers.get(b"authorization", b"").decode("latin-1")
            presented = auth[7:] if auth.startswith("Bearer ") else None
            # hmac.compare_digest, not `!=`: a plain string comparison
            # short-circuits on the first mismatched byte, a theoretical
            # timing side-channel an attacker could use to recover the
            # token one byte at a time (0.2.12 Red Team pass, unverified
            # hunch). compare_digest runs in constant time regardless of
            # where the strings first differ.
            if presented is None or not hmac.compare_digest(presented, self.expected_token):
                response = JSONResponse(
                    {
                        "ok": False,
                        "error": "unauthorized",
                        "message": "Missing or wrong bearer token.",
                        "hint": "Send 'Authorization: Bearer <token>' matching "
                        "the token this server was started with (see "
                        "`cadence mcp --http --show-token`).",
                    },
                    status_code=401,
                )
                await response(scope, receive, send)
                return
            await self.app(scope, receive, send)

    return BearerAuth(inner, token)


def run_http(host: str, port: int, token: str) -> None:
    """Serve the same tool surface over the MCP Streamable HTTP transport
    at http://{host}:{port}/mcp, guarded by `token`.

    Local-first still: this binds a port on the machine it runs on, backed
    by the same on-disk Store as `cadence mcp` (stdio) and the `cadence`
    CLI -- one store, a second way to reach it. It does not add TLS; a
    remote client (Claude web/mobile, or an agent on another machine)
    reaches this either over a private network or through a tunnel/reverse
    proxy the operator sets up themselves.
    """
    import sys

    import uvicorn

    app = _make_http_app(token)
    print(
        f"[cadence mcp --http] listening on http://{host}:{port}/mcp -- "
        "bearer token required on every request (see "
        "`cadence mcp --http --show-token` to print it).",
        file=sys.stderr,
    )
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    run()
