"""Tests for the five R-08 verbs (decompose, reprioritise, undo, sync,
export) and the list-ordering regression Red Team pass-7 found.

Each surface (store, CLI, MCP) is exercised for every verb so a capability
that only works on one surface is caught here, per project rule.
"""
import json
import os
import subprocess
import sys

import pytest

from cadence.store import CadenceError, InvalidTask, Store, TaskNotFound


@pytest.fixture
def store(tmp_path):
    return Store(db_path=tmp_path / "cadence.db")


def _cli_env(tmp_path, name="cli.db"):
    return {**os.environ, "CADENCE_DB_PATH": str(tmp_path / name)}


def _run_cli(*args, env):
    return subprocess.run(
        [sys.executable, "-m", "cadence.cli", *args],
        capture_output=True,
        text=True,
        env=env,
    )


# --- list ordering regression (Red Team pass-7 / R-06 finding #3) ---------


def test_list_orders_pending_by_priority_then_id(store):
    low = store.add("low prio, added first", priority="low")
    high = store.add("high prio, added second", priority="high")
    none = store.add("no prio, added third")
    med = store.add("med prio, added fourth", priority="med")

    pending = store.list(status="pending")
    assert [t.id for t in pending] == [high.id, med.id, low.id, none.id]


def test_list_orders_ties_within_priority_by_id(store):
    a = store.add("high A", priority="high")
    b = store.add("high B", priority="high")
    pending = store.list(status="pending")
    assert [t.id for t in pending] == [a.id, b.id]


def test_list_status_all_puts_done_after_pending_regardless_of_priority(store):
    t1 = store.add("will finish", priority="low")
    t2 = store.add("stays open", priority="high")
    store.complete(t1.id)
    all_tasks = store.list(status="all")
    assert [t.id for t in all_tasks] == [t2.id, t1.id]


def test_mcp_list_tasks_reflects_priority_ordering(tmp_path, monkeypatch):
    monkeypatch.setenv("CADENCE_DB_PATH", str(tmp_path / "mcp_order.db"))
    from cadence.mcp_server import add_task, list_tasks

    low = add_task("low", priority="low")["task"]
    high = add_task("high", priority="high")["task"]
    listed = list_tasks(status="pending")
    assert [t["id"] for t in listed["tasks"]] == [high["id"], low["id"]]


def test_cli_list_renders_priority_ordering(tmp_path):
    env = _cli_env(tmp_path, "cli_order.db")
    _run_cli("add", "low one", "--priority", "low", env=env)
    _run_cli("add", "high one", "--priority", "high", env=env)
    listing = _run_cli("list", env=env)
    assert listing.returncode == 0, listing.stderr
    # "high one" (id 2) must render before "low one" (id 1) despite being
    # added second -- this is the ordering bug's user-visible symptom.
    assert listing.stdout.index("high one") < listing.stdout.index("low one")


# --- decompose --------------------------------------------------------


def test_decompose_store_links_subtasks(store):
    parent = store.add("Bake a cake")
    parent2, children = store.decompose(parent.id, ["Buy flour", "Buy eggs"])
    assert parent2.id == parent.id
    assert [c.title for c in children] == ["Buy flour", "Buy eggs"]
    assert all(c.parent_id == parent.id for c in children)
    assert [t.id for t in store.subtasks(parent.id)] == [c.id for c in children]


def test_decompose_needs_at_least_one_title(store):
    parent = store.add("Bake a cake")
    with pytest.raises(InvalidTask):
        store.decompose(parent.id, [])


def test_decompose_rejects_unknown_parent(store):
    with pytest.raises(TaskNotFound):
        store.decompose(999, ["x"])


def test_decompose_rejects_over_20_subtasks_in_one_call(store):
    parent = store.add("Bake a cake")
    with pytest.raises(InvalidTask):
        store.decompose(parent.id, [f"t{i}" for i in range(21)])


def test_decompose_rejects_exceeding_cap_across_calls(store):
    parent = store.add("Bake a cake")
    store.decompose(parent.id, [f"t{i}" for i in range(15)])
    with pytest.raises(InvalidTask):
        store.decompose(parent.id, [f"more{i}" for i in range(10)])


