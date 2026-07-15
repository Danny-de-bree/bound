"""Unit tests for the BOUND orchestration workflow (v0.3 Phases 9, 10, 16).

These tests pin the :class:`bound.bound_workflow.BoundWorkflow` contract mandated
by the v0.3 TODO ("Workflow" section of Phase 16):

* plan preparation (``prepare`` returns a validated :class:`BoundPlan`),
* multi-step evaluation (evaluate several steps, each producing a decision),
* the first ``ACCEPT`` stops the current optimization loop (the *caller* breaks;
  BOUND itself never loops),
* ``RETRY`` keeps the same step (the caller re-evaluates the same contract),
* ``REPLAN`` requires a new strategy (the caller prepares a new plan),
* ``ROLLBACK`` overrides acceptance (high risk + high score -> ``ROLLBACK``),
* and the :class:`~bound.contract_evaluator.ContractEvaluator` provenance flows
  onto the :class:`~bound.models.EvaluationResult`.

All tests use :class:`~bound.contracts.StaticContractGenerator` +
:class:`~bound.contract_evaluator.ContractEvaluator` +
:class:`~bound.policy.BoundPolicy`: no network access, no API key, and no LLM
SDK. The decision always comes from the deterministic policy, never from the
workflow.

Design note on the policy's evaluator
-------------------------------------
:class:`~bound.policy.BoundPolicy` requires an
:class:`~bound.evaluator.Evaluator` at construction (its
:meth:`~bound.policy.BoundPolicy.evaluate` scores an
:class:`~bound.models.Action`). In the contract workflow the scores always come
from the :class:`~bound.contract_evaluator.ContractEvaluator`, so the policy's
own evaluator is a *vestigial placeholder*: :meth:`evaluate_step` rebinds it per
call to a :class:`~bound.evaluator.StaticEvaluator` of the contract scores and
restores it afterwards (see :mod:`bound.bound_workflow`). The tests therefore
inject a throwaway :class:`~bound.evaluator.StaticEvaluator` whose scores are
never used.
"""

from __future__ import annotations

import pytest

from bound.bound_workflow import BoundWorkflow
from bound.contract_evaluator import ContractEvaluator
from bound.contracts import (
    AcceptanceCheck,
    BoundPlan,
    RiskCheck,
    StaticContractGenerator,
    StepContract,
)
from bound.evaluator import StaticEvaluator
from bound.evidence import CheckEvidence, ExecutionEvidence
from bound.models import (
    BoundCriteria,
    BoundWeights,
    EvaluationResult,
    EvaluationScores,
)
from bound.policy import BoundPolicy

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

#: Two required acceptance checks reused across the acceptance-driven tests.
_REQUIRED_CHECKS = [
    AcceptanceCheck(id="a", description="check a"),
    AcceptanceCheck(id="b", description="check b"),
]

#: All-zero :class:`EvaluationScores` used as the vestigial policy placeholder.
#: ``evaluate_step`` rebinds the policy's evaluator per call, so these scores are
#: never used to score a step — they merely satisfy the
#: :class:`~bound.policy.BoundPolicy` constructor.
_ZERO_SCORES = EvaluationScores(acceptance=0.0, influence=0.0, risk=0.0, cost=0.0)


def _passed(check_id: str, source: str = "pytest") -> CheckEvidence:
    """Build a passing :class:`CheckEvidence` for ``check_id``.

    Args:
        check_id: The check identifier to record as passed.
        source: Free-form provenance string. Defaults to ``"pytest"``.

    Returns:
        A :class:`CheckEvidence` with ``passed=True``.
    """
    return CheckEvidence(check_id=check_id, passed=True, source=source)


def _failed(check_id: str, source: str = "pytest") -> CheckEvidence:
    """Build a failing :class:`CheckEvidence` for ``check_id``.

    Args:
        check_id: The check identifier to record as failed.
        source: Free-form provenance string. Defaults to ``"pytest"``.

    Returns:
        A :class:`CheckEvidence` with ``passed=False``.
    """
    return CheckEvidence(check_id=check_id, passed=False, source=source)


