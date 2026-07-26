"""Tests for controller evaluation (controller_eval.py)."""

from __future__ import annotations

import pytest

from bound.controller_eval import (
    ControllerEvaluator,
    ControllerHealth,
    DecisionRecord,
    IndependentVerifier,
    _compute_grade,
)

# ---------------------------------------------------------------------------
# _compute_grade
# ---------------------------------------------------------------------------


def test_grade_a() -> None:
    """Accuracy >= 0.95 → A."""
    assert _compute_grade(0.95) == "A"
    assert _compute_grade(0.99) == "A"
    assert _compute_grade(1.0) == "A"


def test_grade_b() -> None:
    """Accuracy >= 0.85, < 0.95 → B."""
    assert _compute_grade(0.85) == "B"
    assert _compute_grade(0.94) == "B"


def test_grade_c() -> None:
    """Accuracy >= 0.70, < 0.85 → C."""
    assert _compute_grade(0.70) == "C"
    assert _compute_grade(0.84) == "C"


def test_grade_d() -> None:
    """Accuracy >= 0.50, < 0.70 → D."""
    assert _compute_grade(0.50) == "D"
    assert _compute_grade(0.69) == "D"


def test_grade_f() -> None:
    """Accuracy < 0.50 → F."""
    assert _compute_grade(0.0) == "F"
    assert _compute_grade(0.49) == "F"


# ---------------------------------------------------------------------------
# IndependentVerifier
# ---------------------------------------------------------------------------


@pytest.fixture
def verifier() -> IndependentVerifier:
    """A fresh IndependentVerifier for each test."""
    return IndependentVerifier()


def test_verifier_accept_all_green(verifier: IndependentVerifier) -> None:
    """All gates green → ACCEPT."""
    signals: dict[str, object] = {
        "test_pass_rate": 1.0,
        "required_checks_passed": 1.0,
        "lint_passed": True,
    }
    assert verifier.ground_truth(0, signals) == "ACCEPT"


def test_verifier_rollback_severe_failure(verifier: IndependentVerifier) -> None:
    """test_pass_rate < 0.3 → ROLLBACK."""
    signals: dict[str, object] = {
        "test_pass_rate": 0.1,
    }
    assert verifier.ground_truth(0, signals) == "ROLLBACK"


def test_verifier_rollback_boundary(verifier: IndependentVerifier) -> None:
    """test_pass_rate == 0.29 still triggers ROLLBACK."""
    signals: dict[str, object] = {
        "test_pass_rate": 0.29,
    }
    assert verifier.ground_truth(0, signals) == "ROLLBACK"


def test_verifier_retry_default(verifier: IndependentVerifier) -> None:
    """Non-green, non-severe → RETRY."""
    signals: dict[str, object] = {
        "test_pass_rate": 0.5,
    }
    assert verifier.ground_truth(0, signals) == "RETRY"


def test_verifier_accept_requires_lint_true(verifier: IndependentVerifier) -> None:
    """Even with full test/check pass, lint must be True for ACCEPT."""
    signals: dict[str, object] = {
        "test_pass_rate": 1.0,
        "required_checks_passed": 1.0,
        "lint_passed": False,
    }
    assert verifier.ground_truth(0, signals) == "RETRY"


def test_verifier_missing_signals_defaults_to_retry(verifier: IndependentVerifier) -> None:
    """Missing all signals → RETRY (no evidence of green, no evidence of severe failure)."""
    signals: dict[str, object] = {}
    assert verifier.ground_truth(0, signals) == "RETRY"


# ---------------------------------------------------------------------------
# DecisionRecord
# ---------------------------------------------------------------------------


def test_decision_record_correct() -> None:
    """A correct decision record has is_correct=True, all false flags False."""
    r = DecisionRecord(
        step_index=0,
        bound_decision="ACCEPT",
        ground_truth="ACCEPT",
        is_correct=True,
    )
    assert r.is_correct is True
    assert r.false_accept is False


def test_decision_record_false_accept() -> None:
    """False ACCEPT is flagged."""
    r = DecisionRecord(
        step_index=0,
        bound_decision="ACCEPT",
        ground_truth="RETRY",
        is_correct=False,
        false_accept=True,
    )
    assert r.false_accept is True
    assert r.is_correct is False


# ---------------------------------------------------------------------------
# ControllerEvaluator.build_records
# ---------------------------------------------------------------------------


@pytest.fixture
def evaluator() -> ControllerEvaluator:
    """A fresh ControllerEvaluator."""
    return ControllerEvaluator()


def test_build_records_matching_lengths(evaluator: ControllerEvaluator) -> None:
    """Build records from BOUND decisions and signal dicts."""
    decisions = ["ACCEPT", "RETRY"]
    signals_list: list[dict[str, object]] = [
        {"test_pass_rate": 1.0, "required_checks_passed": 1.0, "lint_passed": True},
        {"test_pass_rate": 0.5},
    ]

    records = evaluator.build_records(decisions, signals_list)

    assert len(records) == 2
    assert records[0].is_correct is True  # ACCEPT matches ground truth
    assert records[0].bound_decision == "ACCEPT"
    assert records[1].is_correct is True  # RETRY matches ground truth


def test_build_records_false_accept_detected(evaluator: ControllerEvaluator) -> None:
    """BOUND accepts but verifier says RETRY → false_accept."""
    decisions = ["ACCEPT"]
    signals_list: list[dict[str, object]] = [
        {"test_pass_rate": 0.5},  # Not green → ground truth is RETRY
    ]

    records = evaluator.build_records(decisions, signals_list)

    assert records[0].is_correct is False
    assert records[0].false_accept is True
    assert records[0].ground_truth == "RETRY"


