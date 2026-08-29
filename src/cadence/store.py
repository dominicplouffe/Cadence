"""Embedded SQLite store for Cadence tasks.

This is the single source of truth used by both the CLI (cadence.cli) and
the MCP server (cadence.mcp_server), so a human and an agent are always
looking at the same data through the same rules.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass, asdict, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from cadence.history import GitHistory

### Priority values match docs/human-surface.md exactly ("high"/"med"/"low");
### a task may also carry no priority at all (None), which is the default.
VALID_PRIORITIES = ("low", "med", "high")
VALID_STATUSES = ("pending", "done")

### docs/human-surface.md §4.7: bounded by construction so a looping agent
### can't decompose forever.
MAX_DECOMPOSE_DEPTH = 3
MAX_SUBTASKS_PER_PARENT = 20

### Red Team pass-3 finding #5: an unbounded title (5000+ chars reproduced)
### makes `cadence list` dump hundreds of wrapped lines for one row, breaking
### the table layout docs/human-surface.md §5 promises. 200 matches the
### longest title docs/human-surface.md §6/§7 actually tested the wrap
### behavior against, so it's a documented, exercised ceiling, not an
### arbitrary one.
MAX_TITLE_LEN = 200

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    priority TEXT,
    due TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    parent_id INTEGER
);
"""

_PRIORITY_RANK = {"high": 0, "med": 1, "low": 2, None: 3}


