"""Regression tests for docs/wow-spec.md Part II (cross-project register/
overdue/sync -- the multi-project answer, task R-07 follow-on).

Same per-surface discipline as test_wow_part3.py: registry (store-level),
CLI, and MCP are each exercised, since a capability that only works on one
surface is caught here.
"""
import datetime
import os
import subprocess
import sys

import pytest

from cadence.registry import project_name, read_registry, register_project, registry_path
from cadence.store import Store


def _yesterday(days=1):
    return (datetime.date.today() - datetime.timedelta(days=days)).isoformat()


def _cli_env(config_home, db_path=None, no_color=True):
    env = {**os.environ, "CADENCE_CONFIG_HOME": str(config_home)}
    if db_path is not None:
        env["CADENCE_DB_PATH"] = str(db_path)
    else:
        env.pop("CADENCE_DB_PATH", None)
    if no_color:
        env["NO_COLOR"] = "1"
    return env


def _run_cli(*args, env, cwd=None):
    return subprocess.run(
        [sys.executable, "-m", "cadence.cli", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
    )


# --- registry (store-level) -------------------------------------------


def test_register_project_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("CADENCE_CONFIG_HOME", str(tmp_path / "config"))
    db = tmp_path / "proj-alpha" / "cadence.db"
    path1, already1 = register_project(db_path=db)
    assert already1 is False
    path2, already2 = register_project(db_path=db)
    assert already2 is True
    assert path1 == path2
    entries = read_registry()
    assert entries == [path1]  # not duplicated


def test_register_project_two_different_projects_both_kept_in_order(tmp_path, monkeypatch):
    monkeypatch.setenv("CADENCE_CONFIG_HOME", str(tmp_path / "config"))
    db_a = tmp_path / "proj-alpha" / "cadence.db"
    db_b = tmp_path / "proj-beta" / "cadence.db"
    register_project(db_path=db_a)
    register_project(db_path=db_b)
    entries = read_registry()
    assert len(entries) == 2
    assert project_name(entries[0]) == "proj-alpha"
    assert project_name(entries[1]) == "proj-beta"


def test_registry_file_is_plain_text_one_path_per_line(tmp_path, monkeypatch):
    monkeypatch.setenv("CADENCE_CONFIG_HOME", str(tmp_path / "config"))
    db = tmp_path / "proj-gamma" / "cadence.db"
    register_project(db_path=db)
    text = registry_path().read_text()
    lines = text.splitlines()
    assert len(lines) == 1
    assert lines[0] == str(db.resolve())


# --- overdue --all-projects (CLI) --------------------------------------


def test_cli_overdue_all_projects_with_no_registry_gives_named_message(tmp_path):
    env = _cli_env(tmp_path / "config")
    result = _run_cli("overdue", "--all-projects", env=env)
    assert result.returncode == 0
    assert "register" in result.stdout.lower()


def test_cli_overdue_all_projects_merges_across_registered_projects(tmp_path):
    config_home = tmp_path / "config"
    alpha_dir = tmp_path / "proj-alpha"
    beta_dir = tmp_path / "proj-beta"
    alpha_dir.mkdir()
    beta_dir.mkdir()

    # Register + seed proj-alpha with one overdue, one not-yet-due task.
    env_a = _cli_env(config_home, db_path=alpha_dir / "cadence.db")
    _run_cli("register", env=env_a)
    _run_cli("add", "Write onboarding docs", "--due", _yesterday(9), env=env_a)
    _run_cli("add", "Not due yet", "--due", "2099-01-01", env=env_a)

    # Register + seed proj-beta with one overdue task.
    env_b = _cli_env(config_home, db_path=beta_dir / "cadence.db")
    _run_cli("register", env=env_b)
    _run_cli("add", "Renew TLS cert", "--due", _yesterday(3), env=env_b)

    result = _run_cli("overdue", "--all-projects", env=_cli_env(config_home))
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "proj-alpha" in out
    assert "Write onboarding docs" in out
    assert "proj-beta" in out
    assert "Renew TLS cert" in out
    assert "Not due yet" not in out
    # §4.1 binding note: the `!` glyph (or [!] no-color fallback) leads
    # every row, same pairing rule as single-project `list`/`overdue`.
    for line in out.splitlines():
        if "Write onboarding docs" in line or "Renew TLS cert" in line:
            assert line.strip().startswith("[!]") or line.strip().startswith("!")
    assert "2 overdue across 2 registered projects" in out


def test_cli_register_is_idempotent_end_to_end(tmp_path):
    config_home = tmp_path / "config"
    proj_dir = tmp_path / "proj-solo"
    proj_dir.mkdir()
    env = _cli_env(config_home, db_path=proj_dir / "cadence.db")
    r1 = _run_cli("register", env=env)
    r2 = _run_cli("register", env=env)
    assert r1.returncode == 0 and r2.returncode == 0
    assert "Registered" in r1.stdout
    assert "Already registered" in r2.stdout
    lines = (config_home / "projects.txt").read_text().splitlines()
    assert len(lines) == 1


# --- sync --all-projects (CLI) ------------------------------------------


@pytest.fixture
def bare_remote(tmp_path):
    remote_path = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(remote_path)],
        check=True,
        capture_output=True,
    )
    return str(remote_path)


