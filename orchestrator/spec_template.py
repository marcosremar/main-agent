"""Shared spec builder for detector tasks.

Makes the TEST a first-class, mandatory deliverable with an explicit contract — weak
workers were silently skipping the .spec.ts file (→ vitest "No test files found" →
no-progress stall). The prompt now spells out exactly what the test must contain.
"""

EXAMPLES = ("examples/game-engine/pharmacy-help-pt-a2/project.json and "
            "examples/game-engine/coffee-order-en-a1/project.json")


def detector_spec(title: str, rule: str, fixture: str, script: str, spec_file: str) -> str:
    """Build a detector task prompt with a strict, explicit test contract."""
    return f"""Create a deterministic manifest detector AND its test: {title}.

DETECTION RULE: {rule} Be defensive — tolerate missing/wrong-typed fields, never throw.

You MUST create EXACTLY TWO files (both are mandatory — the task FAILS without the test):

1) {script}
   - The detector function (per the rule) plus a CLI: `tsx {script} <project.json>` reads
     and JSON.parses the file, prints a JSON summary, and process.exit(1) when findings
     are non-empty (else 0).
   - FIRST read {EXAMPLES} to learn the REAL JSON shape and exact field names; match reality.

2) {spec_file}   ← THE TEST. Do NOT finish until this file exists and passes.
   - vitest. Import the detector from the script with a NodeNext specifier that ends in .js
     and uses the CORRECT relative depth (test is in test/qa/, script in scripts/qa/, so
     `../../scripts/qa/...js`).
   - It MUST contain ALL THREE of these test cases:
     a) POSITIVE: an inline fixture object containing EXACTLY ONE instance of the defect →
        assert the detector reports exactly that one finding (check the value precisely).
     b) CLEAN: an inline fixture with NO defect → assert the detector reports ZERO findings
        (proves no false positives).
     c) ROBUSTNESS: load BOTH real example project.json files and assert the detector
        returns the documented shape WITHOUT THROWING. Do NOT assert zero on the examples —
        they may legitimately contain real findings.

POSITIVE FIXTURE HINT: {fixture}

HARD RULES: TypeScript only. NodeNext ESM (.js specifiers). Comments explain only WHY. No
new dependencies. Touch ONLY those two files. VERIFY before finishing by running exactly
`npx vitest run {spec_file}` and confirming it PASSES (all 3 cases). If genuinely blocked,
STOP and report — do not edit unrelated files."""


def detector_task(tid: str, title: str, rule: str, fixture: str,
                  worker_model: str = "dumont", max_iters: int = 12,
                  no_progress_limit: int = 5, worker_timeout_s: int = 480) -> dict:
    script = f"scripts/qa/{tid}.ts"
    spec_file = f"test/qa/{tid}.spec.ts"
    return {
        "id": tid, "worker_model": worker_model, "max_iters": max_iters,
        "no_progress_limit": no_progress_limit, "worker_timeout_s": worker_timeout_s,
        "commit": f"feat(qa): detector — {title}",
        "allowed_files": [script, spec_file],
        "verify_cmd": f"npx vitest run {spec_file}",
        "spec": detector_spec(title, rule, fixture, script, spec_file),
    }
