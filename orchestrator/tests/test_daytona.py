"""Tests for daytona.py — no real HTTP calls."""
import json
import time
import urllib.error
from unittest.mock import ANY, MagicMock, patch

import pytest

import daytona


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def d():
    """Daytona client with a throwaway key."""
    return daytona.Daytona("test-key-xyz")


# ---------------------------------------------------------------------------
# _req — retry on transient server errors
# ---------------------------------------------------------------------------

@patch("daytona.urllib.request.urlopen")
def test_req_retries_502(mock_urlopen, d):
    """HTTP 502 triggers retry with backoff — must succeed on second attempt."""
    errors = [
        make_error(502, "gateway bad"),
        make_response({"ok": True}),
    ]
    mock_urlopen.side_effect = errors
    result = d._req("GET", "/test")
    assert result == {"ok": True}
    assert mock_urlopen.call_count == 2


@patch("daytona.urllib.request.urlopen")
def test_req_retries_503(mock_urlopen, d):
    errors = [
        make_error(503, "unavailable"),
        make_response({"ok": True}),
    ]
    mock_urlopen.side_effect = errors
    result = d._req("GET", "/test")
    assert result == {"ok": True}
    assert mock_urlopen.call_count == 2


@patch("daytona.urllib.request.urlopen")
def test_req_retries_504(mock_urlopen, d):
    errors = [
        make_error(504, "timeout"),
        make_response({"ok": True}),
    ]
    mock_urlopen.side_effect = errors
    result = d._req("GET", "/test")
    assert result == {"ok": True}
    assert mock_urlopen.call_count == 2


@patch("daytona.urllib.request.urlopen")
def test_req_retries_429(mock_urlopen, d):
    errors = [
        make_error(429, "rate limited"),
        make_response({"ok": True}),
    ]
    mock_urlopen.side_effect = errors
    result = d._req("GET", "/test")
    assert result == {"ok": True}
    assert mock_urlopen.call_count == 2


@patch("daytona.urllib.request.urlopen")
def test_req_retries_all_transient_errors_give_up_after_retries(mock_urlopen, d):
    """Exhausted retries on 502 raises RuntimeError."""
    errors = [make_error(502, "bad")] * 4  # 1 initial + 3 retries
    mock_urlopen.side_effect = errors
    with pytest.raises(RuntimeError) as exc:
        d._req("GET", "/test", retries=4)
    assert "HTTP 502" in str(exc.value)


# ---------------------------------------------------------------------------
# _req — no retry for ordinary 4xx (except rate-limit)
# ---------------------------------------------------------------------------

@patch("daytona.urllib.request.urlopen")
def test_req_no_retry_400(mock_urlopen, d):
    mock_urlopen.side_effect = make_error(400, "bad request")
    with pytest.raises(RuntimeError) as exc:
        d._req("GET", "/test")
    assert "HTTP 400" in str(exc.value)
    assert mock_urlopen.call_count == 1


@patch("daytona.urllib.request.urlopen")
def test_req_no_retry_401(mock_urlopen, d):
    mock_urlopen.side_effect = make_error(401, "unauthorized")
    with pytest.raises(RuntimeError) as exc:
        d._req("GET", "/test")
    assert "HTTP 401" in str(exc.value)
    assert mock_urlopen.call_count == 1


@patch("daytona.urllib.request.urlopen")
def test_req_no_retry_403(mock_urlopen, d):
    mock_urlopen.side_effect = make_error(403, "forbidden")
    with pytest.raises(RuntimeError) as exc:
        d._req("GET", "/test")
    assert "HTTP 403" in str(exc.value)
    assert mock_urlopen.call_count == 1


@patch("daytona.urllib.request.urlopen")
def test_req_no_retry_404(mock_urlopen, d):
    mock_urlopen.side_effect = make_error(404, "not found")
    with pytest.raises(RuntimeError) as exc:
        d._req("GET", "/test")
    assert "HTTP 404" in str(exc.value)
    assert mock_urlopen.call_count == 1


# ---------------------------------------------------------------------------
# _req — network/timeout errors also retry
# ---------------------------------------------------------------------------