def test_decompose_rejects_depth_beyond_three(store):
    top = store.add("L0")
    _, l1 = store.decompose(top.id, ["L1"])
    _, l2 = store.decompose(l1[0].id, ["L2"])
    _, l3 = store.decompose(l2[0].id, ["L3"])
    with pytest.raises(InvalidTask):
        store.decompose(l3[0].id, ["too deep"])


def test_mcp_decompose_task_success_and_error(tmp_path, monkeypatch):
    monkeypatch.setenv("CADENCE_DB_PATH", str(tmp_path / "mcp_decompose.db"))
    from cadence.mcp_server import add_task, decompose_task

    parent_id = add_task("Bake a cake")["task"]["id"]
    result = decompose_task(parent_id, ["Buy flour", "Buy eggs"])
    assert result["ok"] is True
    assert len(result["subtasks"]) == 2
    assert result["subtasks"][0]["parent_id"] == parent_id

    empty = decompose_task(parent_id, [])
    assert empty["ok"] is False
    assert empty["error"] == "invalid_task"
    assert empty["hint"]


def test_cli_decompose_success(tmp_path):
    env = _cli_env(tmp_path, "cli_decompose.db")
    add = _run_cli("add", "Bake a cake", env=env)
    parent_id = add.stdout.split("#")[1].split(":")[0]

    result = _run_cli(
        "decompose", parent_id, "--into", "Buy flour", "Buy eggs", env=env
    )
    assert result.returncode == 0, result.stderr
    assert f"Decomposed #{parent_id} into 2 subtasks:" in result.stdout

    listing = _run_cli("list", env=env)
    assert "Buy flour" in listing.stdout
    assert "1 open subtasks" not in listing.stdout  # 2 open, not 1
    assert "2 open subtasks" in listing.stdout


def test_cli_decompose_without_into_is_recoverable_error(tmp_path):
    env = _cli_env(tmp_path, "cli_decompose_err.db")
    add = _run_cli("add", "Bake a cake", env=env)
    parent_id = add.stdout.split("#")[1].split(":")[0]

    result = _run_cli("decompose", parent_id, env=env)
    assert result.returncode == 1
    assert result.stdout.startswith("Error: 'decompose' needs at least one subtask.")
    assert "cadence decompose" in result.stdout


# --- reprioritise -------------------------------------------------------


def test_reprioritise_store(store):
    task = store.add("Ship it", priority="low")
    updated = store.reprioritise(task.id, "high")
    assert updated.priority == "high"


def test_reprioritise_rejects_invalid_priority(store):
    task = store.add("Ship it")
    with pytest.raises(InvalidTask):
        store.reprioritise(task.id, "urgent")


def test_reprioritise_rejects_unknown_id(store):
    with pytest.raises(TaskNotFound):
        store.reprioritise(999, "high")


def test_mcp_reprioritise_task(tmp_path, monkeypatch):
    monkeypatch.setenv("CADENCE_DB_PATH", str(tmp_path / "mcp_repri.db"))
    from cadence.mcp_server import add_task, reprioritise_task

    task_id = add_task("Ship it")["task"]["id"]
    result = reprioritise_task(task_id, "high")
    assert result["ok"] is True
    assert result["task"]["priority"] == "high"

    bad = reprioritise_task(task_id, "urgent")
    assert bad["ok"] is False
    assert bad["error"] == "invalid_task"


