"""Regression tests for all 5 of 0.2.12 Red Team's findings
(docs/dogfooding-log.md, commit b80ea8e) from the adversarial pass on the
real published cadence-todo==0.2.12 wheel -- the 2 SEVERE ones plus the 3
MODERATE ones fixed in the same pass.

Each reproduces the exact failure mode described in that log entry
against pre-fix code:
  #1 a raw git index.lock race raising HistoryError even though the
     SQLite row already committed (history.py's _LOCK_RETRY_* constants +
     store.py's HistoryDegraded)
  #2 overdue --all-projects silently fabricating an empty store for a
     deleted project (store.py's Store.__init__ `must_exist` branch)
  #3 a non-CadenceError-raising registry entry aborting the whole
     --all-projects call instead of reporting one per-project error
  #4 a relative-path registry entry silently writing a stray db file
     into the caller's cwd
  #5 `register` silently collapsing two different project directories
     onto the single global default store when CADENCE_DB_PATH isn't set
     (registry.py's AmbiguousProject)
"""
from __future__ import annotations

import shutil
import threading
import time

import pytest

import cadence.history as history_mod
from cadence.store import HistoryDegraded, Store


# --- finding #1: concurrent-writer history race --------------------------


def test_add_survives_transient_history_lock_contention(tmp_path):
    """A git index.lock held by another writer for a brief moment (the
    real-world shape of two concurrent `cadence add` processes, or a CLI
    writer racing an MCP add_task) must not make add() fail -- history.py's
    bounded retry should simply wait it out, so the caller never sees a
    false failure for a write that in fact fully succeeded, history
    included."""
    db = tmp_path / "store.db"
    store = Store(db_path=db)
    store.add("warm up")  # ensures the history git repo already exists
    hist_dir = db.parent / (db.name + ".history")
    lock = hist_dir / ".git" / "index.lock"
    lock.write_text("")

    def release():
        time.sleep(0.2)
        lock.unlink()

    threading.Thread(target=release).start()

    task = store.add("survives contention")  # must not raise
    assert task.title == "survives contention"
    # And the history entry was actually recorded once the lock cleared --
    # not just the SQLite row.
    why = store.why(task.id)
    assert why["events"], "history entry should exist once the retry won the race"
    assert why["events"][0]["event"] == "Created"


def test_add_reports_degraded_success_not_a_hard_failure_when_lock_never_clears(
    tmp_path, monkeypatch
):
    """If contention outlasts the whole bounded retry budget (pathological,
    but the original finding's core bug lives here): the SQLite row is
    ALREADY committed by this point (every mutator commits to sqlite
    before attempting the history commit), so the caller must never be
    told the call plainly "failed" -- that lie is exactly what would make
    a reasonable agent retry into a silent duplicate. HistoryDegraded
    carries the already-persisted task instead of a bare HistoryError."""
    monkeypatch.setattr(history_mod, "_LOCK_RETRY_ATTEMPTS", 2)
    monkeypatch.setattr(history_mod, "_LOCK_RETRY_BASE_DELAY", 0.01)
    monkeypatch.setattr(history_mod, "_LOCK_RETRY_MAX_DELAY", 0.01)
    db = tmp_path / "store.db"
    store = Store(db_path=db)
    store.add("warm up")
    hist_dir = db.parent / (db.name + ".history")
    lock = hist_dir / ".git" / "index.lock"
    lock.write_text("")  # never released in this test

    with pytest.raises(HistoryDegraded) as exc_info:
        store.add("lock never clears")

    degraded = exc_info.value
    task = degraded.tasks[0]
    assert task.title == "lock never clears"
    # The core legibility bug: the row must be genuinely there, visible to
    # a caller who (correctly, per the new contract) does not retry.
    titles = [t.title for t in store.list(status="all")]
    assert "lock never clears" in titles
    assert "index.lock" in degraded.reason or "lock" in degraded.reason.lower()
    lock.unlink()


