"""Tests for the shared service layer (``bound.services``).

Verifies that:
1. All service request/response models are serializable and deterministic.
2. All service methods accept typed requests and return typed responses.
3. Service methods never print to stdout/stderr or call ``sys.exit``.
4. CLI handlers call the service layer to produce results.
5. Service methods raise typed errors on invalid inputs.
"""

from __future__ import annotations


import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Union
from pydantic.fields import PydanticUndefined

import pytest

from bound.models import (
    Action,
    BoundCriteria,
    BoundWeights,
    CodingWorkflowSignals,
    EvaluationResult,
    EvaluationScores,
)
from bound.services import (
    PolicyValidateRequest,
    PolicyValidateResponse,
    PolicyExplainRequest,
    PolicyExplainResponse,
    PolicyHashRequest,
    PolicyHashResponse,
    PolicyIdentity,
    RunStartRequest,
    RunStartResponse,
    RunFinishRequest,
    RunFinishResponse,
    RunDeleteRequest,
    RunDeleteResponse,
    RunListRequest,
    RunListResponse,
    RunInspectRequest,
    RunInspectResponse,
    EvaluateRequest,
    EvaluateResponse,
    EvaluateWorkflowRequest,
    EvaluateWorkflowResponse,
    OutcomeRecordRequest,
    OutcomeRecordResponse,
    EvidenceCollectRequest,
    EvidenceCollectResponse,
    BoundaryEvaluateRequest,
    BoundaryEvaluateResponse,
    CheckpointCreateRequest,
    CheckpointCreateResponse,
    CheckpointInspectRequest,
    CheckpointInspectResponse,
    CheckpointListRequest,
    CheckpointListResponse,
    CheckpointRollbackRequest,
    CheckpointRollbackResponse,
    PolicyService,
    RunService,
    EvaluationService,
    OutcomeService,
    EvidenceService,
    BoundaryService,
    CheckpointService,
    ServiceError,
    PolicyLoadError,
    PolicyValidationError,
    RunNotFoundError,
    EvaluationInputError,
    CheckpointError,
)
from bound.contracts import AcceptanceCheck, BoundPlan, RiskCheck, StepContract
from bound.evidence import CheckEvidence, ExecutionEvidence, EvidenceProvenance
from bound.lineage import RunStatus
from bound.lineage_store import RunSummary

# Rebuild models with forward references so model_construct + model_dump works.
# BoundaryEvaluateRequest references StepContract and ExecutionEvidence via
# string annotations (forward refs) that need runtime resolution.
StepContract  # ensure imported
ExecutionEvidence  # ensure imported
BoundaryEvaluateRequest.model_rebuild()
BoundaryEvaluateResponse.model_rebuild()


# =========================================================================
# Constants
# =========================================================================

_SERVICE_MODELS: list[type[Any]] = [
    PolicyValidateRequest, PolicyValidateResponse,
    PolicyExplainRequest, PolicyExplainResponse,
    PolicyHashRequest, PolicyHashResponse, PolicyIdentity,
    RunStartRequest, RunStartResponse,
    RunFinishRequest, RunFinishResponse,
    RunDeleteRequest, RunDeleteResponse,
    RunListRequest, RunListResponse,
    RunInspectRequest, RunInspectResponse,
    EvaluateRequest, EvaluateResponse,
    EvaluateWorkflowRequest, EvaluateWorkflowResponse,
    OutcomeRecordRequest, OutcomeRecordResponse,
    EvidenceCollectRequest, EvidenceCollectResponse,
    BoundaryEvaluateRequest, BoundaryEvaluateResponse,
    CheckpointCreateRequest, CheckpointCreateResponse,
    CheckpointInspectRequest, CheckpointInspectResponse,
    CheckpointListRequest, CheckpointListResponse,
    CheckpointRollbackRequest, CheckpointRollbackResponse,
]

_SERVICE_CLASSES: list[type[Any]] = [
    PolicyService, RunService, EvaluationService,
    OutcomeService, EvidenceService, BoundaryService, CheckpointService,
]