def test_cli_sync_all_projects_reports_pulled_pushed_per_project_line(tmp_path, bare_remote):
    config_home = tmp_path / "config"
    alpha_dir = tmp_path / "proj-alpha"
    alpha_dir.mkdir()
    env_a = _cli_env(config_home, db_path=alpha_dir / "cadence.db")
    _run_cli("register", env=env_a)
    _run_cli("add", "Ship the auth fix", env=env_a)
    # Give proj-alpha a configured remote (a single-project sync, as the
    # spec's device-B walkthrough assumes each project already has).
    r_first = _run_cli("sync", "--remote", bare_remote, env=env_a)
    assert r_first.returncode == 0, r_first.stderr

    # A second client pushes a task of its own to the same remote, so
    # proj-alpha's next --all-projects sync has something real to pull
    # (not just "already in sync") -- this is the pulled/pushed count the
    # single-project command already reports, which --all-projects must
    # not collapse away.
    peer_dir = tmp_path / "peer-b"
    peer_dir.mkdir()
    env_peer = {**os.environ, "CADENCE_DB_PATH": str(peer_dir / "cadence.db"), "NO_COLOR": "1"}
    _run_cli("sync", "--remote", bare_remote, env=env_peer)
    _run_cli("add", "From peer B", env=env_peer)
    _run_cli("sync", env=env_peer)

    result = _run_cli("sync", "--all-projects", env=_cli_env(config_home))
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "proj-alpha" in out
    # Same per-project shape single-project sync already gives: pulled/
    # pushed counts, never silently collapsed past that detail.
    assert "pulled" in out and "pushed" in out


def test_cli_sync_all_projects_no_registry_gives_named_message(tmp_path):
    env = _cli_env(tmp_path / "config")
    result = _run_cli("sync", "--all-projects", env=env)
    assert result.returncode == 0
    assert "register" in result.stdout.lower()


def test_cli_sync_all_projects_surfaces_conflict_recovery_line(tmp_path, bare_remote):
    config_home = tmp_path / "config"
    a_dir = tmp_path / "proj-conf"
    a_dir.mkdir()
    env_a = _cli_env(config_home, db_path=a_dir / "cadence.db")
    _run_cli("register", env=env_a)
    add = _run_cli("add", "Contested task", env=env_a)
    assert add.returncode == 0
    _run_cli("sync", "--remote", bare_remote, env=env_a)

    # A second client (b_dir) pulls the same task, edits it, and pushes --
    # so proj-conf's next --all-projects sync (from A's registry) has to
    # resolve a real conflict against a fresh, unrelated edit made on A too.
    b_dir = tmp_path / "peer-b"
    b_dir.mkdir()
    env_b = {**os.environ, "CADENCE_DB_PATH": str(b_dir / "cadence.db"), "NO_COLOR": "1"}
    _run_cli("sync", "--remote", bare_remote, env=env_b)
    _run_cli("reprioritise", "1", "high", env=env_b)
    _run_cli("sync", env=env_b)

    _run_cli("reprioritise", "1", "low", env=env_a)
    result = _run_cli("sync", "--all-projects", env=_cli_env(config_home))
    out = result.stdout
    assert "conflict" in out.lower()
    assert "--keep-mine" in out and "--keep-theirs" in out


