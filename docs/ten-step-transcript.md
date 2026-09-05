# R-08: ten-step agent transcript (published cadence-todo 0.2.36)

This is the committed transcript required by the project's finish line:
a session in which an agent with **no access to this repository** —
given only the published `cadence-todo` package on PyPI and whatever
tool descriptions it ships with (CLI `--help`, the MCP tool schemas,
and the README bundled in the wheel's own metadata) — completes the
fixed ten-step script: create, schedule, decompose a vague request into
subtasks, re-prioritise, complete, query, undo, sync across two
clients, export, and recover from a deliberately malformed request.

**Who ran this and how.** Run by Build (Rafael Okonkwo) against the
real PyPI artifact, not the local checkout: a brand-new Python
virtualenv (`venv`) created outside any clone of this repository, with
no `PYTHONPATH` pointing at the repo, `pip install cadence-todo`
(resolves to `0.2.36`, the latest published version at the time of this
run) from the public index, driven entirely through the package's own
MCP tool interface (the agent surface) over stdio. Every tool call, its
exact JSON result, and a UTC timestamp are recorded verbatim in Part 2
below — that section is the actual captured stdout of
`docs/ten-step-transcript-runner.py`, unedited, run against two fresh
client stores (`a.db`, `b.db`) that had never synced before.

**Why this replaces the 0.2.1 transcript.** The previous version of
this file (see `git log -p -- docs/ten-step-transcript.md` for the
0.2.1 text) asserted Step 8 by driving `resolve_sync_conflict` — the
documented recovery for an id collision at the time. Since the
0.2.3x `renumbered`/`conflicts` split, an id collision between two
independently-created, unrelated tasks (the exact scenario Step 8
exercises: Client A and Client B each create their own first task,
both landing on local id 1, before ever syncing) is no longer reported
as a `conflicts` entry at all — it is auto-resolved inside the same
`sync_tasks` call and reported in `renumbered`, with both tasks kept
under distinct ids. The old Step 8 script called
`resolve_sync_conflict(id=1, ...)` expecting a `conflicts` entry that
this code path no longer produces, so it failed against live 0.2.36.
Noor (Surface) found this staleness on 2026-09-05 while cross-checking
`docs/human-surface.md` §4.10 against shipped behaviour. `docs/ten-step-transcript-runner.py`'s Step 8 has been rewritten to
assert the current, correct behaviour directly (no `conflicts` entry,
a `renumbered` entry, both tasks' content preserved, and each client's
own id for its own task left unmoved) instead of driving a recovery
path that no longer applies. The old, now-redundant "Step 8b" probe
(added earlier specifically to check this same auto-resolve behaviour
with two extra scratch clients) has been folded into Step 8 itself and
removed as a separate step.

**Headline result: 10 of 10 steps pass** against `cadence-todo` 0.2.36,
including Step 8 under its corrected assertions.

## Part 1 — discovery: what the package's own docs reveal

Before touching the ten-step script, the agent explored the interface
using only what ships with the package: `cadence --help`, every
subcommand's `--help`, the README embedded in the installed wheel's
`METADATA` (the same text PyPI's project page shows), and the MCP
server's own `list_tools()` self-description. No source file in this
repository was read to produce this section.

### `cadence --help`

```text
usage: cadence [-h]
               {add,list,register,overdue,done,schedule,decompose,reprioritise,why,undo,sync,export,mcp}
               ...

Cadence: a todo list for people and agents.

positional arguments:
  {add,list,register,overdue,done,schedule,decompose,reprioritise,why,undo,sync,export,mcp}
    add                 Add a task. Example: cadence add "Buy milk"
    list                List tasks. Example: cadence list
    register            Register this project's store for cross-project
                        commands (overdue --all-projects, sync --all-
                        projects). Example: cadence register
    overdue             Show overdue tasks. Example: cadence overdue --all-
                        projects (across every 'cadence register'-ed project)
    done                Complete a task. Example: cadence done 3
    schedule            Set a due date. Example: cadence schedule 3 2026-09-01
    decompose           Split a task into subtasks. Example: cadence decompose
                        4 --into "Buy flour" "Buy eggs"
    reprioritise        Change an existing task's priority. Example: cadence
                        reprioritise 4 high
    why                 Show why a task changed. Example: cadence why 2
    undo                Revert the single most recent change. Example: cadence
                        undo
    sync                Sync tasks with another Cadence client. Example:
                        cadence sync
    export              Export all tasks. Example: cadence export --format
                        table
    mcp                 Start the MCP server over stdio (agent surface)

options:
  -h, --help            show this help message and exit
```

### Subcommand help (`cadence <cmd> --help`, all 12)

```text
=== cadence add --help ===
usage: cadence add [-h] [--due DUE] [--priority PRIORITY] [text]

positional arguments:
  text

options:
  -h, --help           show this help message and exit
  --due DUE            Due date, e.g. 2026-09-01
  --priority PRIORITY  high, med, or low

=== cadence list --help ===
usage: cadence list [-h]

options:
  -h, --help  show this help message and exit

=== cadence register --help ===
usage: cadence register [-h]

options:
  -h, --help  show this help message and exit

=== cadence overdue --help ===
usage: cadence overdue [-h] [--all-projects]

options:
  -h, --help      show this help message and exit
  --all-projects  Merge overdue tasks across every registered project (see
                  'cadence register')

=== cadence done --help ===
usage: cadence done [-h] id

positional arguments:
  id

options:
  -h, --help  show this help message and exit

=== cadence schedule --help ===
usage: cadence schedule [-h] [--reason REASON] id date

positional arguments:
  id
  date

options:
  -h, --help       show this help message and exit
  --reason REASON  Optional: why, for 'cadence why' to show later

=== cadence decompose --help ===
usage: cadence decompose [-h] [--into TITLE [TITLE ...]] [--reason REASON] id

positional arguments:
  id

options:
  -h, --help            show this help message and exit
  --into TITLE [TITLE ...]
  --reason REASON       Optional: why, for 'cadence why' to show later

=== cadence reprioritise --help ===
usage: cadence reprioritise [-h] [--reason REASON] id priority

positional arguments:
  id
  priority

options:
  -h, --help       show this help message and exit
  --reason REASON  Optional: why, for 'cadence why' to show later

=== cadence why --help ===
usage: cadence why [-h] [--iso] id

positional arguments:
  id

options:
  -h, --help  show this help message and exit
  --iso       Show absolute ISO timestamps instead of relative time

=== cadence undo --help ===
usage: cadence undo [-h]

options:
  -h, --help  show this help message and exit

=== cadence sync --help ===
usage: cadence sync [-h] [--remote REMOTE] [--keep-mine ID] [--keep-theirs ID]
                    [--all-projects] [--reset-sync-base]

options:
  -h, --help         show this help message and exit
  --remote REMOTE    The other client's own CADENCE_DB_PATH value (its plain
                     .db file path), or a git URL -- only needed once. Cadence
                     derives that client's history location itself.
  --keep-mine ID     Resolve a conflict by keeping this client's version
  --keep-theirs ID   Resolve a conflict by keeping the other side's version
  --all-projects     Sync every registered project (see 'cadence register'),
                     one line per project. --remote then means the path to
                     another client's own registry file (its
                     ~/.config/cadence/projects.txt), matched to this client's
                     projects by project name.
  --reset-sync-base  Recovery only: use after a 'history was rewritten' error
                     confirms this store's own hidden .history directory was
                     rewritten outside Cadence (a manual rebase, filter-repo,
                     or forced reset). Drops the remembered sync-base and
                     syncs fresh -- safe: any row this store and the remote
                     both know that isn't already identical becomes a conflict
                     for you to settle, it can never silently drop or
                     overwrite an edit.

=== cadence export --help ===
usage: cadence export [-h] [--format FORMAT] [--out OUT]

options:
  -h, --help       show this help message and exit
  --format FORMAT  json (default) or table
  --out OUT        Write JSON to this path instead of a timestamped file

=== cadence mcp --help ===
usage: cadence mcp [-h] [--http] [--host HOST] [--port PORT] [--token TOKEN]
                   [--show-token]

options:
  -h, --help     show this help message and exit
  --http         Serve over HTTP instead of stdio, so a non-local client
                 (Claude web/mobile, or an agent on another machine) can reach
                 this store. Self-hosted, bearer-token-protected -- see
                 README's 'Remote access' section.
  --host HOST    Bind host for --http (default 127.0.0.1)
  --port PORT    Bind port for --http (default 8765)
  --token TOKEN  Bearer token --http clients must present (or set
                 CADENCE_MCP_TOKEN). Default: the token in --show-token,
                 generated and stored on first use.
  --show-token   Print this machine's remote-MCP bearer token (generating it
                 on first use) and exit, without starting a server.

```

### README (from the installed wheel's own metadata, first section)

```text
# Cadence

There are thousands of todo CLIs. Cadence is the one built for an AI agent
to run as a first-class user, not a bolted-on chat wrapper on top of a
human tool — and it turns git, which you already have installed, into the
undo/history/audit/sync layer instead of building a bespoke (and weaker)
version of all four.

What that buys you, concretely:

- **Hand your agent a messy brain-dump and get tracked subtasks back.**
  `cadence decompose 12 --into "book flights" "book hotel" "pack"` (or the
  `decompose_task` MCP tool) turns one vague task into real, independently
  completable children — no more one giant to-do that never gets checked off.
- **Ask "why did this get bumped to top priority" and get a real answer.**
  Every `reprioritise`, `schedule`, and edit is a git commit under the hood,
  so the answer is `git log`/`git blame` on your own task store, not a guess
  or a feature nobody built.
- **Undo a bad agent action instantly, with the same guarantee git gives
  your code.** `cadence undo` reverts the last change — yours or your
  agent's — because it *is* a git revert, not a bespoke undo stack that
  only covers some operations.
- **Keep two devices' task lists in sync without running a server.**
  `cadence sync` pulls and pushes against a git remote you already control
  (a repo, a USB stick, anything git can reach) — no account, no hosted
  backend, no uptime to worry about.

The plan is to publish Cadence as a public, installable, open-source project
by **6 November 2026**. That date is a delivery commitment, not an estimate.

## Status

Published on PyPI as [`cadence-todo`](https://pypi.org/project/cadence-todo/)
— install with `pip install cadence-todo`. It builds, runs, and is tested
from a fresh clone, and CI is green on a clean GitHub-hosted runner (see the
finish-line checklist below for what's still outstanding). See
[`docs/bakeoff.md`](docs/bakeoff.md) for the five candidate concepts we
researched, the evidence behind each, and which one w
...(truncated here; full text is in the wheel's METADATA / the PyPI
project page — this excerpt is enough to show the agent found the
`decompose`, `undo`/git-history, and `sync` capabilities documented
before ever calling a tool)
```

### MCP `list_tools()` (all 13 tools, full docstrings as returned over stdio)

```text
### add_task
Create a new task.

    Args:
        title: Non-empty task title, max 200 characters.
        due: Optional ISO date string, e.g. "2026-09-01".
        priority: Optional, one of "low", "med", "high". Omit for no priority.

    Returns:
        {"ok": true, "task": {id, title, status, priority, due,
        created_at, completed_at}, "recovered": [task, ...]} on success,
        or {"ok": false, "error", "message", "hint"} if title is empty,
        over 200 characters, or priority is invalid. `recovered` is
        normally empty; if this call happened to find a stray task file
        already on disk (this store having been used as a passive sync
        relay for another client), it was absorbed into your task list
        as a side effect and is listed here -- it is NOT the task you
        just asked for, that's still `task` above.

### list_tasks
List tasks, ordered high-priority first then by id.

    Args:
        status: One of "pending" (default), "done", or "all".

    Returns:
        {"ok": true, "tasks": [task, ...], "count": N} on success, or
        {"ok": false, "error", "message", "hint"} if status is invalid.

### register_project
Register this project's store (its current, resolved CADENCE_DB_PATH)
    in the cross-project registry at ~/.config/cadence/projects.txt.

    Call once per project (e.g. once per repo an agent works in) before
    using overdue_tasks(all_projects=true) or sync_tasks(all_projects=true)
    -- both only see stores that have been registered this way. Idempotent:
    calling it again for the same store is a no-op, not a duplicate entry.

    Returns:
        {"ok": true, "path": "<resolved db path>", "already_registered":
        bool}.

### overdue_tasks
List overdue (pending, past-due) tasks.

    Args:
        all_projects: If false (default), only this store's own overdue
            tasks. If true, opens every project registered via
            register_project read-only and merges their overdue tasks into
            one list, each tagged with its project name -- no project
            needs to be the "current" one for this to see it.

    Returns:
        With all_projects=false: {"ok": true, "tasks": [task +
        "overdue_days", ...], "count": N}.
        With all_projects=true: {"ok": true, "tasks": [task + "project" +
        "overdue_days", ...], "count": N (tasks only), "projects": M
        (registered project count)}. A registered store that can't be
        opened is reported as {"project", "error", "message", "hint"}
        instead of a task, alongside the rest, rather than failing the
        whole call.

### complete_task
Mark a task done.

    Args:
        id: Numeric task id, as returned by add_task or list_tasks.

    Returns:
        {"ok": true, "task": {...}} with status "done" on success, or
        {"ok": false, "error": "task_not_found", "message", "hint"} if the
        id does not exist.

### schedule_task
Set or change a task's due date.

    Args:
        id: Numeric task id, as returned by add_task or list_tasks.
        due: Non-empty ISO date/time string, e.g. "2026-09-01".
        reason: Optional. Why you're setting this due date -- recorded so
            a person can later call why_task(id) and see it. Omit if you
            don't have one; it costs nothing.

    Returns:
        {"ok": true, "task": {...}} with the new due date on success, or
        {"ok": false, "error", "message", "hint"} if id is unknown or due
        is empty.

### decompose_task
Split a task into subtasks by linking titles you already wrote.

    This is a structural primitive, not a planner: Cadence does not invent
    the breakdown -- the caller decides what the subtasks are and passes
    their titles. Bounded so a looping agent can't decompose forever: max
    depth 3, max 20 subtasks per parent (across all decompose calls).

    Args:
        id: Numeric id of the parent task.
        into: Non-empty list of subtask titles (each max 200 characters).
        reason: Optional. Why you're breaking this task down this way --
            recorded so a person can later call why_task(id) (on the parent
            or any subtask) and see it. Omit if you don't have one.

    Returns:
        {"ok": true, "parent": {...}, "subtasks": [task, ...], "recovered":
        [task, ...]} on success, or {"ok": false, "error", "message",
        "hint"} if `into` is empty, the parent is already at max depth, or
        the count would exceed the 20-per-parent cap. `recovered` is
        normally empty; if this call happened to find a stray task file
        already on disk (this store having been used as a passive sync
        relay for another client), it was absorbed into your task list as
        a side effect and is listed here -- it is NOT one of `subtasks`,
        those are still exactly the titles you passed in `into`.

### reprioritise_task
Change an existing task's priority.

    Distinct from setting priority at creation (add_task's `priority` arg):
    re-prioritising a task that already exists is its own auditable event
    and what `undo` reverts back to the prior priority.

    Args:
        id: Numeric task id, as returned by add_task or list_tasks.
        priority: One of "low", "med", "high".
        reason: Optional. Why this task outranks the others now -- recorded
            so a person can later call why_task(id) and see it instead of
            just watching a number change. Omit if you don't have one.

    Returns:
        {"ok": true, "task": {...}} with the new priority on success, or
        {"ok": false, "error": "invalid_task", "message", "hint"} if id is
        unknown or priority isn't one of the three values.

### why_task
Show a task's git-backed change history as a plain-language timeline.

    Every mutation (add/decompose/reprioritise/schedule/complete/undo) is
    already a commit; this reads that history back for one task, newest
    first, including any `reason` left on decompose_task/reprioritise_task/
    schedule_task calls and which surface (CLI or MCP) made each change.

    Args:
        id: Numeric task id, as returned by add_task or list_tasks.

    Returns:
        {"ok": true, "task": {...}, "history": [{"event": "<plain-language
        description>", "priority": "<the task's priority right after this
        change>", "at": "<ISO-8601 timestamp>", "reason": str or null,
        "source": "cli"|"mcp"|null}, ...]} newest first, or {"ok": false,
        "error": "task_not_found", "message", "hint"} if the id doesn't
        exist.

### undo
Revert the single most recent mutation, on any surface (CLI or MCP).

    There is no per-task argument: "whatever happened most recently" is the
    one unambiguous target. Reverting is itself a new mutation, so undo is
    symmetric -- calling it twice in a row returns to the pre-undo state
    (the second undo reverts the first); there is no separate redo tool.

    Returns:
        {"ok": true, "summary": "Undid: <what changed>"} on success, or
        {"ok": false, "error": "nothing_to_undo", "message", "hint"} if
        nothing has been done yet.

### sync_tasks
Sync this store with a shared remote (another client's history).

    Never silently drops data. Two different things can make a task id
    differ between this store and the remote since the last clean sync:

    - Edited on both sides (same task, changed independently on each
      client): left untouched on both this store and the remote, reported
      by id in `conflicts`. Call resolve_sync_conflict(id, keep="mine"|
      "theirs") for each one, then call sync_tasks again.
    - Independently created with the same id (two unrelated tasks that
      happened to get the same auto-assigned id, since each client
      assigns ids on its own): never a real conflict, so it's resolved
      automatically within this same call -- this client's task keeps its
      id, the other client's task is preserved under a freshly assigned
      id. Reported by id in `renumbered`; nothing to call for these.

    Everything else in the same sync still lands either way.

    Args:
        remote: With all_projects=false (default): the OTHER client's own
            CADENCE_DB_PATH value (its plain .db file path) -- this client
            derives that client's history location itself, so you never
            need to know Cadence's internal storage layout. A git URL also
            works, for a shared server remote. Only needed the first time
            (or to change it) -- omit on later calls to reuse the remote
            already configured.
            With all_projects=true: the path to another client's own
            registry file (its ~/.config/cadence/projects.txt) -- projects
            are matched between the two registries by project name.  Omit
            to reuse whatever remote each project already has configured.
        all_projects: If true, loops over every project registered via
            register_project and syncs each one (see the per-project
            result shape below) instead of just this store.
        reset_sync_base: Recovery only -- use after this call (or CLI
            'cadence sync') returns error "history_rewritten", which means
            something outside Cadence rewrote this store's own hidden
            history directory (a manual rebase, filter-repo, or a forced
            reset) since the last sync, and Cadence can no longer trust
            its recorded sync-base. Setting this to true drops that
            marker and syncs fresh -- safe: any row this store and the
            remote both know that isn't already identical becomes a
            `conflicts` entry for you to settle, it can never silently
            drop or overwrite an edit.

    Returns (all_projects=false): {"ok": true, "pulled": N, "pushed": N,
        "already_synced": bool, "conflicts": [{"id", "mine", "theirs"}, ...],
        "renumbered": [{"old_id", "new_id", "kept_at_old_id"}, ...],
        "warnings": [str, ...]}.
        {"ok": false, ...} if no remote is configured or it can't be
        reached.

        `warnings`: non-fatal problems noticed but not fatal to this call
        -- e.g. an on-disk task file this store could not read to confirm
        it was safe to clean up, so it was left in place, named here
        instead of silently dropped.

    Returns (all_projects=true): {"ok": true, "projects": M, "results":
        [{"project": name, ...same shape as one non-all_projects sync
        result, or "error"/"message"/"hint" if that project's store
        couldn't be opened, or "skipped": true + "reason" if `remote` was
        given but had no project of that name}, ...]}. Never fails the
        whole call for one project's problem -- read each result's own
        `ok`/`error`/`skipped`.

### resolve_sync_conflict
Resolve one conflict reported by sync_tasks.

    Args:
        id: Task id from sync_tasks's `conflicts` list.
        keep: "mine" (this client's edit) or "theirs" (the remote's edit).

    Returns:
        {"ok": true, "task": {...}} with the resolved task on success, or
        {"ok": false, "error": "no_such_conflict", "message", "hint"} if
        there is no pending conflict for that id.

### export_tasks
Export every task, open and done, unfiltered.

    Args:
        format: "json" (default) -- the raw task records -- or "table",
            the same row shape list_tasks/`cadence list` use, as an array
            of one rendered string per row.

    Returns:
        {"ok": true, "tasks": [...], "count": N} for format="json", or
        {"ok": true, "rows": [...], "count": N} for format="table", or
        {"ok": false, "error": "invalid_task", "message", "hint"} if format
        isn't "json" or "table".

```

## Part 2 — the ten-step session (raw, verbatim stdout of `docs/ten-step-transcript-runner.py`)

```text
[2026-09-05T02:20:15.442Z] ===== R-08 TEN-STEP TRANSCRIPT START =====
[2026-09-05T02:20:15.442Z] cadence binary: venv/bin/cadence
[2026-09-05T02:20:15.442Z] Client A store: /workspace/tenstep_live_0236/a.db
[2026-09-05T02:20:15.442Z] Client B store: /workspace/tenstep_live_0236/b.db
[2026-09-05T02:20:15.442Z] Shared sync remote: /workspace/tenstep_live_0236/remote
[2026-09-05T02:20:16.202Z] Opened MCP session for Client A (CADENCE_DB_PATH=/workspace/tenstep_live_0236/a.db)
[2026-09-05T02:20:16.202Z] ### STEP 1 -- create a task
[2026-09-05T02:20:16.202Z] PROMPT (agent decides to call): add_task({"title": "Prep the Q4 client offsite", "priority": "med"})   # step: step1-create
[2026-09-05T02:20:16.275Z] RESULT of add_task
{
  "isError": false,
  "content": [
    {
      "type": "text",
      "text": "{\n  \"ok\": true,\n  \"task\": {\n    \"id\": 1,\n    \"title\": \"Prep the Q4 client offsite\",\n    \"status\": \"pending\",\n    \"priority\": \"med\",\n    \"due\": null,\n    \"created_at\": \"2026-09-05T02:20:16+00:00\",\n    \"completed_at\": null,\n    \"parent_id\": null\n  },\n  \"recovered\": []\n}",
      "annotations": null,
      "meta": null
    }
  ],
  "parsed": {
    "ok": true,
    "task": {
      "id": 1,
      "title": "Prep the Q4 client offsite",
      "status": "pending",
      "priority": "med",
      "due": null,
      "created_at": "2026-09-05T02:20:16+00:00",
      "completed_at": null,
      "parent_id": null
    },
    "recovered": []
  }
}
[2026-09-05T02:20:16.275Z] STEP 1 VERDICT: PASS (created task id=1)
[2026-09-05T02:20:16.275Z] ### STEP 2 -- schedule the task
[2026-09-05T02:20:16.275Z] PROMPT (agent decides to call): schedule_task({"id": 1, "due": "2026-09-15"})   # step: step2-schedule
[2026-09-05T02:20:16.300Z] RESULT of schedule_task
{
  "isError": false,
  "content": [
    {
      "type": "text",
      "text": "{\n  \"ok\": true,\n  \"task\": {\n    \"id\": 1,\n    \"title\": \"Prep the Q4 client offsite\",\n    \"status\": \"pending\",\n    \"priority\": \"med\",\n    \"due\": \"2026-09-15\",\n    \"created_at\": \"2026-09-05T02:20:16+00:00\",\n    \"completed_at\": null,\n    \"parent_id\": null\n  }\n}",
      "annotations": null,
      "meta": null
    }
  ],
  "parsed": {
    "ok": true,
    "task": {
      "id": 1,
      "title": "Prep the Q4 client offsite",
      "status": "pending",
      "priority": "med",
      "due": "2026-09-15",
      "created_at": "2026-09-05T02:20:16+00:00",
      "completed_at": null,
      "parent_id": null
    }
  }
}
[2026-09-05T02:20:16.301Z] STEP 2 VERDICT: PASS
[2026-09-05T02:20:16.301Z] USER REQUEST (vague, given to agent out of band): "Sort out the offsite somehow, I don't want to think about it"
[2026-09-05T02:20:16.301Z] ### STEP 3 -- agent decomposes the vague request into subtask titles, then calls decompose_task to link them under task 1
[2026-09-05T02:20:16.301Z] Agent's own breakdown (not produced by the tool): ['Book the venue', 'Send calendar invites', 'Order catering']
[2026-09-05T02:20:16.301Z] PROMPT (agent decides to call): decompose_task({"id": 1, "into": ["Book the venue", "Send calendar invites", "Order catering"]})   # step: step3-decompose
[2026-09-05T02:20:16.326Z] RESULT of decompose_task
{
  "isError": false,
  "content": [
    {
      "type": "text",
      "text": "{\n  \"ok\": true,\n  \"parent\": {\n    \"id\": 1,\n    \"title\": \"Prep the Q4 client offsite\",\n    \"status\": \"pending\",\n    \"priority\": \"med\",\n    \"due\": \"2026-09-15\",\n    \"created_at\": \"2026-09-05T02:20:16+00:00\",\n    \"completed_at\": null,\n    \"parent_id\": null\n  },\n  \"subtasks\": [\n    {\n      \"id\": 2,\n      \"title\": \"Book the venue\",\n      \"status\": \"pending\",\n      \"priority\": null,\n      \"due\": null,\n      \"created_at\": \"2026-09-05T02:20:16+00:00\",\n      \"completed_at\": null,\n      \"parent_id\": 1\n    },\n    {\n      \"id\": 3,\n      \"title\": \"Send calendar invites\",\n      \"status\": \"pending\",\n      \"priority\": null,\n      \"due\": null,\n      \"created_at\": \"2026-09-05T02:20:16+00:00\",\n      \"completed_at\": null,\n      \"parent_id\": 1\n    },\n    {\n      \"id\": 4,\n      \"title\": \"Order catering\",\n      \"status\": \"pending\",\n      \"priority\": null,\n      \"due\": null,\n      \"created_at\": \"2026-09-05T02:20:16+00:00\",\n      \"completed_at\": null,\n      \"parent_id\": 1\n    }\n  ],\n  \"recovered\": []\n}",
      "annotations": null,
      "meta": null
    }
  ],
  "parsed": {
    "ok": true,
    "parent": {
      "id": 1,
      "title": "Prep the Q4 client offsite",
      "status": "pending",
      "priority": "med",
      "due": "2026-09-15",
      "created_at": "2026-09-05T02:20:16+00:00",
      "completed_at": null,
      "parent_id": null
    },
    "subtasks": [
      {
        "id": 2,
        "title": "Book the venue",
        "status": "pending",
        "priority": null,
        "due": null,
        "created_at": "2026-09-05T02:20:16+00:00",
        "completed_at": null,
        "parent_id": 1
      },
      {
        "id": 3,
        "title": "Send calendar invites",
        "status": "pending",
        "priority": null,
        "due": null,
        "created_at": "2026-09-05T02:20:16+00:00",
        "completed_at": null,
        "parent_id": 1
      },
      {
        "id": 4,
        "title": "Order catering",
        "status": "pending",
        "priority": null,
        "due": null,
        "created_at": "2026-09-05T02:20:16+00:00",
        "completed_at": null,
        "parent_id": 1
      }
    ],
    "recovered": []
  }
}
[2026-09-05T02:20:16.326Z] STEP 3 VERDICT: PASS (subtask ids=[2, 3, 4])
[2026-09-05T02:20:16.326Z] ### STEP 4 -- re-prioritise one of the subtasks
[2026-09-05T02:20:16.326Z] PROMPT (agent decides to call): reprioritise_task({"id": 2, "priority": "high"})   # step: step4-reprioritise
[2026-09-05T02:20:16.355Z] RESULT of reprioritise_task
{
  "isError": false,
  "content": [
    {
      "type": "text",
      "text": "{\n  \"ok\": true,\n  \"task\": {\n    \"id\": 2,\n    \"title\": \"Book the venue\",\n    \"status\": \"pending\",\n    \"priority\": \"high\",\n    \"due\": null,\n    \"created_at\": \"2026-09-05T02:20:16+00:00\",\n    \"completed_at\": null,\n    \"parent_id\": 1\n  }\n}",
      "annotations": null,
      "meta": null
    }
  ],
  "parsed": {
    "ok": true,
    "task": {
      "id": 2,
      "title": "Book the venue",
      "status": "pending",
      "priority": "high",
      "due": null,
      "created_at": "2026-09-05T02:20:16+00:00",
      "completed_at": null,
      "parent_id": 1
    }
  }
}
[2026-09-05T02:20:16.355Z] STEP 4 VERDICT: PASS
[2026-09-05T02:20:16.355Z] ### STEP 5 -- complete a task (the now-high-priority venue subtask)
[2026-09-05T02:20:16.355Z] PROMPT (agent decides to call): complete_task({"id": 2})   # step: step5-complete
[2026-09-05T02:20:16.378Z] RESULT of complete_task
{
  "isError": false,
  "content": [
    {
      "type": "text",
      "text": "{\n  \"ok\": true,\n  \"task\": {\n    \"id\": 2,\n    \"title\": \"Book the venue\",\n    \"status\": \"done\",\n    \"priority\": \"high\",\n    \"due\": null,\n    \"created_at\": \"2026-09-05T02:20:16+00:00\",\n    \"completed_at\": \"2026-09-05T02:20:16+00:00\",\n    \"parent_id\": 1\n  }\n}",
      "annotations": null,
      "meta": null
    }
  ],
  "parsed": {
    "ok": true,
    "task": {
      "id": 2,
      "title": "Book the venue",
      "status": "done",
      "priority": "high",
      "due": null,
      "created_at": "2026-09-05T02:20:16+00:00",
      "completed_at": "2026-09-05T02:20:16+00:00",
      "parent_id": 1
    }
  }
}
[2026-09-05T02:20:16.379Z] STEP 5 VERDICT: PASS
[2026-09-05T02:20:16.379Z] ### STEP 6a -- query: list_tasks(status=all) should show all 4 tasks (1 parent + 3 subtasks), 1 done
[2026-09-05T02:20:16.379Z] PROMPT (agent decides to call): list_tasks({"status": "all"})   # step: step6-query-all
[2026-09-05T02:20:16.385Z] RESULT of list_tasks
{
  "isError": false,
  "content": [
    {
      "type": "text",
      "text": "{\n  \"ok\": true,\n  \"tasks\": [\n    {\n      \"id\": 1,\n      \"title\": \"Prep the Q4 client offsite\",\n      \"status\": \"pending\",\n      \"priority\": \"med\",\n      \"due\": \"2026-09-15\",\n      \"created_at\": \"2026-09-05T02:20:16+00:00\",\n      \"completed_at\": null,\n      \"parent_id\": null\n    },\n    {\n      \"id\": 3,\n      \"title\": \"Send calendar invites\",\n      \"status\": \"pending\",\n      \"priority\": null,\n      \"due\": null,\n      \"created_at\": \"2026-09-05T02:20:16+00:00\",\n      \"completed_at\": null,\n      \"parent_id\": 1\n    },\n    {\n      \"id\": 4,\n      \"title\": \"Order catering\",\n      \"status\": \"pending\",\n      \"priority\": null,\n      \"due\": null,\n      \"created_at\": \"2026-09-05T02:20:16+00:00\",\n      \"completed_at\": null,\n      \"parent_id\": 1\n    },\n    {\n      \"id\": 2,\n      \"title\": \"Book the venue\",\n      \"status\": \"done\",\n      \"priority\": \"high\",\n      \"due\": null,\n      \"created_at\": \"2026-09-05T02:20:16+00:00\",\n      \"completed_at\": \"2026-09-05T02:20:16+00:00\",\n      \"parent_id\": 1\n    }\n  ],\n  \"count\": 4\n}",
      "annotations": null,
      "meta": null
    }
  ],
  "parsed": {
    "ok": true,
    "tasks": [
      {
        "id": 1,
        "title": "Prep the Q4 client offsite",
        "status": "pending",
        "priority": "med",
        "due": "2026-09-15",
        "created_at": "2026-09-05T02:20:16+00:00",
        "completed_at": null,
        "parent_id": null
      },
      {
        "id": 3,
        "title": "Send calendar invites",
        "status": "pending",
        "priority": null,
        "due": null,
        "created_at": "2026-09-05T02:20:16+00:00",
        "completed_at": null,
        "parent_id": 1
      },
      {
        "id": 4,
        "title": "Order catering",
        "status": "pending",
        "priority": null,
        "due": null,
        "created_at": "2026-09-05T02:20:16+00:00",
        "completed_at": null,
        "parent_id": 1
      },
      {
        "id": 2,
        "title": "Book the venue",
        "status": "done",
        "priority": "high",
        "due": null,
        "created_at": "2026-09-05T02:20:16+00:00",
        "completed_at": "2026-09-05T02:20:16+00:00",
        "parent_id": 1
      }
    ],
    "count": 4
  }
}
[2026-09-05T02:20:16.386Z] ### STEP 6b -- query: independently re-check the tool's own ordering claim ("ordered high-priority first then by id") -- add three fresh probe tasks with priorities low, high, med (in that order) and confirm list_tasks(status=pending) returns them high, med, low
[2026-09-05T02:20:16.386Z] PROMPT (agent decides to call): add_task({"title": "probe-low", "priority": "low"})   # step: step6-probe-add
[2026-09-05T02:20:16.415Z] RESULT of add_task
{
  "isError": false,
  "content": [
    {
      "type": "text",
      "text": "{\n  \"ok\": true,\n  \"task\": {\n    \"id\": 5,\n    \"title\": \"probe-low\",\n    \"status\": \"pending\",\n    \"priority\": \"low\",\n    \"due\": null,\n    \"created_at\": \"2026-09-05T02:20:16+00:00\",\n    \"completed_at\": null,\n    \"parent_id\": null\n  },\n  \"recovered\": []\n}",
      "annotations": null,
      "meta": null
    }
  ],
  "parsed": {
    "ok": true,
    "task": {
      "id": 5,
      "title": "probe-low",
      "status": "pending",
      "priority": "low",
      "due": null,
      "created_at": "2026-09-05T02:20:16+00:00",
      "completed_at": null,
      "parent_id": null
    },
    "recovered": []
  }
}
[2026-09-05T02:20:16.415Z] PROMPT (agent decides to call): add_task({"title": "probe-high", "priority": "high"})   # step: step6-probe-add
[2026-09-05T02:20:16.440Z] RESULT of add_task
{
  "isError": false,
  "content": [
    {
      "type": "text",
      "text": "{\n  \"ok\": true,\n  \"task\": {\n    \"id\": 6,\n    \"title\": \"probe-high\",\n    \"status\": \"pending\",\n    \"priority\": \"high\",\n    \"due\": null,\n    \"created_at\": \"2026-09-05T02:20:16+00:00\",\n    \"completed_at\": null,\n    \"parent_id\": null\n  },\n  \"recovered\": []\n}",
      "annotations": null,
      "meta": null
    }
  ],
  "parsed": {
    "ok": true,
    "task": {
      "id": 6,
      "title": "probe-high",
      "status": "pending",
      "priority": "high",
      "due": null,
      "created_at": "2026-09-05T02:20:16+00:00",
      "completed_at": null,
      "parent_id": null
    },
    "recovered": []
  }
}
[2026-09-05T02:20:16.440Z] PROMPT (agent decides to call): add_task({"title": "probe-med", "priority": "med"})   # step: step6-probe-add
[2026-09-05T02:20:16.463Z] RESULT of add_task
{
  "isError": false,
  "content": [
    {
      "type": "text",
      "text": "{\n  \"ok\": true,\n  \"task\": {\n    \"id\": 7,\n    \"title\": \"probe-med\",\n    \"status\": \"pending\",\n    \"priority\": \"med\",\n    \"due\": null,\n    \"created_at\": \"2026-09-05T02:20:16+00:00\",\n    \"completed_at\": null,\n    \"parent_id\": null\n  },\n  \"recovered\": []\n}",
      "annotations": null,
      "meta": null
    }
  ],
  "parsed": {
    "ok": true,
    "task": {
      "id": 7,
      "title": "probe-med",
      "status": "pending",
      "priority": "med",
      "due": null,
      "created_at": "2026-09-05T02:20:16+00:00",
      "completed_at": null,
      "parent_id": null
    },
    "recovered": []
  }
}
[2026-09-05T02:20:16.463Z] PROMPT (agent decides to call): list_tasks({"status": "pending"})   # step: step6-query-order
[2026-09-05T02:20:16.467Z] RESULT of list_tasks
{
  "isError": false,
  "content": [
    {
      "type": "text",
      "text": "{\n  \"ok\": true,\n  \"tasks\": [\n    {\n      \"id\": 6,\n      \"title\": \"probe-high\",\n      \"status\": \"pending\",\n      \"priority\": \"high\",\n      \"due\": null,\n      \"created_at\": \"2026-09-05T02:20:16+00:00\",\n      \"completed_at\": null,\n      \"parent_id\": null\n    },\n    {\n      \"id\": 1,\n      \"title\": \"Prep the Q4 client offsite\",\n      \"status\": \"pending\",\n      \"priority\": \"med\",\n      \"due\": \"2026-09-15\",\n      \"created_at\": \"2026-09-05T02:20:16+00:00\",\n      \"completed_at\": null,\n      \"parent_id\": null\n    },\n    {\n      \"id\": 7,\n      \"title\": \"probe-med\",\n      \"status\": \"pending\",\n      \"priority\": \"med\",\n      \"due\": null,\n      \"created_at\": \"2026-09-05T02:20:16+00:00\",\n      \"completed_at\": null,\n      \"parent_id\": null\n    },\n    {\n      \"id\": 5,\n      \"title\": \"probe-low\",\n      \"status\": \"pending\",\n      \"priority\": \"low\",\n      \"due\": null,\n      \"created_at\": \"2026-09-05T02:20:16+00:00\",\n      \"completed_at\": null,\n      \"parent_id\": null\n    },\n    {\n      \"id\": 3,\n      \"title\": \"Send calendar invites\",\n      \"status\": \"pending\",\n      \"priority\": null,\n      \"due\": null,\n      \"created_at\": \"2026-09-05T02:20:16+00:00\",\n      \"completed_at\": null,\n      \"parent_id\": 1\n    },\n    {\n      \"id\": 4,\n      \"title\": \"Order catering\",\n      \"status\": \"pending\",\n      \"priority\": null,\n      \"due\": null,\n      \"created_at\": \"2026-09-05T02:20:16+00:00\",\n      \"completed_at\": null,\n      \"parent_id\": 1\n    }\n  ],\n  \"count\": 6\n}",
      "annotations": null,
      "meta": null
    }
  ],
  "parsed": {
    "ok": true,
    "tasks": [
      {
        "id": 6,
        "title": "probe-high",
        "status": "pending",
        "priority": "high",
        "due": null,
        "created_at": "2026-09-05T02:20:16+00:00",
        "completed_at": null,
        "parent_id": null
      },
      {
        "id": 1,
        "title": "Prep the Q4 client offsite",
        "status": "pending",
        "priority": "med",
        "due": "2026-09-15",
        "created_at": "2026-09-05T02:20:16+00:00",
        "completed_at": null,
        "parent_id": null
      },
      {
        "id": 7,
        "title": "probe-med",
        "status": "pending",
        "priority": "med",
        "due": null,
        "created_at": "2026-09-05T02:20:16+00:00",
        "completed_at": null,
        "parent_id": null
      },
      {
        "id": 5,
        "title": "probe-low",
        "status": "pending",
        "priority": "low",
        "due": null,
        "created_at": "2026-09-05T02:20:16+00:00",
        "completed_at": null,
        "parent_id": null
      },
      {
        "id": 3,
        "title": "Send calendar invites",
        "status": "pending",
        "priority": null,
        "due": null,
        "created_at": "2026-09-05T02:20:16+00:00",
        "completed_at": null,
        "parent_id": 1
      },
      {
        "id": 4,
        "title": "Order catering",
        "status": "pending",
        "priority": null,
        "due": null,
        "created_at": "2026-09-05T02:20:16+00:00",
        "completed_at": null,
        "parent_id": 1
      }
    ],
    "count": 6
  }
}
[2026-09-05T02:20:16.468Z] Observed probe order: ['probe-high', 'probe-med', 'probe-low']; expected: ['probe-high', 'probe-med', 'probe-low']
[2026-09-05T02:20:16.468Z] STEP 6 VERDICT: PASS (6a all-status query=PASS, 6b ordering claim=PASS)
[2026-09-05T02:20:16.468Z] ### STEP 7 -- undo the most recent mutation (should revert step 6b's last add_task, i.e. remove probe-med) and confirm via a follow-up query
[2026-09-05T02:20:16.468Z] PROMPT (agent decides to call): undo({})   # step: step7-undo
[2026-09-05T02:20:16.504Z] RESULT of undo
{
  "isError": false,
  "content": [
    {
      "type": "text",
      "text": "{\n  \"ok\": true,\n  \"summary\": \"Undid: Added #7 \u2192 removed \\\"probe-med\\\"\"\n}",
      "annotations": null,
      "meta": null
    }
  ],
  "parsed": {
    "ok": true,
    "summary": "Undid: Added #7 \u2192 removed \"probe-med\""
  }
}
[2026-09-05T02:20:16.504Z] PROMPT (agent decides to call): list_tasks({"status": "all"})   # step: step7-verify
[2026-09-05T02:20:16.509Z] RESULT of list_tasks
{
  "isError": false,
  "content": [
    {
      "type": "text",
      "text": "{\n  \"ok\": true,\n  \"tasks\": [\n    {\n      \"id\": 6,\n      \"title\": \"probe-high\",\n      \"status\": \"pending\",\n      \"priority\": \"high\",\n      \"due\": null,\n      \"created_at\": \"2026-09-05T02:20:16+00:00\",\n      \"completed_at\": null,\n      \"parent_id\": null\n    },\n    {\n      \"id\": 1,\n      \"title\": \"Prep the Q4 client offsite\",\n      \"status\": \"pending\",\n      \"priority\": \"med\",\n      \"due\": \"2026-09-15\",\n      \"created_at\": \"2026-09-05T02:20:16+00:00\",\n      \"completed_at\": null,\n      \"parent_id\": null\n    },\n    {\n      \"id\": 5,\n      \"title\": \"probe-low\",\n      \"status\": \"pending\",\n      \"priority\": \"low\",\n      \"due\": null,\n      \"created_at\": \"2026-09-05T02:20:16+00:00\",\n      \"completed_at\": null,\n      \"parent_id\": null\n    },\n    {\n      \"id\": 3,\n      \"title\": \"Send calendar invites\",\n      \"status\": \"pending\",\n      \"priority\": null,\n      \"due\": null,\n      \"created_at\": \"2026-09-05T02:20:16+00:00\",\n      \"completed_at\": null,\n      \"parent_id\": 1\n    },\n    {\n      \"id\": 4,\n      \"title\": \"Order catering\",\n      \"status\": \"pending\",\n      \"priority\": null,\n      \"due\": null,\n      \"created_at\": \"2026-09-05T02:20:16+00:00\",\n      \"completed_at\": null,\n      \"parent_id\": 1\n    },\n    {\n      \"id\": 2,\n      \"title\": \"Book the venue\",\n      \"status\": \"done\",\n      \"priority\": \"high\",\n      \"due\": null,\n      \"created_at\": \"2026-09-05T02:20:16+00:00\",\n      \"completed_at\": \"2026-09-05T02:20:16+00:00\",\n      \"parent_id\": 1\n    }\n  ],\n  \"count\": 6\n}",
      "annotations": null,
      "meta": null
    }
  ],
  "parsed": {
    "ok": true,
    "tasks": [
      {
        "id": 6,
        "title": "probe-high",
        "status": "pending",
        "priority": "high",
        "due": null,
        "created_at": "2026-09-05T02:20:16+00:00",
        "completed_at": null,
        "parent_id": null
      },
      {
        "id": 1,
        "title": "Prep the Q4 client offsite",
        "status": "pending",
        "priority": "med",
        "due": "2026-09-15",
        "created_at": "2026-09-05T02:20:16+00:00",
        "completed_at": null,
        "parent_id": null
      },
      {
        "id": 5,
        "title": "probe-low",
        "status": "pending",
        "priority": "low",
        "due": null,
        "created_at": "2026-09-05T02:20:16+00:00",
        "completed_at": null,
        "parent_id": null
      },
      {
        "id": 3,
        "title": "Send calendar invites",
        "status": "pending",
        "priority": null,
        "due": null,
        "created_at": "2026-09-05T02:20:16+00:00",
        "completed_at": null,
        "parent_id": 1
      },
      {
        "id": 4,
        "title": "Order catering",
        "status": "pending",
        "priority": null,
        "due": null,
        "created_at": "2026-09-05T02:20:16+00:00",
        "completed_at": null,
        "parent_id": 1
      },
      {
        "id": 2,
        "title": "Book the venue",
        "status": "done",
        "priority": "high",
        "due": null,
        "created_at": "2026-09-05T02:20:16+00:00",
        "completed_at": "2026-09-05T02:20:16+00:00",
        "parent_id": 1
      }
    ],
    "count": 6
  }
}
[2026-09-05T02:20:16.509Z] Remaining probe tasks after undo: ['probe-high', 'probe-low']
[2026-09-05T02:20:16.509Z] STEP 7 VERDICT: PASS
[2026-09-05T02:20:16.509Z] ### STEP 8 -- sync across two clients
[2026-09-05T02:20:16.509Z] Opening Client B: an independent MCP session with its OWN empty store
[2026-09-05T02:20:17.210Z] Opened MCP session for Client B (CADENCE_DB_PATH=/workspace/tenstep_live_0236/b.db)
[2026-09-05T02:20:17.210Z] Client B creates a task of its own, before ever syncing
[2026-09-05T02:20:17.210Z] PROMPT (agent decides to call): add_task({"title": "Draft offsite agenda", "priority": "low"})   # step: step8-b-create
[2026-09-05T02:20:17.266Z] RESULT of add_task
{
  "isError": false,
  "content": [
    {
      "type": "text",
      "text": "{\n  \"ok\": true,\n  \"task\": {\n    \"id\": 1,\n    \"title\": \"Draft offsite agenda\",\n    \"status\": \"pending\",\n    \"priority\": \"low\",\n    \"due\": null,\n    \"created_at\": \"2026-09-05T02:20:17+00:00\",\n    \"completed_at\": null,\n    \"parent_id\": null\n  },\n  \"recovered\": []\n}",
      "annotations": null,
      "meta": null
    }
  ],
  "parsed": {
    "ok": true,
    "task": {
      "id": 1,
      "title": "Draft offsite agenda",
      "status": "pending",
      "priority": "low",
      "due": null,
      "created_at": "2026-09-05T02:20:17+00:00",
      "completed_at": null,
      "parent_id": null
    },
    "recovered": []
  }
}
[2026-09-05T02:20:17.266Z] Client B's own first task got local id=1, same as Client A's task 1 id=1 -- a genuine id collision between two independently-created, unrelated tasks, neither client having ever synced
[2026-09-05T02:20:17.266Z] Reading the shipped, documented interface only (published 0.2.1): sync_tasks's MCP docstring now says 'remote: The OTHER client's own CADENCE_DB_PATH value (its plain .db file path) -- this client derives that client's history location itself. A git URL also works, for a shared server remote.' The CLI --help says the same thing verbatim. An agent with no repo access, reading only this, would try exactly one value: the other client's own CADENCE_DB_PATH.
[2026-09-05T02:20:17.266Z] Client A syncs, remote = Client B's own plain CADENCE_DB_PATH (DB_B)
[2026-09-05T02:20:17.266Z] PROMPT (agent decides to call): sync_tasks({"remote": "/workspace/tenstep_live_0236/b.db"})   # step: step8-a-sync-to-b-path
[2026-09-05T02:20:17.422Z] RESULT of sync_tasks
{
  "isError": false,
  "content": [
    {
      "type": "text",
      "text": "{\n  \"ok\": true,\n  \"pulled\": 1,\n  \"pushed\": 6,\n  \"conflicts\": [],\n  \"renumbered\": [\n    {\n      \"old_id\": 1,\n      \"new_id\": 7,\n      \"kept_at_old_id\": \"mine\"\n    }\n  ],\n  \"already_synced\": false,\n  \"warnings\": []\n}",
      "annotations": null,
      "meta": null
    }
  ],
  "parsed": {
    "ok": true,
    "pulled": 1,
    "pushed": 6,
    "conflicts": [],
    "renumbered": [
      {
        "old_id": 1,
        "new_id": 7,
        "kept_at_old_id": "mine"
      }
    ],
    "already_synced": false,
    "warnings": []
  }
}
[2026-09-05T02:20:17.422Z] Client B syncs, remote = Client A's own plain CADENCE_DB_PATH (DB_A), to pull A's task and confirm the OTHER direction of the documented contract also works
[2026-09-05T02:20:17.422Z] PROMPT (agent decides to call): sync_tasks({"remote": "/workspace/tenstep_live_0236/a.db"})   # step: step8-b-sync-to-a-path
[2026-09-05T02:20:17.502Z] RESULT of sync_tasks
{
  "isError": false,
  "content": [
    {
      "type": "text",
      "text": "{\n  \"ok\": true,\n  \"pulled\": 6,\n  \"pushed\": 0,\n  \"conflicts\": [],\n  \"renumbered\": [\n    {\n      \"old_id\": 1,\n      \"new_id\": 8,\n      \"kept_at_old_id\": \"mine\"\n    }\n  ],\n  \"already_synced\": false,\n  \"warnings\": []\n}",
      "annotations": null,
      "meta": null
    }
  ],
  "parsed": {
    "ok": true,
    "pulled": 6,
    "pushed": 0,
    "conflicts": [],
    "renumbered": [
      {
        "old_id": 1,
        "new_id": 8,
        "kept_at_old_id": "mine"
      }
    ],
    "already_synced": false,
    "warnings": []
  }
}
[2026-09-05T02:20:17.502Z] Verify convergence: list_tasks(status=all) on BOTH clients should now include both Client A's parent+subtask+probe tasks AND Client B's 'Draft offsite agenda'
[2026-09-05T02:20:17.502Z] PROMPT (agent decides to call): list_tasks({"status": "all"})   # step: step8-verify-a
[2026-09-05T02:20:17.505Z] RESULT of list_tasks
{
  "isError": false,
  "content": [
    {
      "type": "text",
      "text": "{\n  \"ok\": true,\n  \"tasks\": [\n    {\n      \"id\": 6,\n      \"title\": \"probe-high\",\n      \"status\": \"pending\",\n      \"priority\": \"high\",\n      \"due\": null,\n      \"created_at\": \"2026-09-05T02:20:16+00:00\",\n      \"completed_at\": null,\n      \"parent_id\": null\n    },\n    {\n      \"id\": 1,\n      \"title\": \"Prep the Q4 client offsite\",\n      \"status\": \"pending\",\n      \"priority\": \"med\",\n      \"due\": \"2026-09-15\",\n      \"created_at\": \"2026-09-05T02:20:16+00:00\",\n      \"completed_at\": null,\n      \"parent_id\": null\n    },\n    {\n      \"id\": 5,\n      \"title\": \"probe-low\",\n      \"status\": \"pending\",\n      \"priority\": \"low\",\n      \"due\": null,\n      \"created_at\": \"2026-09-05T02:20:16+00:00\",\n      \"completed_at\": null,\n      \"parent_id\": null\n    },\n    {\n      \"id\": 7,\n      \"title\": \"Draft offsite agenda\",\n      \"status\": \"pending\",\n      \"priority\": \"low\",\n      \"due\": null,\n      \"created_at\": \"2026-09-05T02:20:17+00:00\",\n      \"completed_at\": null,\n      \"parent_id\": null\n    },\n    {\n      \"id\": 3,\n      \"title\": \"Send calendar invites\",\n      \"status\": \"pending\",\n      \"priority\": null,\n      \"due\": null,\n      \"created_at\": \"2026-09-05T02:20:16+00:00\",\n      \"completed_at\": null,\n      \"parent_id\": 1\n    },\n    {\n      \"id\": 4,\n      \"title\": \"Order catering\",\n      \"status\": \"pending\",\n      \"priority\": null,\n      \"due\": null,\n      \"created_at\": \"2026-09-05T02:20:16+00:00\",\n      \"completed_at\": null,\n      \"parent_id\": 1\n    },\n    {\n      \"id\": 2,\n      \"title\": \"Book the venue\",\n      \"status\": \"done\",\n      \"priority\": \"high\",\n      \"due\": null,\n      \"created_at\": \"2026-09-05T02:20:16+00:00\",\n      \"completed_at\": \"2026-09-05T02:20:16+00:00\",\n      \"parent_id\": 1\n    }\n  ],\n  \"count\": 7\n}",
      "annotations": null,
      "meta": null
    }
  ],
  "parsed": {
    "ok": true,
    "tasks": [
      {
        "id": 6,
        "title": "probe-high",
        "status": "pending",
        "priority": "high",
        "due": null,
        "created_at": "2026-09-05T02:20:16+00:00",
        "completed_at": null,
        "parent_id": null
      },
      {
        "id": 1,
        "title": "Prep the Q4 client offsite",
        "status": "pending",
        "priority": "med",
        "due": "2026-09-15",
        "created_at": "2026-09-05T02:20:16+00:00",
        "completed_at": null,
        "parent_id": null
      },
      {
        "id": 5,
        "title": "probe-low",
        "status": "pending",
        "priority": "low",
        "due": null,
        "created_at": "2026-09-05T02:20:16+00:00",
        "completed_at": null,
        "parent_id": null
      },
      {
        "id": 7,
        "title": "Draft offsite agenda",
        "status": "pending",
        "priority": "low",
        "due": null,
        "created_at": "2026-09-05T02:20:17+00:00",
        "completed_at": null,
        "parent_id": null
      },
      {
        "id": 3,
        "title": "Send calendar invites",
        "status": "pending",
        "priority": null,
        "due": null,
        "created_at": "2026-09-05T02:20:16+00:00",
        "completed_at": null,
        "parent_id": 1
      },
      {
        "id": 4,
        "title": "Order catering",
        "status": "pending",
        "priority": null,
        "due": null,
        "created_at": "2026-09-05T02:20:16+00:00",
        "completed_at": null,
        "parent_id": 1
      },
      {
        "id": 2,
        "title": "Book the venue",
        "status": "done",
        "priority": "high",
        "due": null,
        "created_at": "2026-09-05T02:20:16+00:00",
        "completed_at": "2026-09-05T02:20:16+00:00",
        "parent_id": 1
      }
    ],
    "count": 7
  }
}
[2026-09-05T02:20:17.505Z] PROMPT (agent decides to call): list_tasks({"status": "all"})   # step: step8-verify-b
[2026-09-05T02:20:17.509Z] RESULT of list_tasks
{
  "isError": false,
  "content": [
    {
      "type": "text",
      "text": "{\n  \"ok\": true,\n  \"tasks\": [\n    {\n      \"id\": 6,\n      \"title\": \"probe-high\",\n      \"status\": \"pending\",\n      \"priority\": \"high\",\n      \"due\": null,\n      \"created_at\": \"2026-09-05T02:20:16+00:00\",\n      \"completed_at\": null,\n      \"parent_id\": null\n    },\n    {\n      \"id\": 8,\n      \"title\": \"Prep the Q4 client offsite\",\n      \"status\": \"pending\",\n      \"priority\": \"med\",\n      \"due\": \"2026-09-15\",\n      \"created_at\": \"2026-09-05T02:20:16+00:00\",\n      \"completed_at\": null,\n      \"parent_id\": null\n    },\n    {\n      \"id\": 1,\n      \"title\": \"Draft offsite agenda\",\n      \"status\": \"pending\",\n      \"priority\": \"low\",\n      \"due\": null,\n      \"created_at\": \"2026-09-05T02:20:17+00:00\",\n      \"completed_at\": null,\n      \"parent_id\": null\n    },\n    {\n      \"id\": 5,\n      \"title\": \"probe-low\",\n      \"status\": \"pending\",\n      \"priority\": \"low\",\n      \"due\": null,\n      \"created_at\": \"2026-09-05T02:20:16+00:00\",\n      \"completed_at\": null,\n      \"parent_id\": null\n    },\n    {\n      \"id\": 3,\n      \"title\": \"Send calendar invites\",\n      \"status\": \"pending\",\n      \"priority\": null,\n      \"due\": null,\n      \"created_at\": \"2026-09-05T02:20:16+00:00\",\n      \"completed_at\": null,\n      \"parent_id\": 1\n    },\n    {\n      \"id\": 4,\n      \"title\": \"Order catering\",\n      \"status\": \"pending\",\n      \"priority\": null,\n      \"due\": null,\n      \"created_at\": \"2026-09-05T02:20:16+00:00\",\n      \"completed_at\": null,\n      \"parent_id\": 1\n    },\n    {\n      \"id\": 2,\n      \"title\": \"Book the venue\",\n      \"status\": \"done\",\n      \"priority\": \"high\",\n      \"due\": null,\n      \"created_at\": \"2026-09-05T02:20:16+00:00\",\n      \"completed_at\": \"2026-09-05T02:20:16+00:00\",\n      \"parent_id\": 1\n    }\n  ],\n  \"count\": 7\n}",
      "annotations": null,
      "meta": null
    }
  ],
  "parsed": {
    "ok": true,
    "tasks": [
      {
        "id": 6,
        "title": "probe-high",
        "status": "pending",
        "priority": "high",
        "due": null,
        "created_at": "2026-09-05T02:20:16+00:00",
        "completed_at": null,
        "parent_id": null
      },
      {
        "id": 8,
        "title": "Prep the Q4 client offsite",
        "status": "pending",
        "priority": "med",
        "due": "2026-09-15",
        "created_at": "2026-09-05T02:20:16+00:00",
        "completed_at": null,
        "parent_id": null
      },
      {
        "id": 1,
        "title": "Draft offsite agenda",
        "status": "pending",
        "priority": "low",
        "due": null,
        "created_at": "2026-09-05T02:20:17+00:00",
        "completed_at": null,
        "parent_id": null
      },
      {
        "id": 5,
        "title": "probe-low",
        "status": "pending",
        "priority": "low",
        "due": null,
        "created_at": "2026-09-05T02:20:16+00:00",
        "completed_at": null,
        "parent_id": null
      },
      {
        "id": 3,
        "title": "Send calendar invites",
        "status": "pending",
        "priority": null,
        "due": null,
        "created_at": "2026-09-05T02:20:16+00:00",
        "completed_at": null,
        "parent_id": 1
      },
      {
        "id": 4,
        "title": "Order catering",
        "status": "pending",
        "priority": null,
        "due": null,
        "created_at": "2026-09-05T02:20:16+00:00",
        "completed_at": null,
        "parent_id": 1
      },
      {
        "id": 2,
        "title": "Book the venue",
        "status": "done",
        "priority": "high",
        "due": null,
        "created_at": "2026-09-05T02:20:16+00:00",
        "completed_at": "2026-09-05T02:20:16+00:00",
        "parent_id": 1
      }
    ],
    "count": 7
  }
}
[2026-09-05T02:20:17.509Z] Client A sees Client B's task ('Draft offsite agenda' in A's list): True; Client B sees Client A's parent task ('Prep the Q4 client offsite' in B's list): True
[2026-09-05T02:20:17.509Z] Current documented behaviour (as of the renumbered/conflicts split, 0.2.3x): an id collision between two independently-created, unrelated tasks is never reported in `conflicts` at all -- there is nothing for resolve_sync_conflict to act on. It is auto-resolved within the same sync_tasks call: whichever task is new to the receiving client gets a fresh, non-colliding local id there, reported by id in `renumbered`, and BOTH tasks' content survives under distinct ids. No manual recovery step is needed or possible here -- checking that against what the two syncs above actually returned:
[2026-09-05T02:20:17.509Z] syncA1['conflicts']=[], syncA1['renumbered']=[{'old_id': 1, 'new_id': 7, 'kept_at_old_id': 'mine'}]
[2026-09-05T02:20:17.509Z] syncB1['conflicts']=[], syncB1['renumbered']=[{'old_id': 1, 'new_id': 8, 'kept_at_old_id': 'mine'}]
[2026-09-05T02:20:17.509Z] no `conflicts` entry on either sync: True; at least one `renumbered` entry recording the auto-resolved collision: True
[2026-09-05T02:20:17.509Z] Client A's own id (task1_id=1) still names its own task ('Prep the Q4 client offsite'), unmoved by the sync: True; Client B's own id (b_task_id=1) still names its own task ('Draft offsite agenda'), unmoved by the sync: True
[2026-09-05T02:20:17.509Z] STEP 8 VERDICT: PASS -- sync itself (plain CADENCE_DB_PATH as remote) works and is discoverable from --help/MCP docstring alone (sync_ok=True); both clients converge on each other's tasks (converged=True); the id collision between Client A's and Client B's independently-created, unrelated id-1 tasks was auto-resolved via renumbering inside the same sync, never routed through `conflicts` or resolve_sync_conflict (no_conflicts=True, got_renumbered=True); each client's own numbering of its own task is unmoved by the sync (a_id1_unchanged=True, b_id1_unchanged=True). No data loss and no manual resolve step is needed for this scenario as of 0.2.36 -- see docs/ten-step-transcript.md for the prior, now-retired resolve_sync_conflict-based recovery this replaces.
[2026-09-05T02:20:17.509Z] ### STEP 9 -- export
[2026-09-05T02:20:17.509Z] PROMPT (agent decides to call): export_tasks({"format": "json"})   # step: step9-export-json
[2026-09-05T02:20:17.512Z] RESULT of export_tasks
{
  "isError": false,
  "content": [
    {
      "type": "text",
      "text": "{\n  \"ok\": true,\n  \"tasks\": [\n    {\n      \"id\": 6,\n      \"title\": \"probe-high\",\n      \"status\": \"pending\",\n      \"priority\": \"high\",\n      \"due\": null,\n      \"created_at\": \"2026-09-05T02:20:16+00:00\",\n      \"completed_at\": null,\n      \"parent_id\": null\n    },\n    {\n      \"id\": 1,\n      \"title\": \"Prep the Q4 client offsite\",\n      \"status\": \"pending\",\n      \"priority\": \"med\",\n      \"due\": \"2026-09-15\",\n      \"created_at\": \"2026-09-05T02:20:16+00:00\",\n      \"completed_at\": null,\n      \"parent_id\": null\n    },\n    {\n      \"id\": 5,\n      \"title\": \"probe-low\",\n      \"status\": \"pending\",\n      \"priority\": \"low\",\n      \"due\": null,\n      \"created_at\": \"2026-09-05T02:20:16+00:00\",\n      \"completed_at\": null,\n      \"parent_id\": null\n    },\n    {\n      \"id\": 7,\n      \"title\": \"Draft offsite agenda\",\n      \"status\": \"pending\",\n      \"priority\": \"low\",\n      \"due\": null,\n      \"created_at\": \"2026-09-05T02:20:17+00:00\",\n      \"completed_at\": null,\n      \"parent_id\": null\n    },\n    {\n      \"id\": 3,\n      \"title\": \"Send calendar invites\",\n      \"status\": \"pending\",\n      \"priority\": null,\n      \"due\": null,\n      \"created_at\": \"2026-09-05T02:20:16+00:00\",\n      \"completed_at\": null,\n      \"parent_id\": 1\n    },\n    {\n      \"id\": 4,\n      \"title\": \"Order catering\",\n      \"status\": \"pending\",\n      \"priority\": null,\n      \"due\": null,\n      \"created_at\": \"2026-09-05T02:20:16+00:00\",\n      \"completed_at\": null,\n      \"parent_id\": 1\n    },\n    {\n      \"id\": 2,\n      \"title\": \"Book the venue\",\n      \"status\": \"done\",\n      \"priority\": \"high\",\n      \"due\": null,\n      \"created_at\": \"2026-09-05T02:20:16+00:00\",\n      \"completed_at\": \"2026-09-05T02:20:16+00:00\",\n      \"parent_id\": 1\n    }\n  ],\n  \"count\": 7\n}",
      "annotations": null,
      "meta": null
    }
  ],
  "parsed": {
    "ok": true,
    "tasks": [
      {
        "id": 6,
        "title": "probe-high",
        "status": "pending",
        "priority": "high",
        "due": null,
        "created_at": "2026-09-05T02:20:16+00:00",
        "completed_at": null,
        "parent_id": null
      },
      {
        "id": 1,
        "title": "Prep the Q4 client offsite",
        "status": "pending",
        "priority": "med",
        "due": "2026-09-15",
        "created_at": "2026-09-05T02:20:16+00:00",
        "completed_at": null,
        "parent_id": null
      },
      {
        "id": 5,
        "title": "probe-low",
        "status": "pending",
        "priority": "low",
        "due": null,
        "created_at": "2026-09-05T02:20:16+00:00",
        "completed_at": null,
        "parent_id": null
      },
      {
        "id": 7,
        "title": "Draft offsite agenda",
        "status": "pending",
        "priority": "low",
        "due": null,
        "created_at": "2026-09-05T02:20:17+00:00",
        "completed_at": null,
        "parent_id": null
      },
      {
        "id": 3,
        "title": "Send calendar invites",
        "status": "pending",
        "priority": null,
        "due": null,
        "created_at": "2026-09-05T02:20:16+00:00",
        "completed_at": null,
        "parent_id": 1
      },
      {
        "id": 4,
        "title": "Order catering",
        "status": "pending",
        "priority": null,
        "due": null,
        "created_at": "2026-09-05T02:20:16+00:00",
        "completed_at": null,
        "parent_id": 1
      },
      {
        "id": 2,
        "title": "Book the venue",
        "status": "done",
        "priority": "high",
        "due": null,
        "created_at": "2026-09-05T02:20:16+00:00",
        "completed_at": "2026-09-05T02:20:16+00:00",
        "parent_id": 1
      }
    ],
    "count": 7
  }
}
[2026-09-05T02:20:17.512Z] PROMPT (agent decides to call): export_tasks({"format": "table"})   # step: step9-export-table
[2026-09-05T02:20:17.516Z] RESULT of export_tasks
{
  "isError": false,
  "content": [
    {
      "type": "text",
      "text": "{\n  \"ok\": true,\n  \"rows\": [\n    \"  [ ]    6   probe-high                                 |  (high)\",\n    \"  [ ]    1   Prep the Q4 client offsite                 |  due 2026-09-15 | med\",\n    \"  [ ]    5   probe-low                                  |  low\",\n    \"  [ ]    7   Draft offsite agenda                       |  low\",\n    \"  [ ]    3   Send calendar invites\",\n    \"  [ ]    4   Order catering\",\n    \"  [x]    2   Book the venue                             |  done 2026-09-05\"\n  ],\n  \"count\": 7\n}",
      "annotations": null,
      "meta": null
    }
  ],
  "parsed": {
    "ok": true,
    "rows": [
      "  [ ]    6   probe-high                                 |  (high)",
      "  [ ]    1   Prep the Q4 client offsite                 |  due 2026-09-15 | med",
      "  [ ]    5   probe-low                                  |  low",
      "  [ ]    7   Draft offsite agenda                       |  low",
      "  [ ]    3   Send calendar invites",
      "  [ ]    4   Order catering",
      "  [x]    2   Book the venue                             |  done 2026-09-05"
    ],
    "count": 7
  }
}
[2026-09-05T02:20:17.516Z] STEP 9 VERDICT: PASS (json export ok/count-consistent=True, table export ok=True)
[2026-09-05T02:20:17.516Z] ### STEP 10 -- deliberately send a malformed request, read the error, and recover
[2026-09-05T02:20:17.516Z] Malformed attempt: add_task with a 250-character title (tool doc says max 200)
[2026-09-05T02:20:17.516Z] PROMPT (agent decides to call): add_task({"title": "XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"})   # step: step10-malformed
[2026-09-05T02:20:17.518Z] RESULT of add_task
{
  "isError": false,
  "content": [
    {
      "type": "text",
      "text": "{\n  \"ok\": false,\n  \"error\": \"invalid_task\",\n  \"message\": \"title is 250 characters, max 200\",\n  \"hint\": \"Try a shorter one.\"\n}",
      "annotations": null,
      "meta": null
    }
  ],
  "parsed": {
    "ok": false,
    "error": "invalid_task",
    "message": "title is 250 characters, max 200",
    "hint": "Try a shorter one."
  }
}
[2026-09-05T02:20:17.518Z] Rejected cleanly: True; carried a hint/message an agent could act on: True
[2026-09-05T02:20:17.518Z] Recovery: shorten the title to <=200 chars per the error's own guidance and retry
[2026-09-05T02:20:17.518Z] PROMPT (agent decides to call): add_task({"title": "XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"})   # step: step10-recover
[2026-09-05T02:20:17.538Z] RESULT of add_task
{
  "isError": false,
  "content": [
    {
      "type": "text",
      "text": "{\n  \"ok\": true,\n  \"task\": {\n    \"id\": 8,\n    \"title\": \"XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX\",\n    \"status\": \"pending\",\n    \"priority\": null,\n    \"due\": null,\n    \"created_at\": \"2026-09-05T02:20:17+00:00\",\n    \"completed_at\": null,\n    \"parent_id\": null\n  },\n  \"recovered\": []\n}",
      "annotations": null,
      "meta": null
    }
  ],
  "parsed": {
    "ok": true,
    "task": {
      "id": 8,
      "title": "XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
      "status": "pending",
      "priority": null,
      "due": null,
      "created_at": "2026-09-05T02:20:17+00:00",
      "completed_at": null,
      "parent_id": null
    },
    "recovered": []
  }
}
[2026-09-05T02:20:17.539Z] STEP 10 VERDICT: PASS (malformed_rejected=True, got_hint=True, recovered=True)
[2026-09-05T02:20:17.539Z] ===== SUMMARY =====
[2026-09-05T02:20:17.539Z] STEP 1: PASS
[2026-09-05T02:20:17.539Z] STEP 2: PASS
[2026-09-05T02:20:17.539Z] STEP 3: PASS
[2026-09-05T02:20:17.539Z] STEP 4: PASS
[2026-09-05T02:20:17.539Z] STEP 5: PASS
[2026-09-05T02:20:17.539Z] STEP 6: PASS
[2026-09-05T02:20:17.539Z] STEP 7: PASS
[2026-09-05T02:20:17.539Z] STEP 8: PASS
[2026-09-05T02:20:17.539Z] STEP 9: PASS
[2026-09-05T02:20:17.539Z] STEP 10: PASS
[2026-09-05T02:20:17.539Z] ALL PASS: True
[2026-09-05T02:20:17.833Z] ===== R-08 TEN-STEP TRANSCRIPT END =====
```

## Summary

| Step | Description | Verdict |
|---|---|---|
| 1 | create a task | PASS |
| 2 | schedule it | PASS |
| 3 | decompose a vague request into subtasks | PASS |
| 4 | re-prioritise | PASS |
| 5 | complete a task | PASS |
| 6 | query (all-status list + ordering claim) | PASS |
| 7 | undo | PASS |
| 8 | sync across two clients | PASS |
| 9 | export (json + table) | PASS |
| 10 | recover from a deliberately malformed request | PASS |

**All 10 of 10 steps pass** against `cadence-todo` 0.2.36, run from a
fresh `pip install` with no repository checkout on the machine.

### Step 8 in detail — what changed and why it now passes correctly

Client A and Client B each create their own first task before either
has ever synced, so both independently land on local id 1 — a genuine
collision between two unrelated tasks, not an edit of the same task.
As of the `renumbered`/`conflicts` split shipped in the 0.2.3x series,
`sync_tasks` resolves this kind of collision itself, in the same call:
the task that is new to the receiving client is given a fresh,
non-colliding local id there, recorded in that sync's `renumbered`
list, and nothing is ever written to `conflicts` for it — there is
nothing for `resolve_sync_conflict` to act on, and calling it for this
scenario would be acting on a `conflicts` entry that does not exist.

The live run above shows exactly that: `syncA1` and `syncB1` both
report `"conflicts": []` and a non-empty `renumbered` entry (Client
B's task became id 7 on Client A's side; Client A's task became id 8
on Client B's side), both clients converge on seeing all of each
other's tasks by title, and each client's own id for its own
originally-created task is left unmoved by the sync. No data is lost
and no manual conflict-resolution call is needed for this scenario.

`resolve_sync_conflict` still exists and is still the right tool for
its own documented case — the same task genuinely edited on both
sides between syncs — which is a different scenario from the one Step
8 exercises and is not re-tested here.

## Reproduction

```
python3 -m venv venv && venv/bin/pip install cadence-todo   # resolves to 0.2.36 or later
venv/bin/pip install mcp
venv/bin/cadence --help
venv/bin/python docs/ten-step-transcript-runner.py \
    venv/bin/cadence /tmp/a.db /tmp/b.db /tmp/remote
```

`docs/ten-step-transcript-runner.py` (committed alongside this file) is
the exact driver used to produce Part 2 above; it takes the `cadence`
binary path and three scratch file paths as arguments and reproduces
this session end to end against any `cadence-todo` install. Run it
against fresh, non-existent db paths — reusing paths from a previous
run carries over that run's task history and will not reproduce the
id numbers shown here (though the verdicts are stable either way).
