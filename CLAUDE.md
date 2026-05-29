# babylon-cinema-brain — Orchestration Brain

This directory is **not** a code project. It is the **control center** that drives the
real project living beside it:

```
/Users/marcos/projects/
├── babylon-cinema/         ← TARGET. The actual repo. All edits happen here.
└── babylon-cinema-brain/   ← YOU ARE HERE. Orchestration only. No app code.
```

## Mission

1. **Read** the `babylon-cinema` project (sibling dir above).
2. **Make it work** — build, typecheck, test green; the two runtime modes runnable.
3. **Drive edits via CLI agents** — launch sub-agents from here to do the actual
   editing inside `babylon-cinema/`. The brain plans and dispatches; agents execute.

## Objetivo do projeto (o PORQUÊ)

`babylon-cinema` existe para dois fins que motores genéricos não resolvem:

- **Cinema offline**: Sora/Veo/Wan geram pixels, mas não dão controle shot-by-shot de
  câmera, personagens consistentes entre cenas, assets 3D reutilizáveis nem saída
  interativa. Babylon.js 9 + `mcp-for-babylon` já ligam agentes LLM a uma cena viva;
  faltava ninguém ligar o pipeline **roteiro → cena → shots → render**. Esse é o repo.
- **Jogo interativo (educação de idiomas)**: professores de idioma não sabem programar.
  O projeto dá uma camada de autoria manifest-first (wizard do professor → projeto Zod
  → cena Babylon jogável → bundle iPhone) para que professor ou agente IA criem
  experiências de aprendizado em minutos.

