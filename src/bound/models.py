"""Pydantic domain models for the BOUND bounded-utility policy.

This module defines the core data structures that flow through the BOUND
pipeline:

    Action → Evaluator → EvaluationScores → BoundCalculator → BoundPolicy → EvaluationResult

All models are deliberately provider-agnostic and deterministic. Nothing here
performs network access or depends on any LLM SDK.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Action(BaseModel):
    """A proposed action to be evaluated by the BOUND policy.

    Attributes:
        description: Human-readable description of the proposed action. Must
            not be empty or whitespace-only.
        goal: The larger goal the action is meant to advance. Must not be
            empty or whitespace-only.
        context: Optional additional context influencing the evaluation.
    """

    model_config = ConfigDict(extra="forbid")

    description: str
    goal: str
    context: str | None = None

    @field_validator("description", "goal")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        """Reject empty or whitespace-only strings.

        A BOUND evaluation is meaningless without a concrete action and goal
        to score against, so both fields must carry actual content.

        Args:
            value: The raw string supplied for the field.

        Returns:
            The validated, unmodified string.

        Raises:
            ValueError: If the string is empty or contains only whitespace.
        """
        if not value or not value.strip():
            raise ValueError("must not be empty or whitespace-only")
        return value


class BoundCriteria(BaseModel):
    """Threshold and goal weight for a BOUND evaluation.

    The threshold is intentionally **not** capped at ``1.0``. The final score
    ``S = (W × A) + I - R - C`` is unbounded above, so a legitimate threshold
    may exceed ``1.0`` (for example when ``W > 1``).

    Attributes:
        threshold: Minimum score ``T`` required to accept the action
            (``S >= T``). Must be non-negative but may exceed ``1.0``.
        weight: Goal weight ``W`` applied to the acceptance score. Defaults
            to ``1.0`` and must be non-negative.
    """

    model_config = ConfigDict(extra="forbid")

    threshold: float = Field(ge=0.0)
    weight: float = Field(default=1.0, ge=0.0)


class EvaluationScores(BaseModel):
    """The four BOUND evaluation dimensions produced by an evaluator.

    Attributes:
        acceptance: ``A ∈ [0, 1]`` — how well the action satisfies the goal.
        influence: ``I ∈ [-1, 1]`` — downstream effect on future goals. May be
            negative (penalty) or positive (bonus); it is not a pure penalty.
        risk: ``R ∈ [0, 1]`` — potential downside if the action goes wrong.
        cost: ``C ∈ [0, 1]`` — normalized resource consumption.
        reasoning: Optional human-readable justification for the scores.
    """

    model_config = ConfigDict(extra="forbid")

    acceptance: float = Field(ge=0.0, le=1.0)
    influence: float = Field(ge=-1.0, le=1.0)
    risk: float = Field(ge=0.0, le=1.0)
    cost: float = Field(ge=0.0, le=1.0)
    reasoning: str | None = None


Decision = Literal["ACCEPT", "RETRY", "REPLAN", "ROLLBACK"]


class EvaluationResult(BaseModel):
    """The auditable outcome of a BOUND evaluation.

    Stores both the final score ``S`` and its individual components so the
    calculation ``S = (W × A) + I - R - C`` can be reconstructed and inspected
    from the result alone.

    Attributes:
        scores: The original :class:`EvaluationScores`.
        weight: Goal weight ``W`` used in the calculation.
        threshold: Acceptance threshold ``T``.
        acceptance_component: ``W × A``.
        influence_component: ``I``.
        risk_component: ``R``.
        cost_component: ``C``.
        score: Final bounded utility score ``S`` (unclamped, unrounded).
        decision: The BOUND decision derived from comparing ``S`` to ``T``.
    """

    model_config = ConfigDict(extra="forbid")

    scores: EvaluationScores
    weight: float
    threshold: float

    acceptance_component: float
    influence_component: float
    risk_component: float
    cost_component: float

    score: float
    decision: Decision
