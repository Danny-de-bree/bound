"""BOUND decision policy (Phase 4).

The :class:`BoundPolicy` is the deterministic decision-maker in the BOUND
pipeline. It wires together three concerns in a fixed order:

    Action → Evaluator → EvaluationScores → BoundCalculator → decision → EvaluationResult

Given an :class:`~bound.models.Action` and :class:`~bound.models.BoundCriteria`,
the policy:

1. Asks the injected :class:`~bound.evaluator.Evaluator` for the
   :class:`~bound.models.EvaluationScores` (``A``, ``I``, ``R``, ``C``). The
   evaluator **never** returns a decision — it only supplies raw scores.
2. Computes the component breakdown and final score ``S = (W × A) + I - R - C``
   via the existing :func:`~bound.calculator.calculate_components` so the
   result and its components stay bit-identical.
3. Applies the deterministic decision rule:

   * if ``S >= T`` (``T`` is the threshold): **ACCEPT**
   * elif ``R > C`` (risk exceeds cost): **ROLLBACK**
   * elif ``C > R`` (cost exceeds risk): **RETRY**
   * else (``R == C`` and below threshold): **REPLAN**

4. Returns an :class:`~bound.models.EvaluationResult` carrying the scores,
   weights, components, ``S``, ``T``, and the decision, so the whole
   calculation is auditable from the result alone.

The policy performs no network access and imports no LLM SDK. Once the
evaluator's scores are supplied, the decision is fully reproducible.
"""

from __future__ import annotations

from bound.calculator import calculate_components
from bound.evaluator import Evaluator
from bound.models import (
    Action,
    BoundCriteria,
    Decision,
    EvaluationResult,
)


class BoundPolicy:
    """Deterministic BOUND decision policy driven by an :class:`Evaluator`.

    The policy is intentionally evaluator-agnostic: any object satisfying the
    :class:`~bound.evaluator.Evaluator` Protocol can be injected, and the
    decision rule below is applied identically regardless of the source of
    scores. The evaluator supplies scores only; this class owns the decision.

    Decision rule (applied exactly, in order):

    * ``S >= T`` → ``ACCEPT`` (boundary-inclusive: ``S == T`` accepts).
    * ``S < T`` and ``risk > cost`` → ``ROLLBACK``.
    * ``S < T`` and ``cost > risk`` → ``RETRY``.
    * ``S < T`` and ``risk == cost`` → ``REPLAN``.

    Attributes:
        evaluator: The :class:`Evaluator` used to score each :class:`Action`.
    """

    def __init__(self, evaluator: Evaluator) -> None:
        """Bind the policy to an :class:`Evaluator`.

        Args:
            evaluator: Any object satisfying the :class:`Evaluator` Protocol.
                It is invoked once per :meth:`evaluate` call and must return
                :class:`~bound.models.EvaluationScores` (never a decision).
        """
        self._evaluator = evaluator

    @property
    def evaluator(self) -> Evaluator:
        """The evaluator bound to this policy."""
        return self._evaluator

    def evaluate(
        self,
        action: Action,
        criteria: BoundCriteria,
    ) -> EvaluationResult:
        """Run the full BOUND pipeline for ``action`` against ``criteria``.

        Execution order is fixed and auditable:

        1. Ask the evaluator for :class:`~bound.models.EvaluationScores`.
        2. Compute ``S = (W × A) + I - R - C`` and its components via
           :func:`~bound.calculator.calculate_components` (kept bit-identical to
           the raw score).
        3. Compare ``S`` to the threshold ``T`` and apply the decision rule.
        4. Assemble and return the :class:`~bound.models.EvaluationResult`.

        Args:
            action: The proposed :class:`Action` to evaluate.
            criteria: The :class:`BoundCriteria` supplying the goal weight
                ``W`` and acceptance threshold ``T``.

        Returns:
            An :class:`~bound.models.EvaluationResult` with the scores, weight,
            threshold, individual components, final score ``S``, and the
            deterministic ``decision``.
        """
        scores = self._evaluator.evaluate(action)
        components = calculate_components(scores, criteria)
        score = components.total
        decision = self._decide(score, criteria.threshold, scores.risk, scores.cost)

        return EvaluationResult(
            scores=scores,
            weight=criteria.weight,
            threshold=criteria.threshold,
            acceptance_component=components.weighted_acceptance,
            influence_component=components.influence,
            risk_component=components.risk,
            cost_component=components.cost,
            score=score,
            decision=decision,
        )

    @staticmethod
    def _decide(
        score: float,
        threshold: float,
        risk: float,
        cost: float,
    ) -> Decision:
        """Apply the deterministic BOUND decision rule.

        The rule is evaluated strictly in this order:

        * ``score >= threshold`` → ``ACCEPT``.
        * ``risk > cost`` → ``ROLLBACK``.
        * ``cost > risk`` → ``RETRY``.
        * otherwise (``risk == cost``, below threshold) → ``REPLAN``.

        Args:
            score: The final BOUND score ``S`` (unclamped, unrounded).
            threshold: The acceptance threshold ``T``.
            risk: The risk penalty ``R``.
            cost: The resource penalty ``C``.

        Returns:
            One of ``ACCEPT``, ``RETRY``, ``REPLAN``, ``ROLLBACK``.
        """
        if score >= threshold:
            return "ACCEPT"
        if risk > cost:
            return "ROLLBACK"
        if cost > risk:
            return "RETRY"
        return "REPLAN"

