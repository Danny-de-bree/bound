"""BOUND calculator — the pure mathematical core (Phase 1).

Implements the deterministic, provider-agnostic bounded-utility score:

    S = (W_A × A) + (W_I × I) - (W_R × R) - (W_C × C)

Where:

* ``W_A`` — acceptance weight (:attr:`BoundWeights.acceptance`).
* ``W_I`` — influence weight (:attr:`BoundWeights.influence`).
* ``W_R`` — risk weight (:attr:`BoundWeights.risk`).
* ``W_C`` — cost weight (:attr:`BoundWeights.cost`).
* ``A`` — acceptance (:attr:`EvaluationScores.acceptance`).
* ``I`` — downstream influence (:attr:`EvaluationScores.influence`).
* ``R`` — risk penalty (:attr:`EvaluationScores.risk`).
* ``C`` — resource/cost penalty (:attr:`EvaluationScores.cost`).

The weights are read from :attr:`BoundCriteria.weights` (a
:class:`~bound.models.BoundWeights`). Every weight defaults to ``1.0`` so the
v0.1 formula ``S = (W × A) + I - R - C`` is reproduced exactly when only the
(deprecated) acceptance ``weight`` is set: ``W_A = W``, ``W_I = W_R = W_C =
1.0``.

The score is returned **raw**: it is never clamped to ``[0, 1]``, never
normalized, never rounded, and never passed through a sigmoid or any other
non-linear transform. Every term is a plain floating-point product/sum, so the
result is bit-for-bit reproducible from the inputs alone.

This module depends only on the v0.2 Pydantic models in :mod:`bound.models`
and the standard library. It performs no network access and imports no LLM SDK.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from bound.models import BoundCriteria, EvaluationScores


class ScoreComponents(BaseModel):
    """Auditable breakdown of the BOUND score.

    The v0.2 score is ``S = (W_A×A) + (W_I×I) - (W_R×R) - (W_C×C)``. Each
    field exposes one *weighted* term of the calculation so a consumer can
    reconstruct the final score from the components alone. No field is clamped
    or rounded; ``total`` is the raw floating-point result, bit-identical to
    :func:`calculate_bound_score`.

    Attributes:
        weighted_acceptance: ``W_A × A`` — the acceptance-weighted acceptance
            term.
        influence: ``W_I × I`` — the influence-weighted influence term (may be
            negative).
        risk: ``W_R × R`` — the risk-weighted risk penalty term.
        cost: ``W_C × C`` — the cost-weighted resource penalty term.
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

        S = (criteria.weights.acceptance * scores.acceptance)
            + (criteria.weights.influence * scores.influence)
            - (criteria.weights.risk * scores.risk)
            - (criteria.weights.cost * scores.cost)

    The result is returned unmodified: no clamping to ``[0, 1]``, no
    normalization, no rounding, and no sigmoid or other non-linear transform.
    Once the evaluation scores are supplied the result is fully deterministic
    and requires no network access.

    Args:
        scores: The four BOUND evaluation dimensions (``A``, ``I``, ``R``,
            ``C``).
        criteria: The :class:`~bound.models.BoundWeights` (via
            :attr:`~bound.models.BoundCriteria.weights`) supplying ``W_A``,
            ``W_I``, ``W_R``, ``W_C``. The threshold ``T`` is unused by the
            score itself; it is consumed later by the policy.

    Returns:
        The raw floating-point score ``S``.
    """
    weights = criteria.weights
    return (
        weights.acceptance * scores.acceptance
        + weights.influence * scores.influence
        - weights.risk * scores.risk
        - weights.cost * scores.cost
    )


def calculate_components(
    scores: EvaluationScores,
    criteria: BoundCriteria,
) -> ScoreComponents:
    """Compute the auditable component breakdown of the BOUND score.

    Builds the individual weighted terms of
    ``S = (W_A×A) + (W_I×I) - (W_R×R) - (W_C×C)`` so the full calculation can
    be inspected and reconstructed from the result alone. ``total`` is
    bit-identical to :func:`calculate_bound_score` because the floating-point
    operations are performed in the same order::

        weighted_acceptance + influence - risk - cost
        == (W_A×A) + (W_I×I) - (W_R×R) - (W_C×C)

    Args:
        scores: The four BOUND evaluation dimensions (``A``, ``I``, ``R``,
            ``C``).
        criteria: The :class:`~bound.models.BoundWeights` (via
            :attr:`~bound.models.BoundCriteria.weights`) supplying ``W_A``,
            ``W_I``, ``W_R``, ``W_C``. The threshold ``T`` is unused by the
            score itself; it is consumed later by the policy.

    Returns:
        A :class:`ScoreComponents` with ``weighted_acceptance = W_A × A``,
        ``influence = W_I × I``, ``risk = W_R × R``, ``cost = W_C × C``, and
        ``total = weighted_acceptance + influence - risk - cost``.
    """
    weights = criteria.weights
    weighted_acceptance = weights.acceptance * scores.acceptance
    influence = weights.influence * scores.influence
    risk = weights.risk * scores.risk
    cost = weights.cost * scores.cost
    total = weighted_acceptance + influence - risk - cost
    return ScoreComponents(
        weighted_acceptance=weighted_acceptance,
        influence=influence,
        risk=risk,
        cost=cost,
        total=total,
    )

