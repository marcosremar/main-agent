"""babylon-cinema agent orchestrator.

Pipeline per task (cloud):
  worktree(branch from integration) -> worker loop(<=max_iters, verify each round,
  escalate stuck MiniMax->Opus) -> push -> open PR vs INTEGRATION BRANCH -> integration
  agent (re-verify + scope check) -> squash-merge into integration branch -> cleanup.

main is OFF-LIMITS. Tasks flagged location:"local" are skipped here and listed for the
local lane (iPhone/simulator/macOS-native), which must run on the Mac.

Usage:
  python3 orchestrate.py config.json
"""
import base64
import json
import os
import sys
import time

# load orchestrator/.env (KEY=VALUE lines) so model/lane settings live in one place
_envf = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_envf):
    for _l in open(_envf):
        _l = _l.strip()
        if _l and not _l.startswith("#") and "=" in _l:
            _k, _v = _l.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

from daytona import Daytona
from creds import inject_into_sandbox, gh_token
from github import GitHub

OPUS = "claude"                       # claude CLI lane id (Opus 4.8 by default)
# the claude WORKER model is configurable via WORKER_CLAUDE_MODEL (orchestrator/.env or env),
# e.g. claude-sonnet-4-6 to run the bulk of tasks on Sonnet. The VALIDATOR stays Opus always
# (policy: hard-coded claude-opus-4-8 in run_llm_verifier) regardless of this setting.
WORKER_CLAUDE_MODEL = os.environ.get("WORKER_CLAUDE_MODEL", "claude-opus-4-8")
MINIMAX = "minimax/MiniMax-M2.7"
OPATH = "export PATH=$PATH:$HOME/.opencode/bin"


def log(msg): print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def run_worker(dt, sid, wt, model, spec, tag, worker_timeout=480):
    """Launch a worker agent (detached) in the worktree, wait for it to finish.

    Raises TimeoutError (after killing the process) if it exceeds worker_timeout."""
    specfile = f"/tmp/spec-{tag}.txt"
    logfile = f"/tmp/work-{tag}.log"
    dt.exec(sid, f"echo {b64(spec)} | base64 -d > {specfile}", timeout=30)
    # CRITICAL: feed the prompt via STDIN, never inline as "$(cat file)". The spec text
    # contains backticks, $( ), <, >, etc. — inline it gets RE-EVALUATED by the nested
    # shells (base64->bash->bash -lc), corrupting the prompt so the worker writes nothing.
    # Piping the file as stdin passes it verbatim.
    if model == OPUS:
        # stream-json so the log GROWS during the run (each tool/use event is a line). Without
        # streaming, claude -p writes nothing until done, and the stall-detector in exec_wait
        # would wrongly kill a working-but-silent worker. Streaming makes stall fire only on a
        # TRUE hang (no events at all).
        cmd = (f"{OPATH}; cd {wt} && cat {specfile} | claude -p --model {WORKER_CLAUDE_MODEL} "
               f"--permission-mode acceptEdits --output-format stream-json --verbose")
        match = "claude -p"
    elif model.startswith("dumont"):
        # dumont-code agent on MiniMax M2.7 — prebuilt binary. Login via
        # ~/.dumont/.credentials.json, key via $MINIMAX_API_KEY (~/.profile, loaded by bash -l).
        dmodel = model.split(":", 1)[1] if ":" in model else "minimax/m2-7"
        cmd = (f"export DUMONT_CONFIG=$HOME/.dumont/dumont.json; cd {wt} && "
               f"cat {specfile} | ~/bin/dumont -p --model {dmodel} "
               "--dangerously-skip-permissions --output-format text")
        match = "dumont"
    elif model.startswith("codex"):
        # OpenAI Codex CLI (gpt-5.3-codex-spark). ChatGPT auth at ~/.codex/auth.json. Reads
        # instructions from stdin. Externally sandboxed (Daytona) -> bypass approvals.
        cxmodel = model.split(":", 1)[1] if ":" in model else "gpt-5.3-codex-spark"
        cmd = (f"cat {specfile} | codex exec -m {cxmodel} "
               f"--dangerously-bypass-approvals-and-sandbox --skip-git-repo-check -C {wt}")
        match = "codex exec"
    else:
        cmd = f"{OPATH}; cd {wt} && cat {specfile} | opencode run -m {model}"
        match = "opencode run"
    dt.exec_detached(sid, cmd, logfile)
    return dt.exec_wait(sid, match, logfile, timeout=worker_timeout)


