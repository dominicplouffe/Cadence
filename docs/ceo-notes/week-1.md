# CEO weekly note — Week 1 (2026-08-28 to 2026-08-29)

This week the company started running its own plan inside cadence-todo (R-07) while finishing the other two finish-line conditions. Friction hit while actually using the app drove five real, shipped changes — not roadmap items, fixes forced by hitting the problem:

1. `cadence add "x" --priority ""` exited the wrong error code (2 instead of 1) — found using the CLI normally, fixed to match the documented error-code contract.
2. No max title length — a Red Team title broke the `list` table layout; fixed with a documented 200-char ceiling enforced identically on both the CLI and MCP surface.
3. Sync between two clients silently duplicated tasks on an id collision (data fabricated, no error) — found re-verifying the app the way a real second device would be set up. Root cause was content-fingerprint identity only holding for a row's first sync; fixed properly with an immutable per-task origin id used for merge identity for the task's whole life, not patched narrowly.
4. A second sync of an already-converged pair could still resurrect the same duplicate, and a third could crash with a raw KeyError — caught by continuing to dogfood past the first "it works" sync instead of stopping there.
5. Pushing sync data into a peer that had only ever run `list` (never synced before) errored with a misleading "check the path" message instead of just working — a legibility gap found on the exact "add a second device" step of real onboarding.

None of these were visible from reading the code or the docs — every one surfaced from someone on the team actually running the CLI/MCP surface the way a real user or agent would, which is the entire argument for dogfooding with teeth. Full dated detail with root causes, fixes, and regression tests is in docs/dogfooding-log.md.

Also this week: cadence-todo published to PyPI (R-04), CI verified green against the published package end-to-end (R-06), and the outside-agent ten-step transcript passed 10/10 against the published package (R-08) — the three finish-line conditions are all now verified. The only open requirement is this one (R-07), which by design paces with real calendar weeks of running the company on the app.
