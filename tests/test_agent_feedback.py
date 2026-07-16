"""Deterministic agent-feedback snapshot/golden tests (Phase 2).

Pins the exact feedback text :func:`bound.integration.render_feedback` produces
for each of the four BOUND decisions, plus the guarantees:

* feedback is a pure function of (evaluation, contract, evidence) — no LLM;
* every decision's feedback stays under 150 words;
* the per-decision behaviour matches the Phase 2 spec (ACCEPT discourages
  further optimisation, RETRY names remaining failed checks, REPLAN calls for a
  different approach, ROLLBACK names the risk boundary and asks for a safe
  state).

These golden strings are frozen so any unintentional change to the feedback
renderer is caught loudly. (Updating them is a deliberate, reviewed change.)
"""

from __future__ import annotations

from bound.bound_workflow import BoundWorkflow
from bound.contracts import AcceptanceCheck, RiskCheck, StepContract
from bound.evidence import CheckEvidence, ExecutionEvidence
from bound.integration import evaluate_agent_step, render_feedback
from bound.models import BoundCriteria, BoundWeights
from bound.prompt import word_count

_MAX_WORDS = 150


def _contract(*, risk_checks: list[RiskCheck] | None = None) -> StepContract:
    """Build a contract with two required acceptance checks and optional risk checks.

    Args:
        risk_checks: Optional risk checks to attach.

    Returns:
        A :class:`StepContract`.
    """
    return StepContract(
        id="s",
        description="d",
        goal="g",
        acceptance_checks=[
            AcceptanceCheck(id="a", description="a"),
            AcceptanceCheck(id="b", description="b"),
        ],
        risk_checks=risk_checks or [],
    )


def _passed(check_id: str) -> CheckEvidence:
    """Build a passing :class:`CheckEvidence` for ``check_id``."""
    return CheckEvidence(check_id=check_id, passed=True, source="x")


def _failed(check_id: str) -> CheckEvidence:
    """Build a failing :class:`CheckEvidence` for ``check_id``."""
    return CheckEvidence(check_id=check_id, passed=False, source="x")


def _evaluate(
    contract: StepContract,
    evidence: ExecutionEvidence,
    criteria: BoundCriteria,
) -> str:
    """Run the integration pipeline and return the rendered feedback string.

    Args:
        contract: The step contract.
        evidence: The execution evidence.
        criteria: The bound criteria.

    Returns:
        The deterministic feedback string.
    """
    return evaluate_agent_step(
        contract=contract, evidence=evidence, criteria=criteria
    ).feedback


_ACCEPT_GOLDEN = (
    "Decision: ACCEPT. The step meets the acceptance threshold "
    "(S=1.0000 >= T=0.6000) and stays within the risk boundary. It is "
    "sufficiently complete. Continue to the next objective. Do not keep "
    "optimizing this step; further refinement is unnecessary and wastes effort."
)

_RETRY_GOLDEN = (
    "Decision: RETRY. The step is close to acceptable "
    "(S=0.5000, T=0.6000, gap=0.1000). Remaining failed/missing required "
    "check(s): b. Stay with the current approach and make one focused correction."
)

_REPLAN_GOLDEN = (
    "Decision: REPLAN. The step is too far below the threshold "
    "(S=0.0000, T=0.6000, gap=0.6000) to fix by retrying. Choose a materially "
    "different approach that better addresses the goal."
)

_ROLLBACK_GOLDEN = (
    "Decision: ROLLBACK. The risk boundary is exceeded "
    "(R=0.9000 >= rollback threshold=0.8000). Violated risk check(s): r. "
    "Return to a safe state before continuing."
)



def test_accept_feedback_golden() -> None:
    """ACCEPT feedback is frozen and discourages further optimisation.

    Both required checks pass -> A=1.0, S=1.0 >= T=0.6 -> ACCEPT. The feedback
    must say the step is sufficiently complete, tell the agent to continue, and
    explicitly discourage unnecessary further optimisation.
    """
    feedback = _evaluate(
        _contract(),
        ExecutionEvidence(
            acceptance=[_passed("a"), _passed("b")], rollback_available=True
        ),
        BoundCriteria(threshold=0.6),
    )

    assert feedback == _ACCEPT_GOLDEN
    assert word_count(feedback) < _MAX_WORDS
    assert "sufficiently complete" in feedback
    assert "Do not keep optimizing" in feedback


