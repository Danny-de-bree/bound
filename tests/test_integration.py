"""Unit tests for the framework-neutral agent control layer (Phase 1).

Pins the exact, deterministic mapping from BOUND's decision to an agent control
action and the guarantees the integration layer must uphold:

* ACCEPT   -> continue
* RETRY    -> retry
* REPLAN   -> replan
* ROLLBACK -> rollback

The layer must not invent scores, modify the BOUND decision, call an LLM, know
about any framework, or execute rollback/retries. These tests assert it merely
*translates* the deterministic :class:`~bound.models.EvaluationResult` produced
by the real contract pipeline (no LLM, no network).
"""

from __future__ import annotations

import pytest

from bound.contracts import AcceptanceCheck, RiskCheck, StepContract
from bound.evidence import CheckEvidence, ExecutionEvidence
from bound.integration import (
    AgentControlResult,
    NextAction,
    evaluate_agent_step,
    render_feedback,
)
from bound.models import BoundCriteria, BoundWeights

_REQUIRED = [
    AcceptanceCheck(id="a", description="check a"),
    AcceptanceCheck(id="b", description="check b"),
]


def _contract(*, risk_checks: list[RiskCheck] | None = None) -> StepContract:
    """Build a contract with two required acceptance checks and optional risk checks.

    Args:
        risk_checks: Optional risk checks to attach.

    Returns:
        A :class:`StepContract`.
    """
    return StepContract(
        id="step",
        description="do the step",
        goal="advance the goal",
        acceptance_checks=list(_REQUIRED),
        risk_checks=risk_checks or [],
    )


def _passed(check_id: str) -> CheckEvidence:
    """Build a passing :class:`CheckEvidence` for ``check_id``."""
    return CheckEvidence(check_id=check_id, passed=True, source="pytest")


def _failed(check_id: str) -> CheckEvidence:
    """Build a failing :class:`CheckEvidence` for ``check_id``."""
    return CheckEvidence(check_id=check_id, passed=False, source="pytest")


def test_accept_maps_to_continue() -> None:
    """ACCEPT (both required checks pass) maps to ``continue``.

    A=1.0, R=0.0, C=0.0, I=0.0 -> S=1.0 >= T=0.6 -> ACCEPT -> continue.
    """
    result = evaluate_agent_step(
        contract=_contract(),
        evidence=ExecutionEvidence(
            acceptance=[_passed("a"), _passed("b")],
            rollback_available=True,
        ),
        criteria=BoundCriteria(threshold=0.6),
    )

    assert isinstance(result, AgentControlResult)
    assert result.evaluation.decision == "ACCEPT"
    assert result.next_action == "continue"
    assert result.feedback  # non-empty deterministic feedback


def test_retry_maps_to_retry() -> None:
    """RETRY (one of two required checks passes, within retry margin) -> ``retry``.

    A=0.5 -> S=0.5; gap=0.1 <= retry_margin=0.2 -> RETRY -> retry. R=0.0 so no
    rollback; rollback_available=True keeps the risk indicator at 0.
    """
    result = evaluate_agent_step(
        contract=_contract(),
        evidence=ExecutionEvidence(
            acceptance=[_passed("a"), _failed("b")],
            rollback_available=True,
        ),
        criteria=BoundCriteria(threshold=0.6, retry_margin=0.2),
    )

    assert result.evaluation.decision == "RETRY"
    assert result.next_action == "retry"
    assert "b" in result.feedback  # names the remaining failed required check


def test_replan_maps_to_replan() -> None:
    """REPLAN (no required checks pass, gap exceeds retry margin) -> ``replan``.

    A=0.0 -> S=0.0; gap=0.6 > retry_margin=0.1 -> REPLAN -> replan.
    """
    result = evaluate_agent_step(
        contract=_contract(),
        evidence=ExecutionEvidence(
            acceptance=[_failed("a"), _failed("b")],
            rollback_available=True,
        ),
        criteria=BoundCriteria(threshold=0.6, retry_margin=0.1),
    )

    assert result.evaluation.decision == "REPLAN"
    assert result.next_action == "replan"



