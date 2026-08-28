"""Embedded SQLite store for Cadence tasks.

This is the single source of truth used by both the CLI (cadence.cli) and
the MCP server (cadence.mcp_server), so a human and an agent are always
looking at the same data through the same rules.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

### Priority values match docs/human-surface.md exactly ("high"/"med"/"low");
### a task may also carry no priority at all (None), which is the default.
VALID_PRIORITIES = ("low", "med", "high")
VALID_STATUSES = ("pending", "done")

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    priority TEXT,
    due TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
"""


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


@dataclass
class Task:
    id: int
    title: str
    status: str
    priority: str
    due: Optional[str]
    created_at: str
    completed_at: Optional[str]

    def to_dict(self) -> dict:
        return asdict(self)


class Store:
    """Thin wrapper around a SQLite file holding one `tasks` table."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.execute(SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> Task:
        return Task(
            id=row["id"],
            title=row["title"],
            status=row["status"],
            priority=row["priority"],
            due=row["due"],
            created_at=row["created_at"],
            completed_at=row["completed_at"],
        )

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
        if priority is not None and priority not in VALID_PRIORITIES:
            raise InvalidTask(
                f"priority must be one of {VALID_PRIORITIES}, got {priority!r}",
                hint="Use one of: low, med, high.",
            )
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "INSERT INTO tasks (title, status, priority, due, created_at) "
                "VALUES (?, 'pending', ?, ?, ?)",
                (title, priority, due, _now()),
            )
            conn.commit()
            return self.get(cur.lastrowid, _conn=conn)

    def list(self, status: Optional[str] = None) -> list[Task]:
        """status=None or 'all' returns everything; otherwise filters.

        Order is insertion order (id ASC) -- the human surface does not
        re-sort by priority, matching tools/human-surface-prototype.
        """
        with closing(self._connect()) as conn:
            if status in (None, "all"):
                rows = conn.execute("SELECT * FROM tasks ORDER BY id ASC").fetchall()
            else:
                if status not in VALID_STATUSES:
                    raise InvalidTask(
                        f"status must be one of {VALID_STATUSES + ('all',)}, got {status!r}",
                        hint="Use one of: pending, done, all.",
                    )
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE status = ? ORDER BY id ASC",
                    (status,),
                ).fetchall()
            return [self._row_to_task(r) for r in rows]

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
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise TaskNotFound(
                    f"no task with id {task_id}",
                    hint="Run `cadence list --all` to see valid ids.",
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
            return self.get(task_id, _conn=conn)

    def schedule(self, task_id: int, due: str) -> Task:
        due = (due or "").strip()
        if not due:
            raise InvalidTask(
                "due must be a non-empty date/time string",
                hint="Use an ISO date like 2026-09-01.",
            )
        with closing(self._connect()) as conn:
            self.get(task_id, _conn=conn)
            conn.execute("UPDATE tasks SET due = ? WHERE id = ?", (due, int(task_id)))
            conn.commit()
            return self.get(task_id, _conn=conn)