def test_cli_reprioritise_success_and_error(tmp_path):
    env = _cli_env(tmp_path, "cli_repri.db")
    _run_cli("add", "Ship it", env=env)

    result = _run_cli("reprioritise", "1", "high", env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("Reprioritised #1 (none → high):")

    bad = _run_cli("reprioritise", "1", "urgent", env=env)
    assert bad.returncode == 1
    assert bad.stdout.startswith("Error: 'urgent' isn't a priority.")
    assert "cadence reprioritise 1 high" in bad.stdout


# --- undo -----------------------------------------------------------


def test_undo_reverts_last_add(store):
    store.add("Ship it")
    assert len(store.list(status="all")) == 1
    summary = store.undo()
    assert "removed" in summary
    assert store.list(status="all") == []


def test_undo_is_symmetric_undo_of_undo_is_redo(store):
    task = store.add("Ship it")
    store.complete(task.id)
    store.undo()  # reopen
    assert store.get(task.id).status == "pending"
    store.undo()  # undo the undo -> done again
    assert store.get(task.id).status == "done"


def test_undo_with_nothing_to_undo_raises_named_error(tmp_path):
    from cadence.store import NothingToUndo

    fresh = Store(db_path=tmp_path / "fresh.db")
    with pytest.raises(NothingToUndo):
        fresh.undo()


def test_mcp_undo(tmp_path, monkeypatch):
    monkeypatch.setenv("CADENCE_DB_PATH", str(tmp_path / "mcp_undo.db"))
    from cadence.mcp_server import add_task, list_tasks, undo

    add_task("Ship it")
    result = undo()
    assert result["ok"] is True
    assert "Undid" in result["summary"]
    assert list_tasks(status="all")["count"] == 0

    # Calling undo again is itself a mutation to undo -- symmetric undo/redo,
    # so this brings the task back rather than erroring.
    redo = undo()
    assert redo["ok"] is True
    assert list_tasks(status="all")["count"] == 1


def test_mcp_undo_with_nothing_ever_done_is_a_named_error(tmp_path, monkeypatch):
    monkeypatch.setenv("CADENCE_DB_PATH", str(tmp_path / "mcp_undo_fresh.db"))
    from cadence.mcp_server import undo

    nothing = undo()
    assert nothing["ok"] is False
    assert nothing["error"] == "nothing_to_undo"
    assert nothing["hint"]


def test_cli_undo(tmp_path):
    env = _cli_env(tmp_path, "cli_undo.db")
    _run_cli("add", "Ship it", env=env)
    result = _run_cli("undo", env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("Undid: Added #1")

    listing = _run_cli("list", env=env)
    assert "No tasks yet" in listing.stdout


def test_cli_undo_with_nothing_ever_done_is_a_named_error(tmp_path):
    env = _cli_env(tmp_path, "cli_undo_fresh.db")
    nothing = _run_cli("undo", env=env)
    assert nothing.returncode == 1
    assert nothing.stdout.startswith("Error: no mutation to undo yet.")


# --- export -----------------------------------------------------------


def test_export_store_returns_all_tasks_unfiltered(store):
    t1 = store.add("open one")
    t2 = store.add("will finish")
    store.complete(t2.id)
    exported = store.export()
    assert {t["id"] for t in exported} == {t1.id, t2.id}


def test_mcp_export_tasks_json_and_table(tmp_path, monkeypatch):
    monkeypatch.setenv("CADENCE_DB_PATH", str(tmp_path / "mcp_export.db"))
    from cadence.mcp_server import add_task, export_tasks

    add_task("Ship it", priority="high")
    as_json = export_tasks()
    assert as_json["ok"] is True
    assert as_json["count"] == 1
    assert as_json["tasks"][0]["title"] == "Ship it"

    as_table = export_tasks(format="table")
    assert as_table["ok"] is True
    assert any("Ship it" in row for row in as_table["rows"])

    bad = export_tasks(format="xml")
    assert bad["ok"] is False
    assert bad["error"] == "invalid_task"


def test_cli_export_json_writes_file(tmp_path):
    env = _cli_env(tmp_path, "cli_export.db")
    _run_cli("add", "Ship it", env=env)
    out_path = tmp_path / "out.json"
    result = _run_cli("export", "--out", str(out_path), env=env)
    assert result.returncode == 0, result.stderr
    assert out_path.exists()
    data = json.loads(out_path.read_text())
    assert data[0]["title"] == "Ship it"


def test_cli_export_table_reuses_list_row_format(tmp_path):
    env = _cli_env(tmp_path, "cli_export_table.db")
    _run_cli("add", "Ship it", "--priority", "high", env=env)
    listing = _run_cli("list", env=env)
    exported = _run_cli("export", "--format", "table", env=env)
    assert exported.returncode == 0, exported.stderr
    assert exported.stdout.strip() == listing.stdout.strip()


def test_cli_export_rejects_unknown_format(tmp_path):
    env = _cli_env(tmp_path, "cli_export_bad.db")
    _run_cli("add", "Ship it", env=env)
    result = _run_cli("export", "--format", "xml", env=env)
    assert result.returncode == 1
    assert result.stdout.startswith("Error: 'xml' isn't a supported export format.")


# --- sync (two clients) -------------------------------------------------


@pytest.fixture
def bare_remote(tmp_path):
    remote_path = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(remote_path)],
        check=True,
        capture_output=True,
    )
    return str(remote_path)