def test_rollback_maps_to_rollback() -> None:
    """ROLLBACK (high-severity risk check violated) overrides acceptance -> ``rollback``.

    A=1.0 but a risk check with severity 0.9 is violated (no passing evidence) ->
    R=0.9 >= rollback_risk_threshold=0.8 -> ROLLBACK -> rollback, even though the
    weighted score would otherwise clear the threshold.
    """
    contract = _contract(
        risk_checks=[RiskCheck(id="r", description="hard safety boundary", severity=0.9)],
    )
    criteria = BoundCriteria(
        threshold=0.6,
        rollback_risk_threshold=0.8,
        weights=BoundWeights(acceptance=2.0),
    )
    result = evaluate_agent_step(
        contract=contract,
        evidence=ExecutionEvidence(
            acceptance=[_passed("a"), _passed("b")],
            risks=[_failed("r")],
            rollback_available=True,
        ),
        criteria=criteria,
    )

    assert result.evaluation.scores.acceptance == pytest.approx(1.0)
    assert result.evaluation.score >= result.evaluation.threshold  # would ACCEPT
    assert result.evaluation.decision == "ROLLBACK"
    assert result.next_action == "rollback"
    assert "r" in result.feedback  # names the violated risk boundary


def test_all_four_mappings_are_exhaustive_and_deterministic() -> None:
    """Every decision maps to exactly one control action, deterministically.

    Guards that the mapping covers all four BOUND decisions with no ambiguity and
    that repeated evaluation of identical inputs yields identical results
    (including the feedback text).
    """
    mapping = {
        "ACCEPT": "continue",
        "RETRY": "retry",
        "REPLAN": "replan",
        "ROLLBACK": "rollback",
    }
    cases: list[tuple[StepContract, ExecutionEvidence, BoundCriteria]] = [
        (
            _contract(),
            ExecutionEvidence(
                acceptance=[_passed("a"), _passed("b")], rollback_available=True
            ),
            BoundCriteria(threshold=0.6),
        ),
        (
            _contract(),
            ExecutionEvidence(
                acceptance=[_passed("a"), _failed("b")], rollback_available=True
            ),
            BoundCriteria(threshold=0.6, retry_margin=0.2),
        ),
        (
            _contract(),
            ExecutionEvidence(
                acceptance=[_failed("a"), _failed("b")], rollback_available=True
            ),
            BoundCriteria(threshold=0.6, retry_margin=0.1),
        ),
        (
            _contract(
                risk_checks=[RiskCheck(id="r", description="boundary", severity=0.9)]
            ),
            ExecutionEvidence(
                acceptance=[_passed("a"), _passed("b")],
                risks=[_failed("r")],
                rollback_available=True,
            ),
            BoundCriteria(
                threshold=0.6,
                rollback_risk_threshold=0.8,
                weights=BoundWeights(acceptance=2.0),
            ),
        ),
    ]

    observed: dict[str, NextAction] = {}
    for contract, evidence, criteria in cases:
        first = evaluate_agent_step(
            contract=contract, evidence=evidence, criteria=criteria
        )
        second = evaluate_agent_step(
            contract=contract, evidence=evidence, criteria=criteria
        )
        assert first.next_action == second.next_action
        assert first.feedback == second.feedback  # deterministic feedback
        observed[first.evaluation.decision] = first.next_action

    assert observed == mapping


def test_evaluate_agent_step_does_not_modify_decision() -> None:
    """The control layer forwards the policy's decision unchanged.

    The :class:`AgentControlResult` must carry the *same* decision the
    deterministic workflow produced — the integration layer only translates, it
    never re-decides.
    """
    from bound.bound_workflow import BoundWorkflow

    contract = _contract()
    evidence = ExecutionEvidence(
        acceptance=[_passed("a"), _failed("b")], rollback_available=True
    )
    criteria = BoundCriteria(threshold=0.6, retry_margin=0.2)

    direct = BoundWorkflow().evaluate_step(
        contract=contract, evidence=evidence, criteria=criteria
    )
    result = evaluate_agent_step(
        contract=contract, evidence=evidence, criteria=criteria
    )

    assert result.evaluation.decision == direct.decision
    assert result.evaluation.score == pytest.approx(direct.score)
    assert result.evaluation.scores == direct.scores


def test_render_feedback_is_pure_function_of_inputs() -> None:
    """``render_feedback`` is a pure function of (evaluation, contract, evidence).

    Calling it twice with the same arguments yields the identical string, and it
    never raises for an ACCEPT result.
    """
    from bound.bound_workflow import BoundWorkflow

    contract = _contract()
    evidence = ExecutionEvidence(
        acceptance=[_passed("a"), _passed("b")], rollback_available=True
    )
    criteria = BoundCriteria(threshold=0.6)

    evaluation = BoundWorkflow().evaluate_step(
        contract=contract, evidence=evidence, criteria=criteria
    )
    fb1 = render_feedback(evaluation, contract=contract, evidence=evidence)
    fb2 = render_feedback(evaluation, contract=contract, evidence=evidence)

    assert fb1 == fb2
    assert "ACCEPT" in fb1


