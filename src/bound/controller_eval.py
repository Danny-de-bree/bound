"""Controller evaluation infrastructure for BOUND decision quality.

Provides the :class:`ControllerEvaluator` that measures BOUND's own decision
accuracy: false ACCEPT, false RETRY, false REPLAN, and false ROLLBACK rates.
Uses :class:`IndependentVerifier` for ground-truth comparison.
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from bound.models import Decision

logger = logging.getLogger("bound.controller_eval")

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

#: Letter grades for controller health.
ControllerGrade = Literal["A", "B", "C", "D", "F"]


class DecisionRecord(BaseModel):
    """A single decision event with ground-truth comparison.

    Attributes:
        step_index: Zero-based step position.
        bound_decision: The decision BOUND produced.
        ground_truth: The correct decision according to the verifier.
        is_correct: Whether BOUND's decision matches ground truth.
        false_accept: BOUND accepted but should not have.
        false_retry: BOUND retried but should not have.
        false_replan: BOUND replanned but should not have.
        false_rollback: BOUND rolled back but should not have.
    """

    model_config = ConfigDict(extra="forbid")

    step_index: int = Field(ge=0)
    bound_decision: Decision
    ground_truth: Decision
    is_correct: bool
    false_accept: bool = False
    false_retry: bool = False
    false_replan: bool = False
    false_rollback: bool = False


_FALSE_ACCEPT: Decision = "ACCEPT"
_FALSE_RETRY: Decision = "RETRY"
_FALSE_REPLAN: Decision = "REPLAN"
_FALSE_ROLLBACK: Decision = "ROLLBACK"


class ControllerHealth(BaseModel):
    """Aggregate controller health metrics with letter grade.

    Attributes:
        total_decisions: Total number of decisions evaluated.
        correct_decisions: Number of decisions matching ground truth.
        false_accept_count: Times BOUND accepted incorrectly.
        false_retry_count: Times BOUND retried incorrectly.
        false_replan_count: Times BOUND replanned incorrectly.
        false_rollback_count: Times BOUND rolled back incorrectly.
        false_accept_rate: Rate of false accepts.
        false_retry_rate: Rate of false retries.
        false_replan_rate: Rate of false replans.
        false_rollback_rate: Rate of false rollbacks.
        overall_accuracy: Fraction of correct decisions.
        grade: Letter grade (A-F) based on overall accuracy.
        deterministic_replay_passed: Whether deterministic replay is consistent.
        policy_consistency_passed: Whether policy consistency holds.
    """

    model_config = ConfigDict(extra="forbid")

    total_decisions: int = Field(ge=0)
    correct_decisions: int = Field(ge=0)
    false_accept_count: int = Field(default=0, ge=0)
    false_retry_count: int = Field(default=0, ge=0)
    false_replan_count: int = Field(default=0, ge=0)
    false_rollback_count: int = Field(default=0, ge=0)
    false_accept_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    false_retry_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    false_replan_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    false_rollback_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    overall_accuracy: float = Field(default=0.0, ge=0.0, le=1.0)
    grade: str = "F"
    deterministic_replay_passed: bool = True
    policy_consistency_passed: bool = True


def _compute_grade(accuracy: float) -> str:
    """Map overall accuracy to a letter grade.

    Args:
        accuracy: Overall accuracy in ``[0, 1]``.

    Returns:
        A letter grade: ``A`` (>=0.95), ``B`` (>=0.85), ``C`` (>=0.70),
        ``D`` (>=0.50), or ``F`` (<0.50).
    """
    if accuracy >= 0.95:
        return "A"
    if accuracy >= 0.85:
        return "B"
    if accuracy >= 0.70:
        return "C"
    if accuracy >= 0.50:
        return "D"
    return "F"


# ---------------------------------------------------------------------------
# IndependentVerifier
# ---------------------------------------------------------------------------


class IndependentVerifier:
    """Provides ground-truth decisions for BOUND evaluation steps.

    The verifier uses deterministic heuristics based on observable signal
    states to determine what the *correct* BOUND decision should have been,
    independently of what BOUND actually produced.

    This is used by :class:`ControllerEvaluator` to compute false-positive
    and false-negative rates for each decision type.
    """

    def ground_truth(
        self,
        step_index: int,
        signals: dict[str, object],
        *,
        threshold: float = 0.6,
    ) -> Decision:
        """Compute the ground-truth decision for a step.

        Heuristic: if test_pass_rate >= 1.0 and required_checks_passed >= 1.0
        and lint_passed is True, the step should be ACCEPT. If test_pass_rate
        is below 0.3, it should be ROLLBACK. Otherwise RETRY.

        Args:
            step_index: The step's zero-based index.
            signals: Observed signal values for the step.
            threshold: Acceptance threshold (unused in current heuristic).

        Returns:
            The ground-truth :data:`~bound.models.Decision`.
        """
        tpr = signals.get("test_pass_rate")
        rcp = signals.get("required_checks_passed")
        lint = signals.get("lint_passed")

        # All gates green → ACCEPT.
        if (
            isinstance(tpr, (int, float))
            and tpr >= 1.0
            and isinstance(rcp, (int, float))
            and rcp >= 1.0
            and lint is True
        ):
            return "ACCEPT"

        # Severe failure → ROLLBACK.
        if isinstance(tpr, (int, float)) and tpr < 0.3:
            return "ROLLBACK"

        # Otherwise → RETRY (keep working).
        return "RETRY"


# ---------------------------------------------------------------------------
# ControllerEvaluator
# ---------------------------------------------------------------------------


class ControllerEvaluator:
    """Measures BOUND decision quality against ground truth.

    Computes false ACCEPT, false RETRY, false REPLAN, and false ROLLBACK
    rates by comparing BOUND's decisions against an :class:`IndependentVerifier`.
    Also checks deterministic replay and policy consistency.

    Usage::

        evaluator = ControllerEvaluator()
        health = evaluator.evaluate_from_experiment(experiment_result)
        print(f"Grade: {health.grade}, Accuracy: {health.overall_accuracy}")
    """

    def __init__(self, verifier: IndependentVerifier | None = None) -> None:
        """Create a :class:`ControllerEvaluator`.

        Args:
            verifier: Optional :class:`IndependentVerifier` instance. Creates
                a default one when omitted.
        """
        self._verifier = verifier or IndependentVerifier()

    def evaluate_decisions(
        self,
        records: list[DecisionRecord],
    ) -> ControllerHealth:
        """Compute :class:`ControllerHealth` from decision records.

        Args:
            records: List of :class:`DecisionRecord` comparing BOUND decisions
                against ground truth.

        Returns:
            A :class:`ControllerHealth` with aggregate metrics and letter grade.
        """
        n = len(records)
        if n == 0:
            return ControllerHealth(
                total_decisions=0,
                correct_decisions=0,
                overall_accuracy=0.0,
                grade="F",
            )

        correct = sum(1 for r in records if r.is_correct)
        accuracy = correct / n

        fa = sum(1 for r in records if r.false_accept)
        fr = sum(1 for r in records if r.false_retry)
        fp = sum(1 for r in records if r.false_replan)
        fb = sum(1 for r in records if r.false_rollback)

        return ControllerHealth(
            total_decisions=n,
            correct_decisions=correct,
            false_accept_count=fa,
            false_retry_count=fr,
            false_replan_count=fp,
            false_rollback_count=fb,
            false_accept_rate=fa / n,
            false_retry_rate=fr / n,
            false_replan_rate=fp / n,
            false_rollback_rate=fb / n,
            overall_accuracy=accuracy,
            grade=_compute_grade(accuracy),
        )

    def build_records(
        self,
        decisions: list[Decision],
        signals_list: list[dict[str, object]],
    ) -> list[DecisionRecord]:
        """Build decision records by comparing BOUND output to ground truth.

        Each step's BOUND decision is compared against the verifier's ground
        truth. False-positive flags are set accordingly.

        Args:
            decisions: BOUND decisions for each step.
            signals_list: Observed signal dicts for each step (same length).

        Returns:
            A list of :class:`DecisionRecord` instances.

        Raises:
            ValueError: If decisions and signals_list have different lengths.
        """
        if len(decisions) != len(signals_list):
            raise ValueError(
                f"Length mismatch: {len(decisions)} decisions vs {len(signals_list)} signal dicts."
            )

        records: list[DecisionRecord] = []
        for i, (decision, signals) in enumerate(zip(decisions, signals_list, strict=False)):
            truth = self._verifier.ground_truth(i, signals)
            is_correct = decision == truth

            false_accept = decision == _FALSE_ACCEPT and truth != _FALSE_ACCEPT
            false_retry = decision == _FALSE_RETRY and truth != _FALSE_RETRY
            false_replan = decision == _FALSE_REPLAN and truth != _FALSE_REPLAN
            false_rollback = decision == _FALSE_ROLLBACK and truth != _FALSE_ROLLBACK

            records.append(
                DecisionRecord(
                    step_index=i,
                    bound_decision=decision,
                    ground_truth=truth,
                    is_correct=is_correct,
                    false_accept=false_accept,
                    false_retry=false_retry,
                    false_replan=false_replan,
                    false_rollback=false_rollback,
                )
            )

        return records

    def deterministic_replay(
        self,
        decisions_run1: list[Decision],
        decisions_run2: list[Decision],
    ) -> bool:
        """Check whether two replays of the same inputs produce identical decisions.

        Deterministic replay is a core BOUND property: re-running the same
        inputs must always produce the same decisions.

        Args:
            decisions_run1: Decisions from the first run.
            decisions_run2: Decisions from the second run.

        Returns:
            ``True`` when both runs produce identical decision sequences.
        """
        return decisions_run1 == decisions_run2

    def policy_consistency(
        self,
        decisions_by_input: dict[str, list[Decision]],
    ) -> bool:
        """Check that same evidence always produces the same decision.

        The policy consistency property: for any given set of evidence signals,
        BOUND must always produce the same decision.

        Args:
            decisions_by_input: Mapping from evidence key to decision list.
                Only the first decision in each list is compared.

        Returns:
            ``True`` when every evidence key maps to a single consistent
            decision.
        """
        for key, decisions in decisions_by_input.items():
            if len(decisions) > 1 and len(set(decisions)) > 1:
                logger.warning(
                    "Policy inconsistency for key '%s': got %s",
                    key,
                    decisions,
                )
                return False
        return True


__all__ = [
    "ControllerEvaluator",
    "ControllerGrade",
    "ControllerHealth",
    "DecisionRecord",
    "IndependentVerifier",
]
