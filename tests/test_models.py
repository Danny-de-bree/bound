from __future__ import annotations

from typing import get_args

import pytest
from pydantic import ValidationError

from bound.evaluator import StaticEvaluator
from bound.models import (
    Action,
    AgentStep,
    AgentTrajectory,
    BoundCriteria,
    BoundWeights,
    CodingWorkflowSignals,
    Decision,
    EvaluationResult,
    EvaluationScores,
    ScoreEvidence,
    WorkflowNormalization,
)
from bound.policy import BoundPolicy

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
# BoundWeights
# ---------------------------------------------------------------------------


def test_weights_default_all_one() -> None:
    """All four weights default to 1.0 (the v0.1-equivalent configuration).

    With ``W_A = W_I = W_R = W_C = 1.0`` the v0.2 formula collapses to the v0.1
    ``S = (W×A) + I - R - C`` when ``W = 1.0``; this default is what keeps
    existing users' math unchanged.
    """
    weights = BoundWeights()
    assert weights.acceptance == 1.0
    assert weights.influence == 1.0
    assert weights.risk == 1.0
    assert weights.cost == 1.0


def test_weights_accepts_zero() -> None:
    """Zero is a valid weight for every dimension (term is effectively ignored)."""
    weights = BoundWeights(acceptance=0.0, influence=0.0, risk=0.0, cost=0.0)
    assert weights.acceptance == 0.0


@pytest.mark.parametrize("field", ["acceptance", "influence", "risk", "cost"])
def test_weights_rejects_negative(field: str) -> None:
    """A negative weight would invert a signal and is forbidden."""
    with pytest.raises(ValidationError):
        BoundWeights(**{field: -0.1})  # type: ignore[arg-type]


def test_weights_rejects_extra_fields() -> None:
    """Strict models forbid unexpected fields."""
    with pytest.raises(ValidationError):
        BoundWeights(acceptance=1.0, surprise=1.0)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# BoundCriteria — weight, weights, threshold, margins
# ---------------------------------------------------------------------------


def test_criteria_default_weight() -> None:
    """Weight defaults to 1.0 when omitted (alias of weights.acceptance)."""
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


def test_criteria_default_weights() -> None:
    """Omitting weights yields the all-1.0 default BoundWeights."""
    criteria = BoundCriteria(threshold=0.6)
    assert criteria.weights == BoundWeights()


def test_criteria_default_retry_margin() -> None:
    """retry_margin defaults to 0.1."""
    assert BoundCriteria(threshold=0.6).retry_margin == 0.1


def test_criteria_default_rollback_risk_threshold() -> None:
    """rollback_risk_threshold defaults to 0.8."""
    assert BoundCriteria(threshold=0.6).rollback_risk_threshold == 0.8


def test_criteria_rejects_negative_retry_margin() -> None:
    """A negative retry margin is nonsensical and rejected."""
    with pytest.raises(ValidationError):
        BoundCriteria(threshold=0.6, retry_margin=-0.1)


def test_criteria_rejects_rollback_risk_threshold_above_one() -> None:
    """The hard risk boundary is capped at 1.0 (inclusive)."""
    with pytest.raises(ValidationError):
        BoundCriteria(threshold=0.6, rollback_risk_threshold=1.01)


def test_criteria_rejects_rollback_risk_threshold_below_zero() -> None:
    """A negative risk boundary is rejected."""
    with pytest.raises(ValidationError):
        BoundCriteria(threshold=0.6, rollback_risk_threshold=-0.1)


def test_criteria_rollback_risk_boundary_one_allowed() -> None:
    """The ``rollback_risk_threshold = 1.0`` is the inclusive upper bound."""
    criteria = BoundCriteria(threshold=0.6, rollback_risk_threshold=1.0)
    assert criteria.rollback_risk_threshold == 1.0


def test_criteria_weight_folds_into_weights_acceptance() -> None:
    """Supplying only ``weight`` folds it into ``weights.acceptance``.

    Legacy v0.1 callers pass ``weight``; v0.2 must route that single scalar into
    the new symmetric ``weights`` system so downstream code reading
    ``criteria.weights.acceptance`` sees the same value.
    """
    criteria = BoundCriteria(threshold=0.6, weight=2.0)
    assert criteria.weights.acceptance == 2.0
    assert criteria.weights.influence == 1.0
    assert criteria.weights.risk == 1.0
    assert criteria.weights.cost == 1.0
    assert criteria.weight == 2.0


