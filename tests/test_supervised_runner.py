"""Tests for the supervised agent runner (v1.0)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from bound.supervised_runner import (
    SupervisedConfig,
    SupervisedRunner,
    SupervisedRunResult,
)


class TestSupervisedConfig:
    def test_defaults(self) -> None:
        cfg = SupervisedConfig()
        assert cfg.agent_id == "claude-code"
        assert cfg.max_retries == 2
        assert cfg.max_replans == 2
        assert cfg.max_candidates == 2
        assert cfg.no_worktree is False

    def test_custom_agent(self) -> None:
        cfg = SupervisedConfig(agent_id="codex", max_retries=3)
        assert cfg.agent_id == "codex"
        assert cfg.max_retries == 3


class TestSupervisedRunResult:
    def test_default_values(self) -> None:
        r = SupervisedRunResult(decision="ACCEPT")
        assert r.decision == "ACCEPT"
        assert r.run_id == ""
        assert r.attempts == 0
        assert r.retries == 0
        assert r.replans == 0

    def test_full_result(self) -> None:
        r = SupervisedRunResult(
            decision="RETRY", run_id="run-1", attempts=3, retries=2, replans=0
        )
        assert r.decision == "RETRY"
        assert r.attempts == 3
        assert r.retries == 2


class TestSupervisedRunner:
    def test_accept_on_evidence_pass(self) -> None:
        cfg = SupervisedConfig(max_retries=1, max_replans=1)
        runner = SupervisedRunner(cfg)
        with patch.object(runner, "_invoke_agent", return_value="output"), \
             patch.object(runner, "_collect_evidence", return_value=True):
            result = runner.run("Test task")
            assert result.decision == "ACCEPT"
            assert result.attempts == 1

    def test_retry_on_evidence_fail(self) -> None:
        cfg = SupervisedConfig(max_retries=2, max_replans=1)
        runner = SupervisedRunner(cfg)
        with patch.object(runner, "_invoke_agent", return_value="output"), \
             patch.object(runner, "_collect_evidence", side_effect=[False, True]):
            result = runner.run("Test task")
            assert result.decision == "ACCEPT"
            assert result.retries == 1

    def test_replan_after_retries_exhausted(self) -> None:
        cfg = SupervisedConfig(max_retries=1, max_replans=2)
        runner = SupervisedRunner(cfg)
        with patch.object(runner, "_invoke_agent", return_value="output"), \
             patch.object(runner, "_collect_evidence", side_effect=[False, False, True]):
            result = runner.run("Test task")
            assert result.decision == "ACCEPT"
            assert result.replans >= 1

    def test_failed_when_all_budgets_exhausted(self) -> None:
        cfg = SupervisedConfig(max_retries=2, max_replans=2)
        runner = SupervisedRunner(cfg)
        with patch.object(runner, "_invoke_agent", return_value="output"), \
             patch.object(runner, "_collect_evidence", return_value=False):
            result = runner.run("Test task")
            assert result.decision == "FAILED"

    def test_evidence_true_when_no_pytest(self) -> None:
        cfg = SupervisedConfig()
        runner = SupervisedRunner(cfg)
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert runner._collect_evidence(Path("/tmp")) is True
