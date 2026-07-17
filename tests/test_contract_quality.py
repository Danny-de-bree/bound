from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from bound.contract_quality import (
    ContractQualityReport,
    assess_contract,
    assess_step,
    load_contract_corpus,
    run_contract_quality_experiment,
    summarize_contract_quality_experiment,
)
from bound.contracts import (
    AcceptanceCheck,
    BoundPlan,
    RiskCheck,
    StepBudget,
    StepContract,
)

# ---------------------------------------------------------------------------
# Fixture builders (self-contained, mirror the benchmark corpus intent)
# ---------------------------------------------------------------------------


def _ac(check_id: str, description: str, *, required: bool = True) -> AcceptanceCheck:
    """Build an :class:`AcceptanceCheck` concisely."""
    return AcceptanceCheck(id=check_id, description=description, required=required)


def _step(
    step_id: str,
    checks: list[AcceptanceCheck],
    *,
    risks: list[RiskCheck] | None = None,
    budget: StepBudget | None = None,
) -> StepContract:
    """Build a minimal but valid :class:`StepContract`."""
    return StepContract(
        id=step_id,
        description=f"Step {step_id}",
        goal=step_id,
        acceptance_checks=checks,
        risk_checks=risks or [],
        expected_artifacts=[],
        budget=budget,
    )


def _good_plan() -> BoundPlan:
    """A high-quality plan: measurable ids, real descriptions, budget, risks."""
    return BoundPlan(
        goal="Ship a JSON parser library",
        steps=[
            _step(
                "write-parser",
                [
                    _ac("parser_returns_ast", "parse() returns an AST node tree for valid JSON"),
                    _ac("parser_raises_on_invalid", "parse() raises ParseError for malformed JSON"),
                    _ac("parser_handles_empty", "parse('{}') returns an empty-object AST node"),
                ],
                risks=[
                    RiskCheck(
                        id="no_secrets",
                        description="No plaintext secrets in source",
                        severity=0.9,
                    ),
                ],
                budget=StepBudget(
                    max_retries=2,
                    max_tool_calls=20,
                    max_tokens=8000,
                    max_runtime_seconds=120.0,
                ),
            )
        ],
    )


def _vague_plan() -> BoundPlan:
    """A vague plan: short/generic descriptions that read as non-measurable."""
    return BoundPlan(
        goal="Improve the dashboard",
        steps=[_step("tweak-ui", [_ac("works", "works"), _ac("ok", "ok"), _ac("fine", "fine")])],
    )


def _duplicate_id_plan() -> BoundPlan:
    """A plan whose single step repeats one acceptance-check id."""
    return BoundPlan(
        goal="Add a caching layer",
        steps=[
            _step(
                "add-cache",
                [
                    _ac("cache_returns_value", "cache.get returns the stored value"),
                    _ac(
                        "cache_returns_value",
                        "cache.get returns the stored value on a second call",
                    ),
                ],
                risks=[
                    RiskCheck(
                        id="no_corruption",
                        description="Caching must not corrupt data",
                        severity=0.8,
                    ),
                ],
                budget=StepBudget(
                    max_retries=2,
                    max_tool_calls=15,
                    max_tokens=6000,
                    max_runtime_seconds=90.0,
                ),
            )
        ],
    )


def _oversized_plan() -> BoundPlan:
    """A plan with 16 acceptance checks (> 15 threshold)."""
    return BoundPlan(
        goal="Build the integration suite",
        steps=[
            _step(
                "integration",
                [
                    _ac(f"endpoint_{n}_returns_200", f"GET /r{n} returns HTTP 200")
                    for n in range(1, 17)
                ],
                budget=StepBudget(
                    max_retries=1,
                    max_tool_calls=50,
                    max_tokens=20000,
                    max_runtime_seconds=300.0,
                ),
            )
        ],
    )


