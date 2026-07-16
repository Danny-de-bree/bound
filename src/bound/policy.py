"""BOUND decision policy (Phase 2 + Phase 4).

The :class:`BoundPolicy` is the deterministic decision-maker in the BOUND
pipeline. It wires together three concerns in a fixed order:

    Action → Evaluator → EvaluationScores → BoundCalculator → decision → EvaluationResult

Given an :class:`~bound.models.Action` and :class:`~bound.models.BoundCriteria`,
the policy:

1. Asks the injected :class:`~bound.evaluator.Evaluator` for the
   :class:`~bound.models.EvaluationScores` (``A``, ``I``, ``R``, ``C``). The
   evaluator **never** returns a decision — it only supplies raw scores.
2. Computes the component breakdown and final score
   ``S = (W_A×A) + (W_I×I) - (W_R×R) - (W_C×C)`` via the existing
   :func:`~bound.calculator.calculate_components` so the result and its
   components stay bit-identical.
3. Applies the deterministic decision rule (v0.2 semantics):

   * if ``scores.risk >= criteria.rollback_risk_threshold``: **ROLLBACK**
     (safety boundary — evaluated *before* the utility threshold so a
     high-scoring but unsafe action is still rolled back).
   * elif ``S >= T`` (``T`` is the threshold): **ACCEPT**
   * elif ``gap = T - S`` and ``gap <= criteria.retry_margin``: **RETRY**
   * else: **REPLAN**

4. Returns an :class:`~bound.models.EvaluationResult` carrying the scores,
   weights, components, ``S``, ``T``, ``distance_to_threshold``, and the
   decision, so the whole calculation is auditable from the result alone.

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
    EvaluationScores,
    ScoreEvidence,
)


class BoundPolicy:
    """Deterministic BOUND decision policy.

    The policy is intentionally evaluator-agnostic: any object satisfying the
    :class:`~bound.evaluator.Evaluator` Protocol can be injected, and the
    decision rule below is applied identically regardless of the source of
    scores. The evaluator (when present) supplies scores only; this class owns
    the decision via :meth:`decide`, the single assembly point for
    :class:`~bound.models.EvaluationResult`. The contract workflow feeds
    pre-computed :class:`~bound.models.EvaluationScores` from a
    :class:`~bound.contract_evaluator.ContractEvaluator` straight into
    :meth:`decide`, so it never needs (and never mutates) an injected
    evaluator — ``BoundPolicy()`` is a valid, placeholder-free construction for
    that path.

    Decision rule (applied exactly, in order):

    * ``scores.risk >= rollback_risk_threshold`` → ``ROLLBACK`` (safety
      boundary, evaluated first; a high-scoring but unsafe action still
      rolls back).
    * ``score >= threshold`` → ``ACCEPT`` (boundary-inclusive:
      ``S == T`` accepts).
    * ``gap = threshold - score`` and ``gap <= retry_margin`` → ``RETRY``
      (the action is close enough to the threshold to justify another
      attempt within the same action space).
    * otherwise → ``REPLAN`` (the action is too far below the threshold;
      choose a materially different strategy).

    This replaces the v0.1 ``risk > cost`` / ``cost > risk`` / ``risk == cost``
    rule entirely. In particular ``REPLAN`` is no longer gated on exact float
    equality of ``risk`` and ``cost``: it is simply the fall-through when the
    score is too far below the threshold to retry.

    Attributes:
        evaluator: The :class:`~bound.evaluator.Evaluator` used to score each
            :class:`Action` in the Action-based :meth:`evaluate` path, or
            ``None`` when the policy was constructed without one (the contract
            workflow uses :meth:`decide` instead).
    """

    def __init__(self, evaluator: Evaluator | None = None) -> None:
        """Optionally bind the policy to an :class:`Evaluator`.

        The evaluator is only required for the Action-based :meth:`evaluate`
        path. The contract workflow (:meth:`~bound.bound_workflow.BoundWorkflow.evaluate_step`)
        scores a *step* with a :class:`~bound.contract_evaluator.ContractEvaluator`
        and feeds the resulting :class:`~bound.models.EvaluationScores` straight
        into :meth:`decide`, so it never touches — and never needs — the
        evaluator injected here. Constructing ``BoundPolicy()`` with no
        evaluator is therefore a legitimate, placeholder-free entry point for
        the contract pipeline.

        Args:
            evaluator: Any object satisfying the :class:`Evaluator` Protocol,
                or ``None`` (the default) when the policy will only ever be
                asked to :meth:`decide` from pre-computed scores. When supplied,
                it is invoked once per :meth:`evaluate` call and must return
                :class:`~bound.models.EvaluationScores` (never a decision).
        """
        self._evaluator = evaluator

    @property
    def evaluator(self) -> Evaluator | None:
        """The evaluator bound to this policy, or ``None`` when none was bound."""
        return self._evaluator

    def decide(
        self,
        scores: EvaluationScores,
        criteria: BoundCriteria,
        *,
        provenance: dict[str, list[ScoreEvidence]] | None = None,
    ) -> EvaluationResult:
        """Apply the deterministic decision rule to *pre-computed* scores.

        This is the single place where BOUND turns ``A / I / R / C`` into a
        decision and assembles the auditable :class:`EvaluationResult`. Both
        the Action-based :meth:`evaluate` path (which scores via the injected
        evaluator) and the contract workflow (which scores via a
        :class:`~bound.contract_evaluator.ContractEvaluator`) funnel through
        here, so the calculation
        ``S = (W_A x A) + (W_I x I) - (W_R x R) - (W_C x C)`` and the decision
        rule run exactly once, in one place, regardless of where the scores
        came from.

        Args:
            scores: The already-computed :class:`EvaluationScores` to decide
                on.
            criteria: The :class:`BoundCriteria` supplying the
                :class:`~bound.models.BoundWeights`, acceptance threshold
                ``T``, ``retry_margin``, and ``rollback_risk_threshold``.
            provenance: Optional per-dimension evidence backing ``scores``.
                When supplied it is forwarded onto the result verbatim so a
                consumer can answer "why A / I / R / C?". Defaults to ``None``.

        Returns:
            An :class:`EvaluationResult` with the scores, weights, threshold,
            individual components, final score ``S``, signed
            ``distance_to_threshold``, the deterministic ``decision``, and the
            supplied ``provenance``.
        """
        components = calculate_components(scores, criteria)
        score = components.total
        decision = self._decide(score=score, scores=scores, criteria=criteria)

        return EvaluationResult(
            scores=scores,
            weights=criteria.weights,
            threshold=criteria.threshold,
            rollback_risk_threshold=criteria.rollback_risk_threshold,
            retry_margin=criteria.retry_margin,
            acceptance_component=components.weighted_acceptance,
            influence_component=components.influence,
            risk_component=components.risk,
            cost_component=components.cost,
            score=score,
            distance_to_threshold=score - criteria.threshold,
            decision=decision,
            provenance=provenance,
        )


    def evaluate(
        self,
        action: Action,
        criteria: BoundCriteria,
    ) -> EvaluationResult:
        """Run the full BOUND pipeline for ``action`` against ``criteria``.

        Execution order is fixed and auditable:

        1. Ask the bound evaluator for :class:`~bound.models.EvaluationScores`.
        2. Delegate to :meth:`decide`, which computes
           ``S = (W_A×A) + (W_I×I) - (W_R×R) - (W_C×C)`` and its components via
           :func:`~bound.calculator.calculate_components` (kept bit-identical to
           the raw score) and applies the decision rule.

        Args:
            action: The proposed :class:`Action` to evaluate.
            criteria: The :class:`BoundCriteria` supplying the
                :class:`~bound.models.BoundWeights`, acceptance threshold
                ``T``, ``retry_margin``, and ``rollback_risk_threshold``.

        Returns:
            An :class:`~bound.models.EvaluationResult` with the scores,
            weights, threshold, individual components, final score ``S``, signed
            ``distance_to_threshold``, and the deterministic ``decision``.

        Raises:
            ValueError: If no evaluator was bound at construction time. The
                Action-based path needs one to score ``action``; callers that
                only need :meth:`decide` (the contract workflow) may construct
                ``BoundPolicy()`` without one.
        """
        if self._evaluator is None:
            raise ValueError(
                "BoundPolicy.evaluate requires an evaluator; construct one with "
                "BoundPolicy(evaluator), or use BoundPolicy.decide(scores, "
                "criteria) for pre-computed scores (e.g. from a "
                "ContractEvaluator)."
            )
        scores = self._evaluator.evaluate(action)

        # Evaluators that produce auditable provenance (e.g.
        # :class:`~bound.workflow.CodingWorkflowEvaluator`) may expose a
        # ``provenance`` property. Forward it onto the result so the evidence
        # backing each score dimension flows through the policy seam.
        # Static / manual evaluators without this property yield ``None``.
        provenance = getattr(self._evaluator, "provenance", None) or None

        return self.decide(scores, criteria, provenance=provenance)

    @staticmethod
    def _decide(
        *,
        score: float,
        scores: EvaluationScores,
        criteria: BoundCriteria,
    ) -> Decision:
        """Apply the deterministic v0.2 BOUND decision rule.

        The rule is evaluated strictly in this order:

        1. ``scores.risk >= criteria.rollback_risk_threshold`` → ``ROLLBACK``.
           This is a *safety* boundary, not a utility comparison, and it is
           checked first so a high-scoring action that is still too risky is
           rolled back rather than accepted.
        2. ``score >= criteria.threshold`` → ``ACCEPT`` (boundary-inclusive).
        3. ``gap = criteria.threshold - score``; if ``gap <= retry_margin`` →
           ``RETRY``. Because step 2 already handled ``score >= threshold``,
           reaching this point guarantees ``gap > 0``, so the condition is
           effectively ``0 < gap <= retry_margin``.
        4. otherwise → ``REPLAN``.

        ``REPLAN`` is the fall-through: it no longer depends on exact float
        equality of ``risk`` and ``cost`` (the v0.1 trap), so all four
        decisions are meaningfully reachable.

        Args:
            score: The final BOUND score ``S`` (unclamped, unrounded).
            scores: The original :class:`EvaluationScores` (used for the
                ``risk`` safety boundary).
            criteria: The :class:`BoundCriteria` supplying the threshold
                ``T``, ``retry_margin``, and ``rollback_risk_threshold``.

        Returns:
            One of ``ACCEPT``, ``RETRY``, ``REPLAN``, ``ROLLBACK``.
        """
        if scores.risk >= criteria.rollback_risk_threshold:
            return "ROLLBACK"
        if score >= criteria.threshold:
            return "ACCEPT"
        gap = criteria.threshold - score
        if gap <= criteria.retry_margin:
            return "RETRY"
        return "REPLAN"

