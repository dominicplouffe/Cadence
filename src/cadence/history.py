"""Git-backed mutation history for `undo` and `sync`.

Every store mutation (add/complete/schedule/reprioritise/decompose/undo
itself) writes the affected tasks as one-file-per-task JSON blobs under
`<history_dir>/tasks/<id>.json` and commits them here. This repo is
separate from any repo the Cadence *project* itself lives in -- it is
purely the user's task-store audit trail, living under CADENCE_HOME (or
next to the test's scratch CADENCE_DB_PATH).

Two things ride on this log:
- `undo` reverts exactly the files the most recent commit touched, using
  the commit before it as the target state -- so "undo the undo" replays
  the original commit, giving symmetric undo/redo for free without a
  separate redo verb (docs/human-surface.md §4.9).
- `sync` treats each client's own history repo as one side of a three-way
  compare against a shared remote (a bare git repo, or another client's
  history dir) -- see cadence.store.Store.sync for the merge/conflict
  logic; this module only provides the git plumbing it runs on.

All git calls go through a fixed local identity (-c user.name/user.email)
so this works in CI and any sandbox with no global git config.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

_GIT_IDENTITY = ["-c", "user.email=cadence@local", "-c", "user.name=Cadence"]


class HistoryError(Exception):
    pass


class GitHistory:
    def __init__(self, repo_dir: Path):
        self.repo_dir = Path(repo_dir)
        self.tasks_dir = self.repo_dir / "tasks"

    # -- plumbing -----------------------------------------------------
    def _git(self, *args, check=True, env=None):
        result = subprocess.run(
            ["git", "-C", str(self.repo_dir), *_GIT_IDENTITY, *args],
            capture_output=True,
            text=True,
            env=env,
        )
        if check and result.returncode != 0:
            raise HistoryError(
                f"git {' '.join(args)} failed: {result.stderr.strip()}"
            )
        return result

    def exists(self) -> bool:
        return (self.repo_dir / ".git").is_dir()

    def ensure(self) -> None:
        if self.exists():
            return
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "init", "-q", "-b", "main", str(self.repo_dir)],
            capture_output=True,
            text=True,
            check=True,
        )
        # Every client's history repo is a normal (non-bare) working repo
        # with `main` checked out -- but §4.10 lets a second client point
        # `sync --remote` directly at THIS client's own CADENCE_DB_PATH,
        # which resolves to pushing straight into this repo, not via a
        # bare intermediary. Git refuses that by default ("refusing to
        # update checked out branch"); `updateInstead` makes it safe by
        # keeping the working tree in sync with whatever lands on `main`,
        # so this client's own next commit never has a stale index to
        # diff against.
        self._git("config", "receive.denyCurrentBranch", "updateInstead")
        (self.tasks_dir / ".gitkeep").write_text("")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "init: empty task store", "--allow-empty")

    # -- task file <-> dict --------------------------------------------
    def task_path(self, task_id: int) -> Path:
        return self.tasks_dir / f"{task_id}.json"

    def write_task_file(self, task: dict) -> None:
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.task_path(task["id"]).write_text(
            json.dumps(task, indent=2, sort_keys=True) + "\n"
        )

    def remove_task_file(self, task_id: int) -> None:
        p = self.task_path(task_id)
        if p.exists():
            p.unlink()

    # -- commits ---------------------------------------------------------
    def commit(self, message: str, allow_empty: bool = False) -> Optional[str]:
        self._git("add", "-A")
        args = ["commit", "-q", "-m", message]
        if allow_empty:
            args.append("--allow-empty")
        result = self._git(*args, check=False)
        if result.returncode != 0:
            if "nothing to commit" in (result.stdout + result.stderr).lower():
                return self.head()
            raise HistoryError(f"git commit failed: {result.stderr.strip()}")
        return self.head()

    def head(self) -> Optional[str]:
        r = self._git("rev-parse", "HEAD", check=False)
        return r.stdout.strip() if r.returncode == 0 else None

    def log(self, limit: int = 10) -> list[str]:
        r = self._git("log", f"-n{limit}", "--pretty=%H", check=False)
        if r.returncode != 0:
            return []
        return [h for h in r.stdout.splitlines() if h]

    def message_of(self, commit: str) -> str:
        r = self._git("log", "-1", "--pretty=%s", commit, check=False)
        return r.stdout.strip() if r.returncode == 0 else ""

    def changed_task_files(self, commit: str) -> list[str]:
        """Paths under tasks/ that `commit` added or changed relative to its
        first parent (or the empty tree, for a root commit)."""
        r = self._git(
            "diff-tree", "--no-commit-id", "--name-only", "-r", commit, check=False
        )
        if r.returncode != 0:
            return []
        return [f for f in r.stdout.splitlines() if f.startswith("tasks/") and f.endswith(".json")]

    def show_file(self, ref: str, relpath: str) -> Optional[str]:
        r = self._git("show", f"{ref}:{relpath}", check=False)
        return r.stdout if r.returncode == 0 else None

    def snapshot_at(self, ref: Optional[str]) -> dict:
        """All tasks/*.json blobs at `ref`, as {id: task_dict}."""
        if ref is None:
            return {}
        r = self._git("ls-tree", "-r", "--name-only", ref, "--", "tasks", check=False)
        if r.returncode != 0:
            return {}
        out = {}
        for path in r.stdout.splitlines():
            if not path.endswith(".json") or path.endswith(".gitkeep"):
                continue
            content = self.show_file(ref, path)
            if content:
                data = json.loads(content)
                out[data["id"]] = data
        return out

    # -- sync-base marker -------------------------------------------------
    def sync_base_sha(self) -> Optional[str]:
        r = self._git("rev-parse", "--verify", "refs/cadence/sync-base", check=False)
        return r.stdout.strip() if r.returncode == 0 else None

    def set_sync_base(self, sha: str) -> None:
        self._git("update-ref", "refs/cadence/sync-base", sha)

    # -- remote -------------------------------------------------------
    def get_remote(self) -> Optional[str]:
        r = self._git("remote", "get-url", "origin", check=False)
        return r.stdout.strip() if r.returncode == 0 else None

    def set_remote(self, url: str) -> None:
        self._git("remote", "remove", "origin", check=False)
        self._git("remote", "add", "origin", url)

    def fetch(self) -> bool:
        r = self._git("fetch", "-q", "origin", check=False)
        return r.returncode == 0

    def remote_main_sha(self) -> Optional[str]:
        r = self._git("rev-parse", "--verify", "origin/main", check=False)
        return r.stdout.strip() if r.returncode == 0 else None

    def push_new_history(self) -> bool:
        """First-ever sync: this client's whole history becomes origin/main."""
        r = self._git("push", "-q", "origin", "HEAD:main", check=False)
        return r.returncode == 0

    def advance_local(self, parents: list[str], message: str) -> str:
        """Commit the CURRENT working tree (already updated with pulled
        writes) as a new commit on local `main`, with the given parent
        commit(s). Used after a sync to fold origin's history into local's
        own, without changing local's file contents (which already reflect
        mine + pulled, conflicts left as mine)."""
        self._git("add", "-A")
        tree = self._git("write-tree").stdout.strip()
        args = ["commit-tree", tree]
        for p in parents:
            args += ["-p", p]
        args += ["-m", message]
        commit = self._git(*args).stdout.strip()
        self._git("update-ref", "refs/heads/main", commit)
        return commit

    def push_safe_merge(
        self, base_ref: str, overlay: dict, message: str, parents: list[str]
    ) -> bool:
        """Build a commit whose tree = `base_ref`'s tree with only `overlay`
        (id -> task dict) task files replaced, then push it to origin/main.

        This never touches any path outside `overlay` relative to
        `base_ref` -- specifically, a conflicted task's file is left
        exactly as it is on origin, so pushing can never clobber a
        concurrent edit this sync chose not to resolve.
        """
        with tempfile.TemporaryDirectory() as tmp:
            index_file = os.path.join(tmp, "index")
            env = {**os.environ, "GIT_INDEX_FILE": index_file}
            self._git("read-tree", base_ref, env=env)
            for task_id, data in overlay.items():
                blob_content = json.dumps(data, indent=2, sort_keys=True) + "\n"
                blob = subprocess.run(
                    ["git", "-C", str(self.repo_dir), *_GIT_IDENTITY, "hash-object", "-w", "--stdin"],
                    input=blob_content,
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout.strip()
                self._git(
                    "update-index", "--add", "--cacheinfo",
                    f"100644,{blob},tasks/{task_id}.json",
                    env=env,
                )
            tree = self._git("write-tree", env=env).stdout.strip()
            args = ["commit-tree", tree]
            for p in parents:
                args += ["-p", p]
            args += ["-m", message]
            commit = self._git(*args).stdout.strip()
        r = self._git("push", "-q", "origin", f"{commit}:refs/heads/main", check=False)
        return r.returncode == 0

    # -- pending conflicts (local-only, never committed/pushed) -----------
    def _conflicts_path(self) -> Path:
        return self.repo_dir / ".git" / "cadence-conflicts.json"

    def load_conflicts(self) -> dict:
        p = self._conflicts_path()
        if not p.exists():
            return {}
        return {int(k): v for k, v in json.loads(p.read_text()).items()}

    def save_conflicts(self, conflicts: dict) -> None:
        self._conflicts_path().write_text(
            json.dumps({str(k): v for k, v in conflicts.items()}, indent=2)
        )

    def clear_conflict(self, task_id: int) -> None:
        conflicts = self.load_conflicts()
        conflicts.pop(task_id, None)
        if conflicts:
            self.save_conflicts(conflicts)
        else:
            p = self._conflicts_path()
            if p.exists():
                p.unlink()