class CadenceError(Exception):
    """Base class for errors the CLI/MCP layers turn into structured output."""

    code = "error"

    def __init__(self, message: str, hint: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.hint = hint


class TaskNotFound(CadenceError):
    code = "task_not_found"


class InvalidTask(CadenceError):
    code = "invalid_task"


class NothingToUndo(CadenceError):
    code = "nothing_to_undo"


class SyncConflict(CadenceError):
    """Raised by resolve_conflict when the given id has no pending conflict."""

    code = "no_such_conflict"


class StoreUnavailable(CadenceError):
    """The store file itself can't be opened/read (bad CADENCE_DB_PATH,
    corrupt file) -- distinct from a bad request, this is an internal/store
    error per docs/human-surface.md §4.4 (exit code 2, not 1)."""

    code = "store_unavailable"


class SyncInconsistent(CadenceError):
    """`sync` hit an internal inconsistency in the history data it read
    (R-08 re-verify Finding B) -- a clean, actionable error instead of a
    raw KeyError/internal_error leaking out of the diff/apply logic. The
    known trigger (two CADENCE_DB_PATH values sharing a history dir) is
    fixed at the source in `_history`/`_resolve_remote`; this is the
    defense-in-depth net for any other way two stores' history could end
    up cross-contaminated."""

    code = "sync_inconsistent"


def default_db_path() -> Path:
    """Resolve the store location, honoring CADENCE_DB_PATH for tests/agents
    that want an isolated scratch store instead of the user's real data."""
    override = os.environ.get("CADENCE_DB_PATH")
    if override:
        return Path(override)
    home = Path(os.environ.get("CADENCE_HOME", Path.home() / ".cadence"))
    home.mkdir(parents=True, exist_ok=True)
    return home / "cadence.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate_due(due: str) -> str:
    """Validate/normalize a due-date string to ISO 'YYYY-MM-DD'.

    Store-side, so every writer of `due` -- the CLI's `schedule`/`add`, the
    MCP `schedule_task`/`add_task` tools, or any future surface -- hits the
    same rule and none of them can write a value the other can't render.
    The CLI already pre-validates for a fast, contextual error message; this
    is the guard that makes that pre-validation a UX nicety rather than the
    only thing standing between an agent and a permanently broken `list`
    (Red Team pass-1 finding #1).
    """
    due = (due or "").strip()
    if not due:
        raise InvalidTask(
            "due must be a non-empty date/time string",
            hint="Use an ISO date like 2026-09-01.",
        )
    try:
        return date.fromisoformat(due).isoformat()
    except ValueError:
        raise InvalidTask(
            f"can't parse '{due}' as a date",
            hint="Use an ISO date like 2026-09-01.",
        )


@dataclass
class Task:
    id: int
    title: str
    status: str
    priority: str
    due: Optional[str]
    created_at: str
    completed_at: Optional[str]
    parent_id: Optional[int] = None

    def to_dict(self) -> dict:
        return asdict(self)


class Store:
    """Thin wrapper around a SQLite file holding one `tasks` table."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with closing(self._connect()) as conn:
                conn.execute(SCHEMA)
                # Migration for stores created before parent_id existed
                # (docs/human-surface.md §4.7, decompose): sqlite has no
                # "ADD COLUMN IF NOT EXISTS" we can rely on across the
                # versions this store has been used with, so probe instead.
                cols = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)")}
                if "parent_id" not in cols:
                    conn.execute("ALTER TABLE tasks ADD COLUMN parent_id INTEGER")
                conn.commit()
        except sqlite3.Error as exc:
            # e.g. CADENCE_DB_PATH points at a directory ("unable to open
            # database file") or a non-sqlite file ("file is not a
            # database") -- both otherwise surface as a raw sqlite3
            # traceback on every command (Red Team pass-1 finding #2).
            raise StoreUnavailable(
                f"can't open the task store at '{self.db_path}' ({exc})",
                hint="Check CADENCE_DB_PATH, or unset it to use the default store.",
            ) from exc

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> Task:
        keys = row.keys()
        return Task(
            id=row["id"],
            title=row["title"],
            status=row["status"],
            priority=row["priority"],
            due=row["due"],
            created_at=row["created_at"],
            completed_at=row["completed_at"],
            parent_id=row["parent_id"] if "parent_id" in keys else None,
        )

    def _history(self) -> GitHistory:
        # One history repo per store file, living next to it -- so a
        # scratch CADENCE_DB_PATH used by tests gets its own scratch
        # history too, never the real user's.
        #
        # Uses the FULL filename (`.name`), not `.stem` (R-08 re-verify
        # Finding B): `Path.stem` only strips the *last* dot-suffix, so
        # two different CADENCE_DB_PATH values that merely share the text
        # before their first dot -- e.g. `store` and `store.db`, or
        # `a.db` and `a.db_backup` -- used to derive to the exact same
        # on-disk history directory and silently cross-contaminate each
        # other's task history. `.name` keeps the whole filename, so
        # distinct paths always derive to distinct history dirs.
        return GitHistory(self.db_path.parent / (self.db_path.name + ".history"))

    @staticmethod
    def _resolve_remote(remote: str) -> str:
        """Resolve a caller-supplied `--remote`/`remote` value to the git
        history location `sync` actually needs (docs/human-surface.md
        §4.10). A caller only ever legitimately holds one of:

        - a git URL or bare-repo path meant to be used as-is (a real
          remote server, or a shared bare repo already set up for this) --
          recognized by "://" / "git@", or by already being a git repo on
          disk (a `.history` working dir, or a bare repo);
        - the OTHER client's own `CADENCE_DB_PATH` value (that client's
          plain `.db` file path) -- this client derives that client's
          `.history` dir itself, the exact same way `Store._history()`
          derives its own, so the caller never has to know that Cadence
          keeps history in a sibling `.history` directory at all.
        """
        if "://" in remote or remote.startswith("git@"):
            return remote
        p = Path(remote)
        if p.is_dir() and ((p / ".git").is_dir() or (p / "HEAD").is_file()):
            return remote  # already points at a history repo (or bare repo)
        # Full filename, not stem -- see the matching comment on
        # Store._history() (R-08 re-verify Finding B). Must derive the
        # exact same way that method does, or the two would themselves
        # disagree about where a given CADENCE_DB_PATH's history lives.
        return str(p.parent / (p.name + ".history"))

    def _snapshot_and_commit(self, tasks: list["Task"], message: str) -> None:
        hist = self._history()
        hist.ensure()
        for t in tasks:
            hist.write_task_file(t.to_dict())
        hist.commit(message)

    def add(
        self,
        title: str,
        due: Optional[str] = None,
        priority: Optional[str] = None,
    ) -> Task:
        title = (title or "").strip()
        if not title:
            raise InvalidTask(
                "title must be a non-empty string",
                hint="Call add with a `title` argument, e.g. add(title='Ship the CLI').",
            )
        if len(title) > MAX_TITLE_LEN:
            # Store-side, so every writer (CLI, MCP, or any future surface)
            # hits the same rule -- same reasoning as _validate_due below.
            raise InvalidTask(
                f"title is {len(title)} characters, max {MAX_TITLE_LEN}",
                hint="Try a shorter one.",
            )
        if priority is not None and priority not in VALID_PRIORITIES:
            raise InvalidTask(
                f"priority must be one of {VALID_PRIORITIES}, got {priority!r}",
                hint="Use one of: low, med, high.",
            )
        if due is not None:
            due = _validate_due(due)
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "INSERT INTO tasks (title, status, priority, due, created_at) "
                "VALUES (?, 'pending', ?, ?, ?)",
                (title, priority, due, _now()),
            )
            conn.commit()
            task = self.get(cur.lastrowid, _conn=conn)
        self._snapshot_and_commit([task], f"Added #{task.id}: {task.title}")
        return task

    def list(self, status: Optional[str] = None) -> list[Task]:
        """status=None or 'all' returns everything; otherwise filters.

        Order, per docs/human-surface.md §4.8 (the fix for Red Team pass-1/7
        finding #3 -- list_tasks's own docstring claimed this ordering
        while the store actually used plain insertion order): open tasks
        sort by priority (high -> med -> low -> none) then id ascending
        within a tier; done tasks always sort after open ones, by
        completed_at descending (most recently finished first).
        """
        if status not in (None, "all") and status not in VALID_STATUSES:
            raise InvalidTask(
                f"status must be one of {VALID_STATUSES + ('all',)}, got {status!r}",
                hint="Use one of: pending, done, all.",
            )
        with closing(self._connect()) as conn:
            pending = done = []
            if status in (None, "all", "pending"):
                rows = conn.execute("SELECT * FROM tasks WHERE status = 'pending'").fetchall()
                pending = sorted(
                    (self._row_to_task(r) for r in rows),
                    key=lambda t: (_PRIORITY_RANK.get(t.priority, 3), t.id),
                )
            if status in (None, "all", "done"):
                rows = conn.execute("SELECT * FROM tasks WHERE status = 'done'").fetchall()
                # Two stable passes (id ascending, then completed_at
                # descending) so ties on the same completed_at keep id
                # ascending as the secondary key instead of an arbitrary one.
                done = sorted((self._row_to_task(r) for r in rows), key=lambda t: t.id)
                done = sorted(done, key=lambda t: t.completed_at or "", reverse=True)
            return [*pending, *done]

    def get(self, task_id: int, _conn: Optional[sqlite3.Connection] = None) -> Task:
        try:
            task_id = int(task_id)
        except (TypeError, ValueError):
            raise InvalidTask(
                f"id must be an integer, got {task_id!r}",
                hint="Pass the numeric id shown by `cadence list`.",
            )
        owns_conn = _conn is None
        conn = _conn or self._connect()
        try:
            try:
                row = conn.execute(
                    "SELECT * FROM tasks WHERE id = ?", (task_id,)
                ).fetchone()
            except OverflowError:
                # sqlite's C API caps bound integers at 64 bits; an id an
                # agent could plausibly pass (e.g. copy-pasted from the
                # wrong field) overflows that and previously raised a raw
                # OverflowError instead of the same "not found" an
                # out-of-range-but-small id gets (Red Team pass-1 #2).
                row = None
            if row is None:
                raise TaskNotFound(
                    f"no task with id {task_id}",
                    hint="Run 'cadence list' to see valid ids.",
                )
            return self._row_to_task(row)
        finally:
            if owns_conn:
                conn.close()

    def complete(self, task_id: int) -> Task:
        with closing(self._connect()) as conn:
            self.get(task_id, _conn=conn)  # raises TaskNotFound/InvalidTask
            conn.execute(
                "UPDATE tasks SET status = 'done', completed_at = ? WHERE id = ?",
                (_now(), int(task_id)),
            )
            conn.commit()
            task = self.get(task_id, _conn=conn)
        self._snapshot_and_commit([task], f"Done #{task.id}: {task.title}")
        return task

    def schedule(self, task_id: int, due: str) -> Task:
        due = _validate_due(due)
        with closing(self._connect()) as conn:
            self.get(task_id, _conn=conn)
            conn.execute("UPDATE tasks SET due = ? WHERE id = ?", (due, int(task_id)))
            conn.commit()
            task = self.get(task_id, _conn=conn)
        self._snapshot_and_commit([task], f"Scheduled #{task.id} for {due}: {task.title}")
        return task

    def reprioritise(self, task_id: int, priority: str) -> Task:
        """docs/human-surface.md §4.8: a dedicated verb, distinct from
        `add --priority`, because re-prioritising an existing task is its
        own auditable event."""
        if priority not in VALID_PRIORITIES:
            raise InvalidTask(
                f"'{priority}' isn't a priority",
                hint=f"Try: cadence reprioritise {task_id} high (low, med, or high)",
            )
        with closing(self._connect()) as conn:
            old = self.get(task_id, _conn=conn)
            conn.execute("UPDATE tasks SET priority = ? WHERE id = ?", (priority, int(task_id)))
            conn.commit()
            task = self.get(task_id, _conn=conn)
        self._snapshot_and_commit(
            [task],
            f"Reprioritised #{task.id} ({old.priority or 'none'} → {priority}): {task.title}",
        )
        return task

    def _depth(self, conn: sqlite3.Connection, task_id: int) -> int:
        depth = 0
        current = task_id
        seen = {current}
        while True:
            row = conn.execute("SELECT parent_id FROM tasks WHERE id = ?", (current,)).fetchone()
            parent = row["parent_id"] if row else None
            if parent is None or parent in seen:
                return depth
            depth += 1
            current = parent
            seen.add(current)

    def decompose(self, parent_id: int, titles: list[str]) -> tuple[Task, list[Task]]:
        """docs/human-surface.md §4.7: structural-only -- links titles the
        caller already wrote to a parent, atomically, as one call. Bounded
        by construction (max depth, max subtasks per parent) so a looping
        agent can't decompose forever."""
        titles = [t.strip() for t in (titles or []) if t and t.strip()]
        if not titles:
            raise InvalidTask(
                "'decompose' needs at least one subtask",
                hint='Try: cadence decompose {} --into "Buy flour" "Buy eggs"'.format(parent_id),
            )
        if len(titles) > MAX_SUBTASKS_PER_PARENT:
            raise InvalidTask(
                f"'decompose' takes at most {MAX_SUBTASKS_PER_PARENT} subtasks per call, "
                f"got {len(titles)}",
                hint="Split into two decompose calls.",
            )
        with closing(self._connect()) as conn:
            parent = self.get(parent_id, _conn=conn)  # raises TaskNotFound
            depth = self._depth(conn, parent_id)
            if depth >= MAX_DECOMPOSE_DEPTH:
                raise InvalidTask(
                    f"task #{parent_id} is already at max decomposition depth "
                    f"({MAX_DECOMPOSE_DEPTH})",
                    hint="Try decomposing a top-level task instead.",
                )
            existing = conn.execute(
                "SELECT COUNT(*) AS n FROM tasks WHERE parent_id = ?", (parent_id,)
            ).fetchone()["n"]
            if existing + len(titles) > MAX_SUBTASKS_PER_PARENT:
                raise InvalidTask(
                    f"task #{parent_id} already has {existing} subtask(s); adding "
                    f"{len(titles)} more would exceed the {MAX_SUBTASKS_PER_PARENT} max per parent",
                    hint="Split into two decompose calls, or decompose a different parent.",
                )
            for t in titles:
                if len(t) > MAX_TITLE_LEN:
                    raise InvalidTask(
                        f"subtask title is {len(t)} characters, max {MAX_TITLE_LEN}",
                        hint="Try a shorter one.",
                    )
            children = []
            for t in titles:
                cur = conn.execute(
                    "INSERT INTO tasks (title, status, priority, due, created_at, parent_id) "
                    "VALUES (?, 'pending', NULL, NULL, ?, ?)",
                    (t, _now(), parent_id),
                )
                children.append(self.get(cur.lastrowid, _conn=conn))
            conn.commit()
            parent = self.get(parent_id, _conn=conn)
        ids = ", ".join(f"#{c.id}" for c in children)
        self._snapshot_and_commit(
            [parent, *children],
            f"Decomposed #{parent.id} into {len(children)} subtasks: {ids}",
        )
        return parent, children

    def subtasks(self, parent_id: int) -> list[Task]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE parent_id = ? ORDER BY id ASC", (parent_id,)
            ).fetchall()
            return [self._row_to_task(r) for r in rows]

    # -- undo -----------------------------------------------------------

    @staticmethod
    def _describe_revert(task_id: int, before: dict, after: Optional[dict]) -> str:
        """Human-facing summary of what one file's revert did, in the
        two-clause style docs/human-surface.md §4.9 shows for `done`."""
        if after is None:
            return f'Added #{task_id} → removed "{before["title"]}"'
        if before["status"] != after["status"]:
            if before["status"] == "done":
                return f'Done #{task_id} → reopened "{before["title"]}"'
            return f'Reopened #{task_id} → done "{before["title"]}"'
        if before.get("priority") != after.get("priority"):
            return (
                f'Reprioritised #{task_id} ({after.get("priority") or "none"} '
                f'→ {before.get("priority") or "none"}) undone: {before["title"]}'
            )
        if before.get("due") != after.get("due"):
            return (
                f'Scheduled #{task_id} undone (due {before.get("due")} '
                f'→ {after.get("due")}): {before["title"]}'
            )
        return f'#{task_id} reverted: {before["title"]}'

    def undo(self) -> str:
        """Revert the single most recent mutation (any surface), regardless
        of which command made it. Reverting is itself a new commit, so
        undo is naturally symmetric: undoing twice in a row returns to the
        pre-undo state (docs/human-surface.md §4.9)."""
        hist = self._history()
        hist.ensure()
        commits = hist.log(limit=2)
        if len(commits) < 2:
            raise NothingToUndo(
                "no mutation to undo yet",
                hint="Run a command first (add/done/schedule/...).",
            )
        last, prev = commits[0], commits[1]
        changed = hist.changed_task_files(last)
        if not changed:
            raise NothingToUndo(
                "no mutation to undo yet",
                hint="Run a command first (add/done/schedule/...).",
            )
        descriptions = []
        with closing(self._connect()) as conn:
            for relpath in changed:
                task_id = int(Path(relpath).stem)
                before_row = conn.execute(
                    "SELECT * FROM tasks WHERE id = ?", (task_id,)
                ).fetchone()
                before = self._row_to_task(before_row).to_dict() if before_row else None
                prev_content = hist.show_file(prev, relpath)
                after = None
                if prev_content is not None:
                    after = json.loads(prev_content)
                    conn.execute(
                        "INSERT INTO tasks (id, title, status, priority, due, created_at, "
                        "completed_at, parent_id) VALUES (?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(id) DO UPDATE SET title=excluded.title, "
                        "status=excluded.status, priority=excluded.priority, "
                        "due=excluded.due, created_at=excluded.created_at, "
                        "completed_at=excluded.completed_at, parent_id=excluded.parent_id",
                        (
                            after["id"], after["title"], after["status"], after["priority"],
                            after["due"], after["created_at"], after["completed_at"],
                            after.get("parent_id"),
                        ),
                    )
                    hist.write_task_file(after)
                else:
                    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
                    hist.remove_task_file(task_id)
                if before is not None:
                    descriptions.append(self._describe_revert(task_id, before, after))
            conn.commit()
        original_message = hist.message_of(last)
        summary = "; ".join(descriptions) if descriptions else original_message
        hist.commit(f"Undo: {original_message}", allow_empty=True)
        return f"Undid: {summary}"

    # -- export -----------------------------------------------------------

    def export(self) -> list[dict]:
        """Every task, open and done, unfiltered -- docs/human-surface.md
        §4.11. Ordering matches `list(status='all')` exactly, same rule."""
        return [t.to_dict() for t in self.list(status="all")]

    # -- sync -----------------------------------------------------------

    @staticmethod
    def _content_fingerprint(data: dict) -> tuple:
        """Every field of a task snapshot except `id` -- two snapshots with
        the same fingerprint are the same task content, however it got
        filed under two different ids (see the ID COLLISION renumbering
        below, which copies a task's content verbatim under a new id)."""
        return (
            data.get("title"), data.get("status"), data.get("priority"),
            data.get("due"), data.get("created_at"), data.get("completed_at"),
            data.get("parent_id"),
        )

    def _apply_remote_task(self, conn: sqlite3.Connection, data: dict) -> None:
        conn.execute(
            "INSERT INTO tasks (id, title, status, priority, due, created_at, "
            "completed_at, parent_id) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET title=excluded.title, "
            "status=excluded.status, priority=excluded.priority, due=excluded.due, "
            "created_at=excluded.created_at, completed_at=excluded.completed_at, "
            "parent_id=excluded.parent_id",
            (
                data["id"], data["title"], data["status"], data["priority"],
                data["due"], data["created_at"], data["completed_at"],
                data.get("parent_id"),
            ),
        )

    def sync(self, remote: Optional[str] = None) -> dict:
        """docs/human-surface.md §4.10: never silently drops data on
        conflict. A task EDITED on both sides since the last clean sync
        (both have a common prior version that then diverged) is left
        untouched on BOTH the local store and the remote, reported by id
        in `conflicts` -- resolve_sync_conflict settles those.

        An id that never had a common prior version -- both sides
        independently created a task under the same auto-assigned id, with
        no shared history to compare against (R-08 re-verify Finding A) --
        is never a real edit conflict (edits never change a task's id or
        created_at, and ids are never reused once assigned). Treating it
        like one used to let `resolve_sync_conflict` permanently destroy
        one side's whole, unrelated task. Instead this is auto-resolved
        within the same sync call: the id keeps this client's task, and
        the other side's task is preserved under a freshly assigned id, on
        both this store and the remote. Reported in `renumbered`, never in
        `conflicts` -- nothing here needs or accepts resolve_sync_conflict.

        Returns {"pulled": N, "pushed": N, "conflicts": [{"id", "mine",
        "theirs"}], "renumbered": [{"old_id", "new_id", "kept_at_old_id"}],
        "already_synced": bool}.
        """
        hist = self._history()
        hist.ensure()
        given_remote = remote
        if remote:
            hist.set_remote(self._resolve_remote(remote))
        remote_url = hist.get_remote()
        if not remote_url:
            raise InvalidTask(
                "no remote configured for sync",
                hint="Run: cadence sync --remote <path>",
            )
        if not hist.fetch():
            raise InvalidTask(
                f"no Cadence store found at '{given_remote or remote_url}'",
                hint=(
                    "Check the path is the other client's CADENCE_DB_PATH "
                    "and that client has run 'cadence sync' at least once."
                ),
            )
        theirs_ref = hist.remote_main_sha()
        if theirs_ref is None:
            # Remote has no history yet: this client seeds it.
            local_head = hist.head()
            hist.push_new_history()
            hist.set_sync_base(local_head)
            n = len(self.list(status="all"))
            return {
                "pulled": 0, "pushed": n, "conflicts": [], "renumbered": [],
                "already_synced": n == 0,
            }

        try:
            return self._sync_diff_and_apply(hist, theirs_ref)
        except CadenceError:
            raise
        except Exception as exc:
            # R-08 re-verify Finding B: a raw KeyError/internal_error out of
            # the diff/apply logic below is exactly the "undesigned crash"
            # this project's own bar refuses. The one known trigger (two
            # CADENCE_DB_PATH values sharing an on-disk history dir) is
            # fixed at the source in _history/_resolve_remote; this is the
            # net for anything else that leaves this store's history
            # inconsistent with what it expects to read back.
            raise SyncInconsistent(
                f"sync hit an internal inconsistency reading history data ({type(exc).__name__}: {exc})",
                hint=(
                    "Nothing was changed. Check that CADENCE_DB_PATH for every "
                    "client is a distinct path ending in '.db', then try again."
                ),
            ) from exc

    def _sync_diff_and_apply(self, hist: GitHistory, theirs_ref: str) -> dict:
        base_ref = hist.sync_base_sha()
        mine = {t.id: t.to_dict() for t in self.list(status="all")}
        theirs = hist.snapshot_at(theirs_ref)
        base = hist.snapshot_at(base_ref) if base_ref else {}

        # Red Team finding C (0.2.2): fingerprint every LOCAL task that has
        # no base of its own (base.get(id) is None) -- i.e. one of mine
        # that has never been reconciled through a common sync yet. That
        # is exactly, and only, the category of row an id-collision on the
        # OTHER side's own earlier sync can echo back into shared history
        # under a brand-new id (see the ID COLLISION branch below: it
        # copies "theirs" verbatim, only changing `id`, so the echo's
        # content-minus-id is byte-identical to the original). A task ID
        # that's new to both `mine` and `base` but whose content matches
        # one of these fingerprints isn't a new remote task at all -- it's
        # a reflection of a task I already hold under a different id, and
        # pulling it as "new" is exactly the direct-peer topology bug that
        # fabricated a permanent duplicate on both clients (ok:true, no
        # conflict reported). A genuinely new task from the remote never
        # matches, since it was never derived from one of my own rows.
        unbased_mine_fingerprints = {
            self._content_fingerprint(v): tid
            for tid, v in mine.items()
            if base.get(tid) is None
        }

        ids = sorted(set(mine) | set(theirs) | set(base))
        next_new_id = (max(ids) + 1) if ids else 1
        pulled_ids, pushed_ids, conflicts, renumbered = [], [], {}, []
        for tid in ids:
            b, m, t = base.get(tid), mine.get(tid), theirs.get(tid)
            if m == t:
                continue
            mine_changed = m != b
            theirs_changed = t != b
            if mine_changed and theirs_changed:
                if b is None:
                    # ID COLLISION, not an edit conflict -- see the
                    # docstring above. Keep mine at its existing id;
                    # preserve theirs under a fresh one, both sides.
                    new_id = next_new_id
                    next_new_id += 1
                    renumbered_task = dict(t)
                    renumbered_task["id"] = new_id
                    mine[new_id] = renumbered_task
                    pushed_ids.append(tid)       # re-assert mine on the remote
                    pushed_ids.append(new_id)    # add theirs under its new id
                    renumbered.append(
                        {"old_id": tid, "new_id": new_id, "kept_at_old_id": "mine"}
                    )
                else:
                    conflicts[tid] = {"mine": m, "theirs": t}
            elif theirs_changed:
                if b is None and m is None and t is not None:
                    echo_of = unbased_mine_fingerprints.get(self._content_fingerprint(t))
                    if echo_of is not None:
                        # It's mine already, under `echo_of` -- do not
                        # fabricate a second row for it.
                        continue
                pulled_ids.append(tid)
            elif mine_changed:
                pushed_ids.append(tid)

        if not pulled_ids and not pushed_ids and not conflicts:
            return {
                "pulled": 0, "pushed": 0, "conflicts": [], "renumbered": [],
                "already_synced": True,
            }

        local_head = hist.head()
        renumbered_ids = [r["new_id"] for r in renumbered]
        with closing(self._connect()) as conn:
            for tid in pulled_ids:
                self._apply_remote_task(conn, theirs[tid])
            for new_id in renumbered_ids:
                self._apply_remote_task(conn, mine[new_id])
            conn.commit()
        for tid in pulled_ids:
            hist.write_task_file(theirs[tid])
        for new_id in renumbered_ids:
            hist.write_task_file(mine[new_id])

        # What we're willing to push: local's own non-conflicting changes
        # plus any renumbered rows, laid on top of origin's own tree -- so
        # pushing can never overwrite a path this sync chose not to
        # resolve (a genuinely conflicted id is simply absent from
        # `overlay`).
        overlay = {tid: mine[tid] for tid in pushed_ids}
        pushed_ok = True
        if overlay:
            pushed_ok = hist.push_safe_merge(
                theirs_ref, overlay, "sync: push local changes", [theirs_ref, local_head]
            )
        # Fold origin's history into local's own timeline too (pulled
        # writes are already applied to the local working tree above).
        hist.advance_local(
            [local_head, theirs_ref],
            f"sync: pulled {len(pulled_ids)}, pushed {len(pushed_ids)}, "
            f"renumbered {len(renumbered)}",
        )

        if conflicts:
            existing = hist.load_conflicts()
            existing.update(conflicts)
            hist.save_conflicts(existing)
        else:
            hist.set_sync_base(hist.head())

        return {
            "pulled": len(pulled_ids),
            "pushed": len(pushed_ids) if pushed_ok else 0,
            "conflicts": [{"id": tid, **v} for tid, v in conflicts.items()],
            "renumbered": renumbered,
            "already_synced": False,
        }

    def resolve_conflict(self, task_id: int, keep: str) -> Task:
        """keep is 'mine' or 'theirs'. Applies the chosen version locally,
        then pushes it to the remote immediately (this task's file only),
        rather than waiting for a later `sync` call to notice and push it.

        This matters for 'mine': the local and remote copies would
        otherwise both keep differing from the stale sync-base forever, so
        every later sync would re-detect the exact same conflict. Pushing
        the resolution now makes the remote match the chosen version, so
        the next sync (from either side) sees this task as settled.
        """
        if keep not in ("mine", "theirs"):
            raise InvalidTask(f"keep must be 'mine' or 'theirs', got {keep!r}")
        hist = self._history()
        hist.ensure()
        conflicts = hist.load_conflicts()
        if task_id not in conflicts:
            raise SyncConflict(
                f"no pending sync conflict for #{task_id}",
                hint="Run 'cadence sync' to see pending conflicts.",
            )
        chosen = conflicts[task_id][keep]
        with closing(self._connect()) as conn:
            self._apply_remote_task(conn, chosen)
            conn.commit()
        hist.write_task_file(chosen)
        hist.commit(f"sync: resolved #{task_id} (kept {keep})")
        hist.clear_conflict(task_id)

        remote_url = hist.get_remote()
        if remote_url and hist.fetch():
            theirs_ref = hist.remote_main_sha()
            local_head = hist.head()
            if theirs_ref:
                message = f"sync: resolved #{task_id} (kept {keep})"
                pushed = hist.push_safe_merge(
                    theirs_ref, {task_id: chosen}, message, [theirs_ref, local_head]
                )
                if pushed:
                    hist.advance_local([local_head, theirs_ref], message)
                    if not hist.load_conflicts():
                        # No other conflict is still open, so this client's
                        # view of "last known good" can advance -- same rule
                        # a clean (conflict-free) sync() uses.
                        hist.set_sync_base(hist.head())
        return self.get(task_id)
