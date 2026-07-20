from __future__ import annotations

import pytest

from bound import (
    AcceptanceCheck,
    BoundCriteria,
    CheckEvidence,
    ExecutionEvidence,
    StepBudget,
    StepContract,
    evaluate_agent_step,
)


def _readme_contract() -> StepContract:
    """Build the exact StepContract shown in the README example.

    Returns:
        The three-required-check contract with a 20-tool-call budget.
    """
    return StepContract(
        id="add-validation",
        description="Add input validation to the registration endpoint.",
        goal="Add input validation to the registration endpoint.",
        acceptance_checks=[
            AcceptanceCheck(id="valid_input_passes", description="Valid input is accepted."),
            AcceptanceCheck(id="invalid_input_rejected", description="Invalid input is rejected."),
            AcceptanceCheck(id="edge_cases_handled", description="Edge-case input is handled."),
        ],
        budget=StepBudget(max_tool_calls=20),
    )


def _readme_criteria() -> BoundCriteria:
    """Build the exact criteria shown in the README example."""
    return BoundCriteria(threshold=0.6, retry_margin=0.2)


def _attempt1_evidence() -> ExecutionEvidence:
    """Attempt 1: 2 of 3 required checks pass; 5 of 20 tool calls used."""
    return ExecutionEvidence(
        acceptance=[
            CheckEvidence(check_id="valid_input_passes", passed=True, source="test-runner"),
            CheckEvidence(check_id="invalid_input_rejected", passed=True, source="test-runner"),
            CheckEvidence(check_id="edge_cases_handled", passed=False, source="test-runner"),
        ],
        tool_call_count=5,
    )


def _attempt2_evidence() -> ExecutionEvidence:
    """Attempt 2: all 3 required checks pass; 8 of 20 tool calls used."""
    return ExecutionEvidence(
        acceptance=[
            CheckEvidence(check_id="valid_input_passes", passed=True, source="test-runner"),
            CheckEvidence(check_id="invalid_input_rejected", passed=True, source="test-runner"),
            CheckEvidence(check_id="edge_cases_handled", passed=True, source="test-runner"),
        ],
        tool_call_count=8,
    )


def test_readme_example_attempt1_is_retry() -> None:
    """Attempt 1 yields the documented RETRY with the documented numbers.

    2 of 3 required checks pass -> A = 2/3 ≈ 0.67; 5/20 tool calls -> C = 0.25;
    no risk -> R = 0.0; default weights, I = 0.0 -> S = A − C ≈ 0.4167, which is
    below T = 0.6 by gap ≈ 0.1833 ≤ retry_margin 0.2 -> RETRY -> retry.
    """
    result = evaluate_agent_step(
        contract=_readme_contract(),
        evidence=_attempt1_evidence(),
        criteria=_readme_criteria(),
    )

    assert result.evaluation.decision == "RETRY"
    assert result.next_action == "retry"

    scores = result.evaluation.scores
    assert scores.acceptance == pytest.approx(2 / 3, abs=1e-6)
    assert scores.risk == pytest.approx(0.0, abs=1e-12)
    assert scores.cost == pytest.approx(0.25, abs=1e-12)
    assert scores.influence == pytest.approx(0.0, abs=1e-12)
    # README prints "Score: 0.42" (2-decimal rounding of 0.4167).
    assert result.evaluation.score == pytest.approx(2 / 3 - 0.25, abs=1e-6)
    assert result.evaluation.threshold == pytest.approx(0.6)
    assert result.feedback  # deterministic, non-empty, re-injectable


def test_readme_example_attempt2_is_accept() -> None:
    """Attempt 2 yields the documented ACCEPT with the documented numbers.

    3 of 3 required checks pass -> A = 1.0; 8/20 tool calls -> C = 0.40; no
    risk -> R = 0.0; default weights, I = 0.0 -> S = 1.0 − 0.40 = 0.60 >= T =
    0.6 -> ACCEPT -> continue.
    """
    result = evaluate_agent_step(
        contract=_readme_contract(),
        evidence=_attempt2_evidence(),
        criteria=_readme_criteria(),
    )

    assert result.evaluation.decision == "ACCEPT"
    assert result.next_action == "continue"

    scores = result.evaluation.scores
    assert scores.acceptance == pytest.approx(1.0, abs=1e-12)
    assert scores.risk == pytest.approx(0.0, abs=1e-12)
    assert scores.cost == pytest.approx(0.40, abs=1e-12)
    assert scores.influence == pytest.approx(0.0, abs=1e-12)
    assert result.evaluation.score == pytest.approx(0.60, abs=1e-12)
    assert result.evaluation.threshold == pytest.approx(0.6)
    assert result.feedback  # deterministic, non-empty



