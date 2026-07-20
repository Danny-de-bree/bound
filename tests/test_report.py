from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from bound.contracts import AcceptanceCheck, RiskCheck, StepBudget, StepContract
from bound.evidence import (
    CheckEvidence,
    EvidenceMetric,
    EvidenceProvenance,
    EvidenceStatus,
    ExecutionEvidence,
)
from bound.integration import evaluate_agent_step
from bound.models import BoundCriteria
from bound.report import (
    DecisionHistoryEntry,
    RawCommandRecord,
    RunTrace,
    render_from_trace,
)
from tests.conftest import REPO_ROOT

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
    """Green evidence: both acceptance checks pass, risk clean.

    v0.7: the checks carry independent (VERIFIED) provenance and a named
    collector, so the report's provenance breakdown and coverage metric show a
    genuinely verified ACCEPT rather than a CLAIMED one. The retry/tool
    telemetry is *measured* (OBSERVED) and in budget — missing telemetry now
    saturates conservatively, so omitting it would no longer yield an ACCEPT.
    """
    return ExecutionEvidence(
        acceptance=[
            CheckEvidence(
                check_id="tests-pass",
                passed=True,
                source="uv run pytest -q",
                details="exit_code=0",
                provenance=EvidenceProvenance.VERIFIED,
                collector="bound.pytest",
                collector_version="0.7.0",
            ),
            CheckEvidence(
                check_id="service-tests-pass",
                passed=True,
                source="uv run pytest tests/test_calculator.py -q",
                details="exit_code=0; executed=34",
                provenance=EvidenceProvenance.VERIFIED,
                collector="bound.pytest",
                collector_version="0.7.0",
            ),
        ],
        risks=[
            CheckEvidence(
                check_id="no-unexpected-files",
                passed=True,
                source="git status --porcelain",
                details="no unexpected paths",
                provenance=EvidenceProvenance.VERIFIED,
                collector="bound.git",
                collector_version="0.7.0",
            ),
        ],
        produced_artifacts=["src/bound/report.py"],
        unexpected_artifacts=[],
        retry_count=EvidenceMetric(
            value=0, provenance=EvidenceProvenance.OBSERVED, source="harness.retries"
        ),
        tool_call_count=EvidenceMetric(
            value=0, provenance=EvidenceProvenance.OBSERVED, source="cline.tool_events"
        ),
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


# ---------------------------------------------------------------------------
# Schema 2.0 — RunTrace config + action fields (items 11, 12)
# ---------------------------------------------------------------------------


def test_run_trace_carries_config() -> None:
    """RunTrace.config logs the policy/config version snapshot (item 11)."""
    from bound.lineage import build_run_config

    trace = _make_trace()
    assert trace.config is None
    cfg = build_run_config(
        bound_version="0.7.0",
        policy_id="default",
        threshold=0.6,
        contract=trace.contract,
    )
    trace.config = cfg
    assert trace.config is not None
    assert trace.config.bound_version == "0.7.0"
    assert trace.config.contract_hash is not None
    restored = RunTrace.model_validate_json(trace.model_dump_json())
    assert restored.config is not None
    assert restored.config.bound_version == "0.7.0"


def test_run_trace_reported_and_observed_action() -> None:
    """RunTrace carries reported/observed action fields (item 12)."""
    trace = _make_trace()
    assert trace.reported_action is None
    assert trace.observed_action is None
    trace.reported_action = "Implemented csv.DictWriter"
    trace.observed_action = "git diff confirmed new file"
    restored = RunTrace.model_validate_json(trace.model_dump_json())
    assert restored.reported_action == "Implemented csv.DictWriter"
    assert restored.observed_action == "git diff confirmed new file"


def test_render_from_trace_includes_policy_line() -> None:
    """The report records the policy id@version and hash when a config is set."""
    from bound.lineage import build_run_config
    from bound.policy_canon import compute_policy_hash
    from bound.policy_schema import load_policy_yaml

    REPO_ROOT = Path(__file__).resolve().parent.parent
    policy = load_policy_yaml(REPO_ROOT / "src" / "bound" / "default_policy.yaml")
    cfg = build_run_config(bound_version="0.7.0", policy=policy)
    trace = _make_trace()
    trace.config = cfg
    report = render_from_trace(trace)
    assert "Policy: `coding-default@1.0`" in report
    phash = compute_policy_hash(policy)
    assert f"hash `{phash}`" in report


def test_render_from_trace_omits_policy_without_config() -> None:
    """No policy line is fabricated when the trace carries no config snapshot."""
    trace = _make_trace()
    assert trace.config is None
    report = render_from_trace(trace)
    assert "Policy:" not in report



def test_run_trace_schema_version_is_2() -> None:
    """New traces default to schema_version 2.0."""
    trace = _make_trace()
    assert trace.schema_version == "2.0"


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


# ---------------------------------------------------------------------------
# v0.7 — provenance visibility in the markdown report (item 14)
# ---------------------------------------------------------------------------


def test_report_renders_evidence_provenance_and_coverage() -> None:
    """The report surfaces per-score provenance, candidate/final, assurance and coverage."""
    report = render_from_trace(_make_trace())
    # Provenance subsection with per-dimension breakdown.
    assert "### Evidence provenance" in report
    assert "Acceptance (A): `VERIFIED`" in report
    assert "Influence (I):" in report
    # Decision assurance subsection with candidate vs final + assurance.
    assert "### Decision assurance" in report
    assert "- Candidate decision:" in report
    assert "- Final decision:" in report
    assert "- Decision assurance:" in report
    # Run-summary coverage line.
    assert "Critical evidence coverage:" in report
    assert "independently verified" in report
    # The thin-harness rollback note is present.
    assert "thin harness" in report


def test_report_shows_collector_failures_and_missing_critical() -> None:
    """Unverifiable / invalid evidence and missing critical checks are surfaced."""
    contract = StepContract(
        id="PHASE-001",
        description="Contract with a decision-critical risk check.",
        goal="Verify collector-failure rendering.",
        acceptance_checks=[AcceptanceCheck(id="tests-pass", description="Suite green.")],
        risk_checks=[
            RiskCheck(
                id="no-critical-security-findings",
                description="No critical security findings.",
                severity=1.0,
                decision_critical=True,
            ),
        ],
        budget=StepBudget(max_retries=3, max_tool_calls=40),
    )
    evidence = ExecutionEvidence(
        acceptance=[
            CheckEvidence(
                check_id="tests-pass",
                passed=True,
                source="uv run pytest -q",
                provenance=EvidenceProvenance.VERIFIED,
                collector="bound.pytest",
            ),
        ],
        risks=[
            CheckEvidence(
                check_id="no-critical-security-findings",
                passed=None,
                source="bandit",
                provenance=EvidenceProvenance.MISSING,
                status=EvidenceStatus.INVALID,
                details="collector crash: bandit timed out",
            ),
        ],
        retry_count=EvidenceMetric(
            value=0, provenance=EvidenceProvenance.OBSERVED, source="harness.retries"
        ),
        tool_call_count=EvidenceMetric(
            value=0, provenance=EvidenceProvenance.OBSERVED, source="cline.tool_events"
        ),
    )
    result = evaluate_agent_step(contract, evidence, BoundCriteria(threshold=0.75))
    trace = RunTrace(
        plan_id="PHASE-001",
        step_id=contract.id,
        run_id="b" * 32,
        bound_version="0.7.0",
        timestamp="2026-01-01T00:00:00+00:00",
        contract=contract,
        evidence=evidence,
        evaluation=result.evaluation,
        next_action=result.next_action,
        feedback=result.feedback,
    )
    report = render_from_trace(trace)
    # The invalid collector evidence is surfaced as a collector failure.
    assert "Collector failures / unverifiable evidence:" in report
    assert "no-critical-security-findings" in report
    assert "invalid" in report
    # The decision-critical risk check has no verified evidence -> missing list.
    assert "Missing decision-critical evidence:" in report
    # Coverage is below 100% because the critical risk check is unverifiable.
    assert "0% independently verified" in report or "33% independently verified" in report
    # The provenance table marks the invalid check's status.
    assert "[invalid]" in report



