"""BOUND semantic-blind-spot example (v0.2 fix).

A coding agent can be *mechanically correct but semantically wrong*: every
green gate says "go" while the code does not actually satisfy the goal. v0.2
ships two deterministic mitigations that narrow this blind spot without an LLM:

1. **Evidence-breadth floor** on acceptance. Acceptance is now
   ``A = (mean of available completion signals) × (n_available / 4)``, where
   the four completion signals are ``test_pass_rate``,
   ``required_checks_passed``, ``lint_passed`` and ``type_check_passed``. A
   single green gate (e.g. only ``lint_passed``) can no longer read as
   ``A = 1.0``: with one of four gates available the breadth floor is ``0.25``.

2. **Test-mutation signals** on risk. ``tests_removed`` and ``tests_modified``
   feed the risk dimension. Deleting failing tests to force a green suite is
   the canonical "mechanically correct, semantically wrong" pattern, so any
   removal is a full surprise-risk indicator (``1.0``); modification is a
   milder, graded, capped signal. ``tests_added`` is *not* a risk signal —
   adding tests is good evidence.

This script runs three scenarios at ``T = 0.7`` and prints, for each, the
signals, the resulting ``A / R / C / I / S``, the decision, the signed
distance to threshold, and a one-line "why this matters" note. The "AFTER"
numbers are computed live by :class:`CodingWorkflowEvaluator`; the "BEFORE"
line states what the pre-v0.2 formula would have produced, to make the blind
spot visible.

Caveat (scenario 2): when *all four* gates are green the breadth floor is
``1.0`` and ``A`` stays ``1.0`` (backward compatible). The breadth floor only
catches *thin* evidence — it cannot tell whether the tests actually cover the
requirement. That residual blind spot needs a semantic evaluator (v0.3+); the
point of this example is to be honest about where deterministic signals stop.

Run with::

    uv run python examples/semantic_blind_spot.py
"""

from __future__ import annotations

import sys

from bound.models import Action, BoundCriteria, CodingWorkflowSignals
from bound.policy import BoundPolicy
from bound.workflow import CodingWorkflowEvaluator

#: Acceptance threshold used across all three scenarios.
THRESHOLD = 0.7

#: Width of the rule drawn between scenario headers.
_RULE = "=" * 66


def _score_line(
    tag: str,
    a: float,
    r: float,
    c: float,
    i: float,
    s: float,
    decision: str,
    dist: float,
) -> str:
    """Format one BEFORE/AFTER score row as an aligned single line.

    Args:
        tag: ``"BEFORE"`` or ``"AFTER"`` label.
        a: Acceptance ``A``.
        r: Risk ``R``.
        c: Cost ``C``.
        i: Influence ``I``.
        s: Final bounded-utility score ``S``.
        decision: The BOUND decision.
        dist: Signed distance to threshold ``S - T``.

    Returns:
        A fixed-width string such as
        ``"  BEFORE: A=1.00  R=0.00  ...  -> ACCEPT   (dist=+0.30)"``.
    """
    return (
        f"  {tag:<5}: A={a:4.2f}  R={r:4.2f}  C={c:4.2f}  "
        f"I={i:4.2f}  S={s:5.2f}  -> {decision:<7} (dist={dist:+.2f})"
    )


def _run_scenario(
    *,
    index: int,
    title: str,
    action: Action,
    signals: CodingWorkflowSignals,
    signal_line: str,
    before: tuple[float, float, float, float, float, str],
    why: str,
    expected_score: float,
    expected_decision: str,
) -> None:
    """Evaluate one scenario live and print a BEFORE/AFTER comparison.

    The "AFTER" row is the real output of :class:`CodingWorkflowEvaluator`
    via :class:`BoundPolicy`; the "BEFORE" row is the documented pre-v0.2
    behaviour. A small assert locks the live values to the documented
    expectation so the example is self-verifying.

    Args:
        index: Scenario number (1-3) for the header.
        title: Short human-readable scenario title.
        action: The realistic coding-task :class:`Action`.
        signals: The workflow signals for this scenario.
        signal_line: One-line description of the signals (the "signals" row).
        before: Pre-v0.2 values as ``(A, R, C, I, S, decision)``.
        why: One-line "why this matters" note.
        expected_score: Documented post-fix ``S`` to assert against.
        expected_decision: Documented post-fix decision to assert against.
    """
    evaluator = CodingWorkflowEvaluator(signals)
    result = BoundPolicy(evaluator).evaluate(action, BoundCriteria(threshold=THRESHOLD))

    b_a, b_r, b_c, b_i, b_s, b_decision = before

    print(_RULE)
    print(f"SCENARIO {index} — {title}")
    print(_RULE)
    print(f"Action : {action.description}")
    print(f"Goal   : {action.goal}")
    print(f"Signals: {signal_line}")
    print()
    print(_score_line("BEFORE", b_a, b_r, b_c, b_i, b_s, b_decision, b_s - THRESHOLD))
    print(
        _score_line(
            "AFTER",
            result.scores.acceptance,
            result.scores.risk,
            result.scores.cost,
            result.scores.influence,
            result.score,
            result.decision,
            result.distance_to_threshold,
        )
    )
    print()
    print(f"  Why this matters: {why}")
    print()

    # Lock the live computation to the documented v0.2 expectation.
    assert result.score == expected_score, (
        f"scenario {index}: expected S={expected_score}, got {result.score}"
    )
    assert result.decision == expected_decision, (
        f"scenario {index}: expected {expected_decision}, got {result.decision}"
    )


