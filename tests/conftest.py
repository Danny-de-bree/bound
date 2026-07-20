"""Shared test fixtures and helpers for the BOUND test suite.

Provides common test-data builders (contracts, evidence, scores, criteria)
and constants (REPO_ROOT, decision-to-action mapping) so individual test
files don't need to redefine them.
"""

from __future__ import annotations

from pathlib import Path

from bound import (
    AcceptanceCheck,
    BoundCriteria,
    BoundWeights,
    Decision,
    EvaluationScores,
    EvidencePolicyAction,
    EvidenceProvenance,
    NextAction,
    RiskCheck,
    ScoreEvidence,
    StepContract,
)

#: Repository root — used by tests that need to reference project files.
REPO_ROOT: Path = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Canonical decision -> control-action mapping
# ---------------------------------------------------------------------------
#: The canonical BOUND decision -> control-action mapping. Used to assert the
#: public API agrees with it; never consulted at runtime.
DECISION_TO_CONTROL: dict[Decision, NextAction] = {
    "ACCEPT": "continue",
    "RETRY": "retry",
    "REPLAN": "replan",
    "ROLLBACK": "rollback",
}


# ---------------------------------------------------------------------------
# Zero-scope helpers
# ---------------------------------------------------------------------------

def _ZERO_SCORES() -> EvaluationScores:
    """Pre-built ``EvaluationScores`` with all dimensions at ``0.0``."""
    return EvaluationScores(acceptance=0.0, influence=0.0, risk=0.0, cost=0.0)


# ---------------------------------------------------------------------------
# Evidence builders
# ---------------------------------------------------------------------------

def _passed(check_id: str, *, provenance: EvidenceProvenance | None = None) -> ScoreEvidence:
    """A ``ScoreEvidence`` with ``passed=True``, optionally with a provenance."""
    kwargs = {"source": check_id, "effective_value": 1.0}
    if provenance is not None:
        kwargs["provenance"] = provenance
    return ScoreEvidence(**kwargs, check=check_id, passed=True)


def _failed(check_id: str, *, provenance: EvidenceProvenance | None = None) -> ScoreEvidence:
    """A ``ScoreEvidence`` with ``passed=False``, optionally with a provenance."""
    kwargs = {"source": check_id, "effective_value": 0.0}
    if provenance is not None:
        kwargs["provenance"] = provenance
    return ScoreEvidence(**kwargs, check=check_id, passed=False)


# ---------------------------------------------------------------------------
# Contract builders
# ---------------------------------------------------------------------------

def _simple_contract(*, risk_checks: list[RiskCheck] | None = None) -> StepContract:
    """A minimal ``StepContract`` with one acceptance check and optional risk checks."""
    checks = [
        AcceptanceCheck(
            id="tests-pass",
            description="All tests pass",
            accepted_provenance=[
                EvidenceProvenance.OBSERVED,
                EvidenceProvenance.VERIFIED,
                EvidenceProvenance.ATTESTED,
            ],
            on_missing=EvidencePolicyAction.REPLAN,
            on_claimed=EvidencePolicyAction.RETRY,
        ),
    ]
    return StepContract(
        id="PHASE-001",
        description="A test step",
        goal="Goal reached",
        acceptance_checks=checks,
        risk_checks=risk_checks or [],
    )


# ---------------------------------------------------------------------------
# Criteria builders
# ---------------------------------------------------------------------------

def _criteria(
    *,
    threshold: float = 0.7,
    retry_margin: float = 0.1,
    weights: BoundWeights | None = None,
) -> BoundCriteria:
    """Pre-built ``BoundCriteria`` with configurable threshold/weights."""
    return BoundCriteria(
        weights=weights or BoundWeights(),
        threshold=threshold,
        retry_margin=retry_margin,
    )


def _scores(
    *,
    acceptance: float = 0.0,
    influence: float = 0.0,
    risk: float = 0.0,
    cost: float = 0.0,
) -> EvaluationScores:
    """Pre-built ``EvaluationScores`` with configurable dimensions."""
    return EvaluationScores(
        acceptance=acceptance,
        influence=influence,
        risk=risk,
        cost=cost,
    )