def _multi_step_plan() -> BoundPlan:
    """A three-step plan of varying quality for aggregation checks."""
    return BoundPlan(
        goal="Migrate the database schema",
        steps=[
            _step(
                "plan-migration",
                [
                    _ac(
                        "migration_plan_written",
                        "The migration plan is written to docs/migration.md",
                    ),
                    _ac(
                        "downtime_estimate_exists",
                        "A downtime estimate exists in the migration plan",
                    ),
                ],
                budget=StepBudget(
                    max_retries=1,
                    max_tool_calls=5,
                    max_tokens=2000,
                    max_runtime_seconds=30.0,
                ),
            ),
            _step("run-migration", [_ac("works", "ok"), _ac("done", "done")]),
            _step(
                "verify-migration",
                [
                    _ac("schema_matches_target", "The live schema matches the target schema"),
                    _ac("good", "looks ok"),
                ],
            ),
        ],
    )


def _corpus_dir() -> Path:
    """Resolve the shipped contract corpus directory from the repo root."""
    return Path(__file__).resolve().parents[1] / "benchmarks" / "contracts"


# ---------------------------------------------------------------------------
# ContractQualityReport model
# ---------------------------------------------------------------------------


def test_contract_quality_report_construction() -> None:
    """A report round-trips and forbids extra fields / out-of-range values.

    ``extra='forbid'`` and the ``[0, 1]`` ratio / non-negative count guards are
    real safety properties: a hallucinated extra field or a NaN-ish ratio must
    surface loudly rather than being silently accepted.
    """
    report = ContractQualityReport(
        measurable_ratio=0.75,
        acceptance_check_count=4,
        risk_check_count=2,
        has_budget=True,
        warnings=["step 'x' has duplicate acceptance check id(s): ['a']"],
    )
    assert report.measurable_ratio == 0.75
    assert report.acceptance_check_count == 4
    assert report.risk_check_count == 2
    assert report.has_budget is True
    assert report.warnings == ["step 'x' has duplicate acceptance check id(s): ['a']"]

    # extra fields rejected (extra='forbid').
    with pytest.raises(ValidationError):
        ContractQualityReport(
            measurable_ratio=0.5,
            acceptance_check_count=1,
            risk_check_count=0,
            has_budget=False,
            warnings=[],
            surprise=True,  # type: ignore[call-arg]
        )

    # ratio must stay in [0, 1].
    with pytest.raises(ValidationError):
        ContractQualityReport(
            measurable_ratio=1.5,
            acceptance_check_count=1,
            risk_check_count=0,
            has_budget=False,
        )
    with pytest.raises(ValidationError):
        ContractQualityReport(
            measurable_ratio=-0.1,
            acceptance_check_count=1,
            risk_check_count=0,
            has_budget=False,
        )
    # negative counts rejected.
    with pytest.raises(ValidationError):
        ContractQualityReport(
            measurable_ratio=0.0,
            acceptance_check_count=-1,
            risk_check_count=0,
            has_budget=False,
        )


# ---------------------------------------------------------------------------
# assess_contract — behaviour
# ---------------------------------------------------------------------------


def test_assess_contract_good_plan_is_clean() -> None:
    """A well-formed plan yields a high measurable ratio and no warnings.

    This is the happy path the rest of v0.3 builds on: measurable ids, real
    descriptions, a budget and a risk check should produce ratio 1.0 with an
    empty warning list.
    """
    report = assess_contract(_good_plan())

    assert report.measurable_ratio == 1.0
    assert report.acceptance_check_count == 3
    assert report.risk_check_count == 1
    assert report.has_budget is True
    assert report.warnings == []


def test_assess_contract_vague_plan_fires_warnings() -> None:
    """Generic/short descriptions fire vague + no-observable-verification warnings.

    "works"/"ok"/"fine" describe nothing a deterministic collector could check,
    so the contract reads as non-measurable (ratio 0.0) and is flagged twice.
    """
    report = assess_contract(_vague_plan())

    assert report.measurable_ratio == 0.0
    assert report.acceptance_check_count == 3
    assert report.has_budget is False
    joined = " | ".join(report.warnings)
    assert "vague acceptance check(s)" in joined
    assert "no acceptance check whose id or description suggests an observable" in joined


