# Bug Report — scope_ok, already_done, pr_files

---

## Bug 1 — `already_done()` false-positive: empty `allowed_files` is vacuously true

**File**: `orchestrate.py:434`

```python
if files and all(gh.file_exists(f, ib) for f in files):
    return True
```

**Problem**: The guard `if files and ...` only protects against the absent-key case
(`files = []`). But a task whose `allowed_files = [""]` (one empty-string entry)
passes `if files` (truthy list) yet `gh.file_exists("", ib)` is called on an empty
path. GitHub API resolves `path=?ref=ib` → API call succeeds (404 only if path truly
absent), so the single-false-path check returns **True** when it should return False.

**Worse**: if the integration branch has every real file under the empty-string path,
the false-positive fires anyway. The real fix requires also checking `all(f for f in files)`
or using `files.pop() if len(files)==1 and files[0]=="" else files`.

**Real fix**: Normalize `allowed_files` before the `all()` — strip empty strings, reject
tasks with no real entries.

---

## Bug 2 — `scope_ok()`: git diff on brand-new worktree returns non-zero when no commits exist

**File**: `orchestrate.py:216-218`

```python
_, out = dt.exec(sid,
    f"cd {wt} && {{ git diff --name-only HEAD; git ls-files --others --exclude-standard; }} "
    f"| grep -v node_modules | sort -u", timeout=30)
```

**Problem**: In the worktree created from the integration branch (`feat/city-pedestrian-population`),
if the task's very first iteration makes **only untracked files** (e.g. a new file, no edits to
existing tracked files), `git diff --name-only HEAD` exits with status 1 (no diff, because HEAD
has no commits yet, or because only untracked files exist). The brace group `{ A; B; }` in bash
with pipes: the pipeline's exit status is that of the **last command** (`sort -u`), so a non-zero
`git diff` is swallowed. This is silent.

**Impact**: The change list still grows because `git ls-files --others` returns untracked files, so
`scope_ok` may not fire false-negatively — but the code's intent (always capture both tracked
diffs and untracked files) is violated. Worse: if the worktree truly has zero files and zero
diffs, the `grep -v node_modules` and `sort -u` pass an empty input through, and `changed` is
`[]`. Then `scope_ok` returns `(True, [], [])` — **falsely passing** a task that made no changes.

**Real fix**: Use `git diff --name-only HEAD || true` so the brace group always exits 0, or check
`${PIPESTATUS[0]}` for the diff exit code separately.

---

## Bug 3 — `scope_ok()`: path comparison mismatch — repo-root-relative vs worktree-root-relative

**File**: `orchestrate.py:219-229`

```python
changed = [l.strip() for l in out.strip().splitlines() if l.strip()]
# ...
def ok(f):
    f = norm(f)
    if f in allowset:
        return True
    return any(f == a or f.startswith(a + "/") for a in allowset)
```

**Problem**: `git diff --name-only` and `git ls-files` emit paths relative to the **repository
root** (`babylon-cinema/`), e.g. `"src/game-engine/foo.ts"`. The worktree is at `~/babylon-cinema`
so the path is already correctly relative to the repo root. However, `allowed_files` in the task
config is also given as repo-root-relative paths, e.g. `["scripts/qa/detectors/foo.ts"]`. The
comparison `f.startswith(a + "/")` should work — but only if `a` is a directory path ending in a
trailing slash or if `f` truly starts with that prefix.

**Subtle mismatch**: If `a = "scripts/qa/detectors"` (no trailing slash) and `f =
"scripts/qa/detectors/foo.ts"`, then `f.startswith(a + "/")` → `"scripts/qa/detectors/foo.ts".startswith("scripts/qa/detectors/")` → **True** — correct.

If `a = "scripts/qa/detectors"` (no trailing slash) and `f = "scripts/qa/detectors"` exactly (no
trailing slash), then `f.startswith(a + "/")` → False. But the exact-match branch catches `f == a`
→ **True** — correct.

**Actual bug**: `norm = lambda p: p.rstrip("/")` strips trailing slashes, so the above actually
works correctly. The real edge case is `allowed_files` containing two entries where one is a
**prefix of another in a non-obvious way**:

- `a1 = "scripts/qa"` and `a2 = "scripts/qa/detectors"` — `"scripts/qa/detectors/foo.ts".startswith("scripts/qa/")` returns True — OK.
- But if `a = "scripts/qa/detectors"` is defined and a file outside that tree is `"scripts/qa/Detector.ts"` (singular, not `detectors/`), `f.startswith(a + "/")` → False, exact match → False. Scope correctly rejected.