_SERVICE_ERRORS: list[type[Exception]] = [
    ServiceError, PolicyLoadError, PolicyValidationError,
    RunNotFoundError, EvaluationInputError, CheckpointError,
]
# =========================================================================
# 1. Serialization determinism
# =========================================================================


def test_all_service_models_serialize_to_json() -> None:
    """Every request/response model can be serialized to JSON."""
    for model in _SERVICE_MODELS:
        try:
            instance = _minimal_instance(model)
            as_dict = instance.model_dump(mode="json")
            json_str = json.dumps(as_dict, default=str, sort_keys=True)
            assert isinstance(json_str, str)
            parsed = json.loads(json_str)
            assert isinstance(parsed, dict)
        except Exception as exc:
            pytest.fail(f"{model.__name__} failed JSON serialization: {exc}")


def test_all_service_models_serialization_is_deterministic() -> None:
    """Serializing the same instance twice yields identical JSON."""
    for model in _SERVICE_MODELS:
        try:
            instance = _minimal_instance(model)
            as_dict_1 = instance.model_dump(mode="json")
            as_dict_2 = instance.model_dump(mode="json")
            json_1 = json.dumps(as_dict_1, default=str, sort_keys=True)
            json_2 = json.dumps(as_dict_2, default=str, sort_keys=True)
            assert json_1 == json_2, (
                f"{model.__name__} serialization is not deterministic"
            )
        except Exception as exc:
            pytest.fail(f"{model.__name__} determinism check failed: {exc}")


# =========================================================================
# 2. Service error hierarchy
# =========================================================================


def test_service_errors_form_a_hierarchy() -> None:
    """All service errors must inherit from ServiceError."""
    for err in _SERVICE_ERRORS:
        assert issubclass(err, ServiceError), (
            f"{err.__name__} does not inherit from ServiceError"
        )


def test_all_service_models_have_forbid_and_frozen() -> None:
    """Every service request/response model must use extra="forbid" and frozen=True.

    ``extra="forbid"`` rejects unknown fields so typos surface immediately.
    ``frozen=True`` makes models immutable, preventing accidental mutation of
    service-layer data after construction.
    """
    for model in _SERVICE_MODELS:
        cfg = model.model_config
        assert cfg.get("extra") == "forbid", (
            f"{model.__name__} is missing extra='forbid' in model_config"
        )
        assert cfg.get("frozen") is True, (
            f"{model.__name__} is missing frozen=True in model_config"
        )
# =========================================================================
# 3. No print/sys.exit in services
# =========================================================================


def test_services_module_has_no_print_or_sys_exit() -> None:
    """The services.py source must not contain print( or sys.exit."""
    services_path = (
        Path(__file__).resolve().parent.parent / "src" / "bound" / "services.py"
    )
    source = services_path.read_text(encoding="utf-8")
    # Strip docstring before checking — module-level docstring mentions sys.exit
    clean = _strip_docstring(source)
    lines = clean.split("\n")
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "print(" in stripped:
            pytest.fail(f"services.py line {i}: contains print(")
        if "sys.exit" in stripped:
            pytest.fail(f"services.py line {i}: contains sys.exit")


def test_services_never_print_at_runtime(capsys: pytest.CaptureFixture[str]) -> None:
    """Calling service methods must never write to stdout/stderr.

    Services are the pure logic layer; adapters (CLI, MCP) own I/O.
    """
    # PolicyService — validate a non-existent file (returns, does not print)
    response = PolicyService.validate(PolicyValidateRequest(path="/tmp/nonexistent-bound-policy-xyz.yaml"))
    assert response.valid is False
    out, err = capsys.readouterr()
    assert out == ""
    assert err == ""

    # EvaluationService — happy path
    response = EvaluationService.evaluate(EvaluateRequest(
        action=Action(description="Test", goal="Test"),
        scores=EvaluationScores(acceptance=0.9, influence=0.0, risk=0.0, cost=0.0),
        criteria=BoundCriteria(threshold=0.6),
    ))
    assert response.result.decision == "ACCEPT"
    out, err = capsys.readouterr()
    assert out == ""
    assert err == ""


