"""Unit tests for the BOUND decision policy (Phase 4).

These tests pin down the deterministic decision rule and the auditability of
the returned :class:`~bound.models.EvaluationResult`:

Decision rule (applied exactly, in order):

* ``S >= T`` -> ``ACCEPT``  (boundary-inclusive: ``S == T`` accepts)
* ``S < T`` and ``risk > cost`` -> ``ROLLBACK``
* ``S < T`` and ``cost > risk`` -> ``RETRY``
* ``S < T`` and ``risk == cost`` -> ``REPLAN``

The policy must derive ``S`` via the existing calculator's
:func:`~bound.calculator.calculate_components` so the components it reports are
bit-identical to the score, and the decision must come from the policy — never
from the evaluator.
"""

from __future__ import annotations

import pytest

from bound.calculator import calculate_components
from bound.evaluator import StaticEvaluator
from bound.models import (
    Action,
    BoundCriteria,
    EvaluationResult,
    EvaluationScores,
)
from bound.policy import BoundPolicy

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_ACTION = Action(
    description="Book the direct flight",
    goal="Travel from Paris to New York",
)


def _scores(
    acceptance: float = 0.0,
    influence: float = 0.0,
    risk: float = 0.0,
    cost: float = 0.0,
) -> EvaluationScores:
    """Build :class:`EvaluationScores` with zero defaults.

    Defaults are all zero so a test only sets the dimensions it cares about,
    keeping the decision under test easy to read.
    """
    return EvaluationScores(
        acceptance=acceptance,
        influence=influence,
        risk=risk,
        cost=cost,
    )


def _criteria(weight: float = 1.0, threshold: float = 0.0) -> BoundCriteria:
    """Build :class:`BoundCriteria`."""
    return BoundCriteria(weight=weight, threshold=threshold)


def _policy(scores: EvaluationScores) -> BoundPolicy:
    """Build a :class:`BoundPolicy` backed by a :class:`StaticEvaluator`.

    Using :class:`StaticEvaluator` keeps the test suite free of network access,
    API keys, and any LLM SDK.
    """
    return BoundPolicy(StaticEvaluator(scores))


def _evaluate(
    scores: EvaluationScores,
    criteria: BoundCriteria,
) -> EvaluationResult:
    """Run the policy with a static evaluator over ``_ACTION``."""
    return _policy(scores).evaluate(_ACTION, criteria)


# ---------------------------------------------------------------------------
# ACCEPT boundary
# ---------------------------------------------------------------------------


def test_accept_at_exact_boundary() -> None:
    """S == T accepts: S=0.6, T=0.6 -> ACCEPT.

    The acceptance condition is ``S >= T`` (not ``S > T``), so landing exactly
    on the threshold is sufficient. This is the boundary case the BOUND
    contract calls out explicitly.
    """
    scores = _scores(acceptance=0.6)
    criteria = _criteria(weight=1.0, threshold=0.6)

    result = _evaluate(scores, criteria)

    assert result.score == pytest.approx(0.6, abs=1e-12)
    assert result.decision == "ACCEPT"


def test_just_below_threshold_is_not_accept() -> None:
    """S=0.599999, T=0.6 -> not ACCEPT.

    Pins the strict side of the boundary: a hair below the threshold must not
    cross to ACCEPT. With risk==cost==0 the tie-breaker resolves to REPLAN, but
    the key assertion here is simply that ACCEPT is excluded.
    """
    scores = _scores(acceptance=0.599999)
    criteria = _criteria(weight=1.0, threshold=0.6)

    result = _evaluate(scores, criteria)

    assert result.score == pytest.approx(0.599999, abs=1e-12)
    assert result.decision != "ACCEPT"
    assert result.decision == "REPLAN"


def test_accept_above_threshold() -> None:
    """S > T accepts: README flight example, S=0.8, T=0.6 -> ACCEPT.

    The canonical walkthrough: ``S = (1.0 x 0.9) + 0.2 - 0.1 - 0.2 = 0.8``,
    comfortably above ``T=0.6``.
    """
    scores = _scores(acceptance=0.9, influence=0.2, risk=0.1, cost=0.2)
    criteria = _criteria(weight=1.0, threshold=0.6)

    result = _evaluate(scores, criteria)

    assert result.score == pytest.approx(0.8, abs=1e-12)
    assert result.decision == "ACCEPT"


# ---------------------------------------------------------------------------
# Below-threshold decisions
# ---------------------------------------------------------------------------


def test_retry_when_cost_exceeds_risk() -> None:
    """Below threshold with cost > risk -> RETRY.

    When the action misses the threshold, the tie-breaker compares the
    resource cost to the risk: a costly-but-safe failure mode says try again
    more cheaply (RETRY).
    """
    scores = _scores(acceptance=0.3, risk=0.1, cost=0.5)
    criteria = _criteria(weight=1.0, threshold=0.6)

    result = _evaluate(scores, criteria)

    assert result.score == pytest.approx(-0.3, abs=1e-12)
    assert result.decision == "RETRY"


def test_rollback_when_risk_exceeds_cost() -> None:
    """Below threshold with risk > cost -> ROLLBACK.

    A high-risk miss says undo or step back (ROLLBACK) rather than retry, to
    avoid repeating a dangerous action.
    """
    scores = _scores(acceptance=0.3, risk=0.5, cost=0.1)
    criteria = _criteria(weight=1.0, threshold=0.6)

    result = _evaluate(scores, criteria)

    assert result.score == pytest.approx(-0.3, abs=1e-12)
    assert result.decision == "ROLLBACK"


