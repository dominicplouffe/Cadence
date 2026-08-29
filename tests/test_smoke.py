"""Smoke tests: store, CLI, and MCP tool wiring all work end to end.

Each test uses a fresh scratch DB (tmp_path) via CADENCE_DB_PATH so nothing
here touches a real user's ~/.cadence store.
"""
import os
import subprocess
import sys

import pytest

from cadence.store import CadenceError, InvalidTask, Store, TaskNotFound


@pytest.fixture
def store(tmp_path):
    return Store(db_path=tmp_path / "cadence.db")


def test_add_and_list(store):
    task = store.add("Write the smoke test", priority="high")
    assert task.id == 1
    assert task.status == "pending"

    pending = store.list(status="pending")
    assert [t.title for t in pending] == ["Write the smoke test"]


def test_complete(store):
    task = store.add("Ship it")
    done = store.complete(task.id)
    assert done.status == "done"
    assert done.completed_at is not None

    assert store.list(status="pending") == []
    assert [t.id for t in store.list(status="done")] == [task.id]


def test_schedule(store):
    task = store.add("Plan the release")
    scheduled = store.schedule(task.id, "2026-11-06")
    assert scheduled.due == "2026-11-06"


def test_complete_unknown_id_raises_named_error(store):
    with pytest.raises(TaskNotFound):
        store.complete(999)


def test_add_empty_title_raises_named_error(store):
    with pytest.raises(InvalidTask):
        store.add("   ")


def test_malformed_priority_is_recoverable(store):
    # A malformed request should be a named, catchable error with a hint,
    # not a crash -- this is what the ten-step script's recovery step needs.
    with pytest.raises(CadenceError) as excinfo:
        store.add("Bad priority", priority="urgent!!")
    assert excinfo.value.hint  # a hint must be present to be agent-legible


def test_mcp_tools_return_structured_dicts(tmp_path, monkeypatch):
    monkeypatch.setenv("CADENCE_DB_PATH", str(tmp_path / "mcp.db"))
    from cadence.mcp_server import add_task, complete_task, list_tasks, schedule_task

    added = add_task("From MCP", priority="low")
    assert added["ok"] is True
    task_id = added["task"]["id"]

    listed = list_tasks(status="all")
    assert listed["ok"] is True
    assert listed["count"] == 1

    scheduled = schedule_task(task_id, "2026-09-01")
    assert scheduled["ok"] is True
    assert scheduled["task"]["due"] == "2026-09-01"

    completed = complete_task(task_id)
    assert completed["ok"] is True
    assert completed["task"]["status"] == "done"

    missing = complete_task(4242)
    assert missing["ok"] is False
    assert missing["error"] == "task_not_found"
    assert missing["hint"]


