"""
R-08 ten-step transcript driver.

Simulates an agent with NO access to the Cadence repository: it only knows
what `pip install cadence-todo` gives it (CLI --help output, the PyPI
project-page README bundled in the wheel's METADATA, and the MCP tool
schemas returned by list_tools/call_tool). It talks to two independent
Cadence clients (two separate MCP stdio sessions, each with its own
CADENCE_DB_PATH) to exercise "sync across two clients" honestly.

Every action and its exact result is printed with a UTC timestamp; this
script's stdout, captured verbatim, IS the transcript body.
"""
import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack
from datetime import datetime, timezone

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

CADENCE_BIN = sys.argv[1]          # path to the venv's `cadence` executable
DB_A = sys.argv[2]
DB_B = sys.argv[3]
REMOTE = sys.argv[4]


def ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def log(label, payload=None):
    print(f"[{ts()}] {label}")
    if payload is not None:
        print(json.dumps(payload, indent=2, default=str))
    sys.stdout.flush()


def parsed(result):
    """Parse the tool's JSON text payload (this server does not populate
    structuredContent on this mcp version; the contract is the JSON text
    in content[0].text, exactly as each tool's docstring promises)."""
    if not result.content:
        return None
    text = result.content[0].text
    try:
        return json.loads(text)
    except (json.JSONDecodeError, AttributeError):
        return None


async def call(session, step_label, tool, args):
    log(f"PROMPT (agent decides to call): {tool}({json.dumps(args)})   # step: {step_label}")
    result = await session.call_tool(tool, args)
    body = parsed(result)
    out = {
        "isError": result.isError,
        "content": [c.model_dump() if hasattr(c, "model_dump") else str(c) for c in result.content],
        "parsed": body,
    }
    log(f"RESULT of {tool}", out)
    return result.isError, body


async def open_session(stack: AsyncExitStack, db_path, label):
    env = dict(os.environ)
    env["CADENCE_DB_PATH"] = db_path
    params = StdioServerParameters(command=CADENCE_BIN, args=["mcp"], env=env)
    read, write = await stack.enter_async_context(stdio_client(params))
    session = await stack.enter_async_context(ClientSession(read, write))
    await session.initialize()
    log(f"Opened MCP session for {label} (CADENCE_DB_PATH={db_path})")
    return session


