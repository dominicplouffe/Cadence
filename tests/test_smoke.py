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
