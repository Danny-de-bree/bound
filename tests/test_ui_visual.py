"""Visual regression tests for the BOUND dashboard UI (``bound.ui``).

Verifies that:
1. HTML rendering is deterministic (same inputs → same output).
2. The overview page renders correctly (empty state, populated).
3. The run detail page renders correctly.
4. All public rendering functions accept typed inputs and return strings.
"""

from __future__ import annotations

from datetime import UTC, datetime

from bound.lineage import (
    Attempt,
    Evaluation,
    Outcome,
    OutcomeRecordedEvent,
    ReasonCode,
    Run,
    RunFinishedEvent,
    RunFinishStatus,
    RunStartedEvent,
    RunStatus,
    Step,
    StepStartedEvent,
    StepStatus,
    generate_event_id,
)
from bound.lineage_store import RunLog, RunSummary
from bound.models import EvaluationScores
from bound.ui import (
    _render_overview_page,
    _render_run_detail,
)

# =========================================================================
# Test data factories
# =========================================================================


def _utc(
    year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0
) -> datetime:
    """Create a timezone-aware UTC datetime."""
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC)


def _make_run_summary(
    run_id: str = "run-001",
    task: str = "Implement feature X",
    status: RunStatus = RunStatus.STARTED,
    step_count: int = 1,
    event_count: int = 5,
    incomplete: bool = False,
    finished: bool = False,
) -> RunSummary:
    """Create a minimal RunSummary for UI rendering tests."""
    return RunSummary(
        run_id=run_id,
        task=task,
        schema_version="1.1",
        started_at=_utc(2025, 6, 1, 12, 0, 0),
        finished_at=_utc(2025, 6, 1, 12, 30, 0) if finished else None,
        status=status,
        step_count=step_count,
        event_count=event_count,
        incomplete=incomplete,
        path="/tmp/.bound/runs/run-001",
    )


def _make_minimal_run_log() -> RunLog:
    """Create a minimal RunLog with one step and one evaluation."""
    run = Run(
        run_id="run-001",
        task="Implement feature X",
        schema_version="1.1",
        started_at=_utc(2025, 6, 1, 12, 0, 0),
        finished_at=_utc(2025, 6, 1, 12, 30, 0),
        status=RunStatus.COMPLETED,
        step_ids=["step-001"],
    )
    step = Step(
        step_id="step-001",
        run_id="run-001",
        contract_id="PHASE-001",
        description="Write tests",
        started_at=_utc(2025, 6, 1, 12, 5, 0),
        finished_at=_utc(2025, 6, 1, 12, 25, 0),
        status=StepStatus.COMPLETED,
        attempts=[
            Attempt(
                attempt=1,
                started_at=_utc(2025, 6, 1, 12, 5, 0),
                evaluation_id="eval-001",
            )
        ],
    )
    evaluation = Evaluation(
        evaluation_id="eval-001",
        run_id="run-001",
        step_id="step-001",
        attempt=1,
        scores=EvaluationScores(
            acceptance=0.9, influence=0.2, risk=0.1, cost=0.2
        ),
        score=0.85,
        threshold=0.6,
        decision="ACCEPT",
        reason_code=ReasonCode.ALL_CHECKS_PASSED,
        recorded_at=_utc(2025, 6, 1, 12, 25, 0),
    )
    outcome = Outcome(
        run_id="run-001",
        step_id="step-001",
        evaluation_id="eval-001",
        decision="ACCEPT",
        next_action="continue",
        reason_code=ReasonCode.ACCEPT,
        recorded_at=_utc(2025, 6, 1, 12, 26, 0),
    )
    _t = _utc  # shorthand
    return RunLog(
        run=run,
        steps=[step],
        evaluations=[evaluation],
        outcomes=[outcome],
        events=[
            RunStartedEvent(
                event_id=generate_event_id(run_id="run-001", sequence=1),
                run_id="run-001",
                task="Implement feature X",
                timestamp=_t(2025, 6, 1, 12, 0, 0),
                schema_version="1.1",
            ),
            StepStartedEvent(
                event_id=generate_event_id(run_id="run-001", sequence=2),
                run_id="run-001",
                step_id="step-001",
                contract_id="PHASE-001",
                attempt=1,
                timestamp=_t(2025, 6, 1, 12, 5, 0),
                schema_version="1.1",
            ),
            OutcomeRecordedEvent(
                event_id=generate_event_id(run_id="run-001", sequence=4),
                run_id="run-001",
                step_id="step-001",
                evaluation_id="eval-001",
                decision="ACCEPT",
                next_action="continue",
                reason_code=ReasonCode.ACCEPT,
                timestamp=_t(2025, 6, 1, 12, 26, 0),
                schema_version="1.1",
            ),
            RunFinishedEvent(
                event_id=generate_event_id(run_id="run-001", sequence=5),
                run_id="run-001",
                status=RunFinishStatus.COMPLETED,
                reason_code=ReasonCode.ACCEPT,
                timestamp=_t(2025, 6, 1, 12, 30, 0),
                schema_version="1.1",
            ),
        ],
        incomplete=False,
    )


