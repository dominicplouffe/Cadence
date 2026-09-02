"""Regression tests for `cadence mcp --http` (docs/dogfooding-log.md:
remote HTTP MCP transport, closing the VSCode/web/phone gap the chairman
named 2026-08-29).

These exercise the real network transport end to end -- a live uvicorn
server in a background thread, a genuinely separate HTTP client (the mcp
SDK's own streamable-http client, not the stdio path) -- not the bare
Python functions, because the point of this feature is the wire protocol
and the bearer-token gate in front of it.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import stat
import subprocess
import sys
import threading
import time
from importlib.metadata import version as _pkg_version

import httpx
import pytest
import uvicorn

from cadence import __version__ as _cadence_version
from cadence.registry import get_or_create_http_token, http_token_path
from cadence.store import Store

_installed_mcp_sdk_version = _pkg_version("mcp")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _LiveServer:
    """Runs `cadence.mcp_server._make_http_app` on a real socket in a
    background thread for the duration of one test, then shuts it down."""

    def __init__(self, token: str):
        from cadence.mcp_server import _make_http_app

        self.port = _free_port()
        self.base_url = f"http://127.0.0.1:{self.port}/mcp"
        app = _make_http_app(token)
        config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        deadline = time.time() + 5
        while time.time() < deadline and not getattr(self.server, "started", False):
            time.sleep(0.02)
        if not getattr(self.server, "started", False):
            raise RuntimeError("test HTTP MCP server did not start in time")
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=5)


@pytest.fixture
def http_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CADENCE_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("CADENCE_DB_PATH", str(tmp_path / "cadence.db"))
    return tmp_path


# --- envelope error classification --------------------------------------


def test_classify_envelope_error_never_blames_the_caller_for_a_5xx():
    from cadence.mcp_server import _classify_envelope_error

    # 0.2.17 independent Red Team pass: any status_code >= 500 must be
    # classified as a server fault, distinctly from a 4xx client mistake
    # -- regardless of what the SDK's own message text happens to say
    # (here, the SDK's real generic message for an uncaught exception).
    code, hint = _classify_envelope_error(500, "Error handling POST request")
    assert code == "server_error"
    # The old hint told the caller to retry the identical request -- the
    # one thing that cannot help a server-side fault. The new one must
    # say plainly that retrying the same request will not help.
    assert "will not help" in hint.lower() or "cannot help" in hint.lower()

    # The concrete old bug: this text ("Error handling POST request")
    # matches none of the 4xx substring patterns, so pre-fix it fell all
    # the way through to the generic 4xx default, "malformed_request".
    assert code != "malformed_request"

    # Any other 5xx must also be classified this way, not just 500.
    code2, hint2 = _classify_envelope_error(502, "Bad Gateway")
    assert code2 == "server_error"
    assert hint2 == hint

    # A genuine 4xx must still classify as before -- this fix must not
    # swallow ordinary client-mistake classification.
    code3, _ = _classify_envelope_error(400, "Parse error: some detail")
    assert code3 == "malformed_json"


# --- token generation/storage ------------------------------------------


def test_get_or_create_http_token_persists_and_is_reused(http_env):
    first = get_or_create_http_token()
    second = get_or_create_http_token()
    assert first == second
    assert len(first) == 64  # 32 random bytes, hex-encoded
    int(first, 16)  # hex, not e.g. base64 -- raises ValueError otherwise


def test_get_or_create_http_token_file_is_owner_only(http_env):
    get_or_create_http_token()
    mode = stat.S_IMODE(http_token_path().stat().st_mode)
    assert mode == stat.S_IRUSR | stat.S_IWUSR, oct(mode)


def test_two_projects_get_different_tokens_when_config_home_differs(tmp_path, monkeypatch):
    monkeypatch.setenv("CADENCE_CONFIG_HOME", str(tmp_path / "a"))
    token_a = get_or_create_http_token()
    monkeypatch.setenv("CADENCE_CONFIG_HOME", str(tmp_path / "b"))
    token_b = get_or_create_http_token()
    assert token_a != token_b


# --- CLI wiring ----------------------------------------------------------


def _cli_env(config_home, db_path):
    env = {**os.environ, "CADENCE_CONFIG_HOME": str(config_home), "CADENCE_DB_PATH": str(db_path)}
    env.pop("CADENCE_MCP_TOKEN", None)
    return env


def test_cli_show_token_prints_generated_token_without_starting_a_server(tmp_path):
    env = _cli_env(tmp_path / "config", tmp_path / "cadence.db")
    result = subprocess.run(
        [sys.executable, "-m", "cadence.cli", "mcp", "--show-token"],
        capture_output=True, text=True, env=env, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    token_on_disk = (tmp_path / "config" / "mcp_http_token").read_text().strip()
    assert result.stdout.strip() == token_on_disk
    assert len(token_on_disk) == 64


# --- HTTP transport: auth gate, then an authorized round trip against
# the same store as the local CLI. One live server per test process:
# FastMCP's underlying StreamableHTTPSessionManager is a singleton with a
# run-once lifespan, so a second `_make_http_app()`/uvicorn boot in the
# same process fails on startup -- one server, exercised in sequence,
# matches how it is actually deployed (started once, hit many times).


def test_http_transport_auth_gate_then_authorized_round_trip_same_store(http_env, tmp_path):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    token = get_or_create_http_token()
    # 0.2.14 regression (Noor's tunnel finding, docs/dogfooding-log.md): a
    # real tunnel (Cloudflare Quick Tunnel, Tailscale Funnel) forwards the
    # request with Host: <random>.trycloudflare.com, never 127.0.0.1 or
    # localhost. The MCP SDK used to auto-attach DNS-rebinding Host-header
    # protection scoped to those two, ahead of BearerAuth, so this exact
    # Host 421'd even with the correct token. mcp_server.py now disables
    # that SDK-level check (see the comment on the FastMCP(...)
    # construction there) since BearerAuth is the actual security boundary
    # for --http mode. Every request below is repeated with this tunnel-
    # shaped Host header to prove the fix, not just asserted once in
    # isolation.
    tunnel_host = "some-name.trycloudflare.com"

    async def _round_trip(base_url: str, extra_headers: dict) -> dict:
        # streamablehttp_client (not the newer streamable_http_client,
        # which in this SDK version takes an httpx.AsyncClient instead of
        # a plain headers dict and is more ceremony for no behavior change
        # here) -- this is the same call shape a real remote client (e.g.
        # Claude web/mobile) makes against this mcp SDK version.
        headers = {"Authorization": f"Bearer {token}", **extra_headers}
        async with streamablehttp_client(base_url, headers=headers) as (
            read,
            write,
            _,
        ):
            async with ClientSession(read, write) as session:
                init_result = await session.initialize()

                async def call(name, args):
                    result = await session.call_tool(name, args)
                    return json.loads(result.content[0].text)

                added = await call("add_task", {"title": "Remote task via HTTP MCP", "priority": "high"})
                tid = added["task"]["id"]
                await call("decompose_task", {"id": tid, "into": ["step one", "step two"]})
                await call("reprioritise_task", {"id": tid, "priority": "low", "reason": "downgrade via http"})
                why = await call("why_task", {"id": tid})
                undone = await call("undo", {})
                return {
                    "id": tid,
                    "why": why,
                    "undone": undone,
                    "server_version": init_result.serverInfo.version,
                }

    with _LiveServer(token) as live:
        no_token = httpx.post(
            live.base_url,
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json"},
        )
        wrong_token = httpx.post(
            live.base_url,
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "Authorization": "Bearer wrong-token-entirely",
            },
        )
        # Wrong token AND a tunnel-shaped Host: must still be a clean 401,
        # not a 421 -- disabling DNS-rebinding protection must not also
        # have weakened BearerAuth, which still has to see and reject a
        # bad token regardless of Host.
        wrong_token_tunnel_host = httpx.post(
            live.base_url,
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "Authorization": "Bearer wrong-token-entirely",
                "Host": tunnel_host,
            },
        )
        outcome = asyncio.run(_round_trip(live.base_url, {}))
        # The same authorized round trip again, this time with every
        # request's Host header set to a tunnel hostname -- the case that
        # 421'd before the fix.
        tunnel_outcome = asyncio.run(_round_trip(live.base_url, {"Host": tunnel_host}))

        # 0.2.12 Red Team pass, finding #6 (docs/dogfooding-log.md): these
        # four are malformed at the HTTP-transport-envelope level -- below
        # the session/tool-call layer BearerAuth and cadence's own tool
        # functions already cover -- rejected by the `mcp` SDK's own
        # request parsing before a tool (or even a session) ever exists.
        # A valid token is presented on every one of these; only the
        # request itself is malformed.
        good_headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
        bad_json_body = httpx.post(
            live.base_url,
            content=b'{"jsonrpc": "2.0", "id": 1, "method": "ping"',  # truncated -- invalid JSON
            headers=good_headers,
        )
        missing_method = httpx.post(
            live.base_url,
            json={"jsonrpc": "2.0", "id": 1},  # valid JSON, no "method" field
            headers=good_headers,
        )
        missing_accept = httpx.post(
            live.base_url,
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={k: v for k, v in good_headers.items() if k != "Accept"},
        )
        oversized_body = httpx.post(
            live.base_url,
            content=json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {"pad": "x" * 5_000_000}}
            ).encode(),
            headers=good_headers,
        )
        # 0.2.17 independent Red Team pass (docs/dogfooding-log.md): a
        # ~2KB body nested >=1000 levels deep used to make the `mcp` SDK's
        # own json.loads(body) raise an uncaught RecursionError, which its
        # outer handler turned into a bare 500 "Error handling POST
        # request" -- and _classify_envelope_error's fallback bucket
        # (never checking status_code) shaped that identically to an
        # ordinary client mistake, with a hint telling the caller to
        # retry the one thing (fixing their own request) that could not
        # help. Fixed two ways, both exercised here: (1) nesting this deep
        # is now bounded before it ever reaches the SDK's json.loads, so
        # it degrades to the same clean 400 malformed_json path ordinary
        # bad JSON already takes; (2) belt-and-braces, _classify_envelope_
        # error now classifies *any* status_code >= 500 as a distinct
        # "server_error", never "malformed_request", so a real server
        # fault (this one or any other) can never be mis-blamed on the
        # caller. This deliberately mirrors Dov's exact repro: depth 1000
        # is where the old code first reproduced the RecursionError.
        deeply_nested = "[" * 1000 + "1" + "]" * 1000
        deeply_nested_body = httpx.post(
            live.base_url,
            content=(
                '{"jsonrpc": "2.0", "id": 1, "method": "ping", "params": '
                + deeply_nested
                + "}"
            ).encode(),
            headers=good_headers,
        )
        # Dov's independent-verify finding on 0.2.20 (docs/dogfooding-log.md):
        # a *flat*, non-nested JSON integer literal with more digits than
        # Python's int<->str conversion limit (4300) made stdlib json.loads
        # raise ValueError inside int() before cadence's code ever ran --
        # unrelated to the nesting-depth guard above (no nesting at all
        # here), so it slipped past that fix and surfaced as a correctly-
        # classified but misleadingly-worded 500 ("editing the request will
        # not help", which was false: shrinking the number fixes it every
        # time). 4300 digits must still parse fine; 4301 is the exact
        # boundary Dov confirmed triggers it.
        oversized_int_body = httpx.post(
            live.base_url,
            content=(
                '{"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {"x": '
                + ("9" * 4301)
                + "}}"
            ).encode(),
            headers=good_headers,
        )
        oversized_int_body_at_boundary = httpx.post(
            live.base_url,
            content=(
                '{"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {"x": '
                + ("9" * 4300)
                + "}}"
            ).encode(),
            headers=good_headers,
        )

    for resp in (no_token, wrong_token, wrong_token_tunnel_host):
        assert resp.status_code == 401, (resp.status_code, resp.text)
        body = resp.json()
        assert body == {
            "ok": False,
            "error": "unauthorized",
            "message": "Missing or wrong bearer token.",
            "hint": (
                "Send 'Authorization: Bearer <token>' matching the token this "
                "server was started with (see `cadence mcp --http --show-token`)."
            ),
        }

    for result in (outcome, tunnel_outcome):
        assert result["why"]["ok"] is True
        assert result["undone"]["ok"] is True
        # 0.2.12 Red Team pass, finding #7: initialize's serverInfo.version
        # used to be the `mcp` SDK's own version (FastMCP has no version=
        # kwarg in this pinned SDK release), not Cadence's, so an agent
        # doing the standard handshake could not tell which Cadence
        # feature set it was talking to.
        assert result["server_version"] == _cadence_version
        assert result["server_version"] != _installed_mcp_sdk_version

    for resp, expected_status, expected_error in (
        (bad_json_body, 400, "malformed_json"),
        (missing_method, 400, "invalid_request"),
        (missing_accept, 406, "not_acceptable"),
        (oversized_body, 413, "request_too_large"),
        (deeply_nested_body, 400, "malformed_json"),
        (oversized_int_body, 400, "malformed_json"),
    ):
        assert resp.status_code == expected_status, (resp.status_code, resp.text)
        assert resp.headers["content-type"].startswith("application/json"), resp.headers
        body = resp.json()
        assert body["ok"] is False, body
        assert body["error"] == expected_error, body
        assert body["message"], body
        assert body["hint"], body

    # The whole point: this is a 4xx now, with a hint that matches the real
    # fix (shrink the number) -- not the old 500 "editing the request will
    # not help", which was false for this input.
    oversized_int_hint = oversized_int_body.json()["hint"].lower()
    assert "will not help" not in oversized_int_hint, oversized_int_body.json()

    # 4300 digits is the exact boundary Dov confirmed still parses fine --
    # must not be caught by the new pre-validation (only 4301+ is). It falls
    # through to an ordinary session_error, since this request never called
    # 'initialize' first -- unrelated to the digit limit, just confirming
    # this body was accepted as valid JSON.
    assert oversized_int_body_at_boundary.status_code == 400, (
        oversized_int_body_at_boundary.status_code,
        oversized_int_body_at_boundary.text,
    )
    assert oversized_int_body_at_boundary.json()["error"] == "session_error", (
        oversized_int_body_at_boundary.json()
    )

    # Not the HTTP session's own view -- a fresh Store opened directly on
    # the same CADENCE_DB_PATH the CLI would use, proving one store, not a
    # divergent copy the HTTP transport kept to itself.
    store = Store()
    for result in (outcome, tunnel_outcome):
        task = store.get(result["id"])
        assert task.title == "Remote task via HTTP MCP"
        # undo reverted the reprioritise (low -> back to high), leaving the
        # decompose in place, exactly as a local `cadence undo` would.
        assert task.priority == "high"
        children = [t for t in store.list(status="all") if t.parent_id == result["id"]]
        assert sorted(t.title for t in children) == ["step one", "step two"]