def main() -> int:
    """Run the three blind-spot scenarios and print BEFORE/AFTER comparisons.

    Returns:
        ``0`` on success (the example is illustrative and never fails the
        process once the asserts hold), so the script can be chained in demos
        and CI smoke runs.
    """
    print("BOUND semantic-blind-spot example (v0.2 fix, no LLM)\n")
    print(
        f"Threshold T={THRESHOLD}  "
        "(retry_margin=0.1, rollback_risk_threshold=0.8, all weights=1.0)\n"
    )

    # ------------------------------------------------------------------ #
    # Scenario 1 — Thin evidence: only the linter is green.              #
    # The breadth floor (1/4 = 0.25) is what catches this.               #
    # ------------------------------------------------------------------ #
    _run_scenario(
        index=1,
        title="Thin evidence — only the linter is green",
        action=Action(
            description="Implement rate limiting on the API",
            goal="Cap requests at 100/min per client",
            context="Agent ran only the linter, no tests or type-check.",
        ),
        signals=CodingWorkflowSignals(lint_passed=True),
        signal_line="CodingWorkflowSignals(lint_passed=True)  -> 1 of 4 gates available",
        before=(1.0, 0.0, 0.0, 0.0, 1.0, "ACCEPT"),
        why=(
            "a single green gate used to read as 'fully accepted' (A=1.0). The "
            "evidence-breadth floor (1/4=0.25) now caps confidence at what was "
            "actually observed, so thin evidence replans instead of silently "
            "accepting."
        ),
        expected_score=-0.5,
        expected_decision="REPLAN",
    )

    # ------------------------------------------------------------------ #
    # Scenario 2 — All four gates green (backward compatible).           #
    # The breadth floor is 1.0 here, so A stays 1.0 -> ACCEPT. This is   #
    # the RESIDUAL blind spot: signals can't see test coverage of the    #
    # actual requirement. Needs a semantic evaluator (v0.3+).            #
    # ------------------------------------------------------------------ #
    _run_scenario(
        index=2,
        title="All four gates green — the residual blind spot",
        action=Action(
            description="Implement user login",
            goal="Add an authenticated /login endpoint returning a JWT",
            context="Tests pass, lint clean, types clean — but tests only assert 200 OK.",
        ),
        signals=CodingWorkflowSignals(
            test_pass_rate=1.0,
            lint_passed=True,
            type_check_passed=True,
            required_checks_passed=1.0,
        ),
        signal_line=(
            "all four completion gates green  -> 4 of 4 gates available "
            "(breadth floor = 1.0)"
        ),
        before=(1.0, 0.0, 0.0, 0.0, 1.0, "ACCEPT"),
        why=(
            "all four gates are green, so the breadth floor is 1.0 and A stays "
            "1.0 (backward compatible). This is the RESIDUAL blind spot: "
            "deterministic signals cannot tell whether the tests actually assert "
            "the goal — they may only check '200 OK', not auth correctness. "
            "Catching that needs a semantic evaluator (v0.3+), which is out of "
            "scope for v0.2's deterministic signals."
        ),
        expected_score=1.0,
        expected_decision="ACCEPT",
    )

    # ------------------------------------------------------------------ #
    # Scenario 3 — Test-deletion gaming: all green, but tests_removed=3. #
    # tests_removed>0 adds a 1.0 risk indicator -> R rises, S drops.     #
    # ------------------------------------------------------------------ #
    _run_scenario(
        index=3,
        title="Test-deletion gaming — green suite, tests removed",
        action=Action(
            description="Fix the flaky checkout test",
            goal="Make the checkout suite green without regressions",
            context="Agent deleted 3 failing tests to force a green run.",
        ),
        signals=CodingWorkflowSignals(
            test_pass_rate=1.0,
            lint_passed=True,
            type_check_passed=True,
            required_checks_passed=1.0,
            tests_removed=3,
        ),
        signal_line=(
            "all four gates green + tests_removed=3  -> deletion is a full "
            "surprise-risk indicator"
        ),
        before=(1.0, 0.0, 0.0, 0.0, 1.0, "ACCEPT"),
        why=(
            "before the fix tests_removed was not a signal, so deleting failing "
            "tests left R=0 and the suite 'accepted'. The new tests_removed>0 "
            "risk indicator raises R (mean(1.0, 0.0)=0.5), dropping S below T, so "
            "the gaming is caught."
        ),
        expected_score=0.5,
        expected_decision="REPLAN",
    )

    print(_RULE)
    print("Summary")
    print(_RULE)
    print("v0.2 narrows the semantic blind spot with two deterministic signals:")
    print("  * evidence-breadth floor  -> catches THIN evidence (scenario 1)")
    print("  * tests_removed indicator -> catches DELETION GAMING (scenario 3)")
    print()
    print("It does NOT solve the all-green-but-wrong case (scenario 2): when")
    print("every gate is green A stays 1.0, and only a semantic evaluator can")
    print("judge whether the tests cover the real goal. That honesty is the")
    print("point of this example.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
