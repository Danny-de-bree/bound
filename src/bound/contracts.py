from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bound.evidence import EvidenceProvenance
from bound.models import DecisionAssurance

#: Regular expression backing :func:`is_valid_phase_id`. Matches the documented
#: stable-ID convention ``PHASE-NNN`` with an optional nested sub-phase suffix
#: (``-A``, ``-B``, ...) and/or an optional replan suffix (``-R1``, ``-R2``),
#: in either order. It is intentionally permissive about the numeric width so
#: ``PHASE-001`` and ``PHASE-042`` are both accepted.
_PHASE_ID_RE = re.compile(
    r"^PHASE-\d+(?:-[A-Z])?(?:-R\d+)?$"
    r"|"
    r"^PHASE-\d+(?:-R\d+)?(?:-[A-Z])?$"
)


def is_valid_phase_id(phase_id: str) -> bool:
    """Return ``True`` when ``phase_id`` matches the BOUND stable-ID convention.

    BOUND v0.6 recommends that every meaningful planned step carry a stable,
    human-readable identifier that is preserved unchanged across the whole
    execution lineage::

        PLAN.md -> StepContract(id=...) -> ExecutionEvidence -> report

    so a reader can follow a single phase from intent to outcome. The
    recommended convention is::

        PHASE-001            base phase
        PHASE-002            another base phase
        PHASE-002-A          nested sub-phase of PHASE-002
        PHASE-002-B          a second nested sub-phase
        PHASE-001-R1         first replan of PHASE-001 (history preserved)
        PHASE-001-R2         second replan of PHASE-001

    This helper validates that shape. It is a *pure, optional* utility: it
    performs no network access, calls no LLM, and — crucially — is **not** wired
    into :class:`StepContract` validation. The contract ``id`` field stays a
    free-form ``str`` so non-``PHASE`` identifiers (e.g. ``write-tests``) remain
    legal; this function only lets integrations and tests *opt in* to the
    documented convention. Replans must append ``-R<N>`` rather than replacing
    the id, so the root identity (and its history) is never lost.

    Args:
        phase_id: The candidate identifier to validate.

    Returns:
        ``True`` if ``phase_id`` matches the ``PHASE-NNN[-R<n>][-<letter>]``
        convention (suffixes in either order); ``False`` otherwise (including
        for empty or non-string input rendered as a string).

    Example:
        >>> is_valid_phase_id("PHASE-001")
        True
        >>> is_valid_phase_id("PHASE-002-A")
        True
        >>> is_valid_phase_id("PHASE-001-R1")
        True
        >>> is_valid_phase_id("PHASE-001-R2-A")
        True
        >>> is_valid_phase_id("write-tests")
        False
    """
    return bool(_PHASE_ID_RE.match(phase_id))


class EvidencePolicyAction(StrEnum):
    """The policy action to take when evidence is missing or only claimed.

    Provenance-aware contracts (v0.7) let each check declare how the policy
    should react when no acceptable evidence is collected, or when the only
    evidence available is a bare agent self-report (CLAIMED). The values mirror
    the BOUND decision space so a contract can express, for example, that a
    decision-critical risk check must trigger a ROLLBACK when its evidence is
    missing rather than a soft RETRY.

    Members:
        ACCEPT: Treat the check as satisfied despite the weak/missing evidence.
            Rare and dangerous — use only for purely advisory checks.
        RETRY: Re-run the step/collector to obtain stronger evidence. The
            default, conservative-but-not-fatal response.
        REPLAN: The current plan cannot supply the evidence; produce a new plan.
        ROLLBACK: Undo the step's effects — used for decision-critical checks
            where missing/claimed evidence is unacceptable.
    """

    ACCEPT = "accept"
    RETRY = "retry"
    REPLAN = "replan"
    ROLLBACK = "rollback"


