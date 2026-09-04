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


def test_sync_first_ever_sync_does_not_false_conflict_on_untouched_task(tmp_path):
    """Chairman demo, 2026-08-31 (docs/dogfooding-log.md): laptop (A)
    creates a task and never touches it again after that; phone (B)
    pulls it on B's own first-ever sync, marks it done, and pushes; A
    then runs `cadence sync` for the very first time ever (A had never
    called sync() before -- only `add`, which just commits to A's own
    local history) and pulls that completion. A must NOT see this as a
    conflict -- A never edited the task since creating it, only B did.
    Reproduces the exact false positive live-demoed to the chairman:
    `cadence sync` reported "1 conflict needs you" on a task the laptop
    had never touched."""
    store_a = Store(db_path=tmp_path / "laptop.db")
    store_b = Store(db_path=tmp_path / "phone.db")

    task_a = store_a.add("Ship the fix")  # A: create, never touched again

    # B's own first-ever sync: points straight at A's plain
    # CADENCE_DB_PATH (the only address a real client can produce) and
    # pulls the task. A itself has never called sync() at this point --
    # A's task only exists via `add`'s own auto-commit to A's history.
    r_b1 = store_b.sync(remote=str(store_a.db_path))
    assert r_b1["conflicts"] == []
    assert r_b1["pulled"] == 1

    store_b.complete(task_a.id)

    r_b2 = store_b.sync()  # remote already saved from the first call
    assert r_b2["conflicts"] == []
    assert r_b2["pushed"] == 1

    # A's FIRST-EVER sync call: pulls B's completion. This is exactly
    # the false-positive-conflict scenario -- A's own base_ref is None.
    r_a = store_a.sync(remote=str(store_b.db_path))
    assert r_a["conflicts"] == [], f"false conflict on A's first sync: {r_a}"
    assert r_a["pulled"] == 1
    assert store_a.get(task_a.id).status == "done"


def test_sync_first_ever_sync_does_not_false_conflict_on_own_pre_sync_edit(tmp_path):
    """Week-2 dogfooding find (docs/dogfooding-log.md, commit 08abb36):
    A edits a task it created (schedules it) BEFORE A's own first-ever
    sync. B does its own first sync, pulls that already-scheduled task,
    and marks it done -- A never touches the task again. When A finally
    runs its own first-ever `sync`, A must not report a conflict: A's
    pre-sync edit is one the remote (B) already fully has, so the two
    sides never actually diverged. This covers one-or-more pre-sync
    local edits on the first-syncing client, which
    test_sync_first_ever_sync_does_not_false_conflict_on_untouched_task
    (zero pre-sync edits) does not."""
    store_a = Store(db_path=tmp_path / "a.db")
    store_b = Store(db_path=tmp_path / "b.db")

    task_a = store_a.add("task edited on A before A ever syncs")
    store_a.schedule(task_a.id, "2026-09-08", reason="pre-sync edit on A")

    # B's first-ever sync: pulls #1 already scheduled.
    r_b1 = store_b.sync(remote=str(store_a.db_path))
    assert r_b1["conflicts"] == []
    assert r_b1["pulled"] == 1

    store_b.complete(task_a.id)  # B's only edit, post-pull

    store_a.add("unrelated new task on A")  # A never touches #1 again

    # A's first-ever sync call.
    r_a = store_a.sync(remote=str(store_b.db_path))
    assert r_a["conflicts"] == [], f"false conflict on A's first sync: {r_a}"
    assert store_a.get(task_a.id).status == "done"


def test_sync_push_into_peer_that_only_ever_listed_bootstraps_and_succeeds(tmp_path):
    """R-08 re-verify Finding D
    (redteam_run7_0224/REDTEAM_PASS_0.2.4_sync_deep.md): a peer whose store
    file exists (created by a read-only `list`) but has never written or
    synced -- no `.history` yet -- used to make a push into it fail with
    `no Cadence store found at '<path>'`, even though the path and the
    store file are both correct. The peer's history must now be
    transparently bootstrapped so the push just works, exactly as if that
    peer had run any command on itself first."""
    store_a = Store(db_path=tmp_path / "a.db")
    store_b_path = tmp_path / "b.db"

    task_a = store_a.add("A's task")

    # Client B: brand new device. A read-only `list()` creates the sqlite
    # file (matching `cadence list` in the real CLI) but must NOT itself
    # create a `.history` dir -- otherwise this test would not reproduce
    # Finding D's precondition at all.
    store_b = Store(db_path=store_b_path)
    assert store_b.list(status="all") == []
    assert store_b_path.exists()
    history_dir = store_b_path.parent / (store_b_path.name + ".history")
    assert not (history_dir / ".git").is_dir()

    # Client A pushes into B -- the normal "sync my tasks to my new device"
    # order. Must not raise (the old bug), and must actually push (not
    # silently no-op).
    result = store_a.sync(remote=str(store_b_path))
    assert result["conflicts"] == []
    assert result["pushed"] == 1
    assert (history_dir / ".git").is_dir()

    # B's own sqlite only picks up an incoming push once B itself syncs --
    # same as an already-initialized peer (see
    # test_sync_remote_accepts_plain_db_path_and_converges); this fix is
    # scoped to the transport bootstrap, not the pull/apply logic. That
    # normal second half of the flow must now work too, proving the
    # bootstrap produced a real, usable history rather than just avoiding
    # the error.
    landed = Store(db_path=store_b_path)
    r_b = landed.sync(remote=str(store_a.db_path))
    assert r_b["conflicts"] == []
    assert r_b["pulled"] == 1
    assert [t.title for t in landed.list(status="all")] == ["A's task"]


