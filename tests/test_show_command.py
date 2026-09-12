"""Tests for `cadence show <id>` / `show_task` (R-07 dogfooding finding,
2026-09-11): a single-item view of a task's current fields, distinct from
`list` (whole tree) and `why` (history, not current state).

Each surface (CLI, MCP) is exercised, per project rule -- a capability
that only works on one surface is caught here.
"""
import os
import subprocess
import sys

import pytest

from cadence.store import Store


def _cli_env(tmp_path, name="cli.db"):
    return {**os.environ, "CADENCE_DB_PATH": str(tmp_path / name), "NO_COLOR": "1"}


def _run_cli(*args, env):
    return subprocess.run(
        [sys.executable, "-m", "cadence.cli", *args],
        capture_output=True,
        text=True,
        env=env,
    )


# --- CLI: cadence show -------------------------------------------------------


def test_cli_show_valid_id_prints_current_fields(tmp_path):
    env = _cli_env(tmp_path)
    assert _run_cli("add", "Buy milk", "--due", "2026-09-20", "--priority", "high", env=env).returncode == 0
    result = _run_cli("show", "1", env=env)
    assert result.returncode == 0
    out = result.stdout
    assert out.startswith("#1 Buy milk\n")
    assert "status:    pending" in out
    assert "priority:  high" in out
    assert "due:       2026-09-20" in out
    # no task exists to be a parent or a subtask
    assert "parent:" not in out
    assert "subtasks:" not in out


def test_cli_show_task_with_no_due_or_priority_prints_none_placeholders(tmp_path):
    env = _cli_env(tmp_path)
    _run_cli("add", "Buy milk", env=env)
    out = _run_cli("show", "1", env=env).stdout
    assert "priority:  none" in out
    assert "due:       (none)" in out


def test_cli_show_unknown_id_matches_444_wording(tmp_path):
    env = _cli_env(tmp_path)
    result = _run_cli("show", "99", env=env)
    assert result.returncode == 1
    assert result.stdout.startswith("Error: no task with id 99. Run 'cadence list' to see valid ids.")


def test_cli_show_non_numeric_id_names_the_bad_id(tmp_path):
    env = _cli_env(tmp_path)
    result = _run_cli("show", "abc", env=env)
    assert result.returncode == 1
    assert result.stdout.startswith("Error: 'abc' is not a task id. Run 'cadence list' to see valid ids.")


def test_cli_show_parent_lists_its_subtasks(tmp_path):
    env = _cli_env(tmp_path)
    _run_cli("add", "Plan the party", env=env)
    _run_cli("decompose", "1", "--into", "Book a venue", "Order a cake", env=env)
    _run_cli("done", "2", env=env)
    out = _run_cli("show", "1", env=env).stdout
    assert "subtasks:  #2 (done), #3 (pending)" in out
    assert "parent:" not in out


def test_cli_show_subtask_names_its_parent(tmp_path):
    env = _cli_env(tmp_path)
    _run_cli("add", "Plan the party", env=env)
    _run_cli("decompose", "1", "--into", "Book a venue", env=env)
    out = _run_cli("show", "2", env=env).stdout
    assert out.startswith("#2 Book a venue\n")
    assert "parent:    #1 (Plan the party)" in out
    assert "subtasks:" not in out


# --- MCP: show_task -----------------------------------------------------


def test_mcp_show_task_valid_id(tmp_path, monkeypatch):
    monkeypatch.setenv("CADENCE_DB_PATH", str(tmp_path / "mcp.db"))
    from cadence.mcp_server import add_task, show_task

    added = add_task("Buy milk", due="2026-09-20", priority="high")
    task_id = added["task"]["id"]
    result = show_task(task_id)
    assert result["ok"] is True
    assert result["task"]["id"] == task_id
    assert result["task"]["title"] == "Buy milk"
    assert result["task"]["priority"] == "high"
    assert result["task"]["due"] == "2026-09-20"
    assert result["parent"] is None
    assert result["subtasks"] == []


def test_mcp_show_task_missing_id_is_structured_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("CADENCE_DB_PATH", str(tmp_path / "mcp2.db"))
    from cadence.mcp_server import show_task

    missing = show_task(999)
    assert missing["ok"] is False
    assert missing["error"] == "task_not_found"
    assert "no task with id 999" in missing["message"]
    assert missing["hint"]


def test_mcp_show_task_reports_parent_and_subtask_links(tmp_path, monkeypatch):
    monkeypatch.setenv("CADENCE_DB_PATH", str(tmp_path / "mcp3.db"))
    from cadence.mcp_server import add_task, complete_task, decompose_task, show_task

    added = add_task("Plan the party")
    parent_id = added["task"]["id"]
    decomposed = decompose_task(parent_id, ["Book a venue", "Order a cake"])
    child_ids = [t["id"] for t in decomposed["subtasks"]]
    complete_task(child_ids[0])

    parent_view = show_task(parent_id)
    assert parent_view["ok"] is True
    assert parent_view["parent"] is None
    assert {s["id"]: s["status"] for s in parent_view["subtasks"]} == {
        child_ids[0]: "done",
        child_ids[1]: "pending",
    }

    child_view = show_task(child_ids[0])
    assert child_view["ok"] is True
    assert child_view["parent"] == {"id": parent_id, "title": "Plan the party"}
    assert child_view["subtasks"] == []
