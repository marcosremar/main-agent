# CHECKLIST — babylon-cinema

Living task tracker. `[ ]` todo · `[~]` in progress · `[x]` done (date it).
Read at session start. A task is `[x]` only after verify (pillar 4).

> **PER-TASK status is now LIVE in the dashboard** → http://127.0.0.1:8787 (grouped by
> objective, with status filter). This CHECKLIST tracks PHASE-level progress; the dashboard
> tracks individual agent tasks. Failed tasks are NOT removed — re-run the config (resume
> skips merged, retries failures) for infra errors; regenerate via the planner for weak specs.

## Orchestration system (built 2026-05-29/30) — DONE
- [x] Daytona sandbox pipeline: worktree → worker → verify → PR → integration merge (main protected)
- [x] 4 worker lanes: Claude/Opus, Codex/gpt-5.3-codex-spark, Dumont/MiniMax-M2.7, (OpenCode retired)
- [x] Opus ALWAYS validates (independent adversarial check on every task)
- [x] Robustness: resume journal, self-heal pool, reaper, retries, no-progress stop, finally-stop
- [x] Vision verifier (Qwen2.5-VL OSS) for fuzzy/visual criteria
- [x] Planner agent (plan.py): Opus reads repo → decomposes objective into grounded task specs
- [x] Live dashboard (Tailwind, plan groups, status filter, prompt view) as a LaunchAgent
- [x] Daytona Tier 2 (100 vCPU); concurrency validated to 15 per lane
- [x] Claude auth via setup-token (CLAUDE_CODE_OAUTH_TOKEN) — fixes mid-session 401

## Delivered work (QA detectors — ROADMAP epic #26 family + oralTaskGraph integrity)
- [x] ~30 detector PRs merged into integration/agent-pipeline-test (tested + Opus-validated)
- [ ] ~19 tasks not merged (mostly retryable infra ERROR/WORKTREE; ~5 weak specs to regenerate)
      → see dashboard "⏳ Em andamento" filter for the exact remaining list

## NEXT — real roadmap phases (NOT started; need planner-driven, sequenced)
- [ ] Fase 1 (CRITICAL): LLM in NPC + voice→action (ROADMAP 14, 101-110)
- [ ] Fase 2: cidade do cotidiano end-to-end (Casa→Padaria, 13 scenarios)
- [ ] Fase 3: real pedagogy scoring · Fase 4: Classroom · Fase 5: QA visual/linguistic
- [ ] Fase 6 (Mac-only): iPhone/Babylon-Native, voice on-device · Fase 7: master scenario #220
- [ ] Promote integration branch → main (human-reviewed, per phase)

## Setup
- [x] Create brain CLAUDE.md with objective, pillars, rules (2026-05-29)
- [x] Add 4 vibe-coding pillars (2026-05-29)
- [x] Add 3-target test requirement (browser/macOS/iPhone) (2026-05-29)
- [x] Establish this CHECKLIST.md (2026-05-29)

## Health baseline (do first)
- [ ] `npm run typecheck:all` — record pass/fail
- [ ] `npm run test` (vitest) — record pass/fail
- [ ] `npm run build` — record pass/fail
- [ ] `npm run dev` — browser boots
- [ ] macOS native build (`npm run mac:bn-build`)
- [ ] iPhone on-device run (cable-connected) via mobile bundle + Xcode

## Orchestrator pipeline (built + tested 2026-05-29)
- [x] Orchestrator built (orchestrator/: daytona, creds, github, orchestrate)
- [x] **E2E PROVEN**: PR #8 (orphaned-npcs detector) worker→verify→PR→integration→MERGED into integration branch (not main)
- [x] Bug: verify always FAIL (zsh ate `${PIPESTATUS[0]}`) → fixed (base64→bash)
- [x] Bug: `git add -A` committed node_modules symlink (.gitignore outside sparse) → fixed (add only allowed files)
- [x] Bug: worker timeout fired at 23min not 8min (blocked polls) → fixed (wall-clock check first, short poll)
- [x] Bug: golden auto-stopped mid-run → fixed (ensure-start per task); strays cleaned
- [ ] **Golden lifecycle is fragile** — single mutable sandbox vanished (auto-stop→archive→restore made new ids). Need ensure-or-rebuild golden + warm pool.
- [ ] MiniMax via opencode flaky + hangs (8min) — wastes 2 iters before escalating. Consider Opus-first for tricky, or shorter hang timeout.
- [ ] Investigate Opus 15min single iteration (over-working or poll lag?)
- [x] dangling-portals MERGED via PR #9 (Opus, 1 iter, ~1.5min) — both test tasks complete
- [x] Golden reproducible via setup_golden.py (sid 6605c257); rebuild when it vanishes
- [x] Policy: workers default Opus (MiniMax flaky/hangs via opencode)
- [x] Parallelism: pool + ThreadPoolExecutor (ran 10 concurrent), merges serialized
- [x] 15-task batch (ROADMAP #26 detectors) → 15/15 MERGED via PR into integration branch
- [x] Robustness 1-6: task-retry on sandbox death, resume merges open PRs, ephemeral/cattle mode, wave-provision+retry, liveness pre-check, reaper + observability
- [x] Self-heal PROVEN: dead pool → provisioned 2 fresh → finished last 2 tasks (PRs #23/#24)
- [x] ROOT CAUSE of vanishing sandboxes: Daytona Tier 1 pool = 10 vCPU / 20GiB RAM / 30GiB disk TOTAL. 15×(1vCPU/3GiB) exceeds it → excess archived/deleted (disk overflows even when stopped). Fixed: max_concurrent=8 (Tier-1 safe). For 15-40 parallel → upgrade Tier 2 (credit card + $25 = 100 vCPU/300GiB). Use archive (not just stop) to free quota.
- [ ] Warm pool persistence (Daytona vanishing makes pool ephemeral in practice)
- [ ] Investigate occasional 15min Opus iteration (poll lag vs over-working)
- [ ] Local lane (iPhone/simulator/macOS) runner — not built
- [ ] ROTATE exposed Daytona key + Claude token

## Backlog

Full master roadmap (220+ tasks, 12 epics) → [ROADMAP.md](ROADMAP.md).
CHECKLIST.md = near-term focus; ROADMAP.md = the doctorate's full plan.

Current top priorities (from ÉPICO 0 / 1):
- [ ] Conectar LLM/Gateway ao jogo (desbloqueia cenários 4–13)
- [ ] Fix cor desbotada iPhone (backbuffer sRGB nativo no Babylon Native)
- [ ] Resolver conflito ONNX ↔ Babylon Native (task 21, crítico iPhone 12)
- [ ] Revisar/mergear PR#5