**Caso-guia**: professor descreve em português ("aluno entra na padaria, pede um
croissant em francês com cortesia") → Babylon Cinema gera cidade, interior de padaria,
NPC e um grafo de tarefa oral → aluno joga no iPhone 12, fala no microfone, recebe
feedback semântico + de cortesia.

**Princípios**: voice-first, mobile-first (iPhone 12), professor-first, data-first
(tudo via Zod), library-not-framework (consumido por `@parle/avatar-engine`).

## The target in one breath

`@parle/babylon-cinema` (v2.0.0+) — one unified Babylon.js 9 engine, **two runtime modes**:

| Mode | Pipeline |
|------|----------|
| **Offline cinema** | pitch → Claude screenplay → MCP Babylon scene → headless render → ffmpeg → mp4 |
| **Interactive game** | `TeacherActivityBrief` → `GameProjectManifest` (Zod) → Babylon `SceneBuilder` → playable slice → iPhone WKWebView |

Voice-first, mobile-first (iPhone 12 budget), professor-first, data-first (everything
through Zod). Babylon.js 9 is a **peer dependency**.

## Canonical docs — read before any work (in the TARGET repo)

- `babylon-cinema/CLAUDE.md` — **single source of truth.** Read first. ~43 KB.
- `babylon-cinema/AGENTS.md` — hard rules digest for any agent.
- `babylon-cinema/README.md` — repo layout + stack.
- `babylon-cinema/docs-site/docs/spec-wiki.md` — spec.
- `babylon-cinema/docs-site/docs/planos-locais/game-engine-roadmap.md` — gap analysis / roadmap.

## Hard rules (inherited — never violate)

1. **Dep direction**: `avatar-engine → babylon-cinema`. Never reverse.
2. **`src/game-engine/core/` is renderer-free** — no `@babylonjs/*` import there. Biggest red flag.
3. **TypeScript only** — no new `.js` source. Run scripts via `tsx`.
4. **NodeNext ESM**: imports end in `.js` even though source is `.ts`. Don't change/omit.
5. **External JSON through Zod** — `parseGameProject`, `parseScreenplay`. No casts.
6. **Comments = WHY only.** No docstrings, no "what" comments, no PR/task refs.
7. **`src/game-engine/` and `src/game/` coexist on purpose.** Don't collapse. Two
   `cinematic-*` vs live-runtime trees are intentional.
8. **Cinema is a runtime mode, not a separate package.** Don't re-split.
9. **Surgical changes**: list files to touch before editing; don't stray outside.

## Make-it-work checklist (run in TARGET)

```bash
cd /Users/marcos/projects/babylon-cinema
npm run typecheck:all     # tsc across main + browser + admin
npm run test              # vitest (pretest seeds citygen bundles)
npm run build             # ktx2 + tsc x2 + browser bundle
npm run dev               # dev server (predev builds viewer + rio city)
```

Diagnose failures → dispatch a focused agent to fix → re-run. Repeat until green.

### Tests must pass on all 3 targets

"Make it work" = green on **every** runtime target, not just one. A fix isn't done
until verified on all three:

1. **Browser** — dev server / headless (`npm run dev`, vitest, playwright e2e).
2. **macOS** — native build (`npm run mac:bn-build`).
3. **iPhone** — physical device, **already cable-connected to this MacBook**. Build +
   deploy the WKWebView bundle to it (`npm run build:mobile-bundle`, `npm run ios:sync-bundle`,
   then build/run via Xcode on the connected device). Verify it actually runs on-device.

Goal-driven loop (pillar 4): a task that touches engine/runtime code self-verifies on
browser → macOS → iPhone before being declared done.

## How the brain dispatches agents

The brain stays in this dir. To make edits, launch agents pointed at the target:

- **Explore** agent — read-only fan-out to locate code / answer "where is X".
- **general-purpose** / **claude** agent — multi-step edits inside `babylon-cinema/`.
- **Plan** agent — design an implementation strategy before touching code.

### Remote sandbox — Daytona (VALIDATED 2026-05-29)

To keep the local machine light, agents run in **Daytona** sandboxes (gVisor-class
isolation, boot ~instant). Proven end-to-end: both CLIs run remotely with real logins.

- **API**: `https://app.daytona.io/api` — `Authorization: Bearer <DAYTONA_KEY>`.
  - `POST /sandbox` (no resources field when using default snapshot) → starts instantly.
  - `POST /toolbox/{id}/toolbox/process/execute` `{"command":"..."}` → runs, returns `{exitCode,result}`.
  - `POST /sandbox/{id}/backup` → snapshot state; poll `backupState` until `Completed`.
  - `POST /sandbox/{id}/stop` → halts billing (disk kept; restart resumes with CLIs).
  - `DELETE /sandbox/{id}?force=true` → destroy. (Default `autoDeleteInterval:-1` = never; always stop/delete explicitly or cost accrues.)
- **Base image** `daytonaio/sandbox:0.8.0` ships Node 25. Install CLIs:
  `npm i -g @anthropic-ai/claude-code` + `curl -fsSL https://opencode.ai/install | bash`.
- **Credentials — inject at RUNTIME, never bake into a snapshot** (snapshots are stored):
  - Claude Code: copy local subscription login from macOS Keychain item
    `Claude Code-credentials` → write to sandbox `~/.claude/.credentials.json` (user is
    `daytona`, home `/home/daytona`). Uses the subscription, no separate API key.
  - OpenCode/MiniMax: key lives in `ai-gateway/.env` (`MINIMAX_API_KEY`) and local
    `~/.local/share/opencode/auth.json` (`minimax` → `{type:"api",key}`). Copy that
    auth.json into the sandbox `~/.local/share/opencode/auth.json`.
- **Boot speed (MEASURED 2026-05-29)**: `stop`→`start` of an existing sandbox =
  **~1.2s to `started`**, well under 5s. Credentials written to disk **persist across
  stop/start** — both CLIs answered logged-in after restart with NO re-injection.
- **Two reuse models**:
  - **Warm pool (fastest, "<5s já logado")**: keep N sandboxes *stopped* with CLIs AND
    creds on disk. `start` = ~1.2s and immediately logged in. Billing only while
    `started`; stopped = free. Best for the <5s-ready requirement. Creds sit on the
    pool's private disk (acceptable — your account).
  - **Snapshot fan-out (cleanest, creds-free)**: bake ONE snapshot = base + CLIs, no
    creds. Each job: create → inject creds (sub-second) → run → stop/delete. Use when
    you want every machine pristine.
  - Per job either way: `git worktree` → run CLI → push branch → stop (pool) / delete (fan-out).
- **Golden machine (built 2026-05-29)**: sandbox `a72ec808` is the prepared template —
  CLIs + Claude login + MiniMax key + repo (blobless sparse clone, code only ~14M) +
  node_modules (`npm ci --ignore-scripts`) + creds, all on disk. STOPPED ($0). `start` =
  ~1.2s and fully ready. **Don't redo setup per job — clone the golden once, reuse.**
- **One-time setup cost is paid ONCE**, never per task. The slow parts (clone, npm ci)
  live in the golden/snapshot; jobs only `start` + `git worktree` + run.
- **Setup gotchas (solved)**: repo `.git` is 4.4G → use blobless sparse clone
  (`--filter=blob:none --no-checkout` + `git sparse-checkout set <code dirs>`) → ~14M, no
  assets. Default sandbox disk is only 3G → sparse keeps it under budget. `npm ci
  --ignore-scripts` skips native builds (sharp/onnx) the QA/test tasks don't need.
- **⚠️ exec proxy times out (504) on long synchronous runs** — agentic worker/verifier
  calls MUST be detached (`nohup … >log 2>&1 &`) and polled, never run synchronously
  through `/toolbox/process/execute`.
- **Observed**: MiniMax via `opencode run` sometimes READS but fails to APPLY a trivial
  edit (saw it skip a one-line import fix twice). Escalate stuck edits to an Opus worker.
- **Verified calls**: `claude -p "…" --model claude-opus-4-8` and
  `opencode run -m minimax/MiniMax-M2.7 "…"` both returned correct output in-sandbox.
- ⚠️ The Daytona key + Claude tokens were exposed in chat during the test — **rotate**.

### Execution location — cloud vs local (HARD split)

Not every task can run on the remote sandbox. Route by what the task needs to touch:

- **CLOUD (Daytona sandbox)** — default. Code edits, typecheck, unit tests, browser/
  headless Babylon, Playwright web, lint, refactors, anything that needs only Linux +
  Node + the repo. Fan out massively here.
- **LOCAL (this MacBook) — MANDATORY, never the VPS** — anything that touches:
  - **physical iPhone** (cable-connected to this Mac)
  - **iOS Simulator** (Xcode, macOS-only)
  - **macOS native build** (`mac:bn-build`, Xcode toolchain)
  - **Keychain / device profiling / on-device voice (mic, VAD, ASR latency, battery)**
  These CANNOT be offloaded — the device and simulator live here. The cloud has no
  iPhone, no simulator, no Xcode.

So the worker/verifier loop splits: a cloud agent can write + unit-test the code, but the
iPhone/simulator verification step (and macOS-native build) runs locally on this Mac.
A task whose acceptance criteria require on-device/simulator proof is NOT "done" until
that local step passes — push the branch from cloud, then build/run/verify locally.

### Task granularity — DECOMPOSE before dispatch (small + specific)

Big tasks make agents slow and loops long. Every dispatched task must be **small and
hyper-specific** so an agent finishes in a few minutes and verifies cleanly:

- **One task = ~1–3 files, one crisp verifiable criterion, minutes of work.** If it
  needs more, it's not one task — split it.
- **Roadmap epics are NOT tasks.** A ROADMAP.md item (e.g. "LLM in gameplay") is an epic;
  the brain breaks it into many small specs before any dispatch. Never hand an epic to an
  agent.
- **Each spec is self-contained**: exact files, exact steps, exact verify command, frozen
  acceptance criteria — zero ambiguity, zero room to wander.
- **Prefer many small parallel tasks over a few big serial ones** — keeps both model
  lanes busy and keeps per-task wall-clock under the fallback budgets.
- A task that keeps failing the loop is usually too big or under-specified → split it
  further or tighten the spec, don't just raise max_iters.

This decomposition is the brain's job (in chat, with the user when scope is unclear)
before the fan-out. It is the single biggest lever on speed and on agent "intelligence":
a small, exact task is one a cheaper model can nail on the first try.

### Dispatch protocol — clarify here, then fan out fully-specified tasks

**Golden rule: all ambiguity is resolved in THIS chat, before launch. Dispatched
agents never ask questions — their spec is already complete.**

1. **Clarify in chat first.** If anything about a task is undecided, the brain asks the
   user *here* (use AskUserQuestion). No HTML page, no out-of-band UI — just the chat.
   Decide every detail: scope, files, acceptance criteria, edge cases.
2. **Write a complete task spec per agent.** Each dispatched task must contain, in the
   prompt itself, everything needed to finish with zero questions:
   - exact goal + why
   - exact files allowed to touch (surgical rule)
   - step-by-step what to do
   - **verifiable acceptance criteria** (pillar 4) + how to verify (commands, targets)
   - the inherited rules ("cd target; read CLAUDE.md+AGENTS.md; obey hard rules; no
     features beyond asked; report files touched + results")
   An agent that hits a real blocker STOPS and reports back — it does not guess.
3. **Fan out in parallel.** Independent tasks launch concurrently:
   - MiniMax M2.7 (OpenCode): ~30–40 agents at once for volume/cheap work.
   - Claude Code (Opus 4.8): ~30–40 agents at once for hard work.
   Split the work into independent, non-overlapping task specs so they don't collide.
4. **One worktree per agent.** Each agent works in its own isolated git worktree on its
   own branch — never the same checkout. This is what makes massive parallelism safe.
5. **Auto-merge.** After an agent finishes + its acceptance criteria pass, merge its
   branch back automatically; on conflict, flag it (task 112) instead of force-merging.
   Remove the worktree when done.

```bash
# per-task isolation pattern (brain orchestrates this for each fanned-out agent):
cd /Users/marcos/projects/babylon-cinema
git worktree add -b agent/<task-id> ../.bc-wt/<task-id> feat/city-pedestrian-population

# Claude Code agent in that worktree:
claude -p "FULL TASK SPEC … (no questions; stop+report if blocked)" \
  --model claude-opus-4-8 --add-dir ../.bc-wt/<task-id>
# OR MiniMax agent:
opencode run -m minimax/MiniMax-M2.7 --dir ../.bc-wt/<task-id> "FULL TASK SPEC …"

# after success + verify:
git -C /Users/marcos/projects/babylon-cinema merge --no-ff agent/<task-id>
git worktree remove ../.bc-wt/<task-id>
```

Brain runs this loop for the whole batch automatically: spec → worktree → dispatch →
verify → merge (or flag conflict) → cleanup. The user only gets pinged for the upfront
clarifications and for merge conflicts.

### CLI dispatch — which model for which task

Two CLIs, two model tiers. Pick by task type. Always `cd` the target first and pass
the rules in the prompt.

**Claude Code (Opus 4.8)** — `claude` CLI. Use for: hard reasoning, architecture,
multi-file refactors, debugging tricky failures, anything touching the engine core,
Zod schemas, render pipeline, pillar-4 verify loops. The high-stakes/high-care work.

```bash
# headless one-shot in the target repo
claude -p "PROMPT" --model claude-opus-4-8 --add-dir /Users/marcos/projects/babylon-cinema

# thinking levels (escalate by keyword in the PROMPT, cheap→deep):
#   "think"        — light planning (simple edits)
#   "think hard"   — moderate (multi-file change)
#   "think harder" — complex (cross-system, tricky bug)
#   "ultrathink"   — max budget (architecture, risky refactor, root-cause hunt)
# e.g.:
claude -p "ultrathink. Root-cause the iPhone washed-out colors (Babylon Native sRGB). \
List files to touch before editing. cd babylon-cinema; read CLAUDE.md+AGENTS.md first." \
  --model claude-opus-4-8 --add-dir /Users/marcos/projects/babylon-cinema
```

**OpenCode (MiniMax M2.7)** — `opencode` CLI. Somewhat less intelligent than Opus, but
capable — give it real work, not just trivia. Use for the broad middle: clearly-scoped
features, well-specified edits, moderate refactors, test writing, codemods, scaffolding
— anything where the spec is detailed enough that there's little room to go wrong. Keep
it busy. Reserve the hardest design/architecture/root-cause work for Opus.

```bash
opencode run -m minimax/MiniMax-M2.7 --dir /Users/marcos/projects/babylon-cinema "PROMPT"
# reasoning effort: --variant minimal|high|max
# high-speed pool:  -m minimax/MiniMax-M2.7-highspeed
# continue/iterate: opencode run -c -m minimax/MiniMax-M2.7 "next step"
```

**Routing rule of thumb**:
| Task | Model |
|------|-------|
| Architecture / cross-system design / root-cause hunts | Opus 4.8 (ultrathink) |
| Engine core / schemas / render / IK / pedagogy eval | Opus 4.8 (think harder) |
| High-risk refactor where a wrong call breaks much | Opus 4.8 (think hard) |
| Clearly-scoped feature / well-specified edit | MiniMax M2.7 |
| Moderate refactor, test writing, codemod, scaffolding | MiniMax M2.7 |

**Current policy (2026-05-29): ALL workers = Opus 4.8 (Claude Code).** MiniMax via
`opencode run` proved unreliable in practice — flaky (passes one run, fails the next) and
hangs (8min+ stuck) — so it is disabled as a worker for now. Re-enable MiniMax only for
proven-trivial mechanical tasks once its reliability is sorted. Until then the
orchestrator runs every worker on Opus.

**Principle (when both are in play): keep BOTH maxed out — they complement for max throughput.**
The goal is to ship tasks as fast as possible, so load up ~30–40 agents on each side in
parallel. The split is by difficulty, not "simple vs everything": Opus takes the hardest
design/architecture/root-cause work; MiniMax takes the well-specified bulk (which can be
moderately complex, given a detailed spec). When a task is borderline, prefer keeping
MiniMax busy and let Opus focus on what truly needs it.
(Codex/`codex` CLI available as a third lane if needed.)

### Pipeline: branch → PR → verify → integration agent → INTEGRATION BRANCH (not main)

Work is integrated into a designated **integration branch**, NOT `main`. `main` stays
separate and untouched — the brain never merges agent work into it. The integration
branch is named per batch (default: the current feature branch, e.g.
`feat/city-pedestrian-population`, or a dedicated `integration/<batch>` branch). Promotion
of the integration branch → `main` is a separate, human-decided step, never automatic.

Three agent roles per task:

1. **Worker** — clones/uses the repo on its sandbox, does the task on its OWN branch
   (`agent/<task-id>`) cut from the integration branch, opens a **PR targeting the
   integration branch** (`gh pr create --base <integration-branch>`). Does not merge.
2. **Verifier** — independent agent (Opus), adversarial, checks the PR's diff against the
   frozen acceptance criteria (see worker/verifier loop below). Loops with the worker
   until pass or 20 iterations.
3. **Integration agent** — dedicated agent that owns the **integration branch**. After a
   PR passes verification it DECIDES whether to merge:
   - re-runs the acceptance checks on the PR head,
   - checks the PR is in scope (only the intended files; no unrelated changes — task 38),
   - merges the integration branch into the PR branch, confirms still-green (post-merge
     compat, task 115); on conflict → flag, do not force (task 112),
   - merges the PR into the integration branch (`gh pr merge --squash`) ONLY if all hold;
     otherwise sends it back or escalates to the user.
   It is the single gatekeeper to the integration branch (NOT to `main`). The brain runs
   it once per PR, serializing merges to avoid races on the integration branch.
   **`main` is off-limits to all agents.**

### Worker / Verifier loop — two agent roles per task

Every task runs through TWO distinct agent roles. The brain (in THIS chat) orchestrates
the loop — orchestration is NOT delegated, because it owns the criteria and the
user-escalation decision.

1. **Define acceptance criteria UP FRONT** (with the user, in chat, before launch).
   Concrete + verifiable: what must be true, which commands prove it, which targets
   (browser/macOS/iPhone) must be green. These are frozen before the loop starts.
2. **Worker agent** — does the task. Routed by difficulty (Opus 4.8 hard / MiniMax M2.7
   bulk). Works in its own worktree/sandbox. Produces the change.
> Implemented in the orchestrator as two modes (per task): **deterministic** (default) runs
> the task's `verify_cmd` — a command, not an agent — so a pool of N workers spawns N
> agents, not 2N; the test IS the verification. **llm** (`"verifier":"llm"`) adds an
> independent adversarial Opus verifier (+ optional `evidence_cmd`) for fuzzy/visual
> criteria a command can't judge; pass requires both the command gate and the LLM verdict.

3. **Verifier agent** — a SEPARATE, independent agent that checks the worker's output
   against the frozen criteria. Adversarial mindset: tries to find why it FAILS, runs
   the verification commands, inspects the actual result (not the worker's claims). Use
   a capable model (Opus 4.8) — judging quality well matters more than cost here. The
   verifier must NOT be the same agent that did the work.
4. **Loop**: verifier PASS → merge the branch. Verifier FAIL → it writes specific
   feedback (what failed, where, expected vs actual) → brain sends that back to a worker
   agent to fix → re-verify. Repeat.
5. **Max 20 iterations per task.** If still failing after 20, STOP, do not merge, and
   escalate to the user with the failure history. Never loop forever, never merge
   unverified work.

```
criteria (frozen, in chat)
   ↓
worker → change → verifier
   ↓ FAIL (feedback)        ↓ PASS
worker fixes ──loop≤20──→   merge
   ↓ still failing @20
escalate to user
```

The brain runs this loop for each task across the whole parallel batch, tracking
iteration count per task. Workers/verifiers are stateless per call; the brain holds the
loop state and the criteria.

Each dispatched agent MUST:
- `cd /Users/marcos/projects/babylon-cinema` first (absolute paths).
- Read `CLAUDE.md` + `AGENTS.md` there before editing.
- Obey the hard rules above.
- Report back: files touched, commands run, results (faithfully — failures stated).

Prefer parallel agents for independent work; one agent per coherent fix.

## Operating principles (vibe-coding — apply to brain AND every dispatched agent)

Four pillars. They override default "just do work" behavior. Each agent launched
from here inherits them; restate them in the agent prompt.

### 1. Think before acting
- Plan before executing. Do not jump straight to edits.
- If the instruction is ambiguous, **ask** — do not guess or pick one interpretation.
- If a simpler approach exists than what was requested, **say so** and let the user
  decide before implementing.
- Only do what was clearly asked. Nothing dubious, nothing inferred-without-confirmation.

### 2. Simplicity first
- Fewest lines that solve it. If 200 lines could be 50, write 50.
- **No features beyond what was asked.** No extra entregáveis, no invented scope.
- Stating what NOT to do matters as much as what to do — when a known bad habit is
  likely, name it and forbid it.

### 3. Surgical changes
- A request to fix one part touches only that part. Magnifying-glass scope.
- Never modify working code outside the requested change. Protect everything that
  isn't the target.
- List the files to be touched before editing; stray outside only if a dependency
  forces it, and explain why. (This is also hard-rule #9 above.)

### 4. Goal-driven execution
- Define **verifiable success criteria** for every task — the concrete things that
  must be true for it to count as done.
- Work toward the goal, not toward "doing work." Don't burn tokens/time on motion.
- **Self-verify in a loop**: after producing a result, check it against the criteria.
  If it fails, fix and re-check. Repeat (10, 15, 20× if needed) without pausing to ask,
  until criteria pass — then stop. For visual/build/test output, the loop = run it,
  observe, judge, iterate. The AI is executor AND reviewer.

## CHECKLIST.md — living task tracker (mandatory)

Keep a file `CHECKLIST.md` in this brain dir as the running task list. It is the
source of truth for what's pending / in progress / done.

Two files:
- `CHECKLIST.md` — near-term focus (current priorities, health baseline).
- `ROADMAP.md` — the doctorate's full master plan: 220+ tasks across 12 epics
  (render base, LLM gameplay, cidade do cotidiano, CI/arquitetura, infra GPU/finetuning,
  QA visual, Classroom, voz+NPCs, QA linguístico, pesquisa experimental, avaliação
  pedagógica, transferência/retenção, cidade completa, movimento procedural).
  Final goal = task 220 (missão integrada) + task 239 (movimento natural validado).

Rules:
- **Read both at the start of every session.** Resume from there.
- **Add** new tasks as they surface (from user, from failures, from discoveries).
- **Mark** state with `[ ]` todo, `[~]` in progress, `[x]` done. Date completed items.
- **Update it as you work** — not at the end. Reflect reality continuously.
- Dispatched agents report back; the brain folds their results into `CHECKLIST.md`.
- A task isn't `[x]` until verified per pillar 4 (and, for engine/runtime, green on
  all 3 targets: browser, macOS, iPhone).

## State (update as work progresses)

- Target branch: `feat/city-pedestrian-population` (verify with `git -C ../babylon-cinema branch --show-current`).
- Open objective: read project → establish current build/test health → drive fixes via agents.
