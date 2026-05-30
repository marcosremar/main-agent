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
import sys
import time

from daytona import Daytona
from creds import claude_credentials_b64, opencode_auth_b64, gh_token

import os
KEY = os.environ.get("DAYTONA_API_KEY", "")
BRANCH = os.environ.get("GOLDEN_BRANCH", "feat/city-pedestrian-population")
# override with DUMONT_ASSET_URL env var when the release asset ID changes
DUMONT_ASSET = os.environ.get(
    "DUMONT_ASSET_URL",
    "https://api.github.com/repos/marcosremar/dumont-code-agent/releases/assets/433362658"
)
SPARSE = "examples/game-engine src/game src/game-engine scripts/qa test package.json package-lock.json vitest.config.ts tsconfig.json"


def provision_one(dt, gh, cc, oc, tag="", stop_when_done=True, snapshot="daytona-medium"):
    """Provision one sandbox into a fully-ready golden; return its sid. Reusable for pools.

    stop_when_done=False leaves it RUNNING for immediate use (avoids a stop/start race
    where Daytona may delete the sandbox in between)."""
    sb = dt.create(auto_stop=120, snapshot=snapshot)  # 8G disk preset (vs 3G default)
    sid = sb["id"]
    dt.start(sid)
    print(f"{tag}sid {sid}")
    logf = f"/tmp/setup-{sid[:8]}.log"
    install = ("npm i -g @anthropic-ai/claude-code >/tmp/c.log 2>&1; "
               "npm i -g @openai/codex >/tmp/cx.log 2>&1; "
               "curl -fsSL https://opencode.ai/install | bash >/tmp/o.log 2>&1; echo CLIS_DONE")
    dt.exec_detached(sid, install, logf)
    dt.exec_wait(sid, "CLIS_DONE", logf, timeout=600)
    # dumont-code agent (MiniMax M2.7 worker): download the PREBUILT linux binary (release
    # asset) — self-contained, no clone/bun-install (which was flaky over sandbox network).
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
    dt.exec_detached(sid, "cd ~/babylon-cinema && npm ci --ignore-scripts --no-audit --no-fund && echo NPMCI_DONE",
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
    sb = dt.create(auto_stop=120, snapshot="daytona-medium")   # 8G disk; 2h idle window
    sid = sb["id"]
    dt.start(sid)
    print("sid", sid)

    gh = gh_token()
    cc = claude_credentials_b64()
    oc = opencode_auth_b64()

    print("installing CLIs (claude + opencode)...")
    logf = "/tmp/setup.log"
    install = ("npm i -g @anthropic-ai/claude-code >/tmp/c.log 2>&1; "
               "curl -fsSL https://opencode.ai/install | bash >/tmp/o.log 2>&1; "
               "echo CLIS_DONE")
    dt.exec_detached(sid, install, logf)
    dt.exec_wait(sid, "npm i -g", logf, timeout=600)

    print("injecting creds + git config...")
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

    print("sparse cloning repo (code only)...")
    clone = (
        "cd ~ && rm -rf babylon-cinema && "
        f"git clone --filter=blob:none --no-checkout --depth 1 --single-branch --branch {BRANCH} "
        "https://github.com/marcosremar/babylon-cinema.git && "
        "cd babylon-cinema && git sparse-checkout init --cone && "
        f"git sparse-checkout set {SPARSE} && git checkout && echo CLONE_DONE")
    _, out = dt.exec(sid, clone, timeout=300)
    if "CLONE_DONE" not in out:
        print("CLONE FAILED:", out[-400:]); sys.exit(1)

    print("npm ci (ignore-scripts)...")
    dt.exec_detached(sid, "cd ~/babylon-cinema && npm ci --ignore-scripts --no-audit --no-fund && echo NPMCI_DONE",
                     "/tmp/npmci.log")
    dt.exec_wait(sid, "npm ci", "/tmp/npmci.log", timeout=600)

    print("verifying golden...")
    _, out = dt.exec(sid,
        "export PATH=$PATH:$HOME/.opencode/bin; "
        "ls ~/babylon-cinema/package.json && ls ~/babylon-cinema/node_modules/.bin/vitest && "
        "ls ~/.claude/.credentials.json && which claude opencode && echo GOLDEN_OK", timeout=60)
    print(out)
    if "GOLDEN_OK" not in out:
        print("GOLDEN INCOMPLETE"); sys.exit(1)

    dt.stop(sid)
    print("\nGOLDEN READY (stopped, $0). sid:")
    print(sid)
    print("=> set this as golden_sid in config.json")


if __name__ == "__main__":
    main()
