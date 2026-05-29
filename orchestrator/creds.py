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


def dumont_config_b64(path="~/.dumont/dumont.json") -> str:
    """Dumont config (providers incl. MiniMax M2.7). Holds an API key — inject at runtime."""
    import os
    p = os.path.expanduser(path)
    if not os.path.exists(p):
        return ""
    with open(p, "rb") as f:
        return base64.b64encode(f.read()).decode()


def gh_token() -> str:
    return _run(["gh", "auth", "token"]).strip()


def _file_b64(path) -> str:
    import os
    p = os.path.expanduser(path)
    if not os.path.exists(p):
        return ""
    with open(p, "rb") as f:
        return base64.b64encode(f.read()).decode()


def codex_auth_b64() -> str:
    """Codex ChatGPT auth (~/.codex/auth.json) — portable OAuth tokens."""
    return _file_b64("~/.codex/auth.json")


def codex_config_b64() -> str:
    """Codex config (~/.codex/config.toml) — pins model gpt-5.3-codex-spark."""
    return _file_b64("~/.codex/config.toml")


def minimax_key(env_file="/Users/marcos/projects/ai-gateway/.env") -> str:
    """MiniMax API key for dumont (referenced as $MINIMAX_API_KEY in dumont.json)."""
    import os
    k = os.environ.get("MINIMAX_API_KEY")
    if k:
        return k
    try:
        for line in open(env_file):
            if line.startswith("MINIMAX_API_KEY="):
                return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    return ""


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
    # codex (gpt-5.3-codex-spark worker): ChatGPT auth + config (model pin), if present locally
    ca, cc2 = codex_auth_b64(), codex_config_b64()
    if ca:
        cmd2 = f"mkdir -p ~/.codex && echo {ca} | base64 -d > ~/.codex/auth.json && chmod 600 ~/.codex/auth.json"
        if cc2:
            cmd2 += f" && echo {cc2} | base64 -d > ~/.codex/config.toml"
        cmd2 += " && echo codex-cfg"
        dt.exec(sid, cmd2, timeout=30)
    # dumont (MiniMax M2.7 worker): config ($MINIMAX_API_KEY ref, no secret in it) +
    # the Claude OAuth at ~/.dumont/.credentials.json (dumont's Linux login path) +
    # MINIMAX_API_KEY exported in ~/.profile so `bash -l` runs pick it up.
    dc = dumont_config_b64()
    if dc:
        mk = minimax_key()
        dt.exec(sid,
            "mkdir -p ~/.dumont && "
            f"echo {dc} | base64 -d > ~/.dumont/dumont.json && "
            f"echo {cc} | base64 -d > ~/.dumont/.credentials.json && "
            "chmod 600 ~/.dumont/dumont.json ~/.dumont/.credentials.json && "
            f"grep -q MINIMAX_API_KEY ~/.profile 2>/dev/null || echo 'export MINIMAX_API_KEY={mk}' >> ~/.profile && "
            "echo dumont-cfg", timeout=30)