# =========================================================================
# 1. Deterministic HTML rendering
# =========================================================================


def test_overview_page_empty_is_deterministic() -> None:
    """Rendering the empty overview twice produces identical HTML."""
    html_1 = _render_overview_page([], "/tmp/.bound/runs")
    html_2 = _render_overview_page([], "/tmp/.bound/runs")
    assert html_1 == html_2, "Empty overview is not deterministic"
    assert "No BOUND runs yet" in html_1


def test_overview_page_populated_is_deterministic() -> None:
    """Rendering a populated overview twice produces identical HTML."""
    summaries = [
        _make_run_summary("run-001", "Implement feature X", RunStatus.COMPLETED),
        _make_run_summary("run-002", "Fix bug Y", RunStatus.STARTED),
    ]
    html_1 = _render_overview_page(summaries, "/tmp/.bound/runs")
    html_2 = _render_overview_page(summaries, "/tmp/.bound/runs")
    assert html_1 == html_2, "Populated overview is not deterministic"


def test_run_detail_is_deterministic() -> None:
    """Rendering a run detail page twice produces identical HTML."""
    log = _make_minimal_run_log()
    html_1 = _render_run_detail(log)
    html_2 = _render_run_detail(log)
    assert html_1 == html_2, "Run detail page is not deterministic"


# =========================================================================
# 2. HTML structure
# =========================================================================


def test_overview_page_empty_has_doctype_and_structure() -> None:
    """Empty overview must be valid HTML with DOCTYPE."""
    html = _render_overview_page([], "/tmp/.bound/runs")
    assert html.startswith("<!DOCTYPE html>")
    assert "<html" in html
    assert "</html>" in html
    assert "<title>BOUND dashboard</title>" in html
    assert "No BOUND runs yet" in html
    assert "local read-only" in html


def test_overview_page_populated_shows_runs() -> None:
    """Populated overview must list runs."""
    summaries = [
        _make_run_summary("run-001", "Implement feature X", RunStatus.COMPLETED),
        _make_run_summary("run-002", "Fix bug Y", RunStatus.STARTED),
    ]
    html = _render_overview_page(summaries, "/tmp/.bound/runs")
    assert "run-001" in html
    assert "run-002" in html
    assert "Implement feature X" in html
    assert "Fix bug Y" in html
    assert "2 run(s)" in html


def test_run_detail_has_doctype_and_structure() -> None:
    """Run detail must be valid HTML."""
    log = _make_minimal_run_log()
    html = _render_run_detail(log)
    assert html.startswith("<!DOCTYPE html>")
    assert "<html" in html
    assert "</html>" in html
    assert "BOUND run" in html
    assert "PHASE-001" in html
    assert "ACCEPT" in html


# =========================================================================
# 3. HTML content stability
# =========================================================================


def test_overview_html_matches_snapshot_on_same_inputs() -> None:
    """Same inputs must produce identical HTML across test runs."""
    summaries = [
        _make_run_summary("run-001", "Implement feature X",
                          RunStatus.COMPLETED, finished=True),
    ]
    html = _render_overview_page(summaries, "/tmp/.bound/runs")
    assert "<a href='/run/run-001'" in html
    assert "class='run-card'" in html
    assert "completed" in html
    assert "1 step(s)" in html


def test_run_detail_shows_decision_tree() -> None:
    """Run detail must show the decision tree."""
    log = _make_minimal_run_log()
    html = _render_run_detail(log)
    assert "ACCEPT" in html
    assert "PHASE-001" in html
    assert "continue" in html