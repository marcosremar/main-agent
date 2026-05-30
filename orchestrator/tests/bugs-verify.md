# verify_task() Bug Report

Analyzed: `orchestrate.py` lines 96–192 (`verify`, `run_llm_verifier`, `verify_vision`, `verify_task`).

---

## Bug 1 — `verify_vision`: unhandled TimeoutError crashes the task loop (HIGH)

**File**: `orchestrate.py`, lines 154–156

```python
if task.get("evidence_cmd"):
    dt.exec(sid, f"{OPATH}; cd {wt} && {task['evidence_cmd']}",
            timeout=task.get("worker_timeout_s", 480))
```

No `try/except`. If `evidence_cmd` hits the 480s wall clock, `dt.exec()` raises `TimeoutError` unhandled. This propagates up:

1. `verify_task()` → outer `try/accept` in `run_task` catches it (line 311) → returns `(False, "verification timed out: …")`
2. BUT: `verify_task` itself never gets to return — it throws before it can return anything, so the call site in `run_task` gets the raw exception, not a `(ok, out)` tuple.

The exception message is `"verification timed out"` (from line 313) — not obviously a timeout to the dashboard reader. The result status will be `FAILED_MAX_ITERS` or `STUCK_NO_PROGRESS` rather than `TIMEOUT`, making the root cause unclear.

**Fix**: wrap in `try/except TimeoutError` and return `(False, "evidence_cmd timed out: …")`.

---

## Bug 2 — `verify_vision`: uses wrong timeout config field (MEDIUM)

**File**: `orchestrate.py`, line 156

```python
timeout=task.get("worker_timeout_s", 480)
```

`run_llm_verifier` (line 140) reads `validate_timeout_s` — the dedicated, per-stage timeout for verifiers. `verify_vision` reads `worker_timeout_s` — the timeout for the *worker agent*, not for verification sub-stages.

If a task sets `worker_timeout_s: 120` for the agent (expecting a 120s agent timeout) but `validate_timeout_s: 720` for the Opus verifier, the vision stage incorrectly inherits 120s instead of using the verifier's own 720s budget.

Concretely: a task with `worker_timeout_s: 120` and `verifier: "vision"` would have its `evidence_cmd` timeout at 120s even though the task author intended the vision stage to have a long budget.

**Fix**: read `task.get("validate_timeout_s", 720)` instead.

---

## Bug 3 — `verify_vision`: no stall=None (LOW)

**File**: `orchestrate.py`, line 155–156

```python
if task.get("evidence_cmd"):
    dt.exec(sid, f"{OPATH}; cd {wt} && {task['evidence_cmd']}",
            timeout=task.get("worker_timeout_s", 480))
```

`run_llm_verifier` (line 141) explicitly passes `stall=None` to disable the stall detector, commenting: _"the validator runs plain `claude -p` (text, non-streaming) so its log stays silent until done — DISABLE the stall-detector here (stall=timeout) and rely on the wall-clock."_

`verify_vision` does not pass `stall=None`. If Daytona enables stall detection by default (checking for log growth), and `evidence_cmd` takes 200s+ with no output to the exec channel, the stall detector could fire even though the command is legitimately still running. The 480s timeout is the ceiling, but the stall detector firing prematurely would produce an unexplained TimeoutError.

**Fix**: pass `stall=None` explicitly for consistency with `run_llm_verifier`.

---

## Bug 4 — `mode == "vision"` always skips Opus validation — `opus_validate` ignored (MEDIUM)

**File**: `orchestrate.py`, lines 181–191

```python
mode = task.get("verifier")
if mode == "vision":
    vok, vout = verify_vision(dt, sid, wt, task)
    return vok, (out + "\n--- VISION VERIFIER ---\n" + vout)
# POLICY: validation is ALWAYS done by Opus …
if mode == "llm" or task.get("opus_validate", True):
    lok, lout = run_llm_verifier(dt, sid, wt, task, tag)
    return lok, (out + "\n--- OPUS VALIDATION ---\n" + lout)
return ok, out
```

