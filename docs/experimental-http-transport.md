# EXPERIMENTAL: remote MCP transport (`cadence mcp --http`)

Status: **spike prototype, not a shipped feature.** Time-boxed to ~2 hours
per the chairman's ask ("if you can't solve my problem [Claude web/mobile
reaching my local store], you can't solve the majority of people's
problem"). This document is the honest go/no-go note that spike produced.

## What it is

`cadence mcp` today only speaks stdio: a local process on the same machine
launches it and talks over its own stdin/stdout. Claude Code / VSCode can
do that; Claude web and Claude's mobile app cannot reach a process on your
laptop at all, because they connect over HTTP, not by spawning a
subprocess.

The MCP spec's **Streamable HTTP** transport (what Claude's web/mobile
"custom connector" feature speaks) is exactly the same JSON-RPC tool
surface, over `POST /mcp` with an `Authorization` header and a session id,
instead of stdin/stdout. The official `mcp` Python SDK's `FastMCP` class
(already Cadence's only MCP dependency, version pinned `>=1.2.0,<2.0.0`,
resolved here to 1.29.1) ships this transport built in —
`mcp.streamable_http_app()` returns a Starlette ASGI app for the exact
same `@mcp.tool()`-decorated functions already used by the stdio path.
**No new runtime dependency was needed**: `mcp` already requires
`starlette` and `uvicorn` unconditionally (not as an extra), confirmed via
`importlib.metadata.requires("mcp")` in this venv.

## What was built (this branch/commit only, not on the published PyPI package)

- `cadence.mcp_server.run_http(host, port, token)`: wraps the stock
  streamable-HTTP ASGI app in a minimal `BearerAuth` ASGI middleware that
  checks `Authorization: Bearer <token>` on every HTTP request and
  responds `401` with the same `{ok:false, error, message, hint}` shape
  every other Cadence error uses, before falling through to the real MCP
  app. No accounts, no multi-tenant backend, no change to the sqlite
  store or the sync/history model — the user runs this process on their
  own machine and owns the port.
- `cadence mcp --http [--host H] [--port P] [--token T]` (token can also
  come from `CADENCE_MCP_TOKEN`) — new opt-in flags on the existing `mcp`
  subcommand. Plain `cadence mcp` (no flags) is byte-for-byte the same
  stdio path as before; nothing about it changed.
- No change to `pyproject.toml`, no version bump, nothing published to
  PyPI. This is additive code sitting behind a flag nobody hits by
  default.

## Real transcript (captured verbatim from this run)

Server started:
```
$ CADENCE_DB_PATH=/tmp/x_httpspike_db/store.db CADENCE_MCP_TOKEN=spiketoken123 \
    /tmp/x_httpspike/bin/cadence mcp --http --port 8765
[cadence mcp --http] EXPERIMENTAL remote transport listening on http://127.0.0.1:8765/mcp -- bearer token required on every request. This is a spike prototype, not a hardened feature; see docs/experimental-http-transport.md.
StreamableHTTP session manager started
```

Wrong token rejected:
```
$ curl -s -i -X POST http://127.0.0.1:8765/mcp \
    -H "Authorization: Bearer wrong-token" -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
HTTP/1.1 401 Unauthorized
content-type: application/json

{"ok":false,"error":"unauthorized","message":"Missing or wrong bearer token.","hint":"Send 'Authorization: Bearer <token>' matching the token this server was started with."}
```

No token at all: same 401 response.

