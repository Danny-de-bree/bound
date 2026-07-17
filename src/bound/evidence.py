from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    # ``StepContract`` is defined in :mod:`bound.contracts`, which a sibling
    # teammate is creating in parallel. The EvidenceCollector Protocol is
    # *structural* (``runtime_checkable`` only verifies method presence, never
    # the annotation), so we import the name solely for type-checking. Combined
    # with ``from __future__ import annotations`` the annotation is never
    # evaluated at runtime, which keeps this module importable even before
    # ``contracts`` exists.
    from bound.contracts import StepContract


class CheckEvidence(BaseModel):
    """The outcome of a single named check observed after execution.

    A check is anything whose pass/fail state can be determined deterministically
    from the execution: a test assertion, a lint rule, a build step, the presence
    of an expected artifact, a risk probe. The collector records *what it saw* —
    it does not decide whether the check *should* have existed (that is the
    contract's job) nor what the outcome *means* for BOUND (that is the
    evaluator's job).

    Attributes:
        check_id: Identifier matching a ``check_id`` declared on the
            :class:`~bound.contracts.StepContract` (an acceptance or risk check).
            Unknown IDs are allowed here: the collector may observe checks the
            contract did not declare, and the evaluator is responsible for
            reconciling evidence against the contract (including treating missing
            *required* evidence as failure rather than silently passing).
        passed: Whether the check passed at observation time. A failing required
            check is valid evidence — ``passed=False`` is recorded faithfully so
            the evaluator can score it, never silently flipped to pass.
        source: Free-form provenance for how the outcome was determined (e.g. an
            artifact path, a command name, a tool name). Recorded for auditability.
        details: Optional human-readable elaboration of the outcome.
    """

    model_config = ConfigDict(extra="forbid")

    check_id: str
    passed: bool
    source: str
    details: str | None = None



class ExecutionEvidence(BaseModel):
    """All observations collected for one executed step.

    This is the aggregate record a :class:`EvidenceCollector` returns. It groups
    acceptance-check outcomes, risk-check outcomes, observed artifacts, and the
    resource/rollback telemetry the evaluator needs to compute the cost and risk
    dimensions. Every field is optional or defaults empty: a step that produced
    no recorded checks, no artifacts, and no telemetry is still *valid* evidence
    (it simply describes an execution about which little is known, which the
    evaluator will score conservatively — never optimistically).

    Attributes:
        acceptance: Outcomes of acceptance checks declared on the contract (or
            observed even if not declared). Empty means no acceptance check was
            recorded.
        risks: Outcomes of risk checks. Empty means no risk check was recorded.
        produced_artifacts: Artifact identifiers the execution produced.
        unexpected_artifacts: Artifact identifiers the execution produced that
            the contract did not expect — a risk signal.
        retry_count: Non-negative number of retries performed for this step.
        tool_call_count: Non-negative number of tool calls performed.
        token_usage: Non-negative total tokens consumed, or ``None`` when
            unmeasured.
        runtime_seconds: Non-negative wall-clock runtime in seconds, or ``None``
            when unmeasured.
        rollback_available: Whether a clean rollback is still possible after the
            execution, or ``None`` when unknown.
    """

    model_config = ConfigDict(extra="forbid")

    acceptance: list[CheckEvidence] = []
    risks: list[CheckEvidence] = []

    produced_artifacts: list[str] = []
    unexpected_artifacts: list[str] = []

    retry_count: int = Field(default=0, ge=0)
    tool_call_count: int = Field(default=0, ge=0)
    token_usage: int | None = Field(default=None, ge=0)
    runtime_seconds: float | None = Field(default=None, ge=0)

    rollback_available: bool | None = None


@runtime_checkable
class EvidenceCollector(Protocol):
    """Environment-agnostic seam that turns an execution into evidence.

    Different agent environments observe execution differently — a local CLI
    run, a CI job, an editor extension session. Rather than couple BOUND to any
    one of them, this Protocol defines the single place where an environment
    adapter extracts deterministic observations from its own execution handle and
    returns a plain :class:`ExecutionEvidence`.

    The ``execution`` parameter is deliberately typed as :class:`object`: it is
    any opaque handle the concrete collector understands (a transcript, a session
    object, a subprocess result, ...). BOUND's core never introspects it. This
    keeps the core free of provider dependencies: concrete collectors that
    bridge to Cline, Claude Code, Codex, Cursor, GitHub Actions, or pytest are
    implemented and integrated with those systems LATER, outside the
    deterministic core. For v0.3 only the Protocol and the data models exist.

    As with the :class:`~bound.evaluator.Evaluator`, a collector must **never**
    choose the final BOUND decision. It records evidence; the deterministic
    evaluator and policy decide.
    """

    def collect(
        self,
        *,
        contract: StepContract,
        execution: object,
    ) -> ExecutionEvidence:
        """Collect deterministic execution evidence for ``contract``.

        The ``contract`` tells the collector *which* checks and artifacts the
        plan declared, so it knows what to look for; ``execution`` is the opaque
        environment handle it reads them from. Implementations must return only
        what was actually observed — they must not fabricate passing evidence,
        must not silently drop failures, and must not produce a BOUND decision.

        Args:
            contract: The :class:`~bound.contracts.StepContract` whose declared
                checks and expected artifacts scope what to collect. Typed via a
                ``TYPE_CHECKING`` import so the Protocol stays structural at
                runtime and the core never imports the concrete contract module
                eagerly.
            execution: Any agent execution handle the concrete collector
                understands. Deliberately generic (``object``) so the core never
                depends on a specific agent environment.

        Returns:
            The :class:`ExecutionEvidence` describing what was observed. The
            collector records observations only; it never returns a decision.
        """
        ...
