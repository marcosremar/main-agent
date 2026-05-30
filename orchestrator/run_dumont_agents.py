"""Launch 10 parallel Dumont (MiniMax M2.7) agents to bug-hunt + write tests for the orchestrator."""
import subprocess, os, time
from concurrent.futures import ThreadPoolExecutor

DUMONT = "/Applications/Dumont Code.app/Contents/Resources/_up_/_up_/bin/dumont"
ORCH = "/Users/marcos/projects/babylon-cinema-brain/orchestrator"
TESTS = f"{ORCH}/tests"

def get_minimax_key():
    for line in open("/Users/marcos/projects/ai-gateway/.env"):
        if line.startswith("MINIMAX_API_KEY="):
            return line.split("=", 1)[1].strip()
    return ""

MINIMAX_KEY = get_minimax_key()
os.makedirs(TESTS, exist_ok=True)

AGENTS = [
    {
        "id": "bugs-main-loop",
        "spec": f"""Read /Users/marcos/projects/babylon-cinema-brain/orchestrator/orchestrate.py in full.
Hunt for bugs in the run_task() function and the main() loop. Look for:
- Off-by-one errors, wrong conditions, silent failures
- Budget/timeout logic issues
- State mutation bugs across iterations
- Anything that could cause tasks to hang, loop forever, or produce wrong results

Write a detailed bug report to {TESTS}/bugs-main-loop.md with each bug found, file:line, and explanation.""",
    },
    {
        "id": "bugs-escalation",
        "spec": f"""Read /Users/marcos/projects/babylon-cinema-brain/orchestrator/orchestrate.py in full.
Focus on the worker escalation logic: when MiniMax/dumont fails and escalates to OPUS (claude lane).
Look for:
- Semaphore accounting bugs when a task changes lanes mid-run
- Incorrect failure counters (minimax_fails) after escalation
- spec accumulation across iters causing oversized prompts
- Any race conditions or logic bugs in the escalation path

Write a detailed bug report to {TESTS}/bugs-escalation.md.""",
    },
    {
        "id": "bugs-verify",
        "spec": f"""Read /Users/marcos/projects/babylon-cinema-brain/orchestrator/orchestrate.py in full.
Focus on verify_task(), verify(), run_llm_verifier(), verify_vision().
Look for:
- Incorrect layering: when does opus_validate fire? Could it fire when it shouldn't?
- Stall detection disabled for verifier (stall=vt means stall == timeout)
- Vision verifier timeout using wrong config field
- Any path where a failed verify could be misreported as pass or vice versa

Write a detailed bug report to {TESTS}/bugs-verify.md.""",
    },
    {
        "id": "bugs-scope",
        "spec": f"""Read /Users/marcos/projects/babylon-cinema-brain/orchestrator/orchestrate.py and /Users/marcos/projects/babylon-cinema-brain/orchestrator/github.py in full.
Focus on scope_ok(), already_done(), and pr_files-based scope check.
Look for:
- Vacuous truth: all() on empty list
- git diff --name-only HEAD on a worktree with no commits yet
- Path comparison mismatches (relative vs repo-root-relative)
- Silent truncation in pr_files() pagination
- already_done() false-positive scenarios

Write a detailed bug report to {TESTS}/bugs-scope.md.""",
    },
    {
        "id": "bugs-daytona",
        "spec": f"""Read /Users/marcos/projects/babylon-cinema-brain/orchestrator/daytona.py in full.
Hunt for bugs in the Daytona REST client. Look for:
- is_alive() returning True on transient errors — could put dead sandboxes back in pool
- backup() polling condition: "None" in stop set means it exits too early
- exec_wait() stall detection when logfile stays empty (worker never started)
- exec_detached() shell escaping issues with complex commands
- _wait_state() swallowing errors silently

Write a detailed bug report to {TESTS}/bugs-daytona.md.""",
    },
    {
        "id": "bugs-github",
        "spec": f"""Read /Users/marcos/projects/babylon-cinema-brain/orchestrator/github.py in full.
Hunt for bugs in the GitHub REST client. Look for:
- pr_files() silent truncation at 300 files with no warning
- open_pr_for() picking wrong PR when multiple exist
- merge_pr() error handling — which HTTP codes are caught vs re-raised?
- 404 not retried — could this cause false failures on transient GitHub 404s?
- Any TOCTOU or race conditions between checking and merging

Write a detailed bug report to {TESTS}/bugs-github.md.""",
    },
    {
        "id": "test-daytona",
        "spec": f"""Read /Users/marcos/projects/babylon-cinema-brain/orchestrator/daytona.py in full.
Write a comprehensive pytest test file at {TESTS}/test_daytona.py.
Use unittest.mock to mock urllib.request so no real HTTP calls are made.
Cover:
- _req() retry logic for 502/503/504/429
- _req() no-retry for 4xx (except rate-limit)
- exec() base64 encoding of commands
- exec_wait() detects SENTINEL → returns output
- exec_wait() stall detection (logfile stops growing)
- exec_wait() wall-clock timeout
- is_alive() returns False on 404, True on transient error
- backup() exits when backupState == "Completed"
Make tests runnable with: cd {ORCH} && python -m pytest tests/test_daytona.py -v""",
    },
    {
        "id": "test-github",
        "spec": f"""Read /Users/marcos/projects/babylon-cinema-brain/orchestrator/github.py in full.
Write a comprehensive pytest test file at {TESTS}/test_github.py.
Use unittest.mock to mock urllib.request so no real HTTP calls are made.
Cover:
- create_pr() happy path
- merge_pr() happy path + MERGE_CONFLICT on 405
- pr_files() pagination: 1 page, 2 pages, stops at page 3
- open_pr_for() returns newest PR when multiple exist, None when empty
- Rate-limit retry on 403 with "rate limit" in body
- 502/503 retry with backoff
Make tests runnable with: cd {ORCH} && python -m pytest tests/test_github.py -v""",
    },
    {
        "id": "test-orchestrate",
        "spec": f"""Read /Users/marcos/projects/babylon-cinema-brain/orchestrator/orchestrate.py in full.
Write a comprehensive pytest test file at {TESTS}/test_orchestrate.py.
Mock both the Daytona and GitHub clients (inject mocks, don't call real APIs).
Cover run_task() end-to-end:
- Happy path: worker passes verify on iter 1 → MERGED
- FAILED_MAX_ITERS: verify always fails
- TIMEOUT_BUDGET: task_budget_s exceeded
- OUT_OF_SCOPE: scope_ok returns extra files
- WORKTREE_FAILED: worktree setup fails
- Escalation: minimax_fails >= 2 switches model to OPUS
- STUCK_NO_PROGRESS: same error signature repeats no_progress_limit times
Also test: already_done() with empty allowed_files (vacuous all() case)
Make tests runnable with: cd {ORCH} && python -m pytest tests/test_orchestrate.py -v""",
    },
    {
        "id": "test-utils",
        "spec": f"""Read /Users/marcos/projects/babylon-cinema-brain/orchestrator/orchestrate.py in full.
Write a pytest test file at {TESTS}/test_utils.py covering the utility functions:
- error_signature(): strips volatile numbers, deduplicates error lines, returns sorted joined string
- scope_ok(): path prefix matching, exact match, out-of-scope detection
- b64(): round-trip encode/decode
- _infra_dead(): all the error string patterns it recognizes
- lane_key(): returns correct key for "claude", "dumont", "codex", "minimax", unknown model
- error_signature() with empty output returns empty string
Make tests runnable with: cd {ORCH} && python -m pytest tests/test_utils.py -v""",
    },
]


