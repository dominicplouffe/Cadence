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
researched, the evidence behind each, and which one we chose and why, and
[`docs/human-surface.md`](docs/human-surface.md) for the CLI's binding
design spec.

## Install

```
pip install cadence-todo
```

Every line below is a real CLI command, run yourself, no agent required —
this is the whole bet: **every change to your list, yours or an agent's,
comes with a legible reason and a clean undo**, because it's backed by the
same git you already trust for code, not a bespoke history feature Cadence
invented and might get wrong. Real output, `cadence-todo` 0.2.9,
`NO_COLOR=1`:

```
$ cadence add "Plan Mara's 30th birthday party"
Added #1: Plan Mara's 30th birthday party

$ cadence decompose 1 --into "Book a venue" "Order a cake" "Send invites"
Decomposed #1 into 3 subtasks: #2, #3, #4

$ cadence reprioritise 2 high --reason "venue books up fastest"
Reprioritised #2 (none → high): Book a venue

$ cadence why 2
#2 Book a venue — history (newest first):

  -  high     just now     Reprioritised (none → high)
                       "venue books up fastest" — you, via CLI

  -  none     just now     Created as subtask of #1 (Plan Mara's 30th birthday
                           party)

No reason was recorded for this change. Reasons are optional —
pass --reason "..." (CLI) or a `reason` argument (MCP tool call)
to leave one next time.

$ cadence undo
Undid: Reprioritised #2 (none → high) undone: Book a venue
```

That `why` line is the payoff: a straight answer to "why did this change,"
without you ever opening git, a hidden folder, or asking your agent to
explain itself. `cadence list` and `cadence done <id>` work exactly the
way you'd expect from any todo CLI — full command reference below.

By default tasks live in `~/.cadence/cadence.db` (a local SQLite file) with
a git-backed history alongside it. Set `CADENCE_DB_PATH` to point at a
scratch file instead (used by the test suite and useful for an agent that
wants an isolated store).

Start the MCP server (agent surface) over stdio, exposing tools such as
`add_task`, `list_tasks`, `complete_task`, `schedule_task`,
`decompose_task`, `reprioritise_task`, `undo`, and `sync_tasks` with
structured JSON returns:

```
cadence mcp
```

### Remote access (Claude web, Claude mobile, another machine)

`cadence mcp` (above) talks stdio, so only a local process on the same
machine — e.g. Claude Code / VSCode — can reach it. If you also want Claude
web or Claude on your phone to reach the *same* task store, run the HTTP
transport instead:

```
cadence mcp --http
```

This is still local-first: it starts a server on **your own machine**
(default `127.0.0.1:8765`), backed by the exact same store as `cadence
mcp` and the `cadence` CLI — there is no hosted backend, no account, no
third party in the middle. It prints the port it's listening on and
requires a bearer token on every request, generated once on first use and
stored at `~/.config/cadence/mcp_http_token` (owner-read/write only, same
directory as the rest of Cadence's config). Get that token with:

```
cadence mcp --show-token
```

To use it from a remote client (Claude web/mobile, or an agent on another
machine): expose the port to that client somehow — an SSH tunnel,
[Tailscale](https://tailscale.com), or a TLS-terminating reverse proxy are
all reasonable choices; Cadence does not add TLS itself — then configure
the client with the resulting URL's `/mcp` path and an `Authorization:
Bearer <token>` header carrying the token from `cadence mcp --show-token`.
A request with a missing or wrong token gets a clean `401` with the same
`{"ok": false, "error", "message", "hint"}` shape every other Cadence
error uses, not a stack trace.

You can also pass `--host`, `--port`, or an explicit `--token` (or set
`CADENCE_MCP_TOKEN`) to override the generated one, e.g. for a fixed token
across restarts in a script.

### Building from source

```
git clone https://github.com/dominicplouffe/Cadence.git
cd Cadence
pip install -e .
pip install pytest
pytest -q
```

## What "agentic-first" means here

- **Primary interface is agent-legible by design.** An agent that has never
  read the docs should be able to work out what each tool does, what it
  returns, and what it did wrong, from the interface itself.
- **A human surface is not optional.** A capability that exists only for
  agents is half-built, and so is one that exists only for humans.
- **Local-first bias.** We prefer something a person installs with a single
  command and owns the data of, over a hosted multi-tenant web app.

## The finish line

Three things must be true and independently checkable by someone outside
this company, on or before the committed date:

1. Published on a public package registry, installable with one command.
2. CI on a clean GitHub-hosted runner goes green on the full suite,
   including an end-to-end test that installs the published artifact and
   drives it.
3. A committed transcript exists of an agent with no access to this
   repository completing a fixed ten-step script using only the published
   package.

## Contributing

Not yet open for external contributions — the project is pre-publication.
Once published, this section will describe how to build from a fresh clone,
run the test suite, and submit changes.

## License

MIT — see [`LICENSE`](LICENSE).