def test_sync_into_genuinely_missing_peer_path_still_errors(tmp_path):
    """The bootstrap above is scoped to a peer store file that actually
    exists on disk -- a path with nothing at all must still fail exactly
    as before (see also
    test_cli_sync_remote_help_and_error_name_the_db_path_contract), so a
    real typo/wrong-path mistake is never silently swallowed."""
    store_a = Store(db_path=tmp_path / "a2.db")
    store_a.add("A's task")
    with pytest.raises(InvalidTask, match="no Cadence store found"):
        store_a.sync(remote=str(tmp_path / "does_not_exist.db"))


def test_cli_sync_remote_help_and_error_name_the_db_path_contract(tmp_path):
    env = _cli_env(tmp_path, "cli_remote_help.db")
    help_out = _run_cli("sync", "--help", env=env)
    assert "CADENCE_DB_PATH" in help_out.stdout

    bad = _run_cli("sync", "--remote", str(tmp_path / "nope.db"), env=env)
    assert bad.returncode == 1
    assert bad.stdout.startswith(f"Error: no Cadence store found at '{tmp_path / 'nope.db'}'.")
    assert "CADENCE_DB_PATH" in bad.stdout
    assert "cadence sync' at least once" in bad.stdout


def test_cli_sync_push_into_freshly_listed_peer_succeeds(tmp_path):
    """Real-CLI reproduction of R-08 re-verify Finding D's exact repro:
    client A has a task, client B is a brand new device that has only ever
    run `list`, then A syncs --remote straight at B's path. Must exit 0
    and actually land the task on B, not report a missing store."""
    env_a = _cli_env(tmp_path, "finding_d_a.db")
    env_b = _cli_env(tmp_path, "finding_d_b.db")

    added = _run_cli("add", "A's task", env=env_a)
    assert added.returncode == 0, added.stdout

    listed = _run_cli("list", env=env_b)
    assert listed.returncode == 0, listed.stdout
    assert (tmp_path / "finding_d_b.db").exists()

    synced = _run_cli("sync", "--remote", str(tmp_path / "finding_d_b.db"), env=env_a)
    assert synced.returncode == 0, synced.stdout
    assert "no Cadence store found" not in synced.stdout

    # B still has to sync itself to materialize the incoming push into its
    # own store (same as any already-initialized peer) -- this fix bootstraps
    # the transport, not the pull/apply logic.
    b_sync = _run_cli("sync", "--remote", str(tmp_path / "finding_d_a.db"), env=env_b)
    assert b_sync.returncode == 0, b_sync.stdout

    b_listed = _run_cli("list", env=env_b)
    assert "A's task" in b_listed.stdout


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


# --- R-08 re-verify Finding A: id collision must never destroy a task ----


def test_sync_id_collision_between_unrelated_tasks_preserves_both(tmp_path, bare_remote):
    """Two never-synced-before clients each add a task before ever
    syncing, so each store assigns id 1 independently -- an id
    COLLISION between two unrelated tasks, not an edit of the same task.
    Unlike a real edit conflict, this must never destroy either side's
    content: it auto-resolves within sync() itself (reported in
    `renumbered`, not `conflicts`), never requiring resolve_sync_conflict
    (which would otherwise permanently delete the losing side's task --
    see docs/ten-step-transcript.md "Step 8 in detail", Finding A)."""
    store_a = Store(db_path=tmp_path / "coll_a.db")
    store_b = Store(db_path=tmp_path / "coll_b.db")

    task_a = store_a.add("Draft offsite agenda")
    task_b = store_b.add("Buy milk")
    assert task_a.id == task_b.id  # both independently assigned id 1

    r_a = store_a.sync(remote=bare_remote)
    assert r_a["conflicts"] == []
    assert r_a["renumbered"] == []

    r_b = store_b.sync(remote=bare_remote)
    assert r_b["conflicts"] == []
    assert len(r_b["renumbered"]) == 1
    assert r_b["renumbered"][0]["old_id"] == task_b.id
    assert r_b["renumbered"][0]["kept_at_old_id"] == "mine"

    # B kept its own task at id 1 AND gained A's task under a fresh id --
    # neither title was deleted.
    b_titles = {t.title for t in store_b.list(status="all")}
    assert "Buy milk" in b_titles
    assert "Draft offsite agenda" in b_titles
    assert store_b.get(task_b.id).title == "Buy milk"

    # A syncing again must also end up with both titles present somewhere
    # in its own store -- still no content lost, from either side.
    r_a2 = store_a.sync(remote=bare_remote)
    assert r_a2["conflicts"] == []
    a_titles = {t.title for t in store_a.list(status="all")}
    assert "Buy milk" in a_titles
    assert "Draft offsite agenda" in a_titles

    # No open conflict was left behind for either client to resolve.
    with pytest.raises(CadenceError):
        store_b.resolve_conflict(task_b.id, "mine")


def test_mcp_sync_tasks_reports_id_collision_as_renumbered_not_conflict(
    tmp_path, monkeypatch, bare_remote
):
    monkeypatch.setenv("CADENCE_DB_PATH", str(tmp_path / "mcp_coll_a.db"))
    from cadence.mcp_server import add_task, sync_tasks

    add_task("A's first task")
    sync_tasks(remote=bare_remote)

    store_b = Store(db_path=tmp_path / "mcp_coll_b.db")
    store_b.add("B's first task")
    result = store_b.sync(remote=bare_remote)
    assert result["conflicts"] == []
    assert len(result["renumbered"]) == 1


