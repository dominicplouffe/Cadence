"""Regression tests for the 0.2.13-independent Red Team finding (Dov
Ferreira, 2026-08-31, re-verifying the published 0.2.13 wheel in a fresh
venv/$HOME): `cadence overdue --all-projects` and `cadence sync
--all-projects` always exited 0, even when EVERY registered project failed
to open. The 0.2.13 fix added per-project error *text* lines but never
touched the exit code, so a script/agent following the exit-code contract
docs/human-surface.md §4.4 promises ("so a script can tell 'you asked
wrong' from 'we broke' apart programmatically") got zero signal from
`--all-projects` even on total failure.

Fix: both commands now exit 2 (the same store-error class single-project
commands already use) when at least one registered entry couldn't be
opened, while still printing every line and not aborting early -- the
"keep going" behavior from the 0.2.13 fix is unchanged and re-asserted
here as a regression guard.
"""
from __future__ import annotations

import os
import subprocess
import sys


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


def _write_only_bad_registry(config_home):
    registry_file = config_home / "projects.txt"
    registry_file.parent.mkdir(parents=True, exist_ok=True)
    with open(registry_file, "ab") as f:
        f.write(b"/tmp/gar\x00bage-1/cadence.db\n")  # embedded null byte
        f.write(b"not-a-real-path-relative\n")  # relative, rejected
    return registry_file


# --- overdue --all-projects -------------------------------------------


def test_cli_overdue_all_projects_exits_nonzero_when_every_entry_fails_to_open(tmp_path):
    config_home = tmp_path / "config"
    _write_only_bad_registry(config_home)

    result = _run_cli("overdue", "--all-projects", env=_cli_env(config_home))
    assert result.returncode == 2, result.stdout
    assert "could not be opened" in result.stdout


def test_cli_overdue_all_projects_exits_nonzero_with_mixed_good_and_bad_entries(tmp_path):
    """A mix of good + bad entries must still print the good project's
    overdue line in full (the 0.2.13 'keep going' behavior, unchanged) and
    must exit non-zero because at least one entry failed."""
    config_home = tmp_path / "config"
    good_dir = tmp_path / "proj-good"
    env_good = _cli_env(config_home, db_path=good_dir / "cadence.db")
    _run_cli("register", env=env_good)
    add = _run_cli("add", "Overdue thing", "--due", "2020-01-01", env=env_good)
    assert add.returncode == 0, add.stderr

    with open(config_home / "projects.txt", "ab") as f:
        f.write(b"/tmp/gar\x00bage/cadence.db\n")

    result = _run_cli("overdue", "--all-projects", env=_cli_env(config_home))
    assert "Overdue thing" in result.stdout, result.stdout
    assert "proj-good" in result.stdout
    assert result.returncode == 2, result.stdout


def test_cli_overdue_all_projects_still_exits_zero_when_every_entry_is_healthy(tmp_path):
    """Regression guard: a fully-healthy registry (no per-project errors at
    all) must not start failing now that errors carry a real exit code."""
    config_home = tmp_path / "config"
    good_dir = tmp_path / "proj-clean"
    env_good = _cli_env(config_home, db_path=good_dir / "cadence.db")
    _run_cli("register", env=env_good)
    add = _run_cli("add", "Not overdue", "--due", "2099-01-01", env=env_good)
    assert add.returncode == 0, add.stderr

    result = _run_cli("overdue", "--all-projects", env=_cli_env(config_home))
    assert result.returncode == 0, result.stdout


def test_cli_overdue_all_projects_still_exits_zero_with_no_registry(tmp_path):
    """Regression guard: the 'nothing registered yet' message is not an
    error and must stay exit 0."""
    result = _run_cli("overdue", "--all-projects", env=_cli_env(tmp_path / "config"))
    assert result.returncode == 0, result.stdout


# --- sync --all-projects ------------------------------------------------


def test_cli_sync_all_projects_exits_nonzero_when_every_entry_fails_to_open(tmp_path):
    config_home = tmp_path / "config"
    _write_only_bad_registry(config_home)

    result = _run_cli("sync", "--all-projects", env=_cli_env(config_home))
    assert result.returncode == 2, result.stdout
    assert "Error" in result.stdout


def test_cli_sync_all_projects_exits_nonzero_with_mixed_good_and_bad_entries(tmp_path):
    """A registered project with no remote configured yet is a store-level
    sync failure (raises CadenceError, printed as a per-project 'Error:'
    line already) -- mixed with one entry that can't even be opened, the
    call must still report both lines and exit non-zero."""
    config_home = tmp_path / "config"
    good_dir = tmp_path / "proj-no-remote"
    env_good = _cli_env(config_home, db_path=good_dir / "cadence.db")
    _run_cli("register", env=env_good)
    _run_cli("add", "Needs a remote first", env=env_good)

    with open(config_home / "projects.txt", "ab") as f:
        f.write(b"not-a-real-path-relative\n")

    result = _run_cli("sync", "--all-projects", env=_cli_env(config_home))
    assert "proj-no-remote" in result.stdout, result.stdout
    assert "no remote configured" in result.stdout
    assert result.returncode == 2, result.stdout


def test_cli_sync_all_projects_still_exits_zero_with_no_registry(tmp_path):
    result = _run_cli("sync", "--all-projects", env=_cli_env(tmp_path / "config"))
    assert result.returncode == 0, result.stdout
