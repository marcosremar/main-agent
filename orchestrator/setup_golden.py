"""Provision (or rebuild) a golden sandbox from scratch — repeatable.

Single mutable goldens vanish (auto-stop -> archive -> restore changes ids / loses disk).
This script builds a fresh one deterministically and prints its id, so the golden is
reproducible rather than precious.

Usage:
  python3 setup_golden.py            # delete all sandboxes, build one fresh golden
  python3 setup_golden.py --keep     # build a golden without deleting existing sandboxes
"""
import base64
import json
import subprocess
import sys
import time

from daytona import Daytona
from creds import claude_credentials_b64, opencode_auth_b64, gh_token

import os
KEY = os.environ.get("DAYTONA_API_KEY", "")
BRANCH = os.environ.get("GOLDEN_BRANCH", "feat/city-pedestrian-population")
DAYTONA_SANDBOX_SIZE = os.environ.get("DAYTONA_SANDBOX_SIZE", "daytona-medium")
DUMONT_ASSET = os.environ.get(
    "DUMONT_ASSET_URL",
    "https://api.github.com/repos/marcosremar/dumont-code-agent/releases/assets/433362658"
)
BABYLON_REPO = os.environ.get("BABYLON_REPO_PATH", "/Users/marcos/projects/babylon-cinema")


def _discover_sparse_dirs(repo_path: str) -> str:
    """Discover real directory structure from babylon-cinema repo using git ls-files."""
    result = subprocess.run(
        ["git", "-C", repo_path, "ls-files"],
        capture_output=True, text=True, check=True
    )
    dirs = sorted(set("/".join(line.split("/")[:2]) for line in result.stdout.strip().splitlines() if line))
    return " ".join(dirs)


SPARSE = _discover_sparse_dirs(BABYLON_REPO)


def _npm_install_with_retry(cmd: str, timeout: int = 300, retries: int = 3) -> str:
    """Run npm i -g with retry loop; returns output on success, raises on failure."""
    for attempt in range(retries):
        logf = f"/tmp/npm-global-{int(time.time())}.log"
        install_cmd = f"{cmd} >{logf} 2>&1; echo NPM_GLOBAL_DONE"
        proc = subprocess.Popen(["bash", "-c", install_cmd])
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            if attempt < retries - 1:
                time.sleep(5)
                continue
            raise RuntimeError(f"npm i -g timed out after {timeout}s across {retries} attempts")
        with open(logf) as f:
            out = f.read()
        if "NPM_GLOBAL_DONE" in out:
            return out
        if attempt < retries - 1:
            time.sleep(5)
    raise RuntimeError(f"npm i -g failed after {retries} attempts: {out[-300:]}")


def provision_one(dt, gh, cc, oc, tag="", stop_when_done=True, snapshot=None):
    """Provision one sandbox into a fully-ready golden; return its sid. Reusable for pools.

    stop_when_done=False leaves it RUNNING for immediate use (avoids a stop/start race
    where Daytona may delete the sandbox in between)."""
    snapshot = snapshot or DAYTONA_SANDBOX_SIZE
    sb = dt.create(auto_stop=120, snapshot=snapshot)
    sid = sb["id"]
    dt.start(sid)
    print(f"{tag}sid {sid}")
    logf = f"/tmp/setup-{sid[:8]}.log"

    npm_retry_log = _npm_install_with_retry(
        "npm i -g @anthropic-ai/claude-code @openai/codex", timeout=300, retries=3
    )
    opencode_install = _npm_install_with_retry(
        "curl -fsSL https://opencode.ai/install | bash", timeout=300, retries=3
    )

    dt.exec_detached(sid, f"echo 'CLIS_DONE' && echo '{npm_retry_log[:50]}'", logf)
    dt.exec_wait(sid, "CLIS_DONE", logf, timeout=30)

    dlog = f"/tmp/dumont-{sid[:8]}.log"
    dumont = (
        "mkdir -p ~/bin && "
        f"curl -fsSL -H 'Authorization: Bearer {gh}' -H 'Accept: application/octet-stream' "
        f"{DUMONT_ASSET} -o /tmp/d.gz && gunzip -f /tmp/d.gz && mv /tmp/d ~/bin/dumont && "
        "chmod +x ~/bin/dumont && ~/bin/dumont --version >/dev/null 2>&1; echo DUMONT_DONE")
    dt.exec_detached(sid, dumont, dlog)
    dt.exec_wait(sid, "dumont", dlog, timeout=300)
    dt.exec(sid,
        "mkdir -p ~/.claude ~/.local/share/opencode && "
        f"echo {cc} | base64 -d > ~/.claude/.credentials.json && "
        f"echo {oc} | base64 -d > ~/.local/share/opencode/auth.json && "
        "chmod 600 ~/.claude/.credentials.json ~/.local/share/opencode/auth.json && "
        'git config --global user.email "marcosremar14@gmail.com" && '
        'git config --global user.name "babylon-cinema agent" && '
        "git config --global credential.helper store && "
        f"printf 'https://x-access-token:{gh}@github.com\\n' > ~/.git-credentials && "
        "chmod 600 ~/.git-credentials && echo CREDS_DONE", timeout=60)
    clone = (
        "cd ~ && rm -rf babylon-cinema && "
        f"git clone --filter=blob:none --no-checkout --depth 1 --single-branch --branch {BRANCH} "
        "https://github.com/marcosremar/babylon-cinema.git && "
        "cd babylon-cinema && git sparse-checkout init --cone && "
        f"git sparse-checkout set {SPARSE} && git checkout && echo CLONE_DONE")
    _, out = dt.exec(sid, clone, timeout=300)
    if "CLONE_DONE" not in out:
        raise RuntimeError(f"clone failed {sid[:8]}: {out[-300:]}")
    dt.exec_detached(sid, "cd ~/babylon-cinema && npm ci --no-audit --no-fund && echo NPMCI_DONE",
                     f"/tmp/npmci-{sid[:8]}.log")
    dt.exec_wait(sid, "npm ci", f"/tmp/npmci-{sid[:8]}.log", timeout=600)
    _, out = dt.exec(sid,
        "export PATH=$PATH:$HOME/.opencode/bin; ls ~/babylon-cinema/node_modules/.bin/vitest && "
        "ls ~/.claude/.credentials.json && which claude && echo GOLDEN_OK", timeout=60)
    if "GOLDEN_OK" not in out:
        raise RuntimeError(f"golden incomplete {sid[:8]}")
    if stop_when_done:
        dt.stop(sid)
    return sid


def main():
    keep = "--keep" in sys.argv
    dt = Daytona(KEY)

    if not keep:
        for x in dt.list():
            print("delete", x["id"][:8])
            try: dt.delete(x["id"])
            except Exception as e: print("  warn", e)

    print("creating sandbox...")
    gh = gh_token()
    cc = claude_credentials_b64()
    oc = opencode_auth_b64()

    sid = provision_one(dt, gh, cc, oc, tag="", stop_when_done=True, snapshot=DAYTONA_SANDBOX_SIZE)

    print("\nGOLDEN READY (stopped, $0). sid:")
    print(sid)
    print("=> set this as golden_sid in config.json")


if __name__ == "__main__":
    main()
