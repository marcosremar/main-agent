"""Tests for orchestrate.py — run with: python -m pytest tests/test_orchestrate.py -v"""
import os, sys, threading
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import orchestrate as orch


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def mm(ret):
    """MagicMock with a single return_value."""
    m = MagicMock()
    m.return_value = ret
    return m


def me(lst):
    """MagicMock with a side_effect list — cycles through items."""
    m = MagicMock()
    m.side_effect = lst
    return m


def make_task(overrides=None):
    t = {
        "id": "task-1",
        "spec": "make the change",
        "commit": "feat: task 1",
        "allowed_files": ["src/a.ts"],
        "worker_model": "minimax/MiniMax-M2.7",
        "max_iters": 20,
        "task_budget_s": 1500,
        "no_progress_limit": 6,
        "verify_cmd": "npm run typecheck:all",
        "location": "cloud",
    }
    if overrides:
        t.update(overrides)
    return t


def mock_dt():
    """Returns a mock Daytona client. dt.exec uses a cycling side_effect so each
    call gets its own distinct return. 100-item cycle is enough for any test; tests
    that need specific sequences reassign dt.exec = me([...])."""
    dt = MagicMock()
    dt.start = mm(None)
    # Cycle through: worktree OK, then sparse-checkout OK. 100-item cycle won't exhaust.
    cycle = [("sid", "WORKTREE_OK"), ("sid", "SPARSE_OK")] * 50
    dt.exec = me(cycle)
    dt.exec_detached = mm(None)
    dt.exec_wait = mm("")
    dt.is_alive = mm(True)
    return dt


def mock_gh():
    gh = MagicMock()
    gh.create_pr = mm({"number": 42})
    gh.pr_files = mm(["src/a.ts"])
    gh.merge_pr = mm({"merged": True})
    return gh


def noop_cm():
    """Yields a no-op — replaces contextlib.nullcontext(merge_lock)."""
    yield


def patch_merge_lock():
    """Replace nullcontext (imported locally in run_task) with a no-op context manager."""
    return patch("contextlib.nullcontext", side_effect=lambda: noop_cm())


# --------------------------------------------------------------------------- #
# run_task — happy path
# --------------------------------------------------------------------------- #

class TestRunTaskHappyPath:

    def test_merged_on_first_iter(self):
        dt = mock_dt()
        gh = mock_gh()
        with patch.object(orch, "verify", return_value=(True, "")):
            with patch.object(orch, "run_llm_verifier", return_value=(True, "")):
                with patch.object(orch, "scope_ok", return_value=(True, [], [])):
                    with patch_merge_lock():
                        result = orch.run_task(dt, gh, "sandbox-1", "feat/test", make_task())
        assert result["status"] == "MERGED"
        assert result["pr"] == 42
        assert result["iters"] == 1
        gh.create_pr.assert_called_once()
        gh.merge_pr.assert_called_once_with(42, "squash")

    def test_merged_after_three_iters(self):
        dt = mock_dt()
        gh = mock_gh()
        verify_calls = []

        original_verify = orch.verify

        def counting_verify(dt, sid, wt, verify_cmd):
            verify_calls.append((dt, sid, wt, verify_cmd))
            if len(verify_calls) < 3:
                return (False, "error: expected something")
            return (True, "")

        with patch.object(orch, "verify", side_effect=counting_verify):
            with patch.object(orch, "run_llm_verifier", return_value=(True, "")):
                with patch.object(orch, "scope_ok", return_value=(True, [], [])):
                    with patch_merge_lock():
                        result = orch.run_task(dt, gh, "sandbox-1", "feat/test", make_task())
        assert result["status"] == "MERGED"
        assert result["iters"] == 3

    def test_empty_allowed_files_returns_error(self):
        dt, gh = mock_dt(), mock_gh()
        task = make_task({"allowed_files": []})
        result = orch.run_task(dt, gh, "sandbox-1", "feat/test", task)
        assert result["status"] == "ERROR"
        assert "nothing to commit" in result["error"]


