"""Unit tests for the BOUND evaluator abstraction (Phase 3).

These tests pin down the two contracts the evaluator layer must hold:

1. :class:`Evaluator` is a structural :class:`typing.Protocol` — any object with
   an ``evaluate(self, action) -> EvaluationScores`` method satisfies it, with
   no inheritance required. This is what lets future LLM-as-judge adapters slot
   in without touching the deterministic core.
2. :class:`StaticEvaluator` returns *exactly* the scores it was given (by
   identity and by value), performs no network access, and never produces a
   decision. It is the fixture that lets the rest of the pipeline be tested
   deterministically.
"""

from __future__ import annotations

from bound.evaluator import Evaluator, StaticEvaluator
from bound.models import Action, EvaluationScores

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_ACTION = Action(
    description="Book the direct flight",
    goal="Travel from Paris to New York",
)

_SCORES = EvaluationScores(
    acceptance=0.9,
    influence=0.2,
    risk=0.1,
    cost=0.2,
)


# ---------------------------------------------------------------------------
# Protocol / structural typing
# ---------------------------------------------------------------------------


def test_static_evaluator_satisfies_evaluator_protocol() -> None:
    """StaticEvaluator structurally satisfies the Evaluator Protocol.

    This guards the replaceability invariant: the policy relies on
    isinstance-style checks (runtime_checkable) and must accept any object
    exposing ``evaluate`` — no subclassing needed.
    """
    evaluator = StaticEvaluator(_SCORES)

    assert isinstance(evaluator, Evaluator)


def test_duck_typed_evaluator_satisfies_protocol() -> None:
    """A bare class with an ``evaluate`` method satisfies the Protocol.

    Ensures future provider adapters can implement the seam without importing
    BOUND's concrete base — keeping the core free of provider dependencies.
    """

    class _AdHoc:
        def evaluate(self, action: Action) -> EvaluationScores:  # noqa: ARG002
            return _SCORES

    assert isinstance(_AdHoc(), Evaluator)


# ---------------------------------------------------------------------------
# StaticEvaluator behaviour
# ---------------------------------------------------------------------------


def test_static_evaluator_returns_supplied_scores_by_identity() -> None:
    """evaluate() returns the exact object supplied at construction.

    Identity matters: the BOUND core must be reproducible, so the evaluator
    should not silently clone or rebuild scores. Returning the same object
    also lets tests assert exact propagation through the policy.
    """
    evaluator = StaticEvaluator(_SCORES)

    assert evaluator.evaluate(_ACTION) is _SCORES


def test_static_evaluator_returns_supplied_scores_by_value() -> None:
    """The returned scores match the supplied ones field for field.

    Belt-and-braces alongside the identity check: even if an implementation
    chose to copy, the values must be preserved exactly.
    """
    evaluator = StaticEvaluator(_SCORES)

    result = evaluator.evaluate(_ACTION)

    assert result == _SCORES


def test_static_evaluator_is_deterministic_across_calls() -> None:
    """Repeated evaluations of the same action yield identical results.

    Determinism is a core BOUND guarantee; the static evaluator must be a pure
    source of scores so downstream calculations never vary between runs.
    """
    evaluator = StaticEvaluator(_SCORES)

    first = evaluator.evaluate(_ACTION)
    second = evaluator.evaluate(_ACTION)

    assert first is second


def test_static_evaluator_exposes_scores_property() -> None:
    """The ``scores`` property exposes the stored EvaluationScores.

    Lets tests and examples introspect what an evaluator will return without
    invoking ``evaluate``.
    """
    evaluator = StaticEvaluator(_SCORES)

    assert evaluator.scores is _SCORES


def test_static_evaluator_default_expected_action_is_none() -> None:
    """Without an expected_action, the evaluator performs no action check.

    Confirms the optional contract: callers are not forced to pin an action.
    """
    evaluator = StaticEvaluator(_SCORES)

    assert evaluator.expected_action is None


def test_static_evaluator_accepts_matching_expected_action() -> None:
    """With expected_action set, an equal action is accepted silently.

    Lets tests assert that the policy forwards the exact action to the
    evaluator while still returning scores normally.
    """
    evaluator = StaticEvaluator(_SCORES, expected_action=_ACTION)

    assert evaluator.expected_action == _ACTION
    assert evaluator.evaluate(_ACTION) is _SCORES


def test_static_evaluator_rejects_mismatched_expected_action() -> None:
    """A mismatched action raises AssertionError.

    Guarantees that, when a test pins the expected action, a forwarding bug in
    the policy surfaces loudly rather than silently passing wrong scores.
    """
    other = Action(description="Take the train", goal="Travel from Paris to New York")
    evaluator = StaticEvaluator(_SCORES, expected_action=_ACTION)

    try:
        evaluator.evaluate(other)
    except AssertionError:
        return
    raise AssertionError("expected AssertionError for mismatched action")
