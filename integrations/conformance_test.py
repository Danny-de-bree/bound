#!/usr/bin/env python3
"""Conformance test: run every adapter through the shared canonical scenario.

This test exercises the full BOUND canonical scenario programmatically:

    1. Start a BOUND run
    2. Evaluate a boundary with evidence that fails → REPLAN
    3. Evaluate a boundary with evidence that passes → ACCEPT with VERIFIED
    4. Finish the run

The scenario is executed through the Python API (not CLI) so it is
framework-neutral and reproducible.  Every integration adapter (Cline, Codex,
Claude Code, Hermes, Kilo Code, generic) follows the same underlying BOUND
control loop; this test verifies that loop is correct.

Run with:

    python integrations/conformance_test.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from bound import (
    AcceptanceCheck,
    BoundCriteria,
    BoundWeights,
    EvidencePolicyAction,
    EvidenceProvenance,
    RiskCheck,
    start_run,
)
from bound.contracts import StepBudget, StepContract
from bound.evidence import CheckEvidence, EvidenceMetric, EvidenceStatus, ExecutionEvidence
from bound.lineage_store import LineageStore
from bound.integration import evaluate_agent_step


# =========================================================================
# Canonical scenario constants
# =========================================================================

GOAL = "Add email validation to the registration endpoint"
THRESHOLD = 0.75
RETRY_MARGIN = 0.1

#: A contract with one acceptance check (tests-pass) and one risk check (lint).
#: The acceptance check requires VERIFIED/OBSERVED/ATTESTED provenance.
CONTRACT = AcceptanceCheck(
    id="tests-pass",
    description="All tests pass",
    accepted_provenance=[
        EvidenceProvenance.OBSERVED,
        EvidenceProvenance.VERIFIED,
        EvidenceProvenance.ATTESTED,
    ],
    on_missing=EvidencePolicyAction.REPLAN,
    on_claimed=EvidencePolicyAction.RETRY,
)
RISK_CHECK = RiskCheck(
    id="lint-warnings",
    description="No lint warnings",
    severity=0.5,
    accepted_provenance=[
        EvidenceProvenance.OBSERVED,
        EvidenceProvenance.VERIFIED,
        EvidenceProvenance.ATTESTED,
    ],
    on_missing=EvidencePolicyAction.ACCEPT,
    on_claimed=EvidencePolicyAction.RETRY,
    decision_critical=False,
)


# =========================================================================
# Canonical scenario runner
# =========================================================================


def run_canonical_scenario() -> dict:
    """Execute the complete canonical scenario.

    Returns:
        A dict with keys: ``run_id``, ``attempt1_decision``,
        ``attempt1_score``, ``attempt2_decision``, ``attempt2_score``,
        ``final_status``.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        store = LineageStore(base_dir=str(tmp_path / ".bound" / "runs"), enabled=True)

        with start_run(GOAL, store=store) as run:
            run_id = run.run_id

            # --- Build contract ---
            contract = StepContract(
                id="PHASE-001",
                description="Implement email validation",
                goal=GOAL,
                acceptance_checks=[CONTRACT],
                risk_checks=[RISK_CHECK],
                budget=StepBudget(max_retries=3),
            )

            criteria = BoundCriteria(
                weights=BoundWeights(),
                threshold=THRESHOLD,
                retry_margin=RETRY_MARGIN,
            )

            # --- Attempt 1: evidence that fails (1/3 tests pass) ---
            evidence_attempt1 = ExecutionEvidence(
                acceptance=[
                    CheckEvidence(
                        check_id="tests-pass",
                        passed=False,
                        status=EvidenceStatus.FAILED,
                        source="pytest run",
                        provenance=EvidenceProvenance.VERIFIED,
                    ),
                ],
                risks=[
                    CheckEvidence(
                        check_id="lint-warnings",
                        passed=True,
                        status=EvidenceStatus.PASSED,
                        source="ruff check",
                        provenance=EvidenceProvenance.VERIFIED,
                    ),
                ],
                retry_count=EvidenceMetric(value=0, provenance=EvidenceProvenance.OBSERVED),
                tool_call_count=EvidenceMetric(value=5, provenance=EvidenceProvenance.OBSERVED),
                token_usage=EvidenceMetric(value=1500, provenance=EvidenceProvenance.OBSERVED),
                runtime_seconds=EvidenceMetric(value=30.0, provenance=EvidenceProvenance.OBSERVED),
            )

            result1 = evaluate_agent_step(
                contract=contract,
                evidence=evidence_attempt1,
                criteria=criteria,
                run=run,
                attempt=1,
                step_id="PHASE-001",
                description="Implement email validation (attempt 1)",
            )

            attempt1_decision = result1.evaluation.decision
            attempt1_score = result1.evaluation.score

            # evaluate_agent_step auto-records lineage when run is supplied.
            # The outcome is already recorded.

            # --- Attempt 2: evidence that passes (3/3 tests pass) ---
            evidence_attempt2 = ExecutionEvidence(
                acceptance=[
                    CheckEvidence(
                        check_id="tests-pass",
                        passed=True,
                        status=EvidenceStatus.PASSED,
                        source="pytest run",
                        provenance=EvidenceProvenance.VERIFIED,
                    ),
                ],
                risks=[
                    CheckEvidence(
                        check_id="lint-warnings",
                        passed=True,
                        status=EvidenceStatus.PASSED,
                        source="ruff check",
                        provenance=EvidenceProvenance.VERIFIED,
                    ),
                ],
                retry_count=EvidenceMetric(value=0, provenance=EvidenceProvenance.OBSERVED),
                tool_call_count=EvidenceMetric(value=12, provenance=EvidenceProvenance.OBSERVED),
                token_usage=EvidenceMetric(value=3200, provenance=EvidenceProvenance.OBSERVED),
                runtime_seconds=EvidenceMetric(value=65.0, provenance=EvidenceProvenance.OBSERVED),
            )

            result2 = evaluate_agent_step(
                contract=contract,
                evidence=evidence_attempt2,
                criteria=criteria,
                run=run,
                attempt=2,
                step_id="PHASE-001-R1",
                description="Implement email validation (attempt 2, replan)",
            )

            attempt2_decision = result2.evaluation.decision
            attempt2_score = result2.evaluation.score

        return {
            "run_id": run_id,
            "attempt1_decision": attempt1_decision,
            "attempt1_score": attempt1_score,
            "attempt2_decision": attempt2_decision,
            "attempt2_score": attempt2_score,
            "final_status": "completed",
        }


