from __future__ import annotations

import pytest

from bound.bound_workflow import BoundWorkflow
from bound.contract_evaluator import ContractEvaluator
from bound.contracts import AcceptanceCheck, StepContract
from bound.evidence import CheckEvidence, ExecutionEvidence
from bound.models import (
    Action,
    BoundCriteria,
    EvaluationScores,
)
from bound.policy import BoundPolicy


def _accepting_contract() -> StepContract:
    """Build a contract whose two required checks both pass deterministically.

    Returns:
        A :class:`StepContract` with two required acceptance checks.
    """
    return StepContract(
        id="ship",
        description="Ship the parser",
        goal="Cover the parser edge cases",
        acceptance_checks=[
            AcceptanceCheck(id="tests-pass", description="All unit tests pass"),
            AcceptanceCheck(id="lint-pass", description="The linter is clean"),
        ],
    )


def _passing_evidence() -> ExecutionEvidence:
    """Build evidence where both required checks passed and rollback is available.

    Returns:
        An :class:`ExecutionEvidence` confirming both checks passed.
    """
    return ExecutionEvidence(
        acceptance=[
            CheckEvidence(check_id="tests-pass", passed=True, source="pytest"),
            CheckEvidence(check_id="lint-pass", passed=True, source="ruff"),
        ],
        rollback_available=True,
    )


def test_minimal_workflow_no_placeholders() -> None:
    """A full contract evaluation needs no placeholder objects.

    This is the Phase 0 Definition-of-Done test: a complete contract evaluation
    expressed in ~20 lines of normal user code, constructed only from public
    domain objects (StepContract, ExecutionEvidence, BoundCriteria, BoundWorkflow)
    — no ``StaticEvaluator``, no ``BoundPolicy(StaticEvaluator(...))``, and no
    knowledge of ``policy._evaluator`` rebinding.
    """
    contract = _accepting_contract()
    evidence = _passing_evidence()
    criteria = BoundCriteria(threshold=0.6)  # default weights & risk boundary

    workflow = BoundWorkflow()
    result = workflow.evaluate_step(
        contract=contract,
        evidence=evidence,
        criteria=criteria,
    )

    # Both required checks passed -> A = 1.0; R = 0.0 (rollback available,
    # no failed risk checks); C = 0.0; I = 0.0 -> S = 1.0 >= 0.6 -> ACCEPT.
    assert result.decision == "ACCEPT"
    assert result.score == pytest.approx(1.0)
    assert result.provenance is not None
    assert set(result.provenance) == {"acceptance", "influence", "risk", "cost"}


def test_boundworkflow_defaults_contract_evaluator_and_policy() -> None:
    """``BoundWorkflow()`` wires a default ContractEvaluator and BoundPolicy.

    Guarantees the no-arg construction wires the real deterministic components,
    while leaving ``contract_generator`` unset (since ``prepare`` is optional
    for the ``evaluate_step`` path).
    """
    workflow = BoundWorkflow()

    assert isinstance(workflow.evaluator, ContractEvaluator)
    assert isinstance(workflow.policy, BoundPolicy)
    assert workflow.policy.evaluator is None  # no placeholder evaluator
    assert workflow.contract_generator is None


def test_boundpolicy_decide_works_without_evaluator() -> None:
    """``BoundPolicy().decide`` runs the decision rule with no injected evaluator.

    The contract workflow reaches the decision through ``decide``; it must never
    require an Action-based :class:`Evaluator`. This pins that a placeholder-free
    ``BoundPolicy()`` is a first-class, fully-functional construction.
    """
    policy = BoundPolicy()
    scores = EvaluationScores(acceptance=0.9, influence=0.2, risk=0.1, cost=0.2)
    criteria = BoundCriteria(threshold=0.6)

    result = policy.decide(scores, criteria)

    assert result.decision == "ACCEPT"
    assert result.scores == scores
    assert result.score == pytest.approx(0.9 + 0.2 - 0.1 - 0.2)
    assert result.provenance is None  # none supplied


def test_boundpolicy_evaluate_without_evaluator_raises() -> None:
    """The Action-based ``evaluate`` raises a clear error when no evaluator exists.

    ``BoundPolicy()`` is valid for ``decide`` but cannot score an
    :class:`Action` without an evaluator; the error must be explicit and
    actionable rather than an :class:`AttributeError` on ``None``.
    """
    policy = BoundPolicy()
    action = Action(description="do thing", goal="achieve goal")
    criteria = BoundCriteria(threshold=0.6)

    with pytest.raises(ValueError, match="requires an evaluator"):
        policy.evaluate(action, criteria)


def test_decide_matches_evaluate_for_same_scores() -> None:
    """``decide`` and ``evaluate`` assemble an identical result for the same scores.

    The decision logic must live in exactly one place. Feeding the same
    :class:`EvaluationScores` through ``BoundPolicy().decide`` and through a
    ``StaticEvaluator``-backed ``BoundPolicy.evaluate`` must yield an identical
    score, components, threshold metadata, and decision.
    """
    from bound.evaluator import StaticEvaluator

    scores = EvaluationScores(acceptance=0.55, influence=0.0, risk=0.0, cost=0.0)
    criteria = BoundCriteria(threshold=0.6, retry_margin=0.1)
    action = Action(description="do thing", goal="achieve goal")

    via_decide = BoundPolicy().decide(scores, criteria)
    via_evaluate = BoundPolicy(StaticEvaluator(scores)).evaluate(action, criteria)

    assert via_decide.decision == via_evaluate.decision == "RETRY"
    assert via_decide.score == pytest.approx(via_evaluate.score)
    assert via_decide.acceptance_component == pytest.approx(
        via_evaluate.acceptance_component
    )
    assert via_decide.distance_to_threshold == pytest.approx(
        via_evaluate.distance_to_threshold
    )


def test_prepare_without_contract_generator_raises() -> None:
    """``prepare`` raises a clear error when no generator was bound.

    A ``BoundWorkflow()`` used purely for ``evaluate_step`` legitimately has no
    generator; calling ``prepare`` on it must fail with an actionable message
    rather than an :class:`AttributeError`.
    """
    workflow = BoundWorkflow()

    with pytest.raises(RuntimeError, match="contract_generator"):
        workflow.prepare(goal="g", plan="p")


def test_workflow_backward_compat_positional_construction() -> None:
    """The original 3-arg construction still works (backwards compatibility).

    Callers that injected a specific generator/evaluator/policy (including the
    former placeholder ``BoundPolicy(StaticEvaluator(...))`` pattern) must keep
    working unchanged after the simplification.
    """
    from bound.contracts import BoundPlan, StaticContractGenerator
    from bound.evaluator import StaticEvaluator

    contract = _accepting_contract()
    plan = BoundPlan(goal="Ship the parser", steps=[contract])
    placeholder_scores = EvaluationScores(
        acceptance=0.0, influence=0.0, risk=0.0, cost=0.0
    )
    workflow = BoundWorkflow(
        StaticContractGenerator(plan),
        ContractEvaluator(),
        BoundPolicy(StaticEvaluator(placeholder_scores)),
    )

    prepared = workflow.prepare(goal="Ship the parser", plan="1. ship")
    assert prepared is plan

    result = workflow.evaluate_step(
        contract=contract,
        evidence=_passing_evidence(),
        criteria=BoundCriteria(threshold=0.6),
    )
    assert result.decision == "ACCEPT"

