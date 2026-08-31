"""Cross-project registry: docs/wow-spec.md Part II.

A plain-text list of registered project store paths at
~/.config/cadence/projects.txt (one resolved CADENCE_DB_PATH per line, no
new file format) so `cadence overdue --all-projects` and `cadence sync
--all-projects` can open every registered store read-only with the
existing Store class, unmodified -- no new storage engine, no schema
change, no change to sync's merge/diff logic. This module only reads and
appends plain lines; every store it points at is still opened the exact
same way a single-project command opens its own.
"""
from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path
from typing import Optional

from cadence.store import AmbiguousProject, default_db_path


def config_home() -> Path:
    """Resolve the config directory, honoring CADENCE_CONFIG_HOME for
    tests/agents that want an isolated scratch config dir instead of the
    user's real one -- same override pattern as CADENCE_HOME in
    store.py's default_db_path."""
    home = Path(os.environ.get("CADENCE_CONFIG_HOME", Path.home() / ".config" / "cadence"))
    home.mkdir(parents=True, exist_ok=True)
    return home


def registry_path() -> Path:
    """The registry file itself: ~/.config/cadence/projects.txt (or
    $CADENCE_CONFIG_HOME/projects.txt)."""
    return config_home() / "projects.txt"


def read_projects_file(path: Path) -> list[str]:
    """Read a plain-text, one-path-per-line projects file, deduped in
    order and skipping blank lines -- resilient to a hand-edited file or
    trailing whitespace. Missing file reads as an empty list, not an
    error: an unregistered/fresh setup is a normal starting state, not a
    broken one."""
    path = Path(path)
    if not path.exists():
        return []
    seen: list[str] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and line not in seen:
            seen.append(line)
    return seen


def read_registry() -> list[str]:
    """This machine's own registered project store paths, in the order
    they were registered."""
    return read_projects_file(registry_path())


def register_project(db_path: Optional[Path] = None) -> tuple[str, bool]:
    """Append the resolved, absolute form of db_path (default: the
    current CADENCE_DB_PATH) to the registry, unless it's already there.

    Returns (resolved_path_str, already_registered) -- idempotent: running
    this twice in the same directory (same resolved CADENCE_DB_PATH)
    never duplicates the entry.

    Raises AmbiguousProject if db_path is None and CADENCE_DB_PATH is not
    set: falling through to default_db_path() here would register the one
    global default store regardless of the calling directory (0.2.12 Red
    Team finding #5) -- there is no per-cwd default store convention to
    fall back on, so this is refused rather than silently collapsing
    distinct project directories onto one registry entry.
    """
    if db_path is None and not os.environ.get("CADENCE_DB_PATH"):
        raise AmbiguousProject(
            "CADENCE_DB_PATH is not set, so there's no per-project store "
            "path to register",
            hint=(
                "Registering the single global default store "
                f"({default_db_path()}) here would silently merge with "
                "any other directory that also runs 'cadence register' "
                "without CADENCE_DB_PATH set. Set CADENCE_DB_PATH to a "
                "path inside this project first, e.g. "
                "'export CADENCE_DB_PATH=$PWD/cadence.db', then run "
                "'cadence register' again."
            ),
        )
    target = str(Path(db_path or default_db_path()).expanduser().resolve())
    existing = read_registry()
    if target in existing:
        return target, True
    path = registry_path()
    with open(path, "a") as f:
        f.write(target + "\n")
    return target, False


def http_token_path() -> Path:
    """Where the remote-MCP bearer token lives: ~/.config/cadence/mcp_http_token
    (or $CADENCE_CONFIG_HOME/mcp_http_token) -- same directory as projects.txt,
    one secret per line, nothing else."""
    return config_home() / "mcp_http_token"


def get_or_create_http_token() -> str:
    """The token `cadence mcp --http` requires of every remote request.

    Generated once, locally, on first use (32 random bytes, hex-encoded --
    unguessable, never derived from anything transmittable), then persisted
    at http_token_path() and reused on every later run so a client only has
    to be configured once. It is never sent anywhere by Cadence itself; the
    operator is the one who copies it into their own remote client's config.
    File is written with owner-only permissions (0600), matching the fact
    that reading it is equivalent to full read/write access to the task
    store over the network.
    """
    path = http_token_path()
    if path.exists():
        existing = path.read_text().strip()
        if existing:
            return existing
    token = secrets.token_hex(32)
    path.write_text(token + "\n")
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass  # best-effort on platforms without POSIX permission bits
    return token


def project_name(db_path_str: str) -> str:
    """A human-readable label for a registered store: the name of the
    directory holding its .db file (e.g. '/home/x/proj-alpha/cadence.db'
    -> 'proj-alpha'), falling back to the db file's own stem if the path
    has no useful parent directory name (e.g. a bare filename)."""
    p = Path(db_path_str)
    return p.parent.name or p.stem