def _contract(
    *,
    id: str = "step",  # noqa: A002 — `id` mirrors the StepContract field name
    description: str = "a step",
    goal: str = "the goal",
    acceptance_checks: list[AcceptanceCheck] | None = None,
    risk_checks: list[RiskCheck] | None = None,
) -> StepContract:
    """Build a :class:`StepContract` with sensible defaults.

    Defaults to :data:`_REQUIRED_CHECKS` and no risk checks so a test only sets
    the dimensions it cares about.

    Args:
        id: Stable step identifier. Defaults to ``"step"``.
        description: Human-readable step summary.
        goal: The step's goal.
        acceptance_checks: Required acceptance checks. Defaults to
            :data:`_REQUIRED_CHECKS`.
        risk_checks: Risk checks. Defaults to an empty list.

    Returns:
        A validated :class:`StepContract`.
    """
    return StepContract(
        id=id,
        description=description,
        goal=goal,
        acceptance_checks=acceptance_checks or _REQUIRED_CHECKS,
        risk_checks=risk_checks or [],
    )


def _criteria(
    *,
    threshold: float = 0.6,
    rollback_risk_threshold: float = 0.8,
    retry_margin: float = 0.1,
    weights: BoundWeights | None = None,
) -> BoundCriteria:
    """Build :class:`BoundCriteria` with documented defaults.

    Args:
        threshold: Acceptance threshold ``T``. Defaults to ``0.6``.
        rollback_risk_threshold: Hard risk boundary. Defaults to ``0.8``.
        retry_margin: RETRY margin. Defaults to ``0.1``.
        weights: Optional symmetric weights. When ``None`` the default
            all-``1.0`` weights are used.

    Returns:
        A :class:`BoundCriteria` ready for :meth:`evaluate_step`.
    """
    if weights is not None:
        return BoundCriteria(
            threshold=threshold,
            rollback_risk_threshold=rollback_risk_threshold,
            retry_margin=retry_margin,
            weights=weights,
        )
    return BoundCriteria(
        threshold=threshold,
        rollback_risk_threshold=rollback_risk_threshold,
        retry_margin=retry_margin,
    )


def _plan(steps: list[StepContract] | None = None) -> BoundPlan:
    """Build a single-step :class:`BoundPlan` with a default step.

    Args:
        steps: Optional explicit step list. Defaults to one default step.

    Returns:
        A validated :class:`BoundPlan`.
    """
    return BoundPlan(
        goal="the goal",
        steps=steps or [_contract(id="s0", description="default step", goal="the goal")],
    )


def _workflow(plan: BoundPlan | None = None) -> BoundWorkflow:
    """Build a :class:`BoundWorkflow` wired with fully deterministic parts.

    Uses a :class:`StaticContractGenerator` (the ``plan`` is returned by
    identity) and a :class:`ContractEvaluator`. The :class:`BoundPolicy` is
    given a throwaway :class:`StaticEvaluator` placeholder whose scores are
    never used: ``evaluate_step`` rebinds the policy's evaluator per call to a
    :class:`StaticEvaluator` of the contract scores and restores it afterwards.

    Args:
        plan: The :class:`BoundPlan` the generator returns. Defaults to
            :func:`_plan`.

    Returns:
        A :class:`BoundWorkflow` requiring no network or LLM.
    """
    policy = BoundPolicy(StaticEvaluator(_ZERO_SCORES))
    return BoundWorkflow(
        StaticContractGenerator(plan or _plan()),
        ContractEvaluator(),
        policy,
    )


# ---------------------------------------------------------------------------
# Plan preparation
# ---------------------------------------------------------------------------