def cleanup_worktree(dt, sid, wt, branch):
    dt.exec(sid, f"cd ~/babylon-cinema && git worktree remove --force {wt} 2>/dev/null; "
                 f"git branch -D {branch} 2>/dev/null; git worktree prune", timeout=60)


def verify(dt, sid, wt, verify_cmd):
    """Deterministic verification = run the task's verify command. Returns (ok, output)."""
    full = (f"{OPATH}; cd {wt} && {verify_cmd} 2>&1 | tail -40; "
            "echo VERIFY_EXIT=${PIPESTATUS[0]}")
    _, out = dt.exec(sid, full, timeout=300)
    ok = "VERIFY_EXIT=0" in out
    return ok, out


def run_llm_verifier(dt, sid, wt, task, tag):
    """INDEPENDENT adversarial verifier (Opus) — for fuzzy criteria a command can't judge.

    Runs a SEPARATE claude agent (not the worker) that inspects the actual change/evidence
    and tries to REFUTE that the frozen criteria are met. Optional `evidence_cmd` produces
    artifacts first (e.g. a screenshot path, a rendered output) for the verifier to read.
    Returns (ok, output). The verifier must end with `VERDICT: PASS` or `VERDICT: FAIL: ...`.
    """
    evidence = task.get("evidence_cmd", "")
    # validate against the ACTUAL task spec (a weak worker may write a test that passes but
    # is trivial/wrong — Opus judges the change against the real intent, not just "tests pass")
    criteria = task.get("criteria") or task.get("spec", "The change fully and correctly satisfies the task.")
    prompt = (
        "You are an INDEPENDENT, ADVERSARIAL validator (Claude Code / Opus). You did NOT "
        "write this code — a weaker model may have. Do not trust that passing tests means "
        "correct: inspect the ACTUAL files changed (git diff / read them) and check the "
        "change genuinely and non-trivially fulfills the TASK below. Catch trivial/empty/"
        "wrong implementations and tests that pass without really testing. Default to FAIL "
        "if uncertain.\n\n"
        f"TASK (what the change must achieve):\n{criteria}\n\n"
        "Then output EXACTLY one final line: 'VERDICT: PASS' if the change is genuinely "
        "correct and complete, otherwise 'VERDICT: FAIL: <short reason>'. Nothing after it.")
    specfile = f"/tmp/verify-{tag}.txt"
    logfile = f"/tmp/verify-{tag}.log"
    dt.exec(sid, f"echo {b64(prompt)} | base64 -d > {specfile}", timeout=30)
    pre = f"{evidence}; " if evidence else ""
    cmd = (f"{OPATH}; cd {wt} && {pre}cat {specfile} | claude -p "
           "--model claude-opus-4-8 --permission-mode acceptEdits")
    dt.exec_detached(sid, cmd, logfile)
    # Opus reading a diff + judging can be slow — give the validator its OWN (longer)
    # budget, decoupled from the worker timeout. A validator hang must NOT be fatal: treat
    # it as a retryable FAIL so the task loops again instead of ERRORing out the whole task.
    try:
        # the validator runs plain `claude -p` (text, non-streaming) so its log stays silent
        # until done — DISABLE the stall-detector here (stall=timeout) and rely on the wall-clock.
        vt = task.get("validate_timeout_s", 720)
        out = dt.exec_wait(sid, "claude -p", logfile, timeout=vt, stall=vt)
    except TimeoutError as e:
        return False, f"OPUS VALIDATION TIMED OUT ({e}) — retrying"
    ok = "VERDICT: PASS" in out
    return ok, out


