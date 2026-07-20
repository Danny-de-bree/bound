from __future__ import annotations

import pytest
from pydantic import ValidationError

from bound.contracts import (
    AcceptanceCheck,
    BoundPlan,
    ContractGenerator,
    EvidencePolicyAction,
    RiskCheck,
    StaticContractGenerator,
    StepBudget,
    StepContract,
)
from bound.evidence import EvidenceProvenance

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_ACCEPT = AcceptanceCheck(id="tests_pass", description="All unit tests pass")
_RISK = RiskCheck(
    id="no_secrets",
    description="No plaintext secrets introduced",
    severity=0.8,
)


def _make_step(step_id: str = "write-tests") -> StepContract:
    """Build a minimal but valid :class:`StepContract` for tests.

    Args:
        step_id: Identifier for the constructed step.

    Returns:
        A :class:`StepContract` with one acceptance check, one risk check, an
        expected artifact, and an explicit budget.
    """
    return StepContract(
        id=step_id,
        description="Add unit tests for the parser",
        goal="Cover the parser edge cases",
        acceptance_checks=[_ACCEPT],
        risk_checks=[_RISK],
        expected_artifacts=["tests/test_parser.py"],
        budget=StepBudget(
            max_retries=2,
            max_tool_calls=10,
            max_tokens=4096,
            max_runtime_seconds=60.0,
        ),
    )


def _make_plan() -> BoundPlan:
    """Build a valid :class:`BoundPlan` with two distinct steps.

    Returns:
        A :class:`BoundPlan` carrying two :class:`StepContract` entries, so the
        "at least one step" invariant is satisfied with margin.
    """
    return BoundPlan(
        goal="Ship the parser",
        steps=[_make_step("write-tests"), _make_step("fix-bugs")],
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_valid_plan_with_multiple_steps_validates() -> None:
    """A well-formed multi-step plan round-trips through validation.

    The baseline contract: two steps, each with an acceptance check, a risk
    check, an expected artifact, and a budget, must validate and preserve every
    field. Downstream phases build on this shape, so it must be stable.
    """
    plan = _make_plan()

    assert plan.goal == "Ship the parser"
    assert len(plan.steps) == 2
    step = plan.steps[0]
    assert step.acceptance_checks[0] == _ACCEPT
    assert step.risk_checks[0] == _RISK
    assert step.expected_artifacts == ["tests/test_parser.py"]
    assert step.budget is not None
    assert step.budget.max_retries == 2
    assert step.budget.max_runtime_seconds == 60.0


def test_step_defaults_to_empty_lists_and_no_budget() -> None:
    """A step needs only id/description/goal/acceptance_checks.

    Confirms the optional fields default sensibly: no risk checks, no expected
    artifacts, and no budget (``None``), so callers are not forced to populate
    every field.
    """
    step = StepContract(
        id="solo",
        description="A single step",
        goal="Get it done",
        acceptance_checks=[AcceptanceCheck(id="ok", description="Done")],
    )

    assert step.risk_checks == []
    assert step.expected_artifacts == []
    assert step.budget is None


# ---------------------------------------------------------------------------
# Required-content invariants
# ---------------------------------------------------------------------------


def test_empty_plan_rejected() -> None:
    """A plan with no steps is rejected with a ValidationError.

    A plan with nothing to evaluate is structurally invalid; the model_validator
    must surface this at construction time rather than letting an empty plan
    flow into execution and evaluation.
    """
    with pytest.raises(ValidationError):
        BoundPlan(goal="Ship the parser", steps=[])


def test_step_without_acceptance_checks_rejected() -> None:
    """A step with no acceptance checks is rejected with a ValidationError.

    A contract without any definition of success cannot be evaluated, so an
    empty ``acceptance_checks`` list must fail validation — this is the core
    v0.3 invariant that makes a contract meaningful.
    """
    with pytest.raises(ValidationError):
        StepContract(
            id="no-success",
            description="A step with no success criteria",
            goal="Undefined",
            acceptance_checks=[],
        )


# ---------------------------------------------------------------------------
# Range validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("severity", [-0.1, 1.1])
def test_invalid_risk_severity_rejected(severity: float) -> None:
    """Risk severity must lie in [0.0, 1.0].

    Severity drives the risk dimension and can gate a ROLLBACK, so out-of-range
    values must be rejected rather than clamped silently. Both bounds are
    checked: below 0.0 and above 1.0.
    """
    with pytest.raises(ValidationError):
        RiskCheck(id="bad", description="Invalid severity", severity=severity)


@pytest.mark.parametrize("severity", [0.0, 1.0])
def test_boundary_risk_severities_accepted(severity: float) -> None:
    """The exact severity bounds 0.0 and 1.0 are valid.

    Boundary-inclusivity matters: 0.0 is a no-op risk signal and 1.0 is a hard
    safety boundary, and both must be expressible in a contract.
    """
    check = RiskCheck(id="edge", description="Boundary severity", severity=severity)

    assert check.severity == severity


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_retries", -1),
        ("max_tool_calls", -1),
        ("max_tokens", -1),
        ("max_runtime_seconds", -0.5),
    ],
)
def test_invalid_budgets_rejected(field: str, value: float) -> None:
    """Negative budgets are rejected for every budget dimension.

    Budgets cap resource use, so a negative cap is nonsensical and must fail
    validation. Each dimension is covered so a regression in one field's
    constraint cannot slip through.
    """
    with pytest.raises(ValidationError):
        StepBudget(**{field: value})