def test_retry_feedback_golden() -> None:
    """RETRY feedback is frozen, names the remaining failed check, stays in strategy.

    One of two required checks passes -> A=0.5, S=0.5, gap=0.1 <= retry_margin=0.2
    -> RETRY. The feedback must identify the remaining failed/missing required
    check (``b``) and tell the agent to stay with the current approach.
    """
    feedback = _evaluate(
        _contract(),
        ExecutionEvidence(
            acceptance=[_passed("a"), _failed("b")], rollback_available=True
        ),
        BoundCriteria(threshold=0.6, retry_margin=0.2),
    )

    assert feedback == _RETRY_GOLDEN
    assert word_count(feedback) < _MAX_WORDS
    assert "b" in feedback
    assert "current approach" in feedback


def test_replan_feedback_golden() -> None:
    """REPLAN feedback is frozen and calls for a materially different approach.

    No required checks pass -> A=0.0, S=0.0, gap=0.6 > retry_margin=0.1 -> REPLAN.
    The feedback must explain the step is too far below the threshold and tell the
    agent to choose a materially different approach.
    """
    feedback = _evaluate(
        _contract(),
        ExecutionEvidence(
            acceptance=[_failed("a"), _failed("b")], rollback_available=True
        ),
        BoundCriteria(threshold=0.6, retry_margin=0.1),
    )

    assert feedback == _REPLAN_GOLDEN
    assert word_count(feedback) < _MAX_WORDS
    assert "materially different" in feedback



def test_rollback_feedback_golden() -> None:
    """ROLLBACK feedback is frozen, names the risk boundary, asks for a safe state.

    A=1.0 but a severity-0.9 risk check is violated -> R=0.9 >= 0.8 -> ROLLBACK.
    The feedback must identify the hard risk boundary and tell the agent to return
    to a safe state before continuing.
    """
    contract = _contract(
        risk_checks=[RiskCheck(id="r", description="hard boundary", severity=0.9)]
    )
    feedback = _evaluate(
        contract,
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
    )

    assert feedback == _ROLLBACK_GOLDEN
    assert word_count(feedback) < _MAX_WORDS
    assert "risk boundary" in feedback
    assert "safe state" in feedback



def test_all_feedback_under_150_words() -> None:
    """Every decision's feedback stays under 150 words for agent re-injection.

    A regression that bloats any branch past the limit fails here.
    """
    cases = [
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
                risk_checks=[RiskCheck(id="r", description="b", severity=0.9)]
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
    for contract, evidence, criteria in cases:
        feedback = _evaluate(contract, evidence, criteria)
        assert word_count(feedback) < _MAX_WORDS, feedback


def test_render_feedback_is_deterministic_snapshot() -> None:
    """Re-rendering identical inputs yields the identical frozen snapshot.

    Combines the determinism guarantee with the golden snapshots: the same
    (evaluation, contract, evidence) must always produce the same string, and it
    must equal the frozen golden value for each decision.
    """
    pairs = [
        (
            _contract(),
            ExecutionEvidence(
                acceptance=[_passed("a"), _passed("b")], rollback_available=True
            ),
            BoundCriteria(threshold=0.6),
            _ACCEPT_GOLDEN,
        ),
        (
            _contract(),
            ExecutionEvidence(
                acceptance=[_passed("a"), _failed("b")], rollback_available=True
            ),
            BoundCriteria(threshold=0.6, retry_margin=0.2),
            _RETRY_GOLDEN,
        ),
        (
            _contract(),
            ExecutionEvidence(
                acceptance=[_failed("a"), _failed("b")], rollback_available=True
            ),
            BoundCriteria(threshold=0.6, retry_margin=0.1),
            _REPLAN_GOLDEN,
        ),
        (
            _contract(
                risk_checks=[RiskCheck(id="r", description="b", severity=0.9)]
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
            _ROLLBACK_GOLDEN,
        ),
    ]
    for contract, evidence, criteria, golden in pairs:
        evaluation = BoundWorkflow().evaluate_step(
            contract=contract, evidence=evidence, criteria=criteria
        )
        first = render_feedback(evaluation, contract=contract, evidence=evidence)
        second = render_feedback(evaluation, contract=contract, evidence=evidence)
        assert first == second == golden


