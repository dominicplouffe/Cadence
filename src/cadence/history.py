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
import random
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

_GIT_IDENTITY = ["-c", "user.email=cadence@local", "-c", "user.name=Cadence"]

### 0.2.12 Red Team finding #1: two writers touching the same store at once
### (e.g. a CLI `add` racing an MCP `add_task`, or two concurrent CLI
### processes) can both reach for this repo's `.git/index.lock` (or a ref
### lock) at nearly the same instant. Git itself holds that lock only for
### the few milliseconds an `add`/`commit`/`update-ref` actually takes, so
### this is real, transient, same-machine contention -- not a genuine
### multi-host conflict -- and a short bounded retry resolves it in every
### case Red Team's 10-concurrent-process repro produced. Total worst-case
### wait across all attempts is a few seconds, never unbounded.
_LOCK_RETRY_ATTEMPTS = 10
_LOCK_RETRY_BASE_DELAY = 0.05
_LOCK_RETRY_MAX_DELAY = 0.5


class HistoryError(Exception):
    pass


def _is_lock_contention(stderr: str) -> bool:
    """True for git's own "another process is holding this lock" wording
    (index.lock, or a ref lock like refs/heads/main.lock) -- never for any
    other failure, so a genuine problem (corrupt repo, bad ref, etc.)
    still fails immediately instead of retrying pointlessly for seconds."""
    s = (stderr or "").lower()
    return ".lock" in s and "file exists" in s