The latent bug: **if `allowed_files` is not pre-normalized in `run_task()` before being passed to
`scope_ok()`, and the worktree's `git diff` returns paths with leading `./` or different canonical
forms (`src/foo.ts` vs `./src/foo.ts`), `norm()` does not strip `./`, so `f.startswith(a + "/")`
fails.** Git normalizes `./` in `git diff --name-only` output, so this rarely fires, but it is
not guaranteed by the code.

**Real fix**: Normalize with `os.path.normpath(f).lstrip("./")` in the `norm()` lambda, or use
pathlib `PurePath(f).relative_to(wt)`.

---

## Bug 4 — `scope_ok()`: `all()` vacuous truth on empty `changed`

**File**: `orchestrate.py:230`

```python
extra = [f for f in changed if not ok(f)]
return (len(extra) == 0), changed, extra
```

**Problem**: The scope check `len(extra) == 0` is correct (not using `all()`). However, the code
comment at `scope_ok` does not document that an **empty** `changed` list (zero files changed) is
treated as in-scope. If a worker silently fails (e.g. the spec was never written to disk, the
agent ran but did nothing, the verify_cmd produced a false pass), the scope check does not catch
it. The **worker**'s verify step may catch the empty-change case, but the scope step alone cannot.

**This is partially mitigated** by `verify_task()` running before `scope_ok()` — if `verify_cmd`
produces a real pass on zero changes, that is the real problem. But if `verify_cmd` also passes
(e.g. `echo "nothing changed" && true`), the task gets merged with no net effect on the codebase.

**Real fix**: Add an explicit guard in `scope_ok`: if `not changed`, fail with message "no files
changed — worker may be empty". Or require `changed` to be non-empty if the task is non-trivial.

---

## Bug 5 — `pr_files()`: silent truncation at 300 files

**File**: `github.py:81-82`

```python
if page > 3:  # safety: max 300 files
    break
```

**Problem**: This hard-cap silently drops files 301+. A task whose worker leaked (added 301+
files beyond allowed scope) would have files 301+ silently ignored by the scope check.

The PR scope check at `orchestrate.py:387`:
```python
pr_files = gh.pr_files(num)
pr_extra = [f for f in pr_files if f not in allowed and not f.startswith("node_modules")]
```

If files > 300 (e.g. a test-writing task that creates hundreds of test fixtures), the page 3 cap
silently drops them from `pr_extra`. The scope check would not catch an out-of-scope file at
index 350+ — potentially merging unrelated changes.

**Real fix**: Raise an exception (not silently truncate) when `page > 3`:
```python
if len(files) >= 300:
    raise RuntimeError(f"PR #{number} has >= 300 files — scope check unsafe, aborting")
```

Or at minimum log a warning before breaking.

---

## Bug 6 — `pr_files()`: pagination page check uses `len(batch) < 100` BEFORE page increment

**File**: `github.py:73-82`

```python
while True:
    batch = self._req("GET", f"/...?per_page=100&page={page}")
    if not batch:
        break
    files.extend(f["filename"] for f in batch)
    if len(batch) < 100:
        break
    page += 1
    if page > 3:  # safety: max 300 files — CRITICAL: this guard fires AFTER page is incremented
        break
```

**Problem**: The page counter blocks at page 4 (after fetching pages 1, 2, 3, then incrementing to
4 and breaking before fetching it). The actual maximum pages fetched is **3**, producing
`max 300 files`. The logic is correct in practice because GitHub API pages at `per_page=100`.

With `len(batch) == 100` on page 3, the code increments to page 4, then the `page > 3` guard
fires and breaks without fetching page 4. This means files 301–400 (if they exist) are silently
dropped (see Bug 5). **The guards interact: when `len(batch) < 100` on page 3, the loop exits
cleanly before page increments. When `len(batch) == 100` on page 3, page becomes 4 and breaks.**

The `page > 3` guard is the definitive cap; `len(batch) < 100` is a convenient early-exit
optimization. Both fire, but in the worst case (batch 100 on page 3), page 4 is never attempted
and 300+ files are silently dropped.

**Real fix**: Merge the guard into the loop condition:
```python
while page <= 3:
    batch = self._req(...)
    if not batch: break
    files.extend(...)
    if len(batch) < 100: break
    page += 1
if len(files) >= 300:
    raise RuntimeError(...)
```

---

## Bug 7 — `already_done()`: file_exists check only on named files, not on task-with-no-files

**File**: `orchestrate.py:433-435`

```python
files = task["allowed_files"]
if files and all(gh.file_exists(f, ib) for f in files):
    return True
```

**Problem**: `all(gh.file_exists(f, ib) for f in files)` returns `True` if `files` contains only
paths that **all already exist on the integration branch**. This is wrong when the task's **real
intent is to modify existing files** — even if the files exist, the modification has not been made
yet, so `already_done` returns True and the task is silently skipped without work.

