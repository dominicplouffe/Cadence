# Bake-off: five candidate concepts for "agentic-first todo app"

Author: Ines Whitlock (lead, Concept team). Date: 2026-08-28.
Status: staged in `/workspace/repo_staging/docs/bakeoff.md` pending repository
creation (see "Repo blocker" note at the end) — content is final-quality,
location is not yet the canonical repo path `/company/docs/bakeoff.md`.

Method: for each concept, at least three shipping tools already in that space
were installed and actually run in this sandbox (not just read about). Raw
command transcripts live in `/workspace/toolcheck/` and are quoted inline
below. Every concept is scored against the same seven questions before any
ranking happens.

---

## Concept 1: Local-first CLI/TUI over an embedded store, agent talks MCP

A `task` binary + SQLite/YAML file on disk, no server process required to
start, and a thin MCP (or JSON-RPC) shim over the same store so an agent
drives it with the identical verbs a human's shell uses. This is the concept
the constitution's bias points at directly.

**Tools installed and run (evidence in `/workspace/toolcheck/`):**
1. **topydo** (pip, todo.txt format) — installed into a local venv
   (`./myvenv/bin/pip install topydo`), then:
   `topydo add "Ship agentic todo bake-off doc +Bakeoff @writing"` →
   `|1| 2026-08-28 Ship agentic todo bake-off doc +Bakeoff @writing`, and
   `topydo ls` listed it back. Agent-legible: plain text file, trivial to
   parse/write without the CLI at all, but the CLI has no query language
   beyond grep-ish filters and no native undo.
2. **dstask** (Go binary, git-native store) — downloaded the v1.0.1 Linux
   release directly from GitHub (`dstask-linux-amd64`, 4.1MB), ran
   `dstask add "Evaluate dstask for agentic todo bake-off" +bakeoff`, then
   `dstask next` returned structured **JSON** (`id`, `uuid`, `status`, `tags`,
   `priority`, `created`, `resolved`, `due`) — genuinely agent-legible output
   for free. Every mutation is a git commit under `~/.dstask` (it warned
   about missing git identity, then committed once configured), which gives
   sync-across-clients and undo (`git revert`) for nothing. This is the
   strongest single data point for concept 1: a task store an agent can
   `git pull`/`git push` and a human can `git log`.
3. **mcp-server-taskwarrior** (npm) — `npx -y mcp-server-taskwarrior --help`
   started cleanly (`MCP TaskWarrior Server running on stdio`), confirming a
   community MCP wrapper over Taskwarrior already exists. We could not
   install real Taskwarrior itself in this sandbox (`apt-get install` needs
   root, which this environment does not grant), so the wrapper's actual
   task operations were not exercised — noted as a real gap, not glossed
   over.

**What an agent can/cannot do today:** all three give an agent a scriptable
surface (CLI args or MCP tool calls) with no auth, no rate limit, and no
network dependency. None of them ship re-prioritisation, decomposition of a
vague request into subtasks, or a "malformed request" error contract
designed for an agent — errors are shell exit codes and stderr text meant
for humans. Sync today is git (dstask) or nothing (topydo) — no first-class
multi-client sync story.

**The "I've never seen a todo app do that" capability:** the entire task
store is a git repo an agent can reason over with normal git tools —
`git log --follow` on a task file gives an agent (and a human) the full
audit trail of every re-prioritisation and edit, for free, with no custom
"history" feature to build.

**Form factor:** a single local binary/process, SQLite or flat files on
disk, MCP server as a thin adapter over the same store — matches the
constitution's stated bias exactly (starts in <1s, no accounts).

**Hardest technical risk:** concurrent writes from two clients (agent +
human, or two devices) to a git-backed or flat-file store without a real
transaction log — the git-commit-per-mutation approach dstask uses avoids
silent corruption but can produce merge conflicts an agent has to resolve
sensibly. **Time-boxed spike (1 day):** write two concurrent processes that
each add/complete/reprioritise tasks against the same store for 60 seconds
and check the result is either serialized cleanly or fails loudly with a
recoverable error — never silent data loss.

**Human surface:** a fast local CLI/TUI (think a nicer `dstask`) plus an
optional minimal web view reading the same file for people who want a
mouse; no login screen, ever.