def test_criteria_weights_without_weight_keeps_alias_in_sync() -> None:
    """Using only the new ``weights`` leaves ``weight`` synced to acceptance.

    A caller that adopts :class:`BoundWeights` (here raising influence) must not
    be forced to also pass the deprecated ``weight``; the alias then simply
    reflects the (default) acceptance weight.
    """
    criteria = BoundCriteria(threshold=0.6, weights=BoundWeights(influence=2.0))
    assert criteria.weights.influence == 2.0
    assert criteria.weights.acceptance == 1.0
    assert criteria.weight == 1.0


def test_criteria_rejects_both_weight_and_non_default_weights() -> None:
    """Two competing weight systems must never coexist.

    Supplying ``weight`` alongside a *non-default* ``weights`` is ambiguous (is
    the acceptance weight the scalar or the struct?) so it is rejected loudly
    rather than silently picking one.
    """
    with pytest.raises(ValidationError):
        BoundCriteria(threshold=0.6, weight=2.0, weights=BoundWeights(influence=2.0))


def test_criteria_weight_with_default_weights_is_allowed() -> None:
    """Passing ``weight`` with an explicit-but-default ``weights`` is not a conflict.

    Explicitly passing ``weights=BoundWeights()`` (all 1.0) together with
    ``weight`` is harmless: there is no second competing system, so ``weight``
    simply folds into acceptance.
    """
    criteria = BoundCriteria(threshold=0.6, weight=2.0, weights=BoundWeights())
    assert criteria.weights.acceptance == 2.0
    assert criteria.weight == 2.0


def test_criteria_weights_rejects_negative() -> None:
    """Negative weights inside ``BoundWeights`` are rejected at construction."""
    with pytest.raises(ValidationError):
        BoundCriteria(threshold=0.6, weights=BoundWeights(acceptance=-0.1))


# ---------------------------------------------------------------------------
# ScoreEvidence
# ---------------------------------------------------------------------------


def test_evidence_constructs_with_all_fields() -> None:
    """A fully-specified evidence record carries source, value and metadata."""
    evidence = ScoreEvidence(source="tests", value=1.0, contribution=0.5, description="all green")
    assert evidence.source == "tests"
    assert evidence.value == 1.0
    assert evidence.contribution == 0.5
    assert evidence.description == "all green"


def test_evidence_optional_fields_default_to_none() -> None:
    """contribution and description are optional and default to None."""
    evidence = ScoreEvidence(source="lint", value=0.0)
    assert evidence.contribution is None
    assert evidence.description is None


def test_evidence_rejects_extra_fields() -> None:
    """Strict models forbid unexpected fields."""
    with pytest.raises(ValidationError):
        ScoreEvidence(source="x", value=1.0, surprise=1)  # type: ignore[call-arg]


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


def _result(**overrides: object) -> EvaluationResult:
    """Build a fully-specified EvaluationResult defaulting to the flight example.

    Defaults reproduce the canonical walkthrough: ``S = (1×0.9)+0.2-0.1-0.2 =
    0.8`` against ``T = 0.6`` → ``ACCEPT`` with ``distance_to_threshold = 0.2``.
    Tests override only the fields they vary.
    """
    fields: dict[str, object] = {
        "scores": _example_scores(),
        "weights": BoundWeights(),
        "threshold": 0.6,
        "acceptance_component": 0.9,
        "influence_component": 0.2,
        "risk_component": 0.1,
        "cost_component": 0.2,
        "score": 0.8,
        "distance_to_threshold": 0.2,
        "decision": "ACCEPT",
    }
    fields.update(overrides)
    return EvaluationResult(**fields)  # type: ignore[arg-type]


def test_evaluation_result_constructs_with_components() -> None:
    """A fully-specified result reconstructs the README example S = 0.8."""
    result = _result()
    assert result.score == pytest.approx(0.8)
    assert result.decision == "ACCEPT"
    assert result.scores.acceptance == 0.9
    assert result.distance_to_threshold == pytest.approx(0.2)
    # Deprecated alias stays in sync with the acceptance weight (1.0 here).
    assert result.weight == 1.0
    assert result.weights.acceptance == 1.0


def test_evaluation_result_requires_weights() -> None:
    """weights is a required v0.2 field (no silent default at construction)."""
    with pytest.raises(ValidationError):
        EvaluationResult(
            scores=_example_scores(),
            threshold=0.6,
            acceptance_component=0.9,
            influence_component=0.2,
            risk_component=0.1,
            cost_component=0.2,
            score=0.8,
            distance_to_threshold=0.2,
            decision="ACCEPT",
        )