class GitHistory:
    def __init__(self, repo_dir: Path):
        self.repo_dir = Path(repo_dir)
        self.tasks_dir = self.repo_dir / "tasks"

    # -- plumbing -----------------------------------------------------
    def _git(self, *args, check=True, env=None):
        attempt = 0
        while True:
            result = subprocess.run(
                ["git", "-C", str(self.repo_dir), *_GIT_IDENTITY, *args],
                capture_output=True,
                text=True,
                env=env,
            )
            if (
                result.returncode != 0
                and attempt < _LOCK_RETRY_ATTEMPTS
                and _is_lock_contention(result.stderr)
            ):
                # Bounded exponential backoff + jitter, so many concurrent
                # losers of the same race don't all retry in lockstep and
                # collide again.
                delay = min(_LOCK_RETRY_BASE_DELAY * (2 ** attempt), _LOCK_RETRY_MAX_DELAY)
                time.sleep(delay + random.uniform(0, delay / 2))
                attempt += 1
                continue
            break
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

    def full_message(self, commit: str) -> str:
        """The commit's whole message (subject + body), e.g. the `Reason:`/
        `Source:` trailers wow-spec.md Part III §1a adds -- `message_of`
        above only returns the subject line (`%s`), which is what every
        pre-existing caller (undo's summary) still wants."""
        r = self._git("log", "-1", "--pretty=%B", commit, check=False)
        return r.stdout if r.returncode == 0 else ""

    def parse_trailers(self, commit: str) -> tuple[str, Optional[str], Optional[str]]:
        """(subject, reason, source) for one commit -- `reason`/`source`
        are None when this commit's message carries no `Reason:`/`Source:`
        trailer (every commit before wow-spec.md Part III, and every
        `add`/`done`/`undo`/sync commit today, which don't accept a reason
        at all per Part III §1a's scope list).

        A `--reason` can itself contain newlines (store.py's _snapshot_
        and_commit rides it verbatim as the commit-body paragraph after
        `Reason: `), so this collects every line after a recognized
        trailer key up to the next recognized key or end of message,
        instead of only the one line immediately following the key --
        Red Team 0.2.7 finding #1: the old one-line-only capture silently
        dropped continuation lines on the read side even though `git log`
        shows the full text was always written correctly.

        Red Team 0.2.8 finding #1: that fix still re-parsed ANY line
        starting with "Reason: "/"Source: " as a new trailer, even one
        that is itself a continuation line inside an already-open reason
        -- plausible task-management prose ("Reason: client asked for it
        verbally.") colliding with the trailer syntax, not an adversarial
        edge case; the self-collision variant was unrecoverable from any
        surface but raw `git log --pretty=%B`. `_snapshot_and_commit`
        (store.py) only ever emits one shape: `Reason: ...` opens once,
        directly after the blank line under the subject, and -- only when
        reason is truthy -- exactly one `Source: ...` line closes the
        message, always as its very last line and always exactly one line
        (source is a fixed "cli"/"mcp" literal, never user text, so it
        never needs a continuation of its own). That invariant is what
        lets the loop below tell the trailers-block boundary apart from a
        continuation line that merely starts with the same words."""
        message = self.full_message(commit)
        # rstrip trailing newlines first -- `git log --pretty=%B` always
        # ends the body in at least one, and splitlines() would otherwise
        # turn a lone trailing blank line into a spurious final "" entry
        # that the loop below (correctly) treats as a continuation line of
        # whichever trailer is still open, silently appending a stray
        # newline onto `source` (or `reason`, if `Source:` is ever absent).
        lines = message.rstrip("\n").splitlines()
        subject = lines[0] if lines else ""
        reason_lines: Optional[list[str]] = None
        source_lines: Optional[list[str]] = None
        current: Optional[list[str]] = None
        last_index = len(lines) - 1
        for index, line in enumerate(lines[1:], start=1):
            is_trailers_block_close = index == last_index
            if current is None and line.startswith("Reason: "):
                # `Reason:` only legally opens a trailer while none is open
                # yet -- a line that merely starts the same way while we're
                # already inside a continuation (current is not None) falls
                # through to the plain "append" branch below instead of
                # re-opening (and truncating) the reason.
                reason_lines = [line[len("Reason: "):]]
                current = reason_lines
            elif is_trailers_block_close and line.startswith("Source: "):
                # `Source:` only legally opens on the message's actual last
                # line -- the structural position _snapshot_and_commit
                # (store.py) guarantees for the real trailer, as opposed to
                # those same words appearing mid-paragraph inside a
                # continuation of `reason`'s own text.
                source_lines = [line[len("Source: "):]]
                current = source_lines
            elif current is not None:
                current.append(line)
        reason = "\n".join(reason_lines) if reason_lines is not None else None
        source = "\n".join(source_lines) if source_lines is not None else None
        return subject, reason, source

    def commit_time(self, commit: str) -> str:
        """Author-date ISO-8601 timestamp for `commit`, for `why`'s
        relative-time display (and `--iso` passthrough)."""
        r = self._git("log", "-1", "--pretty=%aI", commit, check=False)
        return r.stdout.strip() if r.returncode == 0 else ""

    def first_parent(self, commit: str) -> Optional[str]:
        r = self._git("rev-parse", "--verify", f"{commit}^", check=False)
        return r.stdout.strip() if r.returncode == 0 else None

    def log_for_file(self, relpath: str) -> list[str]:
        """Every commit (newest first) that actually changed `relpath`'s
        content -- git's own default history simplification already
        excludes commits that touched the tree but left this blob
        unchanged, which is exactly "this task's own history" wow-spec.md
        Part III §1b needs (one file per task, per history.py's module
        docstring)."""
        r = self._git("log", "--pretty=%H", "--", relpath, check=False)
        if r.returncode != 0:
            return []
        return [h for h in r.stdout.splitlines() if h]

    def mainline_log_for_file(self, relpath: str) -> list[str]:
        """Like `log_for_file`, but `--first-parent` only: the commits
        that changed `relpath` along THIS repo's own linear ref history,
        never a commit that only became reachable because some OTHER
        repo pushed a merge onto this one (`push_safe_merge` always
        commits with THIS repo's own prior head as parent 1 and the
        pushing side's head as parent 2, so a merge commit's own second
        parent -- and everything behind it -- is that other side's
        history, not ours, even though `git log` can reach it from here
        once the push lands). `store.py`'s `_first_sync_task_base` needs
        exactly this: the row's own most recent commit on THIS store's
        real timeline, not a foreign one that only rode in on a push."""
        r = self._git("log", "--first-parent", "--pretty=%H", "--", relpath, check=False)
        if r.returncode != 0:
            return []
        return [h for h in r.stdout.splitlines() if h]

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

    def advance_local(
        self, parents: list[str], message: str, exclude: Optional[list[str]] = None
    ) -> str:
        """Commit the CURRENT working tree (already updated with pulled
        writes) as a new commit on local `main`, with the given parent
        commit(s). Used after a sync to fold origin's history into local's
        own, without changing local's file contents (which already reflect
        mine + pulled, conflicts left as mine).

        `exclude`: repo-relative paths (e.g. "tasks/2.json") to leave out
        of this commit even though they sit in the working tree -- for a
        task file self-heal found unreadable and correctly chose not to
        touch (docs/dogfooding-log.md 2026-09-04): plain `git add -A`
        aborts entirely rather than staging anything if it can't even
        read one file to hash it, which would turn "leave this file
        alone" into "fail the whole sync" -- excluding it by pathspec
        lets everything else this commit needs to record proceed."""
        if exclude:
            self._git("add", "-A", "--", ".", *(f":!{p}" for p in exclude))
        else:
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