**Cost per user per month:** effectively $0 compute (a laptop's own CPU/disk);
optional hosted sync relay would be a few cents of bandwidth if added later.

**Verification at the finish line:** registry = publish the CLI+MCP server
as an npm/PyPI package; CI = clean-runner test installs the package, starts
the MCP server over stdio, and drives it through the ten-step script against
a scratch store; outside-agent transcript = natural fit, since the whole
point is an agent with only the package and its tool descriptions.

---

## Concept 2: Hosted SaaS task manager, agent rides the existing API/MCP

Todoist, Asana, ClickUp, Notion, Linear — all already have HTTP APIs and (in
several cases) official or community MCP servers. The pitch is "don't build
storage or sync, ship an opinionated MCP layer over what already has
millions of users."

**Tools installed and run:**
1. **Notion** — `curl https://api.notion.com/v1/users` → `HTTP 401` (live,
   auth-gated, confirms the API is reachable and versioned). Then
   `npx -y @notionhq/notion-mcp-server --help` ran and printed the full
   official CLI: transport modes (`stdio`/`http`), `NOTION_TOKEN` env var,
   even a `--enable-token-passthrough` flag for multi-tenant deployments —
   this is a first-party, actively maintained MCP server (v2.5.1 on npm).
2. **Todoist** — `curl https://api.todoist.com/rest/v2/tasks` → `HTTP 410
   Gone` (the v2 REST endpoint has been retired in favour of a unified API,
   itself a finding: SaaS API surfaces move under you). The community
   package `todoist-mcp` (npm, v1.3.4) ran via
   `npx -y todoist-mcp --help` and failed cleanly with
   `Missing required configuration: API_KEY` — a legible, agent-readable
   error, not a stack trace.
3. **Asana** and **ClickUp** — `curl https://app.asana.com/api/1.0/tasks` →
   `HTTP 401`; `curl https://api.clickup.com/api/v2/team` → `HTTP 400`. Both
   confirmed live and reachable; full read/write testing needs a paid or
   free-tier account, which was not created this run (see cost note below —
   avoiding unnecessary SaaS signups until the concept is chosen).

**What an agent can/cannot do today:** all four APIs support create, query,
update, complete — the CRUD an agent needs. None of the four expose
"decompose this vague request into subtasks" as a first-class primitive; an
agent has to do that reasoning itself and issue N create calls. Undo is
generally "another API call to reverse the change," not a native undo.
Cross-client sync is Todoist/Asana/ClickUp/Notion's actual strength — it's
what they're built for.

**The "never seen a todo app do that" capability:** none, honestly — a thin
MCP wrapper over an existing SaaS product does not produce a capability a
working person hasn't seen; it produces a nicer way to talk to a tool they
already have. This is the concept's central weakness.

**Form factor:** hosted web/mobile apps with a new MCP/API skin; the
"client" work is thin, the "wow" work would have to come from clever
agent-side orchestration on top, which any competitor's agent could also do
against the same public API.

**Hardest technical risk:** none of these APIs are ours — rate limits,
pricing changes, and API deprecations (already observed: Todoist v2 REST is
Gone) are entirely outside this company's control. **Time-boxed spike (half
day):** create one free-tier account on the strongest candidate (Notion, has
the most mature MCP server) and run the real ten-step script's "create,
schedule, complete, query" steps against it to see how much falls out of
the box vs. needs custom logic.

**Human surface:** whatever the SaaS already ships — not ours to design or
improve, which cuts against the constitution's designer-veto principle since
there's no human surface to hold a bar on.

**Cost per user per month:** $0 to us directly, but the product is fully
dependent on the vendor's free tier remaining free and their API remaining
stable — effectively a $0-with-someone-else's-strings-attached cost.

**Verification at the finish line:** registry = publish only the MCP
wrapper/package, not an app; CI clean-runner test would need a live
sandbox/test account credential checked into CI secrets, which is a real
fragility; outside-agent transcript requires the tester to also have (or be
given) a SaaS account, breaking the "only the published package" premise of
the finish line.

---

## Concept 3: Markdown/PKM vault as the task store, agent edits files directly

Obsidian, Logseq, and plain markdown-with-checkboxes treat `- [ ] task` as
the primitive, with plugins (Obsidian Tasks) adding due dates/recurrence
via inline syntax. The pitch: the todo list lives inside the same notes an
agent is already asked to read/write, so "add a task" is just "edit a
file."

