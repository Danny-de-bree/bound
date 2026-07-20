from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

if TYPE_CHECKING:
    # ``StepContract`` is defined in :mod:`bound.contracts`, which a sibling
    # teammate is creating in parallel. The EvidenceCollector Protocol is
    # *structural* (``runtime_checkable`` only verifies method presence, never
    # the annotation), so we import the name solely for type-checking. Combined
    # with ``from __future__ import annotations`` the annotation is never
    # evaluated at runtime, which keeps this module importable even before
    # ``contracts`` exists.
    from bound.contracts import StepContract

#: Regex matching common secret-looking ``key=value`` / ``key: value`` patterns
#: (case-insensitive). Used by both :mod:`bound.command_collector` (pre-storage
#: command-output scrubbing) and :mod:`bound.lineage_store` (event-dict
#: redaction) to mask credential values before they reach a trace.
SECRET_PATTERN: re.Pattern[str] = re.compile(
    r"(?i)(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|"
    r"private[_-]?key|client[_-]?secret)\s*[:=]\s*(\S+)"
)

if TYPE_CHECKING:
    # ``StepContract`` is defined in :mod:`bound.contracts`, which a sibling
    # teammate is creating in parallel. The EvidenceCollector Protocol is
    # *structural* (``runtime_checkable`` only verifies method presence, never
    # the annotation), so we import the name solely for type-checking. Combined
    # with ``from __future__ import annotations`` the annotation is never
    # evaluated at runtime, which keeps this module importable even before
    # ``contracts`` exists.
    from bound.contracts import StepContract


class EvidenceProvenance(StrEnum):
    """How trustworthy a piece of evidence is — its *trust* provenance.

    This is deliberately distinct from the free-form :attr:`CheckEvidence.source`
    string, which records *where* an observation came from (a path, a command, a
    tool name). Provenance records *how much that source can be trusted* so a
    consumer can never mistake an agent self-report for an independently
    verified observation. The ordering below is not a strict total order, but it
    reflects the intent of the BOUND v0.7 honesty model: stronger provenance
    must never be silently fabricated from weaker provenance.

    Members:
        OBSERVED: Directly measured by an independent collector at execution
            time (e.g. a process exit code, a tool-call counter). The most
            trustworthy *direct* provenance.
        VERIFIED: Independently reproduced/checked by a BOUND-controlled
            collector against a raw artefact (e.g. re-running pytest, hashing a
            JUnit file). Stronger than OBSERVED for claims about reproducibility
            because BOUND — not the agent — performed the check.
        ATTESTED: Signed/attested by a trusted third party (e.g. a signed build
            provenance, a CI attestation). Trusted without BOUND re-running it.
        EVALUATED: Derived by the deterministic BOUND evaluator from other
            evidence (e.g. an acceptance ratio computed from observed checks).
            Honest but not independently measured.
        CLAIMED: Agent self-report only — the agent *says* it is so, with no
            independent confirmation. Agent self-report is always CLAIMED, never
            VERIFIED. The weakest *present* provenance.
        DEFAULTED: No evidence source existed at all, so BOUND substituted a
            policy-neutral value (e.g. influence ``I = 0.0``). DEFAULTED must
            never be presented as VERIFIED — it is an explicit admission of
            absence, not a measurement.
        MISSING: No evidence was collected for this signal at all. ``None`` on
            an :class:`EvidenceMetric` value means MISSING, never ``0``: absence
            is not silently coerced to a measured zero.
    """

    OBSERVED = "observed"
    VERIFIED = "verified"
    ATTESTED = "attested"
    EVALUATED = "evaluated"
    CLAIMED = "claimed"
    DEFAULTED = "defaulted"
    MISSING = "missing"


