from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
)

from bound.evidence import EvidenceProvenance, EvidenceStatus
from bound.integration import NextAction
from bound.models import BoundWeights, Decision, DecisionAssurance, EvaluationScores
from bound.policy_canon import compute_policy_hash

if TYPE_CHECKING:
    from bound.policy_schema import BoundPolicyConfig

__all__ = [
    "ActionObservedEvent",
    "ActionReportedEvent",
    "Attempt",
    "DecisionGatedEvent",
    "EVENT_NAMES",
    "EvaluationCompletedEvent",
    "Evaluation",
    "EvaluationRecordedEvent",
    "EvidenceCollectedEvent",
    "EvidenceCollectionFailedEvent",
    "LINEAGE_SCHEMA_VERSION",
    "LineageEvent",
    "Outcome",
    "OutcomeRecordedEvent",
    "PolicyActivatedEvent",
    "PolicyApprovedEvent",
    "PolicyProposedEvent",
    "PolicyValidatedEvent",
    "ReasonCode",
    "Run",
    "RunConfigSnapshot",
    "RunFinishedEvent",
    "RunFinishStatus",
    "RunStartedEvent",
    "RunStatus",
    "Step",
    "StepCompletedEvent",
    "StepStartedEvent",
    "StepStatus",
    "UTCDateTime",
    "build_run_config",
    "compute_contract_hash",
    "compute_policy_config_hash",
    "generate_event_id",
    "generate_evaluation_id",
    "generate_run_id",
    "generate_step_id",
    "parse_lineage_event",
    "sha256_hex",
    "utc_now",
]

#: Schema version for every lineage entity and event. Bumped only when the
#: event shape changes in a backwards-incompatible way; readers use it to
#: migrate or reject logs they cannot understand.
#:
#: v2.0 adds per-event ``sequence`` / ``parent_event_id``, a
#: :class:`RunConfigSnapshot` on ``run_started`` (item 11), and append-only
#: event types — the schema-1.0 core (``run_started`` / ``step_started`` /
#: ``evaluation_recorded`` / ``outcome_recorded`` / ``run_finished``) plus the
#: schema-2.0 additions: ``evidence.collected``, ``evidence.collection_failed``,
#: ``decision.gated``, ``action.reported`` (item 10/12), and the todo 7.1
#: policy-lifecycle + lifecycle-completion events ``policy.proposed`` /
#: ``policy.validated`` / ``policy.approved`` / ``policy.activated``,
#: ``evaluation.completed``, ``action.observed``, ``step.completed``.
#: Schema-1.0 traces remain readable: the new fields are optional with safe
#: defaults, and :func:`parse_lineage_event` accepts both.
LINEAGE_SCHEMA_VERSION: str = "2.0"

#: The ordered set of lineage event names. This is the complete append-only
#: event vocabulary. The first five (schema 1.0) describe the core
#: contract -> evidence -> calculation -> decision -> outcome flow; the dotted
#: names (schema 2.0) add the policy lifecycle, independent evidence collection,
#: assurance gating, and the agent's claimed/observed action report.
#:
#: Schema 2.0 additions (todo 7.1): ``policy.proposed`` / ``policy.validated``
#: / ``policy.approved`` / ``policy.activated`` record the policy lifecycle;
#: ``evaluation.completed`` marks a finished evaluation (distinct from
#: ``evaluation_recorded`` which records the calculation); ``action.observed``
#: records an independent hook's observation of the agent's action (complements
#: ``action.reported`` which is CLAIMED); ``step.completed`` marks a finished
#: step.
EVENT_NAMES: tuple[str, ...] = (
    "run_started",
    "policy.proposed",
    "policy.validated",
    "policy.approved",
    "policy.activated",
    "step_started",
    "evidence.collected",
    "evidence.collection_failed",
    "evaluation_recorded",
    "evaluation.completed",
    "decision.gated",
    "action.reported",
    "action.observed",
    "step.completed",
    "outcome_recorded",
    "run_finished",
)



def _ensure_utc(value: datetime) -> datetime:
    """Return ``value`` as timezone-aware UTC, rejecting naive datetimes.

    Lineage timestamps must be unambiguous and reproducible, so every
    timestamp is normalized to UTC and naive datetimes are rejected rather
    than silently interpreted as local time.

    Args:
        value: The datetime to normalize.

    Returns:
        The same instant expressed in UTC (``tzinfo=timezone.utc``).

    Raises:
        ValueError: If ``value`` has no timezone information.
    """
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware (UTC); got a naive value")
    return value.astimezone(UTC)


#: A pydantic-validated timezone-aware UTC datetime. Used for every timestamp
#: field in the lineage schema so naive values are rejected at parse time and
#: stored instants serialize to ISO-8601 with a ``+00:00`` offset.
UTCDateTime = Annotated[datetime, AfterValidator(_ensure_utc)]


def utc_now() -> datetime:
    """Return the current instant as timezone-aware UTC.

    Returns:
        ``datetime.now(timezone.utc)`` — the single clock source lineage
        uses so every timestamp is comparable and UTC-normalized.
    """
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Fixed reason-code vocabulary
# ---------------------------------------------------------------------------