# --- R-08 re-verify Finding B: distinct CADENCE_DB_PATH stems must not ---
# --- collide on-disk and must never crash sync with a raw KeyError -------


def test_history_dir_does_not_collide_on_shared_stem(tmp_path):
    """`Path.stem` only strips the last dot-suffix, so `store` and
    `store.db` used to derive to the identical on-disk history directory
    even though they're different CADENCE_DB_PATH values."""
    store_plain = Store(db_path=tmp_path / "store")
    store_dotdb = Store(db_path=tmp_path / "store.db")

    assert store_plain._history().repo_dir != store_dotdb._history().repo_dir


def test_sync_no_crash_when_second_db_path_shares_stem_with_existing_store(tmp_path):
    """Direct regression for the documented repro (docs/ten-step-transcript.md
    "Step 8 in detail", Finding B): two brand-new stores whose paths are
    built by suffixing an already-in-use store's path share that store's
    `Path.stem` and, under the old derivation, all three collapsed onto
    the SAME history directory -- crashing sync with a raw `KeyError: 2`
    and cross-contaminating the pre-existing store's history. Must not
    crash, and the pre-existing store's own data must be untouched."""
    used = Store(db_path=tmp_path / "used.db")
    used.add("Existing task")

    collide_c = Store(db_path=tmp_path / "used.db_collide_c")
    collide_d = Store(db_path=tmp_path / "used.db_collide_d")
    collide_c.add("C's own task")
    collide_d.add("D's own task")

    r = collide_c.sync(remote=str(collide_d.db_path))
    assert r["conflicts"] == []

    c_titles = {t.title for t in collide_c.list(status="all")}
    assert "C's own task" in c_titles
    assert "D's own task" in c_titles

    # The pre-existing, unrelated store's history was never touched.
    assert [t.title for t in used.list(status="all")] == ["Existing task"]


# --- Red Team finding C (0.2.2): direct-peer id collision must not --------
# --- fabricate a duplicate row on either side ------------------------------


def test_sync_direct_peer_id_collision_does_not_duplicate_either_side(tmp_path):
    """Deterministic repro of Red Team's 0.2.2 finding: two clients that
    have NEVER synced with each other or with any hub, each already
    holding one task before their first sync (natural id-1/id-1
    collision), doing a plain peer-to-peer `sync(remote=<other's plain
    CADENCE_DB_PATH>)` in BOTH directions -- exactly step 8 of the
    ten-step script, no extra steps, no bare-git hub.

    Unlike test_sync_id_collision_between_unrelated_tasks_preserves_both
    above (which only exercises the bare_remote HUB topology), this drives
    the direct-peer topology the CLI/MCP docs actually advertise for
    `--remote`. And unlike that test's `set`-based title assertions (which
    silently collapse duplicates and could not have caught this), this
    asserts an exact task COUNT on both sides after each sync call, so a
    fabricated extra row fails loudly here even if titles alone would look
    fine.
    """
    store_a = Store(db_path=tmp_path / "peer_coll_a.db")
    store_b = Store(db_path=tmp_path / "peer_coll_b.db")

    task_a = store_a.add("A-ORIGINAL")
    task_b = store_b.add("B-ORIGINAL")
    assert task_a.id == task_b.id  # both independently assigned id 1

    r_a = store_a.sync(remote=str(store_b.db_path))
    assert r_a["conflicts"] == []
    # A's own store must still hold exactly its own task plus B's -- 2,
    # never 3.
    assert len(store_a.list(status="all")) == 2

    r_b = store_b.sync(remote=str(store_a.db_path))
    assert r_b["conflicts"] == []
    # The fabricated-duplicate bug: B ended up with 3 rows here (its own
    # original task twice, plus A's) even though ok:true and no conflict
    # was ever reported. Must be exactly 2.
    b_tasks = store_b.list(status="all")
    assert len(b_tasks) == 2, f"expected 2 tasks, got {len(b_tasks)}: {[t.title for t in b_tasks]}"
    b_titles = [t.title for t in b_tasks]
    assert sorted(b_titles) == ["A-ORIGINAL", "B-ORIGINAL"]
    # In particular, B's own original task must appear exactly once, not
    # duplicated.
    assert b_titles.count("B-ORIGINAL") == 1

    # And A, still holding just its first-sync state, is unaffected by
    # B's second call until it syncs again.
    assert len(store_a.list(status="all")) == 2


def test_mcp_sync_tasks_direct_peer_id_collision_does_not_duplicate(
    tmp_path, monkeypatch
):
    """Same repro as
    test_sync_direct_peer_id_collision_does_not_duplicate_either_side,
    driven over the actual MCP tool surface (each `CADENCE_DB_PATH` swap
    simulates a separate agent session against its own client), since the
    project's own rule is that a capability isn't proven fixed on one
    surface only."""
    from cadence.mcp_server import add_task, sync_tasks

    a_path = tmp_path / "mcp_peer_coll_a.db"
    b_path = tmp_path / "mcp_peer_coll_b.db"

    monkeypatch.setenv("CADENCE_DB_PATH", str(a_path))
    add_task("A-ORIGINAL")

    monkeypatch.setenv("CADENCE_DB_PATH", str(b_path))
    add_task("B-ORIGINAL")

    monkeypatch.setenv("CADENCE_DB_PATH", str(a_path))
    r_a = sync_tasks(remote=str(b_path))
    assert r_a["ok"] is True
    assert r_a["conflicts"] == []

    monkeypatch.setenv("CADENCE_DB_PATH", str(b_path))
    r_b = sync_tasks(remote=str(a_path))
    assert r_b["ok"] is True
    assert r_b["conflicts"] == []

    b_tasks = Store(db_path=b_path).list(status="all")
    assert len(b_tasks) == 2, f"expected 2 tasks, got {len(b_tasks)}: {[t.title for t in b_tasks]}"
    assert sorted(t.title for t in b_tasks) == ["A-ORIGINAL", "B-ORIGINAL"]

    a_tasks = Store(db_path=a_path).list(status="all")
    assert len(a_tasks) == 2, f"expected 2 tasks, got {len(a_tasks)}: {[t.title for t in a_tasks]}"