# --------------------------------------------------------------------------- #
# run_task — FAILED_MAX_ITERS
# --------------------------------------------------------------------------- #

class TestRunTaskFailedMaxIters:

    def test_failed_max_iters(self):
        dt = mock_dt()
        gh = mock_gh()
        with patch.object(orch, "verify", return_value=(False, "always broken")):
            with patch_merge_lock():
                result = orch.run_task(dt, gh, "sandbox-1", "feat/test",
                                       make_task({"max_iters": 3}))
        assert result["status"] == "FAILED_MAX_ITERS"
        assert result["iters"] == 3


# --------------------------------------------------------------------------- #
# run_task — TIMEOUT_BUDGET
# --------------------------------------------------------------------------- #

class TestRunTaskTimeoutBudget:

    def test_timeout_budget(self):
        dt = mock_dt()
        gh = mock_gh()
        with patch.object(orch.time, "time", return_value=1_000_000_000):
            with patch.object(orch, "verify", return_value=(False, "nope")):
                with patch_merge_lock():
                    result = orch.run_task(dt, gh, "sandbox-1", "feat/test",
                                           make_task({"task_budget_s": 1}))
        assert result["status"] == "TIMEOUT_BUDGET"


# --------------------------------------------------------------------------- #
# run_task — OUT_OF_SCOPE
# --------------------------------------------------------------------------- #

class TestRunTaskOutOfScope:

    def test_out_of_scope(self):
        dt = mock_dt()
        gh = mock_gh()
        with patch.object(orch, "verify", return_value=(True, "")):
            with patch.object(orch, "run_llm_verifier", return_value=(True, "")):
                with patch.object(orch, "scope_ok", return_value=(False, ["src/a.ts"], ["src/bad.ts"])):
                    with patch_merge_lock():
                        result = orch.run_task(dt, gh, "sandbox-1", "feat/test", make_task())
        assert result["status"] == "OUT_OF_SCOPE"
        assert "src/bad.ts" in result["extra"]


# --------------------------------------------------------------------------- #
# run_task — WORKTREE_FAILED
# --------------------------------------------------------------------------- #

class TestRunTaskWorktreeFailed:

    def test_worktree_missing_worktree_ok(self):
        dt = MagicMock()
        dt.start = mm(None)
        dt.exec = me([("sid", "some error")])  # no WORKTREE_OK
        dt.exec_detached = mm(None)
        dt.is_alive = mm(True)
        gh = mock_gh()
        with patch_merge_lock():
            result = orch.run_task(dt, gh, "sandbox-1", "feat/test", make_task())
        assert result["status"] == "WORKTREE_FAILED"

    def test_sparse_checkout_fails(self):
        dt = MagicMock()
        dt.start = mm(None)
        dt.exec = me([("sid", "WORKTREE_OK"), ("sid", "sparse error")])
        dt.exec_detached = mm(None)
        dt.is_alive = mm(True)
        gh = mock_gh()

        with patch_merge_lock():
            result = orch.run_task(dt, gh, "sandbox-1", "feat/test",
                                   make_task({"sparse_extra": ["src/modules/"]}))
        assert result["status"] == "WORKTREE_FAILED"


# --------------------------------------------------------------------------- #
# run_task — escalation minimax -> OPUS
# --------------------------------------------------------------------------- #