class EvidenceStatus(StrEnum):
    """Outcome category of a check, separating *failure* from *unverifiable*.

    A check that genuinely failed (``FAILED``) is valid, recordable evidence and
    must never be silently flipped to a pass. A check whose outcome could not be
    determined (``MISSING``/``INVALID``/``STALE``) is a *different* state: the
    conservative policy outcome still holds, but the trace must not present
    missing evidence as an observed failure. ``PASSED`` records an observed pass
    (paired with ``passed=True``); it is never inferred from absence.

    v0.7.0 additions (todo 1.2) introduce the canonical vocabulary
    ``PASSED`` / ``FAILED`` / ``MISSING`` / ``INVALID`` / ``STALE``. The legacy
    ``UNVERIFIED`` member is retained as a **deprecated alias** so existing
    collectors (which record "zero tests executed" as ``UNVERIFIED``) and
    schema-2.0 traces keep loading unchanged; new code should prefer a more
    specific status (``MISSING`` when nothing was collected, ``INVALID`` when
    the artefact was unusable, ``STALE`` when it was too old). ``UNVERIFIED`` is
    treated as an unverifiable (non-pass) outcome by the reporting layer.

    Members:
        PASSED: The check was observed and passed (``passed=True``).
        FAILED: The check was observed and did not pass.
        MISSING: No evidence was collected for the check at all.
        INVALID: Evidence was collected but is unusable (e.g. a collector
            crash, a tampered hash, malformed output).
        STALE: Evidence was collected but is too old to trust (e.g. an
            artefact whose mtime exceeds the configured freshness window).
        UNVERIFIED: Deprecated alias. Evidence was collected but the outcome
            could not be determined (e.g. zero tests executed). Retained for
            backwards compatibility with existing collectors and traces; new
            code should use ``MISSING``/``INVALID``/``STALE`` as appropriate.
    """

    PASSED = "passed"
    FAILED = "failed"
    MISSING = "missing"
    INVALID = "invalid"
    STALE = "stale"
    UNVERIFIED = "unverified"


