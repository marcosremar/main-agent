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
import sys
import time

from daytona import Daytona
from creds import inject_into_sandbox, gh_token
from github import GitHub

OPUS = "claude"                       # claude CLI, Opus 4.8
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
    prompt = '"$(cat %s)"' % specfile
    if model == OPUS:
        cmd = f"{OPATH}; cd {wt} && claude -p {prompt} --model claude-opus-4-8 --permission-mode acceptEdits"
        match = "claude -p"
    elif model.startswith("dumont"):
        # dumont-code agent on MiniMax M2.7 — prebuilt binary (cheap, fast worker). Login via
        # ~/.dumont/.credentials.json, key via $MINIMAX_API_KEY (~/.profile, loaded by bash -l).
        # model form "dumont" -> minimax/m2-7; "dumont:provider/model" -> that dumont model key
        dmodel = model.split(":", 1)[1] if ":" in model else "minimax/m2-7"
        cmd = (f"export DUMONT_CONFIG=$HOME/.dumont/dumont.json; cd {wt} && "
               f"~/bin/dumont -p {prompt} --model {dmodel} "
               "--dangerously-skip-permissions --output-format text")
        match = "dumont"
    else:
        cmd = f"{OPATH}; cd {wt} && opencode run -m {model} {prompt}"
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
    criteria = task.get("criteria", "The change fully and correctly satisfies the task spec.")
    prompt = (
        "You are an INDEPENDENT, ADVERSARIAL verifier. You did NOT write this code. Do not "
        "trust any prior claim. Inspect the ACTUAL files changed in this worktree (use git "
        "diff / read them) and any evidence artifacts. Try hard to REFUTE that the frozen "
        "acceptance criteria below are met. Default to FAIL if uncertain.\n\n"
        f"FROZEN ACCEPTANCE CRITERIA:\n{criteria}\n\n"
        "Check each criterion against reality. Then output EXACTLY one final line: "
        "'VERDICT: PASS' if every criterion genuinely holds, otherwise "
        "'VERDICT: FAIL: <short reason>'. Output nothing after that line.")
    specfile = f"/tmp/verify-{tag}.txt"
    logfile = f"/tmp/verify-{tag}.log"
    dt.exec(sid, f"echo {b64(prompt)} | base64 -d > {specfile}", timeout=30)
    pre = f"{evidence}; " if evidence else ""
    cmd = (f"{OPATH}; cd {wt} && {pre}claude -p \"$(cat {specfile})\" "
           "--model claude-opus-4-8 --permission-mode acceptEdits")
    dt.exec_detached(sid, cmd, logfile)
    out = dt.exec_wait(sid, "claude -p", logfile, timeout=task.get("worker_timeout_s", 480))
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
    if mode == "llm":
        lok, lout = run_llm_verifier(dt, sid, wt, task, tag)
        return lok, (out + "\n--- LLM VERIFIER ---\n" + lout)
    return ok, out


def scope_ok(dt, sid, wt, allowed):
    _, out = dt.exec(sid, f"cd {wt} && git status --porcelain | grep -v node_modules", timeout=30)
    changed = [l[3:] for l in out.strip().splitlines() if l.strip()]
    extra = [f for f in changed if f not in allowed]
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
    max_iters = task.get("max_iters", 20)

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

    spec = task["spec"]
    worker_timeout = task.get("worker_timeout_s", 480)   # kill a single hung worker
    task_budget = task.get("task_budget_s", 1500)        # wall-clock cap for the whole task
    t_task = time.time()
    minimax_fails = 0
    passed = False
    i = 0
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
            if model == MINIMAX:
                model = OPUS
                log(f"[{tid}] hung MiniMax -> escalate to Opus")
            spec = task["spec"] + "\n\nPREVIOUS ATTEMPT TIMED OUT. Be fast and minimal; make ONLY the required edits."
            continue
        ok, out = verify_task(dt, sid, wt, task, f"{tid}-{i}")
        if ok:
            log(f"[{tid}] verify PASS on iter {i}")
            passed = True
            break
        log(f"[{tid}] verify FAIL iter {i}")
        if model == MINIMAX:
            minimax_fails += 1
            if minimax_fails >= 2:
                log(f"[{tid}] escalating worker MiniMax -> Opus")
                model = OPUS
        spec = (task["spec"] + "\n\nVERIFIER FEEDBACK — previous attempt FAILED. "
                "Fix it. The verification command output was:\n" + out[-1500:])
    if not passed:
        cleanup_worktree(dt, sid, wt, branch)
        return {"id": tid, "status": "FAILED_MAX_ITERS", "iters": max_iters}

    # scope check
    sok, changed, extra = scope_ok(dt, sid, wt, allowed)
    if not sok:
        return {"id": tid, "status": "OUT_OF_SCOPE", "extra": extra, "changed": changed}

    # commit + push — stage ONLY the allowed files (surgical). `git add -A` is wrong here:
    # the sparse checkout omits root .gitignore, so -A would stage the node_modules symlink.
    log(f"[{tid}] commit + push")
    add_list = " ".join(json.dumps(f) for f in allowed)
    dt.exec(sid, f"cd {wt} && git add -- {add_list} && "
                 f"git commit -q -m {json.dumps(task['commit'])} && "
                 f"git push -u origin {branch} 2>&1 | tail -2", timeout=120)

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
    return {"id": tid, "status": "MERGED", "pr": num, "iters": i}


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
    concurrency = cfg.get("max_concurrent", 15)
    global_deadline = cfg.get("global_deadline_s", 7200)
    ephemeral = cfg.get("ephemeral", False)      # cattle: destroy sandbox after each task
    max_retry = cfg.get("task_retries", 3)       # retry a task on sandbox-death
    t_batch = time.time()

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
        for sid in pool:
            try:
                dt.start(sid); inject_into_sandbox(dt, sid, ght)
                free.put(sid); live_sids.append(sid)
                log(f"pool sandbox ready {sid[:8]}")
            except Exception as e:
                log(f"WARN pool sandbox {sid[:8]} unavailable: {e}")
        effective = min(concurrency, max(1, len(pending)))

        def worker(task):
            if time.time() - t_batch > global_deadline:
                return {"id": task["id"], "status": "SKIPPED_DEADLINE"}
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
