"""Versioned BOUND watch event contract (v0.8.0).

The **watch protocol** defines the JSONL events an agent (or adapter) streams
to ``bound watch`` over stdin.  It is deliberately distinct from the
:mod:`~bound.lineage` module, which models BOUND's *internal* lineage events
that live in ``events.jsonl`` inside ``.bound/runs/<run_id>/``.

Design rules
------------
* Every event carries a ``schema_version`` field so ``bound watch`` can
  validate or reject unrecognised versions before dispatching.
* Every event carries a ``task_id`` so the watcher can group events into a
  single BOUND run even when the watcher restarts.
* All timestamps are UTC ISO-8601 strings with ``Z`` suffix
  (e.g. ``2026-07-20T14:30:00Z``).
* The ``event`` field is the discriminator tag — ``task_started``,
  ``step_completed``, ``verification_requested``, ``verification_completed``,
  ``decision_emitted``, ``control_action_reported``,
  ``control_action_observed``, ``task_finished``.
* Unknown event tags produce a typed validation error — the watcher never
  silently ignores unrecognised events.
* All fields use strict Pydantic validation: ``extra='forbid'``, no
  coercion, no mutable defaults.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

__all__ = [
    "WATCH_EVENT_SCHEMA_VERSION",
    "WatchControlActionObservedEvent",
    "WatchControlActionReportedEvent",
    "WatchDecisionEmittedEvent",
    "WatchEvent",
    "WatchStepCompletedEvent",
    "WatchTaskFinishedEvent",
    "WatchTaskStartedEvent",
    "WatchVerificationCompletedEvent",
    "WatchVerificationRequestedEvent",
    "parse_watch_event",
]

#: Schema version for the watch event contract.  Bumped only when the
#: event shape changes in a backwards-incompatible way.
WATCH_EVENT_SCHEMA_VERSION: str = "1.0"

#: Set of recognised event tags in this version.
_WATCH_EVENT_TAGS: frozenset[str] = frozenset(
    {
        "task_started",
        "step_completed",
        "verification_requested",
        "verification_completed",
        "decision_emitted",
        "control_action_reported",
        "control_action_observed",
        "task_finished",
    }
)


class _WatchEventBase(BaseModel):
    """Base class shared by every watch event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = Field(default=WATCH_EVENT_SCHEMA_VERSION)
    event: str
    task_id: str = Field(min_length=1)
    timestamp: str = Field(min_length=1)
    sequence: int | None = None


# ---------------------------------------------------------------------------
# Concrete event types
# ---------------------------------------------------------------------------


class WatchTaskStartedEvent(_WatchEventBase):
    """The agent started (or re-started) a task.

    The watcher creates or re-opens a BOUND run for ``task_id``.
    """

    event: Literal["task_started"] = "task_started"
    goal: str = Field(min_length=1)
    plan: str | None = None
    context: str | None = None
    run_id: str | None = None


class WatchStepCompletedEvent(_WatchEventBase):
    """The agent completed a meaningful execution step.

    The watcher evaluates this against the policy's declared meaningful
    boundaries and triggers boundary evaluation.
    """

    event: Literal["step_completed"] = "step_completed"
    step_id: str = Field(min_length=1)
    description: str | None = None
    attempt: int = Field(default=1, ge=1)
    changed_files: list[str] | None = None
    tool_calls: int | None = None
    tokens_used: int | None = None
    duration_ms: int | None = None
    agent_self_reported_verification: bool | None = None


class WatchVerificationRequestedEvent(_WatchEventBase):
    """The agent requests independent verification for a step."""

    event: Literal["verification_requested"] = "verification_requested"
    step_id: str = Field(min_length=1)
    checks: list[str] | None = None


class WatchVerificationCompletedEvent(_WatchEventBase):
    """Results of an independent verification run."""

    event: Literal["verification_completed"] = "verification_completed"
    step_id: str = Field(min_length=1)
    check_results: dict[str, dict[str, object | None]] = Field(default_factory=dict)


class WatchDecisionEmittedEvent(_WatchEventBase):
    """The watcher emitted a structured control decision.

    Attributes:
        next_action: Agent control action — ``continue``, ``retry``,
            ``replan``, ``rollback``.
        decision: BOUND decision — ``ACCEPT``, ``RETRY``, ``REPLAN``, ``ROLLBACK``.
    """

    event: Literal["decision_emitted"] = "decision_emitted"
    step_id: str = Field(min_length=1)
    evaluation_id: str = Field(min_length=1)
    decision: str = Field(min_length=1)
    next_action: str = Field(min_length=1)
    score: float
    threshold: float
    assurance: str = Field(min_length=1)
    feedback: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    policy_id: str | None = None
    policy_version: str | None = None
    policy_hash: str | None = None


class WatchControlActionReportedEvent(_WatchEventBase):
    """The agent reports the control action it intends to take.

    Always ``CLAIMED`` provenance — the watcher may independently observe.
    """

    event: Literal["control_action_reported"] = "control_action_reported"
    step_id: str = Field(min_length=1)
    evaluation_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    note: str | None = None


class WatchControlActionObservedEvent(_WatchEventBase):
    """An independent hook observed the agent's actual control action."""

    event: Literal["control_action_observed"] = "control_action_observed"
    step_id: str = Field(min_length=1)
    evaluation_id: str = Field(min_length=1)
    intended_action: str = Field(min_length=1)
    observed_action: str = Field(min_length=1)
    matches_intended: bool | None = None
    note: str | None = None


class WatchTaskFinishedEvent(_WatchEventBase):
    """The agent finished (or was interrupted) the task.

    The watcher finalises the BOUND run.
    """

    event: Literal["task_finished"] = "task_finished"
    outcome: Literal["completed", "interrupted", "abandoned", "cancelled"]
    summary: str | None = None
    run_id: str | None = None
# ---------------------------------------------------------------------------
# Discriminated union + parser
# ---------------------------------------------------------------------------

WatchEvent = Annotated[
    (
        WatchTaskStartedEvent
        | WatchStepCompletedEvent
        | WatchVerificationRequestedEvent
        | WatchVerificationCompletedEvent
        | WatchDecisionEmittedEvent
        | WatchControlActionReportedEvent
        | WatchControlActionObservedEvent
        | WatchTaskFinishedEvent
    ),
    Field(discriminator="event"),
]

_WATCH_EVENT_ADAPTER: TypeAdapter[WatchEvent] = TypeAdapter(WatchEvent)


def parse_watch_event(data: str | bytes | dict[str, object]) -> WatchEvent:
    """Parse one watch event from a JSON string, bytes, or dict.

    Routes on the ``event`` discriminator tag to the correct concrete event
    type and validates it strictly (``extra='forbid'``).  Use this to read
    one line of the agent's JSONL stream.

    Args:
        data: A JSON string/bytes (one event) or an already-decoded dict.

    Returns:
        The validated concrete watch event instance.

    Raises:
        pydantic.ValidationError: If ``data`` is not a valid watch event.
    """
    if isinstance(data, dict):
        return _WATCH_EVENT_ADAPTER.validate_python(data)
    return _WATCH_EVENT_ADAPTER.validate_json(data)