"""Tests for the BoundRuntime public API (v0.9.0).

Verifies that:
1. BoundRuntime.from_policy() validates and loads a policy file.
2. runtime.evaluate() produces deterministic EvaluationResult.
3. start_run / finish_run / record_outcome manage lineage correctly.
4. Error handling is clean and typed.
5. Re-exported models from __init__ are importable.
6. events.py parse_bound_event() correctly discriminates events.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bound.events import (
    PUBLIC_EVENT_SCHEMA_VERSION,
    ActionReportedEvent,
    DecisionGatedEvent,
    EvaluationRecordedEvent,
    EvidenceCollectedEvent,
    EvidenceCollectionFailedEvent,
    OutcomeRecordedEvent,
    RunFinishedEvent,
    RunStartedEvent,
    StepStartedEvent,
    parse_bound_event,
)
from bound.models import (
    BoundCriteria,
    EvaluationResult,
    EvaluationScores,
)
from bound.runtime import (
    BoundRuntime,
    EvaluationContext,
    FinishRunResult,
    OutcomeRecordContext,
    OutcomeResult,
    RunHandle,
)
from bound.services import PolicyLoadError, PolicyValidationError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_policy_file(tmp_path: Path) -> Path:
    """Create a minimal valid bound-policy.yaml for testing."""
    policy = {
        "policy": {"id": "test", "version": "0.1.0"},
        "acceptance_checks": [],
        "quality_checks": [],
        "risk_checks": [],
        "budgets": {},
        "change_scope": {},
        "approvals": {},
    }
    policy_path = tmp_path / "bound-policy.yaml"
    policy_path.write_text(json.dumps(policy))
    return policy_path


# ---------------------------------------------------------------------------
# BoundRuntime.from_policy
# ---------------------------------------------------------------------------


class TestFromPolicy:
    """Tests for BoundRuntime.from_policy()."""

    def test_creates_runtime_from_valid_policy(self, tmp_policy_file: Path) -> None:
        """A valid policy file should create a BoundRuntime without errors."""
        runtime = BoundRuntime.from_policy(str(tmp_policy_file))
        assert isinstance(runtime, BoundRuntime)
        assert runtime.policy_path == tmp_policy_file.resolve()

    def test_default_project_root_is_policy_parent(self, tmp_policy_file: Path) -> None:
        """When project_root is omitted, it defaults to the policy's directory."""
        runtime = BoundRuntime.from_policy(str(tmp_policy_file))
        assert runtime.project_root == tmp_policy_file.parent.resolve()

    def test_explicit_project_root(self, tmp_policy_file: Path, tmp_path: Path) -> None:
        """Explicit project_root should override the default."""
        custom_root = tmp_path / "custom_project"
        custom_root.mkdir()
        runtime = BoundRuntime.from_policy(
            str(tmp_policy_file),
            project_root=str(custom_root),
        )
        assert runtime.project_root == custom_root.resolve()

    def test_lineage_enabled_default_true(self, tmp_policy_file: Path) -> None:
        """Lineage should be enabled by default."""
        runtime = BoundRuntime.from_policy(str(tmp_policy_file))
        assert runtime.lineage_enabled is True

    def test_lineage_disabled(self, tmp_policy_file: Path) -> None:
        """lineage_enabled=False should disable lineage."""
        runtime = BoundRuntime.from_policy(
            str(tmp_policy_file),
            lineage_enabled=False,
        )
        assert runtime.lineage_enabled is False

    def test_raises_policy_load_error_for_missing_file(self) -> None:
        """A non-existent policy path should raise PolicyLoadError."""
        with pytest.raises(PolicyLoadError, match="not found"):
            BoundRuntime.from_policy("/nonexistent/policy.yaml")

    def test_raises_policy_load_error_for_directory(self, tmp_path: Path) -> None:
        """A directory instead of a file should raise PolicyLoadError."""
        with pytest.raises(PolicyLoadError, match="not a file"):
            BoundRuntime.from_policy(str(tmp_path))

    def test_raises_policy_validation_error_for_invalid_yaml(
        self,
        tmp_path: Path,
    ) -> None:
        """A YAML file without required fields should fail validation."""
        invalid = tmp_path / "invalid-policy.yaml"
        invalid.write_text("just: some yaml\n")
        with pytest.raises(PolicyValidationError):
            BoundRuntime.from_policy(str(invalid))


