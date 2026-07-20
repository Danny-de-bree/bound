from __future__ import annotations

import importlib.util
import json

import bound
from bound.collectors import (
    GitInspection,
    ServiceTestEvidence,
    parse_pytest_summary,
)
from bound.contracts import AcceptanceCheck, StepContract, is_valid_phase_id
from bound.evidence import CheckEvidence, ExecutionEvidence
from bound.integration import _DECISION_TO_ACTION, evaluate_agent_step
from bound.integration_spec import integration_spec
from bound.models import BoundCriteria
from bound.report import RunTrace, render_from_trace
from tests.conftest import DECISION_TO_CONTROL, REPO_ROOT

#: The canonical BOUND decision -> control-action mapping. Lives ONLY in these
#: tests, used to assert the public API agrees with it; it is never consulted at
#: runtime to translate a decision independently of BOUND.
_CANONICAL_DECISION_TO_CONTROL = DECISION_TO_CONTROL


# ---------------------------------------------------------------------------
# Shared helpers: a green contract + evidence -> a real ACCEPT evaluation
# ---------------------------------------------------------------------------


def _green_contract() -> StepContract:
    """A green contract mirroring the reference integration's shape."""
    return StepContract(
        id="PHASE-001",
        description="DoD contract.",
        goal="A green evaluation for the v0.6 DoD report test.",
        acceptance_checks=[
            AcceptanceCheck(id="tests-pass", description="suite green", required=True),
            AcceptanceCheck(
                id="service-tests-pass", description="service green", required=True
            ),
        ],
    )


def _green_evidence() -> ExecutionEvidence:
    """Green evidence: both acceptance checks pass."""
    return ExecutionEvidence(
        acceptance=[
            CheckEvidence(
                check_id="tests-pass", passed=True, source="uv run pytest -q"
            ),
            CheckEvidence(
                check_id="service-tests-pass",
                passed=True,
                source="uv run pytest tests/test_calculator.py -q",
            ),
        ],
    )


# ---------------------------------------------------------------------------
# DoD: failed git inspection does not produce optimistic evidence
# ---------------------------------------------------------------------------


def test_failed_git_inspection_is_not_optimistic() -> None:
    """A failed ``git status`` can never be read as a clean tree."""
    failed = GitInspection.command_failed()
    # A failed command yields empty path lists (git could not report)...
    assert failed.command_succeeded is False
    assert failed.unexpected_paths == []
    # ...but that is *unavailable* evidence, not *clean* evidence: the guarded
    # read refuses to treat an empty list from a failed command as a pass.
    assert failed.is_clean_proven() is False


# ---------------------------------------------------------------------------
# DoD: pytest warnings are not counted as tests
# ---------------------------------------------------------------------------


def test_pytest_warnings_not_counted_as_tests() -> None:
    """``30 passed, 2 warnings`` is 30 executed tests, never 32."""
    summary = parse_pytest_summary("30 passed, 2 warnings in 0.09s")
    assert summary.executed_test_count == 30
    # Warnings are not a test outcome: there is no warnings field to read.
    assert not hasattr(summary, "warnings")


# ---------------------------------------------------------------------------
# DoD: service test evidence is genuinely service-specific
# ---------------------------------------------------------------------------


def test_service_test_evidence_is_service_specific() -> None:
    """``service-tests-pass`` passes only when the service run executed >=1 test."""
    # A green command whose service module ran 0 tests is NOT a pass.
    assert (
        ServiceTestEvidence(command_succeeded=True, executed_test_count=0).passed
        is False
    )
    # A failed command that did run tests is NOT a pass either.
    assert (
        ServiceTestEvidence(command_succeeded=False, executed_test_count=5).passed
        is False
    )
    # Only command OK AND >=1 executed test satisfies the service-specific check.
    assert (
        ServiceTestEvidence(command_succeeded=True, executed_test_count=1).passed
        is True
    )


# ---------------------------------------------------------------------------
# DoD: plan IDs are preserved in contracts
# ---------------------------------------------------------------------------


def test_plan_ids_preserved_in_contracts() -> None:
    """The plan id survives unchanged into the contract id; replans append."""
    contract = StepContract(
        id="PHASE-001",
        description="the planned phase",
        goal="advance the plan",
        acceptance_checks=[AcceptanceCheck(id="a", description="check a")],
    )
    assert contract.id == "PHASE-001"
    # A -R1 replan keeps the root identity (history preserved, not replaced).
    replan = "PHASE-001-R1"
    assert is_valid_phase_id(replan)
    assert replan.startswith("PHASE-001")


# ---------------------------------------------------------------------------
# DoD: decision mapping is not duplicated in runtime integration code
# ---------------------------------------------------------------------------