def verify_vision(dt, sid, wt, task):
    """Visual verification for fuzzy 3D/UI criteria. Runs `evidence_cmd` in the sandbox to
    produce an image at `evidence_image`, pulls it out, and judges it with a cheap vision
    model (gpt-4o-mini via OpenRouter). Returns (ok, output)."""
    import base64 as _b64, tempfile, os
    from vision import judge_image
    if task.get("evidence_cmd"):
        dt.exec(sid, f"{OPATH}; cd {wt} && {task['evidence_cmd']}",
                timeout=task.get("worker_timeout_s", 480))
    img = task["evidence_image"]
    _, out = dt.exec(sid, f"cd {wt} && base64 -w0 {img} 2>/dev/null || base64 {img}", timeout=60)
    data = out.strip().splitlines()[-1] if out.strip() else ""
    if not data:
        return False, f"no evidence image at {img}"
    tmp = tempfile.mktemp(suffix=".png")
    with open(tmp, "wb") as f:
        f.write(_b64.b64decode(data))
    try:
        r = judge_image(tmp, task["criteria"], model=task.get("vision_model"))
    finally:
        os.remove(tmp)
    return r["ok"], f"vision score={r.get('score')} | {r.get('verdict')}\n{r.get('raw','')}"


def verify_task(dt, sid, wt, task, tag):
    """Dispatch verification (layered): deterministic command gate FIRST (free/cheap), then
    an optional fuzzy verifier — `vision` (cheap vision model on a screenshot/render) or
    `llm` (independent adversarial Opus). Pass requires ALL configured stages."""
    ok, out = True, ""
    if task.get("verify_cmd"):
        ok, out = verify(dt, sid, wt, task["verify_cmd"])
        if not ok:
            return ok, out
    mode = task.get("verifier")
    if mode == "vision":
        vok, vout = verify_vision(dt, sid, wt, task)
        return vok, (out + "\n--- VISION VERIFIER ---\n" + vout)
    # POLICY: validation is ALWAYS done by Opus (Claude Code), regardless of which (possibly
    # weaker) worker did the task. The deterministic gate above is a cheap pre-filter; the
    # final judgment that the change is genuinely correct is Opus's. Disable with
    # opus_validate:false only for trivially-objective tasks where the command is sufficient.
    if mode == "llm" or task.get("opus_validate", True):
        lok, lout = run_llm_verifier(dt, sid, wt, task, tag)
        return lok, (out + "\n--- OPUS VALIDATION ---\n" + lout)
    return ok, out


import re as _re

def error_signature(out: str) -> str:
    """A stable fingerprint of WHY verification failed: the deduped set of error-bearing lines
    with volatile bits (line/col numbers, durations, hex, ms) blanked. Same signature across
    iterations == the worker keeps hitting the same wall."""
    keys = ("error", "fail", "expected", "cannot", "not a function", "is not defined",
            "referenceerror", "typeerror", "no test", "✗", "assert")
    sigs = set()
    for l in out.splitlines():
        ll = l.lower()
        if any(k in ll for k in keys):
            sigs.add(_re.sub(r"\d+|0x[0-9a-f]+|\s+", " ", ll).strip())
    return "\n".join(sorted(sigs))


def scope_ok(dt, sid, wt, allowed):
    # List changed paths as BARE paths (no status prefix) to avoid fragile column slicing of
    # `git status --porcelain` (which dropped a leading char and caused false OUT_OF_SCOPE):
    #   - tracked modifications/staged: `git diff --name-only HEAD`
    #   - brand-new untracked FILES (individually, not collapsed dirs): `git ls-files --others`
    _, out = dt.exec(sid,
        f"cd {wt} && {{ git diff --name-only HEAD; git ls-files --others --exclude-standard; }} "
        f"| grep -v node_modules | sort -u", timeout=30)
    changed = [l.strip() for l in out.strip().splitlines() if l.strip()]
    # a changed path is in-scope if it equals an allowed entry, OR sits under an allowed
    # directory entry (allowed_files may list a dir like "scripts/qa/detectors/"). Normalize
    # trailing slashes so "dir" and "dir/" compare equal.
    norm = lambda p: p.rstrip("/")
    allowset = {norm(a) for a in allowed}
    def ok(f):
        f = norm(f)
        if f in allowset:
            return True
        return any(f == a or f.startswith(a + "/") for a in allowset)
    extra = [f for f in changed if not ok(f)]
    return (len(extra) == 0), changed, extra


