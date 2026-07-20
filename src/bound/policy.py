from __future__ import annotations

from bound.calculator import calculate_components
from bound.contract_evaluator import AssuranceAssessment, PolicyGateOutcome
from bound.contracts import EvidencePolicyAction
from bound.evaluator import Evaluator
from bound.models import (
    Action,
    BoundCriteria,
    Decision,
    DecisionAssurance,
    EvaluationResult,
    EvaluationScores,
    ScoreEvidence,
)

#: Maps an :class:`EvidencePolicyAction` (the contract's reaction to
#: missing/claimed evidence) to the BOUND :class:`Decision` the policy emits
#: when a candidate ACCEPT is gated down. The contract expresses *intent*
#: (retry/replan/rollback); the policy translates it into the BOUND decision
#: vocabulary so :attr:`EvaluationResult.final_decision` stays a single enum.
_POLICY_ACTION_TO_DECISION: dict[EvidencePolicyAction, str] = {
    EvidencePolicyAction.ACCEPT: "ACCEPT",
    EvidencePolicyAction.RETRY: "RETRY",
    EvidencePolicyAction.REPLAN: "REPLAN",
    EvidencePolicyAction.ROLLBACK: "ROLLBACK",
}

#: Severity ordering of BOUND decisions for choosing the most conservative
#: outcome when a candidate decision and one or more forced gate actions both
#: apply (todo 6.2). The gate may only make a decision *more* conservative,
#: never weaker: a candidate ``ROLLBACK`` is never downgraded to a blocker's
#: ``RETRY``. ``ROLLBACK`` is most severe, ``ACCEPT`` the least.
_DECISION_SEVERITY: dict[str, int] = {
    "ACCEPT": 0,
    "RETRY": 1,
    "REPLAN": 2,
    "ROLLBACK": 3,
}

