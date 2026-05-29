"""Pull local credentials to inject into a sandbox at runtime.

Never bake these into a snapshot — snapshots are stored. Inject at start instead.
"""
import base64
import json
import subprocess


def _run(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def claude_credentials_b64() -> str:
    """macOS Keychain item 'Claude Code-credentials' (the subscription OAuth)."""
    raw = _run(["security", "find-generic-password", "-s",
                "Claude Code-credentials", "-w"])
    return base64.b64encode(raw.encode()).decode()


def opencode_auth_b64(path="~/.local/share/opencode/auth.json") -> str:
    import os
    with open(os.path.expanduser(path), "rb") as f:
        return base64.b64encode(f.read()).decode()


def gh_token() -> str:
    return _run(["gh", "auth", "token"]).strip()


def inject_into_sandbox(dt, sid: str, gh: str):
    """Write Claude + OpenCode creds and git credentials onto the sandbox disk."""
    cc = claude_credentials_b64()
    oc = opencode_auth_b64()
    cmd = (
        "mkdir -p ~/.claude ~/.local/share/opencode && "
        f"echo {cc} | base64 -d > ~/.claude/.credentials.json && "
        f"echo {oc} | base64 -d > ~/.local/share/opencode/auth.json && "
        "chmod 600 ~/.claude/.credentials.json ~/.local/share/opencode/auth.json && "
        'git config --global user.email "marcosremar14@gmail.com" && '
        'git config --global user.name "babylon-cinema agent" && '
        "git config --global credential.helper store && "
        f"printf 'https://x-access-token:{gh}@github.com\\n' > ~/.git-credentials && "
        "chmod 600 ~/.git-credentials && echo creds-injected")
    code, out = dt.exec(sid, cmd, timeout=60)
    if "creds-injected" not in out:
        raise RuntimeError(f"cred injection failed: {out}")