def run_agent(agent):
    t0 = time.time()
    env = os.environ.copy()
    env["DUMONT_CONFIG"] = os.path.expanduser("~/.dumont/dumont.json")
    env["MINIMAX_API_KEY"] = MINIMAX_KEY
    proc = subprocess.run(
        [DUMONT, "-p", "--model", "minimax/m2-7",
         "--dangerously-skip-permissions", "--output-format", "text"],
        input=agent["spec"].encode(),
        capture_output=True,
        env=env,
        cwd=ORCH,
    )
    elapsed = round(time.time() - t0)
    ok = proc.returncode == 0
    tail = (proc.stdout or proc.stderr or b"").decode()[-300:]
    print(f"[{agent['id']}] {'OK' if ok else 'FAIL'} {elapsed}s | {tail.strip()[:120]}", flush=True)
    return {"id": agent["id"], "ok": ok, "elapsed": elapsed}


print(f"Launching {len(AGENTS)} Dumont agents in parallel...", flush=True)
t_start = time.time()

with ThreadPoolExecutor(max_workers=10) as ex:
    results = list(ex.map(run_agent, AGENTS))

print(f"\n=== DONE in {round(time.time()-t_start)}s ===")
for r in results:
    print(f"  {'✓' if r['ok'] else '✗'} {r['id']} ({r['elapsed']}s)")
print(f"\nOutputs in {TESTS}/")