#: Assurance levels that block a candidate ACCEPT (downgrading it to the
#: contract's ``on_missing``/``on_claimed`` action). VERIFIED and MIXED permit
#: ACCEPT; CLAIMED (agent self-report) and INSUFFICIENT (missing/invalid
#: evidence) do not.
_ACCEPT_BLOCKING_ASSURANCE: frozenset[DecisionAssurance] = frozenset(
    {DecisionAssurance.CLAIMED, DecisionAssurance.INSUFFICIENT}
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
        assurance_assessment: AssuranceAssessment | None = None,
        policy_gate: PolicyGateOutcome | None = None,
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

        v0.7 decision assurance: when an ``assurance_assessment`` is supplied
        (the contract workflow passes the
        :class:`~bound.contract_evaluator.ContractEvaluator`'s assessment), the
        result carries the *candidate* decision (the raw score-based one, also
        stored in :attr:`EvaluationResult.decision` for backwards
        compatibility) and the *final* gated decision. A candidate ACCEPT whose
        assurance is :attr:`CLAIMED <DecisionAssurance.CLAIMED>` or
        :attr:`INSUFFICIENT <DecisionAssurance.INSUFFICIENT>` is downgraded to
        the contract's ``on_missing``/``on_claimed`` action (mapped to a BOUND
        decision); VERIFIED and MIXED leave the candidate unchanged.

        v0.7 active-policy gate: when a ``policy_gate`` is supplied (the
        contract workflow passes the
        :class:`~bound.contract_evaluator.ContractEvaluator`'s gate outcome for
        an active :class:`~bound.policy_schema.BoundPolicyConfig`), any forced
        action from a failed blocker or breached budget is applied on top of
        the (already assurance-gated) decision — taking the *most
        conservative* of the candidate and the forced action, so a blocker can
        never be compensated by a positive score. The resolved effective
        weights and the active policy id/version/hash are forwarded onto the
        result for the trace. When no assessment *and* no gate are supplied
        (the Action-based path, or a policy used without provenance-aware
        evidence) the candidate/final/assurance/trace fields stay
        ``None``/empty and ``decision`` is the sole outcome — fully backwards
        compatible.

        Args:
            scores: The already-computed :class:`EvaluationScores` to decide
                on.
            criteria: The :class:`BoundCriteria` supplying the
                :class:`~bound.models.BoundWeights`, acceptance threshold
                ``T``, ``retry_margin``, and ``rollback_risk_threshold``.
            provenance: Optional per-dimension evidence backing ``scores``.
                When supplied it is forwarded onto the result verbatim so a
                consumer can answer "why A / I / R / C?". Defaults to ``None``.
            assurance_assessment: Optional :class:`AssuranceAssessment` from a
                :class:`~bound.contract_evaluator.ContractEvaluator`. When
                supplied, candidate/final decisions and the assurance level +
                reasons are populated and a candidate ACCEPT may be gated.
                Defaults to ``None`` (no assurance gating).
            policy_gate: Optional :class:`PolicyGateOutcome` from an active
                :class:`~bound.policy_schema.BoundPolicyConfig`. When supplied, a
                failed blocker or breached budget forces the most conservative
                decision and the effective weights + policy identity/hash are
                recorded. Defaults to ``None`` (no active-policy gating).

        Returns:
            An :class:`EvaluationResult` with the scores, weights, threshold,
            individual components, final score ``S``, signed
            ``distance_to_threshold``, the deterministic ``decision`` (the
            candidate decision), and the supplied ``provenance``. When an
            assurance assessment and/or policy gate is supplied,
            ``candidate_decision``, ``final_decision``, ``assurance``, and
            ``assurance_reasons`` are also populated, and (for an active
            policy) ``effective_weights`` and the active-policy id/version/hash.
        """
        components = calculate_components(scores, criteria)
        score = components.total
        candidate = self._decide(score=score, scores=scores, criteria=criteria)

        assurance: DecisionAssurance | None = None
        assurance_reasons: list[str] = []
        candidate_decision: Decision | None = None
        final_decision: Decision | None = None
        effective_weights: dict[str, float] | None = None
        active_policy_id: str | None = None
        active_policy_version: str | None = None
        active_policy_hash: str | None = None

        gated = assurance_assessment is not None or policy_gate is not None
        if gated:
            candidate_decision = candidate
            final_decision = candidate

        if assurance_assessment is not None:
            assurance = assurance_assessment.assurance
            assurance_reasons = list(assurance_assessment.reasons)
            if (
                final_decision == "ACCEPT"
                and assurance in _ACCEPT_BLOCKING_ASSURANCE
                and assurance_assessment.accept_block_action is not None
            ):
                final_decision = _POLICY_ACTION_TO_DECISION[
                    assurance_assessment.accept_block_action
                ]
                assurance_reasons = assurance_reasons + list(
                    assurance_assessment.accept_block_reasons
                )

        if policy_gate is not None:
            effective_weights = dict(policy_gate.effective_weights)
            active_policy_id = policy_gate.policy_id
            active_policy_version = policy_gate.policy_version
            active_policy_hash = policy_gate.policy_hash
            forced = policy_gate.forced_action
            if forced is not None:
                forced_decision = _POLICY_ACTION_TO_DECISION[forced]
                # The gate may only make the decision more conservative — a
                # blocker/budget breach can never weaken a candidate decision.
                assert final_decision is not None  # gated is True here
                if _DECISION_SEVERITY[forced_decision] > _DECISION_SEVERITY[
                    final_decision
                ]:
                    final_decision = forced_decision
                assurance_reasons = assurance_reasons + policy_gate.forced_reasons

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
            decision=candidate,
            provenance=provenance,
            candidate_decision=candidate_decision,
            final_decision=final_decision,
            assurance=assurance,
            assurance_reasons=assurance_reasons,
            effective_weights=effective_weights,
            active_policy_id=active_policy_id,
            active_policy_version=active_policy_version,
            active_policy_hash=active_policy_hash,
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