def test_prepare_returns_validated_bound_plan() -> None:
    """prepare delegates to the ContractGenerator and returns a validated BoundPlan.

    With a :class:`StaticContractGenerator` the exact plan is returned by
    identity. The point is that the workflow hands back a Pydantic-validated
    :class:`BoundPlan` (goal + >=1 step each with >=1 acceptance check) without
    performing any decision or scoring.
    """
    plan = BoundPlan(
        goal="ship it",
        steps=[_contract(id="s1", description="step", goal="ship it")],
    )
    workflow = _workflow(plan)

    prepared = workflow.prepare(goal="ship it", plan="1. do the step", context="ctx")

    assert prepared is plan  # StaticContractGenerator returns by identity
    assert isinstance(prepared, BoundPlan)
    assert prepared.goal == "ship it"
    assert len(prepared.steps) == 1
    assert prepared.steps[0].acceptance_checks  # validated: >=1 acceptance check


def test_workflow_exposes_components() -> None:
    """contract_generator, evaluator, and policy are exposed read-only.

    Mirrors the ``policy.evaluator`` storage pattern: each injected component is
    retained for introspection.
    """
    generator = StaticContractGenerator(_plan())
    evaluator = ContractEvaluator()
    policy = BoundPolicy(StaticEvaluator(_ZERO_SCORES))

    workflow = BoundWorkflow(generator, evaluator, policy)

    assert workflow.contract_generator is generator
    assert workflow.evaluator is evaluator
    assert workflow.policy is policy


# ---------------------------------------------------------------------------
# Step evaluation
# ---------------------------------------------------------------------------


def test_evaluate_step_multi_step() -> None:
    """Evaluating each step of a multi-step plan yields a decision per step.

    The workflow evaluates one step at a time; iterating a
    :class:`BoundPlan`'s steps and calling ``evaluate_step`` for each produces
    one :class:`EvaluationResult` per step. With every acceptance check
    passing, each step ``ACCEPT``s.
    """
    steps = [
        _contract(
            id="s1",
            description="first step",
            goal="g",
            acceptance_checks=[AcceptanceCheck(id="a", description="a")],
        ),
        _contract(
            id="s2",
            description="second step",
            goal="g",
            acceptance_checks=[AcceptanceCheck(id="b", description="b")],
        ),
        _contract(
            id="s3",
            description="third step",
            goal="g",
            acceptance_checks=[AcceptanceCheck(id="c", description="c")],
        ),
    ]
    plan = BoundPlan(goal="g", steps=steps)
    workflow = _workflow(plan)
    criteria = _criteria(threshold=0.6)

    decisions: list[str] = []
    for step in plan.steps:
        result = workflow.evaluate_step(
            contract=step,
            evidence=ExecutionEvidence(
                acceptance=[_passed(step.acceptance_checks[0].id)],
            ),
            criteria=criteria,
        )
        assert isinstance(result, EvaluationResult)
        decisions.append(result.decision)

    # A=1.0 (one required check, passing) -> S=1.0 >= 0.6 -> ACCEPT for each.
    assert decisions == ["ACCEPT", "ACCEPT", "ACCEPT"]