def test_sync_two_clients_pushes_and_pulls(tmp_path, bare_remote):
    store_a = Store(db_path=tmp_path / "a.db")
    store_b = Store(db_path=tmp_path / "b.db")

    task = store_a.add("Shared task")
    r1 = store_a.sync(remote=bare_remote)
    assert r1["pushed"] == 1
    assert r1["conflicts"] == []

    r2 = store_b.sync(remote=bare_remote)
    assert r2["pulled"] == 1
    assert [t.id for t in store_b.list(status="all")] == [task.id]

    r3 = store_b.sync(remote=bare_remote)
    assert r3["already_synced"] is True


def test_sync_reports_conflict_without_dropping_either_side(tmp_path, bare_remote):
    store_a = Store(db_path=tmp_path / "a2.db")
    store_b = Store(db_path=tmp_path / "b2.db")

    task = store_a.add("Shared task")
    store_a.sync(remote=bare_remote)
    store_b.sync(remote=bare_remote)

    store_a.schedule(task.id, "2026-09-01")
    store_b.reprioritise(task.id, "high")

    r_a = store_a.sync(remote=bare_remote)
    assert r_a["conflicts"] == []
    assert r_a["pushed"] == 1

    r_b = store_b.sync(remote=bare_remote)
    assert r_b["pulled"] == 0
    assert r_b["pushed"] == 0
    assert len(r_b["conflicts"]) == 1
    conflict = r_b["conflicts"][0]
    assert conflict["id"] == task.id
    assert conflict["mine"]["priority"] == "high"
    assert conflict["theirs"]["due"] == "2026-09-01"

    # Nothing was silently overwritten: B still has its own edit locally.
    assert store_b.get(task.id).priority == "high"
    assert store_b.get(task.id).due is None


def test_sync_resolve_keep_theirs_then_converges(tmp_path, bare_remote):
    store_a = Store(db_path=tmp_path / "a3.db")
    store_b = Store(db_path=tmp_path / "b3.db")

    task = store_a.add("Shared task")
    store_a.sync(remote=bare_remote)
    store_b.sync(remote=bare_remote)

    store_a.schedule(task.id, "2026-09-01")
    store_b.reprioritise(task.id, "high")
    store_a.sync(remote=bare_remote)
    r_b = store_b.sync(remote=bare_remote)
    assert len(r_b["conflicts"]) == 1

    resolved = store_b.resolve_conflict(task.id, "theirs")
    assert resolved.due == "2026-09-01"
    assert resolved.priority is None

    r_b2 = store_b.sync(remote=bare_remote)
    assert r_b2["conflicts"] == []


def test_sync_resolve_keep_mine_then_converges_and_wins_on_remote(tmp_path, bare_remote):
    store_a = Store(db_path=tmp_path / "a4.db")
    store_b = Store(db_path=tmp_path / "b4.db")

    task = store_a.add("Shared task")
    store_a.sync(remote=bare_remote)
    store_b.sync(remote=bare_remote)

    store_a.schedule(task.id, "2026-09-01")
    store_b.reprioritise(task.id, "high")
    store_a.sync(remote=bare_remote)
    r_b = store_b.sync(remote=bare_remote)
    assert len(r_b["conflicts"]) == 1

    resolved = store_b.resolve_conflict(task.id, "mine")
    assert resolved.priority == "high"

    # A subsequent sync must not re-report the same conflict forever, and A
    # must eventually see B's resolution too.
    r_b2 = store_b.sync(remote=bare_remote)
    assert r_b2["conflicts"] == []

    r_a2 = store_a.sync(remote=bare_remote)
    assert r_a2["conflicts"] == []
    assert store_a.get(task.id).priority == "high"


def test_sync_without_remote_configured_is_a_named_error(store):
    with pytest.raises(InvalidTask):
        store.sync()


