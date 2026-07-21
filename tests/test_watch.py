"""Tests for the event-driven watch mode (``bound watch``, v0.8.0 Sprint 2).

Tests cover every handler, the CLI command, and the full event loop with
mocked stdin/stdout and services.
"""

from __future__ import annotations

import io
import json
import logging
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from pydantic import ValidationError

from bound.lineage_store import LineageStore

from bound.events_watch import (
    WATCH_EVENT_SCHEMA_VERSION,
    parse_watch_event,
)
from bound.models import (
    BoundCriteria,
    EvaluationResult,
)
from bound.services import (
    BoundaryEvaluateResponse,
    OutcomeRecordRequest,
    RunFinishRequest,
    RunStartResponse,
    RunFinishResponse,
    BoundaryService,
    RunService,
    OutcomeService,
)
from bound.watch import (
    WatchConfig,
    WatchEngine,
    WatchPolicyLoadError,
    WatchShutdown,
    _TaskState,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POLICY = str(REPO_ROOT / "src" / "bound" / "default_policy.yaml")


def _make_event(event_type: str, **overrides: Any) -> dict[str, Any]:
    """Build a minimal watch event dict with default fields."""
    base: dict[str, Any] = {
        "schema_version": WATCH_EVENT_SCHEMA_VERSION,
        "event": event_type,
        "task_id": "test-task-001",
        "timestamp": "2026-07-21T12:00:00Z",
        "sequence": 1,
    }
    type_defaults: dict[str, dict[str, Any]] = {
        "task_started": {"goal": "Test the boundary system"},
        "step_completed": {
            "step_id": "PHASE-001",
            "description": "Implement the feature",
            "attempt": 1,
        },
        "verification_requested": {
            "step_id": "PHASE-001",
        },
        "control_action_reported": {
            "step_id": "PHASE-001",
            "evaluation_id": "eval-001",
            "action": "continue",
        },
        "control_action_observed": {
            "step_id": "PHASE-001",
            "evaluation_id": "eval-001",
            "intended_action": "continue",
            "observed_action": "continue",
            "matches_intended": True,
        },
        "task_finished": {
            "outcome": "completed",
        },
    }

    merged = {**base, **(type_defaults.get(event_type, {}))}
    merged.update(overrides)
    return merged


def _make_engine(**config_kw: Any) -> WatchEngine:
    """Create a WatchEngine with a mocked stdin/stdout and config."""
    kwargs: dict[str, Any] = dict(config_kw)
    kwargs.setdefault("policy_path", DEFAULT_POLICY)
    kwargs.setdefault("once", False)
    kwargs.setdefault("json_output", False)
    store = kwargs.pop("store", None)
    config = WatchConfig(**kwargs)
    if store is not None:
        config.store = store  # type: ignore[assignment]
    stdin = MagicMock()
    stdout = MagicMock()
    return WatchEngine(config, stdin=stdin, stdout=stdout)


def _sentinel_store() -> tuple[LineageStore, str]:
    """Return a (LineageStore, tmpdir) pair backed by a temp directory."""
    tmpdir = tempfile.mkdtemp(prefix="bound_watch_test_")
    store = LineageStore(base_dir=tmpdir)
    return store, tmpdir


# ---------------------------------------------------------------------------
# WatchConfig
# ---------------------------------------------------------------------------


class TestWatchConfig:
    def test_minimal_config(self) -> None:
        """A config with only policy_path is valid."""
        cfg = WatchConfig(policy_path="/some/path.yaml")
        assert cfg.policy_path == "/some/path.yaml"
        assert cfg.once is False
        assert cfg.json_output is False

    def test_extra_fields_rejected(self) -> None:
        """extra='forbid' means unknown fields raise ValidationError."""
        with pytest.raises(ValidationError):
            WatchConfig(policy_path="/p.yaml", unknown=True)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# The watch engine event loop
# ---------------------------------------------------------------------------


class TestWatchEngineEventLoop:
    def test_load_policy_fails(self) -> None:
        """A non-existent policy path raises WatchPolicyLoadError."""
        engine = _make_engine(policy_path="/nonexistent/policy.yaml")
        with pytest.raises(WatchPolicyLoadError):
            engine._load_policy()

    def test_run_with_invalid_event_without_once(self) -> None:
        """Invalid events are logged but don't crash the loop when --once is off."""
        engine = _make_engine()
        engine._load_policy()
        engine._store, _ = _sentinel_store()
        # Directly assign a list for stdin iteration
        engine._stdin = ["not json\n", "{}invalid\n"]
        code = engine.run()
        assert code == 0

    def test_run_with_invalid_event_with_once(self) -> None:
        """Invalid events raise WatchTransportError when --once is set."""
        config = WatchConfig(policy_path=DEFAULT_POLICY, once=True)
        engine = WatchEngine(config)
        engine._stdin = ["not json\n"]
        code = engine.run()
        assert code == 1

    def test_unsupported_schema_version_skipped(self) -> None:
        """Events with an unsupported schema_version are skipped."""
        engine = _make_engine()
        engine._load_policy()
        engine._store, _ = _sentinel_store()
        event = _make_event("task_started", schema_version="0.0")
        engine._stdin = [json.dumps(event) + "\n"]
        code = engine.run()
# ---------------------------------------------------------------------------
# _handle_task_started
# ---------------------------------------------------------------------------


class TestHandleTaskStarted:
    def test_creates_new_run(self, caplog: pytest.LogCaptureFixture) -> None:
        """A task_started event creates a new run via RunService."""
        caplog.set_level(logging.INFO)
        engine = _make_engine()
        engine._load_policy()
        engine._store, _ = _sentinel_store()
        with patch.object(RunService, "start") as mock_start:
            mock_start.return_value = RunStartResponse(
                run_id="test-run-001", task="test", started_at="...",
                status="started", schema_version="1.0",
            )
            event = parse_watch_event(_make_event("task_started"))
            engine._handle_task_started(event)  # type: ignore[arg-type]

        assert "test-task-001" in engine._tasks
        state = engine._tasks["test-task-001"]
        assert state.run_id == "test-run-001"
        assert state.goal == "Test the boundary system"

    def test_reopens_existing_run(self) -> None:
        """A second task_started for the same task re-opens the existing run."""
        engine = _make_engine()
        engine._load_policy()
        engine._store, _ = _sentinel_store()
        engine._tasks["test-task-001"] = _TaskState(
            task_id="test-task-001", run_id="existing-run",
        )
        with patch.object(RunService, "start") as mock_start:
            event = parse_watch_event(_make_event("task_started", goal="Updated goal"))
            engine._handle_task_started(event)  # type: ignore[arg-type]
            mock_start.assert_not_called()
        assert engine._tasks["test-task-001"].goal == "Updated goal"

    def test_handles_start_exception(self) -> None:
        """When RunService.start raises, the error is logged."""
        engine = _make_engine()
        engine._load_policy()
        engine._store, _ = _sentinel_store()
        with patch.object(RunService, "start", side_effect=RuntimeError("boom")):
            event = parse_watch_event(_make_event("task_started"))
            engine._handle_task_started(event)  # type: ignore[arg-type]
        assert "test-task-001" not in engine._tasks


# ---------------------------------------------------------------------------
# _handle_step_completed
# ---------------------------------------------------------------------------


class TestHandleStepCompleted:
    def test_requires_active_run(self, caplog: pytest.LogCaptureFixture) -> None:
        """A step_completed with no active task is an error, not a crash."""
        caplog.set_level(logging.ERROR)
        engine = _make_engine()
        engine._load_policy()
        event = parse_watch_event(_make_event("step_completed"))
        engine._handle_step_completed(event)  # type: ignore[arg-type]
        assert "no active run" in caplog.text

    def test_evaluates_and_emits_decision(self) -> None:
        """A step_completed event triggers boundary evaluation and outcome recording."""
        engine = _make_engine()
        engine._load_policy()
        engine._store, _ = _sentinel_store()
        state = _TaskState(task_id="test-task-001", run_id="test-run-001", goal="test goal")
        engine._tasks["test-task-001"] = state

        mock_result = MagicMock(spec=EvaluationResult)
        mock_result.decision = "ACCEPT"
        mock_result.score = 0.85
        mock_result.threshold = 0.6
        type(mock_result).assurance = PropertyMock(return_value="verified")

        mock_response = MagicMock(spec=BoundaryEvaluateResponse)
        mock_response.result = mock_result
        mock_response.next_action = "continue"
        mock_response.feedback = "All good"

        with (
            patch.object(BoundaryService, "evaluate", return_value=mock_response) as mock_eval,
            patch.object(OutcomeService, "record") as mock_outcome,
        ):
            event = parse_watch_event(_make_event("step_completed", sequence=1))
            engine._handle_step_completed(event)  # type: ignore[arg-type]

        mock_eval.assert_called_once()
        mock_outcome.assert_called_once()
        args: OutcomeRecordRequest = mock_outcome.call_args[0][0]
        assert args.run_id == "test-run-001"
        assert args.decision == "ACCEPT"

    def test_handles_evaluation_error(self, caplog: pytest.LogCaptureFixture) -> None:
        """When BoundaryService.evaluate raises, the error is logged and the daemon survives."""
        caplog.set_level(logging.ERROR)
        engine = _make_engine()
        engine._load_policy()
        engine._store, _ = _sentinel_store()
        state = _TaskState(task_id="test-task-001", run_id="test-run-001", goal="test")
        engine._tasks["test-task-001"] = state

        with patch.object(BoundaryService, "evaluate", side_effect=RuntimeError("eval boom")):
            event = parse_watch_event(_make_event("step_completed", sequence=1))
            # The handler must log the error and return — never propagate.
            engine._handle_step_completed(event)  # type: ignore[arg-type]

        # Error was logged with the exception detail.
        assert "unexpected error during evaluation" in caplog.text
        assert "eval boom" in caplog.text
        # Daemon survives: the task is still tracked and not marked finished.
        assert "test-task-001" in engine._tasks
        assert state.finished is False
# ---------------------------------------------------------------------------
# _handle_verification_requested
# ---------------------------------------------------------------------------


class TestHandleVerificationRequested:
    def test_requires_active_run(self, caplog: pytest.LogCaptureFixture) -> None:
        """A verification_requested with no active task is an error."""
        caplog.set_level(logging.ERROR)
        engine = _make_engine()
        engine._load_policy()
        event = parse_watch_event(_make_event("verification_requested"))
        engine._handle_verification_requested(event)  # type: ignore[arg-type]
        assert "no active run" in caplog.text

    def test_evaluates_and_emits_decision(self) -> None:
        """verification_requested triggers boundary evaluation."""
        engine = _make_engine()
        engine._load_policy()
        engine._store, _ = _sentinel_store()
        state = _TaskState(task_id="test-task-001", run_id="test-run-001", goal="test")
        engine._tasks["test-task-001"] = state

        mock_result = MagicMock(spec=EvaluationResult)
        mock_result.decision = "RETRY"
        mock_result.score = 0.45
        mock_result.threshold = 0.6
        type(mock_result).assurance = PropertyMock(return_value="standard")

        mock_response = MagicMock(spec=BoundaryEvaluateResponse)
        mock_response.result = mock_result
        mock_response.next_action = "retry"
        mock_response.feedback = "Needs retry"

        with (
            patch.object(BoundaryService, "evaluate", return_value=mock_response),
            patch.object(OutcomeService, "record") as mock_outcome,
        ):
            event = parse_watch_event(_make_event("verification_requested"))
            engine._handle_verification_requested(event)  # type: ignore[arg-type]

        mock_outcome.assert_called_once()
        args: OutcomeRecordRequest = mock_outcome.call_args[0][0]
        assert args.decision == "RETRY"


# ---------------------------------------------------------------------------
# _handle_control_action_reported
# ---------------------------------------------------------------------------


class TestHandleControlActionReported:
    def test_requires_active_run(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.ERROR)
        engine = _make_engine()
        engine._load_policy()
        event = parse_watch_event(_make_event("control_action_reported"))
        engine._handle_control_action_reported(event)  # type: ignore[arg-type]
        assert "no active run" in caplog.text

    def test_records_outcome(self) -> None:
        """The reported action is recorded as an outcome."""
        engine = _make_engine()
        engine._load_policy()
        engine._store, _ = _sentinel_store()
        state = _TaskState(task_id="test-task-001", run_id="test-run-001")
        engine._tasks["test-task-001"] = state

        with patch.object(OutcomeService, "record") as mock_outcome:
            event = parse_watch_event(_make_event("control_action_reported",
                                                    action="retry", note="fixing bug"))
            engine._handle_control_action_reported(event)  # type: ignore[arg-type]

        mock_outcome.assert_called_once()
        args: OutcomeRecordRequest = mock_outcome.call_args[0][0]
        assert args.run_id == "test-run-001"
        assert args.decision == "RETRY"
        assert args.next_action == "retry"

    def test_handles_exception(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.ERROR)
        engine = _make_engine()
        engine._load_policy()
        engine._store, _ = _sentinel_store()
        state = _TaskState(task_id="test-task-001", run_id="test-run-001")
        engine._tasks["test-task-001"] = state

        with patch.object(OutcomeService, "record", side_effect=RuntimeError("boom")):
            event = parse_watch_event(_make_event("control_action_reported"))
            engine._handle_control_action_reported(event)  # type: ignore[arg-type]
        assert "failed to record" in caplog.text


# ---------------------------------------------------------------------------
# _handle_control_action_observed
# ---------------------------------------------------------------------------


class TestHandleControlActionObserved:
    def test_requires_active_run(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.ERROR)
        engine = _make_engine()
        engine._load_policy()
        event = parse_watch_event(_make_event("control_action_observed"))
        engine._handle_control_action_observed(event)  # type: ignore[arg-type]
        assert "no active run" in caplog.text

    def test_records_observation(self) -> None:
        """The observed action is recorded with a note about matches_intended."""
        engine = _make_engine()
        engine._load_policy()
        engine._store, _ = _sentinel_store()
        state = _TaskState(task_id="test-task-001", run_id="test-run-001")
        engine._tasks["test-task-001"] = state

        with patch.object(OutcomeService, "record") as mock_outcome:
            event = parse_watch_event(_make_event("control_action_observed",
                                                    matches_intended=False,
                                                    note="agent deviated"))
            engine._handle_control_action_observed(event)  # type: ignore[arg-type]

        mock_outcome.assert_called_once()
        args: OutcomeRecordRequest = mock_outcome.call_args[0][0]
        assert args.run_id == "test-run-001"
        assert args.step_id == "PHASE-001"
        assert args.evaluation_id == "eval-001"
        assert args.decision == "CONTINUE"
        assert args.next_action == "continue"
        # The note records both the match status and the agent's own note.
        assert "matches=False" in args.note
        assert "agent deviated" in args.note
# ---------------------------------------------------------------------------
# _handle_task_finished
# ---------------------------------------------------------------------------


class TestHandleTaskFinished:
    def test_requires_active_run(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.ERROR)
        engine = _make_engine()
        engine._load_policy()
        event = parse_watch_event(_make_event("task_finished"))
        engine._handle_task_finished(event)  # type: ignore[arg-type]
        assert "no active run" in caplog.text

    def test_finalizes_run(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO)
        engine = _make_engine()
        engine._load_policy()
        engine._store, _ = _sentinel_store()
        state = _TaskState(task_id="test-task-001", run_id="test-run-001")
        engine._tasks["test-task-001"] = state

        with patch.object(RunService, "finish") as mock_finish:
            mock_finish.return_value = RunFinishResponse(
                run_id="test-run-001", status="completed", finished_at="...",
            )
            event = parse_watch_event(_make_event("task_finished", outcome="completed"))
            engine._handle_task_finished(event)  # type: ignore[arg-type]

        mock_finish.assert_called_once()
        args: RunFinishRequest = mock_finish.call_args[0][0]
        assert args.run_id == "test-run-001"
        assert args.status == "completed"
        assert state.finished is True

    def test_handles_exception(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.ERROR)
        engine = _make_engine()
        engine._load_policy()
        engine._store, _ = _sentinel_store()
        state = _TaskState(task_id="test-task-001", run_id="test-run-001")
        engine._tasks["test-task-001"] = state

        with patch.object(RunService, "finish", side_effect=RuntimeError("boom")):
            event = parse_watch_event(_make_event("task_finished"))
            engine._handle_task_finished(event)  # type: ignore[arg-type]
        assert "failed to finish" in caplog.text
        assert state.finished is False


# ---------------------------------------------------------------------------
# _emit_decision_event
# ---------------------------------------------------------------------------


class TestEmitDecisionEvent:
    def test_json_output(self) -> None:
        """When json_output is enabled, the decision is written to stdout as JSON."""
        engine = _make_engine(json_output=True)
        engine._load_policy()
        engine._store, _ = _sentinel_store()
        state = _TaskState(task_id="test-task-001", run_id="test-run-001")

        mock_result = MagicMock(spec=EvaluationResult)
        mock_result.decision = "ACCEPT"
        mock_result.score = 0.9
        mock_result.threshold = 0.6

        mock_response = MagicMock(spec=BoundaryEvaluateResponse)
        mock_response.result = mock_result
        mock_response.next_action = "continue"
        mock_response.feedback = "Looks good!"

        event = parse_watch_event(_make_event("step_completed"))
        engine._emit_decision_event(event, state, "eval-001", mock_response)

        engine._stdout.write.assert_called_once()
        written = engine._stdout.write.call_args[0][0]
        payload = json.loads(written)
        assert payload["event"] == "decision_emitted"
        assert payload["decision"] == "ACCEPT"
        assert payload["score"] == 0.9
        assert payload["run_id"] == "test-run-001"


# ---------------------------------------------------------------------------
# _build_criteria
# ---------------------------------------------------------------------------


class TestBuildCriteria:
    def test_returns_defaults(self) -> None:
        """_build_criteria returns a BoundCriteria with sensible defaults."""
        engine = _make_engine()
        engine._load_policy()
        criteria = engine._build_criteria()
        assert isinstance(criteria, BoundCriteria)
        assert criteria.threshold == 0.6
        assert criteria.retry_margin == 0.1
        assert criteria.weights.acceptance == 1.0


# ---------------------------------------------------------------------------
# Full event loop integration
# ---------------------------------------------------------------------------


class TestFullEventLoop:
    def test_full_task_lifecycle(self) -> None:
        """A complete task_started -> step_completed -> task_finished sequence.

        Feeds a full JSONL event stream through the engine via a fake stdin,
        then asserts a run was started, a control decision was emitted to
        stdout, and the run was finished.  This is the only test that
        actually exercises the top-level :meth:`WatchEngine.run` happy path
        end-to-end.
        """
        engine = _make_engine(once=True, json_output=True)
        engine._load_policy()
        engine._store, _ = _sentinel_store()

        mock_result = MagicMock(spec=EvaluationResult)
        mock_result.decision = "ACCEPT"
        mock_result.score = 0.85
        mock_result.threshold = 0.6
        type(mock_result).assurance = PropertyMock(return_value="verified")

        mock_response = MagicMock(spec=BoundaryEvaluateResponse)
        mock_response.result = mock_result
        mock_response.next_action = "continue"
        mock_response.feedback = "All good"

        events = [
            json.dumps(_make_event("task_started", sequence=1)),
            json.dumps(_make_event("step_completed", step_id="PHASE-001", sequence=2)),
            json.dumps(_make_event("task_finished", outcome="completed", sequence=3)),
        ]
        engine._stdin = io.StringIO("\n".join(events) + "\n")
        captured = io.StringIO()
        engine._stdout = captured

        with (
            patch.object(RunService, "start") as mock_start,
            patch.object(BoundaryService, "evaluate", return_value=mock_response) as mock_eval,
            patch.object(OutcomeService, "record") as mock_record,
            patch.object(RunService, "finish") as mock_finish,
        ):
            mock_start.return_value = RunStartResponse(
                run_id="test-run-001", task="test", started_at="...",
                status="started", schema_version="1.0",
            )
            mock_finish.return_value = RunFinishResponse(
                run_id="test-run-001", status="completed", finished_at="...",
            )
            code = engine.run()

        # Engine exited cleanly after the task_finished (because --once).
        assert code == 0

        # A run was started for the task.
        mock_start.assert_called_once()
        start_args = mock_start.call_args[0][0]
        assert start_args.task  # goal propagated

        # The step was evaluated against the boundary service.
        mock_eval.assert_called_once()
        mock_record.assert_called_once()
        record_args: OutcomeRecordRequest = mock_record.call_args[0][0]
        assert record_args.run_id == "test-run-001"
        assert record_args.decision == "ACCEPT"

        # A decision_emitted event was written to stdout as JSON.
        out_lines = [ln for ln in captured.getvalue().splitlines() if ln.strip()]
        decisions = [json.loads(ln) for ln in out_lines
                     if json.loads(ln).get("event") == "decision_emitted"]
        assert len(decisions) == 1
        assert decisions[0]["decision"] == "ACCEPT"
        assert decisions[0]["run_id"] == "test-run-001"

        # The run was finished with the reported outcome.
        mock_finish.assert_called_once()
        finish_args: RunFinishRequest = mock_finish.call_args[0][0]
        assert finish_args.run_id == "test-run-001"
        assert finish_args.status == "completed"

        # The engine tracked the task and marked it finished.
        state = engine._tasks["test-task-001"]
        assert state.run_id == "test-run-001"
        assert state.finished is True

    def test_sigterm_triggers_flush_of_incomplete_tasks(self) -> None:
        """A WatchShutdown (SIGTERM) flushes started-but-unfinished runs.

        Simulates the container-stop signal by having the fake stdin raise
        :class:`WatchShutdown` mid-stream; the run loop must catch it and
        call ``RunService.finish`` with ``status='interrupted'`` for every
        active run before returning ``0``.
        """
        engine = _make_engine()
        engine._load_policy()
        engine._store, _ = _sentinel_store()
        # One active, unfinished run in flight when the signal arrives.
        engine._tasks["test-task-001"] = _TaskState(
            task_id="test-task-001", run_id="test-run-001", goal="test",
        )

        class _ShutdownStdin:
            """Yields one valid line, then raises WatchShutdown on next read."""

            def __iter__(self):
                yield json.dumps(_make_event("task_started", sequence=1)) + "\n"
                raise WatchShutdown()

        engine._stdin = _ShutdownStdin()

        with patch.object(RunService, "finish") as mock_finish:
            mock_finish.return_value = RunFinishResponse(
                run_id="test-run-001", status="interrupted", finished_at="...",
            )
            code = engine.run()

        assert code == 0
        mock_finish.assert_called_once()
        finish_args: RunFinishRequest = mock_finish.call_args[0][0]
        assert finish_args.run_id == "test-run-001"
        assert finish_args.status == "interrupted"
# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


class TestWatchCliCommand:
    def test_parser_has_watch_subcommand(self) -> None:
        """The CLI parser includes a 'watch' subcommand."""
        from bound.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["watch", "--policy", "/path/to/policy.yaml"])
        assert args.command == "watch"
        assert args.policy == "/path/to/policy.yaml"
        assert args.once is False
        assert args.json_output is False

    def test_parser_requires_policy(self) -> None:
        """The --policy flag is required for the watch subcommand."""
        from bound.cli import _build_parser

        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["watch"])

    def test_parser_accepts_flags(self) -> None:
        """The --once and --json flags are accepted."""
        from bound.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["watch", "--policy", "/p.yaml", "--once", "--json"])
        assert args.once is True
        assert args.json_output is True

    def test_run_watch_delegates_to_engine(self) -> None:
        """_run_watch creates a WatchEngine and calls run()."""
        from bound.cli import _build_parser, _run_watch

        parser = _build_parser()
        args = parser.parse_args(["watch", "--policy", DEFAULT_POLICY, "--once"])
        with patch("bound.watch.WatchEngine") as MockEngine:
            instance = MockEngine.return_value
            instance.run.return_value = 0
            code = _run_watch(args)
        assert code == 0
        MockEngine.assert_called_once()
        config = MockEngine.call_args[0][0]
        assert config.policy_path == DEFAULT_POLICY
        assert config.once is True