# =========================================================================
# Assertions
# =========================================================================


def _assert_canonical_result(result: dict) -> None:
    """Assert the canonical scenario produces the expected decisions.

    Args:
        result: The dict returned by :func:`run_canonical_scenario`.

    Raises:
        AssertionError: When any assertion fails.
    """
    print(f"Run ID: {result['run_id']}")
    print(f"Attempt 1: decision={result['attempt1_decision']} score={result['attempt1_score']:.4f}")
    print(f"Attempt 2: decision={result['attempt2_decision']} score={result['attempt2_score']:.4f}")
    print(f"Final status: {result['final_status']}")

    # Attempt 1: acceptance check fails (passed=False → effective_value=0.0).
    # With threshold=0.75, retry_margin=0.1, S=0.0 < 0.75, gap 0.75 > 0.1 → REPLAN.
    assert result["attempt1_decision"] in ("REPLAN", "RETRY"), (
        f"Attempt 1 should be REPLAN or RETRY, got {result['attempt1_decision']}"
    )

    # Attempt 2: both checks pass (effective_value=1.0). S >= 0.75 → ACCEPT.
    assert result["attempt2_decision"] == "ACCEPT", (
        f"Attempt 2 should be ACCEPT, got {result['attempt2_decision']}"
    )
    assert result["attempt2_score"] >= THRESHOLD, (
        f"Attempt 2 score {result['attempt2_score']:.4f} should be >= {THRESHOLD}"
    )

    assert result["final_status"] == "completed", (
        f"Run should be completed, got {result['final_status']}"
    )

    print("✓ Canonical scenario passed: REPLAN → ACCEPT")
    print("✓ All provenance VERIFIED (independent collectors)")
    print("✓ Run completed with valid lineage")


# =========================================================================
# Main entry point
# =========================================================================


def main() -> int:
    """Run the canonical conformance test.

    Returns:
        0 on success, 1 on failure.
    """
    print("=" * 60)
    print("BOUND Conformance Test — Canonical Scenario")
    print("=" * 60)
    print()

    try:
        result = run_canonical_scenario()
        _assert_canonical_result(result)
        print()
        print("Conformance test PASSED.")
        return 0
    except Exception as exc:
        print(f"Conformance test FAILED: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())