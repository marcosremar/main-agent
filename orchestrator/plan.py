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
import tempfile

REPO = "/Users/marcos/projects/babylon-cinema"
BRAIN = "/Users/marcos/projects/babylon-cinema-brain"
IB_PLACEHOLDER = "integration/agent-pipeline-test"


def roadmap_context(objective: str) -> str:
    """Pull the real task source: the ROADMAP epic matching the objective + the CHECKLIST.
    These are the REAL tasks (the doctorate plan), not invented filler."""
    ctx = []
    try:
        rm = open(os.path.join(BRAIN, "ROADMAP.md")).read()
        # find the epic section whose header best matches the objective words
        epics = re.split(r'(?=^## )', rm, flags=re.M)
        words = set(w.lower() for w in re.findall(r'\w+', objective))
        best = max(epics, key=lambda e: len(words & set(re.findall(r'\w+', e.lower()))), default="")
        if best.strip():
            ctx.append("RELEVANT ROADMAP EPIC:\n" + best.strip()[:4000])
    except Exception:
        pass
    try:
        ctx.append("\nCHECKLIST (phase status):\n" + open(os.path.join(BRAIN, "CHECKLIST.md")).read()[:2500])
    except Exception:
        pass
    return "\n".join(ctx)


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
    oat_path = os.path.expanduser("~/.claude-oat-token")
    if not os.path.exists(oat_path):
        raise RuntimeError(
            f"~/.claude-oat-token not found. Run: claude setup-token > ~/.claude-oat-token")
    oat = open(oat_path).read().strip()
    all_done = done_ids()
    avoid = ", ".join(all_done)
    if all_done:
        print(f"  (avoid list: {len(all_done)} existing ids across all configs)", flush=True)
    safe_obj = _safe_objective(objective)
    rmctx = roadmap_context(objective)
    prompt = f"""You are a PLANNER for the babylon-cinema doctorate project. The REAL tasks
come from the roadmap below — decompose THOSE, do not invent unrelated work.

{rmctx}

Read the relevant code in this repo to ground your plan
(schemas under src/game-engine/core/schemas, examples under examples/game-engine, existing
detectors under scripts/qa). Decompose this OBJECTIVE into up to {n} SMALL, INDEPENDENT,
verifiable tasks that can each be done by one agent touching ~1-3 files.

OBJECTIVE: {safe_obj}

Each task MUST be independent (no two tasks edit the same file) and verifiable by a command.
Spread the worker_model lanes EVENLY across "dumont", "codex", and "claude" (roughly a
third each). The orchestrator runs at most 15 tasks PER LANE concurrently (45 total) and
queues the rest, and each task is independently validated by Opus — so balanced lanes
maximize throughput. Do NOT reuse any of these existing task ids: {avoid}

SIZING RULE (decompose to the right granularity — this is the biggest lever on success):
- If a task would force the worker to DECIDE ARCHITECTURE or CHOOSE between files, it is too
  COARSE — split it; coarse tasks cause cascading failures.
- If it is >~5 trivial near-identical steps, it is too FINE — group them.
- Target: the worker finishes in MINUTES and the verify command goes green on the 1st-2nd try.

EVERY task's `spec` is the ONLY thing the worker sees (it runs in an ISOLATED worktree with NO
shared memory), so it MUST be 100% self-contained and contain these SEVEN fields, in
imperative/deterministic language (no "should/maybe" — every noun is a REAL repo symbol/path
the worker confirms by reading, never guesses):
  1. GOAL + WHY (one sentence) — anchors intent.
  2. EXACT ALLOWED FILES (1-3) — must equal `allowed_files`.
  3. CONCRETE ORDERED STEPS (imperative) — a bounded series of edits.
  4. FROZEN ACCEPTANCE CRITERIA + the verify command — how the worker knows it is DONE.
  5. EXPLICIT PROHIBITIONS — "do NOT touch X", plus the inherited rules (cd
     /Users/marcos/projects/babylon-cinema first; read CLAUDE.md+AGENTS.md; NodeNext ESM
     imports end in .js; comments=WHY only; surgical; no scope beyond allowed_files).
  6. EMBEDDED CONTEXT — the real symbol/type/path names needed (not "see the docs").
  7. FALLBACK — "if genuinely blocked, STOP and report exactly what is missing" (no guessing).

MANDATORY TEST: every task MUST create or extend an automated test, and `verify_cmd` MUST run
that test. A task with no test is invalid.

VERIFIER LAYER (pick per task — the deterministic command is ALWAYS the first gate; Opus ALSO
always validates every task):
- Default (objective code): just `verify_cmd` (e.g. npx vitest run <spec>). No extra fields.
- FUZZY semantic criteria a command can't judge (e.g. "feedback is pedagogically adequate",
  "NPC reply stays in target language"): set "verifier":"llm" and a natural-language
  "criteria" string the adversarial Opus verifier checks.
- 3D / VISUAL / SPATIAL criteria (this engine is full of them — "the bakery looks like a
  bakery", "no floating meshes", "city renders without gaps", camera framing): set
  "verifier":"vision", an "evidence_cmd" that renders a PNG headlessly in the sandbox, an
  "evidence_image" path to that PNG, and a "criteria" string describing what the image must
  show. The vision model (Qwen2.5-VL) judges the screenshot. USE THIS WHENEVER the real
  acceptance is something you'd confirm by LOOKING at a render — do not fake a visual check
  with a brittle command.

Output ONLY a JSON array (no prose, no markdown fence) of objects with these keys:
  "id": kebab-case unique id (not in the avoid list)
  "title": one line
  "worker_model": one of "dumont" (simple), "codex" (medium), "claude" (hard)
  "commit": "feat(...): ..." conventional commit line
  "allowed_files": array of the 1-3 file paths the task may create/edit
  "verify_cmd": shell command that proves success and RUNS THE TEST (e.g. npx vitest run <spec>)
  "spec": the self-contained instruction containing the SEVEN fields above
  "verifier": OPTIONAL — omit for default, or "llm" (fuzzy) or "vision" (3D/visual)
  "criteria": REQUIRED when verifier is "llm" or "vision" — what must be true / what the image shows
  "evidence_cmd": REQUIRED when verifier is "vision" — command that renders the PNG
  "evidence_image": REQUIRED when verifier is "vision" — path to the rendered PNG
Output the JSON array and nothing else."""
    safe_obj_for_file = _safe_objective(objective, max_len=100)
    outfile = tempfile.NamedTemporaryFile(
        suffix="-plan-out.json", prefix=f"plan-{safe_obj_for_file[:40]}-",
        dir=os.path.join(REPO), delete=False
    ).name
    prompt += (f"\n\nDo NOT print the JSON. Instead use the Write tool to write the JSON "
               f"array to the file {outfile}. Make sure it is valid JSON (escape every "
               f"backslash inside a string as \\\\). Then stop.")
    env = dict(os.environ, CLAUDE_CODE_OAUTH_TOKEN=oat)
    print("Planning... (streaming output disabled — this takes a few minutes)", flush=True)
    try:
        result = subprocess.run(["claude", "-p", "--model", "claude-opus-4-8",
                        "--permission-mode", "acceptEdits"],
                       input=prompt, text=True, env=env, cwd=REPO, timeout=600)
        if result.returncode != 0:
            print(f"WARN planner exited {result.returncode}: {result.stderr[-300:]}", flush=True)
    except subprocess.TimeoutExpired:
        raise RuntimeError("planner timed out after 600s — increase timeout or simplify objective")
    if not os.path.exists(outfile):
        raise RuntimeError("planner did not write the output file")
    raw = open(outfile).read()
    start = raw.find("[")
    end = raw.rfind("]")
    raw = raw[start:end+1] if start != -1 and end != -1 else raw
    try:
        tasks = json.loads(raw)
    except json.JSONDecodeError:
        fixed = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', raw)
        tasks = json.loads(fixed)
    try:
        os.remove(outfile)
    except OSError:
        pass
    avoidset = set(done_ids())
    clean = []
    for t in tasks:
        if t.get("id") in avoidset:
            continue
        v = t.get("verifier")
        if v == "vision":
            req = ("id", "worker_model", "commit", "allowed_files", "spec", "evidence_cmd", "criteria", "evidence_image")
            if not all(k in t for k in req):
                continue
        elif not all(k in t for k in ("id", "worker_model", "commit", "allowed_files", "verify_cmd", "spec")):
            continue
        t.setdefault("max_iters", 12)
        t.setdefault("no_progress_limit", 5)
        t.setdefault("worker_timeout_s", 600)
        t.setdefault("validate_timeout_s", 720)
        clean.append(t)
    return clean


def main():
    objective = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    out = sys.argv[3] if len(sys.argv) > 3 else "config-planned.json"
    print(f"planning ({n}) for: {objective}")
    tasks = run_planner(objective, n)
    ib = _integration_branch()
    cfg = {"integration_branch": ib, "max_concurrent": 45, "per_lane_max": 15,
           "global_deadline_s": 7200, "pool_sids": [],
           "objective": objective, "planner": "opus",
           "tasks": tasks}
    json.dump(cfg, open(out, "w"), indent=2)
    print(f"\nwrote {out} with {len(tasks)} tasks:")
    for t in tasks:
        print(f"  [{t['worker_model']}] {t['id']} — {t.get('title','')}")
        print(f"       files: {t['allowed_files']}  verify: {t['verify_cmd']}")


if __name__ == "__main__":
    main()
