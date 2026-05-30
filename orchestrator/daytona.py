"""Daytona REST client — stdlib only.

Hard-won lessons baked in:
- The synchronous exec proxy returns 504 on long agentic runs, so anything that can
  run long is launched detached (nohup ... &) and polled via exec_wait().
- Default sandbox disk is 3G, so callers must keep the working tree small (sparse clone).
"""
import json
import random
import time
import urllib.request
import urllib.error

API = "https://app.daytona.io/api"


class Daytona:
    def __init__(self, key: str):
        self.key = key

    def _req(self, method: str, path: str, body=None, timeout=60, retries=3):
        try:
            data = json.dumps(body).encode() if body is not None else None
        except TypeError as e:
            raise TypeError(f"_req: body is not JSON-serializable: {e}") from e
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
                    time.sleep(min(30, 2 ** (attempt + 1)) + random.uniform(0, 1))
                    continue
                raise RuntimeError(f"{method} {path} -> HTTP {code}: {msg}")
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                # network/timeout — retry with backoff
                last = RuntimeError(f"{method} {path} -> {type(e).__name__}: {e}")
                if attempt < retries - 1:
                    time.sleep(min(30, 2 ** (attempt + 1)) + random.uniform(0, 1))
                    continue
                raise last
        raise last

    # --- lifecycle ---
    def list(self, limit=50):
        """Iterate all pages; Daytona returns at most `limit` items per request."""
        items, cursor = [], None
        while True:
            path = f"/sandbox?limit={limit}" + (f"&after={cursor}" if cursor else "")
            page = self._req("GET", path).get("items", [])
            items.extend(page)
            # Daytona uses `nextCursor` or `cursor` in the response envelope
            cursor = (
                page[-1]["id"] if len(page) == limit else None
            )
            if not cursor:
                break
        return items

    def create(self, auto_stop=30, snapshot=None, labels=None):
        # resources (cpu/mem/DISK) are fixed by the snapshot, not settable per-sandbox. The
        # default snapshot is only 3G disk — too tight for .git(1.2G)+node_modules(808M)+worktree.
        # `daytona-medium` is an 8G/2cpu preset; pass snapshot="daytona-medium" for headroom.
        body = {"autoStopInterval": auto_stop}
        if snapshot:
            body["snapshot"] = snapshot
        # labels may not be supported by all API versions — set only if API accepts it
        tag = {"project": "babylon-cinema", "agent": "main", **(labels or {})}
        try:
            body["labels"] = tag
        except Exception:
            pass
        return self._req("POST", "/sandbox", body)

    def set_autodelete(self, sid: str, minutes: int):
        """Minutes after a sandbox STOPS before Daytona destroys it (-1 = never). Makes job
        COPIES ephemeral (cattle): once a run finishes and the sandbox idles->stops, it's gone,
        so disk/quota isn't held. The golden SNAPSHOT template is separate and unaffected."""
        try:
            self._req("POST", f"/sandbox/{sid}/autodelete/{minutes}")
        except Exception as e:
            print(f"WARN set_autodelete {sid[:8]}: {e}", flush=True)

    def set_autostop(self, sid: str, minutes: int):
        """Idle minutes before Daytona auto-stops the sandbox (stopped = $0, disk kept,
        restart ~1.2s). Endpoint is POST /sandbox/{id}/autostop/{minutes}. Safe to set low
        (e.g. 1) during a run: the 10s exec_wait polling keeps lastActivity fresh, so it only
        fires once the sandbox is genuinely idle (task done)."""
        try:
            self._req("POST", f"/sandbox/{sid}/autostop/{minutes}")
        except Exception as e:
            print(f"WARN set_autostop {sid[:8]}: {e}", flush=True)

    def get(self, sid: str):
        return self._req("GET", f"/sandbox/{sid}")

    def state(self, sid: str) -> str:
        return self.get(sid).get("state")

    def start(self, sid: str):
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

    def backup(self, sid: str, timeout=300):
        self._req("POST", f"/sandbox/{sid}/backup")
        t0 = time.time()
        done = {"Completed", "None"}
        error = {"Failed", "Error"}
        while self.get(sid).get("backupState") not in done:
            if self.get(sid).get("backupState") in error:
                raise RuntimeError(f"backup {sid[:8]} entered error state")
            if time.time() - t0 > timeout:
                raise TimeoutError(f"backup {sid[:8]} not completed after {timeout}s")
            time.sleep(3)

    def is_alive(self, sid: str) -> bool:
        """True if the sandbox exists and is not in an error state."""
        try:
            s = self.get(sid).get("state", "")
            return s != "error"
        except RuntimeError as e:
            if "404" in str(e):
                return False
            return True  # transient — assume alive, caller will find out

    def reap_strays(self, keep: list):
        """Delete sandboxes not in `keep` AND not tagged with our orchestrator labels.
        Only deletes sandboxes that belong to this project to avoid collateral damage."""
        keepset = set(keep)
        for x in self.list():
            # Only delete sandboxes we own (tagged project=babylon-cinema)
            labels = x.get("labels", {})
            if labels.get("project") != "babylon-cinema":
                continue
            if x["id"] not in keepset:
                try:
                    self.delete(x["id"])
                except Exception:
                    pass

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
    _MAX_INLINE = 5000

    def exec_detached(self, sid: str, command: str, logfile: str) -> None:
        """Launch a long command in the background; poll with exec_wait().

        A completion sentinel is appended to the log so exec_wait() detects "done" by the
        log marker, not by pgrep (which races: the process may not have spawned yet).
        Uses ERR trap to guarantee sentinel is written even if command calls exit.
        Commands larger than ~5KB are written to /tmp to avoid API proxy limits."""
        wrapped = (
            f"set -e; "
            f"trap 'echo {self.SENTINEL} >>{logfile}' ERR; "
            f"{command}; "
            f"echo {self.SENTINEL} >>{logfile}"
        )
        if len(wrapped) > self._MAX_INLINE:
            tag = str(hash(wrapped))[-8:]
            script_path = f"/tmp/daytona-{tag}.sh"
            write_cmd = f"cat >{script_path} <<'DYNEOF'\n{wrapped}\nDYNEOF"
            self.exec(sid, write_cmd, timeout=30)
            run_cmd = f"bash -l <{script_path} >{logfile} 2>&1 & echo started"
        else:
            run_cmd = f"nohup bash -lc {json.dumps(wrapped)} >{logfile} 2>&1 & echo started"
        self.exec(sid, run_cmd, timeout=30)

    def kill(self, sid: str, match: str):
        """Kill any process matching `match` on the sandbox (runaway agentic worker)."""
        self.exec(sid, f"pkill -9 -f {json.dumps(match)} 2>/dev/null; echo killed", timeout=30)

    def exec_wait(self, sid: str, match: str, logfile: str, timeout=480, stall=150):
        """Wait until the completion sentinel appears in the logfile; return its tail.

        Poll frequency starts at 1s and doubles every 15s up to 10s max.
        Two kill conditions, whichever fires first:
          - wall-clock `timeout`: hard cap on total runtime.
          - `stall`: the logfile stops GROWING for `stall` seconds. A hung `claude -p`/`codex`
            produces no output; waiting the full wall-clock wastes ~10min before the retry.
            Detecting the stall (no new bytes) kills it in ~`stall`s, so recovery is fast.
        On either, the runaway process is KILLED and TimeoutError is raised for the caller to
        handle as a failed iteration."""
        t0 = time.time()
        last_size, last_growth = -1, time.time()
        poll = 1.0
        while True:
            if time.time() - t0 > timeout:
                self.kill(sid, match)
                raise TimeoutError(f"'{match}' exceeded {timeout}s — killed")
            if time.time() - last_growth > stall:
                self.kill(sid, match)
                raise TimeoutError(f"'{match}' stalled {stall}s (no log output) — killed")
            try:
                _, out = self.exec(
                    sid,
                    f"if grep -q {self.SENTINEL} {logfile} 2>/dev/null; then echo DONE; "
                    f"tail -40 {logfile}; else echo RUNNING; wc -c <{logfile} 2>/dev/null; fi",
                    timeout=25, retries=1)
            except RuntimeError:
                out = "RUNNING"
            if "DONE" in out:
                return out
            try:
                size = int(out.strip().split()[-1])
                if size == 0 or size != last_size:
                    last_size, last_growth = size, time.time()
                    poll = 1.0
            except (ValueError, IndexError):
                pass
            elapsed = time.time() - t0
            poll = min(10.0, 1.0 * (2 ** (elapsed / 15.0)))
            time.sleep(poll)
