"""Tests for the BOUND UI view-model layer (``bound.ui_models``).

Verifies that:
1. All models construct with valid defaults.
2. Enums have expected members.
3. PlanProgress.progress_ratio computes correctly.
4. Models reject invalid inputs through Pydantic validation.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from bound.ui_models import (
    ActiveRunCard,
    CandidateState,
    ExecutionState,
    PlanDivergence,
    PlanDivergenceType,
    PlanProgress,
    PlanStep,
    PlanStepOrigin,
    PlanStepStatus,
    PlanVsReality,
)


def _utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    """Create a timezone-aware UTC datetime."""
    return datetime(year, month, day, hour, tzinfo=UTC)


# =============================================================================
# Enum tests
# =============================================================================


class TestPlanStepStatus:
    """Tests for :class:`PlanStepStatus` enum members."""

    def test_all_expected_members_present(self) -> None:
        assert PlanStepStatus.PENDING.value == "pending"
        assert PlanStepStatus.ACTIVE.value == "active"
        assert PlanStepStatus.COMPLETED.value == "completed"
        assert PlanStepStatus.FAILED.value == "failed"
        assert PlanStepStatus.SKIPPED.value == "skipped"
        assert PlanStepStatus.BLOCKED.value == "blocked"
        assert PlanStepStatus.REPLANNED.value == "replanned"


class TestPlanStepOrigin:
    """Tests for :class:`PlanStepOrigin` enum members."""

    def test_all_expected_members_present(self) -> None:
        assert PlanStepOrigin.ORIGINAL.value == "original"
        assert PlanStepOrigin.INSERTED.value == "inserted"
        assert PlanStepOrigin.MODIFIED.value == "modified"
        assert PlanStepOrigin.REPLACEMENT.value == "replacement"


class TestPlanDivergenceType:
    """Tests for :class:`PlanDivergenceType` enum members."""

    def test_all_expected_members_present(self) -> None:
        assert PlanDivergenceType.INSERTED.value == "inserted"
        assert PlanDivergenceType.REMOVED.value == "removed"
        assert PlanDivergenceType.MODIFIED.value == "modified"
        assert PlanDivergenceType.REORDERED.value == "reordered"
        assert PlanDivergenceType.REPEATED.value == "repeated"
        assert PlanDivergenceType.FAILED.value == "failed"


# =============================================================================
# PlanStep model
# =============================================================================


class TestPlanStep:
    """Tests for :class:`PlanStep` construction and validation."""

    def test_minimal_construction(self) -> None:
        """A PlanStep needs only step_id and title."""
        step = PlanStep(step_id="PHASE-001", title="Write tests")
        assert step.step_id == "PHASE-001"
        assert step.title == "Write tests"
        assert step.ordinal == 1
        assert step.status == PlanStepStatus.PENDING
        assert step.origin == PlanStepOrigin.ORIGINAL
        assert step.depth == 0

    def test_ordinal_defaults_to_1(self) -> None:
        """First step gets ordinal 1 by default."""
        step = PlanStep(step_id="s1", title="T", ordinal=1)
        assert step.ordinal == 1

    def test_ordinal_must_be_positive(self) -> None:
        """Ordinal 0 or negative is rejected."""
        with pytest.raises(ValidationError):
            PlanStep(step_id="s1", title="T", ordinal=0)
        with pytest.raises(ValidationError):
            PlanStep(step_id="s1", title="T", ordinal=-1)

    def test_full_construction(self) -> None:
        """All optional fields can be set."""
        now = _utc(2025, 6, 1, 12)
        step = PlanStep(
            step_id="PHASE-002",
            title="Implement validator",
            description="Add input validation module",
            ordinal=2,
            depth=0,
            status=PlanStepStatus.ACTIVE,
            origin=PlanStepOrigin.ORIGINAL,
            parent_step_id="PHASE-001",
            source_line=42,
            linked_runtime_step_ids=["step-abc"],
            started_at=now,
            completed_at=None,
            decision="RETRY",
            attempt_count=2,
            acceptance_checks=["All tests pass", "Coverage >= 80%"],
        )
        assert step.step_id == "PHASE-002"
        assert step.attempt_count == 2
        assert len(step.acceptance_checks) == 2
        assert step.started_at == now

    def test_default_factories_are_distinct(self) -> None:
        """Default factory lists are per-instance, not shared."""
        a = PlanStep(step_id="a", title="A")
        b = PlanStep(step_id="b", title="B")
        a.linked_runtime_step_ids.append("x")
        assert b.linked_runtime_step_ids == []
        assert PlanDivergenceType.SKIPPED.value == "skipped"
        assert PlanDivergenceType.ROLLBACK.value == "rollback"


# =============================================================================
# PlanProgress model
# =============================================================================


class TestPlanProgress:
    """Tests for :class:`PlanProgress` construction and helpers."""

    def test_empty_progress_ratio_zero(self) -> None:
        """When there are no steps, progress_ratio returns 0.0."""
        progress = PlanProgress()
        assert progress.progress_ratio == 0.0

    def test_progress_ratio_with_steps(self) -> None:
        """Progress ratio = completed / total."""
        steps = [
            PlanStep(step_id="1", title="A", status=PlanStepStatus.COMPLETED),
            PlanStep(step_id="2", title="B", status=PlanStepStatus.COMPLETED),
            PlanStep(step_id="3", title="C", status=PlanStepStatus.PENDING),
        ]
        progress = PlanProgress(plan_steps=steps, total_steps=3, completed_steps=2, failed_steps=0)
        assert progress.progress_ratio == 2.0 / 3.0

    def test_progress_ratio_all_completed(self) -> None:
        """All completed gives 1.0."""
        progress = PlanProgress(total_steps=4, completed_steps=4)
        assert progress.progress_ratio == 1.0

    def test_active_step_index(self) -> None:
        """active_step_index is preserved as-is."""
        progress = PlanProgress(active_step_index=3)
        assert progress.active_step_index == 3


# =============================================================================
# PlanDivergence model
# =============================================================================


class TestPlanDivergence:
    """Tests for :class:`PlanDivergence`."""

    def test_minimal_construction(self) -> None:
        d = PlanDivergence(
            step_id="PHASE-003",
            change_type=PlanDivergenceType.INSERTED,
            description="Step added during replan",
        )
        assert d.change_type == PlanDivergenceType.INSERTED
        assert d.original_step_id is None

    def test_with_original_step_id(self) -> None:
        d = PlanDivergence(
            step_id="PHASE-003",
            change_type=PlanDivergenceType.MODIFIED,
            description="Changed acceptance criteria",
            original_step_id="PHASE-001",
        )
        assert d.original_step_id == "PHASE-001"


# =============================================================================
# PlanVsReality model
# =============================================================================


class TestPlanVsReality:
    """Tests for :class:`PlanVsReality`."""

    def test_empty_construction(self) -> None:
        pvr = PlanVsReality()
        assert pvr.original_steps == []
        assert pvr.current_steps == []
        assert pvr.divergences == []

    def test_with_divergence(self) -> None:
        orig = [PlanStep(step_id="1", title="Inspect")]
        curr = [
            PlanStep(step_id="1", title="Inspect"),
            PlanStep(step_id="2", title="Refactor", origin=PlanStepOrigin.INSERTED),
        ]
        div = [
            PlanDivergence(
                step_id="2",
                change_type=PlanDivergenceType.INSERTED,
                description="Added refactor step",
            )
        ]
        pvr = PlanVsReality(original_steps=orig, current_steps=curr, divergences=div)
        assert len(pvr.original_steps) == 1
        assert len(pvr.current_steps) == 2
        assert len(pvr.divergences) == 1


# =============================================================================
# ActiveRunCard model
# =============================================================================


class TestActiveRunCard:
    """Tests for :class:`ActiveRunCard`."""

    def test_minimal_construction(self) -> None:
        card = ActiveRunCard(run_id="run-001", task="Task", status="started")
        assert card.run_id == "run-001"
        assert card.status == "started"
        assert card.decision_color == "#616161"
        assert card.plan_progress_ratio == 0.0

    def test_full_construction(self) -> None:
        now = _utc(2025, 6, 1, 12)
        card = ActiveRunCard(
            run_id="run-001",
            task="Add registration validation",
            status="started",
            current_step="Implement validator",
            current_action="Running unit tests",
            decision="REPLAN",
            decision_color="#1565c0",
            started_at=now,
            candidate_label="A",
            plan_progress_ratio=0.5,
            plan_completed=3,
            plan_total=6,
            duration_seconds=138.0,
            last_activity_at=now,
        )
        assert card.current_step == "Implement validator"
        assert card.decision == "REPLAN"
        assert card.decision_color == "#1565c0"
        assert card.plan_completed == 3
        assert card.plan_total == 6


# =============================================================================
# ExecutionState model
# =============================================================================


class TestExecutionState:
    """Tests for :class:`ExecutionState`."""

    def test_minimal_construction(self) -> None:
        state = ExecutionState(run_id="run-001", task="Task", status="started")
        assert state.run_id == "run-001"
        assert isinstance(state.plan_progress, PlanProgress)
        assert state.latest_decision is None
        assert state.decision_reason is None

    def test_full_construction(self) -> None:
        now = _utc(2025, 6, 1, 12)
        steps = [
            PlanStep(step_id="1", title="Inspect", status=PlanStepStatus.COMPLETED),
            PlanStep(step_id="2", title="Implement", status=PlanStepStatus.ACTIVE),
        ]
        progress = PlanProgress(plan_steps=steps, total_steps=2, completed_steps=1)
        state = ExecutionState(
            run_id="run-001",
            task="Implement feature X",
            status="started",
            plan_progress=progress,
            current_step="Implement",
            current_action="Writing code",
            next_action="continue",
            latest_decision="ACCEPT",
            decision_reason="All checks passed",
            decision_color="#2e7d32",
            attempt_number=2,
            max_attempts=3,
            candidate_label="A",
            started_at=now,
        )
        assert state.latest_decision == "ACCEPT"
        assert state.decision_reason == "All checks passed"
        assert state.plan_progress.total_steps == 2
        assert state.attempt_number == 2


# =============================================================================
# CandidateState model
# =============================================================================


class TestCandidateState:
    """Tests for :class:`CandidateState`."""

    def test_minimal_construction(self) -> None:
        cs = CandidateState(candidate_id="cand-A")
        assert cs.candidate_id == "cand-A"
        assert cs.label == "A"
        assert cs.status == "pending"
        assert cs.decision is None

    def test_full_construction(self) -> None:
        cs = CandidateState(
            candidate_id="cand-B",
            label="B",
            status="running",
            decision="ACCEPT",
            plan_completed=4,
            plan_total=7,
        )
        assert cs.label == "B"
        assert cs.plan_completed == 4
        assert cs.plan_total == 7