def test_assess_contract_duplicate_id_fires_duplicate_warning() -> None:
    """A repeated check id is flagged, but the checks still read as measurable.

    Duplicate ids break evidence correlation, but the wording itself is fine, so
    the only warning is the duplicate one and the ratio stays 1.0.
    """
    report = assess_contract(_duplicate_id_plan())

    assert report.measurable_ratio == 1.0
    assert report.acceptance_check_count == 2
    dup_msg = "duplicate acceptance check id(s): ['cache_returns_value']"
    assert any(dup_msg in w for w in report.warnings)
    # no other warning types fire for this fixture.
    assert len(report.warnings) == 1


def test_assess_contract_oversized_plan_fires_large_warning() -> None:
    """More than 15 acceptance checks in a step is flagged as extremely large.

    Each individual check is measurable, so the ratio stays 1.0; the only signal
    is the size smell.
    """
    report = assess_contract(_oversized_plan())

    assert report.measurable_ratio == 1.0
    assert report.acceptance_check_count == 16
    assert any("is extremely large (16 acceptance checks > 15)" in w for w in report.warnings)
    assert len(report.warnings) == 1


def test_assess_contract_handles_no_acceptance_checks_defensively() -> None:
    """``assess_contract`` never raises on a step with zero acceptance checks.

    :class:`StepContract` rejects an empty ``acceptance_checks`` list at
    construction time, so such a plan can only arise by bypassing validation
    (``model_construct``). ``assess_contract`` must still report cleanly — a
    "no acceptance checks" warning and ratio 0.0 — rather than crash or
    silently pass.
    """
    malformed_step = StepContract.model_construct(
        id="empty",
        description="x",
        goal="y",
        acceptance_checks=[],
        risk_checks=[],
        expected_artifacts=[],
        budget=None,
    )
    malformed_plan = BoundPlan.model_construct(goal="g", steps=[malformed_step])

    report = assess_contract(malformed_plan)

    assert report.acceptance_check_count == 0
    assert report.measurable_ratio == 0.0
    assert any("defines no acceptance checks" in w for w in report.warnings)


def test_assess_contract_is_deterministic() -> None:
    """The same plan always yields the same report (same-input/same-output).

    Determinism is a v0.3 non-negotiable: the structural report must be a pure
    function of the contract, so two calls on an identical plan compare equal.
    """
    plan = _multi_step_plan()

    first = assess_contract(plan)
    second = assess_contract(plan)

    assert first == second


def test_assess_contract_aggregates_per_step_findings() -> None:
    """Plan-level warnings equal the per-step warnings concatenated in order.

    Confirms ``assess_contract`` is a faithful aggregation of ``assess_step``:
    counts sum, warnings concatenate (each prefixed with its step id), and the
    plan ratio is the corpus-style fraction over all checks.
    """
    plan = _multi_step_plan()

    report = assess_contract(plan)
    per_step = [assess_step(step) for step in plan.steps]

    assert report.acceptance_check_count == sum(s.acceptance_check_count for s in per_step)
    assert report.risk_check_count == sum(s.risk_check_count for s in per_step)
    assert report.warnings == [w for s in per_step for w in s.warnings]
    # multi_step has 3 measurable of 6 checks -> 0.5.
    assert report.measurable_ratio == pytest.approx(0.5)
    # warnings carry the originating step id so they are traceable.
    assert any("run-migration" in w for w in report.warnings)
    assert any("verify-migration" in w for w in report.warnings)


# ---------------------------------------------------------------------------
# assess_step — per-step variant
# ---------------------------------------------------------------------------


def test_assess_step_reports_single_step_signals() -> None:
    """``assess_step`` reports one step's ratio, budget and warnings in isolation.

    Lets an experiment drill into a single step; here a vague-only step shows
    ratio 0.0, no budget, and the vague + no-observable warnings.
    """
    report = assess_step(_step("only", [_ac("ok", "ok")]))

    assert report.acceptance_check_count == 1
    assert report.measurable_ratio == 0.0
    assert report.has_budget is False
    assert any("vague" in w for w in report.warnings)


