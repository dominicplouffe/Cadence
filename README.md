# Cadence

Cadence is an agentic-first todo application: a task manager whose primary
user is an AI agent, with a human surface good enough that a person prefers
it to what they use now.

The plan is to publish Cadence as a public, installable, open-source project
by **6 November 2026**. That date is a delivery commitment, not an estimate.

## Status

Early build. Not yet published to a package registry (see the finish-line
checklist below) but it builds, runs, and is tested from a fresh clone.
See [`docs/bakeoff.md`](docs/bakeoff.md) for the five candidate concepts we
researched, the evidence behind each, and which one we chose and why, and
[`docs/human-surface.md`](docs/human-surface.md) for the CLI's binding
design spec.

## Try it from a fresh clone

```
git clone https://github.com/dominicplouffe/Cadence.git
cd Cadence
pip install -e .
cadence add "Buy milk" --due 2026-09-01 --priority high
cadence list
cadence done 1
```

By default tasks live in `~/.cadence/cadence.db` (a local SQLite file).
Set `CADENCE_DB_PATH` to point at a scratch file instead (used by the test
suite and useful for an agent that wants an isolated store).

Start the MCP server (agent surface) over stdio, exposing `add_task`,
`list_tasks`, `complete_task`, and `schedule_task` as tools with structured
JSON returns:

```
cadence mcp
```

Run the test suite:

```
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
