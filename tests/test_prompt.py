"""Unit tests for the BOUND steering-prompt rendering (Phase 5).

The prompt is generated purely from an :class:`~bound.models.EvaluationResult`
(no LLM) and must be:

* deterministic — identical inputs produce identical output;
* mathematically correct — the substituted formula and final ``S`` match the
  result's actual values, and the displayed ``S`` / ``T`` are consistent with
  the decision;
* under 150 words;
* always containing ``S``, ``T`` and the decision.
"""

from __future__ import annotations

import pytest

from bound.evaluator import StaticEvaluator
from bound.models import Action, BoundCriteria, EvaluationResult, EvaluationScores
from bound.policy import BoundPolicy
from bound.prompt import MAX_WORDS, generate_prompt, word_count

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_ACTION = Action(description="Book the direct flight", goal="Travel from Paris to New York")


def _result(
    acceptance: float = 0.9,
    influence: float = 0.2,
    risk: float = 0.1,
    cost: float = 0.2,
    weight: float = 1.0,
    threshold: float = 0.6,
) -> EvaluationResult:
    """Build an :class:`EvaluationResult` via the real policy pipeline.

    Going through :class:`BoundPolicy` (rather than constructing the result by
    hand) keeps the prompt tests honest: the values rendered are exactly those
    the deterministic core produces, so the prompt's maths is checked against
    the real calculation, not a parallel re-implementation.
    """
    scores = EvaluationScores(
        acceptance=acceptance,
        influence=influence,
        risk=risk,
        cost=cost,
    )
    criteria = BoundCriteria(weight=weight, threshold=threshold)
    return BoundPolicy(StaticEvaluator(scores)).evaluate(_ACTION, criteria)


# ---------------------------------------------------------------------------
# Required content: S, T, decision
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "expected_decision"),
    [
        # Flight example: S = (1.0 x 0.9) + 0.2 - 0.1 - 0.2 = 0.8 >= 0.6.
        (dict(), "ACCEPT"),
        # Below threshold with cost > risk -> RETRY.
        (dict(acceptance=0.3, cost=0.4, risk=0.1, threshold=0.6), "RETRY"),
        # Below threshold with risk > cost -> ROLLBACK.
        (dict(acceptance=0.3, risk=0.4, cost=0.1, threshold=0.6), "ROLLBACK"),
        # Below threshold with risk == cost -> REPLAN.
        (dict(acceptance=0.3, risk=0.2, cost=0.2, threshold=0.6), "REPLAN"),
    ],
)
def test_prompt_contains_decision_score_and_threshold(
    kwargs: dict[str, float], expected_decision: str
) -> None:
    """The prompt always carries the score ``S``, threshold ``T`` and decision.

    These three are the contract a consumer relies on, so every decision branch
    must surface them. We check the explicit ``S =``/``T =`` lines (not just
    the bare letters) to avoid false positives from incidental substrings.
    """
    result = _result(**kwargs)
    prompt = generate_prompt(result)

    assert f"S = {result.score:.2f}" in prompt
    assert f"T = {result.threshold:.2f}" in prompt
    assert expected_decision == result.decision
    assert f"Decision: {expected_decision}" in prompt


# ---------------------------------------------------------------------------
# Mathematical correctness
# ---------------------------------------------------------------------------


def test_prompt_substitutes_components_correctly() -> None:
    """The substituted formula line echoes the real components and final S.

    The displayed ``(W × A)`` term must equal the result's
    ``acceptance_component`` and the displayed ``S`` must equal the real score,
    so the printed formula is arithmetically consistent with the result object
    (which is itself verified math-correct by the Phase 2/4 tests).
    """
    result = _result(acceptance=0.9, influence=0.2, risk=0.1, cost=0.2, weight=1.0, threshold=0.6)
    prompt = generate_prompt(result)

    assert f"({result.weight:.2f} × {result.scores.acceptance:.2f})" in prompt
    assert f"+ {result.scores.influence:.2f}" in prompt
    assert f"- {result.scores.risk:.2f}" in prompt
    assert f"- {result.scores.cost:.2f}" in prompt
    assert f"S = {result.score:.2f}" in prompt


