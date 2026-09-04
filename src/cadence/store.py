"""Embedded SQLite store for Cadence tasks.

This is the single source of truth used by both the CLI (cadence.cli) and
the MCP server (cadence.mcp_server), so a human and an agent are always
looking at the same data through the same rules.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass, asdict, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from cadence.history import GitHistory, HistoryError

### Priority values match docs/human-surface.md exactly ("high"/"med"/"low");
### a task may also carry no priority at all (None), which is the default.
VALID_PRIORITIES = ("low", "med", "high")
VALID_STATUSES = ("pending", "done")

### Fixed, never user-derived, commit subject `_sync_diff_and_apply` gives
### an ordinary sync's `push_safe_merge` call (see the call site below).
### `_first_sync_task_base` matches on this exact string to tell "a commit
### I made myself" apart from "a commit that landed on my own checked-out
### tree only because I was the passive REMOTE side of some OTHER
### client's sync push" -- see that function's docstring.
_SYNC_PUSH_LANDING_MESSAGE = "sync: push local changes"

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
    parent_id INTEGER,
    origin TEXT
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


class AmbiguousProject(CadenceError):
    """0.2.12 Red Team finding #5: `register` refuses to register the
    unscoped global default store. default_db_path() is NOT cwd-scoped --
    it resolves to the same ~/.cadence/cadence.db (or CADENCE_HOME
    override) no matter which directory it's called from -- so silently
    registering it would collapse every project directory that hasn't set
    CADENCE_DB_PATH into a single shared registry entry, exactly
    contradicting `register`'s own "once per project" framing
    (mcp_server.py's `initialize` instructions and registry.py's own
    docstring both say so). This is a bad-input/missing-config situation
    (exit code 1), not an internal store failure."""

    code = "ambiguous_project"


class HistoryDegraded(Exception):
    """0.2.12 Red Team finding #1: raised by _snapshot_and_commit only when
    the git history-repo commit step still can't get past lock contention
    even after history.py's own bounded retry -- real, sustained
    multi-writer contention, not the momentary race the retry already
    absorbs. By the time this can ever be raised, the SQLite row for
    every task in `tasks` has ALREADY been durably committed (every
    mutator commits to sqlite first, then calls _snapshot_and_commit) --
    so this is never "the write failed"; it is "the write succeeded, but
    its audit-trail entry could not be recorded". Deliberately NOT a
    CadenceError: existing `except CadenceError` call sites must not
    treat this as an ordinary failure (that was the original bug -- a
    caller trusting a hard failure signal here would retry into a silent
    duplicate). CLI/MCP callers catch this specifically and report
    SUCCESS with an explicit degraded-history warning instead."""

    def __init__(self, tasks: list["Task"], reason: str):
        self.tasks = tasks
        self.reason = reason
        super().__init__(reason)


def history_degraded_warning(task_id: int, verb: str, reason: str) -> str:
    """Shared wording for the CLI/MCP degraded-history warning (Noor's
    design note on 0.2.12 finding #1): explicit about what happened, and
    explicit that retrying is the wrong move, so an agent or person
    reading only this string has everything it needs."""
    return (
        f"Task #{task_id} was {verb}; its history entry failed to record "
        f"({reason}). Do not retry this call -- it already succeeded. "
        f"Run 'cadence why {task_id}' to check, or file a bug."
    )


class SyncInconsistent(CadenceError):
    """`sync` hit an internal inconsistency in the history data it read
    (R-08 re-verify Finding B) -- a clean, actionable error instead of a
    raw KeyError/internal_error leaking out of the diff/apply logic. The
    known trigger (two CADENCE_DB_PATH values sharing a history dir) is
    fixed at the source in `_history`/`_resolve_remote`; this is the
    defense-in-depth net for any other way two stores' history could end
    up cross-contaminated."""

    code = "sync_inconsistent"


class UndoFailed(CadenceError):
    """`undo` could not record its matching history entry (Red Team 0226
    finding: an unrelated unreadable file elsewhere in the tree can break
    the `git add -A` inside `hist.commit`). The sqlite-side revert that
    normally accompanies this is rolled back before this is raised (see
    `Store.undo`), so -- unlike the bug this replaces -- "nothing changed"
    in the hint below is always literally true, never a guess."""

    code = "undo_failed"


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
    # Immutable identity assigned once at creation, carried for the row's
    # whole life (renumbering, sync, undo) -- the sync merge engine's real
    # identity key (see _sync_diff_and_apply). A merge-engine detail, not
    # a CLI/MCP-contract field: never returned by `to_dict()`, so no
    # human-surface or agent-facing change rides on this.
    origin: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("origin", None)
        return d

    def to_full_dict(self) -> dict:
        """Everything, including `origin` -- for the sync merge engine and
        git-history snapshots only. Never returned to a CLI/MCP caller."""
        return asdict(self)


class Store:
    """Thin wrapper around a SQLite file holding one `tasks` table."""

    def __init__(self, db_path: Optional[Path] = None, must_exist: bool = False):
        self.db_path = Path(db_path) if db_path else default_db_path()
        if must_exist:
            # Multi-project callers (overdue/sync --all-projects) open a
            # REGISTERED store read-only -- unlike a normal Store() call,
            # which legitimately creates a fresh store on first use, this
            # is never "first use": the path came from a prior
            # `cadence register`. Silently fabricating it here would mask
            # a deleted/moved project as an empty, valid one (0.2.12 Red
            # Team finding #2), and a relative-path typo hand-edited into
            # the registry would silently write a stray db file into
            # whatever directory the multi-project command happens to run
            # from (finding #4). Both are checked BEFORE any filesystem
            # side effect, so a bad registry entry never touches disk.
            if not self.db_path.is_absolute():
                raise StoreUnavailable(
                    f"registered path '{self.db_path}' is not absolute",
                    hint=(
                        "This registry entry is invalid. Re-run 'cadence "
                        "register' from that project directory, or edit "
                        "the registry file by hand to remove the bad line."
                    ),
                )
            if not self.db_path.exists():
                raise StoreUnavailable(
                    f"no store found at registered path '{self.db_path}'",
                    hint=(
                        "The project directory may have been deleted or "
                        "moved. Re-run 'cadence register' from its new "
                        "location, or remove the stale entry from the "
                        "registry by hand."
                    ),
                )
        try:
            # mkdir lives inside this try (0.2.12 Red Team finding #3):
            # a bad CADENCE_DB_PATH / registry entry (e.g. one containing
            # an embedded null byte) can make mkdir itself raise
            # OSError/ValueError, which used to escape uncaught straight
            # past every "except CadenceError" call site -- aborting a
            # whole --all-projects loop instead of reporting one bad
            # project and continuing, contradicting that call's own
            # documented per-project-error contract.
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            with closing(self._connect()) as conn:
                conn.execute(SCHEMA)
                # Migration for stores created before parent_id existed
                # (docs/human-surface.md §4.7, decompose): sqlite has no
                # "ADD COLUMN IF NOT EXISTS" we can rely on across the
                # versions this store has been used with, so probe instead.
                cols = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)")}
                if "parent_id" not in cols:
                    conn.execute("ALTER TABLE tasks ADD COLUMN parent_id INTEGER")
                if "origin" not in cols:
                    # Structural sync-identity fix (task
                    # task_01a04b9b39057fc952517775 / Ines's spec
                    # r08-sync-finding-c-duplicate-fix-spec.md): every row
                    # needs a stable identity that survives renumbering.
                    # Backfill a fresh UUID for rows that predate this
                    # column -- one-time, immutable from here on.
                    conn.execute("ALTER TABLE tasks ADD COLUMN origin TEXT")
                    for row in conn.execute("SELECT id FROM tasks WHERE origin IS NULL").fetchall():
                        conn.execute(
                            "UPDATE tasks SET origin = ? WHERE id = ?",
                            (uuid.uuid4().hex, row["id"]),
                        )
                conn.commit()
        except (sqlite3.Error, OSError, ValueError) as exc:
            # e.g. CADENCE_DB_PATH points at a directory ("unable to open
            # database file") or a non-sqlite file ("file is not a
            # database") -- both otherwise surface as a raw sqlite3
            # traceback on every command (Red Team pass-1 finding #2).
            # OSError/ValueError (e.g. an embedded null byte reaching
            # mkdir, now inside this same try -- finding #3 above) get the
            # identical treatment: one clean CadenceError shape regardless
            # of which layer under the store actually objected.
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
            origin=row["origin"] if "origin" in keys else None,
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
        # Absolute, not merely as-given: this string is handed straight to
        # `git remote add origin <...>` on THIS client's own history repo,
        # which stores it byte-for-byte and only resolves it later, at
        # `git fetch` time, via `git -C <this store's own history dir>` --
        # and `-C` chdir()s into that dir before git resolves anything, so
        # a relative path here resolves against the WRONG base (this
        # store's own history dir, not the caller's actual cwd) and either
        # fetches nothing ("no Cadence store found") or -- worse -- a
        # coincidentally-present unrelated repo at that relative offset.
        # Resolving to absolute here, once, up front (while this process's
        # own cwd is still the caller's real cwd) makes every later git
        # invocation's own `-C` base irrelevant to this resolution.
        p = Path(remote).resolve()
        if p.is_dir():
            if (p / ".git").is_dir() or (p / "HEAD").is_file():
                return str(p)  # already points at a history repo (or bare repo)
            # Red Team MCP-stress-pass finding 2: a plain directory that is
            # NOT itself a git/bare repo is neither of the two things a
            # caller may legitimately pass (a git URL/bare-repo path, or a
            # peer's own CADENCE_DB_PATH *file*) -- it used to fall through
            # to the derivation below, which silently treated the directory
            # as if it were a peer's .db file and created a brand-new
            # sibling `<dirname>.history` git repo next to it, pushing
            # local tasks into a location nobody would ever sync from
            # again. Reject it with the same two-sentence shape other bad
            # remotes already get, before any side effect happens.
            raise InvalidTask(
                f"'{remote}' is a plain directory, not a git repo or a peer's .db file",
                hint=(
                    "Pass the other client's CADENCE_DB_PATH (its .db file "
                    "path) or a git URL / bare-repo path instead."
                ),
            )
        # Full filename, not stem -- see the matching comment on
        # Store._history() (R-08 re-verify Finding B). Must derive the
        # exact same way that method does, or the two would themselves
        # disagree about where a given CADENCE_DB_PATH's history lives.
        return str(p.parent / (p.name + ".history"))

    @staticmethod
    def _maybe_init_peer_history(remote: str) -> None:
        """R-08 re-verify Finding D (redteam_run7_0224/REDTEAM_PASS_0.2.4_sync_deep.md):
        a peer whose `.db` file exists but has never had a write or a sync
        of its own (e.g. it only ever ran `cadence list`, which creates the
        sqlite file but never touches `.history`) used to make `sync`
        report `no Cadence store found at '<path>'` -- true of the git
        history, false of the store, and actionable only from a command
        run *on that other client*, which the initiating side has no way
        to do. `Store()` already treats "first mutation on a fresh path"
        as an ordinary, silent event (`_snapshot_and_commit` -> `hist.ensure()`);
        this makes "first *push into* a fresh path" the same non-event
        instead of an error, by doing exactly what that first mutation
        would have done to the peer's history, before `fetch` ever runs.

        Deliberately narrow: only fires when `remote` is a plain local
        `.db`-style path (never a git URL/`git@`, matched the same way
        `_resolve_remote` already does) whose file exists on disk (so a
        genuinely wrong/nonexistent path -- see
        test_cli_sync_remote_help_and_error_name_the_db_path_contract --
        still fails exactly as before) but whose derived `.history` dir
        has no `.git` yet. Touches only that derivation and `GitHistory.ensure()`,
        never the diff/apply/merge logic Findings A/B/C already fixed.
        """
        if "://" in remote or remote.startswith("git@"):
            return
        p = Path(remote)
        if p.is_dir() and ((p / ".git").is_dir() or (p / "HEAD").is_file()):
            return  # already points straight at a history/bare repo
        if not p.exists():
            return  # nothing on disk at all -- a real bad-path error, not this case
        hist_dir = p.parent / (p.name + ".history")
        if (hist_dir / ".git").is_dir():
            return  # already initialized -- nothing to do
        GitHistory(hist_dir).ensure()

    def _snapshot_and_commit(
        self,
        tasks: list["Task"],
        message: str,
        reason: Optional[str] = None,
        source: Optional[str] = None,
    ) -> None:
        """wow-spec.md Part III §1a: `reason` (optional, on decompose/
        reprioritise/schedule only) rides as a second paragraph in the same
        commit this method already makes -- no new storage, no schema
        change. `source` ("cli" or "mcp", whichever surface called in)
        rides alongside it so `why` can render "-- you, via CLI" / "--
        agent, via MCP". Omitted entirely when `reason` is falsy, so every
        existing commit shape (and every caller that doesn't pass either)
        is byte-identical to before this change."""
        hist = self._history()
        hist.ensure()
        for t in tasks:
            # to_full_dict (not to_dict): the git-history blob is the sync
            # merge engine's own storage, so it must carry `origin` even
            # though CLI/MCP callers never see it.
            hist.write_task_file(t.to_full_dict())
        if reason:
            message = f"{message}\n\nReason: {reason}\nSource: {source or 'cli'}"
        try:
            hist.commit(message)
        except HistoryError as exc:
            # 0.2.12 Red Team finding #1: every caller of
            # _snapshot_and_commit has already committed `tasks` to
            # SQLite by this point -- see each mutator below -- so a
            # failure here must never look like the whole call failed.
            # history.py's own bounded retry (GitHistory._git) already
            # absorbed the ordinary transient race; reaching this except
            # means it's still contended after that, which is worth
            # surfacing, but as a degraded-success signal, not a lie.
            raise HistoryDegraded(tasks, str(exc)) from exc

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
        hist = self._history()
        # Red Team finding, docs/dogfooding-log.md 2026-09-03 ("second
        # unguarded data-loss path"): the INSERT below lets sqlite pick
        # this row's id from its own AUTOINCREMENT counter alone. On a
        # client that has only ever been a PASSIVE sync relay for some
        # other client (its `tasks/<id>.json` files exist on disk --
        # written straight into the working tree by the peer's push --
        # but its sqlite has never absorbed them, exactly the case
        # `_absorb_orphan_task_files` exists for), that counter starts
        # from empty and hands out id=1 first, colliding with an
        # on-disk id this client is unknowingly carrying for someone
        # else. `_snapshot_and_commit` below then writes tasks/<id>.json
        # unconditionally, silently overwriting that peer's only copy --
        # no sync involved, no error, no exit code signal.
        #
        # Absorbing every orphan into real sqlite rows FIRST closes the
        # gap the same way `_sync_diff_and_apply` already does: each
        # absorbed row's id is inserted into sqlite explicitly, which
        # advances sqlite's own AUTOINCREMENT high-water mark past it,
        # so the plain INSERT just below can never pick that id again.
        #
        # docs/dogfooding-log.md 2026-09-04: absorbing is safe, but it used
        # to be silent -- a caller had no way to tell "I made 1 task" from
        # "I made 1 task and this call also recovered a stray one" without
        # diffing `list` before/after. `recovered` carries that distinctly;
        # it's attached to the returned Task (not a dataclass field --
        # never written to disk or into `to_dict()`/`to_full_dict()`,
        # since it describes this CALL, not the task itself) so CLI/MCP
        # can report it without changing `add`'s return type for every
        # existing caller.
        recovered = self._absorb_orphan_task_files(hist, set())
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "INSERT INTO tasks (title, status, priority, due, created_at, origin) "
                "VALUES (?, 'pending', ?, ?, ?, ?)",
                (title, priority, due, _now(), uuid.uuid4().hex),
            )
            conn.commit()
            task = self.get(cur.lastrowid, _conn=conn)
        task.recovered = recovered
        self._snapshot_and_commit([task], f"Added #{task.id}: {task.title}")
        return task

    def list(
        self, status: Optional[str] = None, _conn: Optional[sqlite3.Connection] = None
    ) -> list[Task]:
        """status=None or 'all' returns everything; otherwise filters.

        Order, per docs/human-surface.md §4.8 (the fix for Red Team pass-1/7
        finding #3 -- list_tasks's own docstring claimed this ordering
        while the store actually used plain insertion order): open tasks
        sort by priority (high -> med -> low -> none) then id ascending
        within a tier; done tasks always sort after open ones, by
        completed_at descending (most recently finished first).

        `_conn`, like `get`'s, lets a caller already holding an open
        (possibly uncommitted) connection read its own pending writes --
        a fresh connection here would only ever see the last COMMITTED
        state, which is wrong mid-transaction (see the sync commit-order
        fix in `_sync_diff_and_apply`)."""
        if status not in (None, "all") and status not in VALID_STATUSES:
            raise InvalidTask(
                f"status must be one of {VALID_STATUSES + ('all',)}, got {status!r}",
                hint="Use one of: pending, done, all.",
            )
        owns_conn = _conn is None
        conn = _conn or self._connect()
        try:
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
        finally:
            if owns_conn:
                conn.close()

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

    def schedule(
        self,
        task_id: int,
        due: str,
        reason: Optional[str] = None,
        source: Optional[str] = None,
    ) -> Task:
        due = _validate_due(due)
        with closing(self._connect()) as conn:
            self.get(task_id, _conn=conn)
            conn.execute("UPDATE tasks SET due = ? WHERE id = ?", (due, int(task_id)))
            conn.commit()
            task = self.get(task_id, _conn=conn)
        self._snapshot_and_commit(
            [task], f"Scheduled #{task.id} for {due}: {task.title}",
            reason=reason, source=source,
        )
        return task

    def reprioritise(
        self,
        task_id: int,
        priority: str,
        reason: Optional[str] = None,
        source: Optional[str] = None,
    ) -> Task:
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
            reason=reason, source=source,
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

    def decompose(
        self,
        parent_id: int,
        titles: list[str],
        reason: Optional[str] = None,
        source: Optional[str] = None,
    ) -> tuple[Task, list[Task]]:
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
        # Same bug and fix as `add()` above (docs/dogfooding-log.md
        # 2026-09-03): subtask ids below come from the same plain
        # INSERT/AUTOINCREMENT allocation `add()` uses, so a client only
        # ever used as a passive sync relay is exposed the same way --
        # absorb any orphan task file into real sqlite rows first, before
        # opening the connection that allocates the new ids, so it can
        # never collide with (and silently overwrite) one.
        #
        # Same legibility fix as `add()` too (docs/dogfooding-log.md
        # 2026-09-04): `recovered` is attached to the returned `parent`
        # below so CLI/MCP can report it distinctly from the subtasks the
        # caller actually asked for.
        recovered = self._absorb_orphan_task_files(self._history(), set())
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
                    "INSERT INTO tasks (title, status, priority, due, created_at, parent_id, origin) "
                    "VALUES (?, 'pending', NULL, NULL, ?, ?, ?)",
                    (t, _now(), parent_id, uuid.uuid4().hex),
                )
                children.append(self.get(cur.lastrowid, _conn=conn))
            conn.commit()
            parent = self.get(parent_id, _conn=conn)
        parent.recovered = recovered
        ids = ", ".join(f"#{c.id}" for c in children)
        self._snapshot_and_commit(
            [parent, *children],
            f"Decomposed #{parent.id} into {len(children)} subtasks: {ids}",
            reason=reason, source=source,
        )
        return parent, children

    def subtasks(self, parent_id: int) -> list[Task]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE parent_id = ? ORDER BY id ASC", (parent_id,)
            ).fetchall()
            return [self._row_to_task(r) for r in rows]

    # -- why (wow-spec.md Part III §1b) ----------------------------------

    def _describe_why_event(self, before: Optional[dict], after: dict) -> tuple[str, bool]:
        """(plain-language description, reason_capable) for one commit that
        touched this task, given the task's content just before and just
        after it. `reason_capable` says whether this *kind* of event is one
        of the three verbs wow-spec.md Part III §1a lets a caller attach a
        reason to (decompose/reprioritise/schedule) -- `why` only offers
        the "no reason was recorded" nudge for those, never for add/done/
        undo, which never had a reason to record in the first place."""
        if before is None:
            parent_id = after.get("parent_id")
            if parent_id is not None:
                try:
                    parent_title = self.get(parent_id).title
                    return f"Created as subtask of #{parent_id} ({parent_title})", True
                except CadenceError:
                    return f"Created as subtask of #{parent_id}", True
            return "Created", False
        if before.get("status") != after.get("status"):
            return ("Completed" if after.get("status") == "done" else "Reopened"), False
        if before.get("priority") != after.get("priority"):
            old = before.get("priority") or "none"
            new = after.get("priority") or "none"
            return f"Reprioritised ({old} → {new})", True
        if before.get("due") != after.get("due"):
            old_due, new_due = before.get("due"), after.get("due")
            if old_due is None:
                return f"Scheduled for {new_due}", True
            return f"Scheduled (due {old_due} → {new_due})", True
        return "Updated", False

    def why(self, task_id: int) -> dict:
        """Render task `task_id`'s git-backed history (already written by
        every mutation above) as a plain-language timeline, newest first --
        wow-spec.md Part III §1b: "cadence why replaces 'go find a hidden
        .git directory and run git log' outright." A thin read layer: each
        task is already one file at tasks/<id>.json (history.py's module
        docstring), so this is `git log -- that file`, not a new index.

        Raises TaskNotFound (same "no task with id N" wording every other
        verb uses, per §4.4) if the id doesn't exist.

        Returns {"task": Task, "events": [{"event", "priority", "at"
        (ISO), "reason", "source", "reason_capable"}, ...]}, newest first.
        """
        task = self.get(task_id)  # raises TaskNotFound/InvalidTask
        hist = self._history()
        hist.ensure()
        relpath = f"tasks/{task_id}.json"
        events = []
        for commit in hist.log_for_file(relpath):
            after_raw = hist.show_file(commit, relpath)
            if after_raw is None:
                continue  # file didn't exist at this commit -- nothing to describe
            after = json.loads(after_raw)
            parent_commit = hist.first_parent(commit)
            before_raw = hist.show_file(parent_commit, relpath) if parent_commit else None
            before = json.loads(before_raw) if before_raw is not None else None
            subject, reason, source = hist.parse_trailers(commit)
            desc, reason_capable = self._describe_why_event(before, after)
            if subject.startswith("Undo:"):
                desc = f"{desc} undone"
            events.append({
                "event": desc,
                "priority": after.get("priority") or "none",
                "at": hist.commit_time(commit),
                "reason": reason,
                "source": source,
                "reason_capable": reason_capable,
            })
        return {"task": task, "events": events}

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
        pre-undo state (docs/human-surface.md §4.9).

        The sqlite-side revert below is only committed once the matching
        history commit (`hist.commit(...)`) has actually succeeded. Red
        Team's 0226 finding: this used to commit sqlite first and call
        `hist.commit` after, with no rollback -- so a git-side failure
        there (e.g. an unrelated unreadable file elsewhere in the tree
        breaking `git add -A`) left a task permanently deleted from
        sqlite while the reported error implied nothing had happened.
        Now any failure anywhere in this method rolls the whole sqlite
        transaction back before it's ever raised, so sqlite and history
        can never disagree about what undo did."""
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
        conn = self._connect()
        try:
            for relpath in changed:
                task_id = int(Path(relpath).stem)
                before_row = conn.execute(
                    "SELECT * FROM tasks WHERE id = ?", (task_id,)
                ).fetchone()
                before_task = self._row_to_task(before_row) if before_row else None
                before = before_task.to_dict() if before_task else None
                prev_content = hist.show_file(prev, relpath)
                after = None
                if prev_content is not None:
                    after = json.loads(prev_content)
                    # `origin` is immutable identity, not a field any
                    # mutation (including undo) may change -- a revert
                    # must never blank it out or fork a new one, or this
                    # row would look like a brand-new task to the sync
                    # merge engine. Older history blobs written before
                    # this column existed won't have it; fall back to
                    # whatever this row already carries (backfilled at
                    # migration), never a fresh UUID here.
                    origin = after.get("origin") or (before_task.origin if before_task else None)
                    conn.execute(
                        "INSERT INTO tasks (id, title, status, priority, due, created_at, "
                        "completed_at, parent_id, origin) VALUES (?,?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(id) DO UPDATE SET title=excluded.title, "
                        "status=excluded.status, priority=excluded.priority, "
                        "due=excluded.due, created_at=excluded.created_at, "
                        "completed_at=excluded.completed_at, parent_id=excluded.parent_id, "
                        "origin=COALESCE(excluded.origin, tasks.origin)",
                        (
                            after["id"], after["title"], after["status"], after["priority"],
                            after["due"], after["created_at"], after["completed_at"],
                            after.get("parent_id"), origin,
                        ),
                    )
                    after["origin"] = origin
                    hist.write_task_file(after)
                else:
                    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
                    hist.remove_task_file(task_id)
                if before is not None:
                    descriptions.append(self._describe_revert(task_id, before, after))
            original_message = hist.message_of(last)
            summary = "; ".join(descriptions) if descriptions else original_message
            # This must be the LAST thing that can fail before `conn.commit()`
            # below -- see the docstring above.
            hist.commit(f"Undo: {original_message}", allow_empty=True)
        except Exception as exc:
            conn.rollback()
            conn.close()
            if isinstance(exc, CadenceError):
                raise
            raise UndoFailed(
                f"undo's history entry failed to record ({type(exc).__name__}: {exc})",
                hint=(
                    "Nothing was changed -- the task list is exactly what "
                    "it was before this undo. Run 'cadence list' to "
                    "confirm, or file a bug."
                ),
            ) from exc
        conn.commit()
        conn.close()
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
        # `origin` rides along with every applied row -- COALESCE so an
        # older peer's blob that predates this column (no "origin" key)
        # never blanks out an origin this row already has locally.
        conn.execute(
            "INSERT INTO tasks (id, title, status, priority, due, created_at, "
            "completed_at, parent_id, origin) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET title=excluded.title, "
            "status=excluded.status, priority=excluded.priority, due=excluded.due, "
            "created_at=excluded.created_at, completed_at=excluded.completed_at, "
            "parent_id=excluded.parent_id, origin=COALESCE(excluded.origin, tasks.origin)",
            (
                data["id"], data["title"], data["status"], data["priority"],
                data["due"], data["created_at"], data["completed_at"],
                data.get("parent_id"), data.get("origin"),
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
        "already_synced": bool, "warnings": [str, ...]}.

        `warnings`: non-fatal problems this sync call noticed but did not
        let stop it or destroy anything over. Currently the only source is
        an on-disk task file this store's self-heal step found stale
        (absent from its own sqlite) but could not read (permission-denied
        or similar) to confirm that -- such a file is left in place, never
        deleted, and named here instead of being silently dropped.
        """
        hist = self._history()
        hist.ensure()
        given_remote = remote
        if remote:
            # Resolve (and validate) first: Finding 2 depends on this
            # raising for a bare non-repo directory *before*
            # _maybe_init_peer_history gets a chance to create a sibling
            # history repo for it as a side effect.
            resolved_remote = self._resolve_remote(remote)
            # R-08 re-verify Finding D: transparently bootstrap a peer that
            # exists on disk but has never written or synced -- see
            # _maybe_init_peer_history -- before fetching it, so pushing
            # into a brand-new second device just works instead of
            # misreporting "no Cadence store found".
            self._maybe_init_peer_history(remote)
            hist.set_remote(resolved_remote)
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
                "already_synced": n == 0, "warnings": [],
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
                    "Nothing was changed. This usually means a task file in "
                    "the history store is corrupted or in an unexpected "
                    "shape -- inspect <store>.history/tasks/*.json for a bad "
                    "file, or file a bug. (Distinct clients sharing one "
                    "CADENCE_DB_PATH is a different, already-guarded case.)"
                ),
            ) from exc

    @staticmethod
    def _no_origin(data: dict) -> dict:
        """Strip the merge-engine-only `origin` field before a task dict
        crosses into a `conflicts` entry returned to a CLI/MCP caller --
        origin is never part of that contract (see Task.to_dict)."""
        return {k: v for k, v in data.items() if k != "origin"}

    @staticmethod
    def _first_sync_task_base(hist: GitHistory, task_id: int) -> Optional[dict]:
        """This client's own most-recent-before-now content for local id
        `task_id`, used ONLY as a stand-in base for the "both sides
        already know this origin" branch of `_sync_diff_and_apply` on a
        client's first-ever sync (no refs/cadence/sync-base commit yet
        -- see the call site). Every task file this client has ever
        itself written by calling add/complete/schedule/decompose/undo
        is committed to this client's own git history one file per id.
        The true base is this client's own most recent PRE-sync edit --
        not the OLDEST/creation-time commit: if this client edited the
        row itself before ever syncing (e.g. scheduled it), the remote
        may already have received that exact edit through an earlier
        sync of ITS OWN; diffing against stale creation content would
        then make `mine_changed` true for an edit the remote already
        fully has, reporting a false "changed on both sides" conflict
        (redteam week-2 dogfooding find, docs/dogfooding-log.md, commit
        08abb36) whenever the remote also touched the row afterward.

        But not every commit reachable from this client's HEAD for this
        file was actually made BY this client: `_sync_diff_and_apply`'s
        own push step (`push_safe_merge`) writes a MERGE commit (parent
        1 = this store's own prior head, parent 2 = the pushING side's
        head) straight into whichever store it targets, as the passive
        REMOTE side of some OTHER client's sync call -- landing that
        other client's entire commit ancestry as reachable history here
        without this client's sqlite or sync bookkeeping ever finding
        out (see the "Self-heal" comment at this method's call site).
        Plain `log_for_file` walks BOTH parents of a merge and would
        surface that foreign ancestry (and, since default history
        simplification skips a merge that is TREESAME to one parent for
        this path, it can even skip straight past the tell-tale
        `_SYNC_PUSH_LANDING_MESSAGE` merge commit itself and hand back
        the other side's own commit under its own original message,
        indistinguishable from a genuine self-edit by message alone).
        `mainline_log_for_file` (`--first-parent`) instead walks only
        this store's own real timeline -- a push always lands as a
        merge on top of it, never spliced into it -- so filtering out
        any commit whose subject is exactly the landing message and
        taking the most recent survivor gives this client's own true
        latest edit even when a peer pushed into this store first. This
        client created this row itself (the only way it can hold this
        origin before its own first sync), so at least the creation
        commit always survives that filter. Returns None (never a
        crash) for an id with no history at all (pre-migration row);
        the caller then falls back to the prior "no base known"
        behaviour for that one origin only."""
        relpath = f"tasks/{task_id}.json"
        commits = hist.mainline_log_for_file(relpath)
        if not commits:
            return None
        chosen = next(
            (c for c in commits if hist.message_of(c) != _SYNC_PUSH_LANDING_MESSAGE),
            commits[-1],
        )
        content = hist.show_file(chosen, relpath)
        if not content:
            return None
        try:
            return json.loads(content)
        except ValueError:
            return None

    def _absorb_orphan_task_files(self, hist: GitHistory, skip_origins: set) -> None:
        """A task file can sit in this store's OWN checked-out tree
        (`tasks/<id>.json`) without this client's sqlite ever having
        heard of it: `push_safe_merge` writes straight into the
        checked-out working tree of whichever store it targets
        (`receive.denyCurrentBranch=updateInstead`), so being used as
        the passive REMOTE for some OTHER client's sync call leaves a
        real task file on disk with no corresponding sqlite row at all.

        The self-heal rewrite below (`final = ... from self.list()`)
        treats "not in my own sqlite" as drift to erase -- correct for
        a genuine stale duplicate (an old file left behind whose origin
        sqlite already holds under a different id, the case self-heal
        was built for) but wrong here: this file's origin is one this
        client has never seen anywhere, so erasing it -- or letting a
        later `alloc()` hand out its on-disk id to some unrelated
        origin and overwrite it -- silently destroys the only copy of a
        task nobody told this client to forget (redteam A2/X2/C2 hub
        finding, docs/dogfooding-log.md 2026-09-03).

        Absorb it into sqlite as a genuine local row first, at its own
        on-disk id (always free, since we only reach here when that id
        is not already in sqlite), so it becomes real content this
        client itself now holds: counted in `local_used` so no later
        allocation can collide with its file, and diffed/pushed like
        any other row it knows -- never purged, never silently
        clobbered. A file whose origin IS already known under some
        other id is left alone here on purpose: that is real drift, and
        self-heal below still cleans it up exactly as before.

        `skip_origins` is every origin THIS sync call's own peer
        (`theirs`) already reports -- for those, the ordinary "New to
        me" pull branch in `_sync_diff_and_apply` below already adopts
        the file correctly (real `theirs`-vs-`base` content, a
        genuine `pulled` count in the returned summary). Absorbing
        those here too would pre-empt that branch with a bare
        same-content no-op, hiding a real pull as "already_synced" --
        this is only for a THIRD party's origin, one that never came
        through this call's own peer at all.

        Dov's independent 0.2.24 pass (docs/dogfooding-log.md
        2026-09-03) found this loop only reserves an id for a file it
        can BOTH parse as JSON and find a real "origin" key in -- a
        truncated/unparseable write (an ordinary crash mid-write;
        `write_task_file` has no atomic temp+rename), a well-formed
        object missing "origin", or valid JSON that isn't an object
        all just `continue` past, leaving that id free for the next
        plain INSERT to claim and overwrite unconditionally (or, for
        the non-object case, `data.get` used to raise an uncaught
        AttributeError -- see the isinstance guard below). Reserving
        every on-disk id up front, before any per-file parsing,
        closes all three shapes with one change: it can only reserve
        MORE ids than the old code did, never fewer, so it changes
        nothing about which files get genuinely absorbed as rows.

        Returns the list of `Task`s actually absorbed this call (empty if
        none) -- docs/dogfooding-log.md 2026-09-04 legibility finding:
        `add`/`decompose` used to swallow this silently, so a caller
        (human or agent) had no way to tell "I made 1 task" from "I made
        1 task and this call also recovered a stray one" short of diffing
        `list` before and after. Every caller of this method now surfaces
        that list distinctly instead of folding it into its own result.
        """
        if not hist.tasks_dir.exists():
            return []
        self._reserve_orphan_ids(hist)
        known = self.list(status="all")
        known_ids = {t.id for t in known}
        known_origins = {t.origin for t in known if t.origin}
        recovered: list[Task] = []
        for p in sorted(hist.tasks_dir.glob("*.json")):
            try:
                file_id = int(p.stem)
            except ValueError:
                continue
            if file_id in known_ids:
                continue
            try:
                data = json.loads(p.read_text())
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict):
                # Valid JSON, wrong shape (e.g. a bare array): `.get`
                # below would raise. Its id is already reserved by
                # `_reserve_orphan_ids` above, so skipping it here is
                # safe -- just not something this loop can turn into a
                # real row.
                continue
            origin = data.get("origin")
            if not origin or origin in known_origins or origin in skip_origins:
                continue
            row = dict(data, id=file_id)
            with closing(self._connect()) as conn:
                self._apply_remote_task(conn, row)
                conn.commit()
                recovered.append(self.get(file_id, _conn=conn))
            known_ids.add(file_id)
            known_origins.add(origin)
        return recovered

    def _reserve_orphan_ids(self, hist: GitHistory) -> None:
        """Bump sqlite's own AUTOINCREMENT high-water mark (the
        `sqlite_sequence` row for `tasks`) up to at least the highest
        `tasks/<id>.json` filename on disk, regardless of whether that
        file's contents can be parsed or understood.

        This does NOT insert a task row -- it only claims the id so a
        later plain `INSERT INTO tasks (...)` (no explicit id, in
        `add`/`decompose`) can never be handed that number by sqlite
        and silently overwrite the file `write_task_file` would then
        write there unconditionally. A file the loop below CAN absorb
        reserves its own id anyway, via its own explicit-id INSERT
        (`_apply_remote_task`); this is only what closes the gap for
        the ones it can't.
        """
        max_on_disk = 0
        for p in hist.tasks_dir.glob("*.json"):
            try:
                max_on_disk = max(max_on_disk, int(p.stem))
            except ValueError:
                continue
        if max_on_disk <= 0:
            return
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT seq FROM sqlite_sequence WHERE name = 'tasks'"
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO sqlite_sequence (name, seq) VALUES ('tasks', ?)",
                    (max_on_disk,),
                )
            elif row["seq"] < max_on_disk:
                conn.execute(
                    "UPDATE sqlite_sequence SET seq = ? WHERE name = 'tasks'",
                    (max_on_disk,),
                )
            conn.commit()

    def _sync_diff_and_apply(self, hist: GitHistory, theirs_ref: str) -> dict:
        """Structural sync-identity fix (task
        task_01a04b9b39057fc952517775, spec
        concept_notes/r08-sync-finding-c-duplicate-fix-spec.md, finding
        redteam_r08_v3_0222/rafael_0.2.3_resync_finding.md): identity for
        merge purposes is each row's immutable `origin` UUID (assigned
        once at creation, never touched by renumbering/sync/undo) -- never
        the display `id` (which renumbering intentionally reassigns) and
        never a content-fingerprint proxy for it (which stops working the
        moment a row has been through one sync round and becomes
        "based", which is exactly what let a 3rd ordinary re-sync of an
        already-converged pair reintroduce the duplicate 0.2.3 shipped).

        A client's own display-id numbering for a given origin, once
        assigned, never changes again on that client -- clients are never
        required to agree with each other on what number a given task is
        under; only content-by-origin has to converge. The only thing
        that can still collide is a *display id* against some other
        origin already using that number on the same side; that is
        display-only bookkeeping, resolved by `renumbered`, never a real
        identity conflict (two independent rows can never share a UUID).
        """
        base_ref = hist.sync_base_sha()
        theirs = hist.snapshot_at(theirs_ref)
        base = hist.snapshot_at(base_ref) if base_ref else {}
        # Absorb any task file this store is only passively carrying for
        # a THIRD party (see _absorb_orphan_task_files) into real sqlite
        # rows BEFORE `mine`/`local_used` are built below, so the diff
        # loop and the self-heal rewrite both see it as genuinely known
        # content -- skipping any origin `theirs` (this call's own peer)
        # already reports, which the ordinary pull branch below already
        # adopts correctly with a real `pulled` count.
        theirs_origins = {d.get("origin") for d in theirs.values() if d.get("origin")}
        self._absorb_orphan_task_files(hist, theirs_origins)
        # to_full_dict, not to_dict: this merge engine needs `origin`;
        # nothing here is returned to a CLI/MCP caller directly.
        mine = {t.id: t.to_full_dict() for t in self.list(status="all")}

        def by_origin(d: dict) -> dict:
            idx = {}
            for tid, data in d.items():
                # Rows written before this column existed carry no
                # `origin` at all (pre-migration git blobs) -- fall back
                # to a per-dict, id-scoped key so such legacy rows only
                # ever match each other by the old id-based rule, instead
                # of accidentally colliding with an unrelated row.
                o = data.get("origin") or f"__legacy_id_{tid}__"
                idx[o] = data
            return idx

        mine_by_o = by_origin(mine)
        theirs_by_o = by_origin(theirs)
        base_by_o = by_origin(base)

        all_ids = set(mine) | set(theirs) | set(base)
        seed_next = (max(all_ids) + 1) if all_ids else 1
        local_used, remote_used = set(mine), set(theirs)
        counters = {"local": seed_next, "remote": seed_next}

        def alloc(used: set, preferred, space: str) -> int:
            if preferred is not None and preferred not in used:
                used.add(preferred)
                return preferred
            new_id = counters[space]
            counters[space] += 1
            used.add(new_id)
            return new_id

        pulled_ids, conflicts, renumbered = [], {}, []
        push_plan: list[tuple[int, int, dict]] = []  # (local_id, remote_id, data)

        for o in set(mine_by_o) | set(theirs_by_o) | set(base_by_o):
            m, t, b = mine_by_o.get(o), theirs_by_o.get(o), base_by_o.get(o)
            m_fp = self._content_fingerprint(m) if m is not None else None
            t_fp = self._content_fingerprint(t) if t is not None else None

            if m is not None and t is not None and m_fp == t_fp:
                continue  # same content already, whatever id each side shows it under
            if m is None and t is None:
                continue

            # Chairman demo, 2026-08-31 (docs/dogfooding-log.md): on this
            # client's FIRST-EVER sync (no refs/cadence/sync-base commit
            # yet) `b` is always None here, which used to make ANY row
            # both sides already know (m and t both present) look
            # "changed on both sides" -- even one this client has never
            # touched since creating it. Scoped tightly to that one
            # case (never to the t-is-None/m-is-None branches below,
            # where "no base" correctly means "brand new, always
            # push/pull"): a client can only already hold an origin
            # before its own first sync by having created that row
            # itself, so that row's own creation content -- read back
            # from this client's own git log -- is the real base to
            # diff against, not an empty one.
            if b is None and base_ref is None and m is not None and t is not None:
                b = self._first_sync_task_base(hist, m["id"])

            b_fp = self._content_fingerprint(b) if b is not None else None
            mine_changed = m_fp != b_fp
            theirs_changed = t_fp != b_fp

            if t is None:
                # Only I know this task. (Theirs having dropped a
                # previously-based origin -- this store has no delete
                # verb -- is a no-op: nothing to push, and resurrecting
                # it as a "pull" isn't right either, so leave it alone.)
                if mine_changed:
                    remote_id = alloc(remote_used, m["id"], "remote")
                    row = dict(m, id=remote_id)
                    push_plan.append((m["id"], remote_id, row))
                    # Not reported in `renumbered`: this only changes what
                    # id the REMOTE stores this task under, which is
                    # invisible in this client's own `list()` -- nothing
                    # about my own numbering moved.
                continue

            if m is None:
                # New to me. Under origin identity this is never a real
                # collision (two independently-created rows can't share a
                # UUID) -- the only clash possible is theirs' display id
                # against some *other* origin I already hold under that
                # number, which just needs a fresh local number.
                local_id = alloc(local_used, t.get("id"), "local")
                row = dict(t, id=local_id)
                mine[local_id] = row
                pulled_ids.append(local_id)
                if local_id != t.get("id"):
                    renumbered.append(
                        {"old_id": t.get("id"), "new_id": local_id, "kept_at_old_id": "mine"}
                    )
                continue

            # Both sides already know this task (by origin).
            if mine_changed and theirs_changed:
                conflicts[m["id"]] = {
                    "mine": self._no_origin(m), "theirs": self._no_origin(t),
                }
            elif theirs_changed:
                # My own display id for this origin never moves.
                row = dict(t, id=m["id"])
                mine[m["id"]] = row
                pulled_ids.append(m["id"])
            elif mine_changed:
                push_plan.append((m["id"], t["id"], dict(m, id=t["id"])))

        if not pulled_ids and not push_plan and not conflicts:
            return {
                "pulled": 0, "pushed": 0, "conflicts": [], "renumbered": [],
                "already_synced": True, "warnings": [],
            }

        local_head = hist.head()
        # This connection is NOT committed until every history (git) write
        # below has actually succeeded -- see `except Exception` at the
        # bottom of this block. Red Team's 0226 finding: this used to
        # commit the pulled rows here, then do the self-heal file rewrite
        # and `advance_local` afterward with no rollback on failure -- so
        # a write failure below (e.g. an unreadable file elsewhere in the
        # tree breaking a plain file write) left sqlite holding a pull
        # that history never recorded, while `sync()`'s own wrapper told
        # the caller "Nothing was changed." Now that claim is guaranteed
        # true: any failure anywhere in this block rolls the whole
        # transaction back before the exception ever reaches that wrapper.
        conn = self._connect()
        try:
            for local_id in pulled_ids:
                self._apply_remote_task(conn, mine[local_id])

            # Self-heal: rewrite EVERY task file from sqlite truth (not just
            # the rows this round touched) before committing locally. A
            # peer's earlier direct push writes straight into this store's
            # own checked-out history dir
            # (receive.denyCurrentBranch=updateInstead) without this store's
            # sqlite or sync bookkeeping ever finding out -- reading that
            # drifted tree back on a later sync is exactly what reintroduced
            # the duplicate on a 3rd/4th round. A blind `git add -A` would
            # launder that drift straight into this store's own next commit;
            # rewriting every file from `mine` first heals it as soon as this
            # store next syncs at all.
            #
            # Reads on THIS SAME connection (`_conn=conn`), not a fresh
            # one: the pulled rows applied above are only visible within
            # this still-open transaction until it commits below.
            final = {t.id: t.to_full_dict() for t in self.list(status="all", _conn=conn)}
            on_disk_ids = set()
            if hist.tasks_dir.exists():
                for p in hist.tasks_dir.glob("*.json"):
                    try:
                        on_disk_ids.add(int(p.stem))
                    except ValueError:
                        continue
            # Red Team independent pass on 0.2.25 (docs/dogfooding-log.md
            # 2026-09-04): an on-disk id absent from sqlite is "stale drift
            # to erase" ONLY if this store could actually read it and
            # confirm that -- a file that EXISTS but can't be READ (chmod
            # 000 / restrictive ACL) is not stale, it's simply unknown, and
            # `remove_task_file` only checks write permission on the parent
            # directory, never read permission on the file itself, so it
            # would unlink it with zero warning. Reading each candidate
            # first before deleting makes this exactly as safe as the
            # "nothing pending" sync path, where such a file already
            # survives untouched -- behaviour must not depend on what else
            # this sync call had to do.
            warnings = []
            unreadable_relpaths = []
            for stale_id in on_disk_ids - set(final):
                stale_path = hist.task_path(stale_id)
                try:
                    stale_path.read_text()
                except OSError as exc:
                    warnings.append(
                        f"#{stale_id}: on-disk task file '{stale_path}' could "
                        f"not be read ({exc.strerror or exc}) so it was left in "
                        f"place instead of being treated as stale drift. It is "
                        f"NOT tracked by this client -- fix its permissions and "
                        f"sync again to have it either absorbed or cleaned up."
                    )
                    # Left alone on disk, but `advance_local` below must not
                    # let a blind `git add -A` trip over its own inability to
                    # read this same file -- see the `exclude` arg there.
                    unreadable_relpaths.append(f"tasks/{stale_path.name}")
                    continue
                hist.remove_task_file(stale_id)
            for tid, data in final.items():
                hist.write_task_file(data)

            # What we're willing to push: only content whose origin is
            # genuinely mine to assert (new-to-remote or edited-by-me),
            # each landing at a remote id chosen against the REMOTE's own
            # id-space -- never a copy of what I just pulled (that would be
            # re-asserting the other side's own task back at it, exactly the
            # mechanism that clobbered a peer's own file in the pre-fix
            # code), and never my own display-id blindly reused if the
            # remote already uses that number for a different origin.
            overlay = {remote_id: data for _, remote_id, data in push_plan}
            pushed_ok = True
            if overlay:
                pushed_ok = hist.push_safe_merge(
                    theirs_ref, overlay, _SYNC_PUSH_LANDING_MESSAGE, [theirs_ref, local_head]
                )
            # Fold origin's history into local's own timeline too (pulled
            # writes, and the self-heal rewrite above, are already applied to
            # the local working tree).
            hist.advance_local(
                [local_head, theirs_ref],
                f"sync: pulled {len(pulled_ids)}, pushed {len(push_plan)}, "
                f"renumbered {len(renumbered)}",
                exclude=unreadable_relpaths,
            )
        except Exception:
            conn.rollback()
            conn.close()
            raise
        conn.commit()
        conn.close()

        if conflicts:
            existing = hist.load_conflicts()
            existing.update(conflicts)
            hist.save_conflicts(existing)
        else:
            hist.set_sync_base(hist.head())

        return {
            "pulled": len(pulled_ids),
            "pushed": len(push_plan) if pushed_ok else 0,
            "conflicts": [{"id": tid, **v} for tid, v in conflicts.items()],
            "renumbered": renumbered,
            "already_synced": False,
            "warnings": warnings,
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
        # `conflicts[task_id][keep]` was stripped of `origin` before being
        # reported (never part of the CLI/MCP contract) and, if `keep`
        # is "theirs", may still carry the REMOTE's own display id for
        # this origin rather than this client's -- always apply/store it
        # under `task_id` (this client's own numbering never moves) and
        # re-attach this row's real origin from what's already on disk.
        existing_origin = self.get(task_id).origin
        chosen = dict(conflicts[task_id][keep])
        chosen["id"] = task_id
        chosen["origin"] = existing_origin
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
                # Push under whatever id the REMOTE already uses for this
                # same origin (if it knows this task at all), never
                # blindly `task_id` -- clients aren't required to agree
                # on numbering, so reusing task_id verbatim could land on
                # a different, unrelated task on the remote's side.
                remote_id = task_id
                if existing_origin:
                    for rid, rdata in hist.snapshot_at(theirs_ref).items():
                        if rdata.get("origin") == existing_origin:
                            remote_id = rid
                            break
                push_data = dict(chosen, id=remote_id)
                message = f"sync: resolved #{task_id} (kept {keep})"
                pushed = hist.push_safe_merge(
                    theirs_ref, {remote_id: push_data}, message, [theirs_ref, local_head]
                )
                if pushed:
                    hist.advance_local([local_head, theirs_ref], message)
                    if not hist.load_conflicts():
                        # No other conflict is still open, so this client's
                        # view of "last known good" can advance -- same rule
                        # a clean (conflict-free) sync() uses.
                        hist.set_sync_base(hist.head())
        return self.get(task_id)