def test_first_accept_stops_optimization_loop() -> None:
    """The caller breaks its per-step optimization loop on the first ACCEPT.

    BOUND never loops itself: ``evaluate_step`` returns exactly one
    :class:`EvaluationResult` per call. The consuming agent decides to retry
    (gather new evidence against the same contract) until ``ACCEPT``, then
    stops optimising that step. This simulates that caller loop: attempt 1
    scores below threshold (``RETRY``), attempt 2 (improved evidence) reaches
    ``ACCEPT`` and the loop breaks without exhausting the evidence sequence.
    """
    contract = _contract(
        acceptance_checks=[
            AcceptanceCheck(id="a", description="a"),
            AcceptanceCheck(id="b", description="b"),
        ],
    )
    criteria = _criteria(threshold=0.6, retry_margin=0.1)
    workflow = _workflow()

    evidence_sequence = [
        # 1 of 2 required checks pass -> A=0.5 -> S=0.5 -> gap=0.1 -> RETRY.
        ExecutionEvidence(acceptance=[_passed("a")]),
        # 2 of 2 required checks pass -> A=1.0 -> S=1.0 >= 0.6 -> ACCEPT.
        ExecutionEvidence(acceptance=[_passed("a"), _passed("b")]),
        # Would also ACCEPT, but the loop must stop before reaching it.
        ExecutionEvidence(acceptance=[_passed("a"), _passed("b")]),
    ]

    attempts = 0
    final: EvaluationResult | None = None
    for evidence in evidence_sequence:
        attempts += 1
        final = workflow.evaluate_step(
            contract=contract, evidence=evidence, criteria=criteria,
        )
        if final.decision == "ACCEPT":
            break

    assert final is not None
    assert final.decision == "ACCEPT"
    # Stopped on the first ACCEPT: did not iterate the whole sequence.
    assert attempts == 2


def test_retry_keeps_same_step() -> None:
    """On RETRY the caller re-evaluates the SAME contract (no new plan, no advance).

    ``RETRY`` means the step is close enough to try again within the same
    action space: the caller gathers new evidence and re-evaluates the
    identical :class:`StepContract`. This pins that BOUND neither advances to a
    new step nor demands a new plan on ``RETRY`` — the same contract is
    evaluated again, in contrast to ``REPLAN`` (see
    :func:`test_replan_requires_new_strategy`).
    """
    contract = _contract(
        acceptance_checks=[
            AcceptanceCheck(id="a", description="a"),
            AcceptanceCheck(id="b", description="b"),
        ],
    )
    criteria = _criteria(threshold=0.6, retry_margin=0.1)
    workflow = _workflow()

    # Attempt 1: 1 of 2 -> A=0.5 -> S=0.5, gap=0.1 <= retry_margin -> RETRY.
    first = workflow.evaluate_step(
        contract=contract,
        evidence=ExecutionEvidence(acceptance=[_passed("a")]),
        criteria=criteria,
    )
    assert first.decision == "RETRY"

    # The caller re-evaluates the SAME contract with improved evidence.
    second = workflow.evaluate_step(
        contract=contract,
        evidence=ExecutionEvidence(acceptance=[_passed("a"), _passed("b")]),
        criteria=criteria,
    )
    assert second.decision == "ACCEPT"
    # No new plan was prepared between attempts: same contract throughout.
    assert second.scores.acceptance == pytest.approx(1.0)


def test_evaluate_step_wires_contract_provenance() -> None:
    """The ContractEvaluator's per-dimension provenance flows onto the result.

    The :class:`StaticEvaluator` bridge carries no provenance, so the workflow
    forwards the :class:`ContractEvaluator`'s evidence (why ``A/I/R/C`` are what
    they are) onto :attr:`EvaluationResult.provenance` after the policy decides.
    """
    contract = _contract(
        acceptance_checks=[AcceptanceCheck(id="a", description="a")],
    )
    criteria = _criteria(threshold=0.6)
    workflow = _workflow()

    result = workflow.evaluate_step(
        contract=contract,
        evidence=ExecutionEvidence(acceptance=[_passed("a")]),
        criteria=criteria,
    )

    assert result.provenance is not None
    assert set(result.provenance.keys()) == {"acceptance", "influence", "risk", "cost"}


def test_evaluate_step_does_not_mutate_policy_evaluator() -> None:
    """evaluate_step restores the policy's evaluator after each call.

    The rebind that feeds contract scores through the policy's Action-based
    pipeline is transient: the injected policy's evaluator is unchanged once
    ``evaluate_step`` returns, so the workflow leaves no surprising side
    effect on the caller's policy.
    """
    placeholder = StaticEvaluator(_ZERO_SCORES)
    policy = BoundPolicy(placeholder)
    workflow = BoundWorkflow(
        StaticContractGenerator(_plan()),
        ContractEvaluator(),
        policy,
    )

    workflow.evaluate_step(
        contract=_contract(
            acceptance_checks=[AcceptanceCheck(id="a", description="a")],
        ),
        evidence=ExecutionEvidence(acceptance=[_passed("a")]),
        criteria=_criteria(),
    )

    assert workflow.policy.evaluator is placeholder