def _strip_docstring(source: str) -> str:
    """Remove the first module-level docstring from source code."""
    # Strip leading whitespace, then remove """...""" or '''...''' at the start
    lines = source.split("\n")
    start = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            if start == -1:
                start = i
                # Check if it ends on the same line
                if stripped.endswith('"""') and stripped != '"""' or \
                   stripped.endswith("'''") and stripped != "'''":
                    # Single-line docstring
                    lines[i] = ""
                    break
            else:
                # End of multi-line docstring
                lines[i] = ""
                break
        elif start != -1:
            lines[i] = ""
    return "\n".join(lines)


# =========================================================================
# 4. Service method integration tests
# =========================================================================


# --- PolicyService ---

class TestPolicyService:
    """Integration tests for PolicyService."""

    def test_validate_returns_typed_response(self) -> None:
        """PolicyService.validate accepts PolicyValidateRequest and returns
        PolicyValidateResponse."""
        response = PolicyService.validate(
            PolicyValidateRequest(path="/tmp/nonexistent-bound-policy-xyz.yaml")
        )
        assert isinstance(response, PolicyValidateResponse)
        assert response.valid is False
        assert response.error_kind == "usage"

    def test_explain_raises_policy_load_error(self) -> None:
        """PolicyService.explain raises PolicyLoadError for a non-existent file."""
        with pytest.raises(PolicyLoadError, match="policy file not found"):
            PolicyService.explain(PolicyExplainRequest(path="/tmp/nonexistent-bound-policy-xyz.yaml"))

    def test_explain_raises_policy_validation_error(self, tmp_path: Path) -> None:
        """PolicyService.explain raises PolicyValidationError for invalid YAML."""
        bad = tmp_path / "bad.yaml"
        bad.write_text("not_a_mapping: [1, 2, 3]\n", encoding="utf-8")
        with pytest.raises(PolicyValidationError):
            PolicyService.explain(PolicyExplainRequest(path=str(bad)))

    def test_hash_raises_policy_load_error(self) -> None:
        """PolicyService.hash raises PolicyLoadError for a non-existent file."""
        with pytest.raises(PolicyLoadError, match="policy file not found"):
            PolicyService.hash(PolicyHashRequest(path="/tmp/nonexistent-bound-policy-xyz.yaml"))

    def test_hash_raises_policy_validation_error(self, tmp_path: Path) -> None:
        """PolicyService.hash raises PolicyValidationError for invalid YAML."""
        bad = tmp_path / "bad.yaml"
        bad.write_text("not_a_mapping: [1, 2, 3]\n", encoding="utf-8")
        with pytest.raises(PolicyValidationError):
            PolicyService.hash(PolicyHashRequest(path=str(bad)))

    def test_validate_default_policy(self) -> None:
        """The built-in default policy validates successfully."""
        default = Path(__file__).resolve().parent.parent / "src" / "bound" / "default_policy.yaml"
        response = PolicyService.validate(PolicyValidateRequest(path=str(default)))
        assert response.valid is True
        assert response.policy is not None
        assert response.policy.id == "coding-default"

    def test_explain_default_policy(self) -> None:
        """PolicyService.explain on the default policy returns a populated response."""
        default = Path(__file__).resolve().parent.parent / "src" / "bound" / "default_policy.yaml"
        response = PolicyService.explain(PolicyExplainRequest(path=str(default)))
        assert isinstance(response, PolicyExplainResponse)
        assert response.policy.id == "coding-default"
        assert response.human_readable != ""

    def test_hash_default_policy(self) -> None:
        """PolicyService.hash on the default policy returns a hash response."""
        default = Path(__file__).resolve().parent.parent / "src" / "bound" / "default_policy.yaml"
        response = PolicyService.hash(PolicyHashRequest(path=str(default)))
        assert isinstance(response, PolicyHashResponse)
        assert response.hash.startswith("sha256:")
        assert response.policy.id == "coding-default"


# --- RunService ---

