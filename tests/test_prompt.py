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
        # Below threshold but within retry_margin -> RETRY.
        # S = 0.55; gap = 0.6 - 0.55 = 0.05 <= 0.1; risk < 0.8.
        (dict(acceptance=0.55, influence=0.0, cost=0.0, risk=0.0, threshold=0.6), "RETRY"),
        # risk >= rollback_risk_threshold (0.8) -> ROLLBACK (safety boundary).
        (dict(acceptance=0.3, risk=0.9, cost=0.1, threshold=0.6), "ROLLBACK"),
        # Below threshold, gap > retry_margin -> REPLAN.
        # S = 0.3 - 0.2 - 0.2 = -0.1; gap = 0.7 > 0.1; risk < 0.8.
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
    """The substituted four-weight formula line echoes the real terms and S.

    The displayed ``(W_A × A)`` / ``(W_I × I)`` / ``(W_R × R)`` / ``(W_C × C)``
    terms must match the result's weights and scores, and the displayed ``S``
    must equal the real score, so the printed formula is arithmetically
    consistent with the result object (itself verified math-correct by the
    Phase 2/4 tests). Both the symbolic and substituted formula lines must be
    present.
    """
    result = _result(acceptance=0.9, influence=0.2, risk=0.1, cost=0.2, weight=1.0, threshold=0.6)
    prompt = generate_prompt(result)

    assert "S = (W_A × A) + (W_I × I) - (W_R × R) - (W_C × C)" in prompt
    assert (
        f"({result.weights.acceptance:.2f} × {result.scores.acceptance:.2f})" in prompt
    )
    assert (
        f"({result.weights.influence:.2f} × {result.scores.influence:.2f})" in prompt
    )
    assert f"({result.weights.risk:.2f} × {result.scores.risk:.2f})" in prompt
    assert f"({result.weights.cost:.2f} × {result.scores.cost:.2f})" in prompt
    assert f"S = {result.score:.2f}" in prompt


def test_prompt_handles_negative_influence() -> None:
    """Negative influence is substituted verbatim and stays arithmetically exact.

    ``I`` may be negative; the prompt shows the weighted term ``(W_I × I)``
    with the negative value (e.g. ``(1.00 × -0.10)``) rather than a broken
    ``+ -0.10`` operator, while remaining exact.
    """
    result = _result(acceptance=0.7, influence=-0.1, risk=0.2, cost=0.2, threshold=0.6)
    prompt = generate_prompt(result)

    assert f"({result.weights.influence:.2f} × {result.scores.influence:.2f})" in prompt
    # S = (1.0 x 0.7) + (1.0 x -0.1) - 0.2 - 0.2 = 0.2; below 0.6 -> not ACCEPT.
    assert result.score == pytest.approx(0.2, abs=1e-12)
    assert f"S = {result.score:.2f}" in prompt
    assert result.decision != "ACCEPT"


def test_prompt_decision_is_consistent_with_score_and_threshold() -> None:
    """The decision surfaced by the prompt matches the S-vs-T comparison.

    Ties the prompt's numbers to the decision: ACCEPT iff ``S >= T`` (and risk
    below the rollback boundary). This is the core BOUND contract made visible
    in the rendered prompt.
    """
    for threshold in (0.0, 0.4, 0.6, 0.8, 1.0):
        result = _result(threshold=threshold)
        prompt = generate_prompt(result)
        if result.score >= result.threshold:
            assert "Decision: ACCEPT" in prompt
        else:
            assert "Decision: ACCEPT" not in prompt


def test_prompt_includes_threshold_metadata() -> None:
    """The prompt surfaces the distance to threshold, risk and rollback boundary.

    Phase 8 requires the prompt to carry the score, threshold, distance from
    threshold, risk, rollback threshold and decision so a consumer can audit
    the decision from the prompt alone.
    """
    result = _result(acceptance=0.3, risk=0.9, cost=0.1, threshold=0.6)  # ROLLBACK
    prompt = generate_prompt(result)

    assert f"S = {result.score:.2f}" in prompt
    assert f"T = {result.threshold:.2f}" in prompt
    assert f"Distance to threshold: {result.distance_to_threshold:.2f}" in prompt
    assert f"Risk: {result.scores.risk:.2f}" in prompt
    assert f"Rollback threshold: {result.rollback_risk_threshold:.2f}" in prompt
    assert "Decision: ROLLBACK" in prompt