# --- structural origin-UUID identity fix (task ---------------------------
# --- task_01a04b9b39057fc952517775): 0.2.3's echo-fix only recognized ----
# --- "my own echo" while a row was still unbased (its first-ever sync), --
# --- so a 3RD ordinary re-sync of an already-converged pair reintroduced -
# --- the phantom duplicate and a 4th crashed with a raw KeyError. --------


def test_sync_direct_peer_repeated_resync_never_duplicates(tmp_path):
    """Same id-1/id-1 collision as
    test_sync_direct_peer_id_collision_does_not_duplicate_either_side, but
    driven through *four* ordinary sync rounds per side (8 sync calls
    total) with zero new local changes after the first round -- exactly
    what a periodic sync job or a defensively-syncing agent does. The old
    content-fingerprint-of-unbased-rows proxy for identity only survived
    each row's first sync; by the 3rd round every row was already
    "based", so the fingerprint match stopped firing and the echo was
    pulled in again as if new. Every round must land on exactly 2 tasks
    per side and never raise.
    """
    store_a = Store(db_path=tmp_path / "resync_a.db")
    store_b = Store(db_path=tmp_path / "resync_b.db")

    store_a.add("A-ORIGINAL")
    store_b.add("B-ORIGINAL")

    for round_num in range(1, 5):
        r_a = store_a.sync(remote=str(store_b.db_path))
        assert r_a["conflicts"] == [], f"round {round_num} A->B: {r_a}"
        a_tasks = store_a.list(status="all")
        assert len(a_tasks) == 2, (
            f"round {round_num} A->B: expected 2 tasks on A, got "
            f"{len(a_tasks)}: {[t.title for t in a_tasks]}"
        )

        r_b = store_b.sync(remote=str(store_a.db_path))
        assert r_b["conflicts"] == [], f"round {round_num} B->A: {r_b}"
        b_tasks = store_b.list(status="all")
        assert len(b_tasks) == 2, (
            f"round {round_num} B->A: expected 2 tasks on B, got "
            f"{len(b_tasks)}: {[t.title for t in b_tasks]}"
        )

    # Both sides converged on exactly the two original tasks, each
    # appearing once -- no phantom third row on either side after 8
    # total sync calls.
    assert sorted(t.title for t in store_a.list(status="all")) == [
        "A-ORIGINAL", "B-ORIGINAL",
    ]
    assert sorted(t.title for t in store_b.list(status="all")) == [
        "A-ORIGINAL", "B-ORIGINAL",
    ]


def test_sync_direct_peer_edit_after_convergence_propagates_without_duplicating(
    tmp_path,
):
    """After the id-collision pair above has fully converged (both
    directions synced once), an edit on one side followed by more sync
    rounds in both directions must propagate the edit and must not
    resurrect a duplicate of either side's original row. This is the
    "re-sync after further edits" case from the fix spec."""
    store_a = Store(db_path=tmp_path / "editresync_a.db")
    store_b = Store(db_path=tmp_path / "editresync_b.db")

    task_a = store_a.add("A-ORIGINAL")
    store_b.add("B-ORIGINAL")

    store_a.sync(remote=str(store_b.db_path))
    store_b.sync(remote=str(store_a.db_path))
    assert len(store_a.list(status="all")) == 2
    assert len(store_b.list(status="all")) == 2

    store_a.reprioritise(task_a.id, "high")

    r_push = store_a.sync(remote=str(store_b.db_path))
    assert r_push["conflicts"] == []
    assert len(store_a.list(status="all")) == 2

    r_pull = store_b.sync(remote=str(store_a.db_path))
    assert r_pull["conflicts"] == []
    b_tasks = store_b.list(status="all")
    assert len(b_tasks) == 2, f"expected 2 tasks, got {len(b_tasks)}: {[t.title for t in b_tasks]}"
    edited = [t for t in b_tasks if t.title == "A-ORIGINAL"]
    assert len(edited) == 1
    assert edited[0].priority == "high"

    # Two more idempotent rounds each way: still exactly 2/2, edit holds.
    for _ in range(2):
        store_a.sync(remote=str(store_b.db_path))
        store_b.sync(remote=str(store_a.db_path))
    assert len(store_a.list(status="all")) == 2
    assert len(store_b.list(status="all")) == 2


def test_mcp_sync_tasks_repeated_resync_never_duplicates(tmp_path, monkeypatch):
    """MCP-surface counterpart of
    test_sync_direct_peer_repeated_resync_never_duplicates -- four
    ordinary sync rounds per side over the actual tool surface an agent
    calls, asserting exact counts throughout."""
    from cadence.mcp_server import add_task, sync_tasks

    a_path = tmp_path / "mcp_resync_a.db"
    b_path = tmp_path / "mcp_resync_b.db"

    monkeypatch.setenv("CADENCE_DB_PATH", str(a_path))
    add_task("A-ORIGINAL")

    monkeypatch.setenv("CADENCE_DB_PATH", str(b_path))
    add_task("B-ORIGINAL")

    for round_num in range(1, 5):
        monkeypatch.setenv("CADENCE_DB_PATH", str(a_path))
        r_a = sync_tasks(remote=str(b_path))
        assert r_a["ok"] is True
        assert r_a["conflicts"] == []
        a_tasks = Store(db_path=a_path).list(status="all")
        assert len(a_tasks) == 2, f"round {round_num}: {[t.title for t in a_tasks]}"

        monkeypatch.setenv("CADENCE_DB_PATH", str(b_path))
        r_b = sync_tasks(remote=str(a_path))
        assert r_b["ok"] is True
        assert r_b["conflicts"] == []
        b_tasks = Store(db_path=b_path).list(status="all")
        assert len(b_tasks) == 2, f"round {round_num}: {[t.title for t in b_tasks]}"


