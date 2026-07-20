from __future__ import annotations

import pytest

from bound.calculator import calculate_components
from bound.contract_evaluator import AssuranceAssessment, PolicyGateOutcome
from bound.contracts import EvidencePolicyAction
from bound.evaluator import StaticEvaluator
from bound.models import (
    Action,
    BoundCriteria,
    BoundWeights,
    DecisionAssurance,
    EvaluationResult,
    EvaluationScores,
)
from bound.policy import BoundPolicy
from tests.conftest import _scores

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_ACTION = Action(
    description="Book the direct flight",
    goal="Travel from Paris to New York",
)


def _criteria(
    *,
    weight: float = 1.0,
    threshold: float = 0.0,
    weights: BoundWeights | None = None,
    rollback_risk_threshold: float = 0.8,
    retry_margin: float = 0.1,
) -> BoundCriteria:
    """Build :class:`BoundCriteria`.

    When ``weights`` is supplied the symmetric v0.2 weights are used directly.
    Otherwise the v0.1-style scalar ``weight`` is folded into
    ``weights.acceptance`` by the model validator. ``rollback_risk_threshold``
    and ``retry_margin`` default to the model defaults (``0.8`` and ``0.1``).
    """
    if weights is not None:
        return BoundCriteria(
            threshold=threshold,
            rollback_risk_threshold=rollback_risk_threshold,
            retry_margin=retry_margin,
            weights=weights,
        )
    return BoundCriteria(
        weight=weight,
        threshold=threshold,
        rollback_risk_threshold=rollback_risk_threshold,
        retry_margin=retry_margin,
    )


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
    on the threshold is sufficient. With risk=0 the safety boundary is not
    triggered, so the utility threshold decides.
    """
    scores = _scores(acceptance=0.6)
    criteria = _criteria(weight=1.0, threshold=0.6)

    result = _evaluate(scores, criteria)

    assert result.score == pytest.approx(0.6, abs=1e-12)
    assert result.decision == "ACCEPT"


def test_accept_above_threshold() -> None:
    """S > T accepts: README flight example, S=0.8, T=0.6 -> ACCEPT.

    The canonical walkthrough: ``S = (1.0×0.9) + 0.2 - 0.1 - 0.2 = 0.8``,
    comfortably above ``T=0.6``, with risk well below the rollback boundary.
    """
    scores = _scores(acceptance=0.9, influence=0.2, risk=0.1, cost=0.2)
    criteria = _criteria(weight=1.0, threshold=0.6)

    result = _evaluate(scores, criteria)

    assert result.score == pytest.approx(0.8, abs=1e-12)
    assert result.decision == "ACCEPT"


# ---------------------------------------------------------------------------
# ROLLBACK (safety boundary)
# ---------------------------------------------------------------------------


def test_hard_risk_rollback() -> None:
    """risk >= rollback_risk_threshold -> ROLLBACK regardless of score.

    A high-risk action (risk=0.9 >= 0.8) is rolled back even though the score
    is below threshold anyway. The safety boundary is checked *first* and is
    independent of the utility comparison.
    """
    scores = _scores(acceptance=0.3, risk=0.9, cost=0.1)
    criteria = _criteria(weight=1.0, threshold=0.6)

    result = _evaluate(scores, criteria)

    # S = 0.3 + 0 - 0.9 - 0.1 = -0.7
    assert result.score == pytest.approx(-0.7, abs=1e-12)
    assert result.decision == "ROLLBACK"


def test_rollback_at_exact_risk_boundary() -> None:
    """risk == rollback_risk_threshold -> ROLLBACK (boundary inclusive).

    With risk=0.8 and rollback_risk_threshold=0.8, the ``>=`` comparison
    triggers the safety rollback. Pins the inclusive side of the boundary.
    """
    scores = _scores(acceptance=0.3, risk=0.8, cost=0.0)
    criteria = _criteria(weight=1.0, threshold=0.6, rollback_risk_threshold=0.8)

    result = _evaluate(scores, criteria)

    assert result.decision == "ROLLBACK"


def test_high_score_but_unsafe_rolls_back() -> None:
    """A high-scoring action may still ROLLBACK if it is unsafe.

    This is the key v0.2 semantic rule: BOUND distinguishes the *utility
    threshold* (``S >= T``) from the *safety boundary*
    (``risk >= rollback_risk_threshold``). Here the score (0.65) clears the
    threshold (0.5) but the risk (0.85 >= 0.8) triggers rollback anyway.
    Safety wins over utility.
    """
    scores = _scores(acceptance=1.0, influence=0.5, risk=0.85, cost=0.0)
    criteria = _criteria(weight=1.0, threshold=0.5, rollback_risk_threshold=0.8)

    result = _evaluate(scores, criteria)

    # S = 1.0 + 0.5 - 0.85 - 0.0 = 0.65 >= T=0.5, but risk >= 0.8 -> ROLLBACK.
    assert result.score == pytest.approx(0.65, abs=1e-12)
    assert result.decision == "ROLLBACK"


# ---------------------------------------------------------------------------
# ROLLBACK strict boundary
# ---------------------------------------------------------------------------


def test_risk_just_below_rollback_boundary_does_not_rollback() -> None:
    """risk < rollback_risk_threshold does NOT force rollback.

    Pins the strict side: risk=0.79 with boundary 0.8 leaves the decision to
    the utility threshold. Here the score is below threshold and the gap
    exceeds the retry margin, so REPLAN.
    """
    scores = _scores(acceptance=0.9, influence=0.0, risk=0.79, cost=0.0)
    criteria = _criteria(weight=1.0, threshold=0.5, rollback_risk_threshold=0.8)

    result = _evaluate(scores, criteria)

    # S = 0.9 - 0.79 = 0.11; gap = 0.5 - 0.11 = 0.39 > 0.1 -> REPLAN.
    assert result.score == pytest.approx(0.11, abs=1e-12)
    assert result.decision == "REPLAN"


# ---------------------------------------------------------------------------
# RETRY (within retry margin)
# ---------------------------------------------------------------------------


def test_retry_just_below_threshold_within_margin() -> None:
    """Score just below T but within retry_margin -> RETRY.

    S=0.599999, T=0.6: gap = 0.000001 <= retry_margin=0.1. The action is
    close enough to the threshold to justify another attempt within the same
    action space. This replaces the v0.1 risk==cost tie-breaker.
    """
    scores = _scores(acceptance=0.599999)
    criteria = _criteria(weight=1.0, threshold=0.6)

    result = _evaluate(scores, criteria)

    assert result.score == pytest.approx(0.599999, abs=1e-12)
    assert result.decision == "RETRY"


def test_retry_at_exact_margin_boundary() -> None:
    """gap == retry_margin -> RETRY (boundary inclusive).

    S=0.5, T=0.6, retry_margin=0.1: gap = 0.6 - 0.5 = 0.1 == retry_margin.
    The ``<=`` comparison includes the exact boundary.
    """
    scores = _scores(acceptance=0.5)
    criteria = _criteria(weight=1.0, threshold=0.6, retry_margin=0.1)

    result = _evaluate(scores, criteria)

    assert result.score == pytest.approx(0.5, abs=1e-12)
    assert result.decision == "RETRY"


def test_retry_with_nonzero_risk_below_boundary() -> None:
    """RETRY is reachable with nonzero risk as long as risk < boundary.

    The old v0.1 rule would compare risk vs cost; v0.2 only checks the safety
    boundary. Here risk=0.05 < 0.8 (safe), and the gap is within margin.
    """
    scores = _scores(acceptance=0.55, risk=0.05, cost=0.0)
    criteria = _criteria(weight=1.0, threshold=0.6)

    result = _evaluate(scores, criteria)

    # S = 0.55 - 0.05 = 0.50; gap = 0.6 - 0.50 = 0.10 <= 0.1 -> RETRY.
    assert result.score == pytest.approx(0.50, abs=1e-12)
    assert result.decision == "RETRY"


# ---------------------------------------------------------------------------
# REPLAN (outside retry margin)
# ---------------------------------------------------------------------------


def test_replan_outside_retry_margin() -> None:
    """gap > retry_margin -> REPLAN.

    S=0.3, T=0.6: gap = 0.3 > retry_margin=0.1. The action is too far below
    the threshold to retry; a materially different strategy is needed.
    """
    scores = _scores(acceptance=0.3)
    criteria = _criteria(weight=1.0, threshold=0.6)

    result = _evaluate(scores, criteria)

    assert result.score == pytest.approx(0.3, abs=1e-12)
    assert result.decision == "REPLAN"


def test_replan_no_longer_requires_risk_equals_cost() -> None:
    """REPLAN is the fall-through, not gated on risk == cost.

    In v0.1 REPLAN only fired when risk == cost (a float-equality trap). In
    v0.2 it fires whenever the gap exceeds the retry margin. Here risk=0.2,
    cost=0.2 (equal), but the decision is REPLAN because gap=0.7 > 0.1 — not
    because of the equality.
    """
    scores = _scores(acceptance=0.3, risk=0.2, cost=0.2)
    criteria = _criteria(weight=1.0, threshold=0.6)

    result = _evaluate(scores, criteria)

    # S = 0.3 - 0.2 - 0.2 = -0.1; gap = 0.6 - (-0.1) = 0.7 > 0.1 -> REPLAN.
    assert result.score == pytest.approx(-0.1, abs=1e-12)
    assert result.decision == "REPLAN"


# ---------------------------------------------------------------------------
# Every decision reachable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("acceptance", "influence", "risk", "cost", "threshold", "expected"),
    [
        # ROLLBACK: risk >= rollback_risk_threshold (0.8 default)
        (0.3, 0.0, 0.9, 0.0, 0.6, "ROLLBACK"),
        # ACCEPT: score >= threshold, risk < 0.8
        (0.9, 0.2, 0.1, 0.2, 0.6, "ACCEPT"),
        # RETRY: gap <= retry_margin, risk < 0.8
        (0.55, 0.0, 0.0, 0.0, 0.6, "RETRY"),
        # REPLAN: gap > retry_margin, risk < 0.8
        (0.3, 0.0, 0.0, 0.0, 0.6, "REPLAN"),
    ],
)
def test_every_decision_reachable(
    acceptance: float,
    influence: float,
    risk: float,
    cost: float,
    threshold: float,
    expected: str,
) -> None:
    """All four BOUND decisions are meaningfully reachable.

    Each row exercises one decision path with default weights and the default
    ``rollback_risk_threshold=0.8`` / ``retry_margin=0.1``. This guards the
    v0.2 Definition of Done: every decision is reachable without relying on
    exact float equality.
    """
    scores = _scores(acceptance=acceptance, influence=influence, risk=risk, cost=cost)
    criteria = _criteria(weight=1.0, threshold=threshold)

    result = _evaluate(scores, criteria)

    assert result.decision == expected


# ---------------------------------------------------------------------------
# Auditability / component consistency
# ---------------------------------------------------------------------------


def test_result_components_match_calculator() -> None:
    """Reported components equal the calculator components, bit-identical.

    The policy must source components from
    :func:`~bound.calculator.calculate_components` so the audit trail in the
    :class:`EvaluationResult` cannot drift from the canonical calculation.
    Uses non-default symmetric weights to verify the weighted terms flow
    through unchanged.
    """
    scores = _scores(acceptance=0.5, influence=0.3, risk=0.2, cost=0.1)
    criteria = _criteria(
        threshold=0.6,
        weights=BoundWeights(acceptance=2.0, influence=3.0, risk=4.0, cost=5.0),
    )

    result = _evaluate(scores, criteria)
    components = calculate_components(scores, criteria)

    assert result.acceptance_component == components.weighted_acceptance
    assert result.influence_component == components.influence
    assert result.risk_component == components.risk
    assert result.cost_component == components.cost
    assert result.score == components.total


def test_result_echoes_weights_and_threshold() -> None:
    """The result echoes the weights and threshold used for the decision.

    Auditability: a consumer must be able to reconstruct ``S >= T`` from the
    result alone, so ``weights`` and ``threshold`` are carried through
    verbatim. The deprecated ``weight`` alias stays in sync with
    ``weights.acceptance``.
    """
    scores = _scores(acceptance=0.5, influence=0.1, risk=0.05, cost=0.05)
    criteria = _criteria(
        threshold=1.0,
        weights=BoundWeights(acceptance=2.0),
    )

    result = _evaluate(scores, criteria)

    assert result.weights == BoundWeights(acceptance=2.0)
    assert result.weight == 2.0  # deprecated alias
    assert result.threshold == 1.0
    # S = (2.0×0.5) + (1.0×0.1) - (1.0×0.05) - (1.0×0.05) = 1.0; S >= T -> ACCEPT.
    assert result.score == pytest.approx(1.0, abs=1e-12)
    assert result.decision == "ACCEPT"


def test_result_echoes_rollback_and_retry_metadata() -> None:
    """The result carries rollback_risk_threshold and retry_margin for audit.

    A consumer must be able to reconstruct the full decision context — not
    just ``S`` vs ``T`` but also the safety boundary and retry margin — from
    the result alone.
    """
    scores = _scores(acceptance=0.5, risk=0.0)
    criteria = _criteria(
        threshold=0.6,
        rollback_risk_threshold=0.75,
        retry_margin=0.05,
    )

    result = _evaluate(scores, criteria)

    assert result.rollback_risk_threshold == 0.75
    assert result.retry_margin == 0.05


def test_result_carries_original_scores() -> None:
    """The result references the evaluator's original :class:`EvaluationScores`.

    The full input must be recoverable from the result for auditing.
    """
    scores = _scores(acceptance=0.9, influence=0.2, risk=0.1, cost=0.2)
    criteria = _criteria(weight=1.0, threshold=0.6)

    result = _evaluate(scores, criteria)

    assert result.scores == scores

# ---------------------------------------------------------------------------
# distance_to_threshold (Phase 4)
# ---------------------------------------------------------------------------


def test_distance_to_threshold_above_threshold() -> None:
    """distance_to_threshold = S - T is positive when above threshold.

    For an accepted action, the signed distance is positive, confirming the
    margin by which the threshold was cleared.
    """
    scores = _scores(acceptance=0.9, influence=0.2, risk=0.1, cost=0.2)
    criteria = _criteria(weight=1.0, threshold=0.6)

    result = _evaluate(scores, criteria)

    # S = 0.8; distance = 0.8 - 0.6 = 0.2
    assert result.distance_to_threshold == pytest.approx(0.2, abs=1e-12)
    assert result.distance_to_threshold > 0


def test_distance_to_threshold_at_threshold() -> None:
    """distance_to_threshold = 0 when S == T exactly.

    Pins the zero case: landing exactly on the threshold yields a signed
    distance of zero.
    """
    scores = _scores(acceptance=0.6)
    criteria = _criteria(weight=1.0, threshold=0.6)

    result = _evaluate(scores, criteria)

    assert result.distance_to_threshold == pytest.approx(0.0, abs=1e-12)


def test_distance_to_threshold_below_threshold() -> None:
    """distance_to_threshold = S - T is negative when below threshold.

    For a below-threshold action, the signed distance is negative, confirming
    how far short the action fell.
    """
    scores = _scores(acceptance=0.3)
    criteria = _criteria(weight=1.0, threshold=0.6)

    result = _evaluate(scores, criteria)

    # S = 0.3; distance = 0.3 - 0.6 = -0.3
    assert result.distance_to_threshold == pytest.approx(-0.3, abs=1e-12)
    assert result.distance_to_threshold < 0



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


# ---------------------------------------------------------------------------
# Decision assurance gating (v0.7)
# ---------------------------------------------------------------------------


def _assessed(
    assurance: DecisionAssurance,
    *,
    block_action: EvidencePolicyAction | None = None,
    reasons: list[str] | None = None,
) -> AssuranceAssessment:
    """Build an :class:`AssuranceAssessment` for gating tests.

    When a block action is set, the block reasons mimic
    :meth:`ContractEvaluator.assess_assurance` (the per-check reason plus the
    "ACCEPT requires VERIFIED acceptance evidence" summary).
    """
    block_reasons: list[str] = []
    if block_action is not None:
        block_reasons = [
            f"check gated by {block_action.value}",
            "ACCEPT requires VERIFIED acceptance evidence; gated to the "
            f"contract's {block_action.value} action.",
        ]
    return AssuranceAssessment(
        assurance=assurance,
        reasons=reasons or [],
        accept_block_action=block_action,
        accept_block_reasons=block_reasons,
    )


def test_no_assessment_leaves_candidate_final_none() -> None:
    """Without an assurance assessment the new fields stay None/empty.

    Intent: pin backwards compatibility — the Action-based path leaves
    ``candidate_decision``, ``final_decision``, ``assurance`` ``None`` and
    ``assurance_reasons`` empty. ``decision`` is the sole outcome.
    """
    scores = _scores(acceptance=0.9)
    criteria = _criteria(weight=1.0, threshold=0.6)

    result = BoundPolicy().decide(scores, criteria)

    assert result.decision == "ACCEPT"
    assert result.candidate_decision is None
    assert result.final_decision is None
    assert result.assurance is None
    assert result.assurance_reasons == []


def test_verified_assurance_keeps_accept() -> None:
    """A VERIFIED assessment leaves a candidate ACCEPT unchanged."""
    scores = _scores(acceptance=0.9)
    criteria = _criteria(weight=1.0, threshold=0.6)

    result = BoundPolicy().decide(
        scores, criteria, assurance_assessment=_assessed(DecisionAssurance.VERIFIED)
    )

    assert result.decision == "ACCEPT"
    assert result.candidate_decision == "ACCEPT"
    assert result.final_decision == "ACCEPT"
    assert result.assurance is DecisionAssurance.VERIFIED


def test_mixed_assurance_keeps_accept() -> None:
    """A MIXED assessment leaves a candidate ACCEPT unchanged.

    Intent: pin that MIXED (some evaluated evidence) is acceptable and never
    blocks an ACCEPT — it surfaces the assurance but keeps the decision.
    """
    scores = _scores(acceptance=0.9)
    criteria = _criteria(weight=1.0, threshold=0.6)

    result = BoundPolicy().decide(
        scores, criteria, assurance_assessment=_assessed(DecisionAssurance.MIXED)
    )

    assert result.candidate_decision == "ACCEPT"
    assert result.final_decision == "ACCEPT"
    assert result.assurance is DecisionAssurance.MIXED


def test_claimed_assurance_blocks_accept_to_on_claimed() -> None:
    """A CLAIMED assessment gates a candidate ACCEPT to the on_claimed action.

    Intent: pin the todo example — candidate ACCEPT + CLAIMED assurance → final
    RETRY, with a reason explaining the block (ACCEPT requires verified evidence).
    """
    scores = _scores(acceptance=0.9)
    criteria = _criteria(weight=1.0, threshold=0.6)

    result = BoundPolicy().decide(
        scores, criteria,
        assurance_assessment=_assessed(
            DecisionAssurance.CLAIMED, block_action=EvidencePolicyAction.RETRY,
        ),
    )

    assert result.decision == "ACCEPT"
    assert result.candidate_decision == "ACCEPT"
    assert result.final_decision == "RETRY"
    assert result.assurance is DecisionAssurance.CLAIMED
    assert any("ACCEPT" in r and "VERIFIED" in r for r in result.assurance_reasons)


def test_insufficient_assurance_blocks_accept_to_on_missing() -> None:
    """An INSUFFICIENT assessment gates a candidate ACCEPT to the on_missing action.

    Intent: pin that missing/invalid critical evidence forces a downgrade —
    candidate ACCEPT + INSUFFICIENT → final ROLLBACK (the contract's action).
    """
    scores = _scores(acceptance=0.9)
    criteria = _criteria(weight=1.0, threshold=0.6)

    result = BoundPolicy().decide(
        scores, criteria,
        assurance_assessment=_assessed(
            DecisionAssurance.INSUFFICIENT, block_action=EvidencePolicyAction.ROLLBACK,
        ),
    )

    assert result.candidate_decision == "ACCEPT"
    assert result.final_decision == "ROLLBACK"
    assert result.assurance is DecisionAssurance.INSUFFICIENT


def test_assurance_does_not_block_non_accept() -> None:
    """Assurance gating only applies to a candidate ACCEPT.

    Intent: pin that a candidate RETRY/REPLAN/ROLLBACK is never changed by the
    assurance assessment — gating is an ACCEPT-only safety check. The assurance
    and reasons still surface for auditability, but the decision is unchanged.
    """
    scores = _scores(acceptance=0.3)  # S=0.3 < 0.6 -> REPLAN
    criteria = _criteria(weight=1.0, threshold=0.6)

    result = BoundPolicy().decide(
        scores, criteria,
        assurance_assessment=_assessed(
            DecisionAssurance.INSUFFICIENT, block_action=EvidencePolicyAction.ROLLBACK,
        ),
    )

    assert result.decision == "REPLAN"
    assert result.candidate_decision == "REPLAN"
    assert result.final_decision == "REPLAN"
    assert result.assurance is DecisionAssurance.INSUFFICIENT


def test_assurance_reasons_are_carried_through() -> None:
    """The assessment's reasons are forwarded onto the result."""
    scores = _scores(acceptance=0.9)
    criteria = _criteria(weight=1.0, threshold=0.6)
    reasons = ["critical check 'no-secrets' had MISSING evidence"]

    result = BoundPolicy().decide(
        scores, criteria,
        assurance_assessment=_assessed(
            DecisionAssurance.INSUFFICIENT,
            block_action=EvidencePolicyAction.RETRY,
            reasons=reasons,
        ),
    )

    assert result.assurance_reasons[0] == reasons[0]
    # The block reason is appended after the assessment reasons.
    assert any("ACCEPT" in r for r in result.assurance_reasons)


# ---------------------------------------------------------------------------
# Active-policy gate gating (v0.7 — todo Phase 6 + 2.2 enforcement)
# ---------------------------------------------------------------------------


def _gate(
    *,
    blocker_action: EvidencePolicyAction | None = None,
    budget_action: EvidencePolicyAction | None = None,
    effective_weights: dict[str, float] | None = None,
    policy_id: str | None = "coding-test",
    policy_version: str | None = "1.0",
    policy_hash: str | None = "sha256:abc",
) -> PolicyGateOutcome:
    """Build a :class:`PolicyGateOutcome` for ``decide`` gating tests."""
    return PolicyGateOutcome(
        blocker_failed=blocker_action is not None,
        blocker_action=blocker_action,
        blocker_reasons=["blocker failed"] if blocker_action is not None else [],
        budget_breached=budget_action is not None,
        budget_action=budget_action,
        budget_reasons=["budget breached"] if budget_action is not None else [],
        effective_weights=effective_weights or {},
        policy_id=policy_id,
        policy_version=policy_version,
        policy_hash=policy_hash,
    )


def test_policy_gate_blocker_cannot_be_compensated() -> None:
    """A failed blocker forces a downgrade even when the score implies ACCEPT.

    Intent: pin the headline guarantee (todo 2.2 / Phase 10) — a blocker
    failure cannot be offset by positive weighted signals, so a candidate
    ACCEPT becomes the blocker's forced action (RETRY here).
    """
    scores = _scores(acceptance=0.9)
    criteria = _criteria(weight=1.0, threshold=0.6)

    result = BoundPolicy().decide(
        scores, criteria, policy_gate=_gate(blocker_action=EvidencePolicyAction.RETRY),
    )

    assert result.decision == "ACCEPT"
    assert result.candidate_decision == "ACCEPT"
    assert result.final_decision == "RETRY"
    assert any("blocker" in r for r in result.assurance_reasons)


def test_policy_gate_budget_breach_forces_downgrade() -> None:
    """A breached budget forces the on_hard action on a candidate ACCEPT."""
    scores = _scores(acceptance=0.9)
    criteria = _criteria(weight=1.0, threshold=0.6)

    result = BoundPolicy().decide(
        scores, criteria, policy_gate=_gate(budget_action=EvidencePolicyAction.REPLAN),
    )

    assert result.candidate_decision == "ACCEPT"
    assert result.final_decision == "REPLAN"
    assert any("budget" in r for r in result.assurance_reasons)


def test_policy_gate_never_weakens_a_conservative_candidate() -> None:
    """The gate may only make a decision more conservative, never weaker.

    Intent: pin that a candidate ROLLBACK is never downgraded to a blocker's
    softer RETRY — the gate takes the most conservative of the two.
    """
    scores = _scores(risk=1.0)  # risk >= 0.8 threshold -> candidate ROLLBACK
    criteria = _criteria(weight=1.0, threshold=0.6)

    result = BoundPolicy().decide(
        scores, criteria, policy_gate=_gate(blocker_action=EvidencePolicyAction.RETRY),
    )

    assert result.candidate_decision == "ROLLBACK"
    assert result.final_decision == "ROLLBACK"


def test_policy_gate_records_effective_weights_and_policy_identity() -> None:
    """``decide`` forwards effective weights + policy id/version/hash to the result."""
    scores = _scores(acceptance=0.9)
    criteria = _criteria(weight=1.0, threshold=0.6)

    result = BoundPolicy().decide(
        scores, criteria,
        policy_gate=_gate(
            effective_weights={"lint": 0.5, "a": 1.0},
            policy_id="coding-default", policy_version="1.0",
            policy_hash="sha256:deadbeef",
        ),
    )

    assert result.effective_weights == {"lint": 0.5, "a": 1.0}
    assert result.active_policy_id == "coding-default"
    assert result.active_policy_version == "1.0"
    assert result.active_policy_hash == "sha256:deadbeef"


def test_policy_gate_passing_gate_keeps_candidate() -> None:
    """A gate that imposes no forced action leaves the candidate unchanged."""
    scores = _scores(acceptance=0.9)
    criteria = _criteria(weight=1.0, threshold=0.6)

    result = BoundPolicy().decide(
        scores, criteria, policy_gate=_gate(),  # no blocker/budget action
    )

    assert result.candidate_decision == "ACCEPT"
    assert result.final_decision == "ACCEPT"
    assert result.assurance_reasons == []


def test_no_assurance_and_no_gate_stays_backwards_compatible() -> None:
    """Without assurance or gate the trace fields stay ``None``/empty."""
    scores = _scores(acceptance=0.9)
    criteria = _criteria(weight=1.0, threshold=0.6)

    result = BoundPolicy().decide(scores, criteria)

    assert result.candidate_decision is None
    assert result.final_decision is None
    assert result.assurance is None
    assert result.effective_weights is None
    assert result.active_policy_id is None
    assert result.active_policy_hash is None


def test_assurance_gate_and_policy_gate_compose() -> None:
    """The most conservative of the assurance and policy gates wins.

    Intent: pin composition — a candidate ACCEPT gated by assurance to RETRY
    is further gated by a budget to REPLAN (the more conservative outcome).
    """
    scores = _scores(acceptance=0.9)
    criteria = _criteria(weight=1.0, threshold=0.6)

    result = BoundPolicy().decide(
        scores, criteria,
        assurance_assessment=_assessed(
            DecisionAssurance.CLAIMED, block_action=EvidencePolicyAction.RETRY,
        ),
        policy_gate=_gate(budget_action=EvidencePolicyAction.REPLAN),
    )

    assert result.candidate_decision == "ACCEPT"
    assert result.final_decision == "REPLAN"
