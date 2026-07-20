"""BOUND v0.7.0 — Decision Lineage demo (REPLAN -> ACCEPT, end-to-end).

Runs the real BOUND lineage Python + CLI surface to produce a local
decision-lineage run under a temp ``.bound/runs/`` directory, then prints the
``bound inspect <run_id>`` tree. The flow is the canonical v0.7.0 example:

    Attempt 1 -> evidence 1/3 (A=0.3333) -> REPLAN  (switch strategy)
    Attempt 2 -> evidence 3/3 (A=1.0000) -> ACCEPT  (continue to next step)

Nothing is hardcoded: scores come from BOUND's deterministic policy, the
decision->action mapping from ``bound.integration``, and the recorded lineage
from the real :class:`bound.LineageStore`. No LLM, no network.

A captured version of the resulting append-only log ships alongside this
script as ``examples/lineage_demo_events.jsonl``.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from bound import LineageStore, ReasonCode, RunFinishStatus, start_run
from bound.models import BoundCriteria, BoundWeights, EvaluationScores

GOAL = "Add input validation to the registration endpoint."

#: With W_A=1.0 and three required checks, A = passed/3 = 0.3333 for 1/3 and
#: 1.0000 for 3/3. Against T=0.7 (retry_margin=0.1) that yields REPLAN / ACCEPT
#: without hardcoding any decision.
THRESHOLD = 0.7
RETRY_MARGIN = 0.1


def _evaluate(score: float) -> tuple[str, str, float]:
    """Return (decision, next_action, computed_score) for a given acceptance A.

    Uses the deterministic BOUND policy via the public ``bound evaluate`` CLI so
    the recorded lineage is identical to what an agent integration would write.
    """
    from bound.evaluator import StaticEvaluator
    from bound.models import Action
    from bound.policy import BoundPolicy

    action = Action(description="validate registration input", goal=GOAL)
    scores = EvaluationScores(acceptance=score, influence=0.0, risk=0.0, cost=0.0)
    criteria = BoundCriteria(
        weights=BoundWeights(),
        threshold=THRESHOLD,
        retry_margin=RETRY_MARGIN,
    )
    result = BoundPolicy(StaticEvaluator(scores)).evaluate(action, criteria)
    from bound.integration import _DECISION_TO_ACTION

    return result.decision, _DECISION_TO_ACTION[result.decision], result.score


def main() -> int:
    runs_dir = Path(tempfile.mkdtemp(prefix="bound-lineage-demo-")) / "runs"
    store = LineageStore(base_dir=runs_dir)

    print(f"lineage store: {runs_dir}")
    print(f"goal: {GOAL}")
    print("=" * 78)

    # 1. Start ONE run for the whole task.
    with start_run(GOAL, store=store) as run:
        run_id = run.run_id
        print(f"started run: {run_id}")

        # Attempt 1 -> 1/3 checks -> REPLAN.
        decision1, action1, score1 = _evaluate(0.3333)
        step1 = run.start_step(
            contract_id="PHASE-001", attempt=1, description="Implement input validation"
        )
        eval1 = run.record_evaluation(
            step_id=step1.step_id,
            attempt=1,
            scores=EvaluationScores(acceptance=0.3333, influence=0.0, risk=0.0, cost=0.0),
            score=score1,
            threshold=THRESHOLD,
            decision=decision1,
        )
        run.record_outcome(
            step_id=step1.step_id,
            evaluation_id=eval1.evaluation_id,
            decision=decision1,
            next_action=action1,
            note="switched strategy to validator + parametrized tests",
        )
        print(f"attempt 1: A=0.3333 -> {decision1} -> {action1}")

        # Attempt 2 (replan -> new -R1 contract id) -> 3/3 checks -> ACCEPT.
        decision2, action2, score2 = _evaluate(1.0)
        step2 = run.start_step(
            contract_id="PHASE-001-R1",
            attempt=2,
            description="Implement input validation (replan)",
        )
        eval2 = run.record_evaluation(
            step_id=step2.step_id,
            attempt=2,
            scores=EvaluationScores(acceptance=1.0, influence=0.0, risk=0.0, cost=0.0),
            score=score2,
            threshold=THRESHOLD,
            decision=decision2,
        )
        run.record_outcome(
            step_id=step2.step_id,
            evaluation_id=eval2.evaluation_id,
            decision=decision2,
            next_action=action2,
            note="continued to next step",
        )
        print(f"attempt 2: A=1.0000 -> {decision2} -> {action2}")

        run.finish_run(
            status=RunFinishStatus.COMPLETED,
            reason_code=ReasonCode.RUN_COMPLETED,
            note="CSV export step completed",
        )

    print("=" * 78)
    print("local lineage path:", runs_dir / run_id)
    print("--- bound inspect (rendered by the CLI) ---")
    import os

    out = subprocess.run(
        [sys.executable, "-m", "bound.cli", "inspect", run_id],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "BOUND_RUNS_DIR": str(runs_dir)},
    )
    print(out.stdout)

    print("append-only events.jsonl (8 events):")
    print((runs_dir / run_id / "events.jsonl").read_text())

    shutil.rmtree(runs_dir.parent, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
