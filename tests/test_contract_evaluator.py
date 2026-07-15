"""Unit tests for the deterministic contract evaluator (v0.3 Phases 7 & 8).

These tests pin the :class:`bound.contract_evaluator.ContractEvaluator` contract
mandated by the v0.3 TODO ("Contract evaluation" section of Phase 16):

* all required checks pass (``A = 1.0``),
* partial acceptance (3 of 4 → ``A = 0.75``),
* no cost budget (``C = 0.0`` with provenance explaining the absence),
* budget exceeded (every normalized dimension saturates at ``1.0`` → ``C = 1.0``),
* a failed high-severity risk check makes risk "rise by its severity",
* a confirmed-unavailable rollback raises risk,
* the same inputs always produce the same outputs (determinism),
* and a required check with **no** matching evidence counts as **failed**
  (never silently passing).

Beyond those, the suite also locks in the provenance contract (Phase 8): every
dimension is backed by :class:`ScoreEvidence` so a consumer can answer "why is
``A = 0.75``?", optional checks are advisory only, duplicate evidence is
deduplicated conservatively, and unmeasured telemetry for a *declared* budget
dimension is conservatively saturated.

All mappings are v0.3 reference heuristics — these tests assert the *documented*
behaviour of those heuristics, not scientific calibration.
"""

from __future__ import annotations

import pytest

from bound.contract_evaluator import ContractEvaluator
from bound.contracts import AcceptanceCheck, RiskCheck, StepBudget, StepContract
from bound.evidence import CheckEvidence, ExecutionEvidence
from bound.models import EvaluationScores, ScoreEvidence

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
        """Influence defaults to ``0.0`` with an honesty note.

        Intent: pin that v0.3 does not invent downstream influence from
        contract evidence — it is honestly ``0.0`` unless supplied externally.
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

    def test_external_influence_override(self) -> None:
        """An externally-supplied influence override is honoured verbatim.

        Intent: confirm the optional constructor seam mirrors
        :class:`~bound.workflow.CodingWorkflowEvaluator`, letting a caller inject
        downstream influence the contract cannot derive itself.
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
        assert evaluator.provenance["influence"][0].source == "external"

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

