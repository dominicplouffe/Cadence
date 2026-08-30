"""Regression tests for docs/wow-spec.md Part III (cleared by design veto,
commit 455ac93 on main): the optional `reason` argument on decompose/
reprioritise/schedule, and the new `cadence why` / `why_task` verb.

Each surface (store, CLI, MCP) is exercised, per project rule -- a
capability that only works on one surface is caught here.
"""
import os
import subprocess
import sys

import pytest

from cadence.store import CadenceError, Store, TaskNotFound


@pytest.fixture
def store(tmp_path):
    return Store(db_path=tmp_path / "cadence.db")


def _cli_env(tmp_path, name="cli.db"):
    return {**os.environ, "CADENCE_DB_PATH": str(tmp_path / name), "NO_COLOR": "1"}


def _run_cli(*args, env):
    return subprocess.run(
        [sys.executable, "-m", "cadence.cli", *args],
        capture_output=True,
        text=True,
        env=env,
    )


# --- store: reason/source recorded, additive and optional -----------------


def test_reprioritise_without_reason_is_unaffected(store):
    task = store.add("Ship it")
    updated = store.reprioritise(task.id, "high")
    assert updated.priority == "high"
    result = store.why(task.id)
    events = result["events"]
    reprio = events[0]
    assert reprio["event"] == "Reprioritised (none → high)"
    assert reprio["reason"] is None
    assert reprio["source"] is None


def test_reprioritise_with_reason_is_recorded(store):
    task = store.add("Ship it")
    store.reprioritise(task.id, "high", reason="customer escalation", source="mcp")
    events = store.why(task.id)["events"]
    assert events[0]["reason"] == "customer escalation"
    assert events[0]["source"] == "mcp"


def test_schedule_with_reason_is_recorded(store):
    task = store.add("Renew TLS cert")
    store.schedule(task.id, "2026-09-10", reason="cert expires that week", source="cli")
    events = store.why(task.id)["events"]
    assert events[0]["event"] == "Scheduled for 2026-09-10"
    assert events[0]["reason"] == "cert expires that week"
    assert events[0]["source"] == "cli"


def test_decompose_with_reason_recorded_on_every_child_and_parent(store):
    parent = store.add("Plan the party")
    _, children = store.decompose(
        parent.id, ["Book a venue", "Order a cake"],
        reason="breaking the vague ask into things I can check off",
        source="mcp",
    )
    for child in children:
        events = store.why(child.id)["events"]
        created = events[-1]
        assert created["event"] == f"Created as subtask of #{parent.id} (Plan the party)"
        assert created["reason"] == "breaking the vague ask into things I can check off"
        assert created["source"] == "mcp"


def test_reason_does_not_change_sync_merge_behavior(tmp_path):
    """wow-spec.md Part III: additive only -- commit bodies are never
    diffed by the merge engine, so a task with a reason syncs exactly like
    one without."""
    a = Store(db_path=tmp_path / "a.db")
    b = Store(db_path=tmp_path / "b.db")
    task = a.add("Shared task")
    a.reprioritise(task.id, "high", reason="urgent", source="cli")
    a.sync(remote=str(tmp_path / "b.db"))
    b.sync(remote=str(tmp_path / "a.db"))
    assert b.get(task.id).priority == "high"
    # already_synced now that both sides match
    result = a.sync(remote=str(tmp_path / "b.db"))
    assert result["already_synced"] is True


# --- store: why() ----------------------------------------------------------


def test_why_missing_id_raises_named_error(store):
    with pytest.raises(TaskNotFound) as excinfo:
        store.why(999)
    assert "no task with id 999" in str(excinfo.value)
    assert excinfo.value.hint


def test_why_orders_newest_first_and_reports_priority_after_each_event(store):
    task = store.add("Book a venue")
    store.reprioritise(task.id, "med")
    store.reprioritise(task.id, "high")
    events = store.why(task.id)["events"]
    assert [e["event"] for e in events] == [
        "Reprioritised (med → high)",
        "Reprioritised (none → med)",
        "Created",
    ]
    assert [e["priority"] for e in events] == ["high", "med", "none"]


def test_why_add_and_done_events_are_not_reason_capable(store):
    task = store.add("Buy milk")
    store.complete(task.id)
    events = store.why(task.id)["events"]
    completed, created = events[0], events[1]
    assert completed["event"] == "Completed"
    assert completed["reason_capable"] is False
    assert created["event"] == "Created"
    assert created["reason_capable"] is False