# ---------------------------------------------------------------------------
# BoundRuntime properties
# ---------------------------------------------------------------------------


class TestProperties:
    """Tests for BoundRuntime property accessors."""

    def test_current_run_id_none_initially(self, tmp_policy_file: Path) -> None:
        """current_run_id should be None before start_run is called."""
        runtime = BoundRuntime.from_policy(str(tmp_policy_file))
        assert runtime.current_run_id is None


# ---------------------------------------------------------------------------
# EvaluationContext
# ---------------------------------------------------------------------------


class TestEvaluationContext:
    """Tests for the EvaluationContext model."""

    def test_minimal_context_valid(self) -> None:
        """Minimal required fields should produce a valid context."""
        ctx = EvaluationContext(
            task_id="task-1",
            step_id="step-1",
            action="Do something",
        )
        assert ctx.task_id == "task-1"
        assert ctx.attempt == 1  # default

    def test_default_attempt_is_one(self) -> None:
        """attempt should default to 1."""
        ctx = EvaluationContext(
            task_id="t",
            step_id="s",
            action="a",
        )
        assert ctx.attempt == 1

    def test_rejects_empty_task_id(self) -> None:
        """Empty task_id should be rejected."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            EvaluationContext(task_id="", step_id="s", action="a")

    def test_rejects_empty_action(self) -> None:
        """Empty action should be rejected."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            EvaluationContext(task_id="t", step_id="s", action="")

    def test_rejects_attempt_zero(self) -> None:
        """attempt=0 should be rejected (must be >= 1)."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            EvaluationContext(task_id="t", step_id="s", action="a", attempt=0)


# ---------------------------------------------------------------------------
# BoundRuntime.evaluate
# ---------------------------------------------------------------------------


class TestEvaluate:
    """Tests for BoundRuntime.evaluate()."""

    def test_evaluate_returns_evaluation_result(
        self,
        tmp_policy_file: Path,
    ) -> None:
        """evaluate() should return an EvaluationResult."""
        runtime = BoundRuntime.from_policy(
            str(tmp_policy_file),
            lineage_enabled=False,
        )
        ctx = EvaluationContext(
            task_id="task-001",
            step_id="PHASE-001",
            attempt=1,
            action="Implement input validation",
        )
        result = runtime.evaluate(ctx)
        assert isinstance(result, EvaluationResult)
        assert result.scores is not None
        assert isinstance(result.score, float)
        assert result.decision in ("ACCEPT", "RETRY", "REPLAN", "ROLLBACK")

    def test_evaluate_with_explicit_scores(
        self,
        tmp_policy_file: Path,
    ) -> None:
        """Supplying explicit scores should use those scores."""
        runtime = BoundRuntime.from_policy(
            str(tmp_policy_file),
            lineage_enabled=False,
        )
        scores = EvaluationScores(
            acceptance=0.9,
            influence=0.1,
            risk=0.0,
            cost=0.0,
        )
        ctx = EvaluationContext(
            task_id="task-001",
            step_id="step-001",
            action="Test",
            scores=scores,
        )
        result = runtime.evaluate(ctx)
        assert result.score > 0.5  # High acceptance should pass

    def test_evaluate_with_custom_criteria(
        self,
        tmp_policy_file: Path,
    ) -> None:
        """Custom BoundCriteria should affect the evaluation."""
        runtime = BoundRuntime.from_policy(
            str(tmp_policy_file),
            lineage_enabled=False,
        )
        scores = EvaluationScores(
            acceptance=0.3,
            influence=0.0,
            risk=0.0,
            cost=0.0,
        )
        ctx = EvaluationContext(
            task_id="task-001",
            step_id="step-001",
            action="Test",
            scores=scores,
            # Low threshold so even 0.3 passes
            criteria=BoundCriteria(threshold=0.2),
        )
        result = runtime.evaluate(ctx)
        assert result.decision == "ACCEPT"

    def test_evaluate_deterministic(
        self,
        tmp_policy_file: Path,
    ) -> None:
        """Identical inputs should produce identical results."""
        runtime = BoundRuntime.from_policy(
            str(tmp_policy_file),
            lineage_enabled=False,
        )
        ctx = EvaluationContext(
            task_id="task-001",
            step_id="step-001",
            action="Test",
            scores=EvaluationScores(
                acceptance=0.7,
                influence=0.0,
                risk=0.0,
                cost=0.0,
            ),
        )
        result1 = runtime.evaluate(ctx)
        result2 = runtime.evaluate(ctx)
        assert result1.score == result2.score
        assert result1.decision == result2.decision


# ---------------------------------------------------------------------------
# BoundRuntime run lifecycle (with lineage)
# ---------------------------------------------------------------------------


class TestRunLifecycle:
    """Tests for start_run / finish_run / record_outcome."""

    def test_start_run_returns_run_handle(
        self,
        tmp_policy_file: Path,
    ) -> None:
        """start_run should return a RunHandle with a valid run_id."""
        runtime = BoundRuntime.from_policy(str(tmp_policy_file))
        handle = runtime.start_run("Test task")
        assert isinstance(handle, RunHandle)
        assert handle.run_id
        assert handle.task == "Test task"
        assert handle.status == "started"
        # Clean up
        runtime.finish_run("completed")

    def test_start_run_sets_current_run_id(
        self,
        tmp_policy_file: Path,
    ) -> None:
        """After start_run, current_run_id should be set."""
        runtime = BoundRuntime.from_policy(str(tmp_policy_file))
        runtime.start_run("task")
        assert runtime.current_run_id is not None
        runtime.finish_run("completed")
        assert runtime.current_run_id is None

    def test_finish_run_returns_finish_result(
        self,
        tmp_policy_file: Path,
    ) -> None:
        """finish_run should return a FinishRunResult."""
        runtime = BoundRuntime.from_policy(str(tmp_policy_file))
        runtime.start_run("task")
        result = runtime.finish_run("completed", note="All done")
        assert isinstance(result, FinishRunResult)
        assert result.status == "completed"
        assert result.finished_at

    def test_finish_run_without_start_raises(
        self,
        tmp_policy_file: Path,
    ) -> None:
        """finish_run without start_run should raise RunNotFoundError."""
        from bound.services import RunNotFoundError

        runtime = BoundRuntime.from_policy(str(tmp_policy_file))
        with pytest.raises(RunNotFoundError, match="No active run"):
            runtime.finish_run("completed")

    def test_start_run_lineage_disabled_raises(
        self,
        tmp_policy_file: Path,
    ) -> None:
        """start_run with lineage_enabled=False should raise ServiceError."""
        from bound.services import ServiceError

        runtime = BoundRuntime.from_policy(
            str(tmp_policy_file),
            lineage_enabled=False,
        )
        with pytest.raises(ServiceError, match="Lineage recording is disabled"):
            runtime.start_run("task")

    def test_record_outcome_lineage_disabled_raises(
        self,
        tmp_policy_file: Path,
    ) -> None:
        """record_outcome with lineage_enabled=False should raise ServiceError."""
        from bound.services import ServiceError

        runtime = BoundRuntime.from_policy(
            str(tmp_policy_file),
            lineage_enabled=False,
        )
        ctx = OutcomeRecordContext(
            run_id="r",
            step_id="s",
            evaluation_id="e",
            decision="ACCEPT",
        )
        with pytest.raises(ServiceError, match="Lineage recording is disabled"):
            runtime.record_outcome(ctx)

    def test_evaluate_with_active_run(
        self,
        tmp_policy_file: Path,
    ) -> None:
        """evaluate() when a run is active should record lineage."""
        runtime = BoundRuntime.from_policy(str(tmp_policy_file))
        runtime.start_run("Test task with lineage")
        ctx = EvaluationContext(
            task_id="task-001",
            step_id="step-001",
            action="Test action",
        )
        result = runtime.evaluate(ctx)
        assert isinstance(result, EvaluationResult)
        runtime.finish_run("completed")


# ---------------------------------------------------------------------------
# events.py — parse_bound_event
# ---------------------------------------------------------------------------


class TestParseBoundEvent:
    """Tests for parse_bound_event() and BoundEvent discriminated union."""

    def test_parse_run_started(self) -> None:
        """parse_bound_event should recognise run_started."""
        data = {
            "event": "run_started",
            "run_id": "r-001",
            "task": "Test task",
            "timestamp": "2025-01-01T00:00:00Z",
        }
        event = parse_bound_event(data)
        assert isinstance(event, RunStartedEvent)
        assert event.run_id == "r-001"
        assert event.task == "Test task"

    def test_parse_run_finished(self) -> None:
        """parse_bound_event should recognise run_finished."""
        data = {
            "event": "run_finished",
            "run_id": "r-001",
            "status": "completed",
            "timestamp": "2025-01-01T00:00:00Z",
        }
        event = parse_bound_event(data)
        assert isinstance(event, RunFinishedEvent)
        assert event.status == "completed"

    def test_parse_step_started(self) -> None:
        """parse_bound_event should recognise step_started."""
        data = {
            "event": "step_started",
            "run_id": "r-001",
            "step_id": "s-001",
            "contract_id": "c-001",
            "timestamp": "2025-01-01T00:00:00Z",
        }
        event = parse_bound_event(data)
        assert isinstance(event, StepStartedEvent)
        assert event.contract_id == "c-001"

    def test_parse_evaluation_recorded(self) -> None:
        """parse_bound_event should recognise evaluation_recorded."""
        data = {
            "event": "evaluation_recorded",
            "run_id": "r-001",
            "step_id": "s-001",
            "evaluation_id": "e-001",
            "score": 0.75,
            "threshold": 0.5,
            "decision": "ACCEPT",
            "reason_code": "ACCEPT",
            "timestamp": "2025-01-01T00:00:00Z",
        }
        event = parse_bound_event(data)
        assert isinstance(event, EvaluationRecordedEvent)
        assert event.score == 0.75
        assert event.decision == "ACCEPT"

    def test_parse_evidence_collected(self) -> None:
        """parse_bound_event should recognise evidence_collected."""
        data = {
            "event": "evidence_collected",
            "run_id": "r-001",
            "step_id": "s-001",
            "evaluation_id": "e-001",
            "check_id": "chk-001",
            "provenance": "verified",
            "passed": True,
            "timestamp": "2025-01-01T00:00:00Z",
        }
        event = parse_bound_event(data)
        assert isinstance(event, EvidenceCollectedEvent)
        assert event.passed is True

    def test_parse_outcome_recorded(self) -> None:
        """parse_bound_event should recognise outcome_recorded."""
        data = {
            "event": "outcome_recorded",
            "run_id": "r-001",
            "step_id": "s-001",
            "evaluation_id": "e-001",
            "decision": "ACCEPT",
            "next_action": "continue",
            "reason_code": "ACCEPT",
            "timestamp": "2025-01-01T00:00:00Z",
        }
        event = parse_bound_event(data)
        assert isinstance(event, OutcomeRecordedEvent)
        assert event.next_action == "continue"

    def test_parse_action_reported(self) -> None:
        """parse_bound_event should recognise action_reported."""
        data = {
            "event": "action_reported",
            "run_id": "r-001",
            "step_id": "s-001",
            "evaluation_id": "e-001",
            "action": "deploy",
            "timestamp": "2025-01-01T00:00:00Z",
        }
        event = parse_bound_event(data)
        assert isinstance(event, ActionReportedEvent)
        assert event.action == "deploy"

    def test_parse_decision_gated(self) -> None:
        """parse_bound_event should recognise decision_gated."""
        data = {
            "event": "decision_gated",
            "run_id": "r-001",
            "step_id": "s-001",
            "evaluation_id": "e-001",
            "decision": "ACCEPT",
            "next_action": "continue",
            "score": 0.85,
            "threshold": 0.5,
            "assurance": "verified",
            "feedback": "All checks passed.",
            "timestamp": "2025-01-01T00:00:00Z",
        }
        event = parse_bound_event(data)
        assert isinstance(event, DecisionGatedEvent)
        assert event.assurance == "verified"

    def test_parse_evidence_collection_failed(self) -> None:
        """parse_bound_event should recognise evidence_collection_failed."""
        data = {
            "event": "evidence_collection_failed",
            "run_id": "r-001",
            "step_id": "s-001",
            "check_id": "chk-001",
            "error": "timeout",
            "timestamp": "2025-01-01T00:00:00Z",
        }
        event = parse_bound_event(data)
        assert isinstance(event, EvidenceCollectionFailedEvent)
        assert event.error == "timeout"

    def test_unknown_event_tag_rejected(self) -> None:
        """An unrecognised event tag should raise ValidationError."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            parse_bound_event({"event": "nonexistent"})

    def test_parse_from_json_string(self) -> None:
        """parse_bound_event should accept JSON strings."""
        json_str = json.dumps(
            {
                "event": "run_started",
                "run_id": "r-001",
                "task": "Test",
                "timestamp": "2025-01-01T00:00:00Z",
            }
        )
        event = parse_bound_event(json_str)
        assert isinstance(event, RunStartedEvent)

    def test_parse_from_bytes(self) -> None:
        """parse_bound_event should accept bytes."""
        json_bytes = json.dumps(
            {
                "event": "run_started",
                "run_id": "r-001",
                "task": "Test",
                "timestamp": "2025-01-01T00:00:00Z",
            }
        ).encode()
        event = parse_bound_event(json_bytes)
        assert isinstance(event, RunStartedEvent)

    def test_extra_fields_rejected(self) -> None:
        """Extra fields should cause validation failure (extra='forbid')."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            parse_bound_event(
                {
                    "event": "run_started",
                    "run_id": "r-001",
                    "task": "Test",
                    "timestamp": "2025-01-01T00:00:00Z",
                    "extra_field": "should fail",
                }
            )

    def test_missing_required_field_rejected(self) -> None:
        """A missing required field should raise ValidationError."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            parse_bound_event({"event": "run_started", "run_id": "r-001"})


