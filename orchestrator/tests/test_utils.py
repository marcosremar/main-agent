"""Tests for orchestrator utility functions."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

# Import from orchestrate (top-level functions, no sandbox/class refs)
import orchestrate

# --- error_signature -----------------------------------------------------------

def test_error_signature_strips_line_numbers():
    out = "Error at src/foo.ts:123: something failed\nTypeError: bar.js:456 is not a function"
    sig = orchestrate.error_signature(out)
    # line numbers stripped; keywords "error", "typeerror" remain; paths stay
    assert "error" in sig
    assert "typeerror" in sig
    assert "123" not in sig
    assert "456" not in sig
    assert "src/foo.ts" in sig or "foo.ts" in sig  # path preserved

def test_error_signature_deduplicates():
    out = "error line 1\nerror line 2\nerror line 3"
    sig = orchestrate.error_signature(out)
    lines = sig.splitlines()
    # all lines normalize to the same pattern after whitespace collapse -> single deduped line
    assert len(lines) == 1

def test_error_signature_retains_keyword_variants():
    # two distinct error keywords produce two distinct signature lines
    out = "ReferenceError at foo.js:10\nTypeError at bar.js:20"
    sig = orchestrate.error_signature(out)
    lines = sig.splitlines()
    assert len(lines) == 2
    assert "referenceerror" in sig
    assert "typeerror" in sig

def test_error_signature_sorted():
    out = "zebra error\nalpha error\nmiddle error"
    sig = orchestrate.error_signature(out)
    lines = sig.splitlines()
    assert lines == sorted(lines)

def test_error_signature_empty():
    assert orchestrate.error_signature("") == ""
    assert orchestrate.error_signature("just noise no keywords") == ""

def test_error_signature_key_matches():
    out = "EXPECTED something\ncannot find module"
    sig = orchestrate.error_signature(out)
    assert "expected" in sig
    assert "cannot" in sig

def test_error_signature_no_false_positives():
    # lines without any keyword must not appear
    out = "INFO: all good\nDEBUG: nothing\njust a normal line"
    sig = orchestrate.error_signature(out)
    assert sig == ""

def test_error_signature_hex_stripped():
    out = "Error 0x1a3b42 and 0xdeadbeef in code"
    sig = orchestrate.error_signature(out)
    # 0x-prefixed hex tokens stripped entirely
    assert "0x" not in sig
    # raw hex digits from within those tokens also gone
    assert "1a3b" not in sig
    # "dead" alone is NOT stripped (it's plain text, not a keyword line)
    # — hex-strip is only applied to lines that already match a keyword
    assert "dead" in sig or "code" in sig  # something meaningful remains


# --- b64 -----------------------------------------------------------------------

def test_b64_roundtrip():
    originals = ["hello", "hello world", "日本語", "newlines\nline2\n", "", "🎉"]
    for s in originals:
        encoded = orchestrate.b64(s)
        import base64
        decoded = base64.b64decode(encoded.encode()).decode()
        assert decoded == s, f"roundtrip failed for {s!r}"


# --- scope_ok (uses a real temp git repo) ------------------------------------

def test_scope_ok_exact_match(tmp_path):
    # repo with a tracked file
    (tmp_path / "README.md").write_text("hello")
    subprocess_run(["git", "init"], cwd=tmp_path)
    subprocess_run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path)
    subprocess_run(["git", "config", "user.name", "test"], cwd=tmp_path)
    subprocess_run(["git", "add", "README.md"], cwd=tmp_path)
    subprocess_run(["git", "commit", "-m", "init"], cwd=tmp_path)

    # modify it
    (tmp_path / "README.md").write_text("modified")
    subprocess_run(["git", "add", "README.md"], cwd=tmp_path)

    class MockDT:
        def exec(self, sid, cmd, timeout=None):
            import subprocess, os
            cwd = str(tmp_path)
            # simulate scope_ok's git commands
            r1 = subprocess.run("git diff --name-only HEAD", shell=True, capture_output=True, text=True, cwd=cwd)
            r2 = subprocess.run("git ls-files --others --exclude-standard", shell=True, capture_output=True, text=True, cwd=cwd)
            combined = r1.stdout + "\n" + r2.stdout
            return True, combined

    ok, changed, extra = orchestrate.scope_ok(MockDT(), "sid", str(tmp_path), ["README.md"])
    assert ok is True
    assert "README.md" in changed
    assert extra == []

def test_scope_ok_dir_prefix(tmp_path):
    # allowed dir "src/" matches file under it
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "foo.ts").write_text("code")

    subprocess_run(["git", "init"], cwd=tmp_path)
    subprocess_run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path)
    subprocess_run(["git", "config", "user.name", "test"], cwd=tmp_path)
    subprocess_run(["git", "add", "src/foo.ts"], cwd=tmp_path)
    subprocess_run(["git", "commit", "-m", "init"], cwd=tmp_path)

    (tmp_path / "src" / "foo.ts").write_text("changed")
    subprocess_run(["git", "add", "src/foo.ts"], cwd=tmp_path)

    class MockDT:
        def exec(self, sid, cmd, timeout=None):
            import subprocess
            cwd = str(tmp_path)
            r1 = subprocess.run("git diff --name-only HEAD", shell=True, capture_output=True, text=True, cwd=cwd)
            r2 = subprocess.run("git ls-files --others --exclude-standard", shell=True, capture_output=True, text=True, cwd=cwd)
            combined = r1.stdout + "\n" + r2.stdout
            return True, combined

    ok, changed, extra = orchestrate.scope_ok(MockDT(), "sid", str(tmp_path), ["src/"])
    assert ok is True
    assert extra == []

def test_scope_ok_out_of_scope(tmp_path):
    # a file NOT in allowed list
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "foo.ts").write_text("code")
    (tmp_path / "BAD.md").write_text("out of scope")

    subprocess_run(["git", "init"], cwd=tmp_path)
    subprocess_run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path)
    subprocess_run(["git", "config", "user.name", "test"], cwd=tmp_path)
    subprocess_run(["git", "add", "src/foo.ts", "BAD.md"], cwd=tmp_path)
    subprocess_run(["git", "commit", "-m", "init"], cwd=tmp_path)

    (tmp_path / "src" / "foo.ts").write_text("ok")
    (tmp_path / "BAD.md").write_text("changed")
    subprocess_run(["git", "add", "src/foo.ts", "BAD.md"], cwd=tmp_path)

    class MockDT:
        def exec(self, sid, cmd, timeout=None):
            import subprocess
            cwd = str(tmp_path)
            r1 = subprocess.run("git diff --name-only HEAD", shell=True, capture_output=True, text=True, cwd=cwd)
            r2 = subprocess.run("git ls-files --others --exclude-standard", shell=True, capture_output=True, text=True, cwd=cwd)
            combined = r1.stdout + "\n" + r2.stdout
            return True, combined

    ok, changed, extra = orchestrate.scope_ok(MockDT(), "sid", str(tmp_path), ["src/"])
    assert ok is False
    assert "BAD.md" in extra


# --- _infra_dead -------------------------------------------------------------

def test_infra_dead_sandbox_not_running():
    assert orchestrate._infra_dead("sandbox not running") is True

def test_infra_dead_sandbox_not_found():
    assert orchestrate._infra_dead("sandbox not found") is True

def test_infra_dead_http404_sandbox():
    assert orchestrate._infra_dead("http 404 sandbox") is True

def test_infra_dead_workspace_not_found():
    assert orchestrate._infra_dead("workspace not found") is True

def test_infra_dead_toolbox_not_found():
    assert orchestrate._infra_dead("toolbox not found") is True

def test_infra_dead_http404_only():
    assert orchestrate._infra_dead("http 404") is True

def test_infra_dead_false_positive():
    # no relevant keywords
    assert orchestrate._infra_dead("error in worker loop") is False
    assert orchestrate._infra_dead("task failed") is False
    assert orchestrate._infra_dead("timeout") is False

def test_infra_dead_case_insensitive():
    assert orchestrate._infra_dead("SANDBOX NOT RUNNING") is True
    assert orchestrate._infra_dead("HTTP 404 SANDBOX") is True


# --- lane_key (defined in main()) -------------------------------------------

def test_lane_key_claude():
    key = orchestrate.lane_key_func("claude")
    assert key == "claude"

def test_lane_key_dumont():
    assert orchestrate.lane_key_func("dumont") == "dumont"
    assert orchestrate.lane_key_func("dumont:minimax/m2-7") == "dumont"
    assert orchestrate.lane_key_func("dumont:minimax/m2-8") == "dumont"

def test_lane_key_codex():
    assert orchestrate.lane_key_func("codex") == "codex"
    assert orchestrate.lane_key_func("codex:gpt-5.3-codex-spark") == "codex"

def test_lane_key_minimax():
    assert orchestrate.lane_key_func("minimax") == "minimax"
    assert orchestrate.lane_key_func("minimax/MiniMax-M2.7") == "minimax"

def test_lane_key_unknown():
    assert orchestrate.lane_key_func("unknown-model") == "unknown-model"

def test_lane_key_fallback_returns_model_str():
    # ensure it always returns a string
    for m in ["claude", "dumont", "codex", "minimax", "some-other", ""]:
        result = orchestrate.lane_key_func(m)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Helper

def subprocess_run(cmd, cwd=None):
    import subprocess
    subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)