def test_build_records_false_rollback_detected(evaluator: ControllerEvaluator) -> None:
    """BOUND rolls back but verifier says RETRY → false_rollback."""
    decisions = ["ROLLBACK"]
    signals_list: list[dict[str, object]] = [
        {"test_pass_rate": 0.5},  # Modest failure → ground truth is RETRY
    ]

    records = evaluator.build_records(decisions, signals_list)

    assert records[0].is_correct is False
    assert records[0].false_rollback is True


def test_build_records_length_mismatch_raises(evaluator: ControllerEvaluator) -> None:
    """Different lengths raise ValueError."""
    with pytest.raises(ValueError, match="Length mismatch"):
        evaluator.build_records(["ACCEPT"], [])


# ---------------------------------------------------------------------------
# ControllerEvaluator.evaluate_decisions
# ---------------------------------------------------------------------------


def test_evaluate_decisions_empty() -> None:
    """Empty records → zero metrics, F grade."""
    evaluator = ControllerEvaluator()
    health = evaluator.evaluate_decisions([])

    assert health.total_decisions == 0
    assert health.correct_decisions == 0
    assert health.overall_accuracy == 0.0
    assert health.grade == "F"


def test_evaluate_decisions_perfect() -> None:
    """All correct → A grade, 100% accuracy."""
    evaluator = ControllerEvaluator()
    records = [
        DecisionRecord(
            step_index=0, bound_decision="ACCEPT", ground_truth="ACCEPT", is_correct=True
        ),
        DecisionRecord(step_index=1, bound_decision="RETRY", ground_truth="RETRY", is_correct=True),
    ]
    health = evaluator.evaluate_decisions(records)

    assert health.total_decisions == 2
    assert health.correct_decisions == 2
    assert health.overall_accuracy == 1.0
    assert health.grade == "A"
    assert health.false_accept_count == 0
    assert health.false_retry_count == 0


def test_evaluate_decisions_mixed() -> None:
    """Mixed results compute correct rates."""
    evaluator = ControllerEvaluator()
    records = [
        DecisionRecord(
            step_index=0, bound_decision="ACCEPT", ground_truth="ACCEPT", is_correct=True
        ),
        DecisionRecord(
            step_index=1,
            bound_decision="ACCEPT",
            ground_truth="RETRY",
            is_correct=False,
            false_accept=True,
        ),
        DecisionRecord(step_index=2, bound_decision="RETRY", ground_truth="RETRY", is_correct=True),
        DecisionRecord(
            step_index=3,
            bound_decision="ROLLBACK",
            ground_truth="RETRY",
            is_correct=False,
            false_rollback=True,
        ),
    ]
    health = evaluator.evaluate_decisions(records)

    assert health.total_decisions == 4
    assert health.correct_decisions == 2
    assert health.overall_accuracy == 0.5
    assert health.grade == "D"
    assert health.false_accept_count == 1
    assert health.false_accept_rate == 0.25
    assert health.false_rollback_count == 1
    assert health.false_rollback_rate == 0.25


# ---------------------------------------------------------------------------
# ControllerEvaluator.deterministic_replay
# ---------------------------------------------------------------------------


def test_deterministic_replay_identical() -> None:
    """Same decisions → replay passes."""
    evaluator = ControllerEvaluator()
    run1 = ["ACCEPT", "RETRY", "RETRY", "ACCEPT"]
    run2 = ["ACCEPT", "RETRY", "RETRY", "ACCEPT"]
    assert evaluator.deterministic_replay(run1, run2) is True


def test_deterministic_replay_different() -> None:
    """Different decisions → replay fails."""
    evaluator = ControllerEvaluator()
    run1 = ["ACCEPT", "RETRY"]
    run2 = ["ACCEPT", "ROLLBACK"]
    assert evaluator.deterministic_replay(run1, run2) is False


def test_deterministic_replay_different_lengths() -> None:
    """Different lengths → replay fails."""
    evaluator = ControllerEvaluator()
    run1 = ["ACCEPT"]
    run2 = ["ACCEPT", "RETRY"]
    assert evaluator.deterministic_replay(run1, run2) is False


# ---------------------------------------------------------------------------
# ControllerEvaluator.policy_consistency
# ---------------------------------------------------------------------------


def test_policy_consistency_all_same() -> None:
    """Each evidence key maps to the same single decision → consistent."""
    evaluator = ControllerEvaluator()
    decisions_by_input = {
        "green": ["ACCEPT"],
        "yellow": ["RETRY"],
        "red": ["ROLLBACK"],
    }
    assert evaluator.policy_consistency(decisions_by_input) is True


def test_policy_consistency_varying() -> None:
    """Same evidence key produces different decisions → inconsistent."""
    evaluator = ControllerEvaluator()
    decisions_by_input = {
        "green": ["ACCEPT", "RETRY"],  # Same evidence, different decisions!
    }
    assert evaluator.policy_consistency(decisions_by_input) is False


def test_policy_consistency_repeated_same_ok() -> None:
    """Same decision repeated is still consistent."""
    evaluator = ControllerEvaluator()
    decisions_by_input = {
        "green": ["ACCEPT", "ACCEPT", "ACCEPT"],
    }
    assert evaluator.policy_consistency(decisions_by_input) is True


# ---------------------------------------------------------------------------
# ControllerHealth model
# ---------------------------------------------------------------------------


def test_controller_health_json_roundtrip() -> None:
    """ControllerHealth round-trips through JSON."""
    h = ControllerHealth(
        total_decisions=10,
        correct_decisions=9,
        false_accept_count=1,
        overall_accuracy=0.9,
        grade="B",
    )
    json_str = h.model_dump_json()
    h2 = ControllerHealth.model_validate_json(json_str)
    assert h2.total_decisions == 10
    assert h2.grade == "B"
    assert h2.overall_accuracy == 0.9
