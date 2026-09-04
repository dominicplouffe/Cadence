"""task_01a06bf5d62ed00fab1e3869: a user (or a rogue process) can rewrite
the hidden git history repo under CADENCE_HOME by hand -- rebase, amend,
filter-repo, a forced reset -- outside Cadence's own control. Before this
fix, `sync`'s stored sync-base SHA and `undo`'s "most recent commit"
assumption were trusted without checking either was still a real ancestor
of this store's current history: `snapshot_at()` silently returned `{}`
for a rewritten sync-base, which `sync` read as "no prior sync" (a false
conflict storm), and `undo` would have reverted whatever the tampered
HEAD said was "last", with no way to tell a genuine mutation from a
fabricated one.

This test rewrites one client's real history repo BY HAND (subprocess git,
not through Store/GitHistory at all -- exactly what a rogue process or an
impatient human would do) and confirms both `sync` and `undo` now refuse
loudly (`HistoryRewritten`) instead of misbehaving quietly, and that
sqlite -- which is never derived from git -- is untouched by either failed
call.
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


def _rows(store):
    """Raw sqlite content, independent of Task/list() so this really
    checks the table on disk, not some in-memory view of it."""
    with store._connect() as conn:
        return sorted(
            dict(r) for r in conn.execute("SELECT * FROM tasks").fetchall()
        )


def _rewrite_history_by_hand(hist_dir):
    """Simulate exactly what the task describes: a rebase/amend done
    directly against the hidden history repo, never through Cadence.
    `commit --amend` is the smallest real instance of "rewrite a commit"
    -- same tree, new SHA, the old tip orphaned -- which is all this fix
    needs to exercise (a full interactive rebase or `filter-repo` run
    rewrites more commits the same way, but the one at HEAD is always
    among them, which is the one both sync's stored marker and undo's
    "most recent commit" assumption were pointed at here)."""
    _git(hist_dir, "commit", "--amend", "-q", "-m", "externally rewritten (simulated rebase)")


def test_rewritten_sync_base_fails_loud_and_sqlite_stays_intact(tmp_path, bare_remote):
    store_a = Store(db_path=tmp_path / "a.db")
    store_b = Store(db_path=tmp_path / "b.db")

    task = store_a.add("Shared task")
    store_a.sync(remote=bare_remote)  # seeds the remote, sets A's sync-base
    store_b.sync(remote=bare_remote)  # B pulls, sets B's own sync-base

    # One more ordinary, legitimate round so the sync-base check is
    # exercised against a LATER head (not just "equal to HEAD"), and to
    # confirm normal use is unaffected by this fix.
    store_a.schedule(task.id, "2026-09-10")
    r = store_a.sync(remote=bare_remote)
    assert r["conflicts"] == []
    assert store_a._history().is_ancestor(
        store_a._history().sync_base_sha(), store_a._history().head()
    ) is True

    before = _rows(store_a)

    hist_dir = store_a._history().repo_dir
    _rewrite_history_by_hand(hist_dir)

    # -- (1) sync fails loud and specific, not a false conflict storm --
    with pytest.raises(HistoryRewritten) as exc_info:
        store_a.sync(remote=bare_remote)
    msg = str(exc_info.value.message) + " " + (exc_info.value.hint or "")
    assert "rewritten" in msg
    assert "3-way merge" in msg
    assert "--reset-sync-base" in msg

    # -- (3a) sqlite untouched by the failed sync --
    assert _rows(store_a) == before
    # and no bogus conflict was recorded either
    assert store_a._history().load_conflicts() == {}

    # -- (2) undo also refuses, rather than reverting the wrong thing --
    with pytest.raises(HistoryRewritten) as exc_info2:
        store_a.undo()
    msg2 = str(exc_info2.value.message) + " " + (exc_info2.value.hint or "")
    assert "rewritten" in msg2
    assert "No sqlite change was attempted" in msg2

    # -- (3b) sqlite untouched by the failed undo too --
    assert _rows(store_a) == before

    # -- B, whose own history was never touched, is unaffected: it can
    # still sync cleanly against the same remote, untouched by A's local
    # corruption --
    r_b = store_b.sync(remote=bare_remote)
    assert r_b["conflicts"] == []


def test_reset_sync_base_recovers_from_rewritten_history(tmp_path, bare_remote):
    """The fix's own error message tells the operator exactly one way
    out -- confirm that way actually works, so the hint isn't a promise
    Cadence doesn't keep."""
    store_a = Store(db_path=tmp_path / "ra.db")
    store_b = Store(db_path=tmp_path / "rb.db")

    task = store_a.add("Recoverable task")
    store_a.sync(remote=bare_remote)
    store_b.sync(remote=bare_remote)

    _rewrite_history_by_hand(store_a._history().repo_dir)

    with pytest.raises(HistoryRewritten):
        store_a.sync(remote=bare_remote)

    result = store_a.sync(remote=bare_remote, reset_sync_base=True)
    assert result["conflicts"] == []
    # Content survived the whole episode, on the store that got rewritten:
    assert [t.title for t in store_a.list(status="all")] == ["Recoverable task"]
    assert store_a.get(task.id).title == "Recoverable task"

    # undo works normally again too, now that sync-base reflects reality.
    store_a.schedule(task.id, "2026-09-11")
    summary = store_a.undo()
    assert "reverted" in summary or "Scheduled" in summary or "undone" in summary
    assert store_a.get(task.id).due is None