def test_replan_requires_new_strategy() -> None:
    """REPLAN drives the caller to prepare a new plan (a new strategy).

    ``REPLAN`` means the step is too far below the threshold to retry in the
    same action space, so the caller must produce a materially different plan
    via ``prepare`` — unlike ``RETRY``, which re-evaluates the same contract. A
    required check with no passing evidence gives ``A=0.0`` -> ``S=0.0``; with
    the threshold far above the retry margin the decision is ``REPLAN``.
    """
    contract = _contract(
        acceptance_checks=[AcceptanceCheck(id="only", description="only check")],
    )
    criteria = _criteria(threshold=0.6, retry_margin=0.1)
    workflow = _workflow()

    result = workflow.evaluate_step(
        contract=contract,
        # No evidence -> the required check has no matching evidence -> FAILED.
        evidence=ExecutionEvidence(),
        criteria=criteria,
    )

    # A=0.0 -> S=0.0; gap=0.6 > retry_margin=0.1 -> REPLAN (no rollback: R=0.0).
    assert result.decision == "REPLAN"

    # On REPLAN the caller abandons the current contract and prepares a new plan.
    original_plan = workflow.contract_generator.plan
    revised_plan = BoundPlan(
        goal="revised goal",
        steps=[
            _contract(
                id="rev",
                description="revised step",
                goal="revised goal",
                acceptance_checks=[AcceptanceCheck(id="only", description="only check")],
            ),
        ],
    )
    new_workflow = BoundWorkflow(
        StaticContractGenerator(revised_plan),
        ContractEvaluator(),
        BoundPolicy(StaticEvaluator(_ZERO_SCORES)),
    )
    prepared = new_workflow.prepare(goal="revised goal", plan="revised strategy")

    assert prepared is revised_plan
    assert prepared is not original_plan
    assert prepared.steps[0].id != original_plan.steps[0].id


def test_rollback_overrides_acceptance() -> None:
    """ROLLBACK is checked before the score threshold: high risk overrides acceptance.

    A step whose acceptance is perfect (``A=1.0``) but which violates a
    high-severity risk check (``R=0.9 >= rollback_risk_threshold=0.8``) is
    ``ROLLBACK`` even though the weighted score ``S`` clears the threshold. This
    pins the safety boundary: a high-scoring but unsafe step is rolled back
    rather than accepted.
    """
    contract = _contract(
        acceptance_checks=[AcceptanceCheck(id="a", description="check a")],
        risk_checks=[RiskCheck(id="r", description="high-severity risk", severity=0.9)],
    )
    # acceptance weight 2.0 so S clears the threshold despite the risk penalty:
    # S = 2.0*1.0 + 0 - 1.0*0.9 - 0 = 1.1 >= 0.6 -> would ACCEPT, but R=0.9 >= 0.8.
    criteria = _criteria(
        threshold=0.6,
        rollback_risk_threshold=0.8,
        weights=BoundWeights(acceptance=2.0),
    )
    workflow = _workflow()

    result = workflow.evaluate_step(
        contract=contract,
        evidence=ExecutionEvidence(
            acceptance=[_passed("a")],
            risks=[_failed("r")],
            rollback_available=True,  # avoid the rollback-unavailable risk indicator
        ),
        criteria=criteria,
    )

    assert result.scores.acceptance == pytest.approx(1.0)
    assert result.scores.risk == pytest.approx(0.9)
    assert result.score >= result.threshold  # would otherwise ACCEPT
    assert result.decision == "ROLLBACK"  # safety boundary overrides acceptance

