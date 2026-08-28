# Cadence

Cadence is an agentic-first todo application: a task manager whose primary
user is an AI agent, with a human surface good enough that a person prefers
it to what they use now.

The plan is to publish Cadence as a public, installable, open-source project
by **6 November 2026**. That date is a delivery commitment, not an estimate.

## Status

This repository is in its bake-off / early-build phase. Nothing here is
installable yet. See [`docs/bakeoff.md`](docs/bakeoff.md) for the five
candidate concepts we researched, the evidence behind each, and which one we
chose and why.

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