Valid token, real MCP handshake + tool calls from a separate `curl`
process (not the server's own process):
```
$ curl -s -i -X POST http://127.0.0.1:8765/mcp \
    -H "Authorization: Bearer spiketoken123" -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"spike-curl","version":"0.1"}}}'
HTTP/1.1 200 OK
mcp-session-id: 873a1d52381041bfb6101ae90eb84911
content-type: text/event-stream

event: message
data: {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05", ...
  "serverInfo":{"name":"cadence","version":"1.29.1"},
  "instructions":"Cadence is a local-first todo store. Use add_task to create work, ..."}}

$ curl ... -H "Mcp-Session-Id: 873a1d52381041bfb6101ae90eb84911" \
    -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'
HTTP/1.1 202 Accepted

$ curl ... -H "Mcp-Session-Id: 873a1d52381041bfb6101ae90eb84911" \
    -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"add_task","arguments":{"title":"Reach Cadence from Claude web via HTTP"}}}'
event: message
data: {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"{\n  \"ok\": true,\n  \"task\": {\n    \"id\": 1,\n    \"title\": \"Reach Cadence from Claude web via HTTP\", ...

$ curl ... -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"list_tasks","arguments":{}}}'
event: message
data: {"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"{\n  \"ok\": true,\n  \"tasks\": [\n    {\n      \"id\": 1,\n      \"title\": \"Reach Cadence from Claude web via HTTP\", ...  \"count\": 1\n}"}],"isError":false}}
```

Post-spike sanity: stdio path re-verified untouched (`cadence add`/`cadence
list` over the default no-flag path both still work), `cadence mcp --help`
shows the new flags alongside stdio, and the full existing suite is green:
`79 passed in 11.82s` (`venv/bin/python -m pytest -q`).

## Verdict: proven, not disproven

The transport works. A separate process with only a URL and a token can
create and read real tasks in the same store the CLI and stdio-MCP use,
with no changes to the store, sync, or history model, and zero risk to
the stdio path or the published package (this code isn't on PyPI).

## What's genuinely hard (the parts that make this a spike, not a ship)

1. **Exposing a home machine to the internet safely.** `--host
   127.0.0.1` (this spike's default) only reaches the same machine —
   useless for Claude web/mobile, which need to reach it from Anthropic's
   servers, not from localhost. To actually solve the chairman's problem,
   the user must expose the port: a Tailscale/Cloudflare Tunnel
   (recommended — no open inbound port, no public IP) or an open port +
   reverse proxy with TLS (Streamable HTTP over plain HTTP is a
   non-starter: the bearer token would go over the wire in clear text).
   That's real setup work outside Cadence's own code, and the honest
   framing for users is "here's how to tunnel it," not "cadence handles
   this for you."
2. **Whether the connector setup flow is walkable by a non-technical
   user.** Claude's custom-connector UI wants a URL, and for a bearer
   token it needs either OAuth or a manually-pasted header — verified
   from `mcp`'s `Settings.auth` field existing (full OAuth provider
   plumbing, not exercised in this spike) that FastMCP supports OAuth,
   but wiring it is materially more work than the token check built
   here, and stock custom-connector token entry vs. OAuth support has
   shifted release-to-release, so this needs a real walkthrough against
   the current Claude UI (not just doc-reading) before promising it
   works, e.g. Claude web's connector setup as of testing here is not
   verified against a running Claude session — a real limitation of this
   spike, not code.
3. **Session/process lifetime.** `uvicorn.run` here is single-process,
   foreground, in-memory session state — fine for one laptop, one user.
   Restarting the process drops in-flight sessions (client just
   reconnects and re-initializes; no task data is lost, since that lives
   in sqlite, not in the session). Needs a documented "run this as a
   background service" story (systemd unit / launchd plist / `pm2`-style
   supervisor) for it to survive a laptop sleep/wake cycle unattended.
4. **Multiple concurrent remote clients writing to one store.** The
   sqlite store already handles concurrent local writers; this spike
   didn't add or test multi-client HTTP concurrency specifically, though
   there's no structural reason it would behave differently from two
   local processes today.

## What's straightforward (already proven above)

- The transport itself: `FastMCP.streamable_http_app()` needed zero
  changes to any `@mcp.tool()` function — the entire existing tool
  surface (all 9 tools, same error contract) is already exposed
  correctly; this spike verified 2 of them live, structurally all 9 go
  through the same code path.
- Bearer-token auth as a floor: ~40 lines of ASGI middleware, no new
  dependency, ships in the same package.
- Coexistence with stdio: zero interference verified (full suite green,
  stdio path manually re-checked).

## Estimate to production-quality, opt-in, non-breaking

Roughly **3-4 additional days** of focused work:
- Day 1: harden the token check (constant-time compare, not `!=`),
  require `--host` to be explicit non-loopback (refuse silent
  `0.0.0.0` by accident), write the "expose this safely" doc section
  (Tailscale/Cloudflare Tunnel walkthrough with actual screenshots),
  add a background-service doc/example (systemd unit).
- Day 1-2: a real walkthrough connecting an actual Claude web session
  through a real tunnel to a real running `cadence mcp --http`, fixing
  whatever the connector UI needs that this spike didn't test (this is
  the highest-uncertainty item — could be half a day, could reveal a
  real blocker).
- Day 1: tests — an HTTP-transport-specific test module exercising the
  same tool surface tests already exercise, plus the 401 path, without
  touching the stdio test suite or the ten-step transcript's evidence
  (which stays on stdio, unaffected either way).
- 0.5 day: CI wiring so the new tests run on the clean GitHub runner
  alongside the existing suite (additive job, doesn't touch the
  publish-and-drive-installed-package E2E test).
- Explicitly NOT in scope for a v1 of this: OAuth, multi-user auth, a
  hosted relay — all of that reintroduces the accounts/billing/uptime
  cost the project is deliberately biased against. A single shared
  bearer token, self-hosted, self-exposed, is the right-sized answer to
  "let *my own* other devices reach *my own* store."

## Recommendation

Go, scoped narrowly: implement the hardened version above as an opt-in
flag, ship it as a minor version bump once the real Claude-web
walkthrough is done, and keep it entirely separate from the stdio path
and the ten-step transcript (which should keep testing stdio, since
that's what the published package's primary, zero-setup agent surface
is). Do not block the 6 Nov finish line on this — it's additive
hardening for the chairman's own workflow, not a requirement of any of
the three finish-line tests.