def test_evaluation_result_requires_distance_to_threshold() -> None:
    """distance_to_threshold is required so the S-vs-T gap is always auditable."""
    with pytest.raises(ValidationError):
        EvaluationResult(
            scores=_example_scores(),
            weights=BoundWeights(),
            threshold=0.6,
            acceptance_component=0.9,
            influence_component=0.2,
            risk_component=0.1,
            cost_component=0.2,
            score=0.8,
            decision="ACCEPT",
        )


def test_result_weight_alias_matches_weights_acceptance() -> None:
    """result.weight is an alias for weights.acceptance."""
    result = _result(weights=BoundWeights(acceptance=2.0))
    assert result.weights.acceptance == 2.0
    assert result.weight == 2.0


def test_result_weight_folds_into_weights() -> None:
    """Supplying only ``weight`` (with default weights) folds into acceptance."""
    result = _result(weight=2.0, weights=BoundWeights())
    assert result.weights.acceptance == 2.0
    assert result.weight == 2.0


def test_result_rejects_both_weight_and_non_default_weights() -> None:
    """Two competing weight systems are rejected on the result too."""
    with pytest.raises(ValidationError):
        _result(weight=2.0, weights=BoundWeights(influence=2.0))


def test_result_revalidation_is_idempotent_with_non_default_weights() -> None:
    """Nesting a non-default-weights result in another model does not raise.

    Pydantic v2 re-validates nested model instances, so the ``weight``/
    ``weights`` reconciliation must be idempotent: once ``weight`` is synced to
    ``weights.acceptance``, re-validating the same instance (e.g. when it is a
    field of :class:`~bound.integration.AgentControlResult`) must not spuriously
    raise "Cannot supply both". This guards the Phase 1 integration layer and
    any other consumer that nests an :class:`EvaluationResult`.
    """
    from pydantic import BaseModel

    class _Wrapper(BaseModel):
        result: EvaluationResult

    result = _result(weights=BoundWeights(acceptance=2.0))
    assert result.weight == 2.0
    assert result.weights.acceptance == 2.0

    wrapped = _Wrapper(result=result)
    assert wrapped.result.weight == 2.0
    assert wrapped.result.weights.acceptance == 2.0
    assert wrapped.result.decision == "ACCEPT"



def test_result_defaults_rollback_risk_threshold_and_retry_margin() -> None:
    """When omitted, the audit fields default to the criteria defaults."""
    result = _result()
    assert result.rollback_risk_threshold == 0.8
    assert result.retry_margin == 0.1


def test_result_provenance_optional() -> None:
    """Provenance is optional (None) for manually supplied scores."""
    assert _result().provenance is None


def test_result_provenance_carries_evidence() -> None:
    """Provenance maps dimension names to lists of ScoreEvidence."""
    result = _result(
        provenance={
            "acceptance": [
                ScoreEvidence(source="tests", value=1.0, contribution=1.0),
                ScoreEvidence(source="lint", value=1.0),
            ]
        }
    )
    assert result.provenance is not None
    assert len(result.provenance["acceptance"]) == 2
    assert result.provenance["acceptance"][0].source == "tests"


def test_evaluation_result_rejects_invalid_decision() -> None:
    """A decision outside the Decision literal must be rejected."""
    with pytest.raises(ValidationError):
        _result(decision="MAYBE")  # type: ignore[arg-type]


@pytest.mark.parametrize("decision", ["ACCEPT", "RETRY", "REPLAN", "ROLLBACK"])
def test_evaluation_result_accepts_all_decisions(decision: str) -> None:
    """Every member of the Decision literal is a valid result decision."""
    result = _result(decision=decision)
    assert result.decision == decision


def test_evaluation_result_rejects_extra_fields() -> None:
    """Strict models forbid unexpected fields to keep results auditable."""
    with pytest.raises(ValidationError):
        _result(surprise="nope")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# distance_to_threshold sign conventions (via the policy)
# ---------------------------------------------------------------------------

_ACTION = Action(description="Book the direct flight", goal="Travel to New York")


