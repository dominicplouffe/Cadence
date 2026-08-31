# Independent Red Team pass: 0.2.15 DNS-rebinding-protection-off fix

Date: 2026-08-31. Run by Dov Ferreira (Red Team), independent of Rafael's
own verification in `tunnel-fix-verified-0.2.15.md` — separate fresh venv,
separate fresh store, separate live tunnel, adversarial inputs Rafael's
pass didn't try.

## Setup

Fresh venv, installed straight from PyPI, no local checkout on `PATH`:

```
$ python3 -m venv venv
$ ./venv/bin/pip install cadence-todo==0.2.15
$ ./venv/bin/pip show cadence-todo | grep Version
Version: 0.2.15
```

Fresh store, fresh config, fresh bearer token:

```
$ export CADENCE_CONFIG_HOME=.../redteam_0215_indep/config
$ export CADENCE_DB_PATH=.../redteam_0215_indep/cadence.db
$ ./venv/bin/cadence add "Red Team 0.2.15 verify seed task"
Added #1: Red Team 0.2.15 verify seed task
$ ./venv/bin/cadence mcp --show-token
52d9e6a2844143a32f3762f7832f103e623a96417ff493c2f54efe75e07d9fd9
$ ./venv/bin/cadence mcp --http --port 8767
[cadence mcp --http] listening on http://127.0.0.1:8767/mcp ...
```

Real cloudflared Quick Tunnel (`cloudflared` v2026.8.3):

```
$ ./cloudflared tunnel --url http://127.0.0.1:8767
https://reveal-weighted-preferred-construction.trycloudflare.com
```

## Check 1: correct token + spoofed/mismatched Host, over the tunnel

Sent `Host: evil-attacker.example.com` (correct token) and separately
(wrong token) through the real Cloudflare Quick Tunnel:

```
$ curl -i https://reveal-....trycloudflare.com/mcp -H "Authorization: Bearer <correct>" -H "Host: evil-attacker.example.com" ...
HTTP/2 403 Forbidden   (server: cloudflare, no cadence response at all)

$ curl -i https://reveal-....trycloudflare.com/mcp -H "Authorization: Bearer wrong" -H "Host: evil-attacker.example.com" ...
HTTP/2 403 Forbidden   (server: cloudflare, no cadence response at all)
```

**Finding, not a defect:** over a real `trycloudflare.com` Quick Tunnel,
Cloudflare's own edge validates the `Host`/SNI against the tunnel's
assigned hostname and 403s a mismatch *before it ever reaches Cadence's
origin* — `cadence-todo` never sees these requests, so this specific
vector can't be used to test the app's own Host handling over this
transport. This is Cloudflare's protection, not Cadence's; it would not
apply to a different reverse proxy, a raw port-forward, or another tunnel
provider. Re-tested with the edge bypassed (Check 1 below, direct to
origin) to actually exercise the app's own logic.

