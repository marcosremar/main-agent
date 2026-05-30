"""Pull local credentials to inject into a sandbox at runtime.

Never bake these into a snapshot — snapshots are stored. Inject at start instead.
"""
import base64
import json
import subprocess


def _run(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def claude_credentials_b64() -> str:
    """macOS Keychain 'Claude Code-credentials' OAuth, with the refreshToken STRIPPED.

    Why strip it: `claude -p` rotates the OAuth token on startup. With N concurrent workers
    sharing one credential, the first rotation invalidates the others -> 401 for ~N-1 of
    them (refresh-token-rotation race). Removing refreshToken stops any process from
    rotating, so all share the still-valid accessToken (re-inject fresh per batch from the
    keychain, which the Mac keeps refreshed)."""
    raw = _run(["security", "find-generic-password", "-s",
                "Claude Code-credentials", "-w"])
    raw = raw.strip()
    try:
        d = json.loads(raw)
        if isinstance(d, list):
            d = d[0] if d else {}
        o = d.get("claudeAiOauth")
        if isinstance(o, dict):
            o.pop("refreshToken", None)
        raw = json.dumps(d)
    except Exception as e:
        print(f"WARN claude_credentials_b64: could not strip refreshToken ({e}) — "
              f"concurrent workers may hit token rotation races", flush=True)
    return base64.b64encode(raw.encode()).decode()


def opencode_auth_b64(path="~/.local/share/opencode/auth.json") -> str:
    import os
    p = os.path.expanduser(path)
    if not os.path.exists(p):
        print(f"WARN opencode_auth_b64: {p} not found — opencode lane will be unavailable", flush=True)
        return ""
    with open(p, "rb") as f:
        content = f.read()
    try:
        decoded = base64.b64decode(content).decode("utf-8").strip()
        if not decoded:
            print(f"WARN opencode_auth_b64: {p} decoded to empty — opencode lane will be unavailable", flush=True)
            return ""
    except Exception as e:
        print(f"WARN opencode_auth_b64: could not decode {p} ({e}) — opencode lane will be unavailable", flush=True)
        return ""
    return content


def dumont_config_b64(path="~/.dumont/dumont.json") -> str:
    """Dumont config (providers incl. MiniMax M2.7). Holds an API key — inject at runtime."""
    import os
    p = os.path.expanduser(path)
    if not os.path.exists(p):
        return ""
    with open(p, "rb") as f:
        return base64.b64encode(f.read()).decode()


def gh_token() -> str:
    return subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True, stderr=subprocess.DEVNULL).stdout.strip()


def claude_oat_token(path="~/.claude-oat-token") -> str:
    """Long-lived Claude Code OAuth token (`claude setup-token`). Stable for headless +
    concurrency, unlike the short-lived keychain credentials that expire mid-session."""
    import os
    p = os.path.expanduser(path)
    if os.path.exists(p):
        return open(p).read().strip()
    print(f"WARN claude_oat_token: {p} not found — workers will use short-lived keychain "
          f"creds (may 401 mid-session). Run: claude setup-token > ~/.claude-oat-token", flush=True)
    return ""


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


def minimax_key(env_file="~/projects/ai-gateway/.env") -> str:
    """MiniMax API key for dumont (referenced as $MINIMAX_API_KEY in dumont.json)."""
    import os
    k = os.environ.get("MINIMAX_API_KEY")
    if k:
        return k
    p = os.path.expanduser(env_file)
    try:
        for line in open(p):
            if line.startswith("MINIMAX_API_KEY="):
                return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    return ""


def inject_into_sandbox(dt, sid: str, gh: str):
    cc = claude_credentials_b64()
    oc = opencode_auth_b64()
    cmd = (
        "mkdir -p ~/.claude ~/.local/share/opencode && "
        f"echo {cc} | base64 -d > ~/.claude/.credentials.json && "
        f"echo {oc} | base64 -d > ~/.local/share/opencode/auth.json && "
        "chmod 600 ~/.claude/.credentials.json ~/.local/share/opencode/auth.json && "
        'git config --global user.email "marcosremar14@gmail.com"; '
        'git config --global user.name "babylon-cinema agent"; '
        "git config --global credential.helper store && "
        f"printf 'https://x-access-token:{gh}@github.com\\n' > ~/.git-credentials && "
        "chmod 600 ~/.git-credentials && echo creds-injected")
    code, out = dt.exec(sid, cmd, timeout=60)
    if "creds-injected" not in out:
        raise RuntimeError(f"cred injection failed: {out}")
    oat = claude_oat_token()
    mk = minimax_key()
    ca, cc2 = codex_auth_b64(), codex_config_b64()
    if ca:
        cmd2 = f"mkdir -p ~/.codex && echo {ca} | base64 -d > ~/.codex/auth.json && chmod 600 ~/.codex/auth.json"
        if cc2:
            cmd2 += f" && echo {cc2} | base64 -d > ~/.codex/config.toml"
        cmd2 += " && echo codex-cfg"
        dt.exec(sid, cmd2, timeout=30)
    dc = dumont_config_b64()
    if dc:
        dt.exec(sid,
            "mkdir -p ~/.dumont && "
            f"echo {dc} | base64 -d > ~/.dumont/dumont.json && "
            f"echo {cc} | base64 -d > ~/.dumont/.credentials.json && "
            "chmod 600 ~/.dumont/dumont.json ~/.dumont/.credentials.json && "
            "echo dumont-cfg", timeout=30)
    return {"oat": oat, "minimax_key": mk}