# ---------------------------------------------------------------------------
# events.py — schema version
# ---------------------------------------------------------------------------


class TestEventSchemaVersion:
    """Tests for the public event schema version."""

    def test_schema_version_is_string(self) -> None:
        """PUBLIC_EVENT_SCHEMA_VERSION should be a string."""
        assert isinstance(PUBLIC_EVENT_SCHEMA_VERSION, str)

    def test_every_event_has_default_schema_version(self) -> None:
        """Every event type should carry the default schema_version."""
        data = {
            "event": "run_started",
            "run_id": "r",
            "task": "T",
            "timestamp": "2025-01-01T00:00:00Z",
        }
        event = parse_bound_event(data)
        assert event.schema_version == PUBLIC_EVENT_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Re-export verification from __init__
# ---------------------------------------------------------------------------


class TestInitExports:
    """Verify that runtime types are re-exported from bound.__init__."""

    def test_bound_runtime_importable(self) -> None:
        """BoundRuntime should be importable from bound."""
        import bound

        assert hasattr(bound, "BoundRuntime")
        assert bound.BoundRuntime is BoundRuntime

    def test_evaluation_context_importable(self) -> None:
        """EvaluationContext should be importable from bound."""
        import bound

        assert hasattr(bound, "EvaluationContext")
        assert bound.EvaluationContext is EvaluationContext

    def test_run_handle_importable(self) -> None:
        """RunHandle should be importable from bound."""
        import bound

        assert hasattr(bound, "RunHandle")
        assert bound.RunHandle is RunHandle