class ReasonCode(StrEnum):
    """Fixed vocabulary of why a lineage event was recorded.

    Reason codes are the only admissible ``reason`` value on events and
    outcomes — there is no free-text reason field. The set is the union of:

    * the four BOUND decisions (``ACCEPT`` / ``RETRY`` / ``REPLAN`` /
      ``ROLLBACK``), mirroring :data:`~bound.models.Decision`;
    * evaluation-evidence reasons explaining *why* a decision was reached;
    * outcome reasons describing the control action the agent took;
    * run-lifecycle reasons.

    New codes are added here, never invented at call sites, so storage and
    inspection tooling can enumerate the complete vocabulary upfront.
    """

    # --- Decision-derived (mirror bound.models.Decision) ---
    ACCEPT = "ACCEPT"
    RETRY = "RETRY"
    REPLAN = "REPLAN"
    ROLLBACK = "ROLLBACK"

    # --- Evaluation evidence ---
    ALL_CHECKS_PASSED = "ALL_CHECKS_PASSED"
    REQUIRED_CHECKS_FAILED = "REQUIRED_CHECKS_FAILED"
    RISK_BOUNDARY_EXCEEDED = "RISK_BOUNDARY_EXCEEDED"
    BELOW_THRESHOLD = "BELOW_THRESHOLD"
    WITHIN_RETRY_MARGIN = "WITHIN_RETRY_MARGIN"

    # --- Outcome / control action ---
    CONTINUED = "CONTINUED"
    RETRIED = "RETRIED"
    REPLANNED = "REPLANNED"
    ROLLED_BACK = "ROLLED_BACK"

    # --- Run lifecycle ---
    RUN_STARTED = "RUN_STARTED"
    RUN_COMPLETED = "RUN_COMPLETED"
    RUN_INTERRUPTED = "RUN_INTERRUPTED"
    RUN_FAILED = "RUN_FAILED"


class RunStatus(StrEnum):
    """Lifecycle status of a :class:`Run` snapshot."""

    STARTED = "started"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


class RunFinishStatus(StrEnum):
    """Terminal status carried by a :class:`RunFinishedEvent`."""

    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


class StepStatus(StrEnum):
    """Lifecycle status of a :class:`Step` snapshot."""

    STARTED = "started"
    COMPLETED = "completed"
    REPLANNED = "replanned"


# ---------------------------------------------------------------------------
# Deterministic, reproducible identifiers
# ---------------------------------------------------------------------------


def _hash_id(prefix: str, parts: tuple[str, ...]) -> str:
    """Return a deterministic ``<prefix>_<hex>`` id from ``parts``.

    The id is a truncated SHA-256 of the parts joined by a unit separator,
    so identical inputs always produce the same id and distinct inputs
    (barring a collision in the truncated digest) produce distinct ids. The
    separator (``\\x1f``) is chosen so it cannot appear inside an ordinary
    part, keeping the preimage unambiguous.

    Args:
        prefix: Human-readable prefix (``run`` / ``step`` / ``eval`` / ``evt``).
        parts: The string parts that determine the id.

    Returns:
        ``f"{prefix}_{digest}"`` where ``digest`` is a 20-character hex
        SHA-256 truncation.
    """
    payload = "\x1f".join(parts).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:20]
    return f"{prefix}_{digest}"


def generate_run_id(*, task: str, started_at: datetime) -> str:
    """Return a deterministic, reproducible ``run_id``.

    The id depends on the run's task and its start instant, so replaying the
    same task at the same instant reproduces the same id byte-for-byte while
    distinct runs (different task or start time) get distinct ids.

    Args:
        task: The natural-language task the run attempts.
        started_at: The run's start instant (normalized to UTC).

    Returns:
        A ``run_<hex>`` identifier.
    """
    return _hash_id("run", (task, _ensure_utc(started_at).isoformat()))


def generate_step_id(*, run_id: str, contract_id: str, attempt: int) -> str:
    """Return a deterministic, reproducible ``step_id``.

    Args:
        run_id: The owning run id.
        contract_id: The stable contract / phase id (e.g. ``PHASE-001`` or
            ``PHASE-001-R1`` after a replan).
        attempt: One-based attempt number for this step.

    Returns:
        A ``step_<hex>`` identifier.
    """
    return _hash_id("step", (run_id, contract_id, str(attempt)))


def generate_evaluation_id(
    *, run_id: str, step_id: str, attempt: int, salt: str = ""
) -> str:
    """Return a deterministic, reproducible ``evaluation_id``.

    Args:
        run_id: The owning run id.
        step_id: The step being evaluated.
        attempt: One-based attempt number.
        salt: Optional disambiguator (e.g. a sub-evaluator name) when more
            than one evaluation is recorded for the same attempt. Defaults
            to empty, which is the normal single-evaluation case.

    Returns:
        An ``eval_<hex>`` identifier.
    """
    return _hash_id("eval", (run_id, step_id, str(attempt), salt))


def generate_event_id(*, run_id: str, sequence: int) -> str:
    """Return a deterministic, reproducible ``event_id``.

    Events are ordered within a run; the ``sequence`` is the one-based
    position of the event in that run's log, so a replay reproduces every
    event id.

    Args:
        run_id: The owning run id.
        sequence: One-based event position within the run.

    Returns:
        An ``evt_<hex>`` identifier.
    """
    return _hash_id("evt", (run_id, str(sequence)))


# ---------------------------------------------------------------------------
# Config / policy version snapshot
# ---------------------------------------------------------------------------


