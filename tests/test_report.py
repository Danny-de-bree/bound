"""Tests for the v0.6 report renderer and RunTrace (Phases 8, 9, 11).

Covers:

* :class:`bound.report.RunTrace` round-trips losslessly through JSON.
* :func:`bound.report.render_from_trace` renders the required subsections,
  preserves the step id, and does not fabricate unavailable telemetry.
* ``scripts/generate_demo.py`` builds demo frames from a stored trace (the
  frame-building logic is tested directly to avoid binary assertions).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from bound.contracts import AcceptanceCheck, RiskCheck, StepBudget, StepContract
from bound.evidence import CheckEvidence, ExecutionEvidence
from bound.integration import evaluate_agent_step
from bound.models import BoundCriteria
from bound.report import (
    DecisionHistoryEntry,
    RawCommandRecord,
    RunTrace,
    render_from_trace,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Shared fixtures: a green contract + evidence -> a real ACCEPT RunTrace
# ---------------------------------------------------------------------------


def _green_contract() -> StepContract:
    """A green contract mirroring the reference integration's shape."""
    return StepContract(
        id="PHASE-001",
        description="Test contract for report rendering.",
        goal="A green evaluation for testing the report renderer.",
        acceptance_checks=[
            AcceptanceCheck(
                id="tests-pass", description="Full suite green.", required=True
            ),
            AcceptanceCheck(
                id="service-tests-pass",
                description="Service tests green.",
                required=True,
            ),
        ],
        risk_checks=[
            RiskCheck(
                id="no-unexpected-files",
                description="No unexpected files.",
                severity=0.6,
            ),
        ],
        expected_artifacts=["src/bound/report.py"],
        budget=StepBudget(max_retries=3, max_tool_calls=40),
    )


def _green_evidence() -> ExecutionEvidence:
    """Green evidence: both acceptance checks pass, risk clean."""
    return ExecutionEvidence(
        acceptance=[
            CheckEvidence(
                check_id="tests-pass",
                passed=True,
                source="uv run pytest -q",
                details="exit_code=0",
            ),
            CheckEvidence(
                check_id="service-tests-pass",
                passed=True,
                source="uv run pytest tests/test_calculator.py -q",
                details="exit_code=0; executed=34",
            ),
        ],
        risks=[
            CheckEvidence(
                check_id="no-unexpected-files",
                passed=True,
                source="git status --porcelain",
                details="no unexpected paths",
            ),
        ],
        produced_artifacts=["src/bound/report.py"],
        unexpected_artifacts=[],
    )


def _make_trace() -> RunTrace:
    """Build a representative green RunTrace (real BOUND evaluation -> ACCEPT)."""
    contract = _green_contract()
    evidence = _green_evidence()
    result = evaluate_agent_step(contract, evidence, BoundCriteria(threshold=0.75))
    return RunTrace(
        plan_id="PHASE-001",
        step_id=contract.id,
        run_id="a" * 32,
        bound_version="0.4.0",
        bound_distribution_version="0.5.0",
        timestamp="2026-01-01T00:00:00+00:00",
        contract=contract,
        evidence=evidence,
        evaluation=result.evaluation,
        next_action=result.next_action,
        feedback=result.feedback,
        raw_commands={
            "full_suite": RawCommandRecord(
                command="uv run pytest -q",
                returncode=0,
                stdout="496 passed in 1.00s\n",
                stderr="",
            ),
            "service_suite": RawCommandRecord(
                command="uv run pytest tests/test_calculator.py -q",
                returncode=0,
                stdout="34 passed in 0.11s\n",
                stderr="",
            ),
            "git_status": RawCommandRecord(
                command="git status --porcelain",
                returncode=0,
                stdout=" M src/bound/report.py\n",
                stderr="",
            ),
        },
        decision_history=[
            DecisionHistoryEntry(
                step_id=contract.id,
                attempt=1,
                decision=result.evaluation.decision,
                next_action=result.next_action,
                note="first evaluation; no replan or retry",
            ),
        ],
        trajectory=["PLAN.md", "PHASE-001", "BOUND", "ACCEPT -> continue"],
        # token_usage / runtime_seconds / tool_call_count / model_metadata
        # deliberately left None (unobservable) to test non-fabrication.
    )


# ---------------------------------------------------------------------------
# Phase 9 — RunTrace JSON round-trip
# ---------------------------------------------------------------------------