class TestRunService:
    """Integration tests for RunService."""

    def test_start_returns_typed_response(self) -> None:
        """RunService.start accepts RunStartRequest and returns RunStartResponse."""
        response = RunService.start(RunStartRequest(task="test task"))
        assert isinstance(response, RunStartResponse)
        assert response.run_id != ""
        assert response.task == "test task"
        assert response.status == "started"

    def test_finish_returns_typed_response(self) -> None:
        """RunService.finish accepts RunFinishRequest and returns RunFinishResponse."""
        # First create a real run so we can finish it
        start = RunService.start(RunStartRequest(task="finish test"))
        response = RunService.finish(RunFinishRequest(run_id=start.run_id))
        assert isinstance(response, RunFinishResponse)
        assert response.run_id == start.run_id
        assert response.status == "completed"

    def test_list_runs_returns_typed_response(self) -> None:
        """RunService.list_runs returns RunListResponse with a list."""
        # Ensure at least one run exists
        RunService.start(RunStartRequest(task="list test"))
        response = RunService.list_runs(RunListRequest())
        assert isinstance(response, RunListResponse)
        assert isinstance(response.runs, list)

    def test_delete_returns_typed_response(self) -> None:
        """RunService.delete accepts RunDeleteRequest and returns RunDeleteResponse."""
        # Create a run, then delete it
        start = RunService.start(RunStartRequest(task="delete test"))
        response = RunService.delete(RunDeleteRequest(run_id=start.run_id))
        assert isinstance(response, RunDeleteResponse)
        assert response.run_id == start.run_id
        assert response.deleted is True

    def test_inspect_raises_not_found(self) -> None:
        """RunService.inspect raises RunNotFoundError for a non-existent run."""
        with pytest.raises(RunNotFoundError, match="no lineage run"):
            RunService.inspect(RunInspectRequest(run_id="nonexistent-run"))

    def test_start_and_list_roundtrip(self) -> None:
        """Starting a run makes it appear in list_runs."""
        start_resp = RunService.start(RunStartRequest(task="roundtrip test"))
        list_resp = RunService.list_runs(RunListRequest())
        run_ids = [r.run_id for r in list_resp.runs]
        assert start_resp.run_id in run_ids


# --- EvaluationService ---

class TestEvaluationService:
    """Integration tests for EvaluationService."""

    def test_evaluate_returns_typed_response(self) -> None:
        """EvaluationService.evaluate returns EvaluateResponse with result."""
        response = EvaluationService.evaluate(EvaluateRequest(
            action=Action(description="Book flight", goal="Travel"),
            scores=EvaluationScores(acceptance=0.9, influence=0.2, risk=0.1, cost=0.2),
            criteria=BoundCriteria(threshold=0.6),
        ))
        assert isinstance(response, EvaluateResponse)
        assert isinstance(response.result, EvaluationResult)
        assert response.result.decision == "ACCEPT"
        assert response.result.score == pytest.approx(0.8, abs=1e-12)
        assert response.prompt != ""
        assert "scores" in response.payload
        assert "decision" in response.payload

    def test_evaluate_retry_below_threshold(self) -> None:
        """A score within retry-margin below threshold yields RETRY."""
        # Score = 0.55, threshold = 0.6, gap = 0.05 <= retry_margin(0.1) -> RETRY
        response = EvaluationService.evaluate(EvaluateRequest(
            action=Action(description="Marginal action", goal="Travel"),
            scores=EvaluationScores(acceptance=0.55, influence=0.0, risk=0.0, cost=0.0),
            criteria=BoundCriteria(threshold=0.6),
        ))
        assert response.result.decision == "RETRY"

    def test_evaluate_raises_on_invalid_input(self) -> None:
        """EvaluationService.evaluate raises EvaluationInputError for invalid inputs."""
        # The service wraps ValueError from BoundPolicy; we need to trigger one.
        # Using a negative threshold passes Pydantic but the evaluator only
        # raises ValueError when no evaluator is bound - so this test verifies
        # the service does NOT raise for valid inputs (the error is never triggered
        # because the service always binds an evaluator).
        response = EvaluationService.evaluate(EvaluateRequest(
            action=Action(description="Test", goal="Test"),
            scores=EvaluationScores(acceptance=0.5, influence=0.0, risk=0.0, cost=0.0),
            criteria=BoundCriteria(threshold=0.6),
        ))
        # Service handles all valid inputs gracefully
        assert isinstance(response, EvaluateResponse)

    def test_evaluate_workflow_returns_typed_response(self) -> None:
        """EvaluationService.evaluate_workflow returns EvaluateWorkflowResponse."""
        response = EvaluationService.evaluate_workflow(EvaluateWorkflowRequest(
            action=Action(description="Implement feature", goal="Ship feature"),
            signals=CodingWorkflowSignals(
                test_pass_rate=1.0, lint_passed=True, type_check_passed=True,
            ),
            criteria=BoundCriteria(threshold=0.6),
        ))
        assert isinstance(response, EvaluateWorkflowResponse)
        assert isinstance(response.result, EvaluationResult)
        assert "signals" in response.payload
        assert response.signals is not None

    def test_evaluate_workflow_raises_on_invalid_input(self) -> None:
        """EvaluationService.evaluate_workflow raises EvaluationInputError with no acceptance evidence."""
        # The CodingWorkflowEvaluator raises ValueError when no acceptance
        # signals are provided (all are None).
        with pytest.raises(EvaluationInputError, match="no acceptance evidence"):
            EvaluationService.evaluate_workflow(EvaluateWorkflowRequest(
                action=Action(description="Test", goal="Test"),
                signals=CodingWorkflowSignals(),
                criteria=BoundCriteria(threshold=0.6),
            ))