# ---------------------------------------------------------------------------
# Word count
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "expected_decision"),
    [
        (dict(), "ACCEPT"),
        # S = 0.55; gap = 0.6 - 0.55 = 0.05 <= 0.1; risk < 0.8 -> RETRY.
        (dict(acceptance=0.55, influence=0.0, cost=0.0, risk=0.0, threshold=0.6), "RETRY"),
        # risk >= rollback_risk_threshold (0.8) -> ROLLBACK.
        (dict(acceptance=0.3, risk=0.9, cost=0.1, threshold=0.6), "ROLLBACK"),
        # S = 0.3 + 0.2 - 0.2 - 0.2 = 0.1; gap = 0.5 > 0.1; risk < 0.8 -> REPLAN.
        (dict(acceptance=0.3, risk=0.2, cost=0.2, threshold=0.6), "REPLAN"),
    ],
)
def test_prompt_under_150_words(
    kwargs: dict[str, float], expected_decision: str
) -> None:
    """Every decision branch stays under the 150-word limit.

    Each of the four decisions is exercised explicitly (and its decision
    asserted) so the wording cannot silently grow past the limit on any branch.
    Token counting over-counts (it counts ``S``, ``=``, numbers, etc. as
    tokens), so a passing check here guarantees the real word count is also
    within the limit.
    """
    result = _result(**kwargs)
    prompt = generate_prompt(result)

    assert result.decision == expected_decision
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
# v0.2 decision-semantics wording
# ---------------------------------------------------------------------------


def test_accept_prompt_states_bounded_optimization_principle() -> None:
    """The ACCEPT branch makes the v0.2 satisficing principle explicit.

    It must state that the result meets the threshold and does not exceed the
    risk boundary, that further optimization of this step is not required, and
    that the agent should continue toward the next goal — the core BOUND
    bounded-optimization message.
    """
    result = _result()  # S = 0.8 >= 0.6, risk 0.1 < 0.8 -> ACCEPT
    prompt = generate_prompt(result)

    assert "meets the required acceptance threshold" in prompt
    assert "does not exceed the configured risk boundary" in prompt
    assert "Further optimization of this step is not required" in prompt
    assert "Continue toward the next goal" in prompt


@pytest.mark.parametrize(
    ("kwargs", "expected_decision", "phrases"),
    [
        # RETRY: close to threshold, one focused attempt within same approach.
        (
            dict(acceptance=0.55, influence=0.0, cost=0.0, risk=0.0, threshold=0.6),
            "RETRY",
            [
                "close to the required acceptance threshold",
                "one focused attempt to close the remaining gap",
            ],
        ),
        # REPLAN: materially below threshold, choose a different strategy.
        (
            dict(acceptance=0.3, risk=0.2, cost=0.2, threshold=0.6),
            "REPLAN",
            [
                "materially below the required acceptance threshold",
                "Choose a different strategy",
            ],
        ),
        # ROLLBACK: exceeds the risk boundary, avoid or revert.
        (
            dict(acceptance=0.3, risk=0.9, cost=0.1, threshold=0.6),
            "ROLLBACK",
            [
                "exceeds the configured acceptable risk boundary",
                "Avoid or revert the action",
            ],
        ),
    ],
)
def test_non_accept_prompt_uses_v02_decision_wording(
    kwargs: dict[str, float],
    expected_decision: str,
    phrases: list[str],
) -> None:
    """Each non-ACCEPT decision renders its v0.2-specific steering wording.

    The v0.2 semantics replace the old risk-vs-cost framing: RETRY asks for one
    focused attempt within the same approach, REPLAN calls for a different
    strategy, and ROLLBACK warns the risk boundary was exceeded. A
    below-threshold action must never claim the threshold is met.
    """
    result = _result(**kwargs)
    prompt = generate_prompt(result)

    assert result.decision == expected_decision
    assert f"Decision: {expected_decision}" in prompt
    assert "meets the required acceptance threshold and does not exceed" not in prompt
    for phrase in phrases:
        assert phrase in prompt

