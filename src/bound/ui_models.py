"""View-model layer for the BOUND UI (v0.9.1).

Pure Pydantic data classes that represent UI state. Every screen renders from
these view models, never from raw lineage events. This makes state derivation
testable without HTML snapshots and keeps rendering code thin.

All types use Pydantic, Google docstrings, and type hints. No emoji characters
are used anywhere.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# Plan step enums
# =============================================================================


class PlanStepStatus(StrEnum):
    """Execution status of a single plan step.

    Members:
        PENDING: Step has not started execution.
        ACTIVE: Step is currently executing.
        COMPLETED: Step finished successfully.
        FAILED: Step finished with a failure.
        SKIPPED: Step was skipped (e.g. conditionally excluded).
        BLOCKED: Step cannot proceed due to a dependency.
        REPLANNED: Step was replaced by a replan version.
    """

    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    REPLANNED = "replanned"


class PlanStepOrigin(StrEnum):
    """How a step entered the plan.

    Members:
        ORIGINAL: Step was in the original plan at run start.
        INSERTED: Step was added by a replan.
        MODIFIED: Step was modified from an original step.
        REPLACEMENT: Step replaces an original step (the original is retired).
    """

    ORIGINAL = "original"
    INSERTED = "inserted"
    MODIFIED = "modified"
    REPLACEMENT = "replacement"


class PlanDivergenceType(StrEnum):
    """Category of divergence between original plan and actual execution.

    Members:
        INSERTED: A step was added that was not in the original plan.
        REMOVED: An original step was dropped.
        MODIFIED: A step's content or criteria changed.
        REORDERED: Step ordering changed.
        REPEATED: A step executed multiple times.
        FAILED: A step failed and required replan/retry.
        SKIPPED: A step was bypassed.
        ROLLBACK: Execution rolled back to a prior checkpoint.
    """

    INSERTED = "inserted"
    REMOVED = "removed"
    MODIFIED = "modified"
    REORDERED = "reordered"
    REPEATED = "repeated"
    FAILED = "failed"
    SKIPPED = "skipped"
    ROLLBACK = "rollback"
# =============================================================================
# Plan step view model
# =============================================================================


class PlanStep(BaseModel):
    """One plan step as rendered in the UI.

    Built from a combination of the parsed plan.md source and runtime
    execution data (linked steps, timings, decisions).

    Attributes:
        step_id: Stable identifier for this step (e.g. ``"PHASE-001"`` or
            a hash-derived id).
        title: Short human-readable title.
        description: Longer description text, or ``None``.
        ordinal: One-based position within the plan.
        depth: Nesting depth (0 = top-level phase, 1 = sub-step, etc.).
        status: Current :class:`PlanStepStatus`.
        origin: How this step entered the plan (:class:`PlanStepOrigin`).
        parent_step_id: Id of the parent step/phase, or ``None`` for
            top-level steps.
        source_line: Line number in the source plan.md, or ``None``.
        linked_runtime_step_ids: Runtime ``step_id`` values from lineage
            events that correspond to this plan step.
        started_at: UTC instant execution started, or ``None``.
        completed_at: UTC instant execution completed, or ``None``.
        decision: The latest BOUND decision for this step, or ``None``.
        attempt_count: Number of attempts on this step (1+).
        acceptance_checks: List of acceptance criteria extracted from the
            plan source.
    """

    model_config = ConfigDict(extra="forbid")

    step_id: str
    title: str
    description: str | None = None
    ordinal: int = Field(default=1, ge=1)
    depth: int = Field(default=0, ge=0)
    status: PlanStepStatus = PlanStepStatus.PENDING
    origin: PlanStepOrigin = PlanStepOrigin.ORIGINAL
    parent_step_id: str | None = None
    source_line: int | None = Field(default=None, ge=1)
    linked_runtime_step_ids: list[str] = Field(default_factory=list)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    decision: str | None = None
    attempt_count: int = Field(default=0, ge=0)
    acceptance_checks: list[str] = Field(default_factory=list)
# =============================================================================
# Plan progress
# =============================================================================


class PlanProgress(BaseModel):
    """Aggregated progress across all plan steps.

    Computed from the current set of :class:`PlanStep` instances. Provides
    completion counts and ratios for UI rendering.

    Attributes:
        plan_steps: The full ordered list of plan steps with their current
            statuses.
        total_steps: Count of non-skipped steps (the denominator for
            progress).
        completed_steps: Count of completed steps.
        failed_steps: Count of failed steps.
        active_step_index: Ordinal of the currently-active step, or ``None``
            when no step is active.
    """

    model_config = ConfigDict(extra="forbid")

    plan_steps: list[PlanStep] = Field(default_factory=list)
    total_steps: int = 0
    completed_steps: int = 0
    failed_steps: int = 0
    active_step_index: int | None = None

    @property
    def progress_ratio(self) -> float:
        """Fraction of steps completed, in ``[0.0, 1.0]``.

        Returns 0.0 when there are no steps.
        """
        if self.total_steps == 0:
            return 0.0
        return self.completed_steps / self.total_steps


# =============================================================================
# Plan divergence
# =============================================================================


class PlanDivergence(BaseModel):
    """One detected divergence between the original plan and actual execution.

    Attributes:
        step_id: The step identifier in the *current* plan that diverged.
        change_type: Category of divergence (:class:`PlanDivergenceType`).
        description: Human-readable explanation of the divergence.
        original_step_id: The original step id before the change, when
            applicable.
    """

    model_config = ConfigDict(extra="forbid")

    step_id: str
    change_type: PlanDivergenceType
    description: str
    original_step_id: str | None = None


class PlanVsReality(BaseModel):
    """Side-by-side comparison of the original plan and actual execution.

    Captures the current state of divergence so the UI can render a
    two-column comparison without recomputing diffs.

    Attributes:
        original_steps: The plan steps as they existed at run start.
        current_steps: The plan steps after all replans and insertions.
        divergences: List of detected divergences between original and current.
    """

    model_config = ConfigDict(extra="forbid")

    original_steps: list[PlanStep] = Field(default_factory=list)
    current_steps: list[PlanStep] = Field(default_factory=list)
    divergences: list[PlanDivergence] = Field(default_factory=list)
# =============================================================================
# Home screen models
# =============================================================================


class ActiveRunCard(BaseModel):
    """View model for one active-run card on the home screen.

    Provides enough information to render the card without querying the
    detail log. See todo-ui.md section 3.3 for the card specification.

    Attributes:
        run_id: The lineage run identifier.
        task: Human-readable task description.
        status: Current run status label (``"started"``, ``"completed"``,
            ``"interrupted"``, ``"failed"``).
        current_step: Title of the currently-active plan step, or ``None``.
        current_action: Description of the current action being performed,
            or ``None``.
        decision: Latest BOUND decision label, or ``None`` when no decision
            has been made yet.
        decision_color: CSS hex colour for the decision badge.
        started_at: UTC instant the run started.
        candidate_label: Candidate identifier label (e.g. ``"A"``), or
            ``None``.
        plan_progress_ratio: Plan completion fraction in ``[0.0, 1.0]``.
        plan_completed: Count of completed plan steps.
        plan_total: Total count of plan steps.
        duration_seconds: Elapsed wall-clock seconds since start, or
            ``None``.
        last_activity_at: UTC instant of the most recent lineage event,
            or ``None``.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    task: str
    status: str
    current_step: str | None = None
    current_action: str | None = None
    decision: str | None = None
    decision_color: str = "#616161"
    started_at: datetime | None = None
    candidate_label: str | None = None
    plan_progress_ratio: float = 0.0
    plan_completed: int = 0
    plan_total: int = 0
    duration_seconds: float | None = None
    last_activity_at: datetime | None = None