def test_replan_when_risk_equals_cost() -> None:
    """Below threshold with risk == cost -> REPLAN.

    When neither risk nor cost dominates, the safe response is to change the
    plan (REPLAN) rather than retry or roll back.
    """
    scores = _scores(acceptance=0.3, risk=0.2, cost=0.2)
    criteria = _criteria(weight=1.0, threshold=0.6)

    result = _evaluate(scores, criteria)

    assert result.score == pytest.approx(-0.1, abs=1e-12)
    assert result.decision == "REPLAN"


def test_high_acceptance_below_threshold_still_tiebreaks() -> None:
    """Below threshold the tie-breaker decides, never ACCEPT.

    Even with a high acceptance, missing the threshold routes through the
    risk/cost comparison; here risk > cost yields ROLLBACK.
    """
    scores = _scores(acceptance=0.55, risk=0.6, cost=0.1)
    criteria = _criteria(weight=1.0, threshold=0.6)

    result = _evaluate(scores, criteria)

    assert result.score == pytest.approx(-0.15, abs=1e-12)
    assert result.decision == "ROLLBACK"


# ---------------------------------------------------------------------------
# Auditability / component consistency
# ---------------------------------------------------------------------------


def test_result_components_match_calculator() -> None:
    """Reported components equal the calculator components, bit-identical.

    The policy must source components from
    :func:`~bound.calculator.calculate_components` so the audit trail in the
    :class:`EvaluationResult` cannot drift from the canonical calculation.
    """
    scores = _scores(acceptance=0.9, influence=0.2, risk=0.1, cost=0.2)
    criteria = _criteria(weight=1.0, threshold=0.6)

    result = _evaluate(scores, criteria)
    components = calculate_components(scores, criteria)

    assert result.acceptance_component == components.weighted_acceptance
    assert result.influence_component == components.influence
    assert result.risk_component == components.risk
    assert result.cost_component == components.cost
    assert result.score == components.total


def test_result_echoes_weights_and_threshold() -> None:
    """The result echoes the weight and threshold used for the decision.

    Auditability: a consumer must be able to reconstruct ``S >= T`` from the
    result alone, so ``weight`` and ``threshold`` are carried through verbatim.
    """
    scores = _scores(acceptance=0.5, influence=0.1, risk=0.05, cost=0.05)
    criteria = _criteria(weight=2.0, threshold=1.0)

    result = _evaluate(scores, criteria)

    assert result.weight == 2.0
    assert result.threshold == 1.0
    # S = (2.0 * 0.5) + 0.1 - 0.05 - 0.05 = 1.0; S >= T -> ACCEPT.
    assert result.score == pytest.approx(1.0, abs=1e-12)
    assert result.decision == "ACCEPT"


def test_result_carries_original_scores() -> None:
    """The result references the evaluator original EvaluationScores.

    The full input must be recoverable from the result for auditing.
    """
    scores = _scores(acceptance=0.9, influence=0.2, risk=0.1, cost=0.2)
    criteria = _criteria(weight=1.0, threshold=0.6)

    result = _evaluate(scores, criteria)

    assert result.scores == scores


# ---------------------------------------------------------------------------
# Policy / evaluator wiring
# ---------------------------------------------------------------------------


def test_policy_exposes_evaluator() -> None:
    """The bound evaluator is accessible via the evaluator property.

    Confirms injection is retained for introspection and replacement.
    """
    evaluator = StaticEvaluator(_scores(acceptance=0.9))
    policy = BoundPolicy(evaluator)

    assert policy.evaluator is evaluator


def test_policy_invokes_evaluator_once_per_call() -> None:
    """The policy forwards the action to the evaluator exactly once.

    Uses a counting evaluator to assert the pipeline order (Action then
    evaluator) and that the evaluator is the sole source of scores.
    """

    class _Counting:
        def __init__(self, scores: EvaluationScores) -> None:
            self._scores = scores
            self.calls = 0
            self.last_action: Action | None = None

        def evaluate(self, action: Action) -> EvaluationScores:
            self.calls += 1
            self.last_action = action
            return self._scores

    scores = _scores(acceptance=0.9, influence=0.2, risk=0.1, cost=0.2)
    evaluator = _Counting(scores)
    policy = BoundPolicy(evaluator)

    result = policy.evaluate(_ACTION, _criteria(weight=1.0, threshold=0.6))

    assert evaluator.calls == 1
    assert evaluator.last_action == _ACTION
    assert result.decision == "ACCEPT"


def test_policy_with_static_evaluator_needs_no_network() -> None:
    """A StaticEvaluator-backed policy runs fully offline.

    Guards the architecture requirement: no network, no API key, and no LLM SDK
    is needed to reach a deterministic BOUND decision.
    """
    scores = _scores(acceptance=0.9, influence=0.2, risk=0.1, cost=0.2)
    criteria = _criteria(weight=1.0, threshold=0.6)

    result = BoundPolicy(StaticEvaluator(scores)).evaluate(_ACTION, criteria)

    assert result.decision == "ACCEPT"