def sha256_hex(data: str | bytes) -> str:
    """Return the SHA-256 hex digest of ``data``.

    Used for content-addressing contracts and policy configs so a trace can
    prove *which* configuration governed a decision without re-running the
    agent's actions (item 11: policy replay).

    Args:
        data: A string (UTF-8 encoded) or bytes payload.

    Returns:
        The 64-character lowercase hex digest.
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _canonical_json(obj: object) -> str:
    """Return a deterministic, sorted-keys JSON string for hashing.

    Pydantic models are dumped to JSON-mode dicts first so the hash is stable
    across Python object identity. ``sort_keys=True`` guarantees field order
    never affects the digest.
    """
    if isinstance(obj, BaseModel):
        obj = obj.model_dump(mode="json")
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def compute_contract_hash(contract: BaseModel | dict | str) -> str:
    """Return the SHA-256 hex of a contract's canonical JSON (item 11).

    Args:
        contract: A :class:`~bound.contracts.StepContract` (or any Pydantic
            model), a dict, or a raw JSON string.

    Returns:
        The 64-character hex digest identifying the exact contract version.
    """
    return sha256_hex(_canonical_json(contract))


def compute_policy_config_hash(config: BaseModel | dict | str) -> str:
    """Return the SHA-256 hex of a resolved policy config (item 11).

    Args:
        config: The resolved policy configuration (weights, thresholds,
            provenance rules, ...) as a Pydantic model, dict, or JSON string.

    Returns:
        The 64-character hex digest identifying the exact policy config.
    """
    return sha256_hex(_canonical_json(config))


# ---------------------------------------------------------------------------
# Entity snapshots (current-state views derived from the event log)
# ---------------------------------------------------------------------------


class Attempt(BaseModel):
    """One evaluation attempt within a :class:`Step`.

    A step may be attempted multiple times (retry / replan); each attempt
    records its one-based number, its own start instant, and the
    ``evaluation_id`` of the evaluation recorded for it (``None`` until the
    evaluation event arrives).

    Attributes:
        attempt: One-based attempt number.
        started_at: UTC instant the attempt began.
        evaluation_id: The evaluation id recorded for this attempt, or
            ``None`` while the attempt is in progress.
    """

    model_config = ConfigDict(extra="forbid")

    attempt: int = Field(ge=1)
    started_at: UTCDateTime
    evaluation_id: str | None = None


class Step(BaseModel):
    """Snapshot of one step within a :class:`Run`.

    Attributes:
        step_id: Unique step id (see :func:`generate_step_id`).
        run_id: Owning run id.
        schema_version: Lineage schema version.
        contract_id: Stable contract / phase id (may carry ``-R<N>``).
        description: Optional human-readable step description.
        started_at: UTC instant the step began.
        finished_at: UTC instant the step finished, or ``None`` while open.
        status: Current :class:`StepStatus`.
        attempts: Ordered attempts for this step (empty until the first
            attempt starts — explicit, not omitted).
    """

    model_config = ConfigDict(extra="forbid")

    step_id: str
    run_id: str
    schema_version: str = LINEAGE_SCHEMA_VERSION
    contract_id: str
    description: str | None = None
    started_at: UTCDateTime
    finished_at: UTCDateTime | None = None
    status: StepStatus = StepStatus.STARTED
    attempts: list[Attempt] = []


class Evaluation(BaseModel):
    """Snapshot of one BOUND evaluation recorded for an attempt.

    Attributes:
        evaluation_id: Unique evaluation id (see :func:`generate_evaluation_id`).
        run_id: Owning run id.
        step_id: Step the evaluation belongs to.
        attempt: One-based attempt number.
        scores: The deterministic :class:`~bound.models.EvaluationScores`.
        score: Final bounded-utility score ``S``.
        threshold: Acceptance threshold ``T``.
        decision: The BOUND :class:`~bound.models.Decision`.
        reason_code: The :class:`ReasonCode` explaining the evaluation.
        recorded_at: UTC instant the evaluation was recorded.
    """

    model_config = ConfigDict(extra="forbid")

    evaluation_id: str
    run_id: str
    step_id: str
    attempt: int = Field(ge=1)
    scores: EvaluationScores
    score: float
    threshold: float
    decision: Decision
    reason_code: ReasonCode
    recorded_at: UTCDateTime


class Outcome(BaseModel):
    """Snapshot of the control action taken after an evaluation.

    Attributes:
        run_id: Owning run id.
        step_id: Step the outcome applies to.
        evaluation_id: Evaluation the outcome responds to.
        decision: The BOUND :class:`~bound.models.Decision`.
        next_action: The mapped :class:`~bound.integration.NextAction`.
        reason_code: The :class:`ReasonCode` explaining the outcome.
        recorded_at: UTC instant the outcome was recorded.
        note: Optional free-text context (e.g. ``"switched to csv.DictWriter"``).
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    step_id: str
    evaluation_id: str
    decision: Decision
    next_action: NextAction
    reason_code: ReasonCode
    recorded_at: UTCDateTime
    note: str | None = None