# ---------------------------------------------------------------------------
# OutcomeRecordContext + OutcomeResult model tests
# ---------------------------------------------------------------------------


class TestOutcomeModels:
    """Tests for OutcomeRecordContext and OutcomeResult models."""

    def test_outcome_context_valid(self) -> None:
        """OutcomeRecordContext with minimal fields should be valid."""
        ctx = OutcomeRecordContext(
            run_id="r",
            step_id="s",
            evaluation_id="e",
            decision="ACCEPT",
        )
        assert ctx.run_id == "r"
        assert ctx.next_action is None

    def test_outcome_result_valid(self) -> None:
        """OutcomeResult should hold all fields."""
        result = OutcomeResult(
            run_id="r",
            step_id="s",
            evaluation_id="e",
            decision="ACCEPT",
            next_action="continue",
            reason_code="ACCEPT",
        )
        assert result.decision == "ACCEPT"
        assert result.next_action == "continue"


# ---------------------------------------------------------------------------
# RunHandle model tests
# ---------------------------------------------------------------------------


class TestRunHandleModel:
    """Tests for RunHandle model."""

    def test_run_handle_frozen(self) -> None:
        """RunHandle should be frozen (immutable)."""
        handle = RunHandle(
            run_id="r",
            task="t",
            started_at="ts",
            status="s",
            schema_version="v",
        )
        from pydantic import ValidationError as PydanticValidationError

        with pytest.raises(PydanticValidationError, match="frozen"):
            handle.run_id = "new"  # type: ignore[misc]


class TestFinishRunResultModel:
    """Tests for FinishRunResult model."""

    def test_finish_result_holds_data(self) -> None:
        """FinishRunResult should hold run_id, status, finished_at."""
        fr = FinishRunResult(
            run_id="r",
            status="completed",
            finished_at="2025-01-01T00:00:00Z",
        )
        assert fr.run_id == "r"
        assert fr.status == "completed"
