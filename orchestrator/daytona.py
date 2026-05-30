"""Daytona REST client — stdlib only.

Hard-won lessons baked in:
- The synchronous exec proxy returns 504 on long agentic runs, so anything that can
  run long is launched detached (nohup ... &) and polled via exec_wait().
- Default sandbox disk is 3G, so callers must keep the working tree small (sparse clone).
"""
import json
import time
import urllib.request
import urllib.error

API = "https://app.daytona.io/api"


class Daytona:
    def __init__(self, key: str):
        self.key = key

    def _req(self, method: str, path: str, body=None, timeout=60, retries=3):
        data = json.dumps(body).encode() if body is not None else None
        last = None
        for attempt in range(retries):
            req = urllib.request.Request(
                f"{API}{path}", data=data, method=method,
                headers={"Authorization": f"Bearer {self.key}",
                         "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    raw = r.read().decode()
                    return json.loads(raw) if raw.strip() else {}
            except urllib.error.HTTPError as e:
                code = e.code
                msg = e.read().decode()[:300]
                # retry transient server errors; surface client errors (4xx) immediately
                if code in (502, 503, 504, 429) and attempt < retries - 1:
                    last = RuntimeError(f"{method} {path} -> HTTP {code}: {msg}")
                    time.sleep(2 * (attempt + 1))
                    continue
                raise RuntimeError(f"{method} {path} -> HTTP {code}: {msg}")
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                # network/timeout — retry with backoff
                last = RuntimeError(f"{method} {path} -> {type(e).__name__}: {e}")
                if attempt < retries - 1:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise last
        raise last

    # --- lifecycle ---
    def list(self):
        return self._req("GET", "/sandbox").get("items", [])

    def create(self, auto_stop=30):
        return self._req("POST", "/sandbox", {"autoStopInterval": auto_stop})

    def set_autostop(self, sid: str, minutes: int):
        """Idle minutes before Daytona auto-stops the sandbox (stopped = $0, disk kept,
        restart ~1.2s). Endpoint is POST /sandbox/{id}/autostop/{minutes}. Safe to set low
        (e.g. 1) during a run: the 10s exec_wait polling keeps lastActivity fresh, so it only
        fires once the sandbox is genuinely idle (task done)."""
        try:
            self._req("POST", f"/sandbox/{sid}/autostop/{minutes}")
        except Exception:
            pass

    def get(self, sid: str):
        return self._req("GET", f"/sandbox/{sid}")

    def state(self, sid: str) -> str:
        return self.get(sid).get("state")

    def start(self, sid: str):
        if self.state(sid) == "started":
            return
        try:
            self._req("POST", f"/sandbox/{sid}/start")
        except RuntimeError as e:
            if "409" not in str(e):
                raise
        self._wait_state(sid, "started")

    def stop(self, sid: str):
        try:
            self._req("POST", f"/sandbox/{sid}/stop")
        except RuntimeError as e:
            if "409" not in str(e):
                raise

    def delete(self, sid: str):
        self._req("DELETE", f"/sandbox/{sid}?force=true")

    def backup(self, sid: str):
        self._req("POST", f"/sandbox/{sid}/backup")
        while self.get(sid).get("backupState") not in ("Completed", "None"):
            time.sleep(3)

    def is_alive(self, sid: str) -> bool:
        """True if the sandbox still exists (Daytona deletes them under load)."""
        try:
            self.get(sid)
            return True
        except RuntimeError as e:
            if "404" in str(e):
                return False
            return True  # transient — assume alive, caller will find out

    def reap_strays(self, keep: list):
        """Delete sandboxes not in `keep` — stops cost + clutter from prior runs."""
        keepset = set(keep)
        for x in self.list():
            if x["id"] not in keepset:
                try:
                    self.delete(x["id"])
                except Exception:
                    pass

    def _wait_state(self, sid: str, target: str, timeout=120):
        t0 = time.time()
        while self.state(sid) != target:
            if time.time() - t0 > timeout:
                raise TimeoutError(f"{sid} not {target} after {timeout}s")
            time.sleep(1)

    # --- exec ---
    def exec(self, sid: str, command: str, timeout=120, retries=3):
        """Synchronous exec. Use ONLY for short commands (proxy 504s on long ones).

        The toolbox default shell is zsh. If we passed `bash -lc "<cmd>"`, the OUTER zsh
        would expand things like $PATH and ${PIPESTATUS[0]} inside the double quotes
        BEFORE bash ever saw them (and zsh has no PIPESTATUS, so it became empty — the
        source of every false 'verify FAIL'). So we base64-encode the command (base64 is
        zsh-safe) and decode it straight into `bash -l` via stdin — zero outer expansion,
        real bash semantics."""
        import base64 as _b64
        enc = _b64.b64encode(command.encode()).decode()
        wrapped = f"echo {enc} | base64 -d | bash -l"
        r = self._req("POST", f"/toolbox/{sid}/toolbox/process/execute",
                       {"command": wrapped}, timeout=timeout, retries=retries)
        return r.get("exitCode"), r.get("result", "")

    SENTINEL = "__CMD_DONE__"

    def exec_detached(self, sid: str, command: str, logfile: str) -> None:
        """Launch a long command in the background; poll with exec_wait().

        A completion sentinel is appended to the log so exec_wait() detects "done" by the
        log marker, not by pgrep (which races: the process may not have spawned yet)."""
        wrapped = f"{command}; echo {self.SENTINEL}"
        self.exec(sid, f"nohup bash -lc {json.dumps(wrapped)} >{logfile} 2>&1 & echo started", timeout=30)

    def kill(self, sid: str, match: str):
        """Kill any process matching `match` on the sandbox (runaway agentic worker)."""
        self.exec(sid, f"pkill -9 -f {json.dumps(match)} 2>/dev/null; echo killed", timeout=30)

    def exec_wait(self, sid: str, match: str, logfile: str, poll=10, timeout=480, stall=150):
        """Wait until the completion sentinel appears in the logfile; return its tail.

        Two kill conditions, whichever fires first:
          - wall-clock `timeout`: hard cap on total runtime.
          - `stall`: the logfile stops GROWING for `stall` seconds. A hung `claude -p`/`codex`
            produces no output; waiting the full wall-clock wastes ~10min before the retry.
            Detecting the stall (no new bytes) kills it in ~`stall`s, so recovery is fast.
        On either, the runaway process is KILLED and TimeoutError is raised for the caller to
        handle as a failed iteration."""
        t0 = time.time()
        last_size, last_growth = -1, time.time()
        while True:
            if time.time() - t0 > timeout:
                self.kill(sid, match)
                raise TimeoutError(f"'{match}' exceeded {timeout}s — killed")
            if time.time() - last_growth > stall:
                self.kill(sid, match)
                raise TimeoutError(f"'{match}' stalled {stall}s (no log output) — killed")
            # short poll timeout + no retries so a slow proxy can't stretch the loop.
            # `wc -c` reports the log size so we can detect a stall (no new bytes).
            try:
                _, out = self.exec(
                    sid,
                    f"if grep -q {self.SENTINEL} {logfile} 2>/dev/null; then echo DONE; "
                    f"tail -40 {logfile}; else echo RUNNING; wc -c <{logfile} 2>/dev/null; fi",
                    timeout=25, retries=1)
            except RuntimeError:
                out = "RUNNING"   # transient proxy error — keep waiting, deadlines still apply
            if "DONE" in out:
                return out
            # last token from `wc -c` is the current byte count; growth resets the stall clock
            try:
                size = int(out.strip().split()[-1])
                if size != last_size:
                    last_size, last_growth = size, time.time()
            except (ValueError, IndexError):
                pass
            time.sleep(poll)
