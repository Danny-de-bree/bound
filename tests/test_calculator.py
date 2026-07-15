"""Unit tests for the BOUND mathematical core (Phase 2).

These tests pin down the exact, raw behaviour of the bounded-utility score:

    S = (W × A) + I - R - C

The BOUND contract is that the score is returned *as-is*: never clamped to
``[0, 1]``, never normalized, never rounded, and never passed through a
sigmoid or any other transform. Each test below asserts one facet of that
contract and documents *why* it matters for the deterministic, auditable core.
"""

from __future__ import annotations

import pytest

from bound.calculator import ScoreComponents, calculate_bound_score, calculate_components
from bound.models import BoundCriteria, EvaluationScores

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scores(
    acceptance: float = 0.0,
    influence: float = 0.0,
    risk: float = 0.0,
    cost: float = 0.0,
) -> EvaluationScores:
    """Build an :class:`EvaluationScores` with zero defaults.

    Defaults are all zero so a test only has to set the dimensions it cares
    about, keeping the formula under test easy to read.
    """
    return EvaluationScores(
        acceptance=acceptance,
        influence=influence,
        risk=risk,
        cost=cost,
    )


def _criteria(weight: float = 1.0, threshold: float = 0.0) -> BoundCriteria:
    """Build :class:`BoundCriteria`.

    ``threshold`` is irrelevant to the score itself (it is consumed later by
    the policy), so it defaults to zero here.
    """
    return BoundCriteria(weight=weight, threshold=threshold)


# ---------------------------------------------------------------------------
# Exact formula
# ---------------------------------------------------------------------------


def test_basic_formula() -> None:
    """Canonical case: W=1, A=0.8, I=0.2, R=0.1, C=0.1 -> S = 0.8.

    The simplest readable demonstration of ``S = (W×A) + I - R - C``; it
    anchors the rest of the suite.
    """
    scores = _scores(acceptance=0.8, influence=0.2, risk=0.1, cost=0.1)
    criteria = _criteria(weight=1.0)

    assert calculate_bound_score(scores, criteria) == pytest.approx(0.8, abs=1e-9)


def test_positive_influence_raises_score_to_one() -> None:
    """A positive influence term lifts the score to exactly 1.0.

    With risk and cost held at 0.1, an influence of +0.4 raises the
    acceptance baseline (``0.8 - 0.1 - 0.1 = 0.6``) by its full value to 1.0.
    This proves influence is *added* to the score, not clamped or normalized.
    """
    scores = _scores(acceptance=0.8, influence=0.4, risk=0.1, cost=0.1)
    criteria = _criteria(weight=1.0)

    assert calculate_bound_score(scores, criteria) == pytest.approx(1.0, abs=1e-9)


def test_negative_influence_lowers_score() -> None:
    """Negative influence penalises the score: I=-0.5 -> S = 0.1.

    Influence may be negative; here a 0.8 acceptance is pulled down to 0.1 by
    a -0.5 influence minus 0.2 of risk+cost. Mirrors the positive case.
    """
    scores = _scores(acceptance=0.8, influence=-0.5, risk=0.1, cost=0.1)
    criteria = _criteria(weight=1.0)

    assert calculate_bound_score(scores, criteria) == pytest.approx(0.1, abs=1e-9)


def test_weight_above_one_scales_acceptance() -> None:
    """Weight > 1 scales acceptance: W=2, A=0.8 -> S = 1.6.

    Because ``S`` is not restricted to ``[0, 1]``, a weight above 1
    legitimately pushes the score past 1.0 — no clamping to the unit
    interval.
    """
    scores = _scores(acceptance=0.8)
    criteria = _criteria(weight=2.0)

    assert calculate_bound_score(scores, criteria) == pytest.approx(1.6, abs=1e-9)


def test_negative_final_score() -> None:
    """The score can go negative: W=1, A=0.1, I=-0.5, R=0.8, C=0.7 -> -1.9.

    Heavy penalties plus a negative influence drive ``S`` well below zero.
    The calculator must return the raw negative value, not clamp it to 0.
    """
    scores = _scores(acceptance=0.1, influence=-0.5, risk=0.8, cost=0.7)
    criteria = _criteria(weight=1.0)

    assert calculate_bound_score(scores, criteria) == pytest.approx(-1.9, abs=1e-9)


# ---------------------------------------------------------------------------
# No clamping
# ---------------------------------------------------------------------------


def test_no_upper_clamping() -> None:
    """Scores above 1.0 are preserved exactly (no upper clamp).

    ``W=2, A=1, I=1, R=0, C=0 -> S = 3.0``. A clamp to ``[0, 1]`` would
    corrupt the threshold comparison for high-weight goals, so 3.0 must
    survive untouched. Exact equality is safe here because every operand is a
    small integer.
    """
    scores = _scores(acceptance=1.0, influence=1.0)
    criteria = _criteria(weight=2.0)

    assert calculate_bound_score(scores, criteria) == 3.0


def test_no_lower_clamping() -> None:
    """Scores below 0 are preserved exactly (no lower clamp).

    ``W=1, A=0, I=-1, R=1, C=1 -> S = -3.0``. A floor at 0 would hide how
    badly an action misses the threshold, so -3.0 must survive untouched.
    """
    scores = _scores(acceptance=0.0, influence=-1.0, risk=1.0, cost=1.0)
    criteria = _criteria(weight=1.0)

    assert calculate_bound_score(scores, criteria) == -3.0


# ---------------------------------------------------------------------------
# No internal rounding
# ---------------------------------------------------------------------------