@patch("daytona.urllib.request.urlopen")
def test_req_retries_urlerror(mock_urlopen, d):
    import urllib.error
    errors = [
        urllib.error.URLError("connection refused"),
        make_response({"ok": True}),
    ]
    mock_urlopen.side_effect = errors
    result = d._req("GET", "/test")
    assert result == {"ok": True}
    assert mock_urlopen.call_count == 2


@patch("daytona.urllib.request.urlopen")
def test_req_retries_timeout_error(mock_urlopen, d):
    import urllib.error
    errors = [
        urllib.error.URLError("timed out"),
        make_response({"ok": True}),
    ]
    mock_urlopen.side_effect = errors
    result = d._req("GET", "/test")
    assert result == {"ok": True}
    assert mock_urlopen.call_count == 2


# ---------------------------------------------------------------------------
# _req — empty response body
# ---------------------------------------------------------------------------

@patch("daytona.urllib.request.urlopen")
def test_req_empty_body_returns_empty_dict(mock_urlopen, d):
    mock_urlopen.return_value = empty_response()
    result = d._req("DELETE", "/test")
    assert result == {}


# ---------------------------------------------------------------------------
# exec — base64 wrapping
# ---------------------------------------------------------------------------

@patch("daytona.urllib.request.urlopen")
def test_exec_base64_wraps_command(mock_urlopen, d):
    mock_urlopen.return_value = FakeResponse({"exitCode": 0, "result": "ok"})
    import base64
    cmd = 'echo "hello $HOME"'
    enc = base64.b64encode(cmd.encode()).decode()
    d.exec("sandbox-abc", cmd)
    args, kwargs = mock_urlopen.call_args
    # urlopen is called with (req, timeout=...) — the Request is positional arg 0
    req = args[0]
    body = json.loads(req.data.decode())
    expected_wrapped = f"echo {enc} | base64 -d | bash -l"
    assert body["command"] == expected_wrapped


# ---------------------------------------------------------------------------
# exec_wait — sentinel detection
# ---------------------------------------------------------------------------

@patch("daytona.time.sleep")
@patch.object(daytona.Daytona, "exec")
@patch.object(daytona.Daytona, "kill")
def test_exec_wait_returns_on_sentinel(mock_kill, mock_exec, mock_sleep, d):
    """When grep finds the sentinel it returns the tail output."""
    # First call: RUNNING + size; subsequent calls: DONE + tail
    mock_exec.side_effect = [
        (0, "RUNNING\n42"),
        (0, f"DONE\nsome output\n{daytona.Daytona.SENTINEL}"),
    ]
    result = d.exec_wait("sid-abc", "claude -p", "/tmp/out.log")
    assert "DONE" in result
    mock_kill.assert_not_called()


# ---------------------------------------------------------------------------
# exec_wait — stall detection
# ---------------------------------------------------------------------------

@patch("daytona.time.sleep")
@patch("daytona.time.time")
@patch.object(daytona.Daytona, "exec")
@patch.object(daytona.Daytona, "kill")
def test_exec_wait_kills_on_stall(mock_kill, mock_exec, mock_time, mock_sleep, d):
    """When the log stops growing for >stall seconds, kill + TimeoutError."""
    # Simulate time advancing: t0=0, poll at 0,10,25,45,70 (stall=30 so at 70s it fires)
    mock_time.side_effect = [0, 0, 10, 10, 25, 25, 45, 45, 70, 70]
    mock_exec.return_value = (0, "RUNNING\n100")  # size never changes → stalled

    with pytest.raises(TimeoutError) as exc:
        d.exec_wait("sid-abc", "claude -p", "/tmp/out.log", poll=10, timeout=480, stall=30)
    assert "stalled" in str(exc.value)
    mock_kill.assert_called()


# ---------------------------------------------------------------------------
# exec_wait — wall-clock timeout
# ---------------------------------------------------------------------------

