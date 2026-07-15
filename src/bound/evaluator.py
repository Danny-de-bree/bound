"""BOUND evaluator abstraction (Phase 3).

The :class:`Evaluator` Protocol defines the single seam at which an external
system (an LLM-as-judge, a rule-based scorer, a test fixture, ...) produces the
four BOUND evaluation dimensions for a proposed :class:`~bound.models.Action`:

    acceptance (A), influence (I), risk (R), cost (C)

Crucially an evaluator only ever returns *scores* — it must **never** choose the
final BOUND decision (ACCEPT / RETRY / REPLAN / ROLLBACK). That decision is the
exclusive responsibility of the deterministic :class:`~bound.policy.BoundPolicy`,
which applies the threshold comparison and risk/cost tie-breakers. Keeping the
decision out of the evaluator is what makes the BOUND core auditable and
provider-agnostic: swap in any evaluator and the decision rule never changes.

This module ships one concrete, dependency-free evaluator,
:class:`StaticEvaluator`, which simply returns scores supplied at construction
time. It exists so tests, examples, and the CLI can exercise the full BOUND
pipeline without any network access, API key, or LLM SDK.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from bound.models import Action, EvaluationScores


@runtime_checkable
class Evaluator(Protocol):
    """Pluggable interface that scores a proposed :class:`Action`.

    An evaluator's sole job is to turn an :class:`Action` into the four BOUND
    evaluation dimensions wrapped in :class:`EvaluationScores`. It must **not**
    apply any threshold, weight, or decision logic — those belong to the
    deterministic :class:`~bound.policy.BoundPolicy`. This separation keeps the
    BOUND core reproducible regardless of which evaluator backs it.

    Concrete implementations may be deterministic (e.g. :class:`StaticEvaluator`
    or a rule-based scorer) or probabilistic (e.g. an LLM-as-judge adapter,
    deferred to a later phase). Either way, once the :class:`EvaluationScores`
    are produced, every downstream calculation is fully deterministic.
    """

    def evaluate(self, action: Action) -> EvaluationScores:
        """Score ``action`` and return its BOUND evaluation dimensions.

        Args:
            action: The proposed :class:`Action` to evaluate.

        Returns:
            The :class:`EvaluationScores` (``A``, ``I``, ``R``, ``C``) for the
            action. Implementations must not return a decision.
        """
        ...


class StaticEvaluator:
    """Deterministic evaluator returning pre-supplied :class:`EvaluationScores`.

    The :class:`StaticEvaluator` holds a fixed :class:`EvaluationScores`
    instance and returns it on every call to :meth:`evaluate`. It performs no
    computation, no network access, and imports no LLM SDK. Its purpose is to
    let tests, examples, and the CLI drive the BOUND pipeline end-to-end with
    fully known, reproducible inputs.

    Example:
        >>> from bound.models import Action, EvaluationScores
        >>> from bound.evaluator import StaticEvaluator
        >>> action = Action(description="Book the direct flight",
        ...                 goal="Travel from Paris to New York")
        >>> scores = EvaluationScores(acceptance=0.9, influence=0.2,
        ...                           risk=0.1, cost=0.2)
        >>> evaluator = StaticEvaluator(scores)
        >>> evaluator.evaluate(action) is scores
        True

    Attributes:
        scores: The :class:`EvaluationScores` returned for every evaluation.
        expected_action: Optional :class:`Action` that, when set, the
            evaluator asserts is passed to :meth:`evaluate`. Useful for tests
            that want to pin the action the policy forwarded to the evaluator.
    """

    def __init__(
        self,
        scores: EvaluationScores,
        *,
        expected_action: Action | None = None,
    ) -> None:
        """Store the scores (and optional expected action) to return later.

        Args:
            scores: The :class:`EvaluationScores` returned by every call to
                :meth:`evaluate`. Stored by reference so the exact object is
                reused, preserving determinism and identity.
            expected_action: When not ``None``, :meth:`evaluate` asserts that
                the action it receives equals this action. Defaults to ``None``.
        """
        self._scores = scores
        self._expected_action = expected_action

    @property
    def scores(self) -> EvaluationScores:
        """The fixed :class:`EvaluationScores` this evaluator returns."""
        return self._scores

    @property
    def expected_action(self) -> Action | None:
        """The optional action :meth:`evaluate` asserts it receives."""
        return self._expected_action

    def evaluate(self, action: Action) -> EvaluationScores:
        """Return the stored :class:`EvaluationScores`.

        When an ``expected_action`` was supplied at construction time, this
        method asserts that ``action`` equals it before returning the scores.
        This keeps the evaluator a pure, deterministic source of scores while
        still letting tests verify the policy forwards the correct action.

        Args:
            action: The proposed :class:`Action`. Unused for scoring but, when
                ``expected_action`` is set, checked for equality.

        Returns:
            The :class:`EvaluationScores` supplied at construction time. This
            never produces a decision; that is the policy's responsibility.

        Raises:
            AssertionError: If ``expected_action`` is set and ``action`` does
                not equal it.
        """
        if self._expected_action is not None and action != self._expected_action:
            raise AssertionError(
                "StaticEvaluator received an unexpected action. "
                f"expected={self._expected_action!r} actual={action!r}",
            )
        return self._scores

