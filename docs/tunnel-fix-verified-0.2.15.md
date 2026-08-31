# Real transcript: tunnel fix verified against the published `cadence-todo==0.2.15`

Date: 2026-08-31. Run by Rafael (Build), fixing Noor's tunnel finding
(`TUNNEL_FINDING.md`).

## Fix

`src/cadence/mcp_server.py` now constructs `FastMCP(...)` with
`transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)`.
Reasoning is in a code comment at the construction site. Root cause and
fix rationale are unchanged from `TUNNEL_FINDING.md`: BearerAuth, not the
MCP SDK's Host-header check, is this app's actual security boundary for
`--http` mode.

Shipped as `cadence-todo` 0.2.15, commit `4a31a87` on `main`.

- `Publish` workflow: `completed`/`success` for `4a31a87`
  (https://github.com/dominicplouffe/Cadence/actions/runs/33415351524).
- `CI` workflow: first attempt's `pypi-install-and-drive` job failed on
  the documented publish/CI race (its 40x15s wait loop's own `pip index`
  check passed before the PyPI CDN had actually propagated the wheel to
  `pip install` -- same race 0.2.11-0.2.14 hit); re-ran via
  `POST /repos/dominicplouffe/Cadence/actions/runs/33415351518/rerun-failed-jobs`
  once `pip install cadence-todo==0.2.15` succeeded locally in a scratch
  venv, and run `33415351518` is now `completed`/`success` for `4a31a87`
  (https://github.com/dominicplouffe/Cadence/actions/runs/33415351518).

## Real tunnel verification (published package, not the local checkout)

Fresh venv, installed straight from PyPI:

```
$ python3 -m venv /tmp/verify_pypi_venv_0215
$ pip install cadence-todo==0.2.15
Successfully installed ... cadence-todo-0.2.15 mcp-1.29.1 ...
$ pip show cadence-todo | grep Version
Version: 0.2.15
```

Fresh store, fresh config:

```
$ export CADENCE_CONFIG_HOME=.../verify_0215/config
$ export CADENCE_DB_PATH=.../verify_0215/cadence.db
$ cadence add "Ship the README (0.2.15 verify)"
Added #1: Ship the README (0.2.15 verify)
$ cadence mcp --show-token
821837ad2db0a531a07cdac1d520f6d18e5156a93fc22cac69d931234d692a5b
$ cadence mcp --http --port 8766
[cadence mcp --http] listening on http://127.0.0.1:8766/mcp -- bearer token required on every request
```

Real cloudflared Quick Tunnel (binary `cloudflared` v2026.8.3, downloaded
straight from Cloudflare's GitHub releases, not simulated):

```
$ cloudflared tunnel --url http://127.0.0.1:8766
...
Your quick Tunnel has been created!
https://component-towns-postal-passes.trycloudflare.com
```
(Full log: `verify_0215/tunnel.log`, `verify_0215/server.log`.)

### 1. No token, over the tunnel -- still 401, as documented

```
$ curl -sS -i https://component-towns-postal-passes.trycloudflare.com/mcp \
    -X POST -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{...}}'

HTTP/2 401
{"ok":false,"error":"unauthorized","message":"Missing or wrong bearer token.", ...}
```

### 2. Correct token, over the tunnel -- was 421 before the fix, now a real `initialize` response

```
$ curl -sS -i https://component-towns-postal-passes.trycloudflare.com/mcp \
    -X POST -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -H "Authorization: Bearer 821837ad2db0a531a07cdac1d520f6d18e5156a93fc22cac69d931234d692a5b" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"verify-client","version":"1.0"}}}'

HTTP/2 200
mcp-session-id: 9c63210fff6241b6b646a036f5a72e0c
content-type: text/event-stream

event: message
data: {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-06-18",
  "capabilities":{...},
  "serverInfo":{"name":"cadence","version":"1.29.1"},
  "instructions":"Cadence is a local-first todo store. ..."}}
```

**200, not 421. This is the fix.**

### 3. A real tool call over the same tunnel session

```
$ curl -sS -i https://component-towns-postal-passes.trycloudflare.com/mcp \
    -X POST -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -H "Authorization: Bearer 821837ad2db0a531a07cdac1d520f6d18e5156a93fc22cac69d931234d692a5b" \
    -H "mcp-session-id: 9c63210fff6241b6b646a036f5a72e0c" \
    -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"add_task","arguments":{"title":"Confirmed: tunnel path works (0.2.15)"}}}'

HTTP/2 200
event: message
data: {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":
  "{\n  \"ok\": true,\n  \"task\": {\n    \"id\": 2,\n    \"title\":
  \"Confirmed: tunnel path works (0.2.15)\", ...}}"}],"isError":false}}
```

Confirmed against the local store directly (not the HTTP session's own
view):

```
$ cadence list
  [ ]    1   Ship the README (0.2.15 verify)
  [ ]    2   Confirmed: tunnel path works (0.2.15)
```

Task #2, created entirely through the real public tunnel, is the same
task a local `cadence list` sees -- one store, reached a second way.

## Regression test

`tests/test_http_transport.py::test_http_transport_auth_gate_then_authorized_round_trip_same_store`
now repeats the full authorized round trip (initialize + add_task +
decompose_task + reprioritise_task + why_task + undo) and the
wrong-token check with a `Host: some-name.trycloudflare.com` header, on
top of the existing localhost-Host coverage, and asserts neither 421s.
(Combined into the existing test, not a separate one, because FastMCP's
`StreamableHTTPSessionManager.run()` can only be called once per
process -- see the comment above that test.)

```
$ pytest tests/test_http_transport.py -v
...
tests/test_http_transport.py::test_http_transport_auth_gate_then_authorized_round_trip_same_store PASSED
5 passed
```

## Status

Fixed, shipped as `cadence-todo` 0.2.15, CI green
(https://github.com/dominicplouffe/Cadence/actions/runs/33415351518),
verified against a real live Cloudflare Quick Tunnel and the real
published PyPI package. Reported to Noor (Surface) -- the README "Get
Started" tunnel path is unblocked.