def run_task(dt, gh, sid, integration_branch, task, merge_lock=None):
    tid = task["id"]
    branch = f"agent/{tid}"
    wt = f"/home/daytona/wt/{tid}"
    allowed = task["allowed_files"]
    # worker_model is assigned PER TASK by the brain, by difficulty (Opus for hard,
    # MiniMax for well-specified). It is NOT a blanket MiniMax default; MINIMAX here is
    # only the fallback when a task spec omits an explicit classification.
    model = task.get("worker_model", MINIMAX)
    # high cap — the no-progress detector (below) is the real guard against stuck loops,
    # so genuinely-progressing tasks can iterate a lot without a stall burning cost forever.
    max_iters = task.get("max_iters", 60)

    # ensure the sandbox is running — a long task can outlast autoStopInterval, or a
    # degraded poll window can let it auto-stop mid-run. start() is idempotent (~1.2s).
    dt.start(sid)
    log(f"[{tid}] worktree {branch} from {integration_branch}")
    # single-branch clone has a restricted fetch refspec, so the remote-tracking ref for
    # the integration branch won't exist — fetch it explicitly into refs/remotes/origin/.
    ib_ref = f"refs/remotes/origin/{integration_branch}"
    _, wtout = dt.exec(sid,
        f"cd ~/babylon-cinema && git worktree remove --force {wt} 2>/dev/null; "
        f"git branch -D {branch} 2>/dev/null; "
        f"git fetch --depth 1 origin +refs/heads/{integration_branch}:{ib_ref} && "
        f"git worktree add -b {branch} {wt} {ib_ref} && "
        f"ln -sfn ~/babylon-cinema/node_modules {wt}/node_modules && echo WORKTREE_OK", timeout=120)
    if "WORKTREE_OK" not in wtout:
        return {"id": tid, "status": "WORKTREE_FAILED", "out": wtout[-400:]}

    # SPARSE-CHECKOUT FIX: the golden is a sparse clone (code dirs only, to fit the 3G disk),
    # so a task whose allowed_files live OUTSIDE the base cone (e.g. src/modules/, apps/admin/,
    # apps/rio-mobile/) gets NO checkout — the worker edits files that aren't in the tree,
    # verify can't find them, and `git add` rejects the path as outside-sparse. Add each
    # allowed file's directory to THIS worktree's cone so its real content is materialized.
    # include allowed_files' dirs (where the worker WRITES) plus task `sparse_extra` dirs
    # (modules the verify step IMPORTS at runtime, e.g. a script that imports src/modules/citygen
    # — those must be materialized too or the test fails to import them).
    sp_dirs = sorted({os.path.dirname(f) for f in allowed if os.path.dirname(f)}
                     | set(task.get("sparse_extra", [])))
    if sp_dirs:
        addlist = " ".join(json.dumps(d) for d in sp_dirs)
        _, spout = dt.exec(sid, f"cd {wt} && git sparse-checkout add {addlist} && echo SPARSE_OK",
                           timeout=90)
        if "SPARSE_OK" not in spout:
            return {"id": tid, "status": "WORKTREE_FAILED", "out": "sparse add failed: " + spout[-300:]}

    spec = task["spec"]
    worker_timeout = task.get("worker_timeout_s", 480)   # kill a single hung worker
    task_budget = task.get("task_budget_s", 1500)        # wall-clock cap for the whole task
    t_task = time.time()
    minimax_fails = 0
    passed = False
    i = 0
    last_out = ""           # for no-progress detection
    stuck = 0
    no_progress_limit = task.get("no_progress_limit", 4)
    last_fail_out = ""      # recorded into the failure result for the dashboard
    for i in range(1, max_iters + 1):
        if time.time() - t_task > task_budget:
            log(f"[{tid}] task budget {task_budget}s exceeded — aborting")
            cleanup_worktree(dt, sid, wt, branch)
            return {"id": tid, "status": "TIMEOUT_BUDGET", "iters": i - 1}
        log(f"[{tid}] iter {i}/{max_iters} worker={model}")
        try:
            run_worker(dt, sid, wt, model, spec, f"{tid}-{i}", worker_timeout)
        except TimeoutError as e:
            log(f"[{tid}] worker HUNG iter {i}: {e}")
            # a hung worker counts as a failed iteration; escalate and retry
            if model == MINIMAX or model.startswith("dumont"):
                model = OPUS
                log(f"[{tid}] hung cheap-lane -> escalate to Opus")
            spec = task["spec"] + "\n\nPREVIOUS ATTEMPT TIMED OUT. Be fast and minimal; make ONLY the required edits."
            continue
        try:
            ok, out = verify_task(dt, sid, wt, task, f"{tid}-{i}")
        except TimeoutError as e:
            # a hung verification stage (e.g. evidence_cmd / vision) is retryable, never fatal
            log(f"[{tid}] verify HUNG iter {i}: {e}")
            ok, out = False, f"verification timed out: {e}"
        if ok:
            log(f"[{tid}] verify PASS on iter {i}")
            passed = True
            break
        log(f"[{tid}] verify FAIL iter {i}")
        last_fail_out = out
        # SEMANTIC no-progress detection: compare the ERROR SIGNATURE, not the whole output.
        # Raw output varies run-to-run (timings, paths, line order) so exact-match almost never
        # fires and a doomed task burns every iteration. The signature is the deduped set of
        # error-bearing lines (stripped of volatile numbers) — same failure repeating N times
        # means the worker isn't converging, so abort early and let the brain replan.
        sig = error_signature(out)
        if sig and sig == last_out:
            stuck += 1
        else:
            stuck = 0; last_out = sig
        if stuck >= no_progress_limit:
            log(f"[{tid}] same error {no_progress_limit}x — stuck, stopping")
            cleanup_worktree(dt, sid, wt, branch)
            return {"id": tid, "status": "STUCK_NO_PROGRESS", "iters": i, "model": model,
                    "error": out[-400:]}
        # ESCALATION: any non-claude lane (codex OR dumont/minimax) that keeps failing verify is
        # likely on a task too hard for it — hand it to the claude lane after 2 fails. claude is
        # the strongest worker; pipeline/multi-symbol tasks belong there (granularity routing).
        if model != OPUS:
            minimax_fails += 1
            if minimax_fails >= 2:
                log(f"[{tid}] escalating {model} -> claude (repeated verify fail)")
                model = OPUS
                stuck = 0; last_out = ""   # give claude a fresh no-progress budget
        spec = (task["spec"] + "\n\nVERIFIER FEEDBACK — previous attempt FAILED. "
                "Fix it. The verification command output was:\n" + out[-1500:])
    if not passed:
        cleanup_worktree(dt, sid, wt, branch)
        return {"id": tid, "status": "FAILED_MAX_ITERS", "iters": max_iters, "model": model,
                "error": last_fail_out[-400:]}

    # scope check
    sok, changed, extra = scope_ok(dt, sid, wt, allowed)
    if not sok:
        return {"id": tid, "status": "OUT_OF_SCOPE", "extra": extra, "changed": changed}

    # commit + push — stage ONLY the allowed files (surgical). `git add -A` is wrong here:
    # the sparse checkout omits root .gitignore, so -A would stage the node_modules symlink.
    log(f"[{tid}] commit + push")
    add_list = " ".join(json.dumps(f) for f in allowed)
    # force-push (+ref): an agent branch is owned solely by THIS task and recreated from the
    # integration base every run, so a leftover remote branch from a prior run would otherwise
    # reject the push as non-fast-forward -> a silent push failure later surfaces as a confusing
    # PR-create "head invalid" 422. Echo a sentinel so we can confirm the push actually landed.
    _, pushout = dt.exec(sid, f"cd {wt} && git add -- {add_list} && "
                 f"git commit -q -m {json.dumps(task['commit'])} && "
                 f"git push -u origin +{branch} 2>&1 && echo PUSH_OK || echo PUSH_FAIL", timeout=120)
    if "PUSH_OK" not in pushout:
        cleanup_worktree(dt, sid, wt, branch)
        return {"id": tid, "status": "ERROR", "error": "git push failed: " + pushout[-300:]}

    # open PR vs integration branch (NOT main)
    pr = gh.create_pr(head=branch, base=integration_branch,
                      title=task["commit"],
                      body=f"Automated by orchestrator. Task `{tid}`.\nVerify: `{task['verify_cmd']}`")
    num = pr["number"]
    log(f"[{tid}] PR #{num} -> {integration_branch}")

    # integration agent: scope check on PR files + final merge into integration branch
    pr_files = gh.pr_files(num)
    pr_extra = [f for f in pr_files if f not in allowed and not f.startswith("node_modules")]
    if pr_extra:
        return {"id": tid, "status": "PR_OUT_OF_SCOPE", "pr": num, "extra": pr_extra}
    # serialize merges — many parallel tasks merge into the SAME integration branch
    import contextlib
    with (merge_lock or contextlib.nullcontext()):
        merged = gh.merge_pr(num, "squash")
    log(f"[{tid}] integration merged PR #{num}: {merged.get('merged')}")

    # cleanup worktree
    dt.exec(sid, f"cd ~/babylon-cinema && git worktree remove --force {wt} 2>/dev/null; "
                 f"git branch -D {branch} 2>/dev/null; git worktree prune", timeout=60)
    return {"id": tid, "status": "MERGED", "pr": num, "iters": i, "model": model}


