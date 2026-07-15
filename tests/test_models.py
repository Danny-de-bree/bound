"""Unit tests for the BOUND Pydantic domain models (Phase 1).

These tests focus on validation *behaviour* — the function of the models —
rather than re-testing Pydantic's type machinery. Edge cases (empty strings,
boundary values, out-of-range scores) are asserted because they encode the
BOUND contract:

* actions must carry meaningful content (no empty/whitespace fields),
* each score dimension must stay within its defined range,
* weight must be non-negative (and may exceed ``1.0``),
* threshold must remain unbounded above (``S`` is not restricted to ``[0, 1]``).
"""

from __future__ import annotations

from typing import get_args

import pytest
from pydantic import ValidationError

from bound.models import (
    Action,
    BoundCriteria,
    Decision,
    EvaluationResult,
    EvaluationScores,
)

# ---------------------------------------------------------------------------
# Action
# ---------------------------------------------------------------------------


def test_action_valid_without_context() -> None:
    """A well-formed Action validates and defaults context to None."""
    action = Action(description="Book the direct flight", goal="Travel to New York")
    assert action.description == "Book the direct flight"
    assert action.goal == "Travel to New York"
    assert action.context is None


def test_action_valid_with_context() -> None:
    """Context is optional and may carry arbitrary text."""
    action = Action(description="Book flight", goal="Travel", context="Budget: 1200")
    assert action.context == "Budget: 1200"


@pytest.mark.parametrize(
    ("description", "goal"),
    [
        ("", "valid goal"),
        ("   ", "valid goal"),
        ("\t\n  ", "valid goal"),
        ("valid action", ""),
        ("valid action", "   "),
        ("valid action", "\t\n"),
    ],
)
def test_action_rejects_empty_or_whitespace(description: str, goal: str) -> None:
    """Empty or whitespace-only description/goal must be rejected.

    This matters because a BOUND evaluation is meaningless without a concrete
    action and goal to score against.
    """
    with pytest.raises(ValidationError):
        Action(description=description, goal=goal)


# ---------------------------------------------------------------------------
# EvaluationScores
# ---------------------------------------------------------------------------


