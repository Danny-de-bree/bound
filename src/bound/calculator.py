"""BOUND calculator — the pure mathematical core (Phase 2).

Implements the deterministic, provider-agnostic bounded-utility score:

    S = (W × A) + I - R - C

Where:

* ``W`` — goal weight (:attr:`BoundCriteria.weight`).
* ``A`` — acceptance (:attr:`EvaluationScores.acceptance`).
* ``I`` — downstream influence (:attr:`EvaluationScores.influence`).
* ``R`` — risk penalty (:attr:`EvaluationScores.risk`).
* ``C`` — resource/cost penalty (:attr:`EvaluationScores.cost`).

The score is returned **raw**: it is never clamped to ``[0, 1]``, never
normalized, never rounded, and never passed through a sigmoid or any other
non-linear transform. Every term is a plain floating-point product/sum, so the
result is bit-for-bit reproducible from the inputs alone.

This module depends only on the Phase 1 Pydantic models in :mod:`bound.models`
and the standard library. It performs no network access and imports no LLM SDK.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from bound.models import BoundCriteria, EvaluationScores


class ScoreComponents(BaseModel):
    """Auditable breakdown of the BOUND score ``S = (W × A) + I - R - C``.

    Each field exposes one term of the calculation so a consumer can
    reconstruct the final score from the components alone. No field is clamped
    or rounded; ``total`` is the raw floating-point result, bit-identical to
    :func:`calculate_bound_score`.

    Attributes:
        weighted_acceptance: ``W × A`` — the goal-weighted acceptance term.
        influence: ``I`` — the downstream influence term (may be negative).
        risk: ``R`` — the risk penalty term.
        cost: ``C`` — the resource penalty term.
        total: ``S`` — ``weighted_acceptance + influence - risk - cost``.
    """

    model_config = ConfigDict(extra="forbid")

    weighted_acceptance: float
    influence: float
    risk: float
    cost: float
    total: float


def calculate_bound_score(scores: EvaluationScores, criteria: BoundCriteria) -> float:
    """Compute the raw BOUND bounded-utility score ``S``.

    The score is computed exactly as::

        S = (criteria.weight * scores.acceptance)
            + scores.influence
            - scores.risk
            - scores.cost

    The result is returned unmodified: no clamping to ``[0, 1]``, no
    normalization, no rounding, and no sigmoid or other non-linear transform.
    Once the evaluation scores are supplied the result is fully deterministic
    and requires no network access.

    Args:
        scores: The four BOUND evaluation dimensions (``A``, ``I``, ``R``,
            ``C``).
        criteria: The goal weight ``W`` (the threshold ``T`` is unused by the
            score itself; it is consumed later by the policy).

    Returns:
        The raw floating-point score ``S``.
    """
    return (criteria.weight * scores.acceptance) + scores.influence - scores.risk - scores.cost


def calculate_components(
    scores: EvaluationScores,
    criteria: BoundCriteria,
) -> ScoreComponents:
    """Compute the auditable component breakdown of the BOUND score.

    Builds the individual terms of ``S = (W × A) + I - R - C`` so the full
    calculation can be inspected and reconstructed from the result alone.
    ``total`` is bit-identical to :func:`calculate_bound_score` because the
    floating-point operations are performed in the same order.

    Args:
        scores: The four BOUND evaluation dimensions (``A``, ``I``, ``R``,
            ``C``).
        criteria: The goal weight ``W`` (the threshold ``T`` is unused by the
            score itself; it is consumed later by the policy).

    Returns:
        A :class:`ScoreComponents` with ``weighted_acceptance = W × A`` and
        ``total = weighted_acceptance + I - R - C``.
    """
    weighted_acceptance = criteria.weight * scores.acceptance
    total = weighted_acceptance + scores.influence - scores.risk - scores.cost
    return ScoreComponents(
        weighted_acceptance=weighted_acceptance,
        influence=scores.influence,
        risk=scores.risk,
        cost=scores.cost,
        total=total,
    )

