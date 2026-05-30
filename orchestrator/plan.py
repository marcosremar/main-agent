"""Planner — decompose a domain objective into small, verifiable task specs (manager layer).

Instead of the brain hand-enumerating tasks, an Opus agent READS the real codebase and
emits N small independent task specs grounded in actual code. Output is a config.json the
orchestrator runs (worker -> Opus validate -> PR). The brain reviews the plan before launch.

Usage:
  python3 plan.py "<objective>" <N> [out.json]
Env: CLAUDE_CODE_OAUTH_TOKEN (from ~/.claude-oat-token) for Opus.
"""
import json
import os
import re
import subprocess
import sys

REPO = "/Users/marcos/projects/babylon-cinema"
IB = "integration/agent-pipeline-test"


def done_ids():
    """Ids already attempted (any config + state) — so the planner doesn't repeat them."""
    import glob
    ids = set()
    here = os.path.dirname(os.path.abspath(__file__))
    for f in glob.glob(os.path.join(here, "config*.json")):
        try:
            for t in json.load(open(f)).get("tasks", []):
                ids.add(t["id"])
        except Exception:
            pass
    for f in glob.glob(os.path.join(here, "state", "state-*.json")):
        try:
            ids.update(json.load(open(f)).keys())
        except Exception:
            pass
    return sorted(ids)


def run_planner(objective: str, n: int) -> list:
    oat = open(os.path.expanduser("~/.claude-oat-token")).read().strip()
    avoid = ", ".join(done_ids())
    prompt = f"""You are a PLANNER. Read the relevant code in this repo to ground your plan
(schemas under src/game-engine/core/schemas, examples under examples/game-engine, existing
detectors under scripts/qa). Decompose this OBJECTIVE into up to {n} SMALL, INDEPENDENT,
verifiable tasks that can each be done by one agent touching ~1-3 files.

OBJECTIVE: {objective}

Each task MUST be independent (no two tasks edit the same file) and verifiable by a command.
Do NOT reuse any of these existing task ids: {avoid}

Output ONLY a JSON array (no prose, no markdown fence) of objects with EXACTLY these keys:
  "id": kebab-case unique id (not in the avoid list)
  "title": one line
  "worker_model": one of "dumont" (simple), "codex" (medium), "claude" (hard)
  "commit": "feat(...): ..." conventional commit line
  "allowed_files": array of the 1-3 file paths the task may create/edit
  "verify_cmd": a shell command that proves success (e.g. npx vitest run <spec>)
  "spec": a complete, self-contained instruction for the worker, including what to build,
          the exact files, and that it MUST create a test and not finish until the verify
          command passes.
Output the JSON array and nothing else."""
    # Have the agent WRITE the JSON to a file (avoids stdout escaping/truncation issues
    # with spec strings full of backslashes/quotes). Read+parse the file, tolerant of
    # invalid JSON escapes the model may emit (\s, \. from regex specs).
    outfile = os.path.join(REPO, ".plan-out.json")   # inside cwd so claude can write it
    prompt += (f"\n\nDo NOT print the JSON. Instead use the Write tool to write the JSON "
               f"array to the file {outfile}. Make sure it is valid JSON (escape every "
               f"backslash inside a string as \\\\). Then stop.")
    env = dict(os.environ, CLAUDE_CODE_OAUTH_TOKEN=oat)
    if os.path.exists(outfile):
        os.remove(outfile)
    subprocess.run(["claude", "-p", "--model", "claude-opus-4-8",
                    "--permission-mode", "acceptEdits"],
                   input=prompt, capture_output=True, text=True, env=env,
                   cwd=REPO, timeout=600)
    if not os.path.exists(outfile):
        raise RuntimeError("planner did not write the output file")
    raw = open(outfile).read()
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    raw = m.group(0) if m else raw
    try:
        tasks = json.loads(raw)
    except json.JSONDecodeError:
        # salvage: escape backslashes that aren't valid JSON escapes
        fixed = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', raw)
        tasks = json.loads(fixed)
    avoidset = set(done_ids())
    clean = []
    for t in tasks:
        if t.get("id") in avoidset:
            continue
        if not all(k in t for k in ("id", "worker_model", "commit", "allowed_files", "verify_cmd", "spec")):
            continue
        t.setdefault("max_iters", 12)
        t.setdefault("no_progress_limit", 5)
        t.setdefault("worker_timeout_s", 480)
        clean.append(t)
    return clean


def main():
    objective = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    out = sys.argv[3] if len(sys.argv) > 3 else "config-planned.json"
    print(f"planning ({n}) for: {objective}")
    tasks = run_planner(objective, n)
    cfg = {"integration_branch": IB, "max_concurrent": min(15, len(tasks)),
           "global_deadline_s": 5400, "pool_sids": [],
           "objective": objective, "planner": "opus",   # surfaced in the dashboard
           "tasks": tasks}
    json.dump(cfg, open(out, "w"), indent=2)
    print(f"\nwrote {out} with {len(tasks)} tasks:")
    for t in tasks:
        print(f"  [{t['worker_model']}] {t['id']} — {t.get('title','')}")
        print(f"       files: {t['allowed_files']}  verify: {t['verify_cmd']}")


if __name__ == "__main__":
    main()