**Scenario**:
1. Task 1 defines `allowed_files = ["src/foo/bar.ts"]` with intent: modify `bar.ts`.
2. A prior run partially touched other tasks on the same integration branch (not this task).
3. `already_done` checks: `gh.file_exists("src/foo/bar.ts", ib)` → **True** (it exists).
4. `all(...)` → True. Task is skipped — but the modification was never made!

**This is the most impactful bug**: `already_done` conflates "file exists" with "task done". For
tasks that modify existing files, this is always wrong. The only valid case for this check is
when `allowed_files` describes brand-new files (`allowed_files = ["new-file.ts"]` and the file
should NOT exist yet) — the correct answer is `False` (not done).

**Real fix**: Remove the `file_exists`-based check entirely from `already_done`. The journal
(`state["status"] == "MERGED"`) is the only reliable resume guard. Alternatively, restrict the
`file_exists` check only to tasks that explicitly declare `allow_new_only: true` (files that
should not yet exist).

---

## Bug 8 — `scope_ok()` vs `pr_files()` scope check interaction: double-check vs double-fail

**File**: `orchestrate.py:385-389`

```python
# before merge — scope check AGAIN on actual PR files
pr_files = gh.pr_files(num)
pr_extra = [f for f in pr_files if f not in allowed and not f.startswith("node_modules")]
if pr_extra:
    return {"id": tid, "status": "PR_OUT_OF_SCOPE", "pr": num, "extra": pr_extra}
```

**Problem**: Two scope checks are applied to the same change:
1. `scope_ok()` — runs against the sandbox's `git diff` + `git ls-files` output.
2. PR files check — runs against GitHub API's reported changed files.

**If the worker made a commit with only allowed files**, both checks pass correctly.

**If the worker committed with extra files but `git diff` did not show them** (e.g. the worker
did `git add -A`, staged node_modules symlink which `git diff --name-only HEAD` doesn't show
because it's untracked/non-committed, yet `git commit` included it), the PR files check catches
it. But `git diff --name-only HEAD` would not show an untracked file that was staged via `git
add -A` because `git add` makes it tracked before commit. After commit, `git diff --name-only HEAD`
would still show nothing for that file if it was committed as a symlink (symlink content diff).

The real double-check failure mode: **empty `allowed`** — if `allowed_files = []` or empty,
`pr_extra = []` (nothing to flag), and `scope_ok` returns True. The fix requires rejecting empty
`allowed_files` earlier (at task load time or in `run_task`).

**Real fix**: Validate `allowed_files` non-empty at task load time:
```python
if not task.get("allowed_files"):
    raise SystemExit(f"Task {task['id']} has empty allowed_files — cannot scope-check")
```

---

## Bug 9 — `scope_ok()`: worktree at `/home/daytona/git/` vs `~/babylon-cinema/` path mismatch

**File**: `orchestrate.py:216-218`

```python
_, out = dt.exec(sid,
    f"cd {wt} && {{ git diff --name-only HEAD; git ls-files --others --exclude-standard; }} "
    f"| grep -v node_modules | sort -u", timeout=30)
```

The worktree path is constructed as `wt = f"/home/daytona/wt/{tid}"`. The `cd {wt}` is correct.
`git diff --name-only HEAD` runs in the worktree, so the output is relative to the worktree root,
which is the git repo root for that worktree. `allowed_files` paths are relative to the repo root.
**Path comparison is valid** — both sides are repo-root-relative.

No latent bug here given the current construction. The path comparison is clean.

---

## Summary Table

| Bug | Location | Severity | Type | Trigger |
|-----|----------|----------|------|---------|
| 1 | `already_done` | **High** | false-positive | `allowed_files = [""]` or task with missing files |
| 2 | `scope_ok` | Medium | silent pass | new worktree with only untracked files |
| 3 | `scope_ok` | Low | path mismatch (latent) | `git diff` output has `./` prefix or unusual form |
| 4 | `scope_ok` | Medium | vacuous pass | worker makes zero changes (silent fail) |
| 5 | `pr_files` | **High** | silent truncation | PR with > 300 files — out-of-scope files silently go uncaught |
| 6 | `pr_files` | Medium | silent truncation | combined with bug 5 — page 4+ silently dropped |
| 7 | `already_done` | **Critical** | false-negative skip | task to modify existing files is skipped after a prior run's partial work |
| 8 | scope interaction | Medium | empty-allowed bypass | `allowed_files = []` bypasses all scope checks |
| 9 | `scope_ok` | None | not a bug | path comparison is correctly repo-root-relative |