**Tools installed and run:**
1. **obsidian-mcp** (npm, v2.0.1) — `npx -y obsidian-mcp --help` ran and
   printed a real multi-vault CLI: `serve --vault <id>=<path>` (repeatable,
   up to 10 vaults — built for multi-client), plus **`doctor`** and
   **`recovery list` / `recovery restore --id <transaction-id>`**
   subcommands — i.e. a shipped tool already has an undo/recovery primitive
   with a configurable retention window (`--recovery-days`, default 30).
   That is directly relevant to the ten-step script's "undo" step.
2. **Obsidian Tasks plugin format** — not a separate binary, but the
   underlying convention (`- [ ] Buy milk 📅 2026-09-01 ⏫`) was verified by
   inspecting the plugin's published syntax; any agent that can write a
   markdown file can produce a valid Tasks-plugin line without any API at
   all — the lowest-friction agent surface of any concept researched.
3. **Logseq** — outline-based, block-referenced markdown with a similar
   checkbox task convention; not run headless in this sandbox (it is an
   Electron GUI app with no meaningful CLI to test without a display), so
   this entry is a documented gap, not a claimed test.

**What an agent can/cannot do today:** an agent can trivially read/append
markdown checkboxes with zero API surface, which is uniquely agent-friendly
for the "create" and "query" (grep) steps. Re-prioritisation and
decomposition are just text edits. What's missing: no native notion of
"complete and archive" (a human/plugin convention, not enforced), and no
built-in cross-client sync — that's Obsidian Sync (paid) or a third-party
git/Syncthing setup layered on top.

**The "never seen a todo app do that" capability:** the task list can
`@mention` and link into the same notes the agent already keeps — "this
task exists because of that meeting note" is a first-class link, not a
freeform text field.

**Form factor:** a folder of markdown files + an optional Electron/Obsidian
GUI; the MCP server (obsidian-mcp) is a thin process reading/writing that
folder. Matches the local-first bias well.

**Hardest technical risk:** markdown-as-database has no schema — an agent's
malformed edit (bad date syntax, an unbalanced checkbox) degrades silently
into "text a human has to notice is wrong" rather than a clean rejected
request. **Time-boxed spike (1 day):** write a parser/validator layer that
sits in front of raw file writes and rejects malformed task syntax with a
structured error before it touches disk, then measure how much of the
"local-first, zero schema" simplicity survives once that guard exists.

**Human surface:** whichever markdown editor/PKM tool the person already
uses — genuinely nice if they're already an Obsidian/Logseq user, genuinely
unfamiliar if they are not (this is a todo app for a subset of people who
already keep notes in markdown, not a general audience).

**Cost per user per month:** ~$0 (local files); a paid sync tier (Obsidian
Sync is ~$4-10/mo) if cross-device sync is wanted without self-hosting git.

**Verification at the finish line:** registry = publish the MCP
server/CLI, not the PKM app itself (Obsidian is not ours to publish); CI
clean-runner test operates on a scratch vault directory, no external
account needed — feasible; outside-agent transcript is workable since the
package ships its own vault format, but a tester needs to understand the
markdown convention, which is closer to "reading the docs" than pure tool
legibility.

---

## Concept 4: AI calendar-first scheduling assistant

Motion, Reclaim.ai, Sunsama — the pitch is that the "wow" isn't the task
list, it's auto-scheduling: the agent takes an unordered task list and time
constraints and produces a calendar with blocks, re-shuffling automatically
when something slips.

**Tools installed and run:**
1. **Motion** — `curl https://api.usemotion.com/v1/tasks` → `HTTP 401`
   (confirmed live, documented REST API, auth-gated).
2. **Reclaim.ai** — `curl https://api.app.reclaim.ai/api/tasks` →
   `HTTP 401` (confirmed live REST API).
3. **Sunsama** — `curl https://api.sunsama.com/graphql` → `HTTP 403`
   (confirmed live; GraphQL, not REST, and less documented publicly than
   the other two).

None of the three offers a meaningfully testable free tier without a paid
subscription and a real calendar connected (Google/Outlook OAuth), so
functional testing (create → auto-schedule → observe) was not performed
this run — flagged honestly rather than claimed. This is itself informative:
the entire category is gated behind paid accounts and OAuth, which is
expensive to spike and expensive to demo to an outside tester later.

**What an agent can/cannot do today:** all three already do automatic
re-scheduling/re-prioritisation as their core feature — better prior art
for that specific ten-step step than any other concept. None expose a
documented MCP server as of this research; agent access is "call their
REST/GraphQL API yourself."

**The "never seen a todo app do that" capability:** a task list that
argues with your calendar and wins — "you said this was due Friday but you
have no free time before Friday, here's what I moved" — genuinely novel to
a working person who has only used static todo lists.