import threading
from concurrent.futures import ThreadPoolExecutor

STATE_DIR = "state"


def state_path(ib):
    slug = ib.replace("/", "_")
    return f"{STATE_DIR}/state-{slug}.json"


def load_state(ib):
    import os
    os.makedirs(STATE_DIR, exist_ok=True)
    p = state_path(ib)
    if os.path.exists(p):
        return json.load(open(p))
    return {}


def save_state(ib, state, lock):
    with lock:
        json.dump(state, open(state_path(ib), "w"), indent=2)


def already_done(gh, ib, task, state):
    """Resume idempotency: a task is done if the journal says MERGED, or its files are
    already on the integration branch (a prior run merged it before we journaled)."""
    if state.get(task["id"], {}).get("status") == "MERGED":
        return True
    try:
        if task["allowed_files"] and all(gh.file_exists(f, ib) for f in task["allowed_files"]):
            return True
    except Exception:
        pass
    return False


def resume_merge_if_open(gh, ib, task, merge_lock):
    """Crash between push and merge: if an open PR already exists for this task's branch,
    just merge it (no rework). Returns a result dict if handled, else None."""
    branch = f"agent/{task['id']}"
    try:
        pr = gh.open_pr_for(branch, ib)
        if pr:
            import contextlib
            with (merge_lock or contextlib.nullcontext()):
                gh.merge_pr(pr["number"], "squash")
            return {"id": task["id"], "status": "MERGED", "pr": pr["number"], "resumed": "pr"}
    except Exception:
        pass
    return None