def test_decision_mapping_not_duplicated_in_runtime() -> None:
    """Exactly one runtime decision->action mapping, consumed by the public API."""
    # The runtime source, the published spec, and the canonical mapping agree.
    assert _DECISION_TO_ACTION == _CANONICAL_DECISION_TO_CONTROL
    assert integration_spec()["decision_to_control"] == _DECISION_TO_ACTION
    # The runtime module carries exactly one decision->action mapping literal:
    # no duplicated/competing table making decisions independently of BOUND.
    integration_source = (
        REPO_ROOT / "src" / "bound" / "integration.py"
    ).read_text(encoding="utf-8")
    assert integration_source.count('"ROLLBACK": "rollback"') == 1
    # evaluate_agent_step is the single public runtime path that consumes it.
    assert "evaluate_agent_step" in integration_source
    assert "_DECISION_TO_ACTION[evaluation.decision]" in integration_source


# ---------------------------------------------------------------------------
# DoD: execution report uses real API-returned values (no fabrication)
# ---------------------------------------------------------------------------


def test_report_uses_real_api_values_and_no_fabrication() -> None:
    """The report carries BOUND's real evaluation verbatim; never invents telemetry."""
    contract = _green_contract()
    evidence = _green_evidence()
    result = evaluate_agent_step(contract, evidence, BoundCriteria(threshold=0.75))
    evaluation = result.evaluation

    trace = RunTrace(
        plan_id="PHASE-001",
        step_id=contract.id,
        run_id="0" * 32,
        timestamp="2026-07-17T00:00:00+00:00",
        contract=contract,
        evidence=evidence,
        evaluation=evaluation,
        next_action=result.next_action,
        feedback=result.feedback,
        # token_usage / runtime_seconds / tool_call_count / model_metadata
        # are unobservable here -> stay None (never fabricated).
    )
    report = render_from_trace(trace)

    # The report emits the *same* score / threshold / decision BOUND returned.
    assert f"S = {evaluation.score:.4f}" in report
    assert f"T = {evaluation.threshold:.4f}" in report
    assert str(evaluation.decision) in report
    assert result.next_action in report
    # Unobservable telemetry stays None on the trace and "unavailable" in prose.
    assert trace.token_usage is None
    assert trace.runtime_seconds is None
    assert "token_usage: unavailable (null)" in report
    assert "runtime_seconds: unavailable (null)" in report


# ---------------------------------------------------------------------------
# DoD: no unavailable fields are fabricated
# ---------------------------------------------------------------------------


def test_runtrace_optional_fields_default_none() -> None:
    """A freshly built RunTrace never invents telemetry: optionals default to None."""
    contract = _green_contract()
    evidence = _green_evidence()
    result = evaluate_agent_step(contract, evidence, BoundCriteria(threshold=0.75))
    trace = RunTrace(
        plan_id="PHASE-001",
        step_id=contract.id,
        run_id="1" * 32,
        timestamp="2026-07-17T00:00:00+00:00",
        contract=contract,
        evidence=evidence,
        evaluation=result.evaluation,
        next_action=result.next_action,
    )
    assert trace.token_usage is None
    assert trace.runtime_seconds is None
    assert trace.tool_call_count is None
    assert trace.model_metadata is None
    assert trace.raw_commands is None
    assert trace.feedback is None


# ---------------------------------------------------------------------------
# DoD: demo generation uses stored trace data
# ---------------------------------------------------------------------------


def _load_generate_demo():
    """Import scripts/generate_demo.py as a module (stdlib-only script)."""
    path = REPO_ROOT / "scripts" / "generate_demo.py"
    spec = importlib.util.spec_from_file_location("_bound_dod_generate_demo", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_demo_generation_uses_stored_trace_data() -> None:
    """The demo GIF is generated from bound's own stored run.json, never invented."""
    demo = _load_generate_demo()
    # The default input is bound's own stored trace.
    default = demo.default_run_json()
    assert default == REPO_ROOT / "bound_integration" / "run.json"
    assert default.is_file(), "bound_integration/run.json must exist"
    # build_frames consumes the stored trace fields (a real trace dict).
    trace = json.loads(default.read_text(encoding="utf-8"))
    frames = demo.build_frames(trace)
    assert len(frames) == 6
    # The committed GIF artifact exists and is non-empty (the visualization).
    gif = REPO_ROOT / "assets" / "bound-demo.gif"
    assert gif.is_file() and gif.stat().st_size > 0


# ---------------------------------------------------------------------------
# DoD: the 0.4.0/0.5.0 version mismatch is resolved (kept current at release)
# ---------------------------------------------------------------------------


def test_version_bumped_and_consistent() -> None:
    """``__init__.py`` and ``pyproject.toml`` agree on the current release."""
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{bound.__version__}"' in pyproject