def test_absent_budget_means_no_budget_not_zero() -> None:
    """An unset budget field is ``None`` (no budget), not zero.

    This is the critical "absence != zero" semantic: ``None`` means "do not
    enforce a limit", whereas ``0`` would mean "forbid everything". The model
    must preserve that distinction so consumers can tell unspecified from
    capped.
    """
    budget = StepBudget()

    assert budget.max_retries is None
    assert budget.max_tool_calls is None
    assert budget.max_tokens is None
    assert budget.max_runtime_seconds is None


def test_extra_fields_rejected() -> None:
    """Contract models reject unknown fields (extra='forbid').

    A hallucinated or typo'd field from an LLM adapter must surface loudly
    rather than being silently dropped, so the forbid config is a real safety
    guarantee worth pinning.
    """
    with pytest.raises(ValidationError):
        AcceptanceCheck(id="x", description="y", surprise=True)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# ContractGenerator Protocol / structural typing
# ---------------------------------------------------------------------------


def test_static_contract_generator_satisfies_protocol() -> None:
    """StaticContractGenerator structurally satisfies ContractGenerator.

    Guards the replaceability invariant: the core relies on isinstance-style
    checks (runtime_checkable) and must accept the shipped static generator
    without subclassing.
    """
    generator = StaticContractGenerator(_make_plan())

    assert isinstance(generator, ContractGenerator)


def test_duck_typed_generator_satisfies_protocol() -> None:
    """A bare class with a ``generate`` method satisfies the Protocol.

    Ensures future provider adapters can implement the seam without importing
    BOUND's concrete base — keeping the core free of provider dependencies.
    """
    stored_plan = _make_plan()

    class _AdHoc:
        def generate(
            self,
            *,
            goal: str,
            plan: str,
            context: str | None = None,
        ) -> BoundPlan:  # noqa: ARG002
            return stored_plan

    assert isinstance(_AdHoc(), ContractGenerator)


# ---------------------------------------------------------------------------
# StaticContractGenerator behaviour
# ---------------------------------------------------------------------------


def test_static_contract_generator_returns_supplied_plan_by_identity() -> None:
    """generate() returns the exact plan object supplied at construction.

    Identity matters: the BOUND core must be reproducible, so the generator
    should not clone or rebuild the plan. Returning the same object also lets
    tests assert exact propagation through the pipeline.
    """
    plan = _make_plan()
    generator = StaticContractGenerator(plan)

    result = generator.generate(goal="Ship the parser", plan="1. write tests 2. fix bugs")

    assert result is plan