def test_prompt_handles_negative_influence() -> None:
    """Negative influence is rendered with a minus operator and stays correct.

    ``I`` may be negative; the prompt should show ``- 0.10`` (not ``+ -0.10``)
    while remaining arithmetically exact: ``+ (-0.1)`` and ``- 0.1`` are equal.
    """
    result = _result(acceptance=0.7, influence=-0.1, risk=0.2, cost=0.2, threshold=0.6)
    prompt = generate_prompt(result)

    assert "- 0.10" in prompt
    # S = (1.0 x 0.7) + (-0.1) - 0.2 - 0.2 = 0.2; below 0.6 -> not ACCEPT.
    assert result.score == pytest.approx(0.2, abs=1e-12)
    assert f"S = {result.score:.2f}" in prompt
    assert result.decision != "ACCEPT"


def test_prompt_decision_is_consistent_with_score_and_threshold() -> None:
    """The decision surfaced by the prompt matches the S-vs-T comparison.

    Ties the prompt's numbers to the decision: ACCEPT iff ``S >= T``. This is
    the core BOUND contract made visible in the rendered prompt.
    """
    for threshold in (0.0, 0.4, 0.6, 0.8, 1.0):
        result = _result(threshold=threshold)
        prompt = generate_prompt(result)
        if result.score >= result.threshold:
            assert "Decision: ACCEPT" in prompt
        else:
            assert "Decision: ACCEPT" not in prompt


# ---------------------------------------------------------------------------
# Word count
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(),
        dict(acceptance=0.3, cost=0.4, risk=0.1, threshold=0.6),  # RETRY
        dict(acceptance=0.3, risk=0.4, cost=0.1, threshold=0.6),  # ROLLBACK
        dict(acceptance=0.3, risk=0.2, cost=0.2, threshold=0.6),  # REPLAN
    ],
)
def test_prompt_under_150_words(kwargs: dict[str, float]) -> None:
    """Every decision branch stays under the 150-word limit.

    Token counting over-counts (it counts ``S``, ``=``, numbers, etc. as
    tokens), so a passing check here guarantees the real word count is also
    within the limit.
    """
    result = _result(**kwargs)
    prompt = generate_prompt(result)

    assert word_count(prompt) < MAX_WORDS
    assert word_count(prompt) < 150


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_prompt_is_deterministic() -> None:
    """Identical inputs produce identical prompts across repeated calls."""
    result = _result()
    first = generate_prompt(result)
    second = generate_prompt(result)

    assert first == second


# ---------------------------------------------------------------------------
# ACCEPT philosophy wording
# ---------------------------------------------------------------------------


def test_accept_prompt_states_bounded_optimization_principle() -> None:
    """The ACCEPT branch makes the bounded-optimization principle explicit.

    It must say the action meets the threshold, that further optimization is
    not required, and that the agent should proceed — the core BOUND message.
    """
    result = _result()  # S = 0.8 >= 0.6 -> ACCEPT
    prompt = generate_prompt(result)

    assert "meets the required acceptance threshold" in prompt
    assert "Further optimization is not required" in prompt
    assert "Proceed" in prompt


def test_non_accept_prompt_includes_assessment_and_next_step() -> None:
    """Non-ACCEPT branches include an Assessment and a Suggested next step.

    A below-threshold action should never claim the threshold is met, and must
    guide the agent toward a corrective next step.
    """
    result = _result(acceptance=0.3, risk=0.2, cost=0.2, threshold=0.6)  # REPLAN
    prompt = generate_prompt(result)

    assert "does not yet meet the required acceptance threshold" in prompt
    assert "Assessment:" in prompt
    assert "Suggested next step:" in prompt