def test_mcp_add_task_reports_ok_true_with_history_recorded_false_on_degraded_history(
    tmp_path, monkeypatch
):
    """Same scenario through the MCP surface (the one Dov's repro calls
    out as sharing add()'s code path): ok must stay True, never False, so
    an agent branching on `ok` per this server's own instructions does not
    treat a persisted write as a failure worth retrying."""
    monkeypatch.setattr(history_mod, "_LOCK_RETRY_ATTEMPTS", 2)
    monkeypatch.setattr(history_mod, "_LOCK_RETRY_BASE_DELAY", 0.01)
    monkeypatch.setattr(history_mod, "_LOCK_RETRY_MAX_DELAY", 0.01)
    monkeypatch.setenv("CADENCE_DB_PATH", str(tmp_path / "store.db"))

    from cadence.mcp_server import add_task

    r0 = add_task(title="warm up")
    assert r0["ok"] is True

    db = tmp_path / "store.db"
    hist_dir = db.parent / (db.name + ".history")
    lock = hist_dir / ".git" / "index.lock"
    lock.write_text("")
    try:
        result = add_task(title="mcp degraded add")
        assert result["ok"] is True
        assert result["history_recorded"] is False
        assert "warning" in result and "do not retry" in result["warning"].lower()
        assert result["task"]["title"] == "mcp degraded add"
    finally:
        lock.unlink()


# --- finding #2: overdue --all-projects recreates a deleted store --------