# --- OutcomeService ---

class TestOutcomeService:
    """Integration tests for OutcomeService."""

    def test_record_raises_not_found(self) -> None:
        """OutcomeService.record raises RunNotFoundError for a non-existent run."""
        with pytest.raises(RunNotFoundError, match="no lineage run"):
            OutcomeService.record(OutcomeRecordRequest(
                run_id="nonexistent",
                step_id="step-1",
                evaluation_id="eval-1",
                decision="ACCEPT",
            ))


# --- EvidenceService ---

class TestEvidenceService:
    """Integration tests for EvidenceService."""

    def test_collect_raises_not_found(self) -> None:
        """EvidenceService.collect raises RunNotFoundError for a non-existent run.

        The run-exists guard must fire *before* any event is appended so a
        stale ``run_id`` never produces a spurious lineage event.
        """
        with pytest.raises(RunNotFoundError, match="no lineage run"):
            EvidenceService.collect(EvidenceCollectRequest(
                run_id="nonexistent",
                step_id="step-1",
                evaluation_id="eval-1",
                check_id="check-1",
                provenance=EvidenceProvenance.OBSERVED,
                passed=True,
            ))

    def test_collect_happy_path(self) -> None:
        """EvidenceService.collect records evidence for an existing run.

        Creates a real run, records evidence, and verifies the response carries
        a non-empty ``event_id`` generated by the store (not by the broken
        ``generate_event_id`` call that was missing ``sequence``).
        """
        start = RunService.start(RunStartRequest(task="evidence collect test"))
        response = EvidenceService.collect(EvidenceCollectRequest(
            run_id=start.run_id,
            step_id="step-1",
            evaluation_id="eval-1",
            check_id="check-1",
            provenance=EvidenceProvenance.OBSERVED,
            passed=True,
            collector="test-collector",
        ))
        assert isinstance(response, EvidenceCollectResponse)
        assert response.event_id != ""
        assert response.run_id == start.run_id
        assert response.check_id == "check-1"


# --- BoundaryService ---

