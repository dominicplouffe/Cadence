"""Smoke tests: store, CLI, and MCP tool wiring all work end to end.

Each test uses a fresh scratch DB (tmp_path) via CADENCE_DB_PATH so nothing
here touches a real user's ~/.cadence store.
"""
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
