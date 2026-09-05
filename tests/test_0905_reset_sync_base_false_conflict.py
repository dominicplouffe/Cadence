"""Dov's independent 0.2.35 pass (docs/dogfooding-log.md 2026-09-05,
MEDIUM-HIGH,
/workspace/redteam_0235_indep/findings/2026-09-05-0235-reset-sync-base-conflict-storm.md):
the 0.2.35 fix for the silent-overwrite bug (test_0904b) closed that hole by
forcing `base=None` for every row on a `--reset-sync-base` call, unconditionally.
That is safe but too blunt: it also makes any row the REMOTE ALONE changed
since the rewrite look like "both sides changed it", with a message that
flatly (and falsely) states this client made an edit it never made.

Exact scenario: A creates two tasks and pushes both. B pulls both -- both
now share a real sync-base. A edits ONE of them (#2) again and pushes; B
never touches EITHER task, ever, proven by B's own first-parent git log
having zero commits against tasks/2.json after its creation. B's own
history is then rewritten outside Cadence. B follows the documented
`--reset-sync-base` recovery. Before this fix: task #2 was reported as
"edited on both this client and the remote" -- false -- forcing a manual
--keep-mine/--keep-theirs the user should never have had to make. This test
confirms task #2 now applies cleanly (pulled, no conflict), while the
already-covered "both genuinely edited" case (test_0904b) and "task nobody
touched" case both keep working exactly as before.
"""
import subprocess

import pytest

from cadence.store import HistoryRewritten, Store


def _git(repo_dir, *args):
    return subprocess.run(
        ["git", "-C", str(repo_dir), "-c", "user.email=x@x", "-c", "user.name=x", *args],
        check=True, capture_output=True, text=True,
    )


@pytest.fixture
def bare_remote(tmp_path):
    remote_path = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(remote_path)],
        check=True, capture_output=True,
    )
    return str(remote_path)


def test_reset_sync_base_does_not_falsely_conflict_a_remote_only_change(tmp_path, bare_remote):
    store_a = Store(db_path=tmp_path / "a.db")
    store_b = Store(db_path=tmp_path / "b.db")

    # A creates two tasks, pushes both. B pulls both -- both now share a
    # real sync-base for both rows.
    untouched = store_a.add("Task R (stays untouched by everyone)")
    remote_only = store_a.add("Task Q (only A will ever edit)")
    store_a.sync(remote=bare_remote)
    store_b.sync(remote=bare_remote)
    assert {t.title for t in store_b.list(status="all")} == {untouched.title, remote_only.title}

    # A edits ONLY task Q again and pushes -- a genuine remote-side change
    # made after the shared sync-base. B never touches either task, ever.
    store_a.reprioritise(remote_only.id, "low")
    store_a.sync(remote=bare_remote)

    # B's own history is rewritten outside Cadence (simulated rebase) --
    # exactly the scenario --reset-sync-base exists to recover from. B has
    # made no edit to either task, before or after this.
    _git(store_b._history().repo_dir, "commit", "--amend", "-q", "-m",
         "externally rewritten, no B edits at all")

    # Proof, from B's own first-parent git log, that it never made a real
    # edit to task Q itself: the only mainline commit touching its file is
    # the pull that landed it, never a later commit authored by B.
    b_hist = store_b._history()
    b_commits_on_q = b_hist.mainline_log_for_file(f"tasks/{remote_only.id}.json")
    assert len(b_commits_on_q) == 1, (
        f"expected exactly one mainline commit (the initial pull) touching "
        f"task Q on B, found {len(b_commits_on_q)}"
    )

    # B syncs -- the 0.2.34 guard correctly refuses (covered by
    # test_0904_history_rewrite_guard.py).
    with pytest.raises(HistoryRewritten):
        store_b.sync(remote=bare_remote)

    # B follows the documented recovery step exactly as instructed.
    result = store_b.sync(remote=bare_remote, reset_sync_base=True)

    # The bug: task Q used to be reported as a conflict, claiming B edited
    # it, which is false. The fix: a row only the remote touched since the
    # real shared sync-base applies cleanly -- pulled, never a conflict.
    conflicts_by_id = {c["id"]: c for c in result["conflicts"]}
    assert remote_only.id not in conflicts_by_id, (
        f"task Q, which B never touched, was falsely reported as a "
        f"conflict: {result!r}"
    )
    assert result["pulled"] >= 1, result
    b_after_q = store_b.get(remote_only.id)
    assert b_after_q.priority == "low", "A's remote-only edit should have been pulled cleanly"

    # The already-untouched-by-either-side row still reports clean too.
    assert untouched.id not in conflicts_by_id
    assert store_b.get(untouched.id).title == untouched.title


def test_reset_sync_base_still_conflicts_a_genuine_both_sides_edit(tmp_path, bare_remote):
    """Guard against overcorrecting: the case test_0904b already covers
    (this client made its own real unsynced edit, and the remote also
    changed the row) must still surface as an explicit conflict after a
    reset, never an assumed-safe pull of either side."""
    store_a = Store(db_path=tmp_path / "a.db")
    store_b = Store(db_path=tmp_path / "b.db")

    task = store_a.add("Shared task T")
    store_a.sync(remote=bare_remote)
    store_b.sync(remote=bare_remote)

    store_a.reprioritise(task.id, "high")
    store_a.sync(remote=bare_remote)

    _git(store_b._history().repo_dir, "commit", "--amend", "-q", "-m",
         "externally rewritten (simulated rebase)")

    store_b.schedule(task.id, "2026-09-20")

    with pytest.raises(HistoryRewritten):
        store_b.sync(remote=bare_remote)

    result = store_b.sync(remote=bare_remote, reset_sync_base=True)
    conflicts_by_id = {c["id"]: c for c in result["conflicts"]}
    assert task.id in conflicts_by_id, (
        "a row this client genuinely edited since the real sync-base, and "
        "the remote also changed, must still surface as a conflict"
    )
    assert conflicts_by_id[task.id]["mine"]["due"] == "2026-09-20"
    assert conflicts_by_id[task.id]["theirs"]["priority"] == "high"