class EvidenceMetric(BaseModel):
    """A single measured telemetry value paired with trust provenance.

    Wraps a raw scalar (a count, a duration, a bool) with the same trust
    provenance discipline as :class:`CheckEvidence`, so a consumer can always
    tell a *measured* value from an *unmeasured* one. The canonical rule of the
    v0.7 data model is: **``value is None`` means MISSING, never ``0``**. A
    measured zero is recorded as ``EvidenceMetric(value=0,
    provenance=EvidenceProvenance.OBSERVED)`` and is distinct from a missing
    signal recorded as ``EvidenceMetric(value=None,
    provenance=EvidenceProvenance.MISSING)``.

    Attributes:
        value: The raw observed value (an int, float, or bool), or ``None`` when
            the signal was not measured. ``None`` is *missing*, never a zero.
        provenance: How trustworthy this value is. Defaults to
            :attr:`EvidenceProvenance.MISSING` so a value supplied without an
            explicit provenance is never accidentally treated as verified.
        source: Optional free-form source string (e.g. ``"cline.tool_events"``).
        collector: Optional name of the collector that produced the value.
    """

    model_config = ConfigDict(extra="forbid")

    value: int | float | bool | None
    provenance: EvidenceProvenance = EvidenceProvenance.MISSING
    source: str | None = None
    collector: str | None = None


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
        passed: Whether the check passed at observation time. ``True``/``False``
            record an observed outcome; ``None`` means the outcome was not
            determined (pair with :attr:`status`). A failing required check is
            valid evidence — ``passed=False`` is recorded faithfully so the
            evaluator can score it, never silently flipped to pass.
        source: Free-form provenance for how the outcome was determined (e.g. an
            artifact path, a command name, a tool name). Recorded for
            auditability. Defaults to ``""`` so legacy traces that omit it still
            load.
        details: Optional human-readable elaboration of the outcome.
        provenance: Trust provenance of this evidence
            (:class:`EvidenceProvenance`). Defaults to :attr:`MISSING
            <EvidenceProvenance.MISSING>` so evidence without an explicit
            provenance is never accidentally trusted as verified.
        collector: Name of the collector that produced this evidence, when
            applicable (e.g. ``"bound.junit"``).
        collector_version: Version string of the collector that produced this
            evidence, for reproducibility audits.
        observed_at: Timezone-aware timestamp at which the evidence was observed.
            ``None`` when unknown; a naive (timezone-unaware) datetime is
            rejected to keep timestamps unambiguous.
        artifact_hash: Optional content hash of the raw artefact backing this
            evidence (e.g. ``"sha256:..."``), enabling staleness and tamper
            detection.
        raw_artifact_ref: Optional reference (path/URI) to the raw artefact the
            collector read. When redaction is enabled the raw contents are not
            stored; this reference plus :attr:`artifact_hash` let a verifier
            re-fetch and re-hash without retaining sensitive content.
        status: Optional :class:`EvidenceStatus` separating a genuine failure
            from missing/unverifiable/invalid evidence.
    """

    model_config = ConfigDict(extra="forbid")

    check_id: str
    passed: bool | None = None
    source: str = ""
    details: str | None = None
    provenance: EvidenceProvenance = EvidenceProvenance.MISSING
    collector: str | None = None
    collector_version: str | None = None
    observed_at: datetime | None = None
    artifact_hash: str | None = None
    raw_artifact_ref: str | None = None
    status: EvidenceStatus | None = None

    @field_validator("observed_at")
    @classmethod
    def _observed_at_must_be_timezone_aware(
        cls, value: datetime | None
    ) -> datetime | None:
        """Reject naive (timezone-unaware) timestamps.

        A timestamp without a timezone is ambiguous and therefore unsafe as
        audit evidence; BOUND standardises on timezone-aware datetimes (UTC
        preferred). ``None`` (unknown) is allowed.

        Args:
            value: The supplied timestamp, or ``None``.

        Returns:
            The validated timestamp, or ``None``.

        Raises:
            ValueError: If a non-``None`` datetime lacks ``tzinfo``.
        """
        if value is not None and value.tzinfo is None:
            raise ValueError(
                "observed_at must be timezone-aware (tzinfo is None); "
                "use a timezone-aware datetime such as datetime.now(timezone.utc)."
            )
        return value



# Telemetry fields on :class:`ExecutionEvidence` that are migrated from legacy
# plain-number traces into :class:`EvidenceMetric` wrappers. Listed once so the
# migration helper and the before-validator stay in sync.
_LEGACY_TELEMETRY_FIELDS: tuple[str, ...] = (
    "retry_count",
    "tool_call_count",
    "token_usage",
    "runtime_seconds",
)


def migrate_legacy_execution_evidence(data: dict[str, Any]) -> dict[str, Any]:
    """Migrate a legacy (schema < 2.0) ``ExecutionEvidence`` dict to the v0.7 shape.

    Old traces stored telemetry (``retry_count``, ``tool_call_count``,
    ``token_usage``, ``runtime_seconds``) as bare ints/floats, defaulting to
    ``0`` for retry/tool-call counts. v0.7 models those values as
    :class:`EvidenceMetric` so a *measured* zero is distinguishable from a
    *missing* signal. This helper wraps any bare numeric value found in ``data``
    into ``EvidenceMetric(value=value, provenance=EvidenceProvenance.MISSING)``:
    provenance is MISSING because a legacy trace cannot retroactively tell us
    whether the value was independently observed — it is **never** silently
    upgraded to OBSERVED or VERIFIED. ``None`` values are preserved (still
    missing). Values that are already :class:`EvidenceMetric` or metric-shaped
    dicts are left untouched for Pydantic to validate.

    This is called automatically by :class:`ExecutionEvidence`'s before-validator,
    so explicit migration is only needed when a caller wants to normalise a dict
    in isolation (e.g. a lineage store reading a schema-1.0 trace).

    Args:
        data: A raw constructor/mapping dict that may carry legacy bare-number
            telemetry fields. The dict is not mutated; a shallow copy is
            returned with only the telemetry keys replaced.

    Returns:
        A new dict with legacy bare-number telemetry wrapped in
        :class:`EvidenceMetric`.
    """
    migrated = dict(data)
    for key in _LEGACY_TELEMETRY_FIELDS:
        if key not in migrated:
            continue
        value = migrated[key]
        if value is None:
            continue
        if isinstance(value, EvidenceMetric):
            continue
        if isinstance(value, dict):
            # Already a metric-shaped mapping; let Pydantic validate it.
            continue
        if isinstance(value, (int, float, bool)):
            migrated[key] = EvidenceMetric(
                value=value, provenance=EvidenceProvenance.MISSING
            )
    return migrated


class ExecutionEvidence(BaseModel):
    """All observations collected for one executed step.

    This is the aggregate record a :class:`EvidenceCollector` returns. It groups
    acceptance-check outcomes, risk-check outcomes, observed artifacts, and the
    resource/rollback telemetry the evaluator needs to compute the cost and risk
    dimensions. Every field is optional or defaults empty: a step that produced
    no recorded checks, no artifacts, and no telemetry is still *valid* evidence
    (it simply describes an execution about which little is known, which the
    evaluator will score conservatively — never optimistically).

    v0.7 honesty change: telemetry values (``retry_count``,
    ``tool_call_count``, ``token_usage``, ``runtime_seconds``) are now
    :class:`EvidenceMetric` instances (or ``None``). A ``None`` metric means the
    signal was **not measured** — it is never silently coerced to a zero. A
    measured zero is ``EvidenceMetric(value=0, provenance=OBSERVED)``. Legacy
    traces that supplied bare numbers are migrated automatically by
    :func:`migrate_legacy_execution_evidence` (provenance MISSING, never
    upgraded to OBSERVED/VERIFIED).

    Attributes:
        acceptance: Outcomes of acceptance checks declared on the contract (or
            observed even if not declared). Empty means no acceptance check was
            recorded.
        risks: Outcomes of risk checks. Empty means no risk check was recorded.
        produced_artifacts: Artifact identifiers the execution produced.
        unexpected_artifacts: Artifact identifiers the execution produced that
            the contract did not expect — a risk signal.
        retry_count: Number of retries performed for this step, as an
            :class:`EvidenceMetric`, or ``None`` when retries were not measured
            (MISSING, never ``0``).
        tool_call_count: Number of tool calls performed, as an
            :class:`EvidenceMetric`, or ``None`` when unmeasured.
        token_usage: Total tokens consumed, as an :class:`EvidenceMetric`, or
            ``None`` when unmeasured.
        runtime_seconds: Wall-clock runtime in seconds, as an
            :class:`EvidenceMetric`, or ``None`` when unmeasured.
        rollback_available: Whether a clean rollback is still possible after the
            execution, or ``None`` when unknown.
    """

    model_config = ConfigDict(extra="forbid")

    acceptance: list[CheckEvidence] = []
    risks: list[CheckEvidence] = []

    produced_artifacts: list[str] = []
    unexpected_artifacts: list[str] = []

    retry_count: EvidenceMetric | None = None
    tool_call_count: EvidenceMetric | None = None
    token_usage: EvidenceMetric | None = None
    runtime_seconds: EvidenceMetric | None = None

    rollback_available: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_telemetry(cls, data: Any) -> Any:
        """Wrap legacy bare-number telemetry into :class:`EvidenceMetric`.

        Old traces supplied ``retry_count``/``tool_call_count`` as plain ints
        (defaulting to ``0``) and ``token_usage``/``runtime_seconds`` as plain
        numbers. v0.7 models these as :class:`EvidenceMetric` so a measured zero
        is distinguishable from a missing signal. This before-validator wraps
        any bare numeric input into ``EvidenceMetric(value=…,
        provenance=MISSING)`` — provenance is MISSING because a legacy trace
        cannot retroactively prove independent observation, and missing evidence
        is **never** silently upgraded to a stronger provenance. ``None`` and
        already-``EvidenceMetric``/metric-dict values are passed through
        unchanged.

        Args:
            data: The raw constructor input (a mapping, an
                :class:`ExecutionEvidence` instance, or a non-mapping value).

        Returns:
            The (possibly migrated) input for normal Pydantic validation.
        """
        if not isinstance(data, dict):
            return data
        return migrate_legacy_execution_evidence(data)


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