class RunConfigSnapshot(BaseModel):
    """Policy/config version snapshot logged at run start (item 11).

    Captures everything needed to *replay* a decision without re-running the
    agent's actions: the BOUND package version, the trace-schema version, the
    policy id and config version+hash, the resolved weights and thresholds,
    the contract hash, the collector versions, and the evaluator model/prompt
    version. All fields are optional so a partial snapshot is still honest
    (``None`` means "not recorded", never a fabricated value).

    Attributes:
        bound_version: ``bound.__version__`` at run time.
        trace_schema_version: :data:`LINEAGE_SCHEMA_VERSION` of the writer.
        policy_id: Identifier of the policy that governed the run.
        policy_version: Version label of the approved policy (e.g. ``"1.0"``,
            from :class:`~bound.policy_schema.PolicyIdentity`).
        policy_hash: Canonical SHA-256 hash of the approved policy
            (``"sha256:<hex>"`` from
            :func:`~bound.policy_canon.compute_policy_hash`). This is the
            release-blocker value: every decision records the policy hash.
        policy_config_version: Version label of the resolved policy config.
        policy_config_hash: SHA-256 of the resolved policy config (enables
            exact replay / diffing).
        weights: The resolved :class:`~bound.models.BoundWeights` as a dict.
        threshold: Acceptance threshold ``T``.
        retry_margin: Retry margin below ``T``.
        rollback_risk_threshold: Hard risk boundary.
        contract_hash: SHA-256 of the contract (see
            :func:`compute_contract_hash`).
        collector_versions: Mapping of collector name -> version.
        evaluator_model: Model id of the evaluator (when applicable).
        evaluator_prompt_version: Prompt version of the evaluator.
    """

    model_config = ConfigDict(extra="forbid")

    bound_version: str | None = None
    trace_schema_version: str = LINEAGE_SCHEMA_VERSION
    policy_id: str | None = None
    policy_version: str | None = None
    policy_hash: str | None = None
    policy_config_version: str | None = None
    policy_config_hash: str | None = None
    weights: dict[str, float] | None = None
    threshold: float | None = None
    retry_margin: float | None = None
    rollback_risk_threshold: float | None = None
    contract_hash: str | None = None
    collector_versions: dict[str, str] = {}
    evaluator_model: str | None = None
    evaluator_prompt_version: str | None = None


def build_run_config(
    *,
    bound_version: str | None = None,
    policy_id: str | None = None,
    policy_version: str | None = None,
    policy_hash: str | None = None,
    policy: BoundPolicyConfig | None = None,
    policy_config_version: str | None = None,
    policy_config: BaseModel | dict | str | None = None,
    weights: BoundWeights | dict[str, float] | None = None,
    threshold: float | None = None,
    retry_margin: float | None = None,
    rollback_risk_threshold: float | None = None,
    contract: BaseModel | dict | str | None = None,
    collector_versions: dict[str, str] | None = None,
    evaluator_model: str | None = None,
    evaluator_prompt_version: str | None = None,
) -> RunConfigSnapshot:
    """Build a :class:`RunConfigSnapshot`, computing hashes from raw inputs.

    Convenience constructor that turns a contract / policy-config object into
    its SHA-256 hash so callers do not have to call
    :func:`compute_contract_hash` / :func:`compute_policy_config_hash` by hand.

    When ``policy`` (a :class:`~bound.policy_schema.BoundPolicyConfig`) is
    supplied, its ``policy.id`` / ``policy.version`` and the canonical
    :func:`~bound.policy_canon.compute_policy_hash` are filled in automatically
    (overriding any explicit ``policy_id`` / ``policy_version`` / ``policy_hash``
    only when those are ``None``). This is the recommended way to record the
    approved policy that governed a run (todo 7.2: every decision records the
    policy hash).

    Args:
        bound_version: ``bound.__version__`` string.
        policy_id: Policy identifier.
        policy_version: Version label of the approved policy (e.g. ``"1.0"``).
        policy_hash: Canonical policy hash (``"sha256:<hex>"``).
        policy: A validated :class:`~bound.policy_schema.BoundPolicyConfig`;
            when supplied, ``policy_id`` / ``policy_version`` / ``policy_hash``
            are derived from it (when not given explicitly).
        policy_config_version: Policy config version label.
        policy_config: The resolved policy config (model/dict/JSON); hashed.
        weights: The resolved weights (model or dict).
        threshold: Acceptance threshold ``T``.
        retry_margin: Retry margin.
        rollback_risk_threshold: Risk boundary.
        contract: The contract (model/dict/JSON); hashed.
        collector_versions: Collector name -> version mapping.
        evaluator_model: Evaluator model id.
        evaluator_prompt_version: Evaluator prompt version.

    Returns:
        A populated :class:`RunConfigSnapshot`.
    """
    if policy is not None:
        if policy_id is None:
            policy_id = policy.policy.id
        if policy_version is None:
            policy_version = policy.policy.version
        if policy_hash is None:
            policy_hash = compute_policy_hash(policy)
    weights_dict: dict[str, float] | None = None
    if weights is not None:
        weights_dict = (
            weights if isinstance(weights, dict) else weights.model_dump(mode="json")
        )
    return RunConfigSnapshot(
        bound_version=bound_version,
        policy_id=policy_id,
        policy_version=policy_version,
        policy_hash=policy_hash,
        policy_config_version=policy_config_version,
        policy_config_hash=(
            compute_policy_config_hash(policy_config) if policy_config is not None else None
        ),
        weights=weights_dict,
        threshold=threshold,
        retry_margin=retry_margin,
        rollback_risk_threshold=rollback_risk_threshold,
        contract_hash=compute_contract_hash(contract) if contract is not None else None,
        collector_versions=collector_versions or {},
        evaluator_model=evaluator_model,
        evaluator_prompt_version=evaluator_prompt_version,
    )


