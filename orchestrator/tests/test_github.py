"""Tests for github.py — no real HTTP calls."""
import json
import sys
import time
import urllib.error
from unittest.mock import patch, MagicMock

import pytest
sys.path.insert(0, ".")
from github import GitHub

API = "https://api.github.com"
REPO = "marcosremar/babylon-cinema"
GH = "ghp_testtoken"


# ---------------------------------------------------------------------------
# mock helpers
# ---------------------------------------------------------------------------

def ok_resp(data):
    m = MagicMock()
    m.__enter__ = MagicMock(return_value=m)
    m.__exit__ = MagicMock(return_value=False)
    m.read.return_value = json.dumps(data).encode()
    m.status = 200
    return m


class FakeHTTPError(urllib.error.HTTPError):
    """HTTPError-compatible exception that urlopen raises on HTTP errors."""

    def __init__(self, code: int, body: str):
        # Signature: (url, code, msg, hdrs, fp)
        super().__init__(
            url=f"{API}/test",
            code=code,
            msg="test",
            hdrs={},
            fp=None,
        )
        self._body_bytes = body.encode()

    def read(self):
        return self._body_bytes


def http_err(code, body=""):
    return FakeHTTPError(code, body)


def make_file_page(files, page):
    return [{"filename": f, "sha": f"{page}_{f}"} for f in files]


# ---------------------------------------------------------------------------
# create_pr — happy path
# ---------------------------------------------------------------------------

def test_create_pr_happy_path():
    gh = GitHub(GH)
    want = {"number": 42, "title": "feat: new thing", "body": "details"}
    with patch("urllib.request.urlopen", return_value=ok_resp(want)) as mock_urlopen:
        got = gh.create_pr(
            head="agent/123",
            base="feat/city",
            title="feat: new thing",
            body="details",
        )
    assert mock_urlopen.call_count == 1
    req = mock_urlopen.call_args[0][0]
    assert req.method == "POST"
    assert f"/repos/{REPO}/pulls" in req.full_url
    payload = json.loads(req.data)
    assert payload["head"] == "agent/123"
    assert payload["base"] == "feat/city"
    assert payload["title"] == "feat: new thing"
    assert payload["body"] == "details"
    assert got == want


# ---------------------------------------------------------------------------
# merge_pr — happy path
# ---------------------------------------------------------------------------

def test_merge_pr_happy_path():
    gh = GitHub(GH)
    want = {"merged": True, "sha": "abc123"}
    with patch("urllib.request.urlopen", return_value=ok_resp(want)) as mock_urlopen:
        got = gh.merge_pr(42)
    assert mock_urlopen.call_count == 1
    req = mock_urlopen.call_args[0][0]
    assert req.method == "PUT"
    assert "/pulls/42/merge" in req.full_url
    assert got == want


# ---------------------------------------------------------------------------
# merge_pr — MERGE_CONFLICT on 405
# ---------------------------------------------------------------------------

def test_merge_pr_merge_conflict_405():
    gh = GitHub(GH)
    with patch("urllib.request.urlopen", side_effect=http_err(405, "Pull request is not mergeable")):
        with pytest.raises(RuntimeError) as exc_info:
            gh.merge_pr(42)
    assert "MERGE_CONFLICT" in str(exc_info.value)
    assert "405" in str(exc_info.value)


def test_merge_pr_merge_conflict_keyword_in_body():
    """'conflict' in body text also triggers MERGE_CONFLICT."""
    gh = GitHub(GH)
    with patch("urllib.request.urlopen", side_effect=http_err(409, "Merge conflict detected")):
        with pytest.raises(RuntimeError) as exc_info:
            gh.merge_pr(42)
    assert "MERGE_CONFLICT" in str(exc_info.value)


# ---------------------------------------------------------------------------
# pr_files — pagination
# ---------------------------------------------------------------------------

def test_pr_files_one_page():
    """Page with < 100 items — loop exits after one call."""
    gh = GitHub(GH)
    with patch("urllib.request.urlopen", return_value=ok_resp(make_file_page(["src/a.ts", "src/b.ts"], 1))):
        got = gh.pr_files(42)
    assert got == ["src/a.ts", "src/b.ts"]