class AcceptanceCheck(BaseModel):
    """One expected outcome a step must satisfy.

    Acceptance checks are the definition of "done" for a step: measurable,
    observable conditions that evidence can later confirm or refute. They carry
    no executable code — only an identifier and a human-readable description —
    so a contract never smuggles arbitrary Python into the deterministic core.

    v0.7 provenance awareness: a check may declare which trust provenances it
    accepts (:attr:`accepted_provenance`) and how the policy should react when
    evidence is missing (:attr:`on_missing`) or only claimed
    (:attr:`on_claimed`). ``None`` for :attr:`accepted_provenance` means
    "accept any provenance"; a non-empty list restricts the check to the listed
    provenances (e.g. only OBSERVED/VERIFIED/ATTESTED), causing CLAIMED or
    MISSING evidence to be treated as not-acceptable.

    Attributes:
        id: Stable identifier used to correlate collected evidence with this
            check (e.g. ``existing_tests_pass``).
        description: Human-readable statement of the expected outcome.
        required: Whether failing this check fails the step outright. Defaults
            to ``True``; set ``False`` for soft / advisory checks.
        importance: How heavily this check weighs in the decision. ``"blocker"``
            marks a hard gate that cannot be compensated by positive scores;
            ``"high"``/``"medium"``/``"low"``/``"ignore"`` mark weighted signals
            (see :data:`bound.policy_schema.DEFAULT_WEIGHTS`). Defaults to
            ``"medium"``.
        weight: Optional explicit numeric weight override (``>= 0.0``). When
            ``None`` the effective weight is derived from :attr:`importance`.
        minimum_assurance: Optional minimum
            :class:`~bound.models.DecisionAssurance` this check's evidence must
            meet for a clean ``ACCEPT``; ``None`` means no per-check assurance
            floor.
        accepted_provenance: Optional allow-list of
            :class:`~bound.evidence.EvidenceProvenance` values this check will
            accept. ``None`` (default) accepts any provenance; a non-empty list
            rejects evidence whose provenance is not in the list (e.g. CLAIMED
            or MISSING).
        on_missing: Policy action when no evidence is collected for this check.
            Defaults to :attr:`RETRY <EvidencePolicyAction.RETRY>`.
        on_claimed: Policy action when the only available evidence is CLAIMED
            (agent self-report). Defaults to
            :attr:`RETRY <EvidencePolicyAction.RETRY>`.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    description: str
    required: bool = True
    importance: Literal["blocker", "high", "medium", "low", "ignore"] = "medium"
    weight: float | None = Field(default=None, ge=0.0)
    minimum_assurance: DecisionAssurance | None = None
    accepted_provenance: list[EvidenceProvenance] | None = None
    on_missing: EvidencePolicyAction = EvidencePolicyAction.RETRY
    on_claimed: EvidencePolicyAction = EvidencePolicyAction.RETRY

    @model_validator(mode="after")
    def _accepted_provenance_non_empty_if_set(self) -> AcceptanceCheck:
        """Reject an empty ``accepted_provenance`` list.

        An empty allow-list would reject *all* evidence, which is almost
        certainly a contract-authoring mistake. ``None`` (accept any) is the way
        to express "no restriction"; an empty list is not a meaningful policy.

        Returns:
            The validated check (unchanged on success).

        Raises:
            ValueError: If ``accepted_provenance`` is an empty list.
        """
        if self.accepted_provenance is not None and len(self.accepted_provenance) == 0:
            raise ValueError(
                "accepted_provenance must be None (accept any) or a non-empty "
                "list of EvidenceProvenance values."
            )
        return self


class RiskCheck(BaseModel):
    """A condition whose violation is a risk signal for the step.

    Risk checks describe what *must not* happen (or what would be alarming if
    it did), weighted by a severity in ``[0, 1]``. Like acceptance checks they
    are declarative descriptions, never executable code.

    v0.7 provenance awareness mirrors :class:`AcceptanceCheck`
    (:attr:`accepted_provenance`, :attr:`on_missing`, :attr:`on_claimed`) and
    adds :attr:`decision_critical`: when ``True``, a missing or only-claimed
    risk check must influence the decision assurance as INSUFFICIENT rather than
    being silently treated as a passed/low-risk signal. This is the contract
    hook the assurance layer uses to gate an ACCEPT on independently verified
    critical evidence.

    Attributes:
        id: Stable identifier used to correlate collected evidence with this
            check (e.g. ``no_plaintext_secrets``).
        description: Human-readable statement of the risk being guarded
            against.
        severity: How seriously a violation should weigh, in ``[0.0, 1.0]``.
            ``1.0`` is a hard safety boundary; lower values are softer signals.
        importance: How heavily this risk check weighs in the decision.
            ``"blocker"`` marks a hard gate that cannot be compensated by
            positive scores; the weighted tiers map to
            :data:`bound.policy_schema.DEFAULT_WEIGHTS`. Defaults to
            ``"medium"``.
        weight: Optional explicit numeric weight override (``>= 0.0``). When
            ``None`` the effective weight is derived from :attr:`importance`.
        minimum_assurance: Optional minimum
            :class:`~bound.models.DecisionAssurance` this check's evidence must
            meet; ``None`` means no per-check assurance floor.
        accepted_provenance: Optional allow-list of
            :class:`~bound.evidence.EvidenceProvenance` values this check will
            accept. ``None`` (default) accepts any provenance.
        on_missing: Policy action when no evidence is collected for this check.
            Defaults to :attr:`RETRY <EvidencePolicyAction.RETRY>`.
        on_claimed: Policy action when the only available evidence is CLAIMED.
            Defaults to :attr:`RETRY <EvidencePolicyAction.RETRY>`.
        decision_critical: When ``True``, this check's evidence is required to
            be independently verified for a VERIFIED decision assurance; missing
            or only-claimed evidence for a critical check forces INSUFFICIENT
            assurance (blocking a clean ACCEPT). Defaults to ``False``.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    description: str
    severity: float = Field(ge=0.0, le=1.0)
    importance: Literal["blocker", "high", "medium", "low", "ignore"] = "medium"
    weight: float | None = Field(default=None, ge=0.0)
    minimum_assurance: DecisionAssurance | None = None
    accepted_provenance: list[EvidenceProvenance] | None = None
    on_missing: EvidencePolicyAction = EvidencePolicyAction.RETRY
    on_claimed: EvidencePolicyAction = EvidencePolicyAction.RETRY
    decision_critical: bool = False

    @model_validator(mode="after")
    def _accepted_provenance_non_empty_if_set(self) -> RiskCheck:
        """Reject an empty ``accepted_provenance`` list.

        See :meth:`AcceptanceCheck._accepted_provenance_non_empty_if_set`:
        an empty allow-list rejects all evidence and is almost certainly a
        mistake; use ``None`` to express "accept any".

        Returns:
            The validated check (unchanged on success).

        Raises:
            ValueError: If ``accepted_provenance`` is an empty list.
        """
        if self.accepted_provenance is not None and len(self.accepted_provenance) == 0:
            raise ValueError(
                "accepted_provenance must be None (accept any) or a non-empty "
                "list of EvidenceProvenance values."
            )
        return self


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
        id: Stable identifier for the step within the plan. BOUND v0.6
            recommends (but does not require) a ``PHASE-NNN`` stable-ID
            convention so the same identifier is preserved unchanged across the
            whole execution lineage — ``PLAN.md -> StepContract(id=...) ->
            ExecutionEvidence -> INTEGRATION_REPORT.md`` — and a reader can
            follow one phase from intent to outcome. The documented forms are::

                PHASE-001          base phase
                PHASE-002          another base phase
                PHASE-002-A        nested sub-phase of PHASE-002
                PHASE-002-B        a second nested sub-phase
                PHASE-001-R1       first replan of PHASE-001 (history kept)
                PHASE-001-R2       second replan of PHASE-001

            Replans must append ``-R<N>`` to the existing id rather than replace
            it, so the root identity (and its history) is never lost. Do not
            silently replace a human-readable plan identity with an unrelated
            contract id. The field itself remains a free-form ``str``; the
            optional :func:`is_valid_phase_id` helper lets integrations and
            tests opt in to the convention without making it mandatory.
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