async def main():
    log("===== R-08 TEN-STEP TRANSCRIPT START =====")
    log(f"cadence binary: {CADENCE_BIN}")
    log(f"Client A store: {DB_A}")
    log(f"Client B store: {DB_B}")
    log(f"Shared sync remote: {REMOTE}")

    results = {}
    async with AsyncExitStack() as stack:
        a = await open_session(stack, DB_A, "Client A")

        # ---- Step 1: create a task ----
        log("### STEP 1 -- create a task")
        is_err, struct = await call(a, "step1-create", "add_task",
                                     {"title": "Prep the Q4 client offsite", "priority": "med"})
        task1_id = struct["task"]["id"]
        results[1] = (not is_err and struct.get("ok") is True
                      and struct["task"]["title"] == "Prep the Q4 client offsite")
        log(f"STEP 1 VERDICT: {'PASS' if results[1] else 'FAIL'} (created task id={task1_id})")

        # ---- Step 2: schedule it ----
        log("### STEP 2 -- schedule the task")
        is_err, struct = await call(a, "step2-schedule", "schedule_task", {"id": task1_id, "due": "2026-09-15"})
        results[2] = struct.get("ok") is True and struct["task"]["due"] == "2026-09-15"
        log(f"STEP 2 VERDICT: {'PASS' if results[2] else 'FAIL'}")

        # ---- Step 3: decompose a vague request into subtasks ----
        # The tool's own description says: "Cadence does not invent the
        # breakdown -- the caller decides what the subtasks are". So the
        # agent must do the decomposition itself from a vague user request,
        # then hand Cadence the concrete titles.
        vague_request = "Sort out the offsite somehow, I don't want to think about it"
        log(f'USER REQUEST (vague, given to agent out of band): "{vague_request}"')
        log("### STEP 3 -- agent decomposes the vague request into subtask titles, "
            "then calls decompose_task to link them under task 1")
        agent_breakdown = ["Book the venue", "Send calendar invites", "Order catering"]
        log(f"Agent's own breakdown (not produced by the tool): {agent_breakdown}")
        is_err, struct = await call(a, "step3-decompose", "decompose_task",
                                     {"id": task1_id, "into": agent_breakdown})
        results[3] = (struct.get("ok") is True and len(struct.get("subtasks", [])) == 3
                      and [t["title"] for t in struct["subtasks"]] == agent_breakdown)
        subtask_ids = [t["id"] for t in struct.get("subtasks", [])] if results[3] else []
        log(f"STEP 3 VERDICT: {'PASS' if results[3] else 'FAIL'} (subtask ids={subtask_ids})")

        # ---- Step 4: re-prioritise ----
        log("### STEP 4 -- re-prioritise one of the subtasks")
        venue_id = subtask_ids[0]
        is_err, struct = await call(a, "step4-reprioritise", "reprioritise_task",
                                     {"id": venue_id, "priority": "high"})
        results[4] = struct.get("ok") is True and struct["task"]["priority"] == "high"
        log(f"STEP 4 VERDICT: {'PASS' if results[4] else 'FAIL'}")

        # ---- Step 5: complete a task ----
        log("### STEP 5 -- complete a task (the now-high-priority venue subtask)")
        is_err, struct = await call(a, "step5-complete", "complete_task", {"id": venue_id})
        results[5] = struct.get("ok") is True and struct["task"]["status"] == "done"
        log(f"STEP 5 VERDICT: {'PASS' if results[5] else 'FAIL'}")

        # ---- Step 6: query ----
        log("### STEP 6a -- query: list_tasks(status=all) should show all 4 tasks "
            "(1 parent + 3 subtasks), 1 done")
        is_err, struct = await call(a, "step6-query-all", "list_tasks", {"status": "all"})
        ids_seen = {t["id"] for t in struct.get("tasks", [])}
        step6a_pass = struct.get("ok") is True and {task1_id, *subtask_ids}.issubset(ids_seen)

        log("### STEP 6b -- query: independently re-check the tool's own ordering "
            'claim ("ordered high-priority first then by id") -- add three fresh '
            "probe tasks with priorities low, high, med (in that order) and confirm "
            "list_tasks(status=pending) returns them high, med, low")
        probe_ids = {}
        for title, pr in [("probe-low", "low"), ("probe-high", "high"), ("probe-med", "med")]:
            _, s2 = await call(a, "step6-probe-add", "add_task", {"title": title, "priority": pr})
            probe_ids[title] = s2["task"]["id"]
        is_err, struct = await call(a, "step6-query-order", "list_tasks", {"status": "pending"})
        probe_order = [t["title"] for t in struct["tasks"] if t["title"] in probe_ids]
        expected_order = ["probe-high", "probe-med", "probe-low"]
        step6b_pass = probe_order == expected_order
        log(f"Observed probe order: {probe_order}; expected: {expected_order}")
        results[6] = step6a_pass and step6b_pass
        log(f"STEP 6 VERDICT: {'PASS' if results[6] else 'FAIL'} "
            f"(6a all-status query={'PASS' if step6a_pass else 'FAIL'}, "
            f"6b ordering claim={'PASS' if step6b_pass else 'FAIL'})")

        # ---- Step 7: undo ----
        log("### STEP 7 -- undo the most recent mutation (should revert step 6b's "
            "last add_task, i.e. remove probe-med) and confirm via a follow-up query")
        is_err, struct = await call(a, "step7-undo", "undo", {})
        _, struct2 = await call(a, "step7-verify", "list_tasks", {"status": "all"})
        remaining_probe_titles = [t["title"] for t in struct2["tasks"] if t["title"].startswith("probe-")]
        results[7] = (struct.get("ok") is True and "probe-med" not in remaining_probe_titles
                      and "probe-high" in remaining_probe_titles and "probe-low" in remaining_probe_titles)
        log(f"Remaining probe tasks after undo: {remaining_probe_titles}")
        log(f"STEP 7 VERDICT: {'PASS' if results[7] else 'FAIL'}")

        # ---- Step 8: sync across two clients ----
        log("### STEP 8 -- sync across two clients")
        log("Opening Client B: an independent MCP session with its OWN empty store")
        b = await open_session(stack, DB_B, "Client B")

        log("Client B creates a task of its own, before ever syncing")
        _, s = await call(b, "step8-b-create", "add_task", {"title": "Draft offsite agenda", "priority": "low"})

        log("The tool's own docs are the only guide here: sync_tasks's description says "
            '"remote: Path/URL of the shared history to sync with"; the CLI --help says '
            '"Remote history path/URL to sync with (only needed once)". Neither names a '
            "concrete shape. Trying the three interpretations a careful reader of just those "
            "two sentences would reach for, in order:")

        log("Attempt 1/3: the most literal reading of 'sync with another Cadence client' -- "
            "point at Client B's own store path (the only identifier for 'another client' this "
            "interface exposes anywhere)")
        _, att1 = await call(a, "step8-attempt1-remote-is-db-path", "sync_tasks", {"remote": DB_B})

        log("Attempt 2/3: a plain shared filesystem location both clients could write to "
            "(freshly created empty directory, since nothing says it must pre-exist or be a "
            "particular format)")
        shared_dir = REMOTE + "_shared_plain_dir"
        os.makedirs(shared_dir, exist_ok=True)
        _, att2 = await call(a, "step8-attempt2-plain-shared-dir", "sync_tasks", {"remote": shared_dir})

        log("Attempt 3/3: a URL, since the docs say 'path/URL' -- a real, reachable local HTTP "
            "server (not a dead port), to rule out 'can't reach' meaning literally unreachable")
        http_proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "http.server", "8123", "--directory", "/tmp",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.sleep(1.0)
        _, att3 = await call(a, "step8-attempt3-http-url", "sync_tasks", {"remote": "http://127.0.0.1:8123/"})
        http_proc.terminate()

        all_failed_identically = all(
            r.get("ok") is False and r.get("error") == "invalid_task" and "can't reach remote" in r.get("message", "")
            for r in (att1, att2, att3)
        )
        log(f"All three documented-shape attempts rejected with the same generic, "
            f"non-diagnostic error (all_failed_identically={all_failed_identically}). "
            "Nothing in --help, the MCP tool schema, or the shipped README explains what a "
            "valid 'remote' value actually is.")
        results[8] = False
        log("STEP 8 VERDICT (documented interface only): FAIL -- sync could not be completed "
            "using any remote value the shipped CLI --help / MCP tool description / README "
            "would lead an agent to try. See docs/ten-step-transcript.md ESCALATION note "
            "and findings for the root cause and severity.")

        log("### STEP 8 ESCALATION (beyond pure tool-description discovery; recorded for "
            "completeness, NOT counted toward the Step 8 verdict above) -- Red Team also "
            "tried directory-listing Client A's own store folder (observing the running "
            "tool's own side effects, not reading source) and noticed a sibling directory "
            "auto-created next to the .db file. Pointing Client A's remote at Client B's "
            "matching sibling directory was tried purely as a diagnostic, out-of-band probe:")
        b_history_guess = DB_B[:-3] + ".history" if DB_B.endswith(".db") else DB_B + ".history"
        log(f"Diagnostic-only attempt: remote = sibling dir of Client B's db ({b_history_guess}), "
            "a value with NO basis in any shipped documentation")
        _, esc1 = await call(a, "step8-escalation-sync1", "sync_tasks", {"remote": b_history_guess})
        _, esc2 = await call(b, "step8-escalation-b-sync1", "sync_tasks", {"remote": b_history_guess})
        _, esc3 = await call(a, "step8-escalation-sync2", "sync_tasks", {})
        log("Escalation results (diagnostic only, not part of Step 8's scored verdict)",
            {"esc1": esc1, "esc2": esc2, "esc3": esc3})

        # ---- Step 9: export ----
        log("### STEP 9 -- export")
        is_err, struct = await call(a, "step9-export-json", "export_tasks", {"format": "json"})
        step9a_pass = (struct.get("ok") is True
                       and struct.get("count", -1) == len(struct.get("tasks", [])))
        is_err, struct2 = await call(a, "step9-export-table", "export_tasks", {"format": "table"})
        step9b_pass = struct2.get("ok") is True and "rows" in struct2
        results[9] = step9a_pass and step9b_pass
        log(f"STEP 9 VERDICT: {'PASS' if results[9] else 'FAIL'} "
            f"(json export ok/count-consistent={step9a_pass}, table export ok={step9b_pass})")

        # ---- Step 10: recover from a deliberately malformed request ----
        log("### STEP 10 -- deliberately send a malformed request, read the error, and recover")
        log("Malformed attempt: add_task with a 250-character title (tool doc says max 200)")
        bad_title = "X" * 250
        is_err, struct = await call(a, "step10-malformed", "add_task", {"title": bad_title})
        malformed_rejected = (is_err is True or struct.get("ok") is False)
        got_hint = bool(struct) and ("hint" in struct or "message" in struct)
        log(f"Rejected cleanly: {malformed_rejected}; carried a hint/message an agent could act on: {got_hint}")

        log("Recovery: shorten the title to <=200 chars per the error's own guidance and retry")
        good_title = bad_title[:200]
        is_err2, struct2 = await call(a, "step10-recover", "add_task", {"title": good_title})
        recovered = struct2.get("ok") is True and struct2["task"]["title"] == good_title
        results[10] = malformed_rejected and got_hint and recovered
        log(f"STEP 10 VERDICT: {'PASS' if results[10] else 'FAIL'} "
            f"(malformed_rejected={malformed_rejected}, got_hint={got_hint}, recovered={recovered})")

        log("===== SUMMARY =====")
        for k in range(1, 11):
            log(f"STEP {k}: {'PASS' if results.get(k) else 'FAIL'}")
        log(f"ALL PASS: {all(results.get(k) for k in range(1, 11))}")

    log("===== R-08 TEN-STEP TRANSCRIPT END =====")


asyncio.run(main())