class TestRunTaskEscalation:

    def test_minimax_escalates_after_two_fails(self):
        dt = mock_dt()
        gh = mock_gh()
        worker_models = []

        def track_worker(dt, sid, wt, model, spec, tag, worker_timeout=480):
            worker_models.append(model)
            return ""

        # 4 verify failures so minimax fails >= 2 and we see the model switch
        with patch.object(orch, "run_worker", side_effect=track_worker):
            with patch.object(orch, "verify", return_value=(False, "fail")):
                with patch.object(orch, "scope_ok", return_value=(True, [], [])):
                    with patch_merge_lock():
                        result = orch.run_task(dt, gh, "sandbox-1", "feat/test",
                                               make_task({"max_iters": 5}))

        # Should have escalated to OPUS after 2 minimax failures
        assert orch.OPUS in worker_models

    def test_worker_timeout_triggers_escalation(self):
        dt = mock_dt()
        gh = mock_gh()
        worker_calls = []

        def track_worker(dt, sid, wt, model, spec, tag, worker_timeout=480):
            worker_calls.append(model)
            raise TimeoutError("hung")

        with patch.object(orch, "run_worker", side_effect=track_worker):
            with patch.object(orch, "verify", return_value=(False, "fail")):
                with patch_merge_lock():
                    result = orch.run_task(dt, gh, "sandbox-1", "feat/test",
                                           make_task({"max_iters": 4}))

        assert orch.OPUS in worker_calls


# --------------------------------------------------------------------------- #
# run_task — STUCK_NO_PROGRESS
# --------------------------------------------------------------------------- #

class TestRunTaskStuckNoProgress:

    def test_stuck_same_error_signature(self):
        dt = mock_dt()
        gh = mock_gh()

        err_output = "ERROR: cannot find module 'foo'\n"
        with patch.object(orch, "run_worker", return_value=""):
            with patch.object(orch, "verify", return_value=(False, err_output)):
                with patch_merge_lock():
                    result = orch.run_task(dt, gh, "sandbox-1", "feat/test",
                                           make_task({"no_progress_limit": 6, "max_iters": 20}))

        assert result["status"] == "STUCK_NO_PROGRESS"

    def test_different_signatures_not_stuck(self):
        """Different errors don't trigger stuck detection."""
        dt = mock_dt()
        gh = mock_gh()

        with patch.object(orch, "run_worker", return_value=""):
            with patch.object(orch, "verify", side_effect=[
                (False, "ERROR: module foo"),
                (False, "ERROR: module bar"),
                (False, "ERROR: module baz"),
                (False, "ERROR: module qux"),
            ]):
                with patch_merge_lock():
                    result = orch.run_task(dt, gh, "sandbox-1", "feat/test",
                                           make_task({"no_progress_limit": 2, "max_iters": 4}))

        assert result["status"] == "FAILED_MAX_ITERS"


# --------------------------------------------------------------------------- #
# already_done()
# --------------------------------------------------------------------------- #

class TestAlreadyDone:

    def test_merged_in_state(self):
        gh = mock_gh()
        state = {"task-1": {"status": "MERGED"}}
        task = make_task()
        assert orch.already_done(gh, "feat/test", task, state) is True

    def test_all_files_exist_on_branch(self):
        gh = mock_gh()
        gh.file_exists = mm(True)
        state = {}
        task = make_task({"allowed_files": ["src/a.ts", "src/b.ts"]})
        assert orch.already_done(gh, "feat/test", task, state) is True

    def test_some_files_missing(self):
        gh = mock_gh()
        # First file exists, second does not
        gh.file_exists = me([True, False])
        state = {}
        task = make_task({"allowed_files": ["src/a.ts", "src/b.ts"]})
        assert orch.already_done(gh, "feat/test", task, state) is False

    def test_empty_allowed_files_is_not_done(self):
        """Empty allowed_files: vacuous all([]) → not done → return False."""
        gh = mock_gh()
        state = {}
        task = make_task({"allowed_files": []})
        # files is falsy → skip check → return False
        assert orch.already_done(gh, "feat/test", task, state) is False

    def test_state_not_merged(self):
        gh = mock_gh()
        gh.file_exists = mm(False)  # files not on branch → not done
        state = {"task-1": {"status": "FAILED_MAX_ITERS"}}
        task = make_task()
        assert orch.already_done(gh, "feat/test", task, state) is False

    def test_file_exists_raises(self):
        """file_exists throws → caught → return False."""
        gh = mock_gh()
        gh.file_exists = me([Exception("network")])
        state = {}
        task = make_task({"allowed_files": ["src/a.ts"]})
        assert orch.already_done(gh, "feat/test", task, state) is False