When `mode == "vision"`, the function returns at line 184 — the `opus_validate` check at line 189 is **never reached**. A task with `verifier: "vision"` and `opus_validate: true` gets only the vision verifier; the LLM Opus validator is completely bypassed.

The comment (lines 185–188) states: _"validation is ALWAYS done by Opus … Disable with opus_validate:false only for trivially-objective tasks."_ This policy is not applied to `mode == "vision"` — there is no code path to run both vision AND Opus validation sequentially.

**Impact**: If someone wants a task to pass through both a deterministic visual check AND an adversarial Opus judge (e.g., render a scene, check pixels, AND have Opus verify the implementation is non-trivial), there is no way to express that. The `opus_validate: true` setting silently has no effect in vision mode.

**Two possible interpretations**:
- **Bug**: vision mode should also run Opus validation (add the `run_llm_verifier` call after `verify_vision`, gated by `opus_validate: true`).
- **Intentional design**: vision IS the validation stage for visual tasks; `opus_validate` should default to `false` for vision mode (not `true`). The comment is misleading because it doesn't account for vision mode's special case.

Given the explicit policy language in the comment, this is filed as a bug — the behavior contradicts the stated intent.

**Fix**: either (a) move the `opus_validate` check before the mode switch so it applies to all modes, or (b) explicitly set `opus_validate: false` in vision-mode task specs and update the comment to acknowledge the exclusion.

---

## Bug 5 — `verify_cmd` deterministic gate is skipped for `mode == "llm"` (by design, but noteworthy)

**File**: `orchestrate.py`, lines 177–180

```python
if task.get("verify_cmd"):
    ok, out = verify(dt, sid, wt, task["verify_cmd"])
    if not ok:
        return ok, out
```

When `verifier: "llm"`, if `verify_cmd` is defined and **fails**, the function returns immediately with the command failure — the LLM verifier never runs.

This is correct behavior: the cheap gate runs first, and if it fails there is no point running Opus. But the inverse is not true: if `verify_cmd` **passes**, the LLM verifier still runs. This is intentional per the policy comment ("The deterministic gate above is a cheap pre-filter; the final judgment that the change is genuinely correct is Opus's").

However, note that for `mode == "vision"`, the deterministic gate is **never run** — `verify_cmd` is ignored when `mode == "vision"` (the function returns at line 184 before checking `verify_cmd`). This may be correct (vision is a different kind of check) but is not documented.

---

## Bug 6 — `run_llm_verifier`: timeout + stall=vt — correct behavior, but subtle

**File**: `orchestrate.py`, line 141

```python
out = dt.exec_wait(sid, "claude -p", logfile, timeout=vt, stall=vt)
```

`stall=vt` means: "fire the stall detector if no output for `vt` seconds." Since the log is non-streaming (plain `claude -p` text output), the entire result appears at once. `stall=vt` is equivalent to "stall == timeout" — the detector fires at the same moment the wall-clock would fire anyway.

This is **correct**, but the comment on line 139 says `"DISABLE the stall-detector here (stall=timeout)"` — that phrasing is slightly misleading. `stall=timeout` **enables** the stall detector (at the same value as the timeout), it does not disable it. `stall=None` would be the way to truly disable it. The behavior is correct, but the comment overstates what is happening.

---

## Summary Table

| # | Severity | Function | Issue |
|---|----------|----------|-------|
| 1 | HIGH | `verify_vision` | Unhandled TimeoutError crashes task loop |
| 2 | MEDIUM | `verify_vision` | Uses `worker_timeout_s` instead of `validate_timeout_s` |
| 3 | LOW | `verify_vision` | Missing `stall=None` (inconsistent with `run_llm_verifier`) |
| 4 | MEDIUM | `verify_task` | `mode=="vision"` skips Opus validation even when `opus_validate: true` |
| 5 | INFO | `verify_task` | `verify_cmd` skipped in vision mode — by design, not documented |
| 6 | INFO | `run_llm_verifier` | Comment is slightly misleading about what `stall=vt` does |