Re-run direct to `127.0.0.1:8767` (bypasses Cloudflare's edge entirely —
this is the honest test of "did disabling DNS-rebinding protection widen
the app's own attack surface"):

```
$ curl -i http://127.0.0.1:8767/mcp -H "Authorization: Bearer <correct>" -H "Host: evil-attacker.example.com" ...
HTTP/1.1 200 OK — real MCP initialize response

$ curl -i http://127.0.0.1:8767/mcp -H "Authorization: Bearer wrong-token" -H "Host: evil-attacker.example.com" ...
HTTP/1.1 401 Unauthorized — {"ok":false,"error":"unauthorized",...}
```

**Pass.** With DNS-rebinding protection off, `Host` is irrelevant to the
auth decision either way: correct token gets in regardless of Host,
wrong token is blocked regardless of Host. BearerAuth alone is the gate,
exactly as claimed. Spoofing Host does not let a bad token through.

## Check 2: wrong / missing / empty token, over the tunnel

```
$ curl -i https://reveal-....trycloudflare.com/mcp -H "Authorization: Bearer wrongtoken123" ...
HTTP/2 401 — {"ok":false,"error":"unauthorized","message":"Missing or wrong bearer token.",...}

$ curl -i https://reveal-....trycloudflare.com/mcp  (no Authorization header) ...
HTTP/2 401 — same clean JSON body

$ curl -i https://reveal-....trycloudflare.com/mcp -H "Authorization: Bearer " ...
HTTP/2 401 — same clean JSON body
```

**Pass.** All three get a clean 401 JSON body, same shape as the
documented contract. No stack trace, no HTML error page, no difference
in behaviour between wrong/missing/empty.

## Check 3: unicode and very-long Host header

Tried a Host header with a raw UTF-8 emoji byte sequence and an
8000-character Host, both with correct and wrong tokens. Over the tunnel,
Cloudflare's edge itself rejects these before reaching Cadence (`400
Bad Request` for the unicode case, `403 Forbidden` for the long one) —
again testing Cloudflare's edge, not the app. Direct to origin:

```
$ curl -i http://127.0.0.1:8767/mcp -H "Authorization: Bearer <correct>" -H "Host: attacker😈.example.com" ...
HTTP/1.1 200 OK — real MCP response

$ curl -i http://127.0.0.1:8767/mcp -H "Authorization: Bearer wrongtoken123" -H "Host: attacker😈.example.com" ...
HTTP/1.1 401 Unauthorized — clean JSON

$ curl -i http://127.0.0.1:8767/mcp -H "Authorization: Bearer <correct>" -H "Host: aaa...a(8000 chars).example.com" ...
HTTP/1.1 200 OK — real MCP response

$ curl -i http://127.0.0.1:8767/mcp -H "Authorization: Bearer wrongtoken123" -H "Host: aaa...a(8000 chars).example.com" ...
HTTP/1.1 401 Unauthorized — clean JSON
```

**Pass.** No crash (no 500, no connection reset), no bypass, no
divergence in behaviour with unicode or oversized Host values. Checked
`server.log` after this whole pass for any traceback/exception/500 —
none. A follow-up sanity request after the abuse pass still returned 200
with a valid token, and the on-disk store (`cadence list`) still showed
only the original seed task, unaffected.

## Check 4: local 127.0.0.1 path

```
$ curl -i http://127.0.0.1:8767/mcp -H "Authorization: Bearer badlocaltoken" ...
HTTP/1.1 401 Unauthorized

$ curl -i http://127.0.0.1:8767/mcp -H "Authorization: Bearer 52d9e6a2844143a32f3762f7832f103e623a96417ff493c2f54efe75e07d9fd9" ...
HTTP/1.1 200 OK — real MCP response
```

**Pass.** Local path unchanged by the fix: bad token still 401s, correct
token still works. DNS-rebinding protection being off did not widen the
*local* attack surface either — the boundary was, and remains, the
token, not the Host header, at every reachable point.

## Check 5: re-run Noor's exact original 421 repro

```
$ curl -i https://reveal-....trycloudflare.com/mcp  (no token) ...
HTTP/2 401 — clean JSON, not 421

$ curl -i https://reveal-....trycloudflare.com/mcp -H "Authorization: Bearer <correct>" ...
HTTP/2 200 — real MCP initialize response, not 421
```

**Pass.** The original bug (`421 Invalid Host header` on a correct-token
tunnel request) does not reproduce. Same live-tunnel shape Noor used,
independent tunnel instance, independent token, same result as Rafael's
own verification.

## Summary

| # | Check | Result |
|---|---|---|
| 1 | correct/wrong token + spoofed Host, tunnel + direct-to-origin | Pass (Cloudflare edge blocks spoofed Host on the tunnel path before Cadence ever sees it; direct-to-origin confirms BearerAuth alone is the gate) |
| 2 | wrong/missing/empty token, tunnel | Pass — clean 401, no stack trace, no difference by failure mode |
| 3 | unicode / 8000-char Host, tunnel + direct | Pass — no crash, no bypass |
| 4 | local 127.0.0.1, bad/correct token | Pass — unchanged, token is still the only gate |
| 5 | original 421 repro, tunnel | Pass — does not reproduce; 401 without token, 200 with it |

No new hole traded for the old one. The fix's premise — BearerAuth, not
the Host header, is the actual security boundary for `--http` mode —
holds under adversarial input, including inputs Rafael's own
verification didn't try (spoofed/unicode/oversized Host, direct-to-origin
bypassing Cloudflare's own edge filtering). One process note, not a
defect: a Cloudflare Quick Tunnel's edge does its own Host validation, so
testing Host-header attacks *only* over `trycloudflare.com` undersells
the app's own exposure — the direct-to-origin runs above are the ones
that actually exercised `transport_security`.

Tunnel and server processes torn down after capture; nothing left
running.