def _policy_result(
    *,
    acceptance: float,
    weight: float,
    threshold: float,
    influence: float = 0.0,
    risk: float = 0.0,
    cost: float = 0.0,
) -> EvaluationResult:
    """Run the real policy so distance_to_threshold is the actual ``S - T``."""
    scores = EvaluationScores(
        acceptance=acceptance, influence=influence, risk=risk, cost=cost
    )
    criteria = BoundCriteria(weight=weight, threshold=threshold)
    return BoundPolicy(StaticEvaluator(scores)).evaluate(_ACTION, criteria)


def test_distance_above_threshold_is_positive() -> None:
    """S > T → distance_to_threshold is positive (and the action is accepted)."""
    result = _policy_result(
        acceptance=0.9, influence=0.2, risk=0.1, cost=0.2, weight=1.0, threshold=0.6
    )
    assert result.score == pytest.approx(0.8, abs=1e-12)
    assert result.distance_to_threshold == pytest.approx(0.2, abs=1e-12)
    assert result.distance_to_threshold > 0
    assert result.decision == "ACCEPT"


def test_distance_at_threshold_is_zero() -> None:
    """S == T → distance_to_threshold is exactly zero (boundary-inclusive ACCEPT)."""
    result = _policy_result(acceptance=0.6, weight=1.0, threshold=0.6)
    assert result.score == pytest.approx(0.6, abs=1e-12)
    assert result.distance_to_threshold == pytest.approx(0.0, abs=1e-12)
    assert result.decision == "ACCEPT"


def test_distance_below_threshold_is_negative() -> None:
    """S < T → distance_to_threshold is negative (below the acceptance bar)."""
    result = _policy_result(acceptance=0.3, weight=1.0, threshold=0.6)
    assert result.score == pytest.approx(0.3, abs=1e-12)
    assert result.distance_to_threshold == pytest.approx(-0.3, abs=1e-12)
    assert result.distance_to_threshold < 0
    assert result.decision != "ACCEPT"


# ---------------------------------------------------------------------------
# WorkflowNormalization
# ---------------------------------------------------------------------------


def test_normalization_defaults() -> None:
    """The default caps match the v0.2 reference configuration."""
    norm = WorkflowNormalization()
    assert norm.max_expected_retries == 5
    assert norm.max_expected_tool_calls == 50
    assert norm.max_expected_tokens == 100_000
    assert norm.max_expected_runtime_seconds == 3600.0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_expected_retries", -1),
        ("max_expected_tool_calls", -1),
        ("max_expected_tokens", -1),
        ("max_expected_runtime_seconds", -1.0),
    ],
)
def test_normalization_rejects_negative_caps(field: str, value: float) -> None:
    """Negative caps would invert normalization and are rejected."""
    with pytest.raises(ValidationError):
        WorkflowNormalization(**{field: value})  # type: ignore[arg-type]


def test_normalization_rejects_extra_fields() -> None:
    """Strict models forbid unexpected fields."""
    with pytest.raises(ValidationError):
        WorkflowNormalization(surprise=1)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# CodingWorkflowSignals
# ---------------------------------------------------------------------------


def test_signals_all_optional_default() -> None:
    """An empty signal set leaves optional fields None and counts at zero.

    Missing signals are ``None`` (not silently zero) so a downstream evaluator
    can ignore unobserved signals rather than treating them as failures.
    """
    signals = CodingWorkflowSignals()
    assert signals.test_pass_rate is None
    assert signals.lint_passed is None
    assert signals.type_check_passed is None
    assert signals.required_checks_passed is None
    assert signals.retry_count == 0
    assert signals.tool_call_count == 0
    assert signals.token_usage is None
    assert signals.execution_time_seconds is None
    assert signals.files_changed is None
    assert signals.unexpected_files_changed is None
    assert signals.rollback_available is None
    # Test-mutation signals default to None (unobserved), like the other counts.
    assert signals.tests_added is None
    assert signals.tests_removed is None
    assert signals.tests_modified is None


