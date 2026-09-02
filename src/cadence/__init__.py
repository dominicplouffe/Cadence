"""Cadence: an agentic-first todo application.

Exposes a plain-text-friendly SQLite store, a human CLI, and an MCP server
that operate on the same data so an agent and a person never see a
different task list.
"""

from importlib.metadata import PackageNotFoundError as _PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    # Single source of truth: the version pip/PyPI actually installed,
    # taken from the built package's own metadata (which setuptools
    # derives from pyproject.toml's `[project].version` at build time).
    # A hardcoded string here is exactly how 0.2.4 through 0.2.16 drifted
    # silently out of sync with the real release (0.2.16 was published
    # while this file still said "0.2.4") -- reading it back from
    # metadata instead of restating it means it cannot drift again.
    __version__ = _pkg_version("cadence-todo")
except _PackageNotFoundError:
    # Bare checkout (pythonpath-only, not `pip install`-ed) has no
    # metadata to read -- e.g. CI's pytest collection step before the
    # editable install runs, or `pytest -q` on a fresh clone per this
    # repo's pyproject.toml convention.
    __version__ = "0+unknown"