# --------------------------------------------------------------------------- #
# error_signature()
# --------------------------------------------------------------------------- #

class TestErrorSignature:

    def test_strips_line_numbers(self):
        sig = orch.error_signature("Error at line 42: something\nError at line 99: something")
        # Numbers stripped → same fingerprint
        lines = sig.splitlines()
        assert len(lines) <= 2

    def test_strips_hex(self):
        sig = orch.error_signature("0x1a3b memory error\n0xdead beef")
        assert "0x" not in sig

    def test_empty_output(self):
        assert orch.error_signature("") == ""

    def test_no_error_keywords(self):
        assert orch.error_signature("hello world") == ""


# --------------------------------------------------------------------------- #
# scope_ok()
# --------------------------------------------------------------------------- #

class TestScopeOk:

    def test_exact_file_match(self):
        dt = MagicMock()
        dt.exec = mm(("sid", "src/a.ts"))
        ok, changed, extra = orch.scope_ok(dt, "sid", "/wt", ["src/a.ts"])
        assert ok is True
        assert extra == []

    def test_dir_prefix_match(self):
        dt = MagicMock()
        dt.exec = mm(("sid", "src/a.ts\nsrc/b.ts"))
        ok, changed, extra = orch.scope_ok(dt, "sid", "/wt", ["src/"])
        assert ok is True

    def test_out_of_scope_file(self):
        dt = MagicMock()
        dt.exec = mm(("sid", "src/a.ts\nsrc/b.ts"))
        ok, changed, extra = orch.scope_ok(dt, "sid", "/wt", ["src/a.ts"])
        assert ok is False
        assert "src/b.ts" in extra

    def test_node_modules_filtered(self):
        """node_modules is excluded by grep -v in the command itself."""
        dt = MagicMock()
        dt.exec = mm(("sid", "src/a.ts\nnode_modules/foo/bar.ts"))
        ok, changed, extra = orch.scope_ok(dt, "sid", "/wt", ["src/a.ts"])
        assert "node_modules" not in changed


# --------------------------------------------------------------------------- #
# git push failure
# --------------------------------------------------------------------------- #

class TestPushFailure:

    def test_push_fails_returns_error(self):
        dt = mock_dt()
        gh = mock_gh()
        # Exec cycle: worktree OK, sparse OK, then push FAIL
        dt.exec = me([("sid", "WORKTREE_OK"), ("sid", "SPARSE_OK"), ("sid", "PUSH_FAIL")])
        dt.exec_detached = mm(None)

        with patch.object(orch, "verify", return_value=(True, "")):
            with patch.object(orch, "run_llm_verifier", return_value=(True, "")):
                with patch.object(orch, "scope_ok", return_value=(True, [], [])):
                    with patch_merge_lock():
                        result = orch.run_task(dt, gh, "sandbox-1", "feat/test", make_task())

        assert result["status"] == "ERROR"
        assert "git push failed" in result["error"]


# --------------------------------------------------------------------------- #
# PR_OUT_OF_SCOPE
# --------------------------------------------------------------------------- #

class TestPROutOfScope:

    def test_pr_out_of_scope(self):
        dt = mock_dt()
        gh = mock_gh()
        gh.pr_files = mm(["src/a.ts", "src/b.ts"])  # b.ts not allowed

        with patch.object(orch, "verify", return_value=(True, "")):
            with patch.object(orch, "run_llm_verifier", return_value=(True, "")):
                with patch.object(orch, "scope_ok", return_value=(True, ["src/a.ts"], [])):
                    with patch_merge_lock():
                        result = orch.run_task(dt, gh, "sandbox-1", "feat/test", make_task())

        assert result["status"] == "PR_OUT_OF_SCOPE"
        assert "src/b.ts" in result["extra"]


# --------------------------------------------------------------------------- #
# resume_merge_if_open()
# --------------------------------------------------------------------------- #