def test_signals_valid_full() -> None:
    """A fully-populated, in-range signal set validates."""
    signals = CodingWorkflowSignals(
        test_pass_rate=1.0,
        lint_passed=True,
        type_check_passed=False,
        required_checks_passed=0.75,
        retry_count=2,
        tool_call_count=14,
        token_usage=40_000,
        execution_time_seconds=120.5,
        files_changed=3,
        unexpected_files_changed=0,
        rollback_available=True,
        tests_added=4,
        tests_removed=0,
        tests_modified=2,
    )
    assert signals.test_pass_rate == 1.0
    assert signals.tool_call_count == 14
    assert signals.rollback_available is True
    assert signals.tests_added == 4
    assert signals.tests_removed == 0
    assert signals.tests_modified == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("test_pass_rate", 1.01),
        ("test_pass_rate", -0.01),
        ("required_checks_passed", 1.01),
        ("retry_count", -1),
        ("tool_call_count", -1),
        ("token_usage", -1),
        ("execution_time_seconds", -0.1),
        ("files_changed", -1),
        ("unexpected_files_changed", -1),
        ("tests_added", -1),
        ("tests_removed", -1),
        ("tests_modified", -1),
    ],
)
def test_signals_reject_out_of_range(field: str, value: float) -> None:
    """Out-of-range signals are rejected so the BOUND ranges stay meaningful."""
    with pytest.raises(ValidationError):
        CodingWorkflowSignals(**{field: value})  # type: ignore[arg-type]


def test_signals_test_mutation_counts_accept_zero() -> None:
    """Test-mutation counts are non-negative integers; zero is the clean baseline.

    Zero is the valid "no mutation" value (not ``None``, which means unobserved),
    so an agent that touched no tests records explicit zeros rather than absence.
    """
    signals = CodingWorkflowSignals(
        tests_added=0, tests_removed=0, tests_modified=0
    )
    assert signals.tests_added == 0
    assert signals.tests_removed == 0
    assert signals.tests_modified == 0


def test_signals_boundary_values_accepted() -> None:
    """The inclusive range endpoints (0.0/1.0 rates, zero counts) are valid."""
    signals = CodingWorkflowSignals(
        test_pass_rate=0.0, required_checks_passed=1.0, retry_count=0, tool_call_count=0
    )
    assert signals.test_pass_rate == 0.0
    assert signals.required_checks_passed == 1.0


def test_signals_rejects_extra_fields() -> None:
    """No provider-specific fields sneak in via extra keys."""
    with pytest.raises(ValidationError):
        CodingWorkflowSignals(surprise=1)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# AgentStep
# ---------------------------------------------------------------------------


def test_step_constructs_with_signals_only() -> None:
    """A step requires an index and signals; scores defaults to None."""
    step = AgentStep(step_index=0, signals=CodingWorkflowSignals())
    assert step.step_index == 0
    assert step.scores is None


def test_step_accepts_optional_scores() -> None:
    """A pre-computed EvaluationScores may be attached to a step."""
    step = AgentStep(
        step_index=1,
        signals=CodingWorkflowSignals(test_pass_rate=1.0),
        scores=EvaluationScores(acceptance=0.9, influence=0.0, risk=0.1, cost=0.2),
    )
    assert step.scores is not None
    assert step.scores.acceptance == 0.9


def test_step_rejects_negative_step_index() -> None:
    """Step indices are non-negative."""
    with pytest.raises(ValidationError):
        AgentStep(step_index=-1, signals=CodingWorkflowSignals())


def test_step_rejects_extra_fields() -> None:
    """Strict models forbid unexpected fields."""
    with pytest.raises(ValidationError):
        AgentStep(step_index=0, signals=CodingWorkflowSignals(), surprise=1)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# AgentTrajectory
# ---------------------------------------------------------------------------


def test_trajectory_constructs_with_steps() -> None:
    """A trajectory composes ordered steps under a task id."""
    trajectory = AgentTrajectory(
        task_id="issue-123",
        steps=[
            AgentStep(step_index=0, signals=CodingWorkflowSignals(test_pass_rate=0.5)),
            AgentStep(step_index=1, signals=CodingWorkflowSignals(test_pass_rate=1.0)),
        ],
    )
    assert trajectory.task_id == "issue-123"
    assert len(trajectory.steps) == 2
    assert trajectory.steps[1].signals.test_pass_rate == 1.0
    assert trajectory.actual_stop_step is None


def test_trajectory_records_actual_stop_step() -> None:
    """actual_stop_step lets the harness compare BOUND's stop vs the agent's."""
    trajectory = AgentTrajectory(
        task_id="issue-123",
        steps=[AgentStep(step_index=0, signals=CodingWorkflowSignals())],
        actual_stop_step=4,
    )
    assert trajectory.actual_stop_step == 4


def test_trajectory_rejects_extra_fields() -> None:
    """Strict models forbid unexpected fields."""
    with pytest.raises(ValidationError):
        AgentTrajectory(task_id="t", steps=[], surprise=1)  # type: ignore[call-arg]
