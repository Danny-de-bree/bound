"""BOUND evaluation contracts (v0.3 Phases 1-4).

This module defines the machine-readable evaluation contracts that describe
what success means *before* an agent executes a step, plus the
provider-agnostic :class:`ContractGenerator` seam that turns a natural-language
goal + plan into a validated :class:`BoundPlan`.

The v0.3 pipeline becomes:

    Natural-language goal + plan
                │
                ▼
        ContractGenerator
                │
                ▼
           BoundPlan
                │
                ▼
         StepContract
                │
                ▼
        Agent execution
                │
                ▼
       EvidenceCollector
                │
                ▼
      ContractEvaluator
                │
                ▼
          A / I / R / C
                │
                ▼
            BOUND

The key v0.3 principle is: *use an LLM to translate intent into an explicit
evaluation contract, not to make the final BOUND decision.* The final policy
remains deterministic.

LLM / provider boundary (Phase 4)
---------------------------------
LLM-backed contract generators are **optional** and must live **outside** the
deterministic core — preferably in a separate adapter module or behind an
optional dependency group (e.g. ``pip install bound[llm]``). An LLM SDK must
**never** be a mandatory installation dependency of the ``bound`` package, and
this module imports none (and performs no network access).

When an LLM adapter is supplied, its job is to emit *structured data only*:

* what success looks like (:class:`AcceptanceCheck`),
* what risks matter (:class:`RiskCheck`),
* what artifacts are expected (``expected_artifacts``),
* what execution budgets apply (:class:`StepBudget`).

It must **not** return BOUND decisions (ACCEPT / RETRY / REPLAN / ROLLBACK) and
must **not** assign final ``A / I / R / C`` scores — those remain the exclusive
responsibility of the deterministic :class:`~bound.evaluator.Evaluator` and
:class:`~bound.policy.BoundPolicy`. Whatever an LLM emits must round-trip
through Pydantic validation before BOUND can use it, so a malformed or
hallucinated contract is rejected rather than silently trusted. See
:mod:`bound.llm_adapters` for the documented (import-free) seam.

This module ships one concrete, dependency-free generator,
:class:`StaticContractGenerator`, which returns a plan supplied at construction
time. It exists so tests, examples, and the CLI can exercise the full contract
pipeline without any network access, API key, or LLM SDK.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AcceptanceCheck(BaseModel):
    """One expected outcome a step must satisfy.

    Acceptance checks are the definition of "done" for a step: measurable,
    observable conditions that evidence can later confirm or refute. They carry
    no executable code — only an identifier and a human-readable description —
    so a contract never smuggles arbitrary Python into the deterministic core.

    Attributes:
        id: Stable identifier used to correlate collected evidence with this
            check (e.g. ``existing_tests_pass``).
        description: Human-readable statement of the expected outcome.
        required: Whether failing this check fails the step outright. Defaults
            to ``True``; set ``False`` for soft / advisory checks.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    description: str
    required: bool = True


class RiskCheck(BaseModel):
    """A condition whose violation is a risk signal for the step.

    Risk checks describe what *must not* happen (or what would be alarming if
    it did), weighted by a severity in ``[0, 1]``. Like acceptance checks they
    are declarative descriptions, never executable code.

    Attributes:
        id: Stable identifier used to correlate collected evidence with this
            check (e.g. ``no_plaintext_secrets``).
        description: Human-readable statement of the risk being guarded
            against.
        severity: How seriously a violation should weigh, in ``[0.0, 1.0]``.
            ``1.0`` is a hard safety boundary; lower values are softer signals.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    description: str
    severity: float = Field(ge=0.0, le=1.0)


class StepBudget(BaseModel):
    """Explicit execution budgets for a single step.

    Every field is optional. A field that is ``None`` means *no explicit budget
    was defined for that dimension* — it is **not** a zero budget. This
    distinction matters: "unlimited / unspecified" must not be confused with
    "forbidden", so consumers treat ``None`` as "do not enforce" rather than
    "enforce a limit of zero".

    Attributes:
        max_retries: Maximum number of retries permitted for the step, or
            ``None`` to leave retries unbounded.
        max_tool_calls: Maximum number of tool calls permitted, or ``None`` to
            leave them unbounded.
        max_tokens: Maximum token consumption permitted, or ``None`` to leave
            it unbounded.
        max_runtime_seconds: Maximum wall-clock runtime in seconds, or ``None``
            to leave it unbounded.
    """

    model_config = ConfigDict(extra="forbid")

    max_retries: int | None = Field(default=None, ge=0)
    max_tool_calls: int | None = Field(default=None, ge=0)
    max_tokens: int | None = Field(default=None, ge=0)
    max_runtime_seconds: float | None = Field(default=None, ge=0.0)


class StepContract(BaseModel):
    """The evaluation contract for a single step in a :class:`BoundPlan`.

    A step contract captures, up front, what success and risk look like for one
    unit of agent work. It is the artefact the deterministic
    :class:`~bound.evaluator.Evaluator` and downstream policy evaluate against
    once evidence is collected.

    Attributes:
        id: Stable identifier for the step within the plan.
        description: Human-readable summary of what the step does.
        goal: The specific goal this step advances (a refinement of the plan
            goal).
        acceptance_checks: At least one :class:`AcceptanceCheck`. A contract
            with no definition of success is invalid and is rejected at
            validation time.
        risk_checks: :class:`RiskCheck` conditions to guard against. Defaults
            to an empty list.
        expected_artifacts: Identifiers of artefacts the step is expected to
            produce, so missing output can be detected. Defaults to an empty
            list.
        budget: Optional :class:`StepBudget` constraining retries, tool calls,
            tokens, and runtime. ``None`` means no explicit budget.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    description: str
    goal: str
    acceptance_checks: list[AcceptanceCheck]
    risk_checks: list[RiskCheck] = []
    expected_artifacts: list[str] = []
    budget: StepBudget | None = None

    @model_validator(mode="after")
    def _require_acceptance_checks(self) -> StepContract:
        """Reject a contract that defines no acceptance checks.

        A step with no definition of success cannot be meaningfully evaluated,
        so an empty ``acceptance_checks`` list is a validation error rather
        than a silently permissive contract.

        Returns:
            This :class:`StepContract` once validation passes.

        Raises:
            ValueError: If ``acceptance_checks`` is empty.
        """
        if not self.acceptance_checks:
            raise ValueError(
                "a StepContract must define at least one acceptance check",
            )
        return self