def test_static_contract_generator_ignores_arguments() -> None:
    """The static generator returns its plan regardless of the text supplied.

    A static generator is a fixed source of contracts; varying the natural-
    language goal/plan/context must not change the output, which keeps it a
    deterministic test fixture.
    """
    plan = _make_plan()
    generator = StaticContractGenerator(plan)

    first = generator.generate(goal="A", plan="B")
    second = generator.generate(goal="C", plan="D", context="E")

    assert first is plan
    assert second is plan


def test_static_contract_generator_exposes_plan_property() -> None:
    """The ``plan`` property exposes the stored BoundPlan.

    Lets tests and examples introspect what a generator will return without
    invoking ``generate``.
    """
    plan = _make_plan()
    generator = StaticContractGenerator(plan)

    assert generator.plan is plan


# ---------------------------------------------------------------------------
# v0.7 provenance-aware contracts (item 4)
# ---------------------------------------------------------------------------


def test_acceptance_check_provenance_fields_default() -> None:
    """Provenance fields default to "accept any" + RETRY, keeping legacy green.

    An old-style acceptance check (id/description only) must still validate, with
    ``accepted_provenance=None`` (accept any) and ``on_missing``/``on_claimed``
    defaulting to RETRY — the conservative-but-not-fatal response.
    """
    check = AcceptanceCheck(id="tests-pass", description="All tests pass")
    assert check.accepted_provenance is None
    assert check.on_missing is EvidencePolicyAction.RETRY
    assert check.on_claimed is EvidencePolicyAction.RETRY


def test_acceptance_check_accepts_provenance_allowlist() -> None:
    """A check may restrict itself to a set of accepted provenances."""
    check = AcceptanceCheck(
        id="tests-pass",
        description="All tests pass",
        accepted_provenance=[
            EvidenceProvenance.OBSERVED,
            EvidenceProvenance.VERIFIED,
            EvidenceProvenance.ATTESTED,
        ],
        on_missing=EvidencePolicyAction.REPLAN,
        on_claimed=EvidencePolicyAction.ROLLBACK,
    )
    assert check.accepted_provenance == [
        EvidenceProvenance.OBSERVED,
        EvidenceProvenance.VERIFIED,
        EvidenceProvenance.ATTESTED,
    ]
    assert check.on_missing is EvidencePolicyAction.REPLAN
    assert check.on_claimed is EvidencePolicyAction.ROLLBACK


def test_acceptance_check_rejects_empty_accepted_provenance() -> None:
    """An empty allow-list rejects all evidence — almost certainly a mistake."""
    with pytest.raises(ValidationError):
        AcceptanceCheck(
            id="x", description="y", accepted_provenance=[]
        )


def test_acceptance_check_coerces_string_provenance_and_action() -> None:
    """Provenance/actions coerce from strings (deserialised JSON contracts)."""
    check = AcceptanceCheck.model_validate(
        {
            "id": "tests-pass",
            "description": "All tests pass",
            "accepted_provenance": ["observed", "verified"],
            "on_missing": "rollback",
            "on_claimed": "retry",
        }
    )
    assert check.accepted_provenance == [
        EvidenceProvenance.OBSERVED,
        EvidenceProvenance.VERIFIED,
    ]
    assert check.on_missing is EvidencePolicyAction.ROLLBACK
    assert check.on_claimed is EvidencePolicyAction.RETRY


def test_risk_check_decision_critical_defaults_false() -> None:
    """Risk checks are not decision-critical by default."""
    risk = RiskCheck(id="no-secrets", description="No secrets", severity=0.5)
    assert risk.decision_critical is False
    assert risk.accepted_provenance is None
    assert risk.on_missing is EvidencePolicyAction.RETRY
    assert risk.on_claimed is EvidencePolicyAction.RETRY


def test_acceptance_check_importance_weight_minimum_assurance_defaults() -> None:
    """New v0.7 fields default sensibly on AcceptanceCheck (backwards-compat).

    ``importance`` defaults to ``"medium"``, ``weight`` and ``minimum_assurance``
    default to ``None`` so legacy contracts keep validating unchanged.
    """
    check = AcceptanceCheck(id="tests-pass", description="All tests pass")
    assert check.importance == "medium"
    assert check.weight is None
    assert check.minimum_assurance is None


def test_acceptance_check_importance_can_be_blocker_and_validated() -> None:
    """``importance`` accepts the documented tiers and rejects others."""
    for tier in ("blocker", "high", "medium", "low", "ignore"):
        AcceptanceCheck(id="x", description="y", importance=tier)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        AcceptanceCheck(id="x", description="y", importance="urgent")  # type: ignore[arg-type]


def test_acceptance_check_weight_must_be_non_negative() -> None:
    """An explicit ``weight`` must be ``>= 0.0``."""
    with pytest.raises(ValidationError):
        AcceptanceCheck(id="x", description="y", weight=-1.0)
    ok = AcceptanceCheck(id="x", description="y", weight=2.5)
    assert ok.weight == 2.5


def test_acceptance_check_minimum_assurance_coerces_from_string() -> None:
    """``minimum_assurance`` coerces from the string value of DecisionAssurance."""
    from bound.models import DecisionAssurance

    check = AcceptanceCheck.model_validate(
        {"id": "x", "description": "y", "minimum_assurance": "verified"}
    )
    assert check.minimum_assurance is DecisionAssurance.VERIFIED


def test_risk_check_carries_new_importance_weight_assurance_fields() -> None:
    """RiskCheck accepts the same new fields as AcceptanceCheck."""
    from bound.models import DecisionAssurance

    risk = RiskCheck(
        id="no-secrets",
        description="No secrets",
        severity=0.5,
        importance="blocker",
        weight=0.0,
        minimum_assurance=DecisionAssurance.VERIFIED,
    )
    assert risk.importance == "blocker"
    assert risk.weight == 0.0
    assert risk.minimum_assurance is DecisionAssurance.VERIFIED


def test_risk_check_decision_critical_can_be_set() -> None:
    """A decision-critical risk check gates ACCEPT on verified evidence."""
    risk = RiskCheck(
        id="no-critical-security-findings",
        description="No critical security findings",
        severity=1.0,
        decision_critical=True,
        accepted_provenance=[EvidenceProvenance.VERIFIED, EvidenceProvenance.ATTESTED],
        on_missing=EvidencePolicyAction.ROLLBACK,
        on_claimed=EvidencePolicyAction.ROLLBACK,
    )
    assert risk.decision_critical is True
    assert risk.accepted_provenance == [
        EvidenceProvenance.VERIFIED,
        EvidenceProvenance.ATTESTED,
    ]
    assert risk.on_missing is EvidencePolicyAction.ROLLBACK


def test_risk_check_rejects_empty_accepted_provenance() -> None:
    """An empty allow-list rejects all evidence — almost certainly a mistake."""
    with pytest.raises(ValidationError):
        RiskCheck(id="x", description="y", severity=0.5, accepted_provenance=[])


def test_evidence_policy_action_enum_values() -> None:
    """The action vocabulary mirrors the BOUND decision space (lowercased)."""
    assert {a.value for a in EvidencePolicyAction} == {
        "accept",
        "retry",
        "replan",
        "rollback",
    }


def test_legacy_plan_with_new_provenance_fields_still_validates() -> None:
    """A plan whose checks omit the new v0.7 fields still validates (backwards-compat).

    Old contracts (no ``accepted_provenance``/``on_missing``/``on_claimed``/
    ``decision_critical``) must load unchanged; the new fields default sensibly
    rather than becoming required.
    """
    plan = _make_plan()
    assert plan.steps[0].acceptance_checks[0].accepted_provenance is None
    assert plan.steps[0].risk_checks[0].decision_critical is False
