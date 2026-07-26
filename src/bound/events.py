"""Formal Pydantic event types for BOUND (v0.9.0).

Provides a stable public event schema so external code can parse BOUND
lineage and watch events without importing internal modules.  All event
types use Pydantic discriminated unions keyed on ``event``.

Usage::

    from bound.events import BoundEvent, parse_bound_event

    event = parse_bound_event({"event": "run_started", ...})
    match event:
        case RunStartedEvent():
            print(f"Run {event.run_id} started")
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

# =========================================================================
# Schema version
# =========================================================================

PUBLIC_EVENT_SCHEMA_VERSION: str = "1.0"


# =========================================================================
# Base
# =========================================================================


class _BoundEventBase(BaseModel):
    """Base class shared by every public BOUND event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = Field(default=PUBLIC_EVENT_SCHEMA_VERSION)
    event: str


# =========================================================================
# Run lifecycle events
# =========================================================================


class RunStartedEvent(_BoundEventBase):
    """A new lineage run was started."""

    event: Literal["run_started"] = "run_started"  # type: ignore[assignment]
    run_id: str = Field(min_length=1)
    task: str = Field(min_length=1)
    timestamp: str = Field(min_length=1)
    metadata: dict[str, str] = Field(default_factory=dict)


class RunFinishedEvent(_BoundEventBase):
    """A lineage run was finished."""

    event: Literal["run_finished"] = "run_finished"  # type: ignore[assignment]
    run_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    timestamp: str = Field(min_length=1)
    note: str | None = None


# =========================================================================
# Step lifecycle events
# =========================================================================


class StepStartedEvent(_BoundEventBase):
    """A step within a run was started."""

    event: Literal["step_started"] = "step_started"  # type: ignore[assignment]
    run_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    contract_id: str = Field(min_length=1)
    attempt: int = Field(default=1, ge=1)
    description: str | None = None
    timestamp: str = Field(min_length=1)


# =========================================================================
# Evaluation events
# =========================================================================


class EvaluationRecordedEvent(_BoundEventBase):
    """An evaluation result was recorded for a step."""

    event: Literal["evaluation_recorded"] = "evaluation_recorded"  # type: ignore[assignment]
    run_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    evaluation_id: str = Field(min_length=1)
    attempt: int = Field(default=1, ge=1)
    score: float
    threshold: float
    decision: str = Field(min_length=1)
    reason_code: str = Field(min_length=1)
    timestamp: str = Field(min_length=1)


class EvidenceCollectedEvent(_BoundEventBase):
    """Evidence was collected for a check within an evaluation."""

    event: Literal["evidence_collected"] = "evidence_collected"  # type: ignore[assignment]
    run_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    evaluation_id: str = Field(min_length=1)
    check_id: str = Field(min_length=1)
    provenance: str = Field(min_length=1)
    passed: bool
    status: str | None = None
    collector: str | None = None
    collector_version: str | None = None
    source: str | None = None
    artifact_hash: str | None = None
    timestamp: str = Field(min_length=1)


# =========================================================================
# Outcome events
# =========================================================================


class OutcomeRecordedEvent(_BoundEventBase):
    """An outcome was recorded for a step evaluation."""

    event: Literal["outcome_recorded"] = "outcome_recorded"  # type: ignore[assignment]
    run_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    evaluation_id: str = Field(min_length=1)
    decision: str = Field(min_length=1)
    next_action: str = Field(min_length=1)
    reason_code: str = Field(min_length=1)
    note: str | None = None
    timestamp: str = Field(min_length=1)


# =========================================================================
# Decision gating events
# =========================================================================


class ActionReportedEvent(_BoundEventBase):
    """An agent reported the action it intends to take."""

    event: Literal["action_reported"] = "action_reported"  # type: ignore[assignment]
    run_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    evaluation_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    note: str | None = None
    timestamp: str = Field(min_length=1)


class DecisionGatedEvent(_BoundEventBase):
    """A final gated decision was emitted."""

    event: Literal["decision_gated"] = "decision_gated"  # type: ignore[assignment]
    run_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    evaluation_id: str = Field(min_length=1)
    decision: str = Field(min_length=1)
    next_action: str = Field(min_length=1)
    score: float
    threshold: float
    assurance: str = Field(min_length=1)
    feedback: str = Field(min_length=1)
    policy_id: str | None = None
    policy_version: str | None = None
    policy_hash: str | None = None
    timestamp: str = Field(min_length=1)


class EvidenceCollectionFailedEvent(_BoundEventBase):
    """Evidence collection failed for a check."""

    event: Literal["evidence_collection_failed"] = "evidence_collection_failed"  # type: ignore[assignment]
    run_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    check_id: str = Field(min_length=1)
    error: str = Field(min_length=1)
    timestamp: str = Field(min_length=1)


# =========================================================================
# Discriminated union + parser
# =========================================================================

BoundEvent = Annotated[
    (
        RunStartedEvent
        | RunFinishedEvent
        | StepStartedEvent
        | EvaluationRecordedEvent
        | EvidenceCollectedEvent
        | OutcomeRecordedEvent
        | ActionReportedEvent
        | DecisionGatedEvent
        | EvidenceCollectionFailedEvent
    ),
    Field(discriminator="event"),
]

_BOUND_EVENT_ADAPTER: TypeAdapter[BoundEvent] = TypeAdapter(BoundEvent)


def parse_bound_event(data: str | bytes | dict[str, object]) -> BoundEvent:
    """Parse one BOUND event from a JSON string, bytes, or dict.

    Routes on the ``event`` discriminator tag to the correct concrete event
    type and validates it strictly (``extra='forbid'``).

    Args:
        data: A JSON string/bytes (one event) or an already-decoded dict.

    Returns:
        The validated concrete :class:`BoundEvent` instance.

    Raises:
        pydantic.ValidationError: If ``data`` is not a valid BOUND event.
    """
    if isinstance(data, dict):
        return _BOUND_EVENT_ADAPTER.validate_python(data)
    return _BOUND_EVENT_ADAPTER.validate_json(data)


__all__ = [
    "ActionReportedEvent",
    "BoundEvent",
    "DecisionGatedEvent",
    "EvaluationRecordedEvent",
    "EvidenceCollectedEvent",
    "EvidenceCollectionFailedEvent",
    "OutcomeRecordedEvent",
    "PUBLIC_EVENT_SCHEMA_VERSION",
    "RunFinishedEvent",
    "RunStartedEvent",
    "StepStartedEvent",
    "parse_bound_event",
]