def test_cli_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("CADENCE_DB_PATH", str(tmp_path / "cli.db"))
    env = {**__import__("os").environ, "CADENCE_DB_PATH": str(tmp_path / "cli.db")}

    add = subprocess.run(
        [sys.executable, "-m", "cadence.cli", "add", "From CLI", "--priority", "high"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert add.returncode == 0, add.stderr
    assert "Added #1: From CLI" in add.stdout

    listing = subprocess.run(
        [sys.executable, "-m", "cadence.cli", "list"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert listing.returncode == 0
    assert "From CLI" in listing.stdout

    done = subprocess.run(
        [sys.executable, "-m", "cadence.cli", "done", "1"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert done.returncode == 0, done.stderr
    assert "Done #1" in done.stdout

    bad = subprocess.run(
        [sys.executable, "-m", "cadence.cli", "done", "999"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert bad.returncode == 1
    assert bad.stdout.startswith("Error: no task with id 999")

    empty_add = subprocess.run(
        [sys.executable, "-m", "cadence.cli", "add"],
        capture_output=True,
        text=True,
        env={**env, "CADENCE_DB_PATH": str(tmp_path / "cli_empty.db")},
    )
    assert empty_add.returncode == 1
    assert "Try: cadence add" in empty_add.stdout


# --- Regression tests: Red Team pass-1 findings #1 (critical), #2 (high),
# and #4 (low-medium), all confirmed reproduced against the shipped wheel
# in pass-2. See /workspace/redteam_run1/findings_pass1.md and
# findings_pass2.md for the original repros these are drawn from.


def test_schedule_rejects_invalid_due_store_side(store):
    # #1: the agent surface (schedule_task) must be rejected by the *store*,
    # not only by the CLI's own pre-check -- this calls Store.schedule
    # directly, bypassing the CLI entirely, the way MCP's schedule_task does.
    task = store.add("Ship it")
    with pytest.raises(InvalidTask):
        store.schedule(task.id, "banana")


def test_add_rejects_invalid_due_store_side(store):
    with pytest.raises(InvalidTask):
        store.add("Bad due", due="banana")


def test_mcp_schedule_task_rejects_bad_due_and_list_survives(tmp_path, monkeypatch):
    # The actual pass-1/pass-2 repro: schedule_task used to write the
    # garbage due date, then every future list_tasks/`cadence list` crashed
    # forever with no recovery. Confirms both halves: the write is rejected,
    # and a subsequent list still works.
    monkeypatch.setenv("CADENCE_DB_PATH", str(tmp_path / "mcp_due.db"))
    from cadence.mcp_server import add_task, list_tasks, schedule_task

    task_id = add_task("test")["task"]["id"]

    result = schedule_task(task_id, "banana")
    assert result["ok"] is False
    assert result["error"] == "invalid_task"
    assert "banana" in result["message"]
    assert result["hint"]

    listed = list_tasks(status="all")
    assert listed["ok"] is True
    assert listed["tasks"][0]["due"] is None


def test_mcp_add_task_rejects_bad_due(tmp_path, monkeypatch):
    monkeypatch.setenv("CADENCE_DB_PATH", str(tmp_path / "mcp_add_due.db"))
    from cadence.mcp_server import add_task

    result = add_task("garbage due via add", due="banana")
    assert result["ok"] is False
    assert result["error"] == "invalid_task"


def test_overflow_id_raises_named_error_not_a_crash(store):
    # #2 case (a)/(d): a huge id (e.g. copy-pasted from the wrong field)
    # previously raised a raw OverflowError out of sqlite3's C binding
    # instead of the same "not found" a normal unknown id gets.
    huge_id = 999999999999999999999999
    with pytest.raises(TaskNotFound):
        store.complete(huge_id)


def test_mcp_complete_task_overflow_id_is_structured(tmp_path, monkeypatch):
    monkeypatch.setenv("CADENCE_DB_PATH", str(tmp_path / "mcp_overflow.db"))
    from cadence.mcp_server import complete_task

    result = complete_task(999999999999999999999999)
    assert result["ok"] is False
    assert result["error"] == "task_not_found"


def test_cli_overflow_id_returns_structured_error_not_traceback(tmp_path):
    env = {**os.environ, "CADENCE_DB_PATH": str(tmp_path / "cli_overflow.db")}
    result = subprocess.run(
        [sys.executable, "-m", "cadence.cli", "done", "999999999999999999999999"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 1, result.stderr
    assert result.stdout.startswith("Error: no task with id")
    assert "Traceback" not in result.stderr
    assert "Traceback" not in result.stdout


def test_cli_bad_db_path_directory_returns_structured_error_not_traceback(tmp_path):
    # #2 case (b): CADENCE_DB_PATH pointing at a directory instead of a file.
    bad_dir = tmp_path / "not_a_file"
    bad_dir.mkdir()
    env = {**os.environ, "CADENCE_DB_PATH": str(bad_dir)}
    result = subprocess.run(
        [sys.executable, "-m", "cadence.cli", "list"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 2, result.stderr
    assert result.stdout.startswith("Error: ")
    assert "Traceback" not in result.stderr
    assert "Traceback" not in result.stdout


def test_store_bad_db_path_directory_raises_named_error(tmp_path):
    from cadence.store import StoreUnavailable

    bad_dir = tmp_path / "another_dir"
    bad_dir.mkdir()
    with pytest.raises(StoreUnavailable):
        Store(db_path=bad_dir)


def test_task_not_found_hint_points_at_a_real_command(store):
    # #4: the hint used to say `cadence list --all`, a flag that doesn't
    # exist.
    with pytest.raises(TaskNotFound) as excinfo:
        store.complete(999)
    assert excinfo.value.hint == "Run 'cadence list' to see valid ids."


def test_cli_add_empty_priority_exits_1_not_2(tmp_path):
    # Red Team pass-3 addendum: `--priority ""` is falsy, so the CLI's own
    # `if args.priority and ...` fast-path pre-check (a user-input error,
    # correctly exit 1) never runs, and store.add() raises InvalidTask --
    # still a user-input error, so it must still exit 1 per §4.4, not the
    # store/internal exit code 2 that cmd_add used to hardcode for every
    # CadenceError it caught.
    env = {**os.environ, "CADENCE_DB_PATH": str(tmp_path / "cli_empty_priority.db")}
    result = subprocess.run(
        [sys.executable, "-m", "cadence.cli", "add", "x", "--priority", ""],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 1, result.stderr
    assert result.stdout.startswith("Error: ")
    assert "priority" in result.stdout


# --- Regression tests: Red Team pass-3 finding #5 (title has no max
# length; a 5000-char repro broke `cadence list`'s table layout). 200
# chars matches the longest title docs/human-surface.md actually tested
# wrap behavior against.


def test_add_rejects_over_length_title_store_side(store):
    with pytest.raises(InvalidTask):
        store.add("x" * 201)


def test_add_accepts_title_at_max_length_store_side(store):
    task = store.add("y" * 200)
    assert len(task.title) == 200


def test_mcp_add_task_rejects_over_length_title(tmp_path, monkeypatch):
    monkeypatch.setenv("CADENCE_DB_PATH", str(tmp_path / "mcp_title_len.db"))
    from cadence.mcp_server import add_task

    result = add_task("x" * 201)
    assert result["ok"] is False
    assert result["error"] == "invalid_task"
    assert "201" in result["message"]
    assert "200" in result["message"]
    assert result["hint"]


def test_mcp_add_task_accepts_title_at_max_length(tmp_path, monkeypatch):
    monkeypatch.setenv("CADENCE_DB_PATH", str(tmp_path / "mcp_title_ok.db"))
    from cadence.mcp_server import add_task

    result = add_task("y" * 200)
    assert result["ok"] is True
    assert len(result["task"]["title"]) == 200


def test_cli_add_over_length_title_exits_1(tmp_path):
    env = {**os.environ, "CADENCE_DB_PATH": str(tmp_path / "cli_title_len.db")}
    result = subprocess.run(
        [sys.executable, "-m", "cadence.cli", "add", "x" * 201],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 1, result.stderr
    assert result.stdout.startswith("Error: title is 201 characters, max 200.")
    assert "Try a shorter one." in result.stdout


def test_cli_add_title_at_max_length_succeeds(tmp_path):
    env = {**os.environ, "CADENCE_DB_PATH": str(tmp_path / "cli_title_ok.db")}
    result = subprocess.run(
        [sys.executable, "-m", "cadence.cli", "add", "y" * 200],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("Added #1:")


def test_cli_add_bad_db_path_directory_still_exits_2(tmp_path):
    # A genuine store failure reaching cmd_add's except CadenceError handler
    # (StoreUnavailable) must still be the internal/store exit code, 2 --
    # the fix must not turn every CadenceError into exit 1.
    bad_dir = tmp_path / "not_a_file_for_add"
    bad_dir.mkdir()
    env = {**os.environ, "CADENCE_DB_PATH": str(bad_dir)}
    result = subprocess.run(
        [sys.executable, "-m", "cadence.cli", "add", "x"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 2, result.stderr
    assert result.stdout.startswith("Error: ")


# --- Regression tests: Red Team MCP-stress-pass (docs/dogfooding-log.md,
# commit d00f9ca; raw evidence /workspace/redteam_mcp_stress/results.jsonl),
# findings 1 and 2. Both go through the real FastMCP call_tool path (the
# tool_manager, not the bare Python function -- calling e.g. add_task(...)
# directly never exercises FastMCP's own arg-schema validation, which is
# exactly where finding 1 lived), matching what an agent actually sees.


def _call_mcp_tool(name: str, arguments: dict) -> dict:
    """Drive a tool the same way FastMCP's protocol layer does (schema
    validation included), and unwrap the JSON text content back to a dict
    -- what an agent parses out of the tool result."""
    import asyncio
    import json

    from cadence.mcp_server import mcp

    async def _run():
        return await mcp._tool_manager.call_tool(name, arguments, convert_result=True)

    result = asyncio.run(_run())
    # convert_result=True with no output_schema returns unstructured content:
    # a list of content blocks, text ones carrying the tool's JSON string.
    for block in result:
        text = getattr(block, "text", None)
        if text is not None:
            return json.loads(text)
    raise AssertionError(f"no text content block in {result!r}")


@pytest.mark.parametrize(
    "tool,bad_args",
    [
        ("add_task", {"title": 12345}),
        ("add_task", {"title": True}),
        ("add_task", {}),
        ("schedule_task", {"id": "abc", "due": "2026-09-01"}),
        ("schedule_task", {"id": 1.5, "due": "2026-09-01"}),
        ("decompose_task", {"id": 2, "into": "not-a-list"}),
    ],
)
def test_mcp_type_mismatched_args_return_structured_error_not_pydantic_dump(
    tmp_path, monkeypatch, tool, bad_args
):
    # Red Team finding 1: a wrong JSON *type* (or a missing required field)
    # used to bypass {ok, error, message, hint} entirely -- FastMCP
    # validates args against the tool's schema *before* the tool function
    # (and its own try/except net) ever runs, so it raised a raw pydantic
    # ValidationError (with a https://errors.pydantic.dev/... URL in it)
    # instead. This must come back as ordinary structured error content,
    # not isError with a stack-trace-shaped string.
    monkeypatch.setenv("CADENCE_DB_PATH", str(tmp_path / "mcp_typeerr.db"))
    result = _call_mcp_tool(tool, bad_args)
    assert result["ok"] is False
    assert result["error"] == "invalid_argument"
    assert "pydantic" not in result["message"].lower()
    assert "errors.pydantic.dev" not in result["message"]
    assert result["hint"]


def test_mcp_sync_remote_bare_non_git_directory_is_rejected(tmp_path, monkeypatch):
    # Red Team finding 2: sync_tasks(remote=<plain dir, not a git repo>)
    # used to be silently accepted (ok:true, pushed:N) -- it fell through
    # _resolve_remote's peer-db-path derivation, silently creating a new
    # sibling `<dirname>.history` git repo and pushing local tasks into a
    # location nobody will ever sync from again. Must be rejected instead,
    # with no sibling history repo created as a side effect.
    monkeypatch.setenv("CADENCE_DB_PATH", str(tmp_path / "mcp_syncdir.db"))
    add_result = _call_mcp_tool("add_task", {"title": "will not be pushed"})
    assert add_result["ok"] is True

    plain_dir = tmp_path / "not_a_repo"
    plain_dir.mkdir()

    sync_result = _call_mcp_tool("sync_tasks", {"remote": str(plain_dir)})
    assert sync_result["ok"] is False
    assert sync_result["error"] == "invalid_task"
    assert sync_result["hint"]
    sibling_history = tmp_path / "not_a_repo.history"
    assert not sibling_history.exists(), (
        "sync_tasks must not create a sibling .history repo for a "
        "rejected non-git remote"
    )
