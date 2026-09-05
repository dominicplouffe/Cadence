"""Dov's independent 0.2.34 pass (docs/dogfooding-log.md 2026-09-05, HIGH,
/workspace/redteam_0234_indep/findings/2026-09-05-0234-reset-sync-base-silent-overwrite.md):
the `--reset-sync-base` recovery path the previous fix (0.2.34,
test_0904_history_rewrite_guard.py) shipped could itself silently discard a
real, never-yet-pushed local edit and report success.

Exact scenario: A creates+pushes a task. B pulls it (both now share a
sync-base). A edits it again and pushes (a legitimate remote change). B's
OWN history is then rewritten outside Cadence (simulating a rebase) --
`HistoryRewritten` fires on B's next sync, as covered by
test_0904_history_rewrite_guard.py. B, not yet having reset, makes its own
real edit to the same row. B follows the documented recovery step,
`--reset-sync-base`. Before this fix: "Up to date", exit success, B's edit
silently gone, B's row now byte-identical to A's. This test confirms that
can no longer happen -- B's edit is never silently dropped, and this
specific case surfaces as an explicit conflict for B to settle.
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


def test_reset_sync_base_never_silently_drops_a_real_unsynced_edit(tmp_path, bare_remote):
    store_a = Store(db_path=tmp_path / "a.db")
    store_b = Store(db_path=tmp_path / "b.db")

    # A creates and pushes T. B pulls it -- both now share a real sync-base.
    task = store_a.add("Shared task T")
    store_a.sync(remote=bare_remote)
    store_b.sync(remote=bare_remote)
    assert store_b.get(task.id).priority != "high"

    # A independently edits T again and pushes -- a genuine, legitimate
    # remote-side change made *after* the shared sync-base.
    store_a.reprioritise(task.id, "high")
    store_a.sync(remote=bare_remote)

    # B's own history is rewritten outside Cadence (simulated rebase) --
    # exactly the scenario --reset-sync-base exists to recover from.
    _git(store_b._history().repo_dir, "commit", "--amend", "-q", "-m",
         "externally rewritten (simulated rebase)")

    # B makes its own real, never-yet-synced edit to the SAME row, before
    # ever re-syncing.
    store_b.schedule(task.id, "2026-09-20")
    b_edit_due = store_b.get(task.id).due
    assert b_edit_due == "2026-09-20"

    # B syncs -- the 0.2.34 guard correctly refuses (covered by
    # test_0904_history_rewrite_guard.py; re-asserted here as the setup
    # this whole scenario depends on).
    with pytest.raises(HistoryRewritten):
        store_b.sync(remote=bare_remote)

    # B follows the documented recovery step exactly as instructed.
    result = store_b.sync(remote=bare_remote, reset_sync_base=True)

    # The bug: this used to report success with B's edit silently gone
    # ("already_synced" or "conflicts": [] while due became None again).
    # The fix: B's edit is never silently discarded -- either it survives
    # untouched, or it is surfaced as an explicit conflict. It must NOT be
    # the case that the sync reports clean success while the edit is gone.
    b_after = store_b.get(task.id)
    conflicts_by_id = {c["id"]: c for c in result["conflicts"]}
    silently_lost = (
        b_after.due != b_edit_due
        and task.id not in conflicts_by_id
    )
    assert not silently_lost, (
        f"B's edit (due={b_edit_due!r}) was discarded with no conflict "
        f"raised -- result={result!r}, b_after.due={b_after.due!r}"
    )

    # This exact case (both sides genuinely changed the row since the real
    # shared sync-base) is expected to resolve as an explicit conflict, not
    # an assumed-safe pull of either side -- confirm it does, and that
    # resolving it the ordinary way (`--keep-mine`) actually recovers B's
    # edit rather than it being gone for good.
    assert task.id in conflicts_by_id, result
    conflict = conflicts_by_id[task.id]
    assert conflict["mine"]["due"] == "2026-09-20"
    assert conflict["theirs"]["priority"] == "high"

    resolved = store_b.resolve_conflict(task.id, "mine")
    assert resolved.due == "2026-09-20"

    # And a genuinely unchanged row (B never edited it since the rewrite)
    # still reports clean after a reset -- the fix must not turn every row
    # into a conflict, only ones this client actually changed.
    store_c = Store(db_path=tmp_path / "c.db")
    other = store_a.add("Untouched by C")
    store_a.sync(remote=bare_remote)
    store_c.sync(remote=bare_remote)
    _git(store_c._history().repo_dir, "commit", "--amend", "-q", "-m",
         "externally rewritten (simulated rebase), no local edit after")
    with pytest.raises(HistoryRewritten):
        store_c.sync(remote=bare_remote)
    clean_result = store_c.sync(remote=bare_remote, reset_sync_base=True)
    assert clean_result["conflicts"] == []
    assert store_c.get(other.id).title == "Untouched by C"
