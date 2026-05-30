# Daytona REST Client — Bug Report

**File:** `orchestrator/daytona.py`
**Date:** 2026-05-30
**Severity scale:** 1=cosmetic, 5=production data loss / infinite billing

---

## Bug 1 — `is_alive()` false positive on transient errors

**Severity:** 4
**Location:** lines 119–127

```python
def is_alive(self, sid: str) -> bool:
    try:
        s = self.get(sid).get("state", "")
        return s != "error"
    except RuntimeError as e:
        if "404" in str(e):
            return False
        return True  # transient — assume alive, caller will find out
```

**Problem:** Any non-404 HTTP error (500, 502, 503, 504, network failure, timeout) causes `is_alive()` to return `True`. The API is up but the sandbox is gone or the request is otherwise meaningless — yet the pool manager treats the slot as live. This can **re-enter a dead sandbox into a warm pool**, causing the next job to hang or fail silently while the orchestrator thinks a worker is running.

**Concrete failure mode:**
- Sandbox crashes or is deleted → Daytona returns 404 → `is_alive` correctly returns `False`. ✓
- Sandbox is fine but the API gateway returns 503 → `is_alive` catches `RuntimeError` → returns `True`. ✗ Sandbox gets put back in the pool with no change in its actual state.

A 503/504 means "I couldn't reach the sandbox controller" — it does not mean the sandbox is alive. The previous behavior was only safe for the 404 case.

**Fix:** Only swallow 404. Re-raise everything else or return `False` (conservative):

```python
except RuntimeError as e:
    if "404" in str(e):
        return False
    raise  # transient server errors are not "alive"
```

---

## Bug 2 — `backup()` exits before snapshot is usable

**Severity:** 3
**Location:** lines 111–117

```python
def backup(self, sid: str, timeout=300):
    self._req("POST", f"/sandbox/{sid}/backup")
    t0 = time.time()
    while self.get(sid).get("backupState") not in ("Completed", "None"):
        if time.time() - t0 > timeout:
            raise TimeoutError(f"backup {sid[:8]} not completed after {timeout}s")
        time.sleep(3)
```

**Problem:** The loop condition `in ("Completed", "None")` checks for the string `"None"` — a literal state value `"None"` that the Daytona API returns as an intermediate state. But the string `"None"` as a Python literal evaluates to `None` (the singleton). The comparison `"None" in ("Completed", None)` is always `False` (string vs NoneType), so the loop exits **never** — not on `"Completed"` and not on `None`. It only stops on timeout.

Wait — actually it **never exits early**: neither `"Completed"` nor `None` matches the literal string `"None"` in the tuple. So the loop waits out the full `timeout`. That's the inverse bug from what the comment suggests is intended.

But there's a subtler issue. If `backupState` can be `null` (JSON null, which `get()` returns as Python `None`), then `None in ("Completed", "None")` is also `False`. The `in` check against a **string** `"None"` never matches JSON `null` returned by the API. The polling is therefore a no-op; the function always times out.

**Fix:** Check for the actual values Daytona returns — likely `None`/`null`, `"Completed"`, and any in-progress state:

```python
while self.get(sid).get("backupState") not in ("Completed", None):
```

Or if `None` is not a real state, simply:

```python
while self.get(sid).get("backupState") != "Completed":
```

---

## Bug 3 — `exec_wait()` stall detection false triggers during slow output

**Severity:** 2–3
**Location:** lines 182–221

```python
def stall():
    ...
    last_size, last_growth = -1, time.time()
    while True:
        ...
        if time.time() - last_growth > stall:
            self.kill(sid, match)
            raise TimeoutError(f"'{match}' stalled {stall}s (no log output) — killed")
```

**Problem:** The stall timer resets based on `size` changes, but the unit of progress detection is `wc -c <logfile` — a byte count. Two failure modes:

1. **Log buffering at 64KB boundaries**: If the process writes to stderr/stdout and the shell buffer fills in chunks, `wc -c` may report the same size for multiple polls even though output is still flowing in 64KB increments. The stall timer then fires and kills a healthy agent.

2. **Growth threshold is 1 byte**: Any single byte of new content resets `last_growth`. But if `grep` races with write (the sentinel check and the tail/`wc -c` are two separate commands), or if the process writes exactly the same number of bytes as before (e.g., a spinner writing the same characters), `size == last_size` triggers a stall kill even though work is ongoing.

**Fix:** Use a minimum growth threshold or a monotonic "output ever appeared" flag:

```python
if size == 0:
    pass  # worker still starting — don't check growth
elif size > last_size:
    last_size, last_growth = size, time.time()
elif time.time() - last_growth > stall:
    self.kill(sid, match)
    raise TimeoutError(...)
```

The existing logic `(size == 0 or size != last_size)` already passes when `size == 0`, which mitigates the starting-up false trigger. But the second failure mode (same-size writes) is unaddressed.

---

## Bug 4 — `exec_detached()` shell escaping on `logfile`

**Severity:** 3
**Location:** lines 170–176

