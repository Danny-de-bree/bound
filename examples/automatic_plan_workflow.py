"""BOUND automatic multi-step plan workflow example (Phase 11).

Drives an entire multi-step plan through the v0.3 contract pipeline **without an
LLM**, simulating agent execution evidence so the deterministic
:class:`~bound.contract_evaluator.ContractEvaluator` and
:class:`~bound.policy.BoundPolicy` can score and decide each step.

The scenario (from ``todo.md`` Phase 11)::

    Goal:
        Add safe input validation to the user registration endpoint.

    Plan:
        Step 1 — Implement validation.
        Step 2 — Add validation tests.
        Step 3 — Run required verification.
        Step 4 — Optional additional refactoring.

The pipeline exercised end-to-end is::

    goal + plan -> StaticContractGenerator -> BoundPlan
        -> per step: StepContract + simulated ExecutionEvidence
            -> ContractEvaluator -> A / I / R / C
            -> BoundPolicy -> ACCEPT / RETRY / REPLAN / ROLLBACK

Each step carries its own :class:`~bound.contracts.StepContract` (acceptance
checks, a risk check, expected artifacts, and an execution budget). Because
BOUND deliberately stays out of the execution loop, this script *simulates* the
agent's execution by hand-building :class:`~bound.evidence.ExecutionEvidence`
for each attempt — :class:`~bound.evidence.CheckEvidence` records with
``passed=True`` / ``False`` chosen to produce specific, auditable decisions.

The example demonstrates at least ``REPLAN``, ``RETRY``, and ``ACCEPT`` across
the steps, and shows the cardinal v0.3 behaviour: **after an ``ACCEPT`` the
current optimization loop for that step stops** (the caller loop ``break``s on
``ACCEPT`` and advances to the next step). In a real agent a ``RETRY``
re-executes the same step and a ``REPLAN`` invokes a new strategy; here the next
pre-built evidence snapshot stands in for that re-execution.

For every attempt the script prints: the contract summary, the evidence summary,
the ``A / I / R / C`` scores, the final score ``S`` and deterministic decision,
and the per-dimension provenance (why each score is what it is).

Optional LLM adapter (Phase 4 boundary)
---------------------------------------
:class:`~bound.contracts.StaticContractGenerator` is one concrete
implementation of the :class:`~bound.contracts.ContractGenerator` Protocol. An
*optional* LLM-backed adapter could compile the *same*
:class:`~bound.contracts.BoundPlan` from the natural-language goal + plan above;
the seam is unchanged and the adapter must never return a BOUND decision or
``A / I / R / C`` scores. Whatever it emits is round-tripped through Pydantic
validation before BOUND uses it, and the deterministic
:class:`~bound.contract_evaluator.ContractEvaluator` and
:class:`~bound.policy.BoundPolicy` then own the scores and the final decision.
That adapter lives outside the deterministic core, so this script — and the
whole package — runs without it.

Run with::

    uv run python examples/automatic_plan_workflow.py
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from bound.bound_workflow import BoundWorkflow
from bound.contract_evaluator import ContractEvaluator
from bound.contracts import (
    AcceptanceCheck,
    BoundPlan,
    RiskCheck,
    StaticContractGenerator,
    StepBudget,
    StepContract,
)
from bound.evaluator import StaticEvaluator
from bound.evidence import CheckEvidence, ExecutionEvidence
from bound.models import BoundCriteria, EvaluationScores
from bound.policy import BoundPolicy

#: The top-level goal of the plan, exactly as stated in ``todo.md`` Phase 11.
GOAL = "Add safe input validation to the user registration endpoint."

#: The natural-language plan text. The :class:`StaticContractGenerator` ignores
#: this (it returns a pre-built plan), but it is the input a real LLM adapter
#: would compile into the :class:`BoundPlan` below.
PLAN_TEXT = (
    "1. Implement validation.\n"
    "2. Add validation tests.\n"
    "3. Run required verification.\n"
    "4. Optional additional refactoring."
)

#: Shared execution budget for every step (only the always-measured integer
#: dimensions are declared, so cost is fully reproducible with no telemetry
#: saturation). ``max_tool_calls=20`` and ``max_retries=3``; token and runtime
#: budgets are left unbounded (``None``) so those dimensions are not scored.
_BUDGET = StepBudget(max_tool_calls=20, max_retries=3)

#: BOUND decision criteria for the whole plan. With default weights
#: (``W_A = W_I = W_R = W_C = 1.0``) and the contract evaluator's
#: ``I = 0.0`` default, the score reduces to ``S = A - R - C``. The threshold
#: ``T = 0.6`` and ``retry_margin = 0.1`` make ``RETRY`` (``0 < gap <= 0.1``)
#: and ``REPLAN`` (``gap > 0.1``) both reachable.
CRITERIA = BoundCriteria(threshold=0.6, retry_margin=0.1)


def _build_plan() -> BoundPlan:
    """Construct the input-validation :class:`BoundPlan`.

    Each :class:`StepContract` declares measurable acceptance checks, a
    meaningful risk check, the artifacts it should produce, and the shared
    budget. The contract carries no executable code — only identifiers and
    descriptions — so nothing here can smuggle arbitrary Python into the
    deterministic core.

    Returns:
        A validated :class:`BoundPlan` for the four-step input-validation plan.
    """
    # ------------------------------------------------------------------ #
    # Step 1 — Implement validation.                                      #
    # ------------------------------------------------------------------ #
    implement_validation = StepContract(
        id="implement-validation",
        description="Add safe input validation to the registration endpoint.",
        goal="Malformed registration input is rejected before reaching storage.",
        acceptance_checks=[
            AcceptanceCheck(id="rejects_empty_email", description="Empty email is rejected."),
            AcceptanceCheck(
                id="rejects_bad_email_format",
                description="Malformed email addresses are rejected.",
            ),
            AcceptanceCheck(
                id="rejects_oversized_input",
                description="Over-long / oversized inputs are rejected.",
            ),
            AcceptanceCheck(
                id="sanitises_input",
                description="Inputs are sanitised before downstream use.",
            ),
        ],
        risk_checks=[
            RiskCheck(
                id="no_sql_in_input",
                description="No raw SQL is built from user input.",
                severity=1.0,
            ),
        ],
        expected_artifacts=["src/auth/validation.py"],
        budget=_BUDGET,
    )

    # ------------------------------------------------------------------ #
    # Step 2 — Add validation tests.                                      #
    # ------------------------------------------------------------------ #
    add_tests = StepContract(
        id="add-validation-tests",
        description="Add tests covering the validation rules.",
        goal="The validation surface is covered by a green test suite.",
        acceptance_checks=[
            AcceptanceCheck(
                id="tests_cover_validation",
                description="Tests assert every validation rule, not just '200 OK'.",
            ),
            AcceptanceCheck(
                id="tests_pass",
                description="The new validation test suite passes.",
            ),
        ],
        risk_checks=[
            RiskCheck(
                id="no_tests_removed",
                description="No existing tests are deleted to force a green suite.",
                severity=1.0,
            ),
        ],
        expected_artifacts=["tests/test_validation.py"],
        budget=_BUDGET,
    )


    # ------------------------------------------------------------------ #
    # Step 3 — Run required verification.                                 #
    # ------------------------------------------------------------------ #
    run_verification = StepContract(
        id="run-verification",
        description="Run the full required verification suite.",
        goal="The repository is in a verified, shippable state.",
        acceptance_checks=[
            AcceptanceCheck(
                id="full_suite_passes",
                description="The full test suite passes.",
            ),
            AcceptanceCheck(id="lint_clean", description="The linter reports no issues."),
            AcceptanceCheck(
                id="type_check_clean",
                description="The type checker reports no issues.",
            ),
        ],
        risk_checks=[
            RiskCheck(
                id="no_unexpected_files_changed",
                description="No files outside the expected scope are changed.",
                severity=0.7,
            ),
        ],
        expected_artifacts=["pytest-report.xml"],
        budget=_BUDGET,
    )

    # ------------------------------------------------------------------ #
    # Step 4 — Optional refactoring (one advisory check).                 #
    # ------------------------------------------------------------------ #
    optional_refactor = StepContract(
        id="optional-refactor",
        description="Optional refactor that must not change behaviour.",
        goal="Improve clarity without altering the validated behaviour.",
        acceptance_checks=[
            AcceptanceCheck(
                id="refactor_preserves_behaviour",
                description="All tests still pass after the refactor (required).",
                required=True,
            ),
            AcceptanceCheck(
                id="refactor_reduces_complexity",
                description="Cyclomatic complexity is reduced (advisory).",
                required=False,
            ),
        ],
        risk_checks=[
            RiskCheck(
                id="no_public_api_breaking_change",
                description="No public API is broken by the refactor.",
                severity=1.0,
            ),
        ],
        expected_artifacts=[],
        budget=_BUDGET,
    )

    return BoundPlan(
        goal=GOAL,
        steps=[
            implement_validation,
            add_tests,
            run_verification,
            optional_refactor,
        ],
    )


def _chk(
    check_id: str,
    passed: bool,
    source: str,
    details: str | None = None,
) -> CheckEvidence:
    """Build one :class:`CheckEvidence` record concisely.

    Args:
        check_id: The identifier matching a contract's acceptance / risk check.
        passed: Whether the check passed at observation time.
        source: Free-form provenance for how the outcome was determined.
        details: Optional human-readable elaboration of the outcome.

    Returns:
        A :class:`CheckEvidence` with the supplied fields.
    """
    return CheckEvidence(check_id=check_id, passed=passed, source=source, details=details)


def _simulated_attempts() -> dict[str, list[tuple[str, ExecutionEvidence]]]:
    """Build the simulated execution evidence for every step's attempts.

    Each entry is ``(attempt_label, ExecutionEvidence)``. The pass/fail states
    are chosen deliberately so the deterministic policy yields, across the
    plan: ``REPLAN`` (step 1, attempt 1), ``RETRY`` (step 2, attempt 1), and
    ``ACCEPT`` (every step's final attempt). Cost is driven only by the integer
    ``retry_count`` / ``tool_call_count`` against the shared budget so it is
    fully reproducible. ``rollback_available=True`` is recorded where relevant so
    no spurious risk is invented.

    Returns:
        A mapping from step ``id`` to that step's ordered attempt list.
    """
    return {
        # -- Step 1: attempt 1 -> REPLAN (1/4 checks pass, far below threshold);
        #            attempt 2 -> ACCEPT (all checks pass after a replan). ----
        "implement-validation": [
            (
                "attempt 1 (initial draft)",
                ExecutionEvidence(
                    acceptance=[
                        _chk("rejects_empty_email", True, "tests"),
                        _chk("rejects_bad_email_format", False, "tests", "regex too lax"),
                        _chk("rejects_oversized_input", False, "tests", "no length cap"),
                        _chk("sanitises_input", False, "tests", "raw input stored"),
                    ],
                    risks=[_chk("no_sql_in_input", True, "code-review")],
                    produced_artifacts=["src/auth/validation.py"],
                    retry_count=0,
                    tool_call_count=6,
                    rollback_available=True,
                ),
            ),
            (
                "attempt 2 (replanned: tightened rules)",
                ExecutionEvidence(
                    acceptance=[
                        _chk("rejects_empty_email", True, "tests"),
                        _chk("rejects_bad_email_format", True, "tests"),
                        _chk("rejects_oversized_input", True, "tests"),
                        _chk("sanitises_input", True, "tests"),
                    ],
                    risks=[_chk("no_sql_in_input", True, "code-review")],
                    produced_artifacts=["src/auth/validation.py"],
                    retry_count=1,
                    tool_call_count=9,
                    rollback_available=True,
                ),
            ),
        ],
        # -- Step 2: attempt 1 -> RETRY (1/2 checks pass, gap exactly 0.1);
        #            attempt 2 -> ACCEPT (suite passes after a retry). ----------
        "add-validation-tests": [
            (
                "attempt 1 (tests written, suite failing)",
                ExecutionEvidence(
                    acceptance=[
                        _chk("tests_cover_validation", True, "code-review"),
                        _chk("tests_pass", False, "pytest", "1 failing assertion"),
                    ],
                    risks=[_chk("no_tests_removed", True, "git-diff")],
                    produced_artifacts=["tests/test_validation.py"],
                    retry_count=0,
                    tool_call_count=0,
                    rollback_available=True,
                ),
            ),
            (
                "attempt 2 (retry: fixed assertion)",
                ExecutionEvidence(
                    acceptance=[
                        _chk("tests_cover_validation", True, "code-review"),
                        _chk("tests_pass", True, "pytest"),
                    ],
                    risks=[_chk("no_tests_removed", True, "git-diff")],
                    produced_artifacts=["tests/test_validation.py"],
                    retry_count=1,
                    tool_call_count=7,
                    rollback_available=True,
                ),
            ),
        ],
        # -- Step 3: attempt 1 -> ACCEPT (verification clean on first run). ---
        "run-verification": [
            (
                "attempt 1 (full verification run)",
                ExecutionEvidence(
                    acceptance=[
                        _chk("full_suite_passes", True, "pytest"),
                        _chk("lint_clean", True, "ruff"),
                        _chk("type_check_clean", True, "mypy"),
                    ],
                    risks=[_chk("no_unexpected_files_changed", True, "git-diff")],
                    produced_artifacts=["pytest-report.xml"],
                    retry_count=0,
                    tool_call_count=5,
                    rollback_available=True,
                ),
            ),
        ],
        # -- Step 4: attempt 1 -> ACCEPT even though the *advisory* optional
        #            check fails (optional checks never affect A). -----------
        "optional-refactor": [
            (
                "attempt 1 (refactor; complexity not reduced)",
                ExecutionEvidence(
                    acceptance=[
                        _chk("refactor_preserves_behaviour", True, "pytest"),
                        _chk(
                            "refactor_reduces_complexity",
                            False,
                            "radon",
                            "complexity unchanged (advisory)",
                        ),
                    ],
                    risks=[_chk("no_public_api_breaking_change", True, "git-diff")],
                    produced_artifacts=[],
                    retry_count=0,
                    tool_call_count=4,
                    rollback_available=True,
                ),
            ),
        ],
    }


def _print_contract_summary(step: StepContract) -> None:
    """Print a one-block summary of a step's contract.

    Args:
        step: The :class:`StepContract` to summarise.
    """
    print(f"  contract:          [{step.id}] {step.description}")
    print(f"  goal:              {step.goal}")
    print("  acceptance checks:")
    for check in step.acceptance_checks:
        tag = "required" if check.required else "optional"
        print(f"    - [{check.id}] ({tag}) {check.description}")
    print("  risk checks:")
    for check in step.risk_checks:
        print(
            f"    - [{check.id}] severity={check.severity:.1f}  {check.description}"
        )
    print(
        f"  budget:            max_tool_calls={step.budget.max_tool_calls}, "
        f"max_retries={step.budget.max_retries} (token/runtime unbounded)"
    )


def _print_evidence_summary(label: str, evidence: ExecutionEvidence) -> None:
    """Print a concise summary of one attempt's simulated evidence.

    Args:
        label: The human-readable attempt label.
        evidence: The :class:`ExecutionEvidence` observed for this attempt.
    """
    print(f"  evidence:          {label}")
    acc = ", ".join(
        f"{ce.check_id}={'pass' if ce.passed else 'FAIL'}" for ce in evidence.acceptance
    )
    print(f"    acceptance:      {acc}")
    rsk = ", ".join(
        f"{ce.check_id}={'pass' if ce.passed else 'FAIL'}" for ce in evidence.risks
    )
    print(f"    risks:          {rsk or '(none)'}")
    print(f"    produced:        {', '.join(evidence.produced_artifacts) or '(none)'}")
    print(
        f"    telemetry:      retries={evidence.retry_count}, "
        f"tool_calls={evidence.tool_call_count}, "
        f"rollback={evidence.rollback_available}"
    )


def _print_provenance(result: object) -> None:
    """Print the per-dimension provenance backing the A / I / R / C scores.

    Each :class:`~bound.models.ScoreEvidence` ``description`` is already a
    self-explaining one-liner ("✓ passed ... contributes 0.25 to A"), so we
    print them grouped by dimension. This lets a reader answer "why is
    ``A = 0.25``?" from the output alone.

    Args:
        result: The :class:`~bound.models.EvaluationResult` whose
            ``provenance`` is printed.
    """
    provenance = getattr(result, "provenance", None)
    if not provenance:
        print("  provenance:        (none)")
        return
    titles = {
        "acceptance": "A (acceptance)",
        "influence": "I (influence)",
        "risk": "R (risk)",
        "cost": "C (cost)",
    }
    for key in ("acceptance", "influence", "risk", "cost"):
        records: Sequence = provenance.get(key, [])
        print(f"  provenance {titles[key]}:")
        for record in records:
            print(f"    [{record.source}] {record.description}")


def main() -> int:
    """Run the full multi-step plan and print every step's BOUND evaluation.

    Returns:
        ``0`` on success (the example is illustrative and never fails the
        process), so the script can be chained in demos and CI smoke runs.
    """
    print("BOUND automatic multi-step plan workflow example (no LLM)\n")
    print("=" * 100)
    print("Natural-language input")
    print("=" * 100)
    print(f"goal: {GOAL}")
    print("plan:")
    print(PLAN_TEXT)
    print()

    # 1. Compile the explicit, validated BoundPlan and drive it through the
    #    ContractGenerator seam (the same seam an optional LLM adapter would
    #    implement). StaticContractGenerator returns the plan by identity.
    plan = _build_plan()
    generator = StaticContractGenerator(plan)
    prepared = generator.generate(goal=GOAL, plan=PLAN_TEXT)
    assert prepared is plan, "StaticContractGenerator must return the plan by identity"

    # 2. Wire the deterministic workflow. The BoundPolicy's injected evaluator
    #    is a vestigial placeholder here: BoundWorkflow.evaluate_step scores via
    #    the ContractEvaluator and feeds those scores through the policy's
    #    unchanged decision rule (see bound_workflow.py).
    workflow = BoundWorkflow(
        contract_generator=generator,
        evaluator=ContractEvaluator(),
        policy=BoundPolicy(
            StaticEvaluator(
                EvaluationScores(acceptance=0.0, influence=0.0, risk=0.0, cost=0.0)
            )
        ),
    )

    attempts = _simulated_attempts()

    print("=" * 100)
    print(f"BoundPlan: goal={prepared.goal!r}  steps={len(prepared.steps)}")
    print(
        f"criteria: threshold={CRITERIA.threshold}  "
        f"retry_margin={CRITERIA.retry_margin}  "
        f"rollback_risk_threshold={CRITERIA.rollback_risk_threshold}"
    )
    print(
        "score formula: S = (W_A*A) + (W_I*I) - (W_R*R) - (W_C*C)  "
        "-> with default weights & I=0: S = A - R - C"
    )
    print("=" * 100)

    seen_decisions: set[str] = set()
    plan_complete = True

    # 3. Walk the plan. For each step we run an inner "optimization loop" over
    #    the simulated attempts. BOUND does not own the loop: the *caller*
    #    decides how to react. Here the loop breaks on ACCEPT (the step is good
    #    enough to advance), continues on RETRY/REPLAN (the next evidence
    #    snapshot stands in for the re-execution), and aborts the whole plan if
    #    a step never ACCEPTs.
    for index, step in enumerate(prepared.steps, start=1):
        print(f"\n{'#' * 100}")
        print(f"### Step {index}/{len(prepared.steps)}")
        print(f"{'#' * 100}")
        _print_contract_summary(step)

        accepted = False
        for attempt_no, (label, evidence) in enumerate(attempts[step.id], start=1):
            print(f"\n  --- attempt {attempt_no} ---")
            _print_evidence_summary(label, evidence)

            result = workflow.evaluate_step(
                contract=step,
                evidence=evidence,
                criteria=CRITERIA,
            )
            scores = result.scores
            print(
                f"  scores:            A={scores.acceptance:.4f}  "
                f"I={scores.influence:.4f}  R={scores.risk:.4f}  "
                f"C={scores.cost:.4f}"
            )
            print(
                f"  final:             S={result.score:.4f}  "
                f"threshold={result.threshold:.4f}  "
                f"distance={result.distance_to_threshold:.4f}  "
                f"decision={result.decision}"
            )
            _print_provenance(result)

            seen_decisions.add(result.decision)

            if result.decision == "ACCEPT":
                # The cardinal v0.3 behaviour: after ACCEPT the current
                # optimization loop for THIS step STOPS — break and advance.
                print(
                    "  loop:              ACCEPT -> optimization loop stops; "
                    "advance to the next step."
                )
                accepted = True
                break
            print(
                f"  loop:              {result.decision} -> optimization loop "
                f"continues (next attempt / new strategy)."
            )

        if not accepted:
            print("  loop:              step never ACCEPTed -> stopping the plan.")
            plan_complete = False
            break

    print(f"\n{'=' * 100}")
    print("Plan summary")
    print("=" * 100)
    print(f"plan complete:        {plan_complete}")
    print(f"decisions observed:   {sorted(seen_decisions)}")
    demonstrated = {"REPLAN", "RETRY", "ACCEPT"}
    missing = demonstrated - seen_decisions
    print(
        f"required decisions:   REPLAN, RETRY, ACCEPT  "
        f"-> {'all demonstrated' if not missing else f'missing {sorted(missing)}'}"
    )
    assert not missing, f"example must demonstrate {demonstrated}, missing {missing}"
    assert plan_complete, "the four-step plan should complete with every step ACCEPTed"
    print(
        "\nAfter every step's ACCEPT the optimization loop stopped and the plan "
        "advanced.\nThe final decision remained fully deterministic — no LLM, no "
        "network, no API key."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())






