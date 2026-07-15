"""Unit tests for the BOUND evaluation contracts (v0.3 Phases 1-4).

These tests pin the two load-bearing guarantees of the contract layer:

1. The Pydantic models enforce the structural invariants a contract must hold —
   a plan has at least one step, a step has at least one acceptance check, risk
   severities and budgets stay in range — so a malformed or hallucinated
   contract is rejected at validation time rather than silently trusted.
2. :class:`ContractGenerator` is a structural :class:`typing.Protocol` — any
   object with a ``generate(self, *, goal, plan, context=None) -> BoundPlan``
   method satisfies it, with no inheritance required. This is what lets future
   LLM-backed adapters slot in without touching the deterministic core, while
   :class:`StaticContractGenerator` keeps every unit test offline.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bound.contracts import (
    AcceptanceCheck,
    BoundPlan,
    ContractGenerator,
    RiskCheck,
    StaticContractGenerator,
    StepBudget,
    StepContract,
)

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
