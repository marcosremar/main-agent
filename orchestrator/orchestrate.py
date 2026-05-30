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
import fcntl
import json
import os
import queue
import signal as _signal
import sys
import time
import uuid
import atexit
from datetime import datetime, timezone

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
WORKER_CLAUDE_MODEL = os.environ.get("WORKER_CLAUDE_MODEL", "claude-opus-4-8")
MINIMAX = "minimax/MiniMax-M2.7"
OPATH = "export PATH=$PATH:$HOME/.opencode/bin"


class Escalate(Exception):
    def __init__(self, new_lane, spec):
        self.new_lane = new_lane
        self.spec = spec
        super().__init__(f"escalate to {new_lane}")


def log(msg): print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def _wt_file_hashes(dt, sid, wt, files):
    """Return {filepath: sha1_hex} by running git hash-object in the worktree."""
    h = {}
    for f in files:
        _, out = dt.exec(sid, f"cd {wt} && git hash-object {json.dumps(f)} 2>/dev/null",
                         timeout=15)
        sha = out.strip()
        if sha:
            h[f] = sha
    return h


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
        cmd = (f"{OPATH}; cd \"{wt}\" && cat {specfile} | claude -p --model {WORKER_CLAUDE_MODEL} "
               f"--permission-mode acceptEdits --output-format stream-json --verbose")
        match = f"claude -p -i {tag}"
    elif model.startswith("dumont"):
        # dumont-code agent on MiniMax M2.7 — prebuilt binary. Login via
        # ~/.dumont/.credentials.json, key via $MINIMAX_API_KEY (~/.profile, loaded by bash -l).
        dmodel = model.split(":", 1)[1] if ":" in model else "minimax/m2-7"
        cmd = (f"export DUMONT_CONFIG=$HOME/.dumont/dumont.json; cd \"{wt}\" && "
               f"cat {specfile} | ~/bin/dumont -p --model {dmodel} "
               "--dangerously-skip-permissions --output-format text")
        match = f"dumont -p -i {tag}"
    elif model.startswith("codex"):
        # OpenAI Codex CLI (gpt-5.3-codex-spark). ChatGPT auth at ~/.codex/auth.json. Reads
        # instructions from stdin. Externally sandboxed (Daytona) -> bypass approvals.
        cxmodel = model.split(":", 1)[1] if ":" in model else "gpt-5.3-codex-spark"
        cmd = (f"cat {specfile} | codex exec -m {cxmodel} "
               f"--dangerously-bypass-approvals-and-sandbox --skip-git-repo-check -C \"{wt}\"")
        match = f"codex exec -i {tag}"
    else:
        cmd = f"{OPATH}; cd \"{wt}\" && cat {specfile} | opencode run -m {model}"
        match = "opencode run"
    dt.exec_detached(sid, cmd, logfile)
    return dt.exec_wait(sid, match, logfile, timeout=worker_timeout)


def cleanup_worktree(dt, sid, wt, branch):
    # always remove worktree FIRST (branch -D fails if checked-out in another worktree)
    # then force-delete the branch; use -f as fallback if branch is still checked-out elsewhere
    dt.exec(sid, f"cd ~/babylon-cinema && git worktree remove --force {wt} 2>/dev/null; "
                 f"git branch -D -f {branch} 2>/dev/null; git worktree prune", timeout=60)