def test_sync_remote_accepts_plain_db_path_and_converges(tmp_path):
    """R-08 finding 1 fix: the *only* address an agent restricted to tool
    descriptions can produce is the other client's own CADENCE_DB_PATH
    value (a plain .db path) -- never Cadence's internal `.history` git
    dir, which is never surfaced anywhere. `sync(remote=...)` must accept
    that value directly and derive the history location itself, and the
    task must actually land in the other store's data (not just avoid
    raising)."""
    store_a = Store(db_path=tmp_path / "peer_a.db")
    store_b = Store(db_path=tmp_path / "peer_b.db")

    task_a = store_a.add("From A")

    # B points straight at A's plain CADENCE_DB_PATH -- not A's .history
    # dir, which B never sees and has no way to construct.
    r_b1 = store_b.sync(remote=str(store_a.db_path))
    assert r_b1["conflicts"] == []
    assert r_b1["pulled"] == 1
    assert store_b.get(task_a.id).title == "From A"

    task_b = store_b.add("From B")
    r_b2 = store_b.sync()  # remote already saved from the first call
    assert r_b2["conflicts"] == []
    assert r_b2["pushed"] == 1

    # A points straight at B's plain CADENCE_DB_PATH to pick up what B
    # pushed -- proves the direct client-to-client push (not just a
    # shared bare-repo intermediary) actually lands, both the address
    # resolution and the underlying git push into a live, checked-out
    # peer repo (receive.denyCurrentBranch=updateInstead).
    r_a = store_a.sync(remote=str(store_b.db_path))
    assert r_a["conflicts"] == []
    assert r_a["pulled"] == 1
    assert store_a.get(task_b.id).title == "From B"

    # Both stores now hold both tasks -- real convergence, not "no error".
    assert {t.id for t in store_a.list(status="all")} == {task_a.id, task_b.id}
    assert {t.id for t in store_b.list(status="all")} == {task_a.id, task_b.id}


def test_cli_sync_remote_help_and_error_name_the_db_path_contract(tmp_path):
    env = _cli_env(tmp_path, "cli_remote_help.db")
    help_out = _run_cli("sync", "--help", env=env)
    assert "CADENCE_DB_PATH" in help_out.stdout

    bad = _run_cli("sync", "--remote", str(tmp_path / "nope.db"), env=env)
    assert bad.returncode == 1
    assert bad.stdout.startswith(f"Error: no Cadence store found at '{tmp_path / 'nope.db'}'.")
    assert "CADENCE_DB_PATH" in bad.stdout
    assert "cadence sync' at least once" in bad.stdout


def test_mcp_sync_tasks_and_resolve(tmp_path, monkeypatch, bare_remote):
    monkeypatch.setenv("CADENCE_DB_PATH", str(tmp_path / "mcp_sync_a.db"))
    from cadence.mcp_server import add_task, sync_tasks

    task_id = add_task("Shared task")["task"]["id"]
    result = sync_tasks(remote=bare_remote)
    assert result["ok"] is True
    assert result["pushed"] == 1

    no_remote = Store(db_path=tmp_path / "no_remote.db")
    from cadence.mcp_server import resolve_sync_conflict

    bad = resolve_sync_conflict(task_id, "mine")
    assert bad["ok"] is False
    assert bad["error"] == "no_such_conflict"


def test_cli_sync_conflict_exit_code_and_recovery_command(tmp_path, bare_remote):
    env_a = _cli_env(tmp_path, "cli_sync_a.db")
    env_b = _cli_env(tmp_path, "cli_sync_b.db")

    _run_cli("add", "Shared task", env=env_a)
    r1 = _run_cli("sync", "--remote", bare_remote, env=env_a)
    assert r1.returncode == 0, r1.stderr

    r2 = _run_cli("sync", "--remote", bare_remote, env=env_b)
    assert r2.returncode == 0, r2.stderr

    _run_cli("schedule", "1", "2026-09-01", env=env_a)
    _run_cli("reprioritise", "1", "high", env=env_b)
    _run_cli("sync", env=env_a)

    r_conflict = _run_cli("sync", env=env_b)
    assert r_conflict.returncode == 1
    assert "1 conflict needs you" in r_conflict.stdout
    assert "cadence sync --keep-mine 1" in r_conflict.stdout
    assert "cadence sync --keep-theirs 1" in r_conflict.stdout

    resolve = _run_cli("sync", "--keep-mine", "1", env=env_b)
    assert resolve.returncode == 0, resolve.stderr

    r_clean = _run_cli("sync", env=env_b)
    assert r_clean.returncode == 0, r_clean.stderr
