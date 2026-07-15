"""BOUND flight-booking example (Phase 7).

Reproduces the README flight-booking concept **without an LLM**.

The scenario: an agent is booking a flight from Paris to New York. Rather than
searching forever for the globally optimal flight, BOUND checks whether a
proposed action is *good enough* to cross the acceptance threshold and move on.

The example feeds the four BOUND evaluation dimensions directly into the
deterministic core:

    scores   = EvaluationScores(acceptance=0.9, influence=0.2,
                                 risk=0.1, cost=0.2)
    criteria = BoundCriteria(weight=1.0, threshold=0.6)

Expected calculation::

    S = (W × A) + I - R - C
    S = (1.0 × 0.9) + 0.2 - 0.1 - 0.2
    S = 0.8

Since ``0.8 >= 0.6`` the decision is ``ACCEPT``: the flight does not need to be
globally optimal — it has crossed the acceptance threshold, so the agent should
continue toward the larger goal.

Run with::

    uv run python examples/flight_booking.py
"""

from __future__ import annotations

import json
import sys

from bound.evaluator import StaticEvaluator
from bound.models import Action, BoundCriteria, EvaluationScores
from bound.policy import BoundPolicy
from bound.prompt import generate_prompt


def main() -> int:
    """Run the flight-booking BOUND evaluation and print the result.

    Returns:
        ``0`` on success (the example is illustrative and never fails the
        process), so the script can be chained in demos and CI smoke runs.
    """
    action = Action(
        description="Book the direct flight",
        goal="Travel from Paris to New York",
        context="Direct flight, zero stops, within budget.",
    )
    scores = EvaluationScores(
        acceptance=0.9,
        influence=0.2,
        risk=0.1,
        cost=0.2,
    )
    criteria = BoundCriteria(
        weight=1.0,
        threshold=0.6,
    )

    policy = BoundPolicy(StaticEvaluator(scores))
    result = policy.evaluate(action, criteria)

    # Auditability: every term of S = (W x A) + I - R - C is present, so the
    # score can be reconstructed from the JSON alone.
    payload = {
        "scores": {
            "acceptance": result.scores.acceptance,
            "influence": result.scores.influence,
            "risk": result.scores.risk,
            "cost": result.scores.cost,
        },
        "weight": result.weight,
        "threshold": result.threshold,
        "acceptance_component": result.acceptance_component,
        "influence_component": result.influence_component,
        "risk_component": result.risk_component,
        "cost_component": result.cost_component,
        "score": result.score,
        "decision": result.decision,
    }

    print("BOUND flight-booking example (no LLM)\n")
    print("JSON result:")
    print(json.dumps(payload, indent=2))
    print("\nSteering prompt:")
    print(generate_prompt(result))

    # Verify the documented expectation: S = 0.8 -> ACCEPT.
    assert result.score == 0.8, f"expected S=0.8, got {result.score}"
    assert result.decision == "ACCEPT", f"expected ACCEPT, got {result.decision}"

    print("\nThe flight does not need to be globally optimal.")
    print("It has crossed the acceptance threshold.")
    print("Continue toward the larger goal.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