def _infra_dead(err: str) -> bool:
    return ("not running" in err) or ("not found" in err) or ("HTTP 404" in err)


def acquire_live(dt, free, ght, provision_fresh):
    """Get a sandbox that is actually alive. Drains dead ones from the queue; if none
    left, provisions a fresh golden (cattle, not pets)."""
    while True:
        try:
            sid = free.get_nowait()
        except Exception:
            sid = None
        if sid is None:
            return provision_fresh()
        if dt.is_alive(sid):
            return sid
        # dead — drop it, try next


def main():
    import queue
    import os
    cfg = json.load(open(sys.argv[1]))
    key = cfg.get("daytona_key") or os.environ.get("DAYTONA_API_KEY")
    if not key:
        raise SystemExit("set DAYTONA_API_KEY env var (or daytona_key in config)")
    dt = Daytona(key)
    gh = GitHub(gh_token())
    ib = cfg["integration_branch"]
    concurrency = cfg.get("max_concurrent", 45)
    per_lane_max = cfg.get("per_lane_max", 15)   # at most N concurrent per worker lane
    global_deadline = cfg.get("global_deadline_s", 7200)
    ephemeral = cfg.get("ephemeral", False)      # cattle: destroy sandbox after each task
    max_retry = cfg.get("task_retries", 3)       # retry a task on sandbox-death
    t_batch = time.time()

    def lane_key(model):
        for k in ("claude", "codex", "dumont", "minimax"):
            if str(model).startswith(k):
                return k
        return str(model)
    # one semaphore per lane -> caps concurrency PER MODEL (e.g. <=15 codex at once, the
    # ChatGPT-safe max). With 3 lanes that's <=45 total; extra tasks queue and wait.
    lane_sems = {}

    pool = cfg.get("pool_sids") or ([cfg["golden_sid"]] if cfg.get("golden_sid") else [])
    cloud = [t for t in cfg["tasks"] if t.get("location", "cloud") == "cloud"]
    local = [t for t in cfg["tasks"] if t.get("location") == "local"]

    state = load_state(ib)
    state_lock = threading.Lock()
    merge_lock = threading.Lock()
    results, results_lock = [], threading.Lock()
    metrics = {"provisioned": 0, "task_retries": 0}
    metrics_lock = threading.Lock()

    from setup_golden import provision_one
    from creds import claude_credentials_b64, opencode_auth_b64
    cc, oc, ght = claude_credentials_b64(), opencode_auth_b64(), gh_token()

    def provision_fresh():
        # leave it RUNNING (stop_when_done=False) — no stop/start race that Daytona
        # could delete into; inject creds and use immediately.
        nsid = provision_one(dt, ght, cc, oc, "[heal] ", stop_when_done=False)
        inject_into_sandbox(dt, nsid, ght)
        with metrics_lock:
            metrics["provisioned"] += 1
        log(f"provisioned fresh sandbox {nsid[:8]}")
        return nsid

    # RESUME: skip done; also merge any orphaned open PR from a prior crash
    pending = []
    for t in cloud:
        if already_done(gh, ib, t, state):
            log(f"[{t['id']}] already done — skip (resume)")
            results.append({"id": t["id"], "status": "MERGED", "resumed": True}); continue
        rm = resume_merge_if_open(gh, ib, t, merge_lock)
        if rm:
            log(f"[{t['id']}] open PR merged (resume)")
            results.append(rm); continue
        pending.append(t)

    # REAPER: remove strays from prior runs (cost + clutter), keep declared pool
    try:
        dt.reap_strays(keep=pool)
    except Exception as e:
        log(f"WARN reaper: {e}")

    free = queue.Queue()
    live_sids = []
    try:
        dead = 0
        for sid in pool:
            try:
                dt.start(sid); inject_into_sandbox(dt, sid, ght)
                # tight idle auto-stop so a sandbox left idle (task done, run crashed) stops
                # itself in ~1 min instead of billing for the 120-min default. Safe mid-run:
                # 10s exec_wait polling keeps an active sandbox's lastActivity fresh.
                dt.set_autostop(sid, cfg.get("idle_autostop_min", 1))
                free.put(sid); live_sids.append(sid)
                log(f"pool sandbox ready {sid[:8]}")
            except Exception as e:
                log(f"WARN pool sandbox {sid[:8]} unavailable: {e}")
                dead += 1
        # AUTO-HEAL (conditional, AFTER the whole pool is tried): only replace as many dead
        # sandboxes as the pending work actually needs. Provisioning a fresh golden costs minutes
        # and blocks, so 1 dead of 8 with only 3 tasks heals NOTHING (7 live already cover it).
        deficit = max(0, len(pending) - len(live_sids))
        for _ in range(min(dead, deficit)):
            try:
                nsid = provision_fresh()
                dt.set_autostop(nsid, cfg.get("idle_autostop_min", 1))
                free.put(nsid); live_sids.append(nsid)
                log(f"healed: fresh sandbox {nsid[:8]} added")
            except Exception as e2:
                log(f"WARN could not heal: {e2}")
        if dead and deficit == 0:
            log(f"skip heal — {len(live_sids)} live cover {len(pending)} tasks ({dead} dead ignored)")
        # build a semaphore per lane present in the pending work (cap = per_lane_max)
        for t in pending:
            lk = lane_key(t.get("worker_model", "?"))
            lane_sems.setdefault(lk, threading.Semaphore(per_lane_max))
        # thread pool can hold all lanes' caps at once; the per-lane semaphores throttle.
        effective = min(concurrency, len(lane_sems) * per_lane_max, max(1, len(pending)))
        log(f"lanes: {[ (k, per_lane_max) for k in lane_sems ]} | pool={len(live_sids)} | effective={effective}")

        def worker(task):
            if time.time() - t_batch > global_deadline:
                return {"id": task["id"], "status": "SKIPPED_DEADLINE"}
            sem = lane_sems[lane_key(task.get("worker_model", "?"))]
            sem.acquire()   # wait for a free slot in this task's lane (<=per_lane_max)
            try:
                attempt = 0
                while True:
                    attempt += 1
                    sid = None
                    try:
                        sid = acquire_live(dt, free, ght, provision_fresh)
                        r = run_task(dt, gh, sid, ib, task, merge_lock)
                        if not ephemeral and dt.is_alive(sid):
                            free.put(sid)                 # healthy → back to pool
                        elif ephemeral:
                            try: dt.delete(sid)            # cattle → destroy
                            except Exception: pass
                        break
                    except Exception as e:
                        msg = str(e)
                        if _infra_dead(msg) and attempt <= max_retry:
                            with metrics_lock: metrics["task_retries"] += 1
                            log(f"[{task['id']}] sandbox died (attempt {attempt}) — retry on fresh")
                            continue                       # acquire_live will heal
                        r = {"id": task["id"], "status": "ERROR", "error": msg}
                        log(f"[{task['id']}] ERROR {msg}")
                        if sid and not ephemeral and dt.is_alive(sid):
                            free.put(sid)
                        break
            finally:
                sem.release()
            with state_lock:
                state[task["id"]] = r
                json.dump(state, open(state_path(ib), "w"), indent=2)
            with results_lock:
                results.append(r)
            return r

        with ThreadPoolExecutor(max_workers=effective) as ex:
            list(ex.map(worker, pending))
    finally:
        # stop EVERY sandbox currently in the org (pool + any fresh-provisioned heals that
        # aren't tracked locally) so billing never leaks after a run.
        log("stopping all sandboxes (billing $0)")
        try:
            for x in dt.list():
                try: dt.stop(x["id"])
                except Exception: pass
        except Exception as e:
            log(f"WARN final stop sweep: {e}")

    with state_lock:
        json.dump(state, open(state_path(ib), "w"), indent=2)

    print("\n==== RESULTS ====")
    merged = sum(1 for r in results if r.get("status") == "MERGED")
    for r in results:
        print(json.dumps(r))
    print(f"\n{merged}/{len(results)} merged | fresh sandboxes provisioned: "
          f"{metrics['provisioned']} | task retries: {metrics['task_retries']} | "
          f"elapsed: {round(time.time()-t_batch)}s")
    if local:
        print("\n==== LOCAL LANE (run on the Mac — iPhone/simulator/macOS) ====")
        for t in local:
            print(f"- {t['id']}: {t.get('local_note', 'needs on-device/simulator/macOS verification')}")


if __name__ == "__main__":
    main()