class TestResumeMergeIfOpen:

    def test_resume_open_pr(self):
        gh = mock_gh()
        gh.open_pr_for = mm({"number": 7, "state": "open"})
        rm = orch.resume_merge_if_open(gh, "feat/test", make_task(), threading.Lock())
        assert rm is not None
        assert rm["status"] == "MERGED"
        assert rm["resumed"] == "pr"

    def test_no_pr_to_resume(self):
        gh = mock_gh()
        gh.open_pr_for = mm(None)
        rm = orch.resume_merge_if_open(gh, "feat/test", make_task(), threading.Lock())
        assert rm is None


# --------------------------------------------------------------------------- #
# run_llm_verifier()
# --------------------------------------------------------------------------- #

class TestLlmVerifier:

    def test_llm_verifier_passes(self):
        dt = MagicMock()
        dt.exec = mm(("sid", "done"))
        dt.exec_detached = mm(None)
        dt.exec_wait = mm("VERDICT: PASS")
        ok, out = orch.run_llm_verifier(dt, "sid", "/wt", make_task(), "tag-1")
        assert ok is True

    def test_llm_verifier_fails(self):
        dt = MagicMock()
        dt.exec = mm(("sid", "done"))
        dt.exec_detached = mm(None)
        dt.exec_wait = mm("VERDICT: FAIL: not complete")
        ok, out = orch.run_llm_verifier(dt, "sid", "/wt", make_task(), "tag-1")
        assert ok is False

    def test_llm_verifier_timeout(self):
        dt = MagicMock()
        dt.exec = mm(("sid", "done"))
        dt.exec_detached = mm(None)
        dt.exec_wait = me([TimeoutError("validator hung")])
        ok, out = orch.run_llm_verifier(dt, "sid", "/wt", make_task(), "tag-1")
        assert ok is False
        assert "TIMED OUT" in out


# --------------------------------------------------------------------------- #
# integration_branch flows through to worktree command
# --------------------------------------------------------------------------- #

class TestIntegrationBranchFlow:

    def test_integration_branch_passed_to_git_worktree(self):
        dt = mock_dt()
        gh = mock_gh()
        with patch.object(orch, "verify", return_value=(True, "")):
            with patch.object(orch, "run_llm_verifier", return_value=(True, "")):
                with patch.object(orch, "scope_ok", return_value=(True, [], [])):
                    with patch_merge_lock():
                        orch.run_task(dt, gh, "sandbox-1", "feat/my-branch", make_task())
        call_text = str(dt.exec.call_args_list)
        assert "feat/my-branch" in call_text


# --------------------------------------------------------------------------- #
# escalation resets stuck counter
# --------------------------------------------------------------------------- #

class TestEscalationResetsStuck:

    def test_stuck_resets_after_escalation(self):
        """Escalation resets stuck counter (so OPUS gets fresh budget)."""
        dt = mock_dt()
        gh = mock_gh()
        fake_verify = me([
            (False, "ERROR: sig-a"),
            (False, "ERROR: sig-a"),   # stuck=1
            (False, "ERROR: sig-a"),   # stuck=2 → escalate, reset stuck to 0
            (False, "ERROR: sig-b"),   # OPUS iter 1 (different sig → stuck=0)
            (False, "ERROR: sig-b"),   # stuck=1
            (False, "ERROR: sig-b"),   # stuck=2 → STUCK (not FAILED_MAX_ITERS)
        ])
        with patch.object(orch, "run_worker", return_value=""):
            with patch.object(orch, "verify", side_effect=fake_verify):
                with patch.object(orch, "run_llm_verifier", return_value=(False, "verifier fail")):
                    with patch_merge_lock():
                        result = orch.run_task(dt, gh, "sandbox-1", "feat/test",
                                               make_task({"no_progress_limit": 3, "max_iters": 10}))
        # stuck counter is reset on escalation, so OPUS gets its own budget.
        # the test verifies STUCK (not FAILED_MAX_ITERS) because OPUS keeps hitting sig-b.
        # (if sig-b was also repeated we'd get FAILED_MAX_ITERS)
        assert result["status"] == "STUCK_NO_PROGRESS"