@patch("daytona.time.sleep")
@patch("daytona.time.time")
@patch.object(daytona.Daytona, "exec")
@patch.object(daytona.Daytona, "kill")
def test_exec_wait_kills_on_wallclock_timeout(mock_kill, mock_exec, mock_time, mock_sleep, d):
    """When elapsed > timeout seconds, kill + TimeoutError."""
    # time advances past the 60s timeout
    mock_time.side_effect = [0, 0, 60, 60, 120, 120]
    mock_exec.return_value = (0, "RUNNING\n100")

    with pytest.raises(TimeoutError) as exc:
        d.exec_wait("sid-abc", "claude -p", "/tmp/out.log", poll=10, timeout=60, stall=150)
    assert "exceeded" in str(exc.value)
    mock_kill.assert_called()


# ---------------------------------------------------------------------------
# exec_wait — transient exec error keeps polling
# ---------------------------------------------------------------------------

@patch("daytona.time.sleep")
@patch.object(daytona.Daytona, "exec")
@patch.object(daytona.Daytona, "kill")
def test_exec_wait_keeps_polling_on_transient_exec_error(mock_kill, mock_exec, mock_sleep, d):
    """RuntimeError from exec is treated as RUNNING — stall/wall-clock still apply."""
    mock_exec.side_effect = [
        RuntimeError("proxy error"),          # transient — treated as RUNNING
        (0, "RUNNING\n1"),
        (0, f"DONE\noutput\n{daytona.Daytona.SENTINEL}"),
    ]
    result = d.exec_wait("sid-abc", "claude -p", "/tmp/out.log")
    assert "DONE" in result
    mock_kill.assert_not_called()


# ---------------------------------------------------------------------------
# is_alive — 404 → False, transient error → True
# ---------------------------------------------------------------------------

@patch.object(daytona.Daytona, "_req")
def test_is_alive_404_returns_false(mock_req, d):
    mock_req.side_effect = RuntimeError("GET /sandbox/abc -> HTTP 404: not found")
    assert d.is_alive("abc") is False


@patch.object(daytona.Daytona, "_req")
def test_is_alive_transient_error_returns_true(mock_req, d):
    mock_req.side_effect = RuntimeError("GET /sandbox/abc -> HTTP 503: unavailable")
    assert d.is_alive("abc") is True


@patch.object(daytona.Daytona, "_req")
def test_is_alive_error_state_returns_false(mock_req, d):
    mock_req.return_value = {"state": "error"}
    assert d.is_alive("abc") is False


@patch.object(daytona.Daytona, "_req")
def test_is_alive_started_state_returns_true(mock_req, d):
    mock_req.return_value = {"state": "started"}
    assert d.is_alive("abc") is True


# ---------------------------------------------------------------------------
# backup — exits when backupState == "Completed"
# ---------------------------------------------------------------------------

@patch("daytona.time.sleep")
@patch.object(daytona.Daytona, "_req")
def test_backup_exits_on_completed(mock_req, mock_sleep, d):
    mock_req.side_effect = [
        {},                      # POST /backup
        {"backupState": "InProgress"},
        {"backupState": "InProgress"},
        {"backupState": "Completed"},
    ]
    d.backup("sid-abc")
    # POST + 3 polls before "Completed" seen
    assert mock_req.call_count == 4


@patch("daytona.time.sleep")
@patch.object(daytona.Daytona, "_req")
def test_backup_timeout_raises(mock_req, mock_sleep, d):
    mock_req.side_effect = [
        {},                      # POST /backup
        {"backupState": "InProgress"},
    ]
    with pytest.raises(TimeoutError) as exc:
        d.backup("sid-abc", timeout=5)
    assert "not completed after" in str(exc.value)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeResponse:
    """Fake file-like HTTP response for urllib.request.urlopen."""

    def __init__(self, data):
        self._data = json.dumps(data).encode() if isinstance(data, dict) else data or b""

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class FakeHTTPError(urllib.error.HTTPError):
    """Proper HTTPError so daytona.py's isinstance(e, HTTPError) check returns True."""

    def __init__(self, code: int, msg: str):
        import io

        class _fp(io.BytesIO):
            def read(self, n=None):
                return super().read(n)

        super().__init__(
            "https://app.daytona.io/api/test",
            code,
            msg,
            {},
            _fp(b'{"error":"%s"}' % msg.encode()),
        )


def make_response(data):
    return FakeResponse(data)


def make_error(code, msg):
    return FakeHTTPError(code, msg)


def empty_response():
    return FakeResponse(b"")