```python
def exec_detached(self, sid: str, command: str, logfile: str) -> None:
    wrapped = f"{command}; echo {self.SENTINEL}"
    self.exec(sid, f"nohup bash -lc {json.dumps(wrapped)} >{logfile} 2>&1 & echo started", timeout=30)
```

**Problem:** `logfile` is interpolated into the shell command **raw**, with no escaping. If `logfile` contains spaces, `>`, `$`, backticks, or other shell metacharacters, the command breaks or allows injection:

- `logfile = "/tmp/my log.txt"` → `> /tmp/my log.txt` → shell writes to `/tmp/my` (truncating it) and interprets `log.txt` as a command to execute.
- `logfile = "/tmp/out$(whoami).txt"` → glob expansion or command substitution.

`command` is correctly protected (base64-in-bash via `exec()`), but `logfile` is a separate path interpolation.

**Fix:** Wrap `logfile` with `json.dumps()` or `'{logfile}'` (single quotes, which prevent all expansions except `'`):

```python
self.exec(sid, f"nohup bash -lc {json.dumps(wrapped)} >{json.dumps(logfile)} 2>&1 & echo started", timeout=30)
```

---

## Bug 5 — `_wait_state()` silently swallowing errors masks root causes

**Severity:** 2
**Location:** lines 139–149

```python
def _wait_state(self, sid: str, target: str, timeout=120):
    t0 = time.time()
    while True:
        if time.time() - t0 > timeout:
            raise TimeoutError(f"{sid} not {target} after {timeout}s")
        try:
            if self.state(sid) == target:
                return
        except RuntimeError:
            pass  # transient API error — keep polling
        time.sleep(1)
```

**Problem:** Any transient API error (503, network timeout, etc.) is discarded, and the loop continues silently. This makes it impossible for a caller to distinguish:

- "Sandbox is starting (waiting)" from
- "API is down and every poll is failing" from
- "Sandbox hit an error state and will never reach `target`"

At the timeout boundary, the caller gets a generic `TimeoutError` with no indication that the actual issue was a cascade of API errors masking the real state.

**Fix:** Track whether any successful poll occurred, or at least accumulate errors for the exception message:

```python
errors = []
try:
    if self.state(sid) == target:
        return
except RuntimeError as e:
    errors.append(str(e)[:50])
time.sleep(1)
...
raise TimeoutError(f"{sid} not {target} after {timeout}s — API errors: {errors}")
```

Or use `exec_wait()` style: assign to a variable and move on.

---

## Bug 6 — `start()` silently ignores idempotency failures other than 409

**Severity:** 1
**Location:** lines 91–99

```python
def start(self, sid: str):
    if self.state(sid) == "started":
        return
    try:
        self._req("POST", f"/sandbox/{sid}/start")
    except RuntimeError as e:
        if "409" not in str(e):
            raise
    self._wait_state(sid, "started")
```

**Problem:** The idempotency guard on line 92 (checking `state() == "started"`) is a TOCTOU race: between the check and the `POST`, the sandbox could change state or the API could return something unexpected. More importantly, if the guard passes and the POST fails with any non-409 error, the except clause re-raises — but then `_wait_state()` is still called with the assumption that the sandbox is in some intermediate state that will resolve to "started".

If the actual error was 403 (forbidden, API key rotated) or 404 (sandbox deleted since the guard checked), the `_wait_state()` call will either poll forever or surface an opaque API error, with no context that the start itself failed.

**Fix:** Refactor to raise on unexpected failures and let the caller handle:

```python
def start(self, sid: str):
    s = self.state(sid)
    if s == "started":
        return
    if s in ("error", "deleting", None):
        raise RuntimeError(f"Cannot start sandbox {sid[:8]} — state: {s}")
    try:
        self._req("POST", f"/sandbox/{sid}/start")
    except RuntimeError as e:
        if "409" in str(e):
            pass  # already started between guard and POST — harmless
        else:
            raise
    self._wait_state(sid, "started")
```

---

## Interaction effects

| Bug | Triggers | Other bug triggered |
|-----|----------|---------------------|
| #1 | API returns 503 during health check | Sandboxes in error state re-enter warm pool |
| #2 | Backup is never usable; timeout always fires | Any code relying on `backup()` snapshooting its state gets broken |
| #3 | Large output (buffer filling) | Long-running agent killed mid-flight; `exec_wait()` raises `TimeoutError` |
| #3 + #4 | `logfile` with spaces crashes the nohup redirect | Stall detection never fires because log write fails; `exec_wait()` times out on wall-clock |
| #5 | All wait loops can hide the real problem | Makes debugging bugs #1–#3 harder |

---

## Priority recommendation

1. **Fix #1 immediately** — can cause dead sandboxes to be recycled as live, leading to task hangs and billing waste.
2. **Fix #4** — trivial but silently breaks `exec_detached()` for any path with spaces.
3. **Fix #2** — makes `backup()` unusable (always times out).
4. **Fix #3** — improves reliability of the agentic worker/verifier loop.
5. **Fix #5, #6** — operational hygiene, lower urgency.