# --- MCP surface ---------------------------------------------------------


def test_mcp_register_and_overdue_all_projects(tmp_path, monkeypatch):
    monkeypatch.setenv("CADENCE_CONFIG_HOME", str(tmp_path / "config"))
    from cadence.mcp_server import overdue_tasks
    from cadence.mcp_server import register_project as mcp_register

    alpha_db = tmp_path / "proj-alpha" / "cadence.db"
    beta_db = tmp_path / "proj-beta" / "cadence.db"

    monkeypatch.setenv("CADENCE_DB_PATH", str(alpha_db))
    Store(db_path=alpha_db).add("Write onboarding docs", due=_yesterday(9))
    r1 = mcp_register()
    assert r1["ok"] is True
    assert r1["already_registered"] is False
    r1b = mcp_register()
    assert r1b["ok"] is True
    assert r1b["already_registered"] is True

    monkeypatch.setenv("CADENCE_DB_PATH", str(beta_db))
    Store(db_path=beta_db).add("Renew TLS cert", due=_yesterday(3))
    mcp_register()

    result = overdue_tasks(all_projects=True)
    assert result["ok"] is True
    assert result["projects"] == 2
    assert result["count"] == 2
    projects_seen = {t["project"] for t in result["tasks"]}
    assert projects_seen == {"proj-alpha", "proj-beta"}


def test_mcp_overdue_all_projects_reports_unreadable_store_without_failing_whole_call(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CADENCE_CONFIG_HOME", str(tmp_path / "config"))
    from cadence.mcp_server import overdue_tasks
    from cadence.mcp_server import register_project as mcp_register

    good_db = tmp_path / "proj-good" / "cadence.db"
    monkeypatch.setenv("CADENCE_DB_PATH", str(good_db))
    Store(db_path=good_db).add("Overdue thing", due=_yesterday(1))
    mcp_register()

    # A registry entry pointing at a path that can never be a store (a
    # directory, not a file) -- the "one bad project doesn't sink the
    # whole call" case this task's goal calls for.
    from cadence.registry import registry_path

    bad_dir = tmp_path / "not-a-store"
    bad_dir.mkdir()
    with open(registry_path(), "a") as f:
        f.write(str(bad_dir) + "\n")

    result = overdue_tasks(all_projects=True)
    assert result["ok"] is True
    assert result["projects"] == 2
    assert result["count"] == 1  # only the good store's task counted
    errored = [r for r in result["tasks"] if "error" in r]
    assert len(errored) == 1
    assert errored[0]["hint"]


def test_mcp_sync_tasks_all_projects_loops_over_registry(tmp_path, monkeypatch, bare_remote):
    monkeypatch.setenv("CADENCE_CONFIG_HOME", str(tmp_path / "config"))
    from cadence.mcp_server import register_project as mcp_register
    from cadence.mcp_server import sync_tasks

    proj_db = tmp_path / "proj-solo" / "cadence.db"
    monkeypatch.setenv("CADENCE_DB_PATH", str(proj_db))
    Store(db_path=proj_db).add("Ship it")
    mcp_register()
    first = sync_tasks(remote=bare_remote)
    assert first["ok"] is True

    result = sync_tasks(all_projects=True)
    assert result["ok"] is True
    assert result["projects"] == 1
    assert result["results"][0]["project"] == "proj-solo"
    assert result["results"][0]["ok"] is True
    assert "pulled" in result["results"][0]