def test_runtrace_round_trips_through_json() -> None:
    """A RunTrace survives ``model_dump_json`` / ``model_validate_json`` losslessly."""
    original = _make_trace()
    blob = original.model_dump_json()
    restored = RunTrace.model_validate_json(blob)

    # Core identity fields round-trip exactly.
    assert restored.plan_id == original.plan_id
    assert restored.step_id == original.step_id
    assert restored.run_id == original.run_id
    assert restored.bound_version == original.bound_version
    assert restored.timestamp == original.timestamp
    # The nested contract / evidence / evaluation survive (structural equality).
    assert restored.contract == original.contract
    assert restored.evidence == original.evidence
    assert restored.evaluation == original.evaluation
    assert restored.next_action == original.next_action
    assert restored.feedback == original.feedback
    # raw_commands survive as typed records.
    assert restored.raw_commands is not None
    assert restored.raw_commands["full_suite"].returncode == 0
    assert restored.raw_commands["service_suite"].command.startswith("uv run pytest")
    # Unobservable telemetry stays None (never fabricated) across round-trip.
    assert restored.token_usage is None
    assert restored.runtime_seconds is None
    assert restored.tool_call_count is None
    assert restored.model_metadata is None
    # decision_history / trajectory survive.
    assert restored.decision_history == original.decision_history
    assert restored.trajectory == original.trajectory


def test_runtrace_rejects_extra_fields() -> None:
    """RunTrace uses extra='forbid', so unknown keys are rejected on parse."""
    original = _make_trace()
    data = json.loads(original.model_dump_json())
    data["fabricated_field"] = "should be rejected"
    with pytest.raises(ValidationError):
        RunTrace.model_validate(data)


# ---------------------------------------------------------------------------
# Phase 8 — report renderer: subsections, step id, no fabrication
# ---------------------------------------------------------------------------


_REQUIRED_SUBSECTIONS = (
    "## Run summary",
    "### Planned goal",
    "### Actual execution",
    "### Observed acceptance evidence",
    "### Observed risk evidence",
    "### Unavailable evidence",
    "### BOUND evaluation",
    "### Decision history",
    "### Plan deviation",
    "### Produced artifacts",
    "### Unexpected artifacts",
    "### Final verification",
)


def test_report_renders_required_subsections_and_preserves_step_id() -> None:
    """The renderer emits every required subsection and the step id verbatim."""
    report = render_from_trace(_make_trace())
    for section in _REQUIRED_SUBSECTIONS:
        assert section in report, f"missing required subsection: {section!r}"
    # The step / contract id is preserved verbatim from the plan.
    assert "## PHASE-001" in report
    assert "PHASE-001" in report
    # The deterministic decision is carried verbatim (never reconstructed).
    assert "ACCEPT" in report


def test_report_does_not_fabricate_unavailable_telemetry() -> None:
    """Unobservable telemetry is rendered as unavailable (null), never invented."""
    trace = _make_trace()
    assert trace.token_usage is None
    assert trace.runtime_seconds is None
    report = render_from_trace(trace)
    # Each unobservable signal is honestly marked unavailable.
    assert "token_usage: unavailable (null)" in report
    assert "runtime_seconds: unavailable (null)" in report
    assert "tool_call_count: unavailable (null)" in report
    assert "model_metadata: unavailable (null)" in report
    # A fabricated number would never appear for these.
    assert "token_usage: 1234" not in report


def test_report_is_deterministic_for_same_trace() -> None:
    """Rendering the same trace twice yields the same report bit-for-bit."""
    trace = _make_trace()
    assert render_from_trace(trace) == render_from_trace(trace)


# ---------------------------------------------------------------------------
# Phase 11 — demo frames are built from stored trace data
# ---------------------------------------------------------------------------


def _load_generate_demo():
    """Import scripts/generate_demo.py as a module (stdlib-only script)."""
    path = REPO_ROOT / "scripts" / "generate_demo.py"
    spec = importlib.util.spec_from_file_location("_bound_generate_demo", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_demo_frames_built_from_trace_data() -> None:
    """build_frames produces six non-empty frames from a real trace dict."""
    demo = _load_generate_demo()
    trace_dict = _make_trace().model_dump(mode="json")
    frames = demo.build_frames(trace_dict)
    assert len(frames) == 6
    for frame in frames:
        # Each frame is a palette-indexed buffer of WIDTH * HEIGHT bytes.
        assert len(frame) == demo.WIDTH * demo.HEIGHT
        assert any(frame)  # not all-zero (the frame has content)


def test_demo_frames_built_from_stored_run_json_if_present() -> None:
    """If bound_integration/run.json exists, build_frames consumes it directly."""
    run_json = REPO_ROOT / "bound_integration" / "run.json"
    if not run_json.is_file():
        pytest.skip("bound_integration/run.json not generated yet")
    demo = _load_generate_demo()
    trace = json.loads(run_json.read_text(encoding="utf-8"))
    frames = demo.build_frames(trace)
    assert len(frames) == 6
    for frame in frames:
        assert len(frame) == demo.WIDTH * demo.HEIGHT
        assert any(frame)