class Run(BaseModel):
    """Snapshot of a lineage run's current state.

    Attributes:
        run_id: Unique run id (see :func:`generate_run_id`).
        schema_version: Lineage schema version.
        task: The natural-language task the run attempts.
        started_at: UTC instant the run began.
        finished_at: UTC instant the run finished, or ``None`` while open.
        status: Current :class:`RunStatus`.
        step_ids: Ordered step ids belonging to this run (empty until the
            first step starts — explicit, not omitted).
        metadata: Optional free-form string metadata (never secrets; the
            privacy layer redacts before persistence).
        config: Optional :class:`RunConfigSnapshot` logged at run start
            (item 11). ``None`` for schema-1.0 traces or when not supplied.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    schema_version: str = LINEAGE_SCHEMA_VERSION
    task: str
    started_at: UTCDateTime
    finished_at: UTCDateTime | None = None
    status: RunStatus = RunStatus.STARTED
    step_ids: list[str] = []
    metadata: dict[str, str] = {}
    config: RunConfigSnapshot | None = None



# ---------------------------------------------------------------------------
# Append-only event types
# ---------------------------------------------------------------------------


class _LineageEventBase(BaseModel):
    """Common fields shared by every lineage event.

    Every event is immutable and carries its own identity, the instant it
    was recorded (UTC), and the lineage schema version. Concrete events add
    a Literal ``event`` tag and their specific payload.

    Schema 2.0 adds two optional ordering/linking fields (item 10):
    ``sequence`` (the one-based position of this event in its run's log) and
    ``parent_event_id`` (the id of the event this one causally follows, when
    applicable — e.g. an ``evaluation_recorded`` event's parent is the
    ``step_started`` it evaluates). Both default to ``None`` so schema-1.0
    traces (which lack them) still parse.

    Attributes:
        schema_version: Lineage schema version.
        event_id: Unique event id (see :func:`generate_event_id`).
        timestamp: UTC instant the event was recorded.
        sequence: One-based position of this event in its run's log, or
            ``None`` for schema-1.0 traces.
        parent_event_id: Id of the causally-preceding event, or ``None``.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = LINEAGE_SCHEMA_VERSION
    event_id: str
    timestamp: UTCDateTime
    sequence: int | None = Field(default=None, ge=1)
    parent_event_id: str | None = None


class RunStartedEvent(_LineageEventBase):
    """Event: a run has started.

    Always the first event in a run's log.

    Attributes:
        event: The literal tag ``"run_started"``.
        run_id: The new run's id.
        task: The natural-language task the run attempts.
        metadata: Optional free-form string metadata.
        config: Optional :class:`RunConfigSnapshot` logging the policy/config
            version that governed this run (item 11). ``None`` when not
            supplied (backwards compatible with schema 1.0).
    """

    event: Literal["run_started"] = "run_started"
    run_id: str
    task: str
    metadata: dict[str, str] = {}
    config: RunConfigSnapshot | None = None


class StepStartedEvent(_LineageEventBase):
    """Event: a step attempt has started.

    A replan or retry emits a fresh ``step_started`` with an incremented
    ``attempt`` (and a ``-R<N>``-suffixed ``contract_id`` for replans) so
    the lineage preserves every attempt rather than rewriting it.

    Attributes:
        event: The literal tag ``"step_started"``.
        run_id: Owning run id.
        step_id: Unique step id for this attempt.
        contract_id: Stable contract / phase id (may carry ``-R<N>``).
        attempt: One-based attempt number.
        description: Optional human-readable step description.
    """

    event: Literal["step_started"] = "step_started"
    run_id: str
    step_id: str
    contract_id: str
    attempt: int = Field(ge=1)
    description: str | None = None




class EvaluationRecordedEvent(_LineageEventBase):
    """Event: a BOUND evaluation was recorded for an attempt.

    Carries the deterministic scores, derived decision, and the
    :class:`ReasonCode` explaining it. This is the only event that carries
    scoring data — outcomes and run-finish events reference it by id.

    Phase 7.2 (trace contents): the evaluation also records the policy
    id/version/hash and contract hash that governed it, the candidate and
    final decisions, the decision assurance, the effective weights, collector
    versions, and the raw/effective evidence values. All policy fields are
    optional (``None`` = not recorded) so schema-1.0 traces and existing callers
    that do not supply them still parse unchanged.

    Attributes:
        event: The literal tag ``"evaluation_recorded"``.
        evaluation_id: Unique evaluation id.
        run_id: Owning run id.
        step_id: Step the evaluation belongs to.
        attempt: One-based attempt number.
        scores: The deterministic :class:`~bound.models.EvaluationScores`.
        score: Final bounded-utility score ``S``.
        threshold: Acceptance threshold ``T``.
        decision: The BOUND :class:`~bound.models.Decision`.
        reason_code: The :class:`ReasonCode` explaining the evaluation.
        policy_id: Identifier of the governing policy, or ``None``.
        policy_version: Version label of the governing policy, or ``None``.
        policy_hash: Canonical policy hash (``"sha256:<hex>"``), or ``None``.
        contract_hash: SHA-256 hex of the contract, or ``None``.
        candidate_decision: Score-based decision before assurance gating.
        final_decision: Decision after assurance gating.
        assurance: The :class:`~bound.models.DecisionAssurance` level.
        effective_weights: Resolved weights as a dict, or ``None``.
        collector_versions: Collector name -> version mapping, or ``None``.
        raw_evidence_values: Raw evidence values per check id, or ``None``.
        effective_evidence_values: Policy-adjusted effective values, or ``None``.
    """

    event: Literal["evaluation_recorded"] = "evaluation_recorded"
    evaluation_id: str
    run_id: str
    step_id: str
    attempt: int = Field(ge=1)
    scores: EvaluationScores
    score: float
    threshold: float
    decision: Decision
    reason_code: ReasonCode
    policy_id: str | None = None
    policy_version: str | None = None
    policy_hash: str | None = None
    contract_hash: str | None = None
    candidate_decision: Decision | None = None
    final_decision: Decision | None = None
    assurance: DecisionAssurance | None = None
    effective_weights: dict[str, float] | None = None
    collector_versions: dict[str, str] | None = None
    raw_evidence_values: dict[str, float | None] | None = None
    effective_evidence_values: dict[str, float] | None = None


class OutcomeRecordedEvent(_LineageEventBase):
    """Event: the control action taken after an evaluation was recorded.

    Attributes:
        event: The literal tag ``"outcome_recorded"``.
        run_id: Owning run id.
        step_id: Step the outcome applies to.
        evaluation_id: Evaluation the outcome responds to.
        decision: The BOUND :class:`~bound.models.Decision`.
        next_action: The mapped :class:`~bound.integration.NextAction`.
        reason_code: The :class:`ReasonCode` explaining the outcome.
        note: Optional free-text context (e.g. ``"switched to csv.DictWriter"``).
    """

    event: Literal["outcome_recorded"] = "outcome_recorded"
    run_id: str
    step_id: str
    evaluation_id: str
    decision: Decision
    next_action: NextAction
    reason_code: ReasonCode
    note: str | None = None


class RunFinishedEvent(_LineageEventBase):
    """Event: a run has finished (completed, interrupted, or failed).

    Always the last event in a run's log; a missing ``run_finished`` marks
    an incomplete / crashed run, which storage and inspection must keep
    readable.

    Attributes:
        event: The literal tag ``"run_finished"``.
        run_id: The finished run's id.
        status: Terminal :class:`RunFinishStatus`.
        reason_code: The :class:`ReasonCode` explaining the finish.
        note: Optional free-text context.
    """

    event: Literal["run_finished"] = "run_finished"
    run_id: str
    status: RunFinishStatus
    reason_code: ReasonCode
    note: str | None = None


class EvidenceCollectedEvent(_LineageEventBase):
    """Event: a piece of evidence was collected by an independent collector.

    Schema 2.0 (item 10). Records that a BOUND-controlled collector produced
    evidence for a check, with its trust provenance. This is the append-only
    audit record backing ``VERIFIED`` / ``OBSERVED`` provenance — the *proof*
    that a check was independently verified, not just claimed.

    Attributes:
        event: The literal tag ``"evidence.collected"``.
        run_id: Owning run id.
        step_id: Step the evidence applies to.
        check_id: The check id this evidence is for.
        collector: Collector name (e.g. ``"bound.pytest"``).
        collector_version: Collector version string.
        provenance: The :class:`~bound.evidence.EvidenceProvenance`.
        passed: Whether the check passed (``None`` when undetermined).
        status: The :class:`~bound.evidence.EvidenceStatus`, or ``None``.
        artifact_hash: SHA-256 of the raw artifact, or ``None``.
        source: Where the evidence came from (path, command, ...).
        observed_at: UTC instant the evidence was observed.
    """

    event: Literal["evidence.collected"] = "evidence.collected"
    run_id: str
    step_id: str
    check_id: str
    collector: str
    collector_version: str | None = None
    provenance: EvidenceProvenance
    passed: bool | None = None
    status: EvidenceStatus | None = None
    artifact_hash: str | None = None
    source: str | None = None
    observed_at: UTCDateTime


class EvidenceCollectionFailedEvent(_LineageEventBase):
    """Event: an evidence collector failed to collect (item 10).

    Records a collector crash, timeout, or parse failure so the trace is
    honest about *why* evidence is missing — a ``evidence.collection_failed``
    event proves the evidence was attempted but could not be obtained, not
    silently omitted.

    Attributes:
        event: The literal tag ``"evidence.collection_failed"``.
        run_id: Owning run id.
        step_id: Step the evidence was for.
        check_id: The check id, or ``None`` when the collector failed before
            identifying the check.
        collector: Collector name, or ``None`` when unknown.
        error: Error message describing the failure.
        observed_at: UTC instant the failure was observed.
    """

    event: Literal["evidence.collection_failed"] = "evidence.collection_failed"
    run_id: str
    step_id: str
    check_id: str | None = None
    collector: str | None = None
    error: str
    observed_at: UTCDateTime


class DecisionGatedEvent(_LineageEventBase):
    """Event: a candidate decision was gated by assurance (item 10/12).

    Records the assurance assessment that may downgrade a candidate ACCEPT.
    When ``candidate_decision`` equals ``final_decision`` the gate did not
    change the outcome (but the assurance level is still recorded for
    auditability). When they differ, assurance gating blocked the ACCEPT.

    Attributes:
        event: The literal tag ``"decision.gated"``.
        run_id: Owning run id.
        step_id: Step the decision applies to.
        evaluation_id: The evaluation being gated.
        candidate_decision: The raw score-based decision before gating.
        final_decision: The decision after assurance gating.
        assurance: The :class:`~bound.models.DecisionAssurance` level.
        assurance_reasons: Human-readable reasons for the assurance level.
    """

    event: Literal["decision.gated"] = "decision.gated"
    run_id: str
    step_id: str
    evaluation_id: str
    candidate_decision: Decision
    final_decision: Decision
    assurance: DecisionAssurance
    assurance_reasons: list[str] = []


class ActionReportedEvent(_LineageEventBase):
    """Event: the agent reported taking an action after a BOUND decision (item 12).

    Records the three views of an agent action:

    * **intended** — what BOUND told the agent to do (``intended_action``,
      carried from the mapped :class:`~bound.integration.NextAction`).
    * **reported** — what the agent *says* it did (``reported_action``). This
      is always agent self-report, so ``reported_provenance`` defaults to
      :attr:`~bound.evidence.EvidenceProvenance.CLAIMED` and must never be
      VERIFIED — the agent cannot grant itself verified provenance.
    * **observed** — what an independent integration hook *confirmed* the
      agent did (``observed_action`` / ``observed_provenance``). ``None``
      when no hook observed the action, in which case the action stays
      CLAIMED / UNVERIFIED. This is how ROLLBACK is proven executed: without
      an ``observed`` confirmation, a ROLLBACK is only *claimed*, not
      *verified*.

    For REPLAN, ``new_contract_id`` records the new plan/contract id that
    resulted from the replan (e.g. ``PHASE-001-R1``), proving a new plan was
    produced rather than the old one silently reused.

    Attributes:
        event: The literal tag ``"action.reported"``.
        run_id: Owning run id.
        step_id: Step the action was taken for.
        evaluation_id: The evaluation that triggered the action.
        intended_action: The control action BOUND mapped.
        reported_action: The agent's self-reported action description.
        reported_provenance: Provenance of the report (always CLAIMED).
        observed_action: What an independent hook observed, or ``None``.
        observed_provenance: Provenance of the observation, or ``None``.
        new_contract_id: New contract id from a REPLAN, or ``None``.
        note: Optional free-text context.
    """

    event: Literal["action.reported"] = "action.reported"
    run_id: str
    step_id: str
    evaluation_id: str
    intended_action: NextAction
    reported_action: str
    reported_provenance: EvidenceProvenance = EvidenceProvenance.CLAIMED
    observed_action: str | None = None
    observed_provenance: EvidenceProvenance | None = None
    new_contract_id: str | None = None
    note: str | None = None


class PolicyProposedEvent(_LineageEventBase):
    """Event: a policy was proposed (todo 7.1 policy lifecycle).

    The first policy-lifecycle transition: an authored ``bound-policy.yaml``
    has been parsed into a :class:`~bound.policy_schema.BoundPolicyConfig` but
    not yet validated, approved, or activated. Records the policy id, version,
    and canonical hash so the lineage proves *which* policy was proposed.

    Attributes:
        event: The literal tag ``"policy.proposed"``.
        run_id: Owning run id.
        policy_id: The policy identifier.
        policy_version: The policy version label.
        policy_hash: Canonical policy hash (``"sha256:<hex>"``).
        contract_hash: SHA-256 hex of the contract, or ``None``.
        note: Optional free-text context.
    """

    event: Literal["policy.proposed"] = "policy.proposed"
    run_id: str
    policy_id: str
    policy_version: str
    policy_hash: str
    contract_hash: str | None = None
    note: str | None = None


class PolicyValidatedEvent(_LineageEventBase):
    """Event: a proposed policy was validated (todo 7.1 policy lifecycle).

    The policy passed schema validation and structural checks (duplicate IDs,
    viable collectors, budget dimensions). Records the same id/version/hash so
    the lineage proves the validated policy is identical to the proposed one.

    Attributes:
        event: The literal tag ``"policy.validated"``.
        run_id: Owning run id.
        policy_id: The policy identifier.
        policy_version: The policy version label.
        policy_hash: Canonical policy hash (``"sha256:<hex>"``).
        contract_hash: SHA-256 hex of the contract, or ``None``.
        note: Optional free-text context.
    """

    event: Literal["policy.validated"] = "policy.validated"
    run_id: str
    policy_id: str
    policy_version: str
    policy_hash: str
    contract_hash: str | None = None
    note: str | None = None


class PolicyApprovedEvent(_LineageEventBase):
    """Event: a validated policy was approved by a human (todo 7.1).

    The policy-lifecycle transition that makes a policy eligible for
    activation. Records the approver and the approval time so the lineage proves
    *who* approved *what* and *when*. Only an approved policy may be activated
    and control decisions.

    Attributes:
        event: The literal tag ``"policy.approved"``.
        run_id: Owning run id.
        policy_id: The policy identifier.
        policy_version: The policy version label.
        policy_hash: Canonical policy hash (``"sha256:<hex>"``).
        approver: The human/approver identifier who approved the policy.
        approved_at: UTC instant the approval was granted.
        contract_hash: SHA-256 hex of the contract, or ``None``.
        note: Optional free-text context.
    """

    event: Literal["policy.approved"] = "policy.approved"
    run_id: str
    policy_id: str
    policy_version: str
    policy_hash: str
    approver: str
    approved_at: UTCDateTime
    contract_hash: str | None = None
    note: str | None = None


class PolicyActivatedEvent(_LineageEventBase):
    """Event: an approved policy was activated (todo 7.1 policy lifecycle).

    The final policy-lifecycle transition: the policy is now the *active*
    policy that governs decisions. Only an activated policy controls BOUND
    decisions; an agent cannot weaken or replace the active policy mid-run.

    Attributes:
        event: The literal tag ``"policy.activated"``.
        run_id: Owning run id.
        policy_id: The policy identifier.
        policy_version: The policy version label.
        policy_hash: Canonical policy hash (``"sha256:<hex>"``).
        contract_hash: SHA-256 hex of the contract, or ``None``.
        note: Optional free-text context.
    """

    event: Literal["policy.activated"] = "policy.activated"
    run_id: str
    policy_id: str
    policy_version: str
    policy_hash: str
    contract_hash: str | None = None
    note: str | None = None


class EvaluationCompletedEvent(_LineageEventBase):
    """Event: an evaluation finished (todo 7.1).

    Distinct from ``evaluation_recorded`` (which records the deterministic
    calculation), ``evaluation.completed`` marks the end of the full evaluation
    process — evidence collection, scoring, assurance gating, and the final
    decision. It carries the complete trace-contents payload (todo 7.2): policy
    id/version/hash, contract hash, collector versions, effective weights,
    raw/effective evidence values, candidate and final decisions, and assurance.

    Attributes:
        event: The literal tag ``"evaluation.completed"``.
        run_id: Owning run id.
        step_id: Step the evaluation belongs to.
        evaluation_id: The evaluation that completed.
        policy_id: Identifier of the governing policy, or ``None``.
        policy_version: Version label of the governing policy, or ``None``.
        policy_hash: Canonical policy hash (``"sha256:<hex>"``), or ``None``.
        contract_hash: SHA-256 hex of the contract, or ``None``.
        candidate_decision: Score-based decision before assurance gating.
        final_decision: Decision after assurance gating.
        assurance: The :class:`~bound.models.DecisionAssurance` level.
        reason_code: The :class:`ReasonCode`, or ``None``.
        collector_versions: Collector name -> version mapping, or ``None``.
        effective_weights: Resolved weights as a dict, or ``None``.
        raw_evidence_values: Raw evidence values per check id, or ``None``.
        effective_evidence_values: Policy-adjusted effective values, or ``None``.
        note: Optional free-text context.
    """

    event: Literal["evaluation.completed"] = "evaluation.completed"
    run_id: str
    step_id: str
    evaluation_id: str
    policy_id: str | None = None
    policy_version: str | None = None
    policy_hash: str | None = None
    contract_hash: str | None = None
    candidate_decision: Decision | None = None
    final_decision: Decision | None = None
    assurance: DecisionAssurance | None = None
    reason_code: ReasonCode | None = None
    collector_versions: dict[str, str] | None = None
    effective_weights: dict[str, float] | None = None
    raw_evidence_values: dict[str, float | None] | None = None
    effective_evidence_values: dict[str, float] | None = None
    note: str | None = None


class ActionObservedEvent(_LineageEventBase):
    """Event: an independent hook observed the agent's action (todo 7.1/7.3).

    Complements ``action.reported`` (which is the agent's CLAIMED self-report):
    an independent integration hook confirms what the agent actually did. This
    is the proof that upgrades a ROLLBACK from *claimed* to *verified* — without
    an ``action.observed`` event, a ROLLBACK stays CLAIMED.

    When ``reported_action`` is supplied, ``matches_reported`` records whether
    the observed action agrees with what the agent reported, so action
    mismatches are explicit in the trace (todo 7.3: record action mismatches).

    Attributes:
        event: The literal tag ``"action.observed"``.
        run_id: Owning run id.
        step_id: Step the action was taken for.
        evaluation_id: The evaluation that triggered the action.
        intended_action: The control action BOUND mapped.
        observed_action: What the independent hook observed.
        observed_provenance: Provenance of the observation.
        reported_action: What the agent reported, or ``None`` (for mismatch
            detection).
        matches_reported: Whether observed agrees with reported, or ``None``.
        new_contract_id: New contract id from a REPLAN, or ``None``.
        note: Optional free-text context.
    """

    event: Literal["action.observed"] = "action.observed"
    run_id: str
    step_id: str
    evaluation_id: str
    intended_action: NextAction
    observed_action: str
    observed_provenance: EvidenceProvenance
    reported_action: str | None = None
    matches_reported: bool | None = None
    new_contract_id: str | None = None
    note: str | None = None


class StepCompletedEvent(_LineageEventBase):
    """Event: a step finished (todo 7.1).

    Marks that a step attempt completed — distinct from ``step_started`` which
    begins the attempt. Carries the step's terminal outcome so the trace
    reconstructs the full attempt lifecycle without inferring it.

    Attributes:
        event: The literal tag ``"step.completed"``.
        run_id: Owning run id.
        step_id: The step that completed.
        outcome: The terminal outcome description, or ``None``.
        note: Optional free-text context.
    """

    event: Literal["step.completed"] = "step.completed"
    run_id: str
    step_id: str
    outcome: str | None = None
    note: str | None = None


#: Discriminated union of every lineage event. ``event`` is the discriminator
#: tag, so a single :func:`parse_lineage_event` call routes a JSONL record to
#: the correct concrete event type.
LineageEvent = Annotated[
    (
        RunStartedEvent
        | PolicyProposedEvent
        | PolicyValidatedEvent
        | PolicyApprovedEvent
        | PolicyActivatedEvent
        | StepStartedEvent
        | EvaluationRecordedEvent
        | EvaluationCompletedEvent
        | OutcomeRecordedEvent
        | RunFinishedEvent
        | EvidenceCollectedEvent
        | EvidenceCollectionFailedEvent
        | DecisionGatedEvent
        | ActionReportedEvent
        | ActionObservedEvent
        | StepCompletedEvent
    ),
    Field(discriminator="event"),
]

_EVENT_ADAPTER: TypeAdapter[LineageEvent] = TypeAdapter(LineageEvent)


def parse_lineage_event(data: str | bytes | dict[str, object]) -> LineageEvent:
    """Parse one lineage event from a JSON string, bytes, or dict.

    Routes on the ``event`` discriminator tag to the correct concrete event
    type and validates it strictly (``extra='forbid'``, UTC timestamps,
    fixed reason codes). Use this to read one line of an ``events.jsonl``
    log.

    Args:
        data: A JSON string/bytes (one event) or an already-decoded dict.

    Returns:
        The validated concrete event instance.

    Raises:
        pydantic.ValidationError: If ``data`` is not a valid lineage event.
    """
    if isinstance(data, dict):
        return _EVENT_ADAPTER.validate_python(data)
    return _EVENT_ADAPTER.validate_json(data)