def test_pr_files_two_pages():
    """Page 1 is full (100 items) → fetch page 2 which is < 100 → stop."""
    gh = GitHub(GH)
    page1_files = [f"src/f{i}.ts" for i in range(100)]
    page2_files = ["src/a.ts", "src/b.ts"]
    pages_iter = iter([
        ok_resp(make_file_page(page1_files, 1)),
        ok_resp(make_file_page(page2_files, 2)),
    ])
    with patch("urllib.request.urlopen", side_effect=pages_iter) as mock_urlopen:
        got = gh.pr_files(42)
    assert got == page1_files + page2_files
    assert mock_urlopen.call_count == 2


def test_pr_files_stops_at_page_3():
    """Safety limit: max 300 files (3 × 100). All three pages are full → still stop."""
    gh = GitHub(GH)
    pages_iter = iter([
        ok_resp(make_file_page([f"src/p1f{i}.ts" for i in range(100)], 1)),
        ok_resp(make_file_page([f"src/p2f{i}.ts" for i in range(100)], 2)),
        ok_resp(make_file_page([f"src/p3f{i}.ts" for i in range(50)], 3)),
    ])
    with patch("urllib.request.urlopen", side_effect=pages_iter) as mock_urlopen:
        got = gh.pr_files(42)
    assert mock_urlopen.call_count == 3
    assert len(got) == 250  # 100 + 100 + 50


# ---------------------------------------------------------------------------
# open_pr_for
# ---------------------------------------------------------------------------

def test_open_pr_for_returns_newest():
    """Multiple PRs for same head branch → pick highest number (newest)."""
    gh = GitHub(GH)
    prs = [
        {"number": 10, "title": "first", "state": "open"},
        {"number": 99, "title": "newest", "state": "open"},
        {"number": 55, "title": "middle", "state": "open"},
    ]
    with patch("urllib.request.urlopen", return_value=ok_resp(prs)):
        got = gh.open_pr_for("agent/123", "feat/city")
    assert got["number"] == 99


def test_open_pr_for_returns_none_when_empty():
    gh = GitHub(GH)
    with patch("urllib.request.urlopen", return_value=ok_resp([])):
        got = gh.open_pr_for("agent/123", "feat/city")
    assert got is None


# ---------------------------------------------------------------------------
# rate-limit retry on 403 with "rate limit" in body
# ---------------------------------------------------------------------------

def test_rate_limit_retry_on_403():
    gh = GitHub(GH)
    pages_iter = iter([
        http_err(403, "API rate limit exceeded"),
        ok_resp({"number": 1}),
    ])
    with patch("urllib.request.urlopen", side_effect=pages_iter) as mock_urlopen:
        with patch("time.sleep") as mock_sleep:
            got = gh.create_pr("head", "base", "title", "body")
    assert mock_urlopen.call_count == 2
    assert mock_sleep.call_count >= 1
    assert got == {"number": 1}


def test_rate_limit_no_retry_on_403_without_rate_limit_text():
    """403 without 'rate limit' in body → raises immediately, no retry."""
    gh = GitHub(GH)
    with patch("urllib.request.urlopen", side_effect=http_err(403, "Resource not found")):
        with pytest.raises(RuntimeError) as exc_info:
            gh.create_pr("head", "base", "title", "body")
    assert "403" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 502 / 503 retry with backoff
# ---------------------------------------------------------------------------

def test_502_retry_with_backoff():
    gh = GitHub(GH)
    pages_iter = iter([http_err(502, "Bad gateway"), ok_resp({"number": 1})])
    with patch("urllib.request.urlopen", side_effect=pages_iter):
        with patch("time.sleep") as mock_sleep:
            got = gh.create_pr("head", "base", "title", "body")
    assert mock_sleep.call_count >= 1
    first_sleep = mock_sleep.call_args_list[0][0][0]
    assert 1 <= first_sleep <= 3
    assert got == {"number": 1}


def test_503_retry_with_backoff():
    gh = GitHub(GH)
    pages_iter = iter([http_err(503, "Service unavailable"), ok_resp({"number": 1})])
    with patch("urllib.request.urlopen", side_effect=pages_iter):
        with patch("time.sleep") as mock_sleep:
            got = gh.create_pr("head", "base", "title", "body")
    assert mock_sleep.call_count >= 1
    first_sleep = mock_sleep.call_args_list[0][0][0]
    assert 1 <= first_sleep <= 3
    assert got == {"number": 1}


def test_504_no_retry():
    """504 is not in the explicit retry list → raises immediately."""
    gh = GitHub(GH)
    with patch("urllib.request.urlopen", side_effect=http_err(504, "Gateway timeout")):
        with pytest.raises(RuntimeError) as exc_info:
            gh.create_pr("head", "base", "title", "body")
    assert "504" in str(exc_info.value)