# ---------------------------------------------------------------------------
# Phase 14 experiment over the shipped corpus
# ---------------------------------------------------------------------------


def test_load_contract_corpus_returns_validated_plans() -> None:
    """Every fixture round-trips through ``BoundPlan`` validation and is keyed by stem."""
    corpus = load_contract_corpus(_corpus_dir())

    assert len(corpus) >= 10
    assert all(isinstance(plan, BoundPlan) for plan in corpus.values())
    assert "good_plan" in corpus and "measurable_but_irrelevant_plan" in corpus


def test_experiment_runs_over_corpus() -> None:
    """``run_contract_quality_experiment`` assesses every corpus plan reproducibly.

    The aggregate signals must be sane (ratio in ``[0, 1]``, non-negative counts)
    and findings must be in sorted file-stem order so the experiment is stable.
    """
    summary = run_contract_quality_experiment(_corpus_dir())

    assert summary.total_plans >= 10
    assert 0.0 <= summary.aggregate_measurable_ratio <= 1.0
    assert summary.plans_with_warnings >= 1
    assert len(summary.honest_limitations) >= 1
    names = [f.name for f in summary.findings]
    assert names == sorted(names)


def test_experiment_measurable_but_irrelevant_is_the_blind_spot() -> None:
    """The irrelevant-but-measurable fixture exposes what structural checks miss.

    This is the honest centrepiece of Phase 14: a contract checking coffee-machine
    behaviour for a JSON-parser goal scores *perfectly* (ratio 1.0, no warnings),
    proving structural validation cannot judge relevance. The experiment must not
    hide this — the limitation is recorded explicitly.
    """
    summary = run_contract_quality_experiment(_corpus_dir())
    finding = next(f for f in summary.findings if f.name == "measurable_but_irrelevant_plan")

    assert finding.report.measurable_ratio == 1.0
    assert finding.report.warnings == []
    assert finding.goal == "Ship a JSON parser library"
    assert any("relevance" in lim.lower() for lim in summary.honest_limitations)


def test_experiment_spot_checks_corpus_fixtures() -> None:
    """Specific corpus fixtures produce their intended structural verdicts.

    Ties the shipped corpus to the behaviour: good=clean, vague=vague+no-
    observable, duplicate_id=duplicate, oversized=large, no_budget=has_budget False.
    """
    by_name = {f.name: f for f in run_contract_quality_experiment(_corpus_dir()).findings}

    assert by_name["good_plan"].report.warnings == []
    assert by_name["good_plan"].report.has_budget is True

    vague = by_name["vague_plan"].report
    assert any("vague" in w for w in vague.warnings)
    assert any("observable verification method" in w for w in vague.warnings)

    assert any("duplicate" in w for w in by_name["duplicate_id_plan"].report.warnings)
    assert any("extremely large" in w for w in by_name["oversized_plan"].report.warnings)
    assert by_name["no_budget_plan"].report.has_budget is False


def test_summarize_is_deterministic_and_honest() -> None:
    """``summarize_contract_quality_experiment`` is reproducible and surfaces the blind spot."""
    summary = run_contract_quality_experiment(_corpus_dir())

    text_a = summarize_contract_quality_experiment(summary)
    text_b = summarize_contract_quality_experiment(summary)

    assert text_a == text_b
    assert "measurable_but_irrelevant_plan" in text_a
    assert "honest limitations of structural validation" in text_a


def test_experiment_default_corpus_dir_matches_repo_corpus() -> None:
    """The default corpus dir resolves to this repo's ``benchmarks/contracts``."""
    from bound.contract_quality import _default_corpus_dir  # noqa: PLC0415

    default_summary = run_contract_quality_experiment()
    explicit_summary = run_contract_quality_experiment(_corpus_dir())

    assert _default_corpus_dir().resolve() == _corpus_dir().resolve()
    assert default_summary.total_plans == explicit_summary.total_plans