def test_why_reprioritise_event_is_reason_capable_even_without_one(store):
    task = store.add("Buy milk")
    store.reprioritise(task.id, "low")
    events = store.why(task.id)["events"]
    assert events[0]["reason_capable"] is True
    assert events[0]["reason"] is None


def test_why_marks_undone_change_distinctly(store):
    task = store.add("Book a venue")
    store.reprioritise(task.id, "high", reason="urgent", source="cli")
    store.undo()
    events = store.why(task.id)["events"]
    undone = events[0]
    assert undone["event"] == "Reprioritised (high → none) undone"
    # The undo commit itself carries no reason of its own.
    assert undone["reason"] is None


# --- CLI: cadence why -------------------------------------------------------


def test_cli_why_missing_id_matches_444_wording(tmp_path):
    env = _cli_env(tmp_path)
    result = _run_cli("why", "99", env=env)
    assert result.returncode == 1
    assert result.stdout.startswith("Error: no task with id 99. Run 'cadence list' to see valid ids.")


def test_cli_why_end_to_end_with_reason_and_glyph(tmp_path):
    env = _cli_env(tmp_path)
    assert _run_cli("add", "Plan the party", env=env).returncode == 0
    decomposed = _run_cli(
        "decompose", "1", "--into", "Book a venue",
        "--reason", "breaking the vague ask into things I can check off",
        env=env,
    )
    assert decomposed.returncode == 0
    repri = _run_cli(
        "reprioritise", "2", "high",
        "--reason", "venues book up fast",
        env=env,
    )
    assert repri.returncode == 0

    why = _run_cli("why", "2", env=env)
    assert why.returncode == 0
    out = why.stdout
    assert "#2 Book a venue — history (newest first):" in out
    # Non-TTY/NO_COLOR fallback bullet, distinct from `list`'s "[ ]"/"○".
    assert "  -  high" in out
    assert "Reprioritised (none → high)" in out
    assert '"venues book up fast" — you, via CLI' in out
    assert "Created as subtask of #1 (Plan the party)" in out
    assert '"breaking the vague ask into things I can check off"' in out
    # Attribution may wrap onto its own line -- check content, not exact wrap point.
    assert "you, via CLI" in out


def test_cli_why_no_reason_gives_actionable_note_not_a_dead_end(tmp_path):
    env = _cli_env(tmp_path)
    _run_cli("add", "Buy milk", env=env)
    _run_cli("reprioritise", "1", "high", env=env)
    result = _run_cli("why", "1", env=env)
    assert result.returncode == 0
    assert "No reason was recorded for this change." in result.stdout
    assert "--reason" in result.stdout


def test_cli_why_iso_flag_shows_absolute_timestamp(tmp_path):
    env = _cli_env(tmp_path)
    _run_cli("add", "Buy milk", env=env)
    result = _run_cli("why", "1", "--iso", env=env)
    assert result.returncode == 0
    # ISO-8601 date at the start of the timestamp column, not "just now".
    assert "just now" not in result.stdout
    import re

    assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", result.stdout)


# --- MCP: why_task and reason params ----------------------------------------


def test_mcp_why_task_and_reason_params(tmp_path, monkeypatch):
    monkeypatch.setenv("CADENCE_DB_PATH", str(tmp_path / "mcp.db"))
    from cadence.mcp_server import add_task, decompose_task, reprioritise_task, why_task

    added = add_task("Plan the party")
    parent_id = added["task"]["id"]
    decomposed = decompose_task(
        parent_id, ["Book a venue"], reason="breaking it down"
    )
    assert decomposed["ok"] is True
    child_id = decomposed["subtasks"][0]["id"]

    repri = reprioritise_task(child_id, "high", reason="urgent")
    assert repri["ok"] is True

    result = why_task(child_id)
    assert result["ok"] is True
    assert result["task"]["id"] == child_id
    history = result["history"]
    assert history[0]["event"] == "Reprioritised (none → high)"
    assert history[0]["reason"] == "urgent"
    assert history[0]["source"] == "mcp"
    assert history[1]["event"] == f"Created as subtask of #{parent_id} (Plan the party)"
    assert history[1]["reason"] == "breaking it down"
    assert history[1]["source"] == "mcp"


