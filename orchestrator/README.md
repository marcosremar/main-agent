# Orchestrator

Automates the dispatch pipeline described in `../CLAUDE.md`. Stdlib Python only.

```
orchestrate.py   main loop: worktree -> worker(loop<=20, verify, escalate) -> push
                 -> PR vs integration branch -> integration agent -> squash-merge -> cleanup
daytona.py       Daytona REST client (lifecycle + exec; long runs detached + polled)
creds.py         pull Claude (Keychain) + MiniMax + gh token, inject into sandbox at runtime
github.py        PR create/merge via GitHub API (targets INTEGRATION BRANCH, never main)
config.json      golden sandbox id, integration branch, task specs
```

## Run

```bash
cd orchestrator
python3 orchestrate.py config.json
```

## Model
- Workers run on Opus 4.8 (Claude Code). MiniMax disabled (flaky/hangs via opencode).

## Verification (two modes, per task)
- **deterministic** (default): runs the task's `verify_cmd` (e.g. vitest) IN the worker's
  sandbox. A command, not an agent — no extra pool. The test IS the verification.
- **vision** (`"verifier": "vision"`): for fuzzy 3D/UI criteria. `evidence_cmd` renders an
  image (`evidence_image`) in the sandbox; it's pulled out and judged by a cheap
  OPEN-SOURCE vision model via OpenRouter (default `qwen/qwen2.5-vl-72b-instruct`; key from
  ai-gateway/.env). Returns SCORE + VERDICT. Override with `vision_model` (e.g.
  `qwen/qwen2.5-vl-32b-instruct` or `meta-llama/llama-3.2-11b-vision-instruct`, even free
  tiers). Validated on real city renders: correct PASS/FAIL discrimination.
- **llm** (`"verifier": "llm"`): after the deterministic gate, an INDEPENDENT adversarial
  Opus agent (not the worker) inspects the actual change + optional `evidence_cmd`
  artifacts (e.g. a screenshot) and tries to REFUTE the `criteria`. Must end with
  `VERDICT: PASS|FAIL`. Pass requires BOTH gate and LLM. Use for fuzzy/visual criteria a
  command can't judge. Task fields: `criteria` (frozen acceptance text), `evidence_cmd`
  (optional artifact-producing command run before the verifier).

## Rules enforced
- `main` is OFF-LIMITS. PRs target the configured integration branch only.
- Surgical scope: a task that changes files outside `allowed_files` is rejected.
- Loop caps at `max_iters` (default 20); exceeding → reported, not merged.
- Long agentic calls run detached + polled (the exec proxy 504s on long sync runs).

## Hang / slowness fallbacks
- **Per-worker timeout** (`worker_timeout_s`, default 480s): a single agentic run that
  exceeds it is **killed** (`pkill -9` on the sandbox) and counted as a failed iteration;
  a hung MiniMax escalates to Opus, then the loop retries.
- **Per-task wall-clock budget** (`task_budget_s`, default 1500s): exceeded → task aborted
  as `TIMEOUT_BUDGET`, worktree cleaned, batch continues.
- **Loop cap** (`max_iters`, default 20): exceeded → `FAILED_MAX_ITERS`, not merged.
- **Global batch deadline** (`global_deadline_s`, default 7200s): remaining tasks
  `SKIPPED_DEADLINE`.
- **Daytona API retry**: transient 502/503/504/429 + network/timeouts retried 3× w/ backoff.
- **golden stop in `finally`**: billing never leaks, even on crash/interrupt.
- **Per-task isolation**: one task's exception → `ERROR` for that task only; batch continues.

## Parallelism + resume
- `max_concurrent` (default 15) parallel workers via a thread pool; clamped to pool size.
- `pool_sids`: list of ready sandboxes. Build with `python3 provision_pool.py config.json [N]`
  (provisions N goldens in parallel, writes `pool_sids`). Falls back to single `golden_sid`.
- Each task grabs a free sandbox from a queue, releases it after. Merges are serialized
  (one integration branch) via a lock.
- **Resume / no-loss**: state journal `state/state-<branch>.json` written after EVERY task.
  On restart, a task is skipped if the journal says MERGED OR its `allowed_files` already
  exist on the integration branch (GitHub check). Validated: rerun skips merged tasks.
- Rebuild a vanished golden/pool anytime with `setup_golden.py` / `provision_pool.py`.

## Gaps / TODO
- Custom baked snapshot via Daytona SDK (faster pool spin-up; REST create was 403).
- Investigate occasional slow (15min) Opus iteration.
- Local lane runner (iPhone/simulator/macOS) — tasks flagged `location:"local"` are
  listed, not executed; must run on the Mac.
- Optional Opus verifier agent for non-deterministic acceptance criteria.
- Cost/observability dashboard.
- **Rotate the Daytona key + Claude token** (exposed during development).