def test_scores_valid_with_reasoning() -> None:
    """All four dimensions accept their canonical mid-range values."""
    scores = EvaluationScores(
        acceptance=0.9,
        influence=0.2,
        risk=0.1,
        cost=0.2,
        reasoning="Direct flight satisfies the goal with low risk.",
    )
    assert scores.acceptance == 0.9
    assert scores.influence == 0.2
    assert scores.risk == 0.1
    assert scores.cost == 0.2
    assert scores.reasoning is not None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("acceptance", 0.0),
        ("acceptance", 1.0),
        ("influence", -1.0),
        ("influence", 1.0),
        ("risk", 0.0),
        ("risk", 1.0),
        ("cost", 0.0),
        ("cost", 1.0),
    ],
)
def test_scores_accept_boundary_values(field: str, value: float) -> None:
    """The inclusive range endpoints must be accepted for every dimension."""
    kwargs: dict[str, float | str | None] = {
        "acceptance": 0.5,
        "influence": 0.0,
        "risk": 0.5,
        "cost": 0.5,
    }
    kwargs[field] = value
    scores = EvaluationScores(**kwargs)  # type: ignore[arg-type]
    assert getattr(scores, field) == value


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("acceptance", -0.01),
        ("acceptance", 1.01),
        ("influence", -1.01),
        ("influence", 1.01),
        ("risk", -0.01),
        ("risk", 1.01),
        ("cost", -0.01),
        ("cost", 1.01),
    ],
)
def test_scores_reject_out_of_range(field: str, value: float) -> None:
    """Values just outside the defined range must be rejected by Pydantic."""
    kwargs: dict[str, float | str | None] = {
        "acceptance": 0.5,
        "influence": 0.0,
        "risk": 0.5,
        "cost": 0.5,
    }
    kwargs[field] = value
    with pytest.raises(ValidationError):
        EvaluationScores(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# BoundCriteria — weight and threshold
# ---------------------------------------------------------------------------


def test_criteria_default_weight() -> None:
    """Weight defaults to 1.0 when omitted."""
    criteria = BoundCriteria(threshold=0.6)
    assert criteria.weight == 1.0
    assert criteria.threshold == 0.6


def test_criteria_positive_weight_allowed() -> None:
    """Any non-negative weight is permitted, including values above 1.0."""
    criteria = BoundCriteria(threshold=0.6, weight=2.5)
    assert criteria.weight == 2.5


def test_criteria_zero_weight_allowed() -> None:
    """Zero weight is a valid edge case (acceptance is effectively ignored)."""
    criteria = BoundCriteria(threshold=0.6, weight=0.0)
    assert criteria.weight == 0.0


def test_criteria_rejects_negative_weight() -> None:
    """Negative weight would invert the acceptance signal and is forbidden."""
    with pytest.raises(ValidationError):
        BoundCriteria(threshold=0.6, weight=-0.1)


def test_criteria_rejects_negative_threshold() -> None:
    """A threshold below zero is nonsensical and rejected."""
    with pytest.raises(ValidationError):
        BoundCriteria(threshold=-0.1)


def test_criteria_allows_threshold_above_one() -> None:
    """Threshold is intentionally unbounded above 1.0 because S can exceed 1.

    Example: with ``W = 2.0`` and ``A = 1.0`` the score ``S`` can reach 2.0+,
    so a threshold of ``2.0`` is a legitimate acceptance bar.
    """
    criteria = BoundCriteria(threshold=2.0, weight=2.0)
    assert criteria.threshold == 2.0


def test_criteria_allows_large_threshold() -> None:
    """No upper cap on threshold; large values are permitted."""
    criteria = BoundCriteria(threshold=3.0, weight=2.0)
    assert criteria.threshold == 3.0


# ---------------------------------------------------------------------------
# Decision literal
# ---------------------------------------------------------------------------


def test_decision_literal_values() -> None:
    """The Decision literal exposes exactly the four BOUND outcomes."""
    assert get_args(Decision) == ("ACCEPT", "RETRY", "REPLAN", "ROLLBACK")


# ---------------------------------------------------------------------------
# EvaluationResult
# ---------------------------------------------------------------------------


def _example_scores() -> EvaluationScores:
    return EvaluationScores(acceptance=0.9, influence=0.2, risk=0.1, cost=0.2)


def test_evaluation_result_constructs_with_components() -> None:
    """A fully-specified result reconstructs the README example S = 0.8."""
    result = EvaluationResult(
        scores=_example_scores(),
        weight=1.0,
        threshold=0.6,
        acceptance_component=0.9,
        influence_component=0.2,
        risk_component=0.1,
        cost_component=0.2,
        score=0.8,
        decision="ACCEPT",
    )
    assert result.score == pytest.approx(0.8)
    assert result.decision == "ACCEPT"
    assert result.scores.acceptance == 0.9


def test_evaluation_result_rejects_invalid_decision() -> None:
    """A decision outside the Decision literal must be rejected."""
    with pytest.raises(ValidationError):
        EvaluationResult(
            scores=_example_scores(),
            weight=1.0,
            threshold=0.6,
            acceptance_component=0.9,
            influence_component=0.2,
            risk_component=0.1,
            cost_component=0.2,
            score=0.8,
            decision="MAYBE",
        )


@pytest.mark.parametrize("decision", ["ACCEPT", "RETRY", "REPLAN", "ROLLBACK"])
def test_evaluation_result_accepts_all_decisions(decision: str) -> None:
    """Every member of the Decision literal is a valid result decision."""
    result = EvaluationResult(
        scores=_example_scores(),
        weight=1.0,
        threshold=0.6,
        acceptance_component=0.9,
        influence_component=0.2,
        risk_component=0.1,
        cost_component=0.2,
        score=0.8,
        decision=decision,  # type: ignore[arg-type]
    )
    assert result.decision == decision


def test_evaluation_result_rejects_extra_fields() -> None:
    """Strict models forbid unexpected fields to keep results auditable."""
    with pytest.raises(ValidationError):
        EvaluationResult(
            scores=_example_scores(),
            weight=1.0,
            threshold=0.6,
            acceptance_component=0.9,
            influence_component=0.2,
            risk_component=0.1,
            cost_component=0.2,
            score=0.8,
            decision="ACCEPT",
            surprise="nope",  # type: ignore[call-arg]
        )