def test_undo_preserves_origin_identity_across_sync(tmp_path):
    """Undo must never blank out or fork a task's immutable `origin` --
    doing so would make a reverted row look brand-new to the sync merge
    engine and reintroduce a duplicate on the next sync. Exercises add ->
    edit -> undo -> sync and checks the peer ends up with exactly one
    copy of the row, not two."""
    store_a = Store(db_path=tmp_path / "undo_origin_a.db")
    store_b = Store(db_path=tmp_path / "undo_origin_b.db")

    task = store_a.add("Reprioritise me")
    origin_before = store_a.get(task.id).origin
    assert origin_before
    # `sync` requires the peer's own history to exist already (as with
    # every other direct-peer test in this file) -- give B an unrelated
    # task of its own so both sides have run their own first `add`.
    store_b.add("B's unrelated task")

    store_a.reprioritise(task.id, "high")
    store_a.undo()
    assert store_a.get(task.id).origin == origin_before

    r = store_a.sync(remote=str(store_b.db_path))
    assert r["conflicts"] == []
    # A's push lands in B's own git history immediately, but B's sqlite
    # only picks it up on B's own next sync (same as every other
    # direct-peer test in this file).
    store_b.sync(remote=str(store_a.db_path))
    b_titles = [t.title for t in store_b.list(status="all")]
    assert b_titles.count("Reprioritise me") == 1, b_titles

    # A second, idempotent sync round must not duplicate the undone row.
    store_a.sync(remote=str(store_b.db_path))
    store_b.sync(remote=str(store_a.db_path))
    a_titles = [t.title for t in store_a.list(status="all")]
    b_titles = [t.title for t in store_b.list(status="all")]
    assert a_titles.count("Reprioritise me") == 1, a_titles
    assert b_titles.count("Reprioritise me") == 1, b_titles
    assert len(store_a.list(status="all")) == 2
    assert len(store_b.list(status="all")) == 2


def test_sync_passive_relay_task_survives_hosts_own_later_sync(tmp_path):
    """Red Team independent pass, 2026-09-03
    (redteam_verify0222_indep/findings/2026-09-03-sync-0.2.22-pass.md):
    A2 is never touched directly by X2's owner -- X2 just runs `cadence
    sync --remote A2`, which writes X2's task straight into A2's git
    tree (`push_safe_merge`) without A2's own sqlite ever learning about
    it. The very next time A2 runs its OWN `cadence sync`, this time
    against a third client C2 that never heard of X2's task either,
    A2's self-heal step used to treat "not in my own sqlite" as drift
    and delete X2's on-disk file -- permanently, with zero conflict or
    warning, even though the CLI's own text claims nothing was lost.
    X2's task must still be on A2 after A2's second sync against a
    different, unrelated peer."""
    store_a2 = Store(db_path=tmp_path / "a2.db")
    store_x2 = Store(db_path=tmp_path / "x2.db")
    store_c2 = Store(db_path=tmp_path / "c2.db")

    store_a2.add("A2's own task")
    store_x2.add("X2's own task")

    # X2's own first sync: pushes straight into A2's tree. A2's sqlite
    # does NOT learn about it -- A2 never calls sync() here.
    r_x2_1 = store_x2.sync(remote=str(store_a2.db_path))
    assert r_x2_1["conflicts"] == []
    assert r_x2_1["pushed"] == 1

    store_c2.add("C2's own task")  # C2 never touched X2 or its task

    # A2's own first sync, against C2 -- who never heard of X2's task.
    r_a2 = store_a2.sync(remote=str(store_c2.db_path))
    assert r_a2["conflicts"] == [], f"unexpected conflict: {r_a2}"

    a2_titles = [t.title for t in store_a2.list(status="all")]
    assert "X2's own task" in a2_titles, (
        f"X2's passively-relayed task was silently purged by A2's own "
        f"self-heal: {a2_titles}"
    )
    assert "A2's own task" in a2_titles
    assert "C2's own task" in a2_titles
    assert len(a2_titles) == 3

    # X2's own second sync must still find its task alive on A2, and
    # must not need to re-push it (it was never actually lost).
    r_x2_2 = store_x2.sync(remote=str(store_a2.db_path))
    assert r_x2_2["conflicts"] == []
    x2_titles = [t.title for t in store_x2.list(status="all")]
    assert x2_titles.count("X2's own task") == 1, x2_titles
    assert len(x2_titles) == 3