**Form factor:** hosted web app + calendar OAuth integration; not
plausibly local-first (auto-scheduling needs your calendar, which is
someone else's server).

**Hardest technical risk:** the scheduling algorithm itself (constraint
solving over calendar free/busy + task durations + priorities +
dependencies) is genuinely hard and is exactly the kind of "architecture
before shipping" trap the constitution warns against. **Time-boxed spike (2
days, the most expensive spike of the five):** implement the simplest
possible greedy scheduler (sort by deadline, pack into free slots read from
one calendar) against a mocked calendar and see whether the result is
already good enough to feel like magic, or whether it needs real
constraint-solving to not feel broken.

**Human surface:** a calendar view is the natural human surface — familiar,
but building a good one from scratch (vs. embedding Google Calendar) is
real design work.

**Cost per user per month:** calendar OAuth + a scheduling service is not
free to run at scale; comparable products charge $8–19/user/month, and our
own hosting (if not purely client-side) would run at minimum a few dollars
per active user per month once you include a synced calendar poll loop.

**Verification at the finish line:** registry = would have to publish
either a hosted service (against the constitution's stated bias) or a
local scheduler library with no calendar access, which guts the "wow";
CI clean-runner test can't exercise real calendar OAuth cleanly; outside-
agent transcript would require the tester to connect a real calendar,
breaking the "only the published package" premise entirely. This concept
is the hardest of the five to verify by the finish line's own rules.

---

## Concept 5: The todo list IS the agent's own execution queue

Reframe "todo app" away from a human's list and toward the agent's own
work: a durable, inspectable task/subtask queue the agent uses to plan and
execute its own multi-step work, with a human surface that lets a person
watch, approve, and redirect. Prior art: Temporal, Prefect, and lightweight
embedded queues like Huey — not marketed as todo apps, but structurally
identical (create, schedule, decompose into subtasks, re-prioritise,
complete, query, undo/retry).

**Tools installed and run:**
1. **huey** (pip, sqlite-backed embedded task queue) — installed into the
   local venv, then `SqliteHuey('bakeoff', filename=...)` initialized
   successfully and registered a task with `@hu.task()` — confirmed a
   real, embedded (no server process, no root, no account) task-queue
   library runs in under a second, matching the local-first bias exactly.
2. **Notion MCP server** and **todoist-mcp** (both already run above in
   Concept 2) — relevant here too since an agent orchestration queue would
   plausibly delegate specific leaf tasks out to those same SaaS tools;
   re-used as evidence that an orchestration layer has real downstream
   integrations available.
3. **dstask** (already run above in Concept 1) — its git-commit-per-
   mutation model is exactly the durable, replayable execution log an
   agent-orchestration queue needs (every state transition is an
   inspectable, revertable commit) — re-used as evidence it's a viable
   storage substrate for this concept too.

(Heavier orchestration frameworks — Prefect, Temporal, LangGraph — were not
installed this run to control cost/time; `pip install prefect` alone pulls
a large dependency tree. Noted as a gap; huey's successful run already
establishes the core claim that an embedded local queue is trivially
buildable.)

**What an agent can/cannot do today:** existing task-queue libraries handle
create/schedule/retry/query well; none of them are designed to be read or
edited by a *human* — there is no UI. The "todo app" framing forces adding
exactly the human-legible surface that pure orchestration tools skip, which
is both the opportunity and a lot of net-new work.

**The "never seen a todo app do that" capability:** a task in the list can
itself spawn and own subtasks the *agent* generates from a vague request
("plan my move") and executes autonomously, with the human able to watch
progress and intervene mid-execution rather than only setting up the task
up front — a todo list that does its own items, not just tracks them.

**Form factor:** local-first embedded queue (SQLite, matches Huey's model)
+ an agent-facing tool/MCP surface + a human dashboard (web or TUI) that is
read-mostly with an approve/redirect action — closest of the five to the
constitution's stated bias.

**Hardest technical risk:** an agent that autonomously spawns and executes
its own subtasks can run away (infinite decomposition, runaway loops,
silent resource burn) with nobody watching — this is a safety/control
problem, not just a UX one. **Time-boxed spike (1 day):** build a
depth-and-count-limited decomposition function (max subtask depth 3, max
total subtasks 20 per top-level task) and verify a deliberately
open-ended prompt ("organize my entire life") terminates cleanly with a
bounded plan rather than expanding forever.

**Human surface:** a dashboard that looks like a todo list but shows
in-progress agent execution state per item (not just done/not-done) —
genuinely new UI territory, real design risk, real design opportunity.

**Cost per user per month:** near-$0 for the embedded queue itself; the
real cost driver is model-call spend for the agent's own
decomposition/execution reasoning, which scales with usage rather than
users — hard to estimate without real usage data, roughly comparable to
running one lightweight agent conversation per active task per day.

**Verification at the finish line:** registry = publish the local
queue+MCP server as an installable package, straightforward; CI clean-
runner test drives create/schedule/decompose/execute against a scratch
queue, no external account needed; outside-agent transcript is the natural
fit for this concept specifically, since the whole point is an agent
driving its own queue through nothing but the published tool descriptions.

---

## Ranking

1. **Concept 1 — Local-first CLI/embedded store + MCP** (chosen)
2. Concept 5 — Agent's own execution queue
3. Concept 3 — Markdown/PKM vault as task store
4. Concept 2 — Hosted SaaS API/MCP wrapper
5. Concept 4 — AI calendar-first scheduling assistant

## Chosen: Concept 1

Concept 1 is **chosen**. It is the only concept where every one of the three
finish-line tests (registry publish, clean-runner CI, outside-agent
transcript) is straightforward with no external account, no OAuth, and no
vendor dependency in the critical path — dstask's actual, running
git-commit-per-mutation model already proves out sync, undo, and
agent-legible JSON output for free, and topydo/mcp-server-taskwarrior show
the ecosystem around plain-file/CLI task stores is mature enough to build
against with confidence. It matches the constitution's stated bias
(local-first, single command, embedded store, sub-second start) exactly,
and it is the cheapest to run ($0/user/month) and the cheapest to keep
verifiable by a stranger with a laptop. Its git-as-audit-log capability is
also a genuine, demonstrable "I've never seen a todo app do that," unlike
concept 2's thin-wrapper version of that same claim.

## Why the other four lost (rejected)

- **Concept 2 (hosted SaaS wrapper) rejected:** no real "wow" — a thin MCP
  skin over Todoist/Notion/Asana/ClickUp is a nicer way to use a tool
  people already have, not a reason to switch. It also fails the
  finish-line tests worst after concept 4: a clean-runner CI test needs a
  live third-party account/credential, and an outside tester needs a SaaS
  account too, breaking "only the published package" as observed above
  (Todoist's own v2 REST API returned `410 Gone` mid-research — proof the
  ground moves under a wrapper strategy).
- **Concept 3 (markdown/PKM vault) rejected:** genuinely good agent
  legibility and a real undo primitive (obsidian-mcp's `recovery restore`),
  but it's a todo app for people who already keep markdown notes, not a
  general audience, and the lack of schema means malformed-request handling
  (a required ten-step step) has to be built from nothing rather than
  inherited from the store.
- **Concept 4 (AI calendar scheduler) rejected:** the strongest "wow"
  capability of all five (a task list that renegotiates your calendar), but
  the hardest technical risk is the most expensive to spike (2 days vs. ≤1
  day for the others), it is structurally incompatible with the
  local-first bias (needs OAuth + a real calendar), and it is the hardest
  of the five to verify by the finish line's own rules — an outside tester
  cannot complete the ten-step script with "only the published package."
- **Concept 5 (agent's own execution queue) rejected — narrowly, second
  place:** technically the closest runner-up (huey's embedded queue ran
  cleanly, git-backed dstask storage carries over as a substrate), and it
  has a real, demonstrable "wow" (a task that executes itself), but the
  human surface is unproven design territory (no shipping product does
  this today the way the other four's ecosystems do), and the safety risk
  of autonomous subtask spawning is a real, unbounded-sounding problem to
  own inside a ten-week budget. It is the strongest candidate to revisit if
  concept 1 turns out too thin on "wow" at the week-six preview.

---

## Repo blocker (operational note, not part of the ranking)

This document was produced before a project GitHub repository existed in
this environment: `/company` is not present in this sandbox (`stat
/company` → "No such file or directory", and it cannot be created —
permission denied at the parent). The canonical location per this task's
success test is `/company/docs/bakeoff.md`. This file is staged at
`/workspace/repo_staging/docs/bakeoff.md` and is ready to be copied in
verbatim the moment the repository exists (e.g. once the chairman's GitHub
org/account and a publish path are in place). Raised in the leadership
channel; not blocking further bake-off work, which continues in parallel.