def test_overdue_all_projects_reports_deleted_project_instead_of_recreating_it(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CADENCE_CONFIG_HOME", str(tmp_path / "config"))
    proj_dir = tmp_path / "proj-a"
    db = proj_dir / "cadence.db"
    Store(db_path=db).add("real task", due="2020-01-01")

    from cadence.registry import register_project

    register_project(db_path=db)
    shutil.rmtree(proj_dir)
    assert not proj_dir.exists()

    from cadence.mcp_server import overdue_tasks

    result = overdue_tasks(all_projects=True)
    assert result["ok"] is True
    assert result["projects"] == 1
    assert result["count"] == 0  # not a phantom "0 overdue" -- it's an error, not a real absence
    errored = [r for r in result["tasks"] if "error" in r]
    assert len(errored) == 1
    assert errored[0]["hint"]
    # The core bug: must NOT have silently fabricated a fresh empty store.
    assert not proj_dir.exists(), "deleted project dir must not be silently recreated"


def test_cli_overdue_all_projects_reports_deleted_project_instead_of_recreating_it(tmp_path):
    import os
    import subprocess
    import sys

    config_home = tmp_path / "config"
    proj_dir = tmp_path / "proj-b"
    db = proj_dir / "cadence.db"
    env = {**os.environ, "CADENCE_CONFIG_HOME": str(config_home), "CADENCE_DB_PATH": str(db), "NO_COLOR": "1"}
    subprocess.run([sys.executable, "-m", "cadence.cli", "add", "real task", "--due", "2020-01-01"], env=env, capture_output=True, text=True, check=True)
    subprocess.run([sys.executable, "-m", "cadence.cli", "register"], env=env, capture_output=True, text=True, check=True)
    shutil.rmtree(proj_dir)

    result = subprocess.run(
        [sys.executable, "-m", "cadence.cli", "overdue", "--all-projects"],
        env={**os.environ, "CADENCE_CONFIG_HOME": str(config_home), "NO_COLOR": "1"},
        capture_output=True,
        text=True,
    )
    # Exit code 2 (store-error class), not 0: this is the 0.2.13-indep Red
    # Team fix (test_0213_all_projects_exit_code.py) -- the sole registered
    # project failing to open is a total failure, not a clean run.
    assert result.returncode == 2
    assert "0 overdue across 1 registered project" not in result.stdout, (
        "must not silently report a phantom zero for a deleted project's store"
    )
    assert "Error" in result.stdout
    assert not proj_dir.exists()


# --- finding #3: one bad registry line must not abort the whole call -----


def test_cli_overdue_all_projects_reports_bad_registry_entry_instead_of_aborting(tmp_path):
    """A registry line that raises something other than CadenceError (a
    null byte reaching mkdir/open raises ValueError, not sqlite3.Error)
    used to escape `cmd_overdue`'s except CadenceError and abort the
    whole command -- zero output for every other, perfectly valid
    registered project. Mirrors the MCP-side regression already covered
    by test_mcp_overdue_all_projects_reports_unreadable_store_without_failing_whole_call
    in test_wow_part2.py; this is the CLI surface of the same bug."""
    import os
    import subprocess
    import sys

    config_home = tmp_path / "config"
    good_dir = tmp_path / "proj-good"
    good_db = good_dir / "cadence.db"
    env_good = {**os.environ, "CADENCE_CONFIG_HOME": str(config_home), "CADENCE_DB_PATH": str(good_db), "NO_COLOR": "1"}
    subprocess.run([sys.executable, "-m", "cadence.cli", "add", "real task", "--due", "2020-01-01"], env=env_good, capture_output=True, text=True, check=True)
    subprocess.run([sys.executable, "-m", "cadence.cli", "register"], env=env_good, capture_output=True, text=True, check=True)

    registry_file = config_home / "projects.txt"
    with open(registry_file, "ab") as f:
        f.write(b"/tmp/gar\x00bage/cadence.db\n")

    result = subprocess.run(
        [sys.executable, "-m", "cadence.cli", "overdue", "--all-projects"],
        env={**os.environ, "CADENCE_CONFIG_HOME": str(config_home), "NO_COLOR": "1"},
        capture_output=True,
        text=True,
    )
    # Exit code 2 (store-error class), not 0: this finding's own contract
    # (keep going, report the good project) is orthogonal to whether the
    # exit code may also carry the "something failed" signal -- see
    # test_0213_all_projects_exit_code.py for the fix that added it.
    assert result.returncode == 2, result.stderr
    assert "real task" in result.stdout, "the good project's overdue task must still be reported"
    assert "proj-good" in result.stdout


# --- finding #4: relative registry path must error, not write into cwd ---


def test_overdue_all_projects_relative_registry_entry_errors_not_writes_into_cwd(tmp_path, monkeypatch):
    monkeypatch.setenv("CADENCE_CONFIG_HOME", str(tmp_path / "config"))
    from cadence.registry import registry_path

    with open(registry_path(), "a") as f:
        f.write("not-a-real-path-relative\n")

    cwd_dir = tmp_path / "somewhere-unrelated"
    cwd_dir.mkdir()
    monkeypatch.chdir(cwd_dir)

    from cadence.mcp_server import overdue_tasks

    result = overdue_tasks(all_projects=True)
    assert result["ok"] is True
    errored = [r for r in result["tasks"] if "error" in r]
    assert len(errored) == 1
    assert not (cwd_dir / "not-a-real-path-relative").exists(), (
        "a relative registry entry must not silently write a stray db "
        "file into the caller's cwd"
    )


# --- finding #5: register must not silently collapse distinct projects ---


def test_register_without_cadence_db_path_refuses_instead_of_using_global_default(tmp_path, monkeypatch):
    monkeypatch.setenv("CADENCE_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("CADENCE_DB_PATH", raising=False)
    monkeypatch.setenv("CADENCE_HOME", str(tmp_path / "global-default"))

    from cadence.store import AmbiguousProject
    from cadence.registry import register_project

    with pytest.raises(AmbiguousProject):
        register_project()


def test_cli_register_without_cadence_db_path_errors_instead_of_silently_registering(tmp_path):
    # Deliberately no `cwd=` override (house convention per test_wow_part2.py's
    # _run_cli: this sandbox has no editable install, only a gitignored
    # `cadence -> src/cadence` symlink at the repo root that makes `python -m
    # cadence.cli` resolvable via `-m`'s cwd-based sys.path[0] only when cwd
    # IS the repo root -- a real `pip install -e .` doesn't need this, but
    # nothing in this suite relies on changing cwd). The bug itself is about
    # CADENCE_DB_PATH being unset, not about which directory the command runs
    # from, so this reproduces it just as well without moving cwd.
    import os
    import subprocess
    import sys

    config_home = tmp_path / "config"
    global_home = tmp_path / "global-default"
    env = {**os.environ, "CADENCE_CONFIG_HOME": str(config_home), "CADENCE_HOME": str(global_home), "NO_COLOR": "1"}
    env.pop("CADENCE_DB_PATH", None)

    result = subprocess.run([sys.executable, "-m", "cadence.cli", "register"], env=env, capture_output=True, text=True)

    assert result.returncode != 0
    assert "CADENCE_DB_PATH" in result.stdout
    registry_file = config_home / "projects.txt"
    assert not registry_file.exists() or registry_file.read_text().strip() == "", (
        "must not have registered the ambiguous global default store"
    )