def test_local_add_on_passive_relay_does_not_overwrite_orphan_task_file(tmp_path):
    """Red Team independent pass on 0.2.23
    (docs/dogfooding-log.md 2026-09-03, commit 989844d): the 0.2.23 fix
    above only guards the SYNC path (`_sync_diff_and_apply` absorbs
    orphans before its own self-heal runs). A relay client's sqlite is
    still empty, so a plain LOCAL `add` -- no sync involved -- used to
    allocate its new row's id straight from sqlite's own AUTOINCREMENT
    counter, which starts from empty and hands out id=1 first. That
    collided with the on-disk orphan file X is passively carrying for A
    (written straight into X's tree by A's push, per the test above),
    and `_snapshot_and_commit` wrote tasks/1.json unconditionally,
    silently overwriting A's only copy -- no error, no exit code
    signal. A's task must still be on X after X's own local add."""
    store_a = Store(db_path=tmp_path / "orphan_add_a.db")
    store_x = Store(db_path=tmp_path / "orphan_add_x.db")

    store_a.add("A's task")
    # A's own first sync: pushes straight into X's tree. X's sqlite does
    # NOT learn about it -- X never calls sync() here, exactly like the
    # relay case above.
    r = store_a.sync(remote=str(store_x.db_path))
    assert r["conflicts"] == []
    assert r["pushed"] == 1
    assert store_x.list(status="all") == []  # X's sqlite: still empty

    # X's own local add -- no sync, no peer, nothing but this client.
    task_x = store_x.add("task native to X")

    x_titles = [t.title for t in store_x.list(status="all")]
    assert "A's task" in x_titles, (
        f"X's local add silently overwrote A's passively-relayed orphan "
        f"task file: {x_titles}"
    )
    assert "task native to X" in x_titles
    assert len(x_titles) == 2
    # The two rows must have landed at distinct ids -- the whole bug was
    # sqlite handing task_x the same id (1) A's on-disk file already used.
    x_by_title = {t.title: t.id for t in store_x.list(status="all")}
    assert x_by_title["A's task"] != x_by_title["task native to X"]

    # X's next sync against A must not see the just-absorbed row as new
    # (it already has A's content, verbatim) and must not duplicate it.
    r2 = store_x.sync(remote=str(store_a.db_path))
    assert r2["conflicts"] == []
    a_titles_after = [t.title for t in store_a.list(status="all")]
    assert a_titles_after.count("A's task") == 1, a_titles_after


def test_decompose_on_passive_relay_does_not_overwrite_orphan_task_file(tmp_path):
    """Same bug, same fix, the other allocation site: `decompose`'s
    subtask ids come from the identical plain INSERT/AUTOINCREMENT
    pattern `add` uses, so a passive relay is exposed the same way --
    Dov flagged this as untested-but-likely-shared in the 0.2.23 finding;
    confirming it here."""
    store_a = Store(db_path=tmp_path / "orphan_decomp_a.db")
    store_x = Store(db_path=tmp_path / "orphan_decomp_x.db")

    store_a.add("A's task")
    r = store_a.sync(remote=str(store_x.db_path))
    assert r["conflicts"] == []
    assert r["pushed"] == 1
    assert store_x.list(status="all") == []

    # X's parent task, created locally (exercises add's own fix too, but
    # that is covered above -- here we only need a valid parent to
    # decompose).
    parent = store_x.add("X's parent task")
    _, children = store_x.decompose(parent.id, ["sub one", "sub two"])

    x_titles = [t.title for t in store_x.list(status="all")]
    assert "A's task" in x_titles, (
        f"X's decompose silently overwrote A's passively-relayed orphan "
        f"task file: {x_titles}"
    )
    ids = [t.id for t in store_x.list(status="all")]
    assert len(ids) == len(set(ids)), f"duplicate ids after decompose: {x_titles}"
    assert len(x_titles) == 4  # A's task + X's parent + 2 subtasks


def test_add_reports_recovered_orphan_distinctly_from_requested_task(tmp_path):
    """docs/dogfooding-log.md 2026-09-04 legibility finding: absorbing an
    orphan (tested above) is safe, but silent -- a caller could not tell
    "I made 1 task" from "I made 1 task and this call also recovered a
    stray one" without diffing `list` before/after. The `Task` `add`
    returns must carry that distinctly, never folded into the requested
    task itself."""
    store_a = Store(db_path=tmp_path / "recovered_add_a.db")
    store_x = Store(db_path=tmp_path / "recovered_add_x.db")

    store_a.add("A's task")
    store_a.sync(remote=str(store_x.db_path))
    assert store_x.list(status="all") == []  # still an orphan file, not a row

    task_x = store_x.add("new native on X")

    # The requested task is unaffected: it's still exactly what was asked
    # for, at its own id.
    assert task_x.title == "new native on X"
    # The recovered orphan is reported separately, by id and title -- not
    # merged into task_x, not silently dropped.
    assert [r.title for r in task_x.recovered] == ["A's task"]
    assert task_x.recovered[0].id != task_x.id

    # A second add on the same store, with nothing left to recover, reports
    # an empty list -- this is a signal about THIS call, not a sticky flag.
    task_x2 = store_x.add("another native on X")
    assert task_x2.recovered == []


def test_decompose_reports_recovered_orphan_distinctly_from_requested_subtasks(tmp_path):
    """Same legibility fix, the other allocation site -- see `add`'s
    version above for the full rationale."""
    store_b = Store(db_path=tmp_path / "recovered_decomp_b.db")
    store_x = Store(db_path=tmp_path / "recovered_decomp_x.db")

    # X gets a parent to decompose from a genuine local add first (its own
    # first-ever local write, so nothing to recover yet -- matches `add`'s
    # own test above).
    parent = store_x.add("X's parent task")
    assert parent.recovered == []

    # A second, distinct orphan (a THIRD party to the a<->x pair, so
    # `decompose`'s own absorb call -- not the ordinary sync pull path --
    # is what has to pick it up) lands in X's tree as a passive relay,
    # exactly like the `add` case above but happening between the parent
    # add and the decompose call.
    store_b.add("B's task")
    store_b.sync(remote=str(store_x.db_path))
    assert store_x.list(status="all") == [parent], "still an orphan file, not a row"

    parent2, children = store_x.decompose(parent.id, ["sub one", "sub two"])

    # The requested subtasks are unaffected: exactly the two titles asked
    # for, linked to the right parent.
    assert [c.title for c in children] == ["sub one", "sub two"]
    assert all(c.parent_id == parent.id for c in children)
    # The recovered orphan rides on the returned parent, distinct from the
    # subtasks list.
    assert [r.title for r in parent2.recovered] == ["B's task"]
    assert parent2.recovered[0].id not in {c.id for c in children}
    assert parent2.recovered[0].id != parent2.id


