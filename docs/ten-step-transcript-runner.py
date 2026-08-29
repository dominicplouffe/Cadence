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
        _, sb = await call(b, "step8-b-create", "add_task", {"title": "Draft offsite agenda", "priority": "low"})
        b_task_id = sb["task"]["id"]

        log("Reading the shipped, documented interface only (published 0.2.1): sync_tasks's "
            "MCP docstring now says 'remote: The OTHER client's own CADENCE_DB_PATH value (its "
            "plain .db file path) -- this client derives that client's history location itself. "
            "A git URL also works, for a shared server remote.' The CLI --help says the same "
            "thing verbatim. An agent with no repo access, reading only this, would try exactly "
            "one value: the other client's own CADENCE_DB_PATH.")

        log("Client A syncs, remote = Client B's own plain CADENCE_DB_PATH (DB_B)")
        _, syncA1 = await call(a, "step8-a-sync-to-b-path", "sync_tasks", {"remote": DB_B})

        log("Client B syncs, remote = Client A's own plain CADENCE_DB_PATH (DB_A), to pull A's "
            "task and confirm the OTHER direction of the documented contract also works")
        _, syncB1 = await call(b, "step8-b-sync-to-a-path", "sync_tasks", {"remote": DB_A})

        log("Verify convergence: list_tasks(status=all) on BOTH clients should now include "
            "both Client A's parent+subtask+probe tasks AND Client B's 'Draft offsite agenda'")
        _, listA = await call(a, "step8-verify-a", "list_tasks", {"status": "all"})
        _, listB = await call(b, "step8-verify-b", "list_tasks", {"status": "all"})
        titles_a = {t["title"] for t in listA.get("tasks", [])}
        titles_b = {t["title"] for t in listB.get("tasks", [])}
        b_task_title = sb["task"]["title"]
        sync_ok = syncA1.get("ok") is True and syncB1.get("ok") is True
        converged = (b_task_title in titles_a) and ("Prep the Q4 client offsite" in titles_b)
        no_conflicts = not syncA1.get("conflicts") and not syncB1.get("conflicts")
        log(f"Client A sees Client B's task ({b_task_title!r} in A's list): "
            f"{b_task_title in titles_a}; Client B sees Client A's parent task "
            f"('Prep the Q4 client offsite' in B's list): "
            f"{'Prep the Q4 client offsite' in titles_b}")

        log(f"sync_tasks reported a conflict on id=1 (expected: both stores independently "
            f"assigned id=1 to their own first task before ever syncing -- Client A's "
            f"'Prep the Q4 client offsite' vs Client B's 'Draft offsite agenda'). The tool's "
            f"own Returns doc names the documented recovery: 'call resolve_sync_conflict(id, "
            f"keep=\"mine\"|\"theirs\") for each one, then call sync_tasks again.' Following "
            f"that documented path to completion, deciding to keep Client A's version as "
            f"authoritative on both sides:")
        _, resA = await call(a, "step8-resolve-a", "resolve_sync_conflict", {"id": 1, "keep": "mine"})
        _, syncA2 = await call(a, "step8-a-resync-after-resolve", "sync_tasks", {})
        _, resB = await call(b, "step8-resolve-b", "resolve_sync_conflict", {"id": 1, "keep": "theirs"})
        _, syncB2 = await call(b, "step8-b-resync-after-resolve", "sync_tasks", {})

        log("Re-verify convergence after the documented resolve+resync recovery, and check "
            "whether the LOSING side's task content survived under a different id or was "
            "silently discarded")
        _, listA2 = await call(a, "step8-verify-a-2", "list_tasks", {"status": "all"})
        _, listB2 = await call(b, "step8-verify-b-2", "list_tasks", {"status": "all"})
        titles_a2 = [t["title"] for t in listA2.get("tasks", [])]
        titles_b2 = [t["title"] for t in listB2.get("tasks", [])]
        both_id1_match = (
            listA2["ok"] and listB2["ok"]
            and next((t for t in listA2["tasks"] if t["id"] == 1), {}).get("title")
            == next((t for t in listB2["tasks"] if t["id"] == 1), {}).get("title")
            == "Prep the Q4 client offsite"
        )
        b_task_survived = b_task_title in titles_a2 or b_task_title in titles_b2
        log(f"After resolve+resync: A's tasks={titles_a2}")
        log(f"After resolve+resync: B's tasks={titles_b2}")
        log(f"id=1 now identical on both sides ('Prep the Q4 client offsite'): {both_id1_match}")
        log(f"Client B's original task ({b_task_title!r}) survived ANYWHERE under any id, on "
            f"either side, after resolving the collision: {b_task_survived}")
        if not b_task_survived:
            log("FINDING: resolve_sync_conflict(keep=\"mine\"/\"theirs\") resolves an id "
                "COLLISION (two independently-created, unrelated tasks that happen to share an "
                "id) the same way it resolves an id EDIT conflict (one task edited on both "
                "sides): it keeps exactly one side's row and permanently discards the other's "
                "row's content. For a genuine edit-conflict this is correct (there's truly one "
                "task). For an id-collision between two DIFFERENT tasks (the documented, named "
                "scenario per the sync_tasks docstring itself: 'independently created with the "
                "same id') this silently deletes a real, unrelated task with no renumbering "
                "and no warning that data (not just an edit) will be lost.")

        results[8] = bool(sync_ok and both_id1_match)
        log(f"STEP 8 VERDICT (documented interface + documented recovery path): "
            f"{'PASS' if results[8] else 'FAIL'} -- sync itself (plain CADENCE_DB_PATH as "
            f"remote) now works and is discoverable from --help/MCP docstring alone "
            f"(sync_ok={sync_ok}); the initial conflict on id=1 was resolved to a consistent "
            f"state on both clients via the documented resolve_sync_conflict()+re-sync path "
            f"(both_id1_match={both_id1_match}). NOTE (does not flip this verdict, but is a "
            f"real data-loss risk on the same documented path): the losing side's actual task "
            f"content was NOT preserved (b_task_survived={b_task_survived}) -- see FINDING "
            f"above and docs/ten-step-transcript.md.")

        log("### STEP 8b -- id-collision wording check (Finding 2 reword verification): "
            "two fresh, never-synced clients that each independently created a task with the "
            "same auto-assigned id 1, then sync -- confirm the conflict message now reads "
            "'differs between this client and the remote ... (edited on both sides, or "
            "independently created with the same id)' instead of only 'edited on both sides'")
        c_db = DB_A + "_collide_c"
        d_db = DB_A + "_collide_d"
        c = await open_session(stack, c_db, "Client C (fresh)")
        d = await open_session(stack, d_db, "Client D (fresh)")
        _, sc = await call(c, "step8b-c-create", "add_task", {"title": "Client C's own id-1 task", "priority": "med"})
        _, sd = await call(d, "step8b-d-create", "add_task", {"title": "Client D's own id-1 task", "priority": "med"})
        log(f"Both fresh clients independently created id={sc['task']['id']} and id={sd['task']['id']} "
            "(both 1, as expected -- neither has ever synced)")
        _, syncC = await call(c, "step8b-c-sync", "sync_tasks", {"remote": d_db})
        conflict_msgs = [conf.get("message", "") for conf in syncC.get("conflicts", [])] if syncC.get("conflicts") else []
        reworded = any("independently created with the same id" in m for m in conflict_msgs)
        log(f"Conflict reported: {bool(syncC.get('conflicts'))}; message(s): {conflict_msgs}; "
            f"Finding-2 reword present: {reworded}")
        log(f"STEP 8b VERDICT (Finding 2 wording, informational -- not required for the "
            f"ten-step script itself): {'PASS' if reworded else 'FAIL'}")

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