# =============================================================================
# Detail / Execution screen models
# =============================================================================


class ExecutionState(BaseModel):
    """View model for the run detail Execution tab.

    Captures everything the execution screen needs in one place. See
    todo-ui.md sections 4.3-4.4 for the specification.

    Attributes:
        run_id: The lineage run identifier.
        task: Human-readable task description.
        status: Current run status label.
        plan_progress: Aggregated :class:`PlanProgress` for the current plan.
        current_step: Title of the currently-active plan step, or ``None``.
        current_action: Description of the current action being performed,
            or ``None``.
        next_action: The mapped next action from the latest outcome, or
            ``None``.
        latest_decision: Latest BOUND decision label, or ``None``.
        decision_reason: Human-readable reason for the latest decision, or
            ``None``.
        decision_color: CSS hex colour for the decision badge.
        attempt_number: Current attempt number (1-based), or ``None``.
        max_attempts: Maximum allowed attempts, or ``None`` when unbounded.
        candidate_label: Candidate identifier label, or ``None``.
        started_at: UTC instant the run started.
        finished_at: UTC instant the run finished, or ``None``.
        duration_seconds: Elapsed wall-clock seconds, or ``None``.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    task: str
    status: str
    plan_progress: PlanProgress = Field(default_factory=PlanProgress)
    current_step: str | None = None
    current_action: str | None = None
    next_action: str | None = None
    latest_decision: str | None = None
    decision_reason: str | None = None
    decision_color: str = "#616161"
    attempt_number: int | None = None
    max_attempts: int | None = None
    candidate_label: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float | None = None


# =============================================================================
# Candidate state (placeholder for future multi-candidate support)
# =============================================================================


class CandidateState(BaseModel):
    """Placeholder view model for future candidate support.

    Even when only one candidate exists, the UI references a candidate so
    that introducing branching later requires no UI redesign. See
    todo-ui.md section 8.

    Attributes:
        candidate_id: Unique candidate identifier.
        label: Short display label (e.g. ``"A"``, ``"B"``).
        status: Current status of this candidate (``"running"``,
            ``"pending"``, ``"failed"``, etc.).
        decision: Latest BOUND decision for this candidate, or ``None``.
        plan_completed: Completed step count for this candidate.
        plan_total: Total step count for this candidate.
    """

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    label: str = "A"
    status: str = "pending"
    decision: str | None = None
    plan_completed: int = 0
    plan_total: int = 0


# =============================================================================
# Public API
# =============================================================================

__all__ = [
    "ActiveRunCard",
    "CandidateState",
    "ExecutionState",
    "PlanDivergence",
    "PlanDivergenceType",
    "PlanProgress",
    "PlanStep",
    "PlanStepOrigin",
    "PlanStepStatus",
    "PlanVsReality",
]