# --- Dov's independent 0.2.24 pass: three orphan shapes the absorb loop --
# --- couldn't parse/understand still lost or wedged data -----------------


def _orphan_task_path(store: Store, task_id: int) -> "Path":
    """The on-disk tasks/<id>.json path for `store`'s own history dir --
    ensures the history repo (and its tasks/ dir) exists first, same as
    a real mutation would."""
    hist = store._history()
    hist.ensure()
    return hist.tasks_dir / f"{task_id}.json"


@pytest.mark.parametrize("verb", ["add", "decompose"])
def test_truncated_orphan_file_not_overwritten(tmp_path, verb):
    """Shape #1, worst finding in Dov's pass: a process killed mid-write
    (write_task_file has no atomic temp+rename) leaves an unparseable
    tasks/<id>.json on disk. The very next add/decompose on that store
    used to hand its id straight back out and overwrite it silently,
    exit 0, no relay or second client involved at all."""
    store = Store(db_path=tmp_path / "cadence.db")
    store.add("seed")  # id 1, so the next allocation would be 2
    orphan = _orphan_task_path(store, 2)
    orphan.write_text('{"id": 2, "title": "truncated mid-write')
    before = orphan.read_text()

    if verb == "add":
        store.add("new native task")
    else:
        parent = store.add("parent for decompose")
        store.decompose(parent.id, ["sub one"])

    after = orphan.read_text()
    assert before == after, "truncated orphan file was silently overwritten"


@pytest.mark.parametrize("verb", ["add", "decompose"])
def test_no_origin_orphan_file_not_overwritten(tmp_path, verb):
    """Shape #2: a well-formed task JSON object missing (or with a falsy)
    "origin" key -- same silent-overwrite failure as shape #1, narrower
    trigger (hand-restored file, pre-origin-schema file)."""
    store = Store(db_path=tmp_path / "cadence.db")
    store.add("seed")
    orphan = _orphan_task_path(store, 2)
    orphan.write_text(json.dumps({
        "id": 2, "title": "orphan with no origin field", "status": "pending",
        "priority": None, "due": None, "created_at": "2026-01-01T00:00:00",
        "completed_at": None, "parent_id": None,
    }))
    before = orphan.read_text()

    if verb == "add":
        store.add("new native task")
    else:
        parent = store.add("parent for decompose")
        store.decompose(parent.id, ["sub one"])

    after = orphan.read_text()
    assert before == after, "no-origin orphan file was silently overwritten"


@pytest.mark.parametrize("verb", ["add", "decompose"])
def test_non_object_json_orphan_does_not_crash_or_wedge(tmp_path, verb):
    """Shape #3: valid JSON that isn't an object (e.g. a bare array) used
    to raise an uncaught AttributeError out of the absorb loop's
    `data.get("origin")`, leaking through the generic exception handler
    and leaving the store PERMANENTLY wedged -- every subsequent
    add/decompose on it failed the same way. Must not raise, and the
    store must keep working afterward (id 2 stays reserved, but 3 and
    later ids allocate normally)."""
    store = Store(db_path=tmp_path / "cadence.db")
    store.add("seed")
    orphan = _orphan_task_path(store, 2)
    orphan.write_text(json.dumps([1, 2, 3]))

    if verb == "add":
        first = store.add("next real task")
        second = store.add("retry after supposed crash")
    else:
        parent = store.add("parent for decompose")
        _, first_children = store.decompose(parent.id, ["sub one"])
        first = first_children[0]
        _, second_children = store.decompose(parent.id, ["sub two"])
        second = second_children[0]

    assert first.id != 2 and second.id != 2
    assert first.id != second.id
    # The bogus array file was left alone, not turned into a fabricated row.
    assert json.loads(orphan.read_text()) == [1, 2, 3]


def test_sync_internal_error_hint_does_not_blame_cadence_db_path(tmp_path, monkeypatch):
    """`sync`'s catch-all wrapper used to tell every caller to check for a
    shared CADENCE_DB_PATH stem, even when that has nothing to do with
    the actual failure (e.g. the non-object-JSON crash above, before it
    was fixed at the source). The hint text must no longer name that as
    the presumed cause."""
    from cadence.store import SyncInconsistent

    store_a = Store(db_path=tmp_path / "sync_hint_a.db")
    store_b = Store(db_path=tmp_path / "sync_hint_b.db")
    store_a.add("a's task")
    store_a.sync(remote=str(store_b.db_path))

    def _boom(self, hist, theirs_ref):
        raise KeyError("simulated internal inconsistency")

    monkeypatch.setattr(Store, "_sync_diff_and_apply", _boom)

    with pytest.raises(SyncInconsistent) as exc_info:
        store_a.sync(remote=str(store_b.db_path))
    hint = exc_info.value.hint
    # The old wording asserted a shared CADENCE_DB_PATH stem was the
    # cause, unconditionally -- it must not claim that anymore.
    assert "distinct path ending in '.db'" not in hint
    assert "Rolled back automatically: nothing was changed" in hint