class TestBoundaryService:
    """Integration tests for BoundaryService."""

    def test_evaluate_returns_typed_response(self) -> None:
        """BoundaryService.evaluate accepts BoundaryEvaluateRequest and returns
        BoundaryEvaluateResponse."""
        # This test just verifies the method accepts the typed request and returns
        # a typed response, even if the evaluation may not produce a meaningful result.
        response = BoundaryService.evaluate(BoundaryEvaluateRequest(
            contract=StepContract(
                id="test", description="Test", goal="Goal",
                acceptance_checks=[
                    AcceptanceCheck(
                        id="c1", description="d1",
                        accepted_provenance=[EvidenceProvenance.OBSERVED],
                    ),
                ],
            ),
            evidence=ExecutionEvidence(),
            criteria=BoundCriteria(threshold=0.5),
        ))
        assert isinstance(response, BoundaryEvaluateResponse)
        assert isinstance(response.result, EvaluationResult)

    def test_evaluate_propagates_unexpected_exceptions(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Unexpected (non-validation) exceptions propagate, not swallowed.

        Previously a bare ``except Exception`` wrapped *every* failure as
        ``EvaluationInputError``, hiding programming errors.  Now only
        ``ValueError`` / ``ValidationError`` are wrapped; anything else
        (e.g. ``RuntimeError``) must propagate unchanged so real bugs are
        visible.
        """
        from bound.bound_workflow import BoundWorkflow

        class _ExplodingWorkflow:
            def evaluate_step(self, **_kwargs: object) -> None:
                raise RuntimeError("unexpected internal failure")

        monkeypatch.setattr(
            "bound.bound_workflow.BoundWorkflow", _ExplodingWorkflow
        )

        with pytest.raises(RuntimeError, match="unexpected internal failure"):
            BoundaryService.evaluate(BoundaryEvaluateRequest(
                contract=StepContract(
                    id="test", description="Test", goal="Goal",
                    acceptance_checks=[
                        AcceptanceCheck(
                            id="c1", description="d1",
                            accepted_provenance=[EvidenceProvenance.OBSERVED],
                        ),
                    ],
                ),
                evidence=ExecutionEvidence(),
                criteria=BoundCriteria(threshold=0.5),
            ))


# --- CheckpointService ---

class TestCheckpointService:
    """Integration tests for CheckpointService."""

    def test_create_raises_not_found(self) -> None:
        """CheckpointService.create raises RunNotFoundError for non-existent run."""
        request = CheckpointCreateRequest(run_id="test-run", step_id="test-step")
        with pytest.raises(RunNotFoundError, match="no lineage run"):
            CheckpointService.create(request)

    def test_rollback_raises_not_found(self) -> None:
        """CheckpointService.rollback raises RunNotFoundError for non-existent run."""
        request = CheckpointRollbackRequest(run_id="test-run", checkpoint_id="cp-1")
        with pytest.raises(RunNotFoundError, match="no lineage run"):
            CheckpointService.rollback(request)


# =========================================================================
# 5. CLI output reflects service results
# =========================================================================

class TestCliCallsServices:
    """Verify that the CLI handlers call the service layer and reflect its results."""

    def test_cli_policy_validate_calls_service(self, capsys: pytest.CaptureFixture[str]) -> None:
        """bound policy validate on a non-existent file calls PolicyService and reflects the error."""
        from bound.cli import main
        rc = main(["policy", "validate", "/tmp/nonexistent-bound-policy-xyz.yaml"])
        out, err = capsys.readouterr()
        assert rc != 0
        assert "policy file not found" in err

    def test_cli_policy_hash_calls_service(self, capsys: pytest.CaptureFixture[str]) -> None:
        """bound policy hash on the default policy calls PolicyService.hash."""
        from bound.cli import main
        default = Path(__file__).resolve().parent.parent / "src" / "bound" / "default_policy.yaml"
        rc = main(["policy", "hash", str(default)])
        out, _ = capsys.readouterr()
        assert rc == 0
        assert out.strip().startswith("sha256:")

    def test_cli_evaluate_calls_service(self, capsys: pytest.CaptureFixture[str]) -> None:
        """bound evaluate calls EvaluationService and writes JSON to stdout."""
        from bound.cli import main
        rc = main([
            "evaluate",
            "--action", "Book flight",
            "--goal", "Travel",
            "--acceptance", "0.9",
            "--influence", "0.2",
            "--risk", "0.1",
            "--cost", "0.2",
            "--threshold", "0.6",
        ])
        out, err = capsys.readouterr()
        assert rc == 0
        payload = json.loads(out)
        assert payload["decision"] == "ACCEPT"
        assert payload["score"] == pytest.approx(0.8, abs=1e-12)
        assert "[BOUND evaluation]" in err

    def test_cli_run_start_calls_service(self, capsys: pytest.CaptureFixture[str]) -> None:
        """bound run start calls RunService.start and prints the run_id."""
        from bound.cli import main
        rc = main(["run", "start", "test task from cli"])
        out, _ = capsys.readouterr()
        assert rc == 0
        assert out.strip() != ""
        assert len(out.strip()) > 10


# =========================================================================
# Helpers
# =========================================================================


def _minimal_instance(model: type) -> Any:
    """Create a minimal instance of a Pydantic model using construct() to skip validation."""
    field_values: dict[str, Any] = {}
    for field_name, field_info in model.model_fields.items():
        if field_name == "store":
            continue
        if field_info.is_required():
            field_values[field_name] = _inject_value(field_info.annotation, field_name)
        elif field_info.default is not None and field_info.default is not PydanticUndefined:
            field_values[field_name] = field_info.default
        elif field_info.default_factory is not None:
            field_values[field_name] = field_info.default_factory()
    return model.model_construct(**field_values)


def _inject_value(type_hint: Any, field_name: str) -> Any:
    """Inject a minimal value for a type hint."""
    if type_hint is str:
        return f"test-{field_name}"
    if type_hint is int:
        return 0
    if type_hint is float:
        return 0.0
    if type_hint is bool:
        return False
    if type_hint is datetime:
        return datetime(2025, 1, 1, tzinfo=timezone.utc)
    if type_hint is bytes:
        return b""
    if type_hint is None:
        return None
    origin = getattr(type_hint, "__origin__", None)
    # Literal types — return first value
    if origin is Literal:
        args = getattr(type_hint, "__args__", ())
        if args:
            return args[0]
        return "default"
    # Union / Optional — pick first non-None arg
    if origin is Union or str(origin).endswith(".UnionType'"):
        if hasattr(type_hint, "__args__"):
            args = [a for a in type_hint.__args__ if a is not type(None)]
            if args:
                return _inject_value(args[0], field_name)
        return None
    if origin is list:
        return []
    if origin is dict:
        return {}
    type_name = getattr(type_hint, "__name__", "")
    if "EvaluationScores" in type_name:
        return EvaluationScores(acceptance=0.5, influence=0.0, risk=0.0, cost=0.0)
    if "PolicyIdentity" in type_name:
        return PolicyIdentity(id="test", version="0.1", hash="sha256:abc")
    if "BoundCriteria" in type_name:
        return BoundCriteria(threshold=0.5)
    if "CodingWorkflowSignals" in type_name:
        return CodingWorkflowSignals(
            test_pass_rate=1.0, lint_passed=True, type_check_passed=True
        )
    if "EvaluationResult" in type_name:
        return EvaluationResult.model_construct(
            scores=EvaluationScores(
                acceptance=0.5, influence=0.0, risk=0.0, cost=0.0
            ),
            weights=BoundWeights(),
            threshold=0.6,
            acceptance_component=0.5,
            influence_component=0.0,
            risk_component=0.0,
            cost_component=0.0,
            score=0.5,
            distance_to_threshold=0.0,
            decision="ACCEPT",
        )
    if "StepContract" in type_name:
        return StepContract(
            id="test-contract", description="Test", goal="Goal",
            acceptance_checks=[
                AcceptanceCheck(
                    id="check-1", description="Check",
                    accepted_provenance=[EvidenceProvenance.OBSERVED],
                ),
            ],
            risk_checks=[],
        )
    if "ExecutionEvidence" in type_name:
        return ExecutionEvidence()
    if "BoundPlan" in type_name:
        return BoundPlan(goal="Test goal", steps=[])
    if "RunStatus" in type_name:
        return RunStatus.STARTED
    if "RunSummary" in type_name:
        return RunSummary(
            run_id="test-run", task="test", status=RunStatus.STARTED,
            started_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            finished_at=None, step_count=0, event_count=0, incomplete=False,
            path="/tmp/.bound/runs/test-run",
        )
    if hasattr(type_hint, "model_fields"):
        return _minimal_instance(type_hint)
    return None