def test_mcp_why_task_missing_id_is_structured_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("CADENCE_DB_PATH", str(tmp_path / "mcp2.db"))
    from cadence.mcp_server import why_task

    missing = why_task(999)
    assert missing["ok"] is False
    assert missing["error"] == "task_not_found"
    assert "no task with id 999" in missing["message"]
    assert missing["hint"]


def test_mcp_schedule_task_reason_is_optional(tmp_path, monkeypatch):
    monkeypatch.setenv("CADENCE_DB_PATH", str(tmp_path / "mcp3.db"))
    from cadence.mcp_server import add_task, schedule_task, why_task

    added = add_task("Renew TLS cert")
    task_id = added["task"]["id"]
    scheduled = schedule_task(task_id, "2026-09-10")
    assert scheduled["ok"] is True
    result = why_task(task_id)
    assert result["history"][0]["reason"] is None
    assert result["history"][0]["source"] is None


# --- Red Team 0.2.7 finding #1: multi-line `reason` continuation lines -----
# were silently dropped on read (history.parse_trailers only captured the
# one line immediately after `Reason: `/`Source: `) even though the write
# side (git commit body) always held the full text. These pin the fix on
# all three reason-capable verbs and on both surfaces.

_MULTILINE_REASON = (
    "First consideration: budget is tight.\n"
    "Second consideration: timeline is short.\n"
    "Third: stakeholder prefers Tuesday."
)


def test_why_reprioritise_multiline_reason_keeps_every_line(store):
    task = store.add("Ship it")
    store.reprioritise(task.id, "med", reason=_MULTILINE_REASON, source="cli")
    events = store.why(task.id)["events"]
    assert events[0]["reason"] == _MULTILINE_REASON


def test_why_schedule_multiline_reason_keeps_every_line(store):
    task = store.add("Renew TLS cert")
    store.schedule(task.id, "2026-09-10", reason=_MULTILINE_REASON, source="cli")
    events = store.why(task.id)["events"]
    assert events[0]["reason"] == _MULTILINE_REASON


def test_why_decompose_multiline_reason_keeps_every_line(store):
    parent = store.add("Plan the party")
    _, children = store.decompose(
        parent.id, ["Book a venue"], reason=_MULTILINE_REASON, source="mcp"
    )
    events = store.why(children[0].id)["events"]
    assert events[-1]["reason"] == _MULTILINE_REASON


def test_cli_why_shows_every_line_of_a_multiline_reason(tmp_path):
    env = _cli_env(tmp_path)
    _run_cli("add", "Ship it", env=env)
    result = _run_cli(
        "reprioritise", "1", "med", "--reason", _MULTILINE_REASON, env=env
    )
    assert result.returncode == 0
    out = _run_cli("why", "1", env=env).stdout
    assert "First consideration" in out
    assert "Second consideration" in out
    assert "Third: stakeholder prefers Tuesday" in out


def test_mcp_why_task_multiline_reason_keeps_every_line(tmp_path, monkeypatch):
    monkeypatch.setenv("CADENCE_DB_PATH", str(tmp_path / "mcp4.db"))
    from cadence.mcp_server import add_task, reprioritise_task, why_task

    added = add_task("Ship it")
    task_id = added["task"]["id"]
    reprioritise_task(task_id, "med", reason=_MULTILINE_REASON)
    result = why_task(task_id)
    assert result["history"][0]["reason"] == _MULTILINE_REASON


# --- Red Team 0.2.7 finding #2: `why`'s reason wrap must respect COLUMNS ---
# like `list`'s title wrap already does, instead of a hardcoded width=56.


def test_cli_why_reason_wrap_respects_columns_like_list_does(tmp_path):
    env = _cli_env(tmp_path)
    _run_cli("add", "Ship it", env=env)
    long_reason = "word " * 40
    _run_cli("reprioritise", "1", "high", "--reason", long_reason.strip(), env=env)
    narrow = _run_cli("why", "1", env={**env, "COLUMNS": "40"})
    wide = _run_cli("why", "1", env={**env, "COLUMNS": "200"})
    assert narrow.returncode == 0 and wide.returncode == 0
    assert narrow.stdout != wide.stdout
    # Narrower terminal must wrap onto strictly more lines than the wide one.
    assert narrow.stdout.count("\n") > wide.stdout.count("\n")


# --- Red Team 0.2.8 finding #1: a `reason` whose own text starts a line ----
# with "Reason: " or "Source: " must never be treated as opening a NEW
# trailer -- `parse_trailers` (history.py) used to re-match on line prefix
# alone, silently dropping everything captured before the collision (in the
# self-collision case, permanently, from every surface but raw `git log`).

_SELF_COLLIDING_REASON = (
    "Kickoff notes below.\n"
    "Reason: client asked for it verbally.\n"
    "Follow up next week."
)


def test_why_reprioritise_reason_surviving_its_own_reason_prefix_collision(store):
    """The reason's own middle line starts with 'Reason: ' -- plausible
    task-management prose, not an adversarial string -- and must not
    truncate everything recorded before it."""
    task = store.add("Ship it")
    store.reprioritise(task.id, "high", reason=_SELF_COLLIDING_REASON, source="cli")
    events = store.why(task.id)["events"]
    assert events[0]["reason"] == _SELF_COLLIDING_REASON


def test_why_reprioritise_reason_surviving_a_source_prefix_collision(store):
    """Same class, `Source: ` this time, and not on the message's last
    line -- must still be treated as reason content, not as the real
    `Source:` trailer (which only ever legally sits on the last line)."""
    reason = "Line one.\nSource: not the real source line.\nLine three."
    task = store.add("Ship it")
    store.reprioritise(task.id, "high", reason=reason, source="cli")
    events = store.why(task.id)["events"]
    assert events[0]["reason"] == reason
    assert events[0]["source"] == "cli"


def test_cli_why_shows_every_line_despite_self_colliding_reason_prefix(tmp_path):
    env = _cli_env(tmp_path)
    _run_cli("add", "Ship it", env=env)
    result = _run_cli(
        "reprioritise", "1", "high", "--reason", _SELF_COLLIDING_REASON, env=env
    )
    assert result.returncode == 0
    out = _run_cli("why", "1", env=env).stdout
    assert "Kickoff notes below" in out
    assert "client asked for it verbally" in out
    assert "Follow up next week" in out


def test_mcp_why_task_survives_self_colliding_reason_prefix(tmp_path, monkeypatch):
    monkeypatch.setenv("CADENCE_DB_PATH", str(tmp_path / "mcp5.db"))
    from cadence.mcp_server import add_task, reprioritise_task, why_task

    added = add_task("Ship it")
    task_id = added["task"]["id"]
    reprioritise_task(task_id, "high", reason=_SELF_COLLIDING_REASON)
    result = why_task(task_id)
    assert result["history"][0]["reason"] == _SELF_COLLIDING_REASON


# --- Red Team 0.2.8 finding #2: `cmd_why`'s COLUMNS-aware wrap must ---------
# subtract its own rendered indent (23 cols for the reason quote) the same
# way `cmd_list` subtracts its layout overhead -- otherwise reason lines
# render wider than the terminal at narrow COLUMNS instead of narrower.


def test_cli_why_output_never_exceeds_narrow_columns(tmp_path):
    env = _cli_env(tmp_path)
    _run_cli("add", "Repro task collision", env=env)
    _run_cli(
        "reprioritise", "1", "high", "--reason", _SELF_COLLIDING_REASON, env=env
    )
    out = _run_cli("why", "1", env={**env, "COLUMNS": "40"}).stdout
    longest = max((len(line) for line in out.splitlines()), default=0)
    assert longest <= 40


# --- git >=2.42-style "Z" author-date suffix, Python 3.10 -----------------
# `%aI` (git's strict-ISO-8601 author date) prints a trailing "Z" for a
# +00:00 offset on newer git; `datetime.fromisoformat` only accepts that
# form starting in Python 3.11. A real GitHub Actions run on the 3.10 leg
# hit exactly this: the un-normalized "Z" string fell through
# `_relative_time`'s except-clause fallback as `when`, and that ~20-char
# string was wide enough to shrink `cmd_why`'s event-text wrap budget
# below a single word's length, overflowing COLUMNS=40 even though the
# reason-quote wrap (this file's `test_cli_why_output_never_exceeds_narrow_columns`)
# was already correct. `_relative_time` must produce "just now" for a
# recent Z-suffixed timestamp exactly like it does for a +00:00 one.


def test_relative_time_accepts_git_z_suffix_offset():
    import datetime

    from cadence.cli import _relative_time

    now_z = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    assert _relative_time(now_z) == "just now"
