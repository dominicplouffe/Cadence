"""External verification for the 0.2.21 fix (docs/dogfooding-log.md): a
bare JSON integer literal over Python's int<->str digit-conversion limit
(4300 digits) used to crash stdlib json.loads inside the `mcp` SDK's
parser, surfacing as a 500 whose hint ("editing the request will not
help") was false -- shrinking the number fixes it every time.

This does what Dov did by hand against the real published package: pip
install the latest cadence-todo from the real PyPI index into a fresh
temp venv (never the local checkout), start `cadence mcp --http` from
that installed console script, and POST a 4301-digit-integer JSON-RPC
body at it over a real HTTP connection. Asserts the response is now a
4xx malformed_json, not the old 500 with the misleading hint -- and that
the exact boundary (4300 digits) still parses fine.

Exits 0 and prints "PASS" on success, non-zero with a clear reason
otherwise. Deliberately does not import anything from `src/cadence` --
the whole point is to drive the artifact a stranger would actually
install, not this checkout's source tree.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def _fail(msg: str) -> "typing.NoReturn":  # type: ignore[name-defined]
    print(f"FAIL: {msg}")
    sys.exit(1)


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="cadence-verify-oversized-int-"))
    venv_dir = tmp / "venv"
    print(f"Creating fresh venv at {venv_dir} ...")
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
    venv_python = venv_dir / "bin" / "python"
    venv_pip = venv_dir / "bin" / "pip"

    print("Installing latest cadence-todo from the real PyPI index (no local source) ...")
    subprocess.run(
        [str(venv_pip), "install", "--no-cache-dir", "--upgrade", "pip"],
        check=True,
        capture_output=True,
    )
    # PyPI's Simple index (what `pip install` resolves against) sits behind
    # a CDN whose edge nodes can briefly disagree just after a publish --
    # the exact race ci.yml's pypi-install-and-drive job already retries
    # around (docs/dogfooding-log.md, 0.2.19/0.2.20). Mirror that here with
    # a short retry-with-backoff instead of failing on one unlucky edge hit.
    install = None
    for attempt in range(1, 6):
        install = subprocess.run(
            [str(venv_pip), "install", "--no-cache-dir", "cadence-todo"],
            capture_output=True,
            text=True,
        )
        if install.returncode == 0:
            break
        print(f"pip install attempt {attempt} failed, retrying in 15s ...")
        time.sleep(15)
    if install.returncode != 0:
        _fail(f"pip install cadence-todo failed after 5 attempts:\n{install.stdout}\n{install.stderr}")

    show = subprocess.run(
        [str(venv_pip), "show", "cadence-todo"], capture_output=True, text=True, check=True
    )
    version_line = next(l for l in show.stdout.splitlines() if l.startswith("Version:"))
    installed_version = version_line.split(":", 1)[1].strip()
    print(f"Installed cadence-todo=={installed_version} from real PyPI into {venv_dir}")

    cadence_bin = venv_dir / "bin" / "cadence"
    if not cadence_bin.exists():
        _fail(f"no `cadence` console script at {cadence_bin} after install")

    db_path = tmp / "cadence.db"
    config_home = tmp / "config"
    env = {**os.environ}
    env.pop("CADENCE_MCP_TOKEN", None)
    env["CADENCE_DB_PATH"] = str(db_path)
    env["CADENCE_CONFIG_HOME"] = str(config_home)

    token_out = subprocess.run(
        [str(cadence_bin), "mcp", "--show-token"],
        capture_output=True,
        text=True,
        env=env,
    )
    token = token_out.stdout.strip().splitlines()[-1].strip()
    if not token:
        _fail(f"could not read a token from `cadence mcp --show-token`: {token_out.stdout!r}")

    port = 8799
    server = subprocess.Popen(
        [str(cadence_bin), "mcp", "--http", "--port", str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        base_url = f"http://127.0.0.1:{port}/mcp"
        deadline = time.time() + 15
        last_error = None
        while time.time() < deadline:
            try:
                urllib.request.urlopen(urllib.request.Request(base_url, method="GET"), timeout=1)
            except urllib.error.HTTPError:
                # Any HTTP response at all (even a 4xx to a bare GET)
                # means the server is up and listening.
                last_error = None
                break
            except Exception as exc:  # noqa: BLE001 - genuinely any connect failure
                last_error = exc
                time.sleep(0.3)
        else:
            if server.poll() is not None:
                _fail(f"cadence mcp --http exited early:\n{server.stdout.read()}")
            _fail(f"server never came up on {base_url}: {last_error}")

        def _post(digits: int) -> tuple[int, dict]:
            body = (
                '{"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {"x": '
                + ("9" * digits)
                + "}}"
            ).encode()
            req = urllib.request.Request(
                base_url,
                data=body,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                    "Authorization": f"Bearer {token}",
                },
            )
            try:
                resp = urllib.request.urlopen(req, timeout=10)
                return resp.status, json.loads(resp.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())

        status, payload = _post(4301)
        print(f"4301-digit integer -> HTTP {status}, error={payload.get('error')!r}, "
              f"hint={payload.get('hint')!r}")
        if status >= 500:
            _fail(
                f"4301-digit integer literal still crashes to a {status} -- "
                f"fix not present in cadence-todo=={installed_version}: {payload}"
            )
        if not (400 <= status < 500):
            _fail(f"expected a 4xx for the oversized integer, got {status}: {payload}")
        if payload.get("error") != "malformed_json":
            _fail(f"expected error='malformed_json', got {payload.get('error')!r}: {payload}")
        hint = str(payload.get("hint", "")).lower()
        if "will not help" in hint or "cannot help" in hint:
            _fail(f"hint still tells the caller editing won't help, which is false here: {payload}")

        status_ok, payload_ok = _post(4300)
        print(f"4300-digit integer (boundary, must still parse) -> HTTP {status_ok}, "
              f"error={payload_ok.get('error')!r}")
        if status_ok >= 500:
            _fail(f"the exact boundary (4300 digits) now wrongly triggers a 5xx: {payload_ok}")
        if payload_ok.get("error") == "malformed_json":
            _fail(
                "4300 digits (the exact boundary, one under the limit) got "
                f"malformed_json -- over-triggering: {payload_ok}"
            )

        print(f"PASS: cadence-todo=={installed_version} (real PyPI) rejects an oversized "
              "bare JSON integer with a clean 4xx and an accurate hint; the boundary "
              "(4300 digits) still parses fine.")
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
