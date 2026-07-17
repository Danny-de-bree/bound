"""BOUND v0.6 — runnable multi-step agent control-loop example (Phase 7).

Demonstrates a realistic agent trajectory driven by BOUND's *real* public API:

    StepContract + ExecutionEvidence + ContractEvaluator + BoundPolicy
        -> EvaluationResult (REPLAN / RETRY / ACCEPT)
        -> AgentControlResult (replan / retry / continue)

The agent works on a small coding task: "Add input validation to the
registration endpoint, with tests for valid, invalid, and edge-case input."
Three attempts are simulated, each providing richer :class:`ExecutionEvidence`
(standing in for what a real agent execution + evidence collector would record):

    Attempt 1: only the valid-input check passes  -> A=1/3 -> REPLAN
    Attempt 2: valid + invalid checks pass        -> A=2/3 -> RETRY
    Attempt 3: all three required checks pass     -> A=3/3 -> ACCEPT

Nothing is hardcoded: scores come from the deterministic
:class:`ContractEvaluator`, the decision from the deterministic
:class:`BoundPolicy`, and the control action from the deterministic
:func:`bound.integration.evaluate_agent_step` mapping. No LLM, no network.

Stable plan IDs (v0.6 Phase 6)
------------------------------
This step carries the stable identifier ``PHASE-001`` exactly as a repository
``PLAN.md`` would record it, and that same id is preserved unchanged across
``PLAN.md -> StepContract(id="PHASE-001") -> printed trajectory``. A replan of
this step would append ``-R<N>`` (``PHASE-001-R1``) rather than replace the id,
so the root identity (and its history) is never lost. The example deliberately
consumes the public :func:`bound.integration.evaluate_agent_step` API and does
**not** re-implement the BOUND decision->action mapping: that translation has
exactly one runtime source (``bound.integration._DECISION_TO_ACTION``).

The "avoided hypothetical extra steps" are explicitly labelled *simulated*
because they are not measured from a real agent run — they illustrate how
ACCEPT stops unnecessary further optimization.
"""

from __future__ import annotations

from bound.bound_workflow import BoundWorkflow
from bound.contract_evaluator import ContractEvaluator
from bound.contracts import AcceptanceCheck, StepContract
from bound.evidence import CheckEvidence, ExecutionEvidence
from bound.integration import evaluate_agent_step
from bound.models import BoundCriteria, BoundWeights
from bound.policy import BoundPolicy

#: The coding task this loop advances.
GOAL = "Add input validation to the registration endpoint."

#: The stable plan step identifier this contract is derived from. The same id
#: is preserved PLAN.md -> StepContract -> printed trajectory (v0.6 Phase 6).
#: A replan of this step would become ``PHASE-001-R1`` (append ``-R<N>``), never
#: an unrelated id, so the plan lineage stays traceable end to end.
PLAN_STEP_ID = "PHASE-001"

#: With acceptance weight W_A=0.9 and three required checks, the weighted score
#: S = 0.9 * (passed/3) lands at 0.3 / 0.6 / 0.9 for 1 / 2 / 3 checks passing,
#: which yields REPLAN / RETRY / ACCEPT against T=0.7, retry_margin=0.2 — without
#: hardcoding any decision.
THRESHOLD = 0.7
RETRY_MARGIN = 0.2
ACCEPTANCE_WEIGHT = 0.9

#: The three required acceptance checks for the task.
_CHECKS = [
    AcceptanceCheck(id="valid_input_passes", description="Valid input is accepted."),
    AcceptanceCheck(id="invalid_input_rejected", description="Invalid input is rejected."),
    AcceptanceCheck(id="edge_cases_handled", description="Edge-case input is handled."),
]

#: Hypothetical extra optimization an unbounded agent might do *after* the task
#: is already satisfactory. SIMULATED — not measured from a real run — existing
#: only to show that ACCEPT stops unnecessary refinement.
_SIMULATED_AVOIDED_STEPS = [
    "attempt 4: add a fourth validation rule the contract did not ask for",
    "attempt 5: refactor validators for style after tests already pass",
    "attempt 6: re-run the full suite a second time for confidence",
]



def _contract() -> StepContract:
    """Build the step contract for the validation task.

    Returns:
        A :class:`StepContract` with the three required acceptance checks.
    """
    return StepContract(
        id=PLAN_STEP_ID,
        description="Add input validation to the registration endpoint.",
        goal=GOAL,
        acceptance_checks=list(_CHECKS),
    )