def verify(dt, sid, wt, verify_cmd):
    """Deterministic verification = run the task's verify command. Returns (ok, output)."""
    full = (f"{OPATH}; cd \"{wt}\" && {verify_cmd} 2>&1 | tail -100; "
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
    pre = f"{evidence} && " if evidence else ""
    cmd = (f"{OPATH}; cd \"{wt}\" && {pre}cat {specfile} | claude -p "
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
    evidence = task.get("evidence_cmd", "")
    if evidence:
        _, evout = dt.exec(sid, f"{OPATH}; cd \"{wt}\" && {evidence}",
                           timeout=task.get("worker_timeout_s", 480))
        if "VERIFY_EXIT=0" not in evout:
            return False, f"evidence_cmd failed: {evout[-300:]}"
    img = task["evidence_image"]
    _, out = dt.exec(sid, f"cd \"{wt}\" && base64 -w0 {img} 2>/dev/null || base64 {img}", timeout=60)
    data = out.strip().splitlines()[-1] if out.strip() else ""
    if not data:
        return False, f"no evidence image at {img}"
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    try:
        tmp.write(_b64.b64decode(data))
        tmp.close()
        spec_lines = task.get("spec", "").splitlines()[:5]
        criteria = task.get("criteria", "")
        truncated_spec = "\n".join(spec_lines)
        r = judge_image(tmp.name, criteria, model=task.get("vision_model"))
    finally:
        os.remove(tmp.name)
    return r["ok"], f"vision score={r.get('score')} | {r.get('verdict')}\n{truncated_spec}\n{r.get('raw','')}"


def verify_task(dt, sid, wt, task, tag, opus_validate_default=True):
    """Dispatch verification (layered): deterministic command gate FIRST (free/cheap), then
    an optional fuzzy verifier — `vision` (cheap vision model on a screenshot/render) or
    `llm` (independent adversarial Opus). Pass requires ALL configured stages."""
    ok, out = True, ""
    mode = task.get("verifier")
    has_vision = mode == "vision"
    if task.get("verify_cmd"):
        ok, out = verify(dt, sid, wt, task["verify_cmd"])
        if not ok:
            return ok, out
    elif has_vision:
        pass  # vision tasks without verify_cmd are accepted (evidence_cmd is the gate)
    else:
        return False, "no verify_cmd and no vision verifier"
    if has_vision:
        vok, vout = verify_vision(dt, sid, wt, task)
        return vok, (out + "\n--- VISION VERIFIER ---\n" + vout)
    # POLICY: validation is ALWAYS done by Opus (Claude Code), regardless of which (possibly
    # weaker) worker did the task. The deterministic gate above is a cheap pre-filter; the
    # final judgment that the change is genuinely correct is Opus's. Disable with
    # opus_validate:false only for trivially-objective tasks where the command is sufficient.
    if mode == "llm" or task.get("opus_validate", opus_validate_default):
        lok, lout = run_llm_verifier(dt, sid, wt, task, tag)
        return lok, (out + "\n--- OPUS VALIDATION ---\n" + lout)
    return ok, out


import re as _re

_ERROR_PAT = _re.compile(r"(?:^|\n)(Error|Error:|failed|FAILED)", _re.IGNORECASE | _re.MULTILINE)

def error_signature(out: str) -> str:
    """A stable fingerprint of WHY verification failed: the deduped set of error-bearing lines
    with volatile bits (line/col numbers, durations, hex, ms) blanked. Same signature across
    iterations == the worker keeps hitting the same wall."""
    sigs = set()
    for l in out.splitlines():
        ll = l.lower()
        if _ERROR_PAT.search(l):
            sigs.add(_re.sub(r"\d+|0x[0-9a-f]+|\s+", " ", ll).strip())
    return "\n".join(sorted(sigs))


def scope_ok(dt, sid, wt, allowed):
    # List changed paths as BARE paths (no status prefix) to avoid fragile column slicing of
    # `git status --porcelain` (which dropped a leading char and caused false OUT_OF_SCOPE):
    #   - tracked modifications:  `git diff --name-only HEAD`
    #     NOTE: git diff includes DELETED files — a removed file IS a changed path and is
    #     checked against allowed_files just like a new/modified file. This is intentional;
    #     if a task deletes a file it must be in allowed_files to pass scope_ok.
    #   - staged in index:        `git diff --cached --name-only`
    #   - brand-new untracked FILES (individually, not collapsed dirs): `git ls-files --others`
    _, out = dt.exec(sid,
        f"cd {wt} && {{ git diff --name-only HEAD; git diff --cached --name-only; "
        f"git ls-files --others --exclude-standard; }} "
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


def run_task(dt, gh, sid, integration_branch, task, merge_lock, lane_sems, lane_key):
    tid = task["id"]
    # sanitize tid for path use: / creates subdirectories in wt path, replace with -
    safe_tid = tid.replace("/", "-")
    branch = f"agent/{safe_tid}"
    wt = f"/home/daytona/wt/{safe_tid}"
    allowed = task["allowed_files"]
    # clean up stale tmp artifacts from prior runs — they accumulate and waste disk
    dt.exec(sid, "rm -f /tmp/spec-{tid}-*.txt /tmp/work-{tid}-*.log /tmp/verify-{tid}-*.txt "
                "/tmp/spec-*.txt /tmp/work-*.log /tmp/verify-*.txt 2>/dev/null; "
                "rm -rf /tmp/bundle-* /tmp/citygen-* 2>/dev/null; true", timeout=30)
    if not allowed:
        return {"id": tid, "status": "ERROR", "error": "allowed_files is empty — nothing to commit"}
    # worker_model is assigned PER TASK by the brain, by difficulty (Opus for hard,
    # MiniMax for well-specified). It is NOT a blanket MiniMax default; MINIMAX here is
    # only the fallback when a task spec omits an explicit classification.
    model = task.get("worker_model", MINIMAX)
    task["_lane"] = lane_key(model)
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
    # shallow fetch breaks on force-push ("shallow update not allowed"); use --unshallow
    # to convert to full history, or fall back to depth=50 which handles most rebase scenarios
    _, wtout = dt.exec(sid,
        f"cd ~/babylon-cinema && git worktree remove --force {wt} 2>/dev/null; "
        f"git branch -D -f {branch} 2>/dev/null; "
        f"git fetch --unshallow origin refs/heads/{integration_branch} 2>/dev/null || "
        f"git fetch --depth 50 origin refs/heads/{integration_branch}; "
        f"git worktree add -b {branch} {wt} origin/{integration_branch} && "
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
    # Use cone mode (--cone) for predictable behaviour — non-cone mode has different semantics.
    sp_dirs = sorted({os.path.dirname(f) for f in allowed if os.path.dirname(f)}
                     | set(task.get("sparse_extra", [])))
    if sp_dirs:
        addlist = " ".join(json.dumps(d) for d in sp_dirs)
        _, spout = dt.exec(sid,
                           f"cd {wt} && git sparse-checkout init --cone && "
                           f"git sparse-checkout add {addlist} && echo SPARSE_OK",
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
    no_progress_limit = task.get("no_progress_limit", 6)
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
            if model == MINIMAX or model.startswith("dumont"):
                log(f"[{tid}] hung cheap-lane -> escalate to Opus")
                new_lane = OPUS
                task["_lane"] = new_lane
                raise Escalate(new_lane,
                    task["spec"] + "\n\nPREVIOUS ATTEMPT TIMED OUT. Be fast and minimal; make ONLY the required edits.")
            spec = task["spec"] + "\n\nPREVIOUS ATTEMPT TIMED OUT. Be fast and minimal; make ONLY the required edits."
            continue
        try:
            ok, out = verify_task(dt, sid, wt, task, f"{tid}-{i}")
        except TimeoutError as e:
            log(f"[{tid}] verify HUNG iter {i}: {e}")
            ok, out = False, f"verification timed out: {e}"
        if time.time() - t_task > task_budget:
            log(f"[{tid}] task budget {task_budget}s exceeded after verify — aborting")
            cleanup_worktree(dt, sid, wt, branch)
            return {"id": tid, "status": "TIMEOUT_BUDGET", "iters": i}
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
        if sig:  # only track no-progress for known error patterns
            if sig == last_out:
                stuck += 1
            else:
                stuck = 0; last_out = sig
        # empty sig = silent failure (OOM, segfault); reset stuck so we don't abort prematurely
        else:
            stuck = 0
        if stuck >= no_progress_limit:
            log(f"[{tid}] same error {no_progress_limit}x — stuck, stopping")
            cleanup_worktree(dt, sid, wt, branch)
            return {"id": tid, "status": "STUCK_NO_PROGRESS", "iters": i, "model": model,
                    "error": out[-400:]}
        if model != OPUS:
            minimax_fails += 1
            if minimax_fails >= 2:
                log(f"[{tid}] escalating {model} -> claude (repeated verify fail)")
                new_lane = OPUS
                stuck = 0; last_out = ""
                task["_lane"] = new_lane
                raise Escalate(new_lane, spec)
        spec = (task["spec"] + "\n\nVERIFIER FEEDBACK — previous attempt FAILED. "
                "Fix it. The verification command output was:\n" + out[-4000:])
        # cap spec at 60KB — truncate oldest feedback if it grows beyond that
        MAX_SPEC = 60 * 1024
        if len(spec.encode()) > MAX_SPEC:
            # keep task spec header, drop oldest feedback entries from the tail
            header = task["spec"] + "\n\n"
            # gather feedback blocks (each starts with "VERIFIER FEEDBACK")
            parts = spec[len(header):].split("VERIFIER FEEDBACK")
            # keep newest 8 blocks (roughly 32KB of feedback + header = ~45KB)
            kept = "VERIFIER FEEDBACK".join(parts[-8:])
            spec = header + kept
    if not passed:
        cleanup_worktree(dt, sid, wt, branch)
        return {"id": tid, "status": "FAILED_MAX_ITERS", "iters": max_iters, "model": model,
                "error": last_fail_out[-400:]}

    # scope check
    sok, changed, extra = scope_ok(dt, sid, wt, allowed)
    if not sok:
        cleanup_worktree(dt, sid, wt, branch)
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
                 f"{{ git commit -q -m {json.dumps(task['commit'])} || true; }} && "
                 f"git push -u origin +{branch} 2>&1 && echo PUSH_OK || echo PUSH_FAIL", timeout=120)
    if "PUSH_OK" not in pushout:
        cleanup_worktree(dt, sid, wt, branch)
        return {"id": tid, "status": "ERROR", "error": "git push failed: " + pushout[-300:]}

    # open PR vs integration branch (NOT main) — serialize with merge to prevent race
    import contextlib
    with (merge_lock or contextlib.nullcontext()):
        verify_display = task.get("verify_cmd") or f"verifier:{task.get('verifier','llm')}"
        pr = gh.create_pr(head=branch, base=integration_branch,
                          title=task["commit"],
                          body=f"Automated by orchestrator. Task `{tid}`.\nVerify: `{verify_display}`")
        num = pr["number"]
        log(f"[{tid}] PR #{num} -> {integration_branch}")

        # integration agent: scope check on PR files + final merge into integration branch
        pr_files = gh.pr_files(num)
        pr_extra = [f for f in pr_files if f not in allowed and not f.startswith("node_modules")]
        if pr_extra:
            cleanup_worktree(dt, sid, wt, branch)
            return {"id": tid, "status": "PR_OUT_OF_SCOPE", "pr": num, "extra": pr_extra}
        # capture hashes BEFORE merge (on the agent branch —squash-merge makes IB HEAD match these)
        file_hashes = _wt_file_hashes(dt, sid, wt, allowed)
        import hashlib, json as _json
        allowed_files_hash = hashlib.sha1(_json.dumps(sorted(allowed)).encode()).hexdigest()
        try:
            merged = gh.merge_pr(num, "squash")
        except Exception as e:
            gh.close_pr(num, f"Merged failed: {e}")
            raise
    log(f"[{tid}] integration merged PR #{num}: {merged.get('merged')}")

    # cleanup worktree
    dt.exec(sid, f"cd ~/babylon-cinema && git worktree remove --force {wt} 2>/dev/null; "
                 f"git branch -D -f {branch} 2>/dev/null; git worktree prune", timeout=60)
    return {"id": tid, "status": "MERGED", "pr": num, "iters": i, "model": model,
            "file_hashes": file_hashes, "allowed_files_hash": allowed_files_hash}


import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

STATE_DIR = "state"


def state_path(ib):
    slug = ib.replace("/", "_")
    return f"{STATE_DIR}/state-{slug}.json"


def load_state(ib):
    import os
    os.makedirs(STATE_DIR, exist_ok=True)
    p = state_path(ib)
    if os.path.exists(p):
        try:
            return json.load(open(p))
        except json.JSONDecodeError:
            bak = p + f".bak-{int(time.time())}"
            os.rename(p, bak)
            log(f"state corrupted (JSONDecodeError) — backed up to {bak}, starting fresh")
            return {}
    return {}


def save_state(ib, state, lock):
    with lock:
        json.dump(state, open(state_path(ib), "w"), indent=2)


def _hash_files(gh, ib, files):
    """Return {filepath: sha1_hex} for files on the given branch (HEAD)."""
    h = {}
    for f in files:
        try:
            c = gh.file_contents(f, ib)
            import hashlib as _hm
            h[f] = _hm.sha1(f"blob {len(c)}\0{c}".encode()).hexdigest()
        except Exception:
            pass
    return h


def already_done(gh, ib, task, state):
    """Resume idempotency: a task is done if the journal says MERGED and the current IB
    HEAD has matching file hashes, OR the file hashes on IB match what was stored (prevents
    false-positive when allowed_files overlap between tasks). Also checks that allowed_files
    hash hasn't changed since the last run — different allowed_files = different scope."""
    tid = task["id"]
    entry = state.get(tid, {})
    if entry.get("status") == "MERGED":
        # hash-based recheck: ensure IB HEAD still has the same content we merged
        stored_hashes = entry.get("file_hashes", {})
        allowed = task["allowed_files"]
        if stored_hashes and allowed:
            current_hashes = _hash_files(gh, ib, allowed)
            if current_hashes == stored_hashes:
                return True
            # hashes differ — either IB was force-pushed or allowed_files changed
            allowed_hash = entry.get("allowed_files_hash", "")
            import hashlib, json as _json
            current_af_hash = hashlib.sha1(_json.dumps(sorted(allowed)).encode()).hexdigest()
            if current_af_hash != allowed_hash:
                return False  # allowed_files changed — not the same task scope
            return False  # IB diverged — re-run
        return True
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
    except RuntimeError as e:
        if "404" in str(e) or "not found" in str(e).lower():
            pass  # PR gone — fine
        else:
            log(f"WARN resume_merge_if_open [{task['id']}]: {e}")
    except Exception:
        pass
    return None


def _infra_dead(err: str) -> bool:
    el = err.lower()
    return (
        "sandbox" in el and ("not running" in el or "not found" in el)
    ) or ("http 404" in el and "sandbox" in el) or (
        # daytona returns these for genuinely missing/stopped sandboxes
        "not found" in el and any(x in el for x in ("sandbox", "toolbox", "workspace"))
    ) or "http 404" in el


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


def lane_key_func(model):
    """Returns the lane key for a model string: claude, dumont, codex, minimax, or the
    model string itself for unknown models."""
    for k in ("claude", "codex", "dumont", "minimax"):
        if str(model).startswith(k):
            return k
    return str(model)
    cfg = json.load(open(sys.argv[1]))
    # validate required fields up front — a missing key deep in the run produces confusing errors
    for _req_key in ("integration_branch", "tasks"):
        if _req_key not in cfg:
            raise SystemExit(f"config missing required field: {_req_key!r}")
    if not isinstance(cfg["tasks"], list):
        raise SystemExit("config 'tasks' must be a list")
    key = cfg.get("daytona_key") or os.environ.get("DAYTONA_API_KEY")
    if not key:
        raise SystemExit("set DAYTONA_API_KEY env var (or daytona_key in config)")
    dt = Daytona(key)
    gh = GitHub(gh_token())

    _stop = threading.Event()
    def _sigint(sig, frame):
        log("SIGINT — stopping batch (sandboxes will be stopped in finally)")
        _stop.set()
    _signal.signal(_signal.SIGINT, _sigint)
    _signal.signal(_signal.SIGTERM, _sigint)

    ib = cfg["integration_branch"]

    state_lock_f = f"{STATE_DIR}/.{ib.replace('/', '_')}.lock"
    os.makedirs(STATE_DIR, exist_ok=True)
    state_lock_fd = open(state_lock_f, "w")
    try:
        fcntl.flock(state_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log(f"ERROR: another orchestrator instance is running (lock held on {state_lock_f})")
        sys.exit(1)

    concurrency = cfg.get("max_concurrent", 45)
    per_lane_max = cfg.get("per_lane_max", 15)   # at most N concurrent per worker lane
    global_deadline = cfg.get("global_deadline_s", 7200)
    ephemeral = cfg.get("ephemeral", False)      # cattle: destroy sandbox after each task
    max_retry = cfg.get("task_retries", 3)       # retry a task on sandbox-death
    t_batch = time.time()

    lane_key = lane_key_func
    lane_sems = {}

    pool = cfg.get("pool_sids") or ([cfg["golden_sid"]] if cfg.get("golden_sid") else [])
    cloud = [t for t in cfg["tasks"] if t.get("location", "cloud") == "cloud"]
    local = [t for t in cfg["tasks"] if t.get("location") == "local"]

    state = load_state(ib)
    state_lock = threading.Lock()
    provision_lock = threading.Lock()
    merge_lock = threading.Lock()
    results, results_lock = [], threading.Lock()
    metrics = {"provisioned": 0, "task_retries": 0}
    metrics_lock = threading.Lock()

    from setup_golden import provision_one
    from creds import claude_credentials_b64, opencode_auth_b64
    cc, oc, ght = claude_credentials_b64(), opencode_auth_b64(), gh_token()

    def provision_fresh():
        acquired = provision_lock.acquire(timeout=300)
        if not acquired:
            log("provision_lock timeout (300s) — INFRA_DEAD")
            raise RuntimeError("INFRA_DEAD: provision_lock timeout 300s")
        try:
            nsid = provision_one(dt, ght, cc, oc, "[heal] ", stop_when_done=False)
            inject_into_sandbox(dt, nsid, ght)
            with metrics_lock:
                metrics["provisioned"] += 1
            log(f"provisioned fresh sandbox {nsid[:8]}")
            return nsid
        finally:
            provision_lock.release()

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
                # EPHEMERAL COPIES: destroy the sandbox a few minutes after it stops (idle, run
                # done) so copies don't linger holding disk/quota. The golden snapshot template
                # is untouched. -1 in config keeps a sandbox forever (warm-pool mode).
                ad = cfg.get("copy_autodelete_min", 5)
                if ad is not None and ad >= 0:
                    dt.set_autodelete(sid, ad)
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
                ad = cfg.get("copy_autodelete_min", 5)
                if ad is not None and ad >= 0:
                    dt.set_autodelete(nsid, ad)
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
            if _stop.is_set() or time.time() - t_batch > global_deadline:
                return {"id": task["id"], "status": "SKIPPED_DEADLINE"}
            entry_lane = lane_key(task.get("worker_model", "?"))
            cur_lane = entry_lane
            sem = lane_sems[cur_lane]
            sem.acquire()
            try:
                attempt = 0
                while True:
                    attempt += 1
                    sid = None
                    try:
                        sid = acquire_live(dt, free, ght, provision_fresh)
                        r = run_task(dt, gh, sid, ib, task, merge_lock, lane_sems, lane_key)
                        if not ephemeral and dt.is_alive(sid):
                            free.put(sid)
                        elif ephemeral:
                            try: dt.delete(sid)
                            except Exception: pass
                        break
                    except Escalate as exc:
                        old_lane = cur_lane
                        new_lane = lane_key(exc.new_lane)
                        if new_lane != old_lane:
                            lane_sems[old_lane].release()
                            lane_sems[new_lane].acquire()
                            cur_lane = new_lane
                            log(f"[{task['id']}] semaphore swapped {old_lane}->{new_lane}")
                        spec = exc.spec
                        continue
                    except Exception as e:
                        msg = str(e)
                        if _infra_dead(msg) and attempt <= max_retry:
                            with metrics_lock: metrics["task_retries"] += 1
                            log(f"[{task['id']}] sandbox died (attempt {attempt}) — retry on fresh")
                            continue
                        r = {"id": task["id"], "status": "ERROR", "error": msg}
                        log(f"[{task['id']}] ERROR {msg}")
                        if sid and not ephemeral and dt.is_alive(sid):
                            free.put(sid)
                        break
            finally:
                lane_sems[cur_lane].release()
            with state_lock:
                state[task["id"]] = r
                tmp = state_path(ib) + f".tmp-{os.getpid()}-{int(time.time()*1000)}"
                with open(tmp, "w") as f:
                    json.dump(state, f, indent=2)
                os.replace(tmp, state_path(ib))
            with results_lock:
                results.append(r)
            return r

        with ThreadPoolExecutor(max_workers=effective) as ex:
            futures = {ex.submit(worker, t): t for t in pending}
            for f in as_completed(futures, timeout=global_deadline + 60):
                try:
                    f.result()
                except Exception:
                    pass
                if time.time() - t_batch > global_deadline:
                    for ff in futures:
                        ff.cancel()
                    break
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
        tmp = state_path(ib) + f".tmp-{os.getpid()}-{int(time.time()*1000)}"
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, state_path(ib))
    fcntl.flock(state_lock_fd, fcntl.LOCK_UN)
    state_lock_fd.close()

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