def test_no_internal_rounding_preserves_float_artifact() -> None:
    """The raw float result is returned without any rounding.

    ``0.1 + 0.2`` is not exactly ``0.3`` in IEEE-754 (it is
    ``0.30000000000000004``). If the calculator rounded internally — to 2
    decimal places, say — this artifact would collapse to ``0.3``. Asserting
    the full-precision value proves no ``round()``, quantization, or rescaling
    is applied.
    """
    scores = _scores(acceptance=0.1, influence=0.2)
    criteria = _criteria(weight=1.0)

    expected = (1.0 * 0.1) + 0.2 - 0.0 - 0.0  # 0.30000000000000004

    score = calculate_bound_score(scores, criteria)
    assert score == expected
    assert score != 0.3  # rounding to 2 dp would yield exactly 0.3


def test_no_internal_rounding_at_high_precision() -> None:
    """A many-decimal result retains full precision (no rounding at any scale).

    Inputs with 9 significant decimals guarantee the exact result is not a
    short decimal, so any internal ``round(x, k)`` for small ``k`` would
    change it.
    """
    scores = _scores(
        acceptance=0.123456789,
        influence=0.987654321,
        risk=0.111111111,
        cost=0.222222222,
    )
    criteria = _criteria(weight=1.0)

    expected = (1.0 * 0.123456789) + 0.987654321 - 0.111111111 - 0.222222222

    score = calculate_bound_score(scores, criteria)
    assert score == expected
    assert score != round(expected, 4)
    assert score != round(expected, 6)


# ---------------------------------------------------------------------------
# ScoreComponents / calculate_components
# ---------------------------------------------------------------------------


def test_components_breakdown_matches_formula() -> None:
    """``calculate_components`` exposes each term and the correct total.

    ``weighted_acceptance`` must equal ``W × A``, the other components must
    pass the raw scores through unchanged, and ``total`` must equal the full
    formula — making the result auditable end-to-end.
    """
    scores = _scores(acceptance=0.8, influence=0.2, risk=0.1, cost=0.1)
    criteria = _criteria(weight=1.0)

    components = calculate_components(scores, criteria)

    assert isinstance(components, ScoreComponents)
    assert components.weighted_acceptance == pytest.approx(0.8, abs=1e-9)
    assert components.influence == 0.2
    assert components.risk == 0.1
    assert components.cost == 0.1
    assert components.total == pytest.approx(0.8, abs=1e-9)


def test_components_weighted_acceptance_uses_weight() -> None:
    """``weighted_acceptance`` reflects the weight: W=2, A=0.8 -> 1.6."""
    scores = _scores(acceptance=0.8)
    criteria = _criteria(weight=2.0)

    components = calculate_components(scores, criteria)

    assert components.weighted_acceptance == pytest.approx(1.6, abs=1e-9)
    assert components.total == pytest.approx(1.6, abs=1e-9)


def test_components_total_matches_negative_score() -> None:
    """``total`` carries negative scores through without a floor.

    The component breakdown must be as honest as the raw score, so a negative
    ``S`` is reflected in ``total`` rather than clamped to 0.
    """
    scores = _scores(acceptance=0.1, influence=-0.5, risk=0.8, cost=0.7)
    criteria = _criteria(weight=1.0)

    components = calculate_components(scores, criteria)

    assert components.weighted_acceptance == pytest.approx(0.1, abs=1e-9)
    assert components.total == pytest.approx(-1.9, abs=1e-9)


@pytest.mark.parametrize(
    ("weight", "acceptance", "influence", "risk", "cost"),
    [
        (1.0, 0.8, 0.2, 0.1, 0.1),
        (1.0, 0.8, 0.4, 0.1, 0.1),
        (1.0, 0.8, -0.5, 0.1, 0.1),
        (2.0, 0.8, 0.0, 0.0, 0.0),
        (1.0, 0.1, -0.5, 0.8, 0.7),
        (2.0, 1.0, 1.0, 0.0, 0.0),
    ],
)
def test_components_total_equals_bound_score(
    weight: float,
    acceptance: float,
    influence: float,
    risk: float,
    cost: float,
) -> None:
    """``components.total`` is bit-identical to ``calculate_bound_score``.

    Both compute ``(W×A) + I - R - C`` in the same operation order, so the two
    entry points can never diverge. This guards the auditability invariant:
    the score reported by the calculator always equals the component total.
    """
    scores = _scores(acceptance=acceptance, influence=influence, risk=risk, cost=cost)
    criteria = _criteria(weight=weight)

    assert calculate_components(scores, criteria).total == calculate_bound_score(scores, criteria)


# ---------------------------------------------------------------------------
# Determinism & README walkthrough
# ---------------------------------------------------------------------------


def test_calculator_is_deterministic() -> None:
    """Identical inputs always yield identical outputs (pure function).

    Determinism is a core BOUND guarantee: once scores are supplied, nothing
    downstream may introduce nondeterminism.
    """
    scores = _scores(acceptance=0.8, influence=0.2, risk=0.1, cost=0.1)
    criteria = _criteria(weight=1.0)

    first = calculate_bound_score(scores, criteria)
    second = calculate_bound_score(scores, criteria)

    assert first == second


def test_flight_example_scores_point_eight() -> None:
    """README flight example: W=1, A=0.9, I=0.2, R=0.1, C=0.2 -> S = 0.8.

    The canonical BOUND walkthrough must reproduce ``S = 0.8`` deterministically
    with no LLM in the loop; this is the value the CLI/definition-of-done
    checks against.
    """
    scores = _scores(acceptance=0.9, influence=0.2, risk=0.1, cost=0.2)
    criteria = _criteria(weight=1.0, threshold=0.6)

    assert calculate_bound_score(scores, criteria) == pytest.approx(0.8, abs=1e-9)
