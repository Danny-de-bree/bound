from __future__ import annotations

import pytest

from bound.bound_workflow import BoundWorkflow
from bound.contract_evaluator import (
    AssuranceAssessment,
    BudgetStatus,
    ContractEvaluator,
    PolicyGateOutcome,
)
from bound.contracts import (
    AcceptanceCheck,
    EvidencePolicyAction,
    RiskCheck,
    StepBudget,
    StepContract,
)
from bound.evidence import (
    CheckEvidence,
    EvidenceMetric,
    EvidenceProvenance,
    EvidenceStatus,
    ExecutionEvidence,
)
from bound.models import (
    BoundCriteria,
    DecisionAssurance,
    EvaluationScores,
    ScoreEvidence,
)
from bound.policy_schema import (
    BoundPolicyConfig,
    BudgetDimension,
    HardGate,
    PolicyIdentity,
    WeightedSignal,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

#: Four required acceptance checks used across the acceptance tests.
_REQUIRED_CHECKS = [
    AcceptanceCheck(id="a", description="check a"),
    AcceptanceCheck(id="b", description="check b"),
    AcceptanceCheck(id="c", description="check c"),
    AcceptanceCheck(id="d", description="check d"),
]


def _full_budget() -> StepBudget:
    """Build a budget with all four dimensions defined.

    Returns:
        A :class:`StepBudget` with small ceilings so budget-exceeded behaviour
        is easy to drive from the tests.
    """
    return StepBudget(
        max_retries=2,
        max_tool_calls=10,
        max_tokens=100,
        max_runtime_seconds=10.0,
    )


def _passed(check_id: str, source: str = "pytest") -> CheckEvidence:
    """Build a passing :class:`CheckEvidence` for ``check_id``.

    Args:
        check_id: The check identifier to record as passed.
        source: Free-form provenance string. Defaults to ``"pytest"``.

    Returns:
        A :class:`CheckEvidence` with ``passed=True``.
    """
    return CheckEvidence(check_id=check_id, passed=True, source=source)


def _failed(check_id: str, source: str = "pytest") -> CheckEvidence:
    """Build a failing :class:`CheckEvidence` for ``check_id``.

    Args:
        check_id: The check identifier to record as failed.
        source: Free-form provenance string. Defaults to ``"pytest"``.

    Returns:
        A :class:`CheckEvidence` with ``passed=False``.
    """
    return CheckEvidence(check_id=check_id, passed=False, source=source)


def _contract(
    *,
    acceptance_checks: list[AcceptanceCheck] | None = None,
    risk_checks: list[RiskCheck] | None = None,
    budget: StepBudget | None = None,
) -> StepContract:
    """Build a minimal valid :class:`StepContract` for tests.

    Args:
        acceptance_checks: The acceptance checks to carry. Defaults to the four
            required checks in :data:`_REQUIRED_CHECKS`.
        risk_checks: The risk checks to carry. Defaults to an empty list.
        budget: The budget to carry. Defaults to ``None`` (no budget).

    Returns:
        A :class:`StepContract` populated with the supplied parts.
    """
    return StepContract(
        id="step-1",
        description="A test step",
        goal="Cover the contract evaluator",
        acceptance_checks=acceptance_checks
        if acceptance_checks is not None
        else list(_REQUIRED_CHECKS),
        risk_checks=risk_checks or [],
        budget=budget,
    )


# ---------------------------------------------------------------------------
# Acceptance
# ---------------------------------------------------------------------------


class TestAcceptance:
    """Acceptance ``A = passed_required / total_required`` and provenance."""

    def test_all_required_checks_pass(self) -> None:
        """All four required checks pass → ``A = 1.0``.

        Intent: pin the happy path where every required acceptance check has
        confirming ``passed=True`` evidence; the step is fully accepted.
        """
        contract = _contract()
        evidence = ExecutionEvidence(
            acceptance=[_passed(cid) for cid in ("a", "b", "c", "d")],
            rollback_available=True,
        )
        scores = ContractEvaluator().evaluate(contract, evidence)
        assert scores.acceptance == 1.0

    def test_partial_acceptance_three_of_four(self) -> None:
        """Three of four required checks pass → ``A = 0.75``.

        Intent: pin that ``A`` is the exact required-check pass rate, so a
        single failing required check lowers acceptance proportionally rather
        than to zero.
        """
        contract = _contract()
        evidence = ExecutionEvidence(
            acceptance=[_passed("a"), _passed("b"), _passed("c"), _failed("d")],
            rollback_available=True,
        )
        evaluator = ContractEvaluator()
        scores = evaluator.evaluate(contract, evidence)
        assert scores.acceptance == pytest.approx(0.75)

    def test_missing_required_evidence_counts_as_failed(self) -> None:
        """A required check with no matching evidence is failed, not passed.

        Intent: lock the non-negotiable honesty rule — missing *required*
        evidence must lower acceptance (here 1 of 2 → ``0.5``) rather than be
        silently treated as passing.
        """
        contract = _contract(
            acceptance_checks=[
                AcceptanceCheck(id="a", description="a"),
                AcceptanceCheck(id="b", description="b"),
            ],
        )
        # Only "a" has evidence; "b" is entirely absent.
        evidence = ExecutionEvidence(acceptance=[_passed("a")])
        evaluator = ContractEvaluator()
        scores = evaluator.evaluate(contract, evidence)
        assert scores.acceptance == 0.5
        b_record = next(
            ev for ev in evaluator.provenance["acceptance"] if ev.source == "b"
        )
        assert "missing required evidence counts as failed" in b_record.description

    def test_optional_checks_are_advisory_only(self) -> None:
        """Failing an optional check does not affect ``A``.

        Intent: pin the documented choice — optional (``required=False``) checks
        are recorded as advisory provenance but never change ``A``, so a step
        with all required checks passing is fully accepted even if an advisory
        gate failed.
        """
        contract = _contract(
            acceptance_checks=[
                AcceptanceCheck(id="req", description="required"),
                AcceptanceCheck(id="opt", description="optional", required=False),
            ],
        )
        evidence = ExecutionEvidence(
            acceptance=[_passed("req"), _failed("opt")],
            rollback_available=True,
        )
        evaluator = ContractEvaluator()
        scores = evaluator.evaluate(contract, evidence)
        assert scores.acceptance == 1.0
        opt_record = next(
            ev for ev in evaluator.provenance["acceptance"] if ev.source == "opt"
        )
        assert opt_record.contribution == 0.0
        assert "advisory" in opt_record.description

    def test_no_required_checks_means_zero_acceptance(self) -> None:
        """A contract whose checks are all optional yields ``A = 0.0``.

        Intent: pin that acceptance cannot be established from advisory checks
        alone — the evaluator refuses to silently pass a step that defined no
        hard gate.
        """
        contract = _contract(
            acceptance_checks=[
                AcceptanceCheck(id="opt", description="optional", required=False),
            ],
        )
        evidence = ExecutionEvidence(acceptance=[_passed("opt")])
        scores = ContractEvaluator().evaluate(contract, evidence)
        assert scores.acceptance == 0.0

    def test_duplicate_evidence_dedup_all_must_pass(self) -> None:
        """Duplicate evidence for one id passes only when all records pass.

        Intent: pin the conservative dedup rule — a single ``passed=False``
        among duplicates for a required check fails that check, so the
        collector cannot mask a failure by re-recording a pass.
        """
        contract = _contract(
            acceptance_checks=[AcceptanceCheck(id="a", description="a")],
        )
        # One pass and one fail for the same id → failed.
        evidence = ExecutionEvidence(
            acceptance=[_passed("a"), _failed("a")],
            rollback_available=True,
        )
        scores = ContractEvaluator().evaluate(contract, evidence)
        assert scores.acceptance == 0.0

    def test_provenance_answers_why_acceptance(self) -> None:
        """The acceptance summary reconstructs ``A`` in human-readable form.

        Intent: pin the Phase 8 requirement — a consumer can answer "why is
        ``A = 0.75``?" by reading the acceptance provenance, which names the
        passed and failed required checks.
        """
        contract = _contract()
        evidence = ExecutionEvidence(
            acceptance=[_passed("a"), _passed("b"), _passed("c"), _failed("d")],
        )
        evaluator = ContractEvaluator()
        evaluator.evaluate(contract, evidence)
        summary = evaluator.provenance["acceptance"][-1]
        assert summary.source == "summary"
        assert "3 of 4" in summary.description
        assert "a, b, c" in summary.description
        assert "d" in summary.description



# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------


class TestCost:
    """Cost ``C = mean(available normalized budget dimensions)`` and provenance."""

    def test_no_cost_budget_means_zero_with_provenance(self) -> None:
        """No budget on the contract → ``C = 0.0`` with an explanatory note.

        Intent: lock the documented "no cost budget was defined" outcome and its
        provenance, so a missing budget never produces an arbitrary cost.
        """
        contract = _contract(budget=None)
        evaluator = ContractEvaluator()
        scores = evaluator.evaluate(
            contract,
            ExecutionEvidence(rollback_available=True),
        )
        assert scores.cost == 0.0
        cost_records = evaluator.provenance["cost"]
        assert len(cost_records) == 1
        assert "no cost budget was defined" in cost_records[0].description

    def test_budget_exceeded_saturates_to_one(self) -> None:
        """Every available dimension over budget → each saturates → ``C = 1.0``.

        Intent: pin the cap at ``1.0`` — once a dimension exceeds its ceiling it
        cannot drive cost above ``1.0``, so a thoroughly over-budget step scores
        the maximum cost rather than an unbounded value.
        """
        contract = _contract(budget=_full_budget())
        evidence = ExecutionEvidence(
            retry_count=5,  # > 2
            tool_call_count=20,  # > 10
            token_usage=200,  # > 100
            runtime_seconds=30.0,  # > 10
            rollback_available=True,
        )
        evaluator = ContractEvaluator()
        scores = evaluator.evaluate(contract, evidence)
        assert scores.cost == 1.0
        # Each of the four available dimensions saturates at normalized=1.0, so
        # each contributes exactly 1/4 to the mean (cost == 1.0).
        dim_records = [
            r for r in evaluator.provenance["cost"] if r.source != "summary"
        ]
        assert len(dim_records) == 4
        for record in dim_records:
            assert record.contribution == pytest.approx(0.25)
            assert "normalized=" in record.description

    def test_cost_is_mean_of_available_dimensions(self) -> None:
        """A single over-budget dimension among four raises cost to ``0.25``.

        Intent: pin that ``C`` is the *mean* of the available dimensions, so one
        saturated dimension out of four contributes exactly ``1/4``.
        """
        contract = _contract(budget=_full_budget())
        evidence = ExecutionEvidence(
            retry_count=5,  # saturated -> 1.0
            tool_call_count=0,
            token_usage=0,
            runtime_seconds=0.0,
            rollback_available=True,
        )
        scores = ContractEvaluator().evaluate(contract, evidence)
        assert scores.cost == pytest.approx(0.25)
    def test_unmeasured_telemetry_is_conservatively_saturated(self) -> None:
        """A declared budget dimension with unmeured telemetry saturates to 1.0.

        Intent: lock the conservative rule from the evidence contract — ``None``
        telemetry for a *declared* budget dimension is not silently zero; it is
        saturated because compliance with the budget cannot be confirmed.
        """
        contract = _contract(budget=_full_budget())
        evidence = ExecutionEvidence(
            retry_count=0,
            tool_call_count=0,
            token_usage=None,  # unmeasured but a token budget IS declared
            runtime_seconds=None,  # unmeasured but a runtime budget IS declared
            rollback_available=True,
        )
        evaluator = ContractEvaluator()
        scores = evaluator.evaluate(contract, evidence)
        # retry + tool = 0; token + runtime = 2 saturated -> mean(0,0,1,1) = 0.5
        assert scores.cost == pytest.approx(0.5)
        token_record = next(
            ev for ev in evaluator.provenance["cost"] if ev.source == "token_cost"
        )
        assert "unmeasured" in token_record.description
        assert token_record.contribution == pytest.approx(0.25)
        # v0.7: an unmeasured dimension records MISSING provenance (never a
        # silent OBSERVED zero); a measured dimension carries its metric's
        # provenance verbatim.
        assert token_record.provenance is EvidenceProvenance.MISSING
        retry_record = next(
            ev for ev in evaluator.provenance["cost"] if ev.source == "retry_cost"
        )
        assert retry_record.provenance is EvidenceProvenance.MISSING

    def test_measured_cost_carries_observed_provenance(self) -> None:
        """A measured telemetry dimension records its metric's provenance.

        Intent: pin the v0.7 cost provenance rule — when telemetry *is*
        measured (an :class:`EvidenceMetric` with a value), the cost record
        carries that metric's provenance so a consumer can tell a measured
        budget dimension from a saturated-unmeasured one.
        """
        contract = _contract(budget=_full_budget())
        evidence = ExecutionEvidence(
            retry_count=EvidenceMetric(value=1, provenance=EvidenceProvenance.OBSERVED),
            tool_call_count=EvidenceMetric(value=4, provenance=EvidenceProvenance.OBSERVED),
            token_usage=EvidenceMetric(value=40, provenance=EvidenceProvenance.OBSERVED),
            runtime_seconds=EvidenceMetric(value=3.0, provenance=EvidenceProvenance.OBSERVED),
            rollback_available=True,
        )
        evaluator = ContractEvaluator()
        evaluator.evaluate(contract, evidence)
        retry_record = next(
            ev for ev in evaluator.provenance["cost"] if ev.source == "retry_cost"
        )
        assert retry_record.provenance is EvidenceProvenance.OBSERVED



# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------


class TestRisk:
    """Risk ``R = min(1.0, Σ contributions)`` and provenance."""

    def test_failed_high_severity_risk_rises_by_severity(self) -> None:
        """A single failed severity-0.9 risk check → ``R = 0.9``.

        Intent: pin that risk is additive and capped — a single failed check
        makes risk "rise by its severity" rather than being diluted by a mean,
        and the capped sum keeps ``R`` within ``[0, 1]``.
        """
        contract = _contract(
            acceptance_checks=[AcceptanceCheck(id="a", description="a")],
            risk_checks=[RiskCheck(id="r", description="r", severity=0.9)],
        )
        evidence = ExecutionEvidence(
            acceptance=[_passed("a")],
            risks=[_failed("r")],
            rollback_available=True,
        )
        scores = ContractEvaluator().evaluate(contract, evidence)
        assert scores.risk == pytest.approx(0.9)

    def test_failed_low_severity_is_lower_than_high_severity(self) -> None:
        """A severity-0.3 failure is below a severity-0.9 failure.

        Intent: confirm the configured ``severity`` scales the contribution, so
        the contract author's severity weighting is honoured deterministically.
        """
        base = _contract(
            acceptance_checks=[AcceptanceCheck(id="a", description="a")],
        )
        ev_ok = ExecutionEvidence(
            acceptance=[_passed("a")], rollback_available=True,
        )

        def risk_for(severity: float) -> float:
            contract = _contract(
                acceptance_checks=[AcceptanceCheck(id="a", description="a")],
                risk_checks=[RiskCheck(id="r", description="r", severity=severity)],
            )
            evidence = ExecutionEvidence(
                acceptance=[_passed("a")],
                risks=[_failed("r")],
                rollback_available=True,
            )
            return ContractEvaluator().evaluate(contract, evidence).risk

        assert risk_for(0.3) == pytest.approx(0.3)
        assert risk_for(0.9) == pytest.approx(0.9)
        assert risk_for(0.3) < risk_for(0.9)
        # A clean baseline (no failing checks, rollback available) is zero risk.
        assert ContractEvaluator().evaluate(base, ev_ok).risk == 0.0

    def test_rollback_unavailable_raises_risk(self) -> None:
        """``rollback_available=False`` raises risk above the available case.

        Intent: pin that a confirmed-unavailable rollback is a recovery-risk
        signal — all else equal, ``rollback_available=False`` must produce a
        strictly higher risk than ``rollback_available=True``.
        """
        contract = _contract(
            acceptance_checks=[AcceptanceCheck(id="a", description="a")],
        )
        available = ExecutionEvidence(
            acceptance=[_passed("a")], rollback_available=True,
        )
        unavailable = ExecutionEvidence(
            acceptance=[_passed("a")], rollback_available=False,
        )
        r_available = ContractEvaluator().evaluate(contract, available).risk
        r_unavailable = ContractEvaluator().evaluate(contract, unavailable).risk
        assert r_available == 0.0
        assert r_unavailable > r_available
        assert r_unavailable == 1.0  # full recovery-risk indicator

    def test_missing_risk_evidence_is_conservatively_violated(self) -> None:
        """A declared risk check with no evidence counts as violated.

        Intent: lock the conservative principle for *declared* contract items —
        a risk check the collector never observed is not assumed safe; it
        contributes its full severity so declared risks cannot be ignored.
        """
        contract = _contract(
            acceptance_checks=[AcceptanceCheck(id="a", description="a")],
            risk_checks=[RiskCheck(id="r", description="r", severity=0.5)],
        )
        # No risk evidence at all for the declared check "r".
        evidence = ExecutionEvidence(
            acceptance=[_passed("a")], rollback_available=True,
        )
        evaluator = ContractEvaluator()
        scores = evaluator.evaluate(contract, evidence)
        assert scores.risk == pytest.approx(0.5)
        r_record = next(
            ev for ev in evaluator.provenance["risk"] if ev.source == "r"
        )
        assert "conservatively as violated" in r_record.description

    def test_unmeasured_rollback_does_not_inflate_baseline_risk(self) -> None:
        """``rollback_available=None`` (unmeasured) does not raise risk.

        Intent: pin the pure-observable rule — rollback is not declared on the
        contract, so an unmeasured value is skipped rather than invented,
        keeping a clean step's baseline risk at zero.
        """
        contract = _contract(
            acceptance_checks=[AcceptanceCheck(id="a", description="a")],
        )
        evidence = ExecutionEvidence(acceptance=[_passed("a")])  # rollback None
        scores = ContractEvaluator().evaluate(contract, evidence)
        assert scores.risk == 0.0

    def test_unexpected_artifacts_raise_risk(self) -> None:
        """Non-empty ``unexpected_artifacts`` contributes to risk.

        Intent: pin the observable safety signal — artifacts the contract did
        not expect are a surprise-risk indicator that raises ``R``.
        """
        contract = _contract(
            acceptance_checks=[AcceptanceCheck(id="a", description="a")],
        )
        evidence = ExecutionEvidence(
            acceptance=[_passed("a")],
            unexpected_artifacts=["scratch/untracked.py"],
            rollback_available=True,
        )
        scores = ContractEvaluator().evaluate(contract, evidence)
        assert scores.risk == 1.0  # full surprise indicator



# ---------------------------------------------------------------------------
# Influence & determinism
# ---------------------------------------------------------------------------


class TestInfluenceAndDeterminism:
    """Influence honesty and end-to-end determinism."""

    def test_influence_defaults_to_zero(self) -> None:
        """Influence defaults to ``0.0`` recorded as a DEFAULTED value.

        Intent: pin that v0.3 does not invent downstream influence from
        contract evidence — it is honestly ``0.0`` unless supplied externally.
        v0.7 records this absence explicitly: ``provenance=DEFAULTED``,
        ``raw_value=None`` (nothing observed), ``effective_value=0.0`` (the
        policy-neutral value used), and a ``reason`` explaining the
        substitution. DEFAULTED must never be presented as VERIFIED.
        """
        contract = _contract(
            acceptance_checks=[AcceptanceCheck(id="a", description="a")],
        )
        evidence = ExecutionEvidence(
            acceptance=[_passed("a")], rollback_available=True,
        )
        evaluator = ContractEvaluator()
        scores = evaluator.evaluate(contract, evidence)
        assert scores.influence == 0.0
        inf = evaluator.provenance["influence"]
        assert len(inf) == 1
        assert "honesty" in inf[0].description.lower()
        assert inf[0].provenance is EvidenceProvenance.DEFAULTED
        assert inf[0].raw_value is None
        assert inf[0].effective_value == 0.0
        assert inf[0].reason == "policy neutral value; no evidence source"

    def test_external_influence_override(self) -> None:
        """An externally-supplied influence override is honoured verbatim.

        Intent: confirm the optional constructor seam mirrors
        :class:`~bound.workflow.CodingWorkflowEvaluator`, letting a caller inject
        downstream influence the contract cannot derive itself. v0.7 records the
        externally-supplied value as EVALUATED provenance (derived/supplied, not
        independently observed) with explicit ``raw_value``/``effective_value``.
        """
        contract = _contract(
            acceptance_checks=[AcceptanceCheck(id="a", description="a")],
        )
        evidence = ExecutionEvidence(
            acceptance=[_passed("a")], rollback_available=True,
        )
        evaluator = ContractEvaluator(influence=0.3)
        scores = evaluator.evaluate(contract, evidence)
        assert scores.influence == pytest.approx(0.3)
        inf = evaluator.provenance["influence"][0]
        assert inf.source == "external"
        assert inf.provenance is EvidenceProvenance.EVALUATED
        assert inf.raw_value == pytest.approx(0.3)
        assert inf.effective_value == pytest.approx(0.3)

    def test_deterministic_repeatability_same_inputs(self) -> None:
        """The same contract + evidence yield identical scores and provenance.

        Intent: pin the non-negotiable determinism guarantee — no network, no
        LLM, no hidden state, so two evaluations (even on fresh evaluator
        instances) are bit-for-bit equal including the full provenance.
        """
        contract = _contract(
            acceptance_checks=list(_REQUIRED_CHECKS),
            risk_checks=[RiskCheck(id="r", description="r", severity=0.6)],
            budget=_full_budget(),
        )
        evidence = ExecutionEvidence(
            acceptance=[_passed("a"), _passed("b"), _passed("c"), _failed("d")],
            risks=[_failed("r")],
            retry_count=1,
            tool_call_count=4,
            token_usage=40,
            runtime_seconds=3.0,
            rollback_available=False,
            unexpected_artifacts=["x.py"],
        )

        evaluator_a = ContractEvaluator()
        evaluator_b = ContractEvaluator()
        scores_a = evaluator_a.evaluate(contract, evidence)
        scores_b = evaluator_b.evaluate(contract, evidence)

        assert scores_a == scores_b
        # Provenance must also be stable across calls/instances.
        assert evaluator_a.provenance == evaluator_b.provenance

        # A second call on the same instance reproduces the first.
        scores_a_again = evaluator_a.evaluate(contract, evidence)
        assert scores_a_again == scores_a

    def test_provenance_has_all_four_dimensions(self) -> None:
        """Provenance is keyed by all four BOUND dimensions after evaluate.

        Intent: pin the Phase 8 contract shape so a downstream policy/harness can
        always read ``acceptance``/``influence``/``risk``/``cost``.
        """
        contract = _contract(
            acceptance_checks=[AcceptanceCheck(id="a", description="a")],
            budget=_full_budget(),
        )
        evaluator = ContractEvaluator()
        # Before evaluate, provenance is empty.
        assert evaluator.provenance == {}
        evaluator.evaluate(
            contract,
            ExecutionEvidence(acceptance=[_passed("a")], rollback_available=True),
        )
        assert set(evaluator.provenance.keys()) == {
            "acceptance",
            "influence",
            "risk",
            "cost",
        }
        # Every entry is a list of ScoreEvidence.
        for records in evaluator.provenance.values():
            assert isinstance(records, list)
            assert records
            assert all(isinstance(r, ScoreEvidence) for r in records)

    def test_scores_carry_reasoning_summary(self) -> None:
        """Returned scores carry a human-readable ``reasoning`` summary.

        Intent: pin the self-explaining property (mirroring
        :class:`~bound.workflow.CodingWorkflowEvaluator`) so scores are auditable
        even without the surrounding result object.
        """
        contract = _contract(
            acceptance_checks=[AcceptanceCheck(id="a", description="a")],
        )
        evidence = ExecutionEvidence(
            acceptance=[_passed("a")], rollback_available=True,
        )
        scores = ContractEvaluator().evaluate(contract, evidence)
        assert isinstance(scores, EvaluationScores)
        assert scores.reasoning is not None
        assert "v0.3 reference heuristic" in scores.reasoning
        assert "A=1.0000" in scores.reasoning


# ---------------------------------------------------------------------------
# Decision assurance (v0.7)
# ---------------------------------------------------------------------------


def _verified(check_id: str, source: str = "bound.junit") -> CheckEvidence:
    """Build a passing check backed by VERIFIED provenance."""
    return CheckEvidence(
        check_id=check_id, passed=True, source=source,
        provenance=EvidenceProvenance.VERIFIED,
    )


def _observed(check_id: str, source: str = "harness") -> CheckEvidence:
    """Build a passing check backed by OBSERVED provenance."""
    return CheckEvidence(
        check_id=check_id, passed=True, source=source,
        provenance=EvidenceProvenance.OBSERVED,
    )


def _claimed(check_id: str, source: str = "agent") -> CheckEvidence:
    """Build a passing check backed by CLAIMED (agent self-report) provenance."""
    return CheckEvidence(
        check_id=check_id, passed=True, source=source,
        provenance=EvidenceProvenance.CLAIMED,
    )


def _evaluated(check_id: str, source: str = "bound.eval") -> CheckEvidence:
    """Build a passing check backed by EVALUATED provenance."""
    return CheckEvidence(
        check_id=check_id, passed=True, source=source,
        provenance=EvidenceProvenance.EVALUATED,
    )


def _invalid(check_id: str, source: str = "bound.junit") -> CheckEvidence:
    """Build a check with INVALID status (collector/parser failure)."""
    return CheckEvidence(
        check_id=check_id, passed=None, source=source,
        provenance=EvidenceProvenance.MISSING, status=EvidenceStatus.INVALID,
    )


class TestDecisionAssurance:
    """Deterministic :class:`DecisionAssurance` from decision-critical evidence."""

    def test_no_restricted_checks_yields_verified(self) -> None:
        """No decision-critical / accepted-provenance checks → VERIFIED (vacuous).

        Intent: pin the vacuous case — when the contract requires nothing to be
        independently verified, nothing can degrade the assurance.
        """
        contract = _contract(
            acceptance_checks=[AcceptanceCheck(id="a", description="a")],
        )
        evidence = ExecutionEvidence(
            acceptance=[_passed("a")], rollback_available=True,
        )
        evaluator = ContractEvaluator()
        evaluator.evaluate(contract, evidence)
        a = evaluator.assurance_assessment
        assert isinstance(a, AssuranceAssessment)
        assert a.assurance is DecisionAssurance.VERIFIED
        assert a.accept_block_action is None
        assert a.accept_block_reasons == []

    def test_verified_critical_risk_yields_verified(self) -> None:
        """A decision-critical risk check with VERIFIED evidence → VERIFIED.

        Intent: pin the strongest assurance — every decision-critical check
        backed by independently verified evidence.
        """
        contract = _contract(
            risk_checks=[
                RiskCheck(
                    id="no-secrets", description="No secrets", severity=1.0,
                    decision_critical=True,
                    accepted_provenance=[EvidenceProvenance.VERIFIED],
                ),
            ],
        )
        evidence = ExecutionEvidence(
            risks=[_verified("no-secrets")], rollback_available=True,
        )
        evaluator = ContractEvaluator()
        evaluator.evaluate(contract, evidence)
        a = evaluator.assurance_assessment
        assert a.assurance is DecisionAssurance.VERIFIED
        assert a.accept_block_action is None

    def test_observed_critical_risk_yields_verified(self) -> None:
        """OBSERVED provenance is verified-tier for assurance purposes."""
        contract = _contract(
            risk_checks=[
                RiskCheck(
                    id="no-secrets", description="No secrets", severity=1.0,
                    decision_critical=True,
                ),
            ],
        )
        evidence = ExecutionEvidence(
            risks=[_observed("no-secrets")], rollback_available=True,
        )
        evaluator = ContractEvaluator()
        evaluator.evaluate(contract, evidence)
        assert evaluator.assurance_assessment.assurance is DecisionAssurance.VERIFIED

    def test_missing_critical_risk_yields_insufficient(self) -> None:
        """A decision-critical check with no evidence → INSUFFICIENT.

        Intent: pin the conservative rule — required/decision-critical evidence
        that is missing forces INSUFFICIENT assurance, blocking a clean ACCEPT.
        """
        contract = _contract(
            risk_checks=[
                RiskCheck(
                    id="no-secrets", description="No secrets", severity=1.0,
                    decision_critical=True,
                    on_missing=EvidencePolicyAction.ROLLBACK,
                ),
            ],
        )
        evidence = ExecutionEvidence(rollback_available=True)
        evaluator = ContractEvaluator()
        evaluator.evaluate(contract, evidence)
        a = evaluator.assurance_assessment
        assert a.assurance is DecisionAssurance.INSUFFICIENT
        assert a.accept_block_action is EvidencePolicyAction.ROLLBACK
        assert any("no matching evidence" in r for r in a.reasons)

    def test_invalid_evidence_yields_insufficient(self) -> None:
        """A restricted check with INVALID evidence (collector crash) → INSUFFICIENT.

        Intent: pin item-8 fail-safety — a stale artefact or collector crash
        produces INVALID evidence, which forces INSUFFICIENT (never a pass).
        """
        contract = _contract(
            risk_checks=[
                RiskCheck(
                    id="tests-pass", description="Tests pass", severity=0.9,
                    decision_critical=True,
                    accepted_provenance=[EvidenceProvenance.VERIFIED],
                    on_missing=EvidencePolicyAction.RETRY,
                ),
            ],
        )
        evidence = ExecutionEvidence(
            risks=[_invalid("tests-pass")], rollback_available=True,
        )
        evaluator = ContractEvaluator()
        evaluator.evaluate(contract, evidence)
        a = evaluator.assurance_assessment
        assert a.assurance is DecisionAssurance.INSUFFICIENT
        assert a.accept_block_action is EvidencePolicyAction.RETRY
        assert any("INVALID" in r for r in a.reasons)

    def test_claimed_critical_risk_yields_claimed(self) -> None:
        """A decision-critical check relying on agent self-report → CLAIMED.

        Intent: pin the item-9 rule — when the decision leans on agent
        self-report (CLAIMED) for a critical check, the assurance is CLAIMED and
        the candidate ACCEPT is gated to the contract's ``on_claimed`` action.
        """
        contract = _contract(
            risk_checks=[
                RiskCheck(
                    id="no-secrets", description="No secrets", severity=1.0,
                    decision_critical=True,
                    on_claimed=EvidencePolicyAction.RETRY,
                ),
            ],
        )
        evidence = ExecutionEvidence(
            risks=[_claimed("no-secrets")], rollback_available=True,
        )
        evaluator = ContractEvaluator()
        evaluator.evaluate(contract, evidence)
        a = evaluator.assurance_assessment
        assert a.assurance is DecisionAssurance.CLAIMED
        assert a.accept_block_action is EvidencePolicyAction.RETRY
        assert any("CLAIMED" in r for r in a.reasons)

    def test_evaluated_evidence_yields_mixed(self) -> None:
        """A restricted check backed by EVALUATED evidence → MIXED (no block).

        Intent: pin that EVALUATED (derived by BOUND, not independently
        observed) is honest but weaker, contributing to a MIXED assurance. MIXED
        never blocks an ACCEPT — the evidence is acceptable, just not verified.
        """
        contract = _contract(
            acceptance_checks=[
                AcceptanceCheck(
                    id="ux", description="UX quality",
                    accepted_provenance=[
                        EvidenceProvenance.VERIFIED,
                        EvidenceProvenance.EVALUATED,
                    ],
                ),
            ],
        )
        evidence = ExecutionEvidence(
            acceptance=[_evaluated("ux")], rollback_available=True,
        )
        evaluator = ContractEvaluator()
        evaluator.evaluate(contract, evidence)
        a = evaluator.assurance_assessment
        assert a.assurance is DecisionAssurance.MIXED
        assert a.accept_block_action is None

    def test_verified_plus_evaluated_yields_mixed(self) -> None:
        """Verified acceptance plus evaluated UX → MIXED (todo DoD example)."""
        contract = _contract(
            acceptance_checks=[
                AcceptanceCheck(
                    id="tests-pass", description="Tests pass",
                    accepted_provenance=[EvidenceProvenance.VERIFIED],
                ),
                AcceptanceCheck(
                    id="ux", description="UX quality",
                    accepted_provenance=[
                        EvidenceProvenance.VERIFIED,
                        EvidenceProvenance.EVALUATED,
                    ],
                ),
            ],
        )
        evidence = ExecutionEvidence(
            acceptance=[_verified("tests-pass"), _evaluated("ux")],
            rollback_available=True,
        )
        evaluator = ContractEvaluator()
        evaluator.evaluate(contract, evidence)
        assert evaluator.assurance_assessment.assurance is DecisionAssurance.MIXED

    def test_unrestricted_check_never_degrades_assurance(self) -> None:
        """A check with no accepted_provenance and not decision-critical is ignored.

        Intent: pin that only *restricted* checks influence assurance — an
        unrestricted check whose evidence is only CLAIMED does not degrade the
        assurance, because the contract did not require it to be verified.
        """
        contract = _contract(
            acceptance_checks=[AcceptanceCheck(id="a", description="a")],
            risk_checks=[
                RiskCheck(id="soft", description="soft signal", severity=0.1),
            ],
        )
        evidence = ExecutionEvidence(
            acceptance=[_claimed("a")],
            risks=[_claimed("soft")],
            rollback_available=True,
        )
        evaluator = ContractEvaluator()
        evaluator.evaluate(contract, evidence)
        assert evaluator.assurance_assessment.assurance is DecisionAssurance.VERIFIED

    def test_most_severe_block_action_wins(self) -> None:
        """When several restricted checks fail, the most severe action wins.

        Intent: pin that a single ``on_missing=rollback`` critical check drags
        the ACCEPT-block action to ROLLBACK even alongside a softer RETRY check.
        """
        contract = _contract(
            risk_checks=[
                RiskCheck(
                    id="soft", description="soft", severity=0.1,
                    accepted_provenance=[EvidenceProvenance.VERIFIED],
                    on_missing=EvidencePolicyAction.RETRY,
                ),
                RiskCheck(
                    id="hard", description="hard", severity=1.0,
                    decision_critical=True,
                    accepted_provenance=[EvidenceProvenance.VERIFIED],
                    on_missing=EvidencePolicyAction.ROLLBACK,
                ),
            ],
        )
        evidence = ExecutionEvidence(rollback_available=True)
        evaluator = ContractEvaluator()
        evaluator.evaluate(contract, evidence)
        a = evaluator.assurance_assessment
        assert a.assurance is DecisionAssurance.INSUFFICIENT
        assert a.accept_block_action is EvidencePolicyAction.ROLLBACK


# ---------------------------------------------------------------------------
# Active-policy gating (v0.7 — todo Phase 6 + 2.2 enforcement)
# ---------------------------------------------------------------------------


def _policy(**overrides: object) -> BoundPolicyConfig:
    """Build a minimal valid :class:`BoundPolicyConfig` for the gating tests.

    Args:
        **overrides: Fields to override on the base ``coding-test@1.0`` policy.

    Returns:
        A validated :class:`BoundPolicyConfig`.
    """
    base: dict[str, object] = {
        "schema_version": "1.0",
        "policy": PolicyIdentity(id="coding-test", version="1.0"),
    }
    base.update(overrides)
    return BoundPolicyConfig(**base)  # type: ignore[arg-type]


def _metric(
    value: int | float, prov: EvidenceProvenance = EvidenceProvenance.OBSERVED,
) -> EvidenceMetric:
    """Build a measured :class:`EvidenceMetric`."""
    return EvidenceMetric(value=value, provenance=prov)


def _passed_evs(*check_ids: str) -> list[CheckEvidence]:
    """Build a passing :class:`CheckEvidence` per id (VERIFIED)."""
    return [
        CheckEvidence(check_id=cid, passed=True, provenance=EvidenceProvenance.VERIFIED)
        for cid in check_ids
    ]


class TestAcceptanceWithPolicy:
    """Weighted-signal aggregation into the acceptance dimension (todo 2.2)."""

    def test_quality_signals_feed_weighted_acceptance(self) -> None:
        """Passing weighted signals are recorded with their effective weights."""
        contract = _contract()
        evidence = ExecutionEvidence(
            acceptance=[*_passed_evs("a", "b", "c", "d")],
            rollback_available=True,
        )
        policy = _policy(
            quality_checks=[
                WeightedSignal(id="lint", description="lint", importance="high"),
                WeightedSignal(id="coverage", description="cov", importance="low"),
            ],
        )
        evaluator = ContractEvaluator()
        evaluator.evaluate(contract, evidence, policy=policy)

        weights = evaluator.effective_weights
        assert weights["lint"] == 1.0
        assert weights["coverage"] == 0.25
        # Required contract checks carry an implicit weight of 1.0 each.
        assert all(weights[c.id] == 1.0 for c in contract.acceptance_checks)

    def test_failing_signal_does_not_confirm_pass(self) -> None:
        """A signal whose evidence is missing contributes 0 to A."""
        contract = _contract(acceptance_checks=[AcceptanceCheck(id="a", description="a")])
        evidence = ExecutionEvidence(acceptance=[_passed("a")], rollback_available=True)
        policy = _policy(
            quality_checks=[WeightedSignal(id="lint", description="lint", importance="high")],
        )
        evaluator = ContractEvaluator()
        scores = evaluator.evaluate(contract, evidence, policy=policy)
        # One passing required check (w=1.0) + one missing signal (w=1.0, pass=0)
        # → A = 1.0 / 2.0 = 0.5.
        assert scores.acceptance == pytest.approx(0.5)

    def test_ignore_weighted_signal_contributes_zero(self) -> None:
        """An ``ignore`` signal has effective_weight 0 and never moves A."""
        contract = _contract(acceptance_checks=[AcceptanceCheck(id="a", description="a")])
        evidence = ExecutionEvidence(acceptance=[_passed("a")], rollback_available=True)
        policy = _policy(
            quality_checks=[WeightedSignal(id="noop", description="noop", importance="ignore")],
        )
        evaluator = ContractEvaluator()
        scores = evaluator.evaluate(contract, evidence, policy=policy)
        assert scores.acceptance == pytest.approx(1.0)
        assert evaluator.effective_weights["noop"] == 0.0

    def test_explicit_weight_override(self) -> None:
        """An explicit ``weight`` overrides the importance-derived default."""
        contract = _contract(acceptance_checks=[AcceptanceCheck(id="a", description="a")])
        evidence = ExecutionEvidence(acceptance=[_passed("a")], rollback_available=True)
        policy = _policy(
            quality_checks=[
                WeightedSignal(id="lint", description="lint", importance="low", weight=0.9),
            ],
        )
        evaluator = ContractEvaluator()
        evaluator.evaluate(contract, evidence, policy=policy)
        assert evaluator.effective_weights["lint"] == 0.9


class TestPolicyGateBlockers:
    """Hard gates (blockers) cannot be compensated (todo 2.2 / 6.1)."""

    def test_missing_blocker_evidence_cannot_be_compensated(self) -> None:
        """A blocker with no evidence fails regardless of positive signals."""
        contract = _contract(acceptance_checks=[AcceptanceCheck(id="a", description="a")])
        evidence = ExecutionEvidence(acceptance=[_passed("a")], rollback_available=True)
        policy = _policy(
            acceptance_checks=[
                HardGate(id="must", description="must", on_missing=EvidencePolicyAction.RETRY),
            ],
            quality_checks=[WeightedSignal(id="lint", description="lint", importance="high")],
        )
        evaluator = ContractEvaluator()
        gate = evaluator.assess_policy_gate(contract, evidence, policy)
        assert gate.blocker_failed is True
        assert gate.blocker_action is EvidencePolicyAction.RETRY
        assert gate.forced_action is EvidencePolicyAction.RETRY
        assert any("blocker" in r and "must" in r for r in gate.blocker_reasons)

    def test_failed_blocker_uses_on_failure(self) -> None:
        """An observed blocker failure forces ``on_failure`` (rollback here)."""
        contract = _contract(acceptance_checks=[AcceptanceCheck(id="a", description="a")])
        evidence = ExecutionEvidence(
            acceptance=[
                _passed("a"),
                CheckEvidence(
                    check_id="must", passed=False, provenance=EvidenceProvenance.VERIFIED,
                ),
            ],
            rollback_available=True,
        )
        policy = _policy(
            acceptance_checks=[
                HardGate(id="must", description="must", on_failure=EvidencePolicyAction.ROLLBACK),
            ],
        )
        evaluator = ContractEvaluator()
        gate = evaluator.assess_policy_gate(contract, evidence, policy)
        assert gate.blocker_failed is True
        assert gate.blocker_action is EvidencePolicyAction.ROLLBACK

    def test_passing_blocker_does_not_gate(self) -> None:
        """A blocker whose evidence passes imposes no forced action."""
        contract = _contract(acceptance_checks=[AcceptanceCheck(id="a", description="a")])
        evidence = ExecutionEvidence(
            acceptance=[
                _passed("a"),
                CheckEvidence(check_id="must", passed=True, provenance=EvidenceProvenance.VERIFIED),
            ],
            rollback_available=True,
        )
        policy = _policy(acceptance_checks=[HardGate(id="must", description="must")])
        evaluator = ContractEvaluator()
        gate = evaluator.assess_policy_gate(contract, evidence, policy)
        assert gate.blocker_failed is False
        assert gate.forced_action is None

    def test_risk_blocker_reconciled_against_risk_evidence(self) -> None:
        """Risk blockers are matched against ``evidence.risks`` by id."""
        contract = _contract(risk_checks=[])
        evidence = ExecutionEvidence(
            risks=[
                CheckEvidence(
                    check_id="no-secrets", passed=False,
                    provenance=EvidenceProvenance.VERIFIED,
                )
            ],
            rollback_available=True,
        )
        policy = _policy(
            risk_checks=[
                HardGate(
                    id="no-secrets", description="ns",
                    on_failure=EvidencePolicyAction.ROLLBACK,
                ),
            ],
        )
        evaluator = ContractEvaluator()
        gate = evaluator.assess_policy_gate(contract, evidence, policy)
        assert gate.blocker_failed is True
        assert gate.blocker_action is EvidencePolicyAction.ROLLBACK

    def test_advisory_blocker_never_fails(self) -> None:
        """A ``required=False`` blocker is advisory and never gates."""
        contract = _contract()
        evidence = ExecutionEvidence(rollback_available=True)
        policy = _policy(
            acceptance_checks=[HardGate(id="opt", description="opt", required=False)],
        )
        evaluator = ContractEvaluator()
        gate = evaluator.assess_policy_gate(contract, evidence, policy)
        assert gate.blocker_failed is False
        assert gate.forced_action is None

    def test_multiple_blockers_most_severe_wins(self) -> None:
        """When several blockers fail, the most conservative action wins."""
        contract = _contract()
        evidence = ExecutionEvidence(
            acceptance=[
                CheckEvidence(
                    check_id="soft", passed=False, provenance=EvidenceProvenance.VERIFIED,
                ),
                CheckEvidence(
                    check_id="hard", passed=False, provenance=EvidenceProvenance.VERIFIED,
                ),
            ],
            rollback_available=True,
        )
        policy = _policy(
            acceptance_checks=[
                HardGate(id="soft", description="s", on_failure=EvidencePolicyAction.RETRY),
                HardGate(id="hard", description="h", on_failure=EvidencePolicyAction.ROLLBACK),
            ],
        )
        evaluator = ContractEvaluator()
        gate = evaluator.assess_policy_gate(contract, evidence, policy)
        assert gate.blocker_action is EvidencePolicyAction.ROLLBACK

    def test_blocker_cannot_be_compensated_by_score(self) -> None:
        """End-to-end: a high score ACCEPT is downgraded when a blocker fails.

        Intent: pin the headline guarantee — blockers cannot be offset by
        positive weighted signals, so a candidate ACCEPT becomes RETRY here.
        """
        contract = _contract(acceptance_checks=[AcceptanceCheck(id="a", description="a")])
        evidence = ExecutionEvidence(acceptance=[_passed("a")], rollback_available=True)
        policy = _policy(
            acceptance_checks=[
                HardGate(
                    id="must", description="must", on_missing=EvidencePolicyAction.RETRY,
                ),
            ],
        )
        result = BoundWorkflow().evaluate_step(
            contract=contract,
            evidence=evidence,
            criteria=BoundCriteria(threshold=0.4, rollback_risk_threshold=0.9),
            policy=policy,
        )
        assert result.candidate_decision == "ACCEPT"
        assert result.final_decision == "RETRY"


class TestPolicyGateMinimumAssurance:
    """``minimum_assurance`` enforcement on hard gates (todo 6.1)."""

    def test_claimed_cannot_satisfy_verified_blocker(self) -> None:
        """CLAIMED evidence below a VERIFIED floor fails the gate."""
        contract = _contract(acceptance_checks=[AcceptanceCheck(id="a", description="a")])
        evidence = ExecutionEvidence(
            acceptance=[
                _passed("a"),
                CheckEvidence(check_id="gate", passed=True, provenance=EvidenceProvenance.CLAIMED),
            ],
            rollback_available=True,
        )
        policy = _policy(
            acceptance_checks=[
                HardGate(
                    id="gate", description="gate",
                    minimum_assurance=DecisionAssurance.VERIFIED,
                    accepted_provenance=[EvidenceProvenance.VERIFIED, EvidenceProvenance.OBSERVED],
                    on_claimed=EvidencePolicyAction.REPLAN,
                ),
            ],
        )
        evaluator = ContractEvaluator()
        gate = evaluator.assess_policy_gate(contract, evidence, policy)
        assert gate.blocker_failed is True
        assert gate.blocker_action is EvidencePolicyAction.REPLAN

    def test_verified_evidence_meets_verified_floor(self) -> None:
        """VERIFIED evidence satisfies a VERIFIED ``minimum_assurance``."""
        contract = _contract(acceptance_checks=[AcceptanceCheck(id="a", description="a")])
        evidence = ExecutionEvidence(
            acceptance=[
                _passed("a"),
                CheckEvidence(check_id="gate", passed=True, provenance=EvidenceProvenance.VERIFIED),
            ],
            rollback_available=True,
        )
        policy = _policy(
            acceptance_checks=[
                HardGate(
                    id="gate", description="gate",
                    minimum_assurance=DecisionAssurance.VERIFIED,
                ),
            ],
        )
        evaluator = ContractEvaluator()
        gate = evaluator.assess_policy_gate(contract, evidence, policy)
        assert gate.blocker_failed is False

    def test_missing_evidence_is_insufficient(self) -> None:
        """Missing evidence for a ``minimum_assurance`` gate is INSUFFICIENT."""
        contract = _contract(acceptance_checks=[AcceptanceCheck(id="a", description="a")])
        evidence = ExecutionEvidence(acceptance=[_passed("a")], rollback_available=True)
        policy = _policy(
            acceptance_checks=[
                HardGate(
                    id="gate", description="gate",
                    minimum_assurance=DecisionAssurance.MIXED,
                    on_missing=EvidencePolicyAction.RETRY,
                ),
            ],
        )
        evaluator = ContractEvaluator()
        gate = evaluator.assess_policy_gate(contract, evidence, policy)
        assert gate.blocker_failed is True
        assert gate.blocker_action is EvidencePolicyAction.RETRY


class TestPolicyGateBudgets:
    """Budget enforcement with soft/hard limits (todo 2.2)."""

    def test_hard_limit_breach_forces_on_hard(self) -> None:
        """A value at/over the hard limit breaches with ``on_hard``."""
        contract = _contract()
        evidence = ExecutionEvidence(tool_call_count=_metric(20), rollback_available=True)
        policy = _policy(
            budgets={
                "tool_calls": BudgetDimension(hard_limit=10, on_hard=EvidencePolicyAction.REPLAN),
            },
        )
        evaluator = ContractEvaluator()
        gate = evaluator.assess_policy_gate(contract, evidence, policy)
        assert gate.budget_breached is True
        assert gate.budget_action is EvidencePolicyAction.REPLAN
        status = next(s for s in gate.budget_status if s.dimension == "tool_calls")
        assert status.state == "hard"

    def test_soft_limit_breach_forces_on_soft(self) -> None:
        """A value over the soft (under hard) limit breaches with ``on_soft``."""
        contract = _contract()
        evidence = ExecutionEvidence(tool_call_count=_metric(12), rollback_available=True)
        policy = _policy(
            budgets={
                "tool_calls": BudgetDimension(
                    soft_limit=10, hard_limit=20,
                    on_soft=EvidencePolicyAction.RETRY, on_hard=EvidencePolicyAction.REPLAN,
                )
            },
        )
        evaluator = ContractEvaluator()
        gate = evaluator.assess_policy_gate(contract, evidence, policy)
        assert gate.budget_action is EvidencePolicyAction.RETRY
        status = next(s for s in gate.budget_status if s.dimension == "tool_calls")
        assert status.state == "soft"

    def test_missing_telemetry_cannot_silently_satisfy_budget(self) -> None:
        """Missing telemetry for a declared limit is treated as over-budget."""
        contract = _contract()
        evidence = ExecutionEvidence(rollback_available=True)  # token_usage is None
        policy = _policy(
            budgets={
                "tokens": BudgetDimension(hard_limit=1000, on_hard=EvidencePolicyAction.REPLAN),
            },
        )
        evaluator = ContractEvaluator()
        gate = evaluator.assess_policy_gate(contract, evidence, policy)
        assert gate.budget_breached is True
        assert gate.budget_action is EvidencePolicyAction.REPLAN
        status = next(s for s in gate.budget_status if s.dimension == "tokens")
        assert status.state == "missing"
        assert status.measured_value is None

    def test_disabled_budget_is_skipped(self) -> None:
        """An explicitly ``enabled=False`` budget is not enforced."""
        contract = _contract()
        evidence = ExecutionEvidence(tool_call_count=_metric(999), rollback_available=True)
        policy = _policy(
            budgets={"tool_calls": BudgetDimension(hard_limit=10, enabled=False)},
        )
        evaluator = ContractEvaluator()
        gate = evaluator.assess_policy_gate(contract, evidence, policy)
        assert gate.budget_breached is False
        assert gate.budget_action is None
        assert not any(s.dimension == "tool_calls" for s in gate.budget_status)

    def test_within_budget_dimension_records_none_state(self) -> None:
        """A dimension within its limits records state ``none`` with no action."""
        contract = _contract()
        evidence = ExecutionEvidence(tool_call_count=_metric(5), rollback_available=True)
        policy = _policy(
            budgets={
                "tool_calls": BudgetDimension(
                    soft_limit=10, hard_limit=20,
                    on_soft=EvidencePolicyAction.RETRY, on_hard=EvidencePolicyAction.REPLAN,
                )
            },
        )
        evaluator = ContractEvaluator()
        gate = evaluator.assess_policy_gate(contract, evidence, policy)
        assert gate.budget_breached is False
        status = next(s for s in gate.budget_status if s.dimension == "tool_calls")
        assert status.state == "none"
        assert status.action is None

    def test_financial_cost_budget_with_no_telemetry_is_missing(self) -> None:
        """``financial_cost`` has no telemetry, so a declared budget breaches."""
        contract = _contract()
        evidence = ExecutionEvidence(rollback_available=True)
        policy = _policy(
            budgets={
                "financial_cost": BudgetDimension(
                    hard_limit=1.0, on_hard=EvidencePolicyAction.REPLAN,
                ),
            },
        )
        evaluator = ContractEvaluator()
        gate = evaluator.assess_policy_gate(contract, evidence, policy)
        assert gate.budget_breached is True
        status = next(s for s in gate.budget_status if s.dimension == "financial_cost")
        assert status.state == "missing"


class TestPolicyGateTrace:
    """Trace fields: effective weights, policy identity and hash (todo 2.2/6.2)."""

    def test_gate_records_policy_identity_and_hash(self) -> None:
        """The gate outcome carries the policy id/version/hash for the trace."""
        contract = _contract()
        evidence = ExecutionEvidence(rollback_available=True)
        policy = _policy(
            acceptance_checks=[
                HardGate(id="g", description="g", on_missing=EvidencePolicyAction.RETRY),
            ],
        )
        evaluator = ContractEvaluator()
        gate = evaluator.assess_policy_gate(contract, evidence, policy)
        assert gate.policy_id == "coding-test"
        assert gate.policy_version == "1.0"
        assert gate.policy_hash.startswith("sha256:")

    def test_evaluate_without_policy_has_no_gate(self) -> None:
        """The contract-only path leaves ``policy_gate`` and weights empty."""
        contract = _contract()
        evidence = ExecutionEvidence(
            acceptance=[*_passed_evs("a", "b", "c", "d")],
            rollback_available=True,
        )
        evaluator = ContractEvaluator()
        evaluator.evaluate(contract, evidence)  # no policy
        assert evaluator.policy_gate is None
        assert evaluator.effective_weights == {}

    def test_evaluate_with_policy_populates_gate_and_weights(self) -> None:
        """An active policy populates the gate outcome and effective weights."""
        contract = _contract()
        evidence = ExecutionEvidence(
            acceptance=[*_passed_evs("a", "b", "c", "d")],
            rollback_available=True,
        )
        policy = _policy(
            quality_checks=[
                WeightedSignal(id="lint", description="lint", importance="medium"),
            ],
        )
        evaluator = ContractEvaluator()
        evaluator.evaluate(contract, evidence, policy=policy)
        assert evaluator.policy_gate is not None
        assert evaluator.policy_gate.policy_id == "coding-test"
        assert evaluator.effective_weights["lint"] == 0.5


class TestBudgetStatus:
    """The :class:`BudgetStatus` trace record (todo 2.2)."""

    def test_budget_status_is_dataclass(self) -> None:
        """``BudgetStatus`` is a plain dataclass with the documented fields."""
        status = BudgetStatus(
            dimension="attempts", measured_value=3.0, soft_limit=2.0, hard_limit=3.0,
            state="hard", action=EvidencePolicyAction.REPLAN, reason="over",
        )
        assert status.dimension == "attempts"
        assert status.state == "hard"
        assert status.action is EvidencePolicyAction.REPLAN

    def test_policy_gate_outcome_forced_action_picks_most_severe(self) -> None:
        """``forced_action`` returns the most severe of blocker/budget actions."""
        gate = PolicyGateOutcome(
            blocker_failed=True, blocker_action=EvidencePolicyAction.RETRY,
            budget_breached=True, budget_action=EvidencePolicyAction.ROLLBACK,
        )
        assert gate.forced_action is EvidencePolicyAction.ROLLBACK

    def test_policy_gate_outcome_no_force_returns_none(self) -> None:
        """With no blocker/budget action, ``forced_action`` is ``None``."""
        gate = PolicyGateOutcome()
        assert gate.forced_action is None
        assert gate.forced_reasons == []