class BoundPlan(BaseModel):
    """A compiled plan: a goal plus an ordered list of step contracts.

    A :class:`BoundPlan` is the validated output of a
    :class:`ContractGenerator`. It is the structured, machine-readable form of a
    natural-language plan: the top-level goal and one or more
    :class:`StepContract` entries that an agent executes in order.

    Attributes:
        goal: The top-level goal the entire plan advances.
        steps: One or more :class:`StepContract` entries. A plan with no steps
            is invalid and is rejected at validation time.
    """

    model_config = ConfigDict(extra="forbid")

    goal: str
    steps: list[StepContract]

    @model_validator(mode="after")
    def _require_steps(self) -> BoundPlan:
        """Reject a plan that contains no steps.

        A plan with no steps has nothing to evaluate, so an empty ``steps``
        list is a validation error rather than a silently trivial plan.

        Returns:
            This :class:`BoundPlan` once validation passes.

        Raises:
            ValueError: If ``steps`` is empty.
        """
        if not self.steps:
            raise ValueError("a BoundPlan must define at least one step")
        return self


@runtime_checkable
class ContractGenerator(Protocol):
    """Pluggable interface that compiles a goal + plan into a :class:`BoundPlan`.

    A contract generator's sole job is to turn a natural-language goal and plan
    (plus optional context) into a *validated* :class:`BoundPlan`. It must
    **not** apply any BOUND decision logic, threshold, or score — those belong
    to the deterministic :class:`~bound.evaluator.Evaluator` and
    :class:`~bound.policy.BoundPolicy`. This separation keeps the BOUND core
    reproducible regardless of which generator backs it.

    Concrete implementations may be deterministic (e.g.
    :class:`StaticContractGenerator` or a rule-based compiler) or probabilistic
    (e.g. an LLM-backed adapter, which lives **outside** the deterministic
    core as an optional dependency). Either way, once the :class:`BoundPlan` is
    produced and validated, every downstream calculation is fully deterministic.
    """

    def generate(
        self,
        *,
        goal: str,
        plan: str,
        context: str | None = None,
    ) -> BoundPlan:
        """Compile ``goal`` and ``plan`` into a validated :class:`BoundPlan`.

        Args:
            goal: The natural-language top-level goal of the plan.
            plan: The natural-language plan text (e.g. a sequence of steps).
            context: Optional additional context influencing contract
                generation. Defaults to ``None``.

        Returns:
            A :class:`BoundPlan` that has passed Pydantic validation.
            Implementations must not return a BOUND decision or A/I/R/C scores.
        """
        ...


class StaticContractGenerator:
    """Deterministic generator returning a pre-supplied :class:`BoundPlan`.

    The :class:`StaticContractGenerator` holds a fixed :class:`BoundPlan`
    instance and returns it on every call to :meth:`generate`, ignoring the
    natural-language ``goal`` / ``plan`` / ``context`` arguments. It performs no
    computation, no network access, and imports no LLM SDK. Its purpose is to
    let tests, examples, and the CLI drive the contract pipeline end-to-end
    with fully known, reproducible inputs — no API key or provider required.

    Example:
        >>> from bound.contracts import (
        ...     AcceptanceCheck, BoundPlan, StaticContractGenerator, StepContract,
        ... )
        >>> step = StepContract(
        ...     id="write-tests",
        ...     description="Add unit tests for the parser",
        ...     goal="Cover the parser edge cases",
        ...     acceptance_checks=[
        ...         AcceptanceCheck(id="tests-pass", description="All tests pass"),
        ...     ],
        ... )
        >>> plan = BoundPlan(goal="Ship the parser", steps=[step])
        >>> generator = StaticContractGenerator(plan)
        >>> generator.generate(goal="Ship the parser", plan="1. write tests") is plan
        True

    Attributes:
        plan: The :class:`BoundPlan` returned for every generation call.
    """

    def __init__(self, plan: BoundPlan) -> None:
        """Store the plan to return later.

        Args:
            plan: The :class:`BoundPlan` returned by every call to
                :meth:`generate`. Stored by reference so the exact object is
                reused, preserving determinism and identity.
        """
        self._plan = plan

    @property
    def plan(self) -> BoundPlan:
        """The fixed :class:`BoundPlan` this generator returns."""
        return self._plan

    def generate(
        self,
        *,
        goal: str,
        plan: str,
        context: str | None = None,
    ) -> BoundPlan:
        """Return the stored :class:`BoundPlan`, ignoring the supplied text.

        The natural-language ``goal``, ``plan`` and ``context`` arguments are
        accepted to satisfy the :class:`ContractGenerator` Protocol but are not
        used: a static generator is a fixed, deterministic source of contracts.
        This never produces a decision; that is the policy's responsibility.

        Args:
            goal: The natural-language top-level goal. Unused.
            plan: The natural-language plan text. Unused.
            context: Optional additional context. Unused.

        Returns:
            The :class:`BoundPlan` supplied at construction time, by identity.
        """
        return self._plan