def test_sync_self_heal_never_deletes_unreadable_orphan_task_file(tmp_path):
    """Red Team independent pass on 0.2.25
    (docs/dogfooding-log.md 2026-09-04): self-heal's rewrite step treated
    ANY on-disk id absent from sqlite as stale drift to erase, without
    ever checking whether it could actually READ that file first.
    `remove_task_file` only needs write permission on the parent
    directory, not read permission on the file itself, so a file that
    exists but is unreadable (chmod 000 / restrictive ACL -- never even
    absorbed, because `_absorb_orphan_task_files` correctly refuses to
    guess at content it can't read) got silently unlinked the moment
    self-heal had ANY other genuine pull/push work to do in the same
    sync call, with zero warning -- and the CLI's own 'Nothing was lost
    or overwritten' text prints right alongside the loss when a
    renumber also happens in that same call. The file must survive, and
    the caller must get a real warning naming it, not silence."""
    store = Store(db_path=tmp_path / "unreadable_p.db")
    store_r = Store(db_path=tmp_path / "unreadable_r.db")

    store.add("p's own task")  # id 1
    orphan = _orphan_task_path(store, 2)
    orphan.write_text(json.dumps(
        {"id": 2, "title": "unreadable orphan", "origin": "nobody-knows-this-origin"}
    ))
    os.chmod(orphan, 0o000)
    try:
        # Sanity: this store's own sqlite genuinely never absorbed it --
        # otherwise this test would not be exercising the unreadable
        # case at all.
        rows = store.list(status="all")
        assert len(rows) == 1 and rows[0].title == "p's own task"

        # R is a genuinely different, unrelated peer this store has
        # never synced with -- syncing against it gives this store real
        # PUSH work to do (its own task, never seen by R), which is
        # what makes self-heal actually run in the same call.
        result = store.sync(remote=str(store_r.db_path))
        assert result["conflicts"] == []
        assert result["pushed"] == 1
        assert result["pulled"] == 0

        assert orphan.exists(), (
            "self-heal deleted an on-disk task file it could not read"
        )
        warnings = result.get("warnings", [])
        assert any("2" in w and "not be read" in w for w in warnings), (
            f"no warning surfaced for the unreadable orphan file: {warnings}"
        )
    finally:
        os.chmod(orphan, 0o644)  # let tmp_path's own cleanup remove it


def test_sync_git_write_failure_leaves_local_sqlite_untouched(tmp_path):
    """Red Team's 0226 pass (docs/dogfooding-log.md, commit c453ee3,
    2026-09-04): the worst finding in this whole series. Sync's pull step
    committed the pulled rows to sqlite BEFORE the matching history
    (git) write, with no rollback if that write failed -- so a git-side
    failure left sqlite permanently holding a pull that history never
    recorded, while `sync()`'s own error told the caller "Nothing was
    changed." Repro needs no malice: an unreadable (chmod 000) orphan
    file already sitting at some id, plus a peer whose own next pull
    happens to land at that same numeric id -- routine once two clients
    have synced a few times (ids are assigned per-client, not reserved
    across peers for an id that only exists as an on-disk orphan)."""
    from cadence.store import SyncInconsistent

    store = Store(db_path=tmp_path / "ry_p.db")
    peer = Store(db_path=tmp_path / "ry_q.db")

    store.add("ry task1")  # id 1
    orphan = _orphan_task_path(store, 2)
    orphan.write_text(json.dumps({"bad": True}))
    os.chmod(orphan, 0o000)
    try:
        peer.add("ry2 taskA")  # id 1 on peer
        peer.add("ry2 taskB")  # id 2 on peer -- lands at local id 2 on pull,
        # colliding with the unreadable orphan file already sitting there.

        before = store.list(status="all")
        assert [t.title for t in before] == ["ry task1"]

        with pytest.raises(SyncInconsistent) as exc_info:
            store.sync(remote=str(peer.db_path))
        assert "Rolled back automatically: nothing was changed" in exc_info.value.hint

        after = store.list(status="all")
        assert [t.title for t in after] == ["ry task1"], (
            "sync reported failure but sqlite gained the pulled rows anyway"
        )
    finally:
        os.chmod(orphan, 0o644)


def test_undo_git_write_failure_leaves_sqlite_task_intact(tmp_path):
    """Red Team's 0226 pass (docs/dogfooding-log.md, commit c453ee3,
    2026-09-04): undo's sqlite-side revert (INSERT/DELETE) used to commit
    BEFORE the matching history commit, with no rollback if that commit
    failed -- so a git-side failure (here: an unrelated unreadable file
    elsewhere in the tree breaking `git add -A`) left a real,
    pre-existing task permanently deleted from sqlite while the reported
    error implied nothing had happened."""
    from cadence.store import CadenceError

    store = Store(db_path=tmp_path / "ry_undo.db")
    store.add("keep me")  # id 1 -- the only mutation so far; undo would
    # delete it (its prior state, before this commit, is "doesn't exist").

    orphan = _orphan_task_path(store, 99)
    orphan.write_text(json.dumps({"bad": True}))
    os.chmod(orphan, 0o000)
    try:
        before = store.list(status="all")
        assert [t.title for t in before] == ["keep me"]

        with pytest.raises(CadenceError):
            store.undo()

        after = store.list(status="all")
        assert [t.title for t in after] == ["keep me"], (
            "undo reported failure but deleted the task from sqlite anyway"
        )
    finally:
        os.chmod(orphan, 0o644)