def _evidence(passed_ids: list[str]) -> ExecutionEvidence:
    """Build evidence where exactly ``passed_ids`` passed and the rest failed.

    Args:
        passed_ids: The ids of checks recorded as passed.

    Returns:
        An :class:`ExecutionEvidence` with a :class:`CheckEvidence` for every
        declared check (passed or failed) plus ``rollback_available=True`` so no
        spurious risk indicator is introduced.
    """
    return ExecutionEvidence(
        acceptance=[
            CheckEvidence(
                check_id=check.id,
                passed=check.id in passed_ids,
                source="pytest",
            )
            for check in _CHECKS
        ],
        rollback_available=True,
    )


def _criteria() -> BoundCriteria:
    """Build the :class:`BoundCriteria` for the loop.

    Returns:
        Criteria with the tuned acceptance weight, threshold, and retry margin.
    """
    return BoundCriteria(
        threshold=THRESHOLD,
        retry_margin=RETRY_MARGIN,
        weights=BoundWeights(acceptance=ACCEPTANCE_WEIGHT),
    )


def main() -> int:
    """Run the multi-step BOUND control loop and print the trajectory.

    Returns:
        ``0`` (the example is illustrative and never fails the process).
    """
    print("BOUND v0.6 — agent control loop example (no LLM, no network)\n")
    print("=" * 80)

    # Real public API: ContractEvaluator + BoundPolicy (no placeholder evaluator)
    # wired through BoundWorkflow. evaluate_agent_step feeds the contract scores
    # straight through BoundPolicy.decide and maps the decision to a control action.
    workflow = BoundWorkflow(evaluator=ContractEvaluator(), policy=BoundPolicy())
    contract = _contract()
    criteria = _criteria()

    # The contract id is the stable plan identity carried verbatim from PLAN.md
    # (v0.6 Phase 6): PLAN.md "PHASE-001" -> StepContract(id="PHASE-001") -> here.
    print(f"plan step id: {PLAN_STEP_ID}  (preserved PLAN.md -> StepContract -> trajectory)")
    print(f"goal: {GOAL}")
    print(
        f"contract id: {contract.id}  (matches plan step id: {contract.id == PLAN_STEP_ID})"
    )
    print(
        f"criteria: threshold T={THRESHOLD}  retry_margin={RETRY_MARGIN}  "
        f"W_A={ACCEPTANCE_WEIGHT}"
    )
    print("score formula (this task): S = W_A * (passed_required / total_required)")
    print("=" * 80)

    # Each attempt is one "agent execution + evidence collection" snapshot. The
    # decisions are NOT hardcoded: they are computed by BOUND from the evidence.
    attempts = [
        ("Attempt 1", ["valid_input_passes"]),
        ("Attempt 2", ["valid_input_passes", "invalid_input_rejected"]),
        (
            "Attempt 3",
            ["valid_input_passes", "invalid_input_rejected", "edge_cases_handled"],
        ),
    ]

    decisions: list[str] = []
    final_score: float | None = None
    accepted = False

    for label, passed_ids in attempts:
        control = evaluate_agent_step(
            contract=contract,
            evidence=_evidence(passed_ids),
            criteria=criteria,
            workflow=workflow,
        )
        result = control.evaluation
        decisions.append(result.decision)
        if result.decision == "ACCEPT":
            accepted = True
            final_score = result.score

        print(f"\n{'-' * 80}")
        print(f"[{contract.id}] {label}: passing checks = {passed_ids}")
        print(
            f"  A={result.scores.acceptance:.4f}  R={result.scores.risk:.4f}  "
            f"C={result.scores.cost:.4f}  S={result.score:.4f}  "
            f"(T={result.threshold:.4f})"
        )
        print(
            f"  decision: {result.decision}  ->  control action: {control.next_action}"
        )
        print(f"  feedback: {control.feedback}")

        if accepted:
            # BOUND owns the stop signal: ACCEPT halts the optimization loop.
            # The caller (this example) breaks instead of refining further.
            break

    print("\n" + "=" * 80)
    print("Trajectory summary")
    print("=" * 80)
    print(f"plan step id:               {contract.id}")
    print(f"attempts evaluated:        {len(decisions)}")
    print(f"decisions observed:        {decisions}")
    print(f"final score (ACCEPT):      {final_score}")
    print(f"acceptance threshold T:    {THRESHOLD}")
    print(f"BOUND returned ACCEPT at:  attempt {len(decisions)}")

    print(
        "\navoided hypothetical extra steps (SIMULATED — not measured from a real run):"
    )
    for step in _SIMULATED_AVOIDED_STEPS:
        print(f"  - [simulated] {step}")
    print(
        "\nBOUND's ACCEPT stopped the optimization loop: no real work was done on "
        "these steps. They are labelled simulated because they were not observed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


