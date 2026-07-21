"""Shared application/service layer for BOUND (v0.8.0).

Typed request/response models and service classes that encapsulate
orchestration logic.  Every service method is a pure operation that:

* accepts a typed **request** dataclass (or plain kwargs for simple lookups);
* returns a typed **response** dataclass (never ``None``);
* raises a typed **error** (never ``print``, ``sys.exit``, or ``logging`` for control flow);
* imports the same domain models as the CLI, MCP, hooks, and UI.

Services never write to ``stdout``/``stderr``, never call ``sys.exit``,
and never parse ``argparse.Namespace`` — that is the adapter's job.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, SkipValidation

from bound.evaluator import StaticEvaluator
from bound.evidence import EvidenceProvenance
from bound.lineage import (
    ReasonCode,
    RunStatus,
    generate_evaluation_id,
    generate_step_id,
)
from bound.lineage_store import (
    LineageStore,
    RunLog,
    RunNotFound,
    RunSummary,
    get_default_store,
)
from bound.models import (
    Action,
    BoundCriteria,
    CodingWorkflowSignals,
    EvaluationResult,
    EvaluationScores,
)
from bound.policy import BoundPolicy
from bound.policy_canon import compute_policy_hash
from bound.policy_schema import (
    BoundPolicyConfig,
    HardGate,
    WeightedSignal,
    load_policy_yaml,
)
from bound.prompt import generate_prompt
from bound.workflow import CodingWorkflowEvaluator

from bound.lineage_api import RunContext

if TYPE_CHECKING:
    from bound.contracts import BoundPlan, ContractGenerator


# =========================================================================
# Typed errors
# =========================================================================


class ServiceError(Exception):
    """Base class for all service-layer errors."""


class PolicyLoadError(ServiceError):
    """The policy file could not be loaded or parsed."""


class PolicyValidationError(ServiceError):
    """The policy file failed schema validation."""


class RunNotFoundError(ServiceError):
    """The referenced lineage run does not exist."""


class EvaluationInputError(ServiceError):
    """The evaluation inputs failed validation."""


class CheckpointError(ServiceError):
    """A checkpoint operation could not be completed safely.

    Raised when the working-tree state cannot be captured, saved, or
    restored without risking data loss (e.g. dirty index, unstaged changes
    that cannot be stashed, or I/O errors during snapshot serialisation).
    """


# =========================================================================
# Request / Response models
# =========================================================================

# --- Policy service ---


class PolicyValidateRequest(BaseModel):
    """Request to validate a policy YAML file.

    Attributes:
        path: Absolute or relative path to the ``bound-policy.yaml`` file.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    path: str


class PolicyIdentity(BaseModel):
    """Identity of a policy config.

    Attributes:
        id: Policy identifier.
        version: Policy version label.
        hash: Canonical hash (``\"sha256:<hex>\"``).
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    id: str
    version: str
    hash: str


class PolicyValidateResponse(BaseModel):
    """Result of validating a policy file.

    Attributes:
        valid: ``True`` when the file parsed and validated.
        policy: The :class:`PolicyIdentity` when valid, else ``None``.
        warnings: List of human-readable warning strings (may be empty).
        errors: List of human-readable error strings (empty when valid).
        error_kind: ``\"usage\"`` (file not found), ``\"invalid\"`` (schema
            failure), or ``None`` when valid.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    valid: bool
    policy: PolicyIdentity | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    error_kind: Literal["usage", "invalid"] | None = None


class PolicyExplainRequest(BaseModel):
    """Request to explain a policy file.

    Attributes:
        path: Absolute or relative path to the ``bound-policy.yaml`` file.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    path: str


class PolicyExplainResponse(BaseModel):
    """Result of explaining a policy.

    Attributes:
        policy: The :class:`PolicyIdentity`.
        collectors: Optional serialized collector configs.
        acceptance_checks: Serialized acceptance-check list.
        quality_checks: Serialized weighted-signal list.
        risk_checks: Serialized risk-check list.
        budgets: Serialized budget dimensions.
        change_scope: Serialized change-scope config.
        approvals: Serialized approvals config.
        human_readable: Multi-line human-readable explanation.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    policy: PolicyIdentity
    collectors: dict[str, Any] = Field(default_factory=dict)
    acceptance_checks: list[dict[str, Any]] = Field(default_factory=list)
    quality_checks: list[dict[str, Any]] = Field(default_factory=list)
    risk_checks: list[dict[str, Any]] = Field(default_factory=list)
    budgets: dict[str, Any] = Field(default_factory=dict)
    change_scope: dict[str, Any] = Field(default_factory=dict)
    approvals: dict[str, Any] = Field(default_factory=dict)
    human_readable: str = ""


class PolicyHashRequest(BaseModel):
    """Request to compute the hash of a policy file.

    Attributes:
        path: Absolute or relative path to the ``bound-policy.yaml`` file.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    path: str


class PolicyHashResponse(BaseModel):
    """Result of hashing a policy.

    Attributes:
        hash: The canonical hash string (``\"sha256:<hex>\"``).
        policy: The :class:`PolicyIdentity`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    hash: str
    policy: PolicyIdentity


# --- Run service ---


class RunStartRequest(BaseModel):
    """Request to start a new lineage run.

    Attributes:
        task: Natural-language task description.
        metadata: Optional free-form string metadata.
        store: Optional explicit store; defaults to the process-wide store.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    task: str
    metadata: dict[str, str] | None = None
    store: LineageStore | None = None


class RunStartResponse(BaseModel):
    """Result of starting a run.

    Attributes:
        run_id: The generated run id.
        task: The task description.
        started_at: ISO-8601 UTC timestamp.
        status: The run status string.
        schema_version: The lineage schema version.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    run_id: str
    task: str
    started_at: str
    status: str
    schema_version: str


class RunFinishRequest(BaseModel):
    """Request to finish a lineage run.

    Attributes:
        run_id: The run to finish.
        status: Finish status (default ``\"completed\"``).
        note: Optional note.
        store: Optional explicit store.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    run_id: str
    status: str = "completed"
    note: str | None = None
    store: LineageStore | None = None


class RunFinishResponse(BaseModel):
    """Result of finishing a run.

    Attributes:
        run_id: The run id.
        status: The finish status.
        finished_at: ISO-8601 UTC timestamp.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    run_id: str
    status: str
    finished_at: str


class RunDeleteRequest(BaseModel):
    """Request to delete a lineage run.

    Attributes:
        run_id: The run to delete.
        store: Optional explicit store.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    run_id: str
    store: LineageStore | None = None


class RunDeleteResponse(BaseModel):
    """Result of deleting a run.

    Attributes:
        run_id: The deleted run id.
        deleted: Always ``True`` on success.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    run_id: str
    deleted: bool = True


class RunListRequest(BaseModel):
    """Request to list all lineage runs.

    Attributes:
        store: Optional explicit store.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    store: LineageStore | None = None


class RunListResponse(BaseModel):
    """Result of listing runs.

    Attributes:
        runs: Ordered list of :class:`~bound.lineage_store.RunSummary` objects
            (newest first).
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    runs: list[RunSummary]


class RunInspectRequest(BaseModel):
    """Request to inspect a lineage run.

    Attributes:
        run_id: The run to inspect.
        only_unverified: When ``True``, filter checks to unverified/claimed/
            missing/invalid evidence only.
        store: Optional explicit store.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    run_id: str
    only_unverified: bool = False
    store: LineageStore | None = None


class RunInspectResponse(BaseModel):
    """Result of inspecting a run.

    Attributes:
        log: The full :class:`~bound.lineage_store.RunLog`.
        html: Self-contained HTML timeline (only when requested).
        json_payload: Machine-readable JSON dict.
        tree: Human-readable decision-tree string.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    log: RunLog
    html: str | None = None
    json_payload: dict[str, Any] | None = None
    tree: str | None = None


# --- Evaluation service ---


class EvaluateRequest(BaseModel):
    """Request to evaluate an action with pre-supplied scores.

    Attributes:
        action: The proposed :class:`Action`.
        scores: The :class:`EvaluationScores` for the action.
        criteria: The :class:`BoundCriteria` (threshold, weights, retry margin).
        run_id: Optional run id for lineage recording.
        step: Optional step id/contract id for lineage.
        attempt: Optional attempt number (default 1).
        description: Optional step description for lineage.
        store: Optional explicit store.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    action: Action
    scores: EvaluationScores
    criteria: SkipValidation[BoundCriteria]
    run_id: str | None = None
    step: str | None = None
    attempt: int = 1
    description: str | None = None
    store: LineageStore | None = None


class EvaluateResponse(BaseModel):
    """Result of evaluating an action.

    Attributes:
        result: The :class:`EvaluationResult` from the policy.
        prompt: The deterministic steering prompt.
        payload: The auditable JSON-serialisable dict.
        lineage: Optional lineage recording info when ``run_id`` was supplied.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    result: EvaluationResult
    prompt: str
    payload: dict[str, Any]
    lineage: dict[str, Any] | None = None


class EvaluateWorkflowRequest(BaseModel):
    """Request to evaluate using coding-workflow signals.

    Attributes:
        action: The proposed :class:`Action`.
        signals: The :class:`CodingWorkflowSignals`.
        criteria: The :class:`BoundCriteria` (threshold, weights, retry margin).
        influence: Influence score (default 0.0).
        run_id: Optional run id for lineage recording.
        step: Optional step id/contract id for lineage.
        attempt: Optional attempt number (default 1).
        description: Optional step description for lineage.
        store: Optional explicit store.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    action: Action
    signals: CodingWorkflowSignals
    criteria: SkipValidation[BoundCriteria]
    influence: float = 0.0
    run_id: str | None = None
    step: str | None = None
    attempt: int = 1
    description: str | None = None
    store: LineageStore | None = None


class EvaluateWorkflowResponse(BaseModel):
    """Result of evaluating using coding-workflow signals.

    Attributes:
        result: The :class:`EvaluationResult` from the policy.
        prompt: The deterministic steering prompt.
        payload: The auditable JSON-serialisable dict.
        signals: The input :class:`CodingWorkflowSignals` serialised.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    result: EvaluationResult
    prompt: str
    payload: dict[str, Any]
    signals: dict[str, Any]


# --- Outcome service ---


class OutcomeRecordRequest(BaseModel):
    """Request to record an outcome for a step evaluation.

    Attributes:
        run_id: The owning run.
        step_id: The step id the outcome applies to.
        evaluation_id: The evaluation id the outcome applies to.
        decision: The BOUND decision (``ACCEPT``, ``RETRY``, etc.).
        next_action: Optional control action (derived from decision when omitted).
        reason_code: Optional :class:`~bound.lineage.ReasonCode` (derived when
            omitted).
        note: Optional free-text note.
        store: Optional explicit store.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    run_id: str
    step_id: str
    evaluation_id: str
    decision: str
    next_action: str | None = None
    reason_code: str | None = None
    note: str | None = None
    store: LineageStore | None = None


class OutcomeRecordResponse(BaseModel):
    """Result of recording an outcome.

    Attributes:
        run_id: The run id.
        step_id: The step id.
        evaluation_id: The evaluation id.
        decision: The decision.
        next_action: The control action.
        reason_code: The reason code (string form).
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    run_id: str
    step_id: str
    evaluation_id: str
    decision: str
    next_action: str
    reason_code: str


# --- Evidence service ---


class EvidenceCollectRequest(BaseModel):
    """Request to record collected evidence for a step evaluation.

    Attributes:
        run_id: The owning run.
        step_id: The step the evidence belongs to.
        evaluation_id: The evaluation the evidence belongs to.
        check_id: The check identifier.
        provenance: The :class:`~bound.evidence.EvidenceProvenance`.
        passed: Whether the check passed.
        status: Optional evidence status string.
        collector: Optional collector name.
        collector_version: Optional collector version.
        source: Optional source description.
        artifact_hash: Optional artifact hash.
        observed_at: Optional observation timestamp.
        store: Optional explicit store.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    run_id: str
    step_id: str
    evaluation_id: str
    check_id: str
    provenance: EvidenceProvenance
    passed: bool
    status: str | None = None
    collector: str | None = None
    collector_version: str | None = None
    source: str | None = None
    artifact_hash: str | None = None
    observed_at: datetime | None = None
    store: LineageStore | None = None


class EvidenceCollectResponse(BaseModel):
    """Result of recording collected evidence.

    Attributes:
        event_id: The generated event id.
        run_id: The run id.
        check_id: The check identifier.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    event_id: str
    run_id: str
    check_id: str


# --- Boundary / policy-config evaluation ---


class BoundaryEvaluateRequest(BaseModel):
    """Request to evaluate a step against a full policy config.

    Attributes:
        contract: The :class:`StepContract` for the executed step.
        evidence: The :class:`ExecutionEvidence` observed.
        criteria: The :class:`BoundCriteria`.
        policy_config: Optional active :class:`BoundPolicyConfig`.
        run: Optional :class:`~bound.lineage_api.RunContext` for lineage.
        attempt: One-based attempt number.
        step_id: Optional explicit step id.
        description: Optional step description.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    contract: 'StepContract'
    evidence: 'ExecutionEvidence'
    criteria: SkipValidation[BoundCriteria]
    policy_config: BoundPolicyConfig | None = None
    run: RunContext | None = None
    attempt: int = 1
    step_id: str | None = None
    description: str | None = None


class BoundaryEvaluateResponse(BaseModel):
    """Result of a boundary evaluation.

    Attributes:
        result: The :class:`EvaluationResult`.
        next_action: The mapped control action.
        feedback: Deterministic feedback string.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    result: EvaluationResult
    next_action: str
    feedback: str


# --- Checkpoint service ---


class CheckpointCreateRequest(BaseModel):
    """Request to create a BOUND-owned checkpoint.

    Attributes:
        run_id: The owning run id.
        step_id: The step this checkpoint belongs to.
        scope: Optional allowed path prefixes to restrict the checkpoint.
        message: Optional checkpoint message (stored in metadata).
        store: Optional explicit lineage store.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    run_id: str
    step_id: str
    scope: list[str] | None = None
    message: str | None = None
    store: LineageStore | None = None


class CheckpointCreateResponse(BaseModel):
    """Result of creating a checkpoint.

    Attributes:
        run_id: The run id.
        step_id: The step id.
        checkpoint_id: The checkpoint identifier.
        path: Path to the stored checkpoint file.
        changed_files_count: Number of changed files captured.
        untracked_files_count: Number of untracked files captured.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    run_id: str
    step_id: str
    checkpoint_id: str
    path: str
    changed_files_count: int = 0
    untracked_files_count: int = 0


class CheckpointInspectRequest(BaseModel):
    """Request to inspect a checkpoint.

    Attributes:
        run_id: The owning run id.
        checkpoint_id: The checkpoint to inspect.
        store: Optional explicit lineage store.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    run_id: str
    checkpoint_id: str
    store: LineageStore | None = None


class CheckpointInspectResponse(BaseModel):
    """Result of inspecting a checkpoint.

    Attributes:
        checkpoint_id: The checkpoint identifier.
        run_id: The run id.
        step_id: The step id.
        head_commit: The HEAD commit at checkpoint time.
        branch: The branch at checkpoint time.
        timestamp: UTC timestamp of checkpoint creation.
        changed_files: List of changed file entries.
        untracked_files: List of untracked file paths.
        scope: The allowed path prefixes.
        artifact_hashes_count: Number of artifact hashes recorded.
        metadata: Optional key-value metadata.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    checkpoint_id: str
    run_id: str
    step_id: str
    head_commit: str | None = None
    branch: str | None = None
    timestamp: str = ""
    changed_files: list[dict[str, Any]] = Field(default_factory=list)
    untracked_files: list[str] = Field(default_factory=list)
    scope: list[str] = Field(default_factory=list)
    artifact_hashes_count: int = 0
    metadata: dict[str, str] = Field(default_factory=dict)


class CheckpointListRequest(BaseModel):
    """Request to list checkpoints for a run.

    Attributes:
        run_id: The run to list checkpoints for.
        store: Optional explicit lineage store.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    run_id: str
    store: LineageStore | None = None


class CheckpointListResponse(BaseModel):
    """Result of listing checkpoints.

    Attributes:
        run_id: The run id.
        checkpoint_ids: List of checkpoint ids (newest first).
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    run_id: str
    checkpoint_ids: list[str] = Field(default_factory=list)


class CheckpointRollbackRequest(BaseModel):
    """Request to verify rollback readiness against a checkpoint.

    BOUND does **not** execute rollback — it only signals ROLLBACK.
    This request verifies that a checkpoint exists and is valid for the
    agent to roll back to.  The agent is responsible for execution.

    Attributes:
        run_id: The owning run id.
        checkpoint_id: The checkpoint to verify against.
        store: Optional explicit lineage store.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    run_id: str
    checkpoint_id: str
    store: LineageStore | None = None


class CheckpointRollbackResponse(BaseModel):
    """Result of rollback readiness verification.

    Attributes:
        run_id: The run id.
        checkpoint_id: The checkpoint verified.
        is_valid: Whether the checkpoint is valid for rollback.
        issues: List of issues found during verification.
        preview: Optional dict with checkpoint verification data.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    run_id: str
    checkpoint_id: str
    is_valid: bool = False
    issues: list[str] = Field(default_factory=list)
    preview: dict[str, Any] | None = None
# =========================================================================
# Internal helpers (used by services, also reusable from adapters)
# =========================================================================

_DECISION_NEXT_ACTION: dict[str, str] = {
    "ACCEPT": "continue",
    "RETRY": "retry",
    "REPLAN": "replan",
    "ROLLBACK": "rollback",
}

_NEXT_ACTION_REASON: dict[str, ReasonCode] = {
    "continue": ReasonCode.CONTINUED,
    "retry": ReasonCode.RETRIED,
    "replan": ReasonCode.REPLANNED,
    "rollback": ReasonCode.ROLLED_BACK,
}

_DECISION_TO_EVAL_REASON: dict[str, ReasonCode] = {
    "ACCEPT": ReasonCode.ACCEPT,
    "RETRY": ReasonCode.RETRY,
    "REPLAN": ReasonCode.REPLAN,
    "ROLLBACK": ReasonCode.ROLLBACK,
}


_POLICY_ACTION_TO_DECISION: dict[str, str] = {
    "accept": "ACCEPT",
    "retry": "RETRY",
    "replan": "REPLAN",
    "rollback": "ROLLBACK",
}


_DECISION_SEVERITY: dict[str, int] = {
    "ACCEPT": 0,
    "RETRY": 1,
    "REPLAN": 2,
    "ROLLBACK": 3,
}


_ACCEPT_BLOCKING_ASSURANCE: frozenset[str] = frozenset(
    {"CLAIMED", "INSUFFICIENT"}
)


_INDEPENDENTLY_VERIFIED: frozenset[EvidenceProvenance] = frozenset({
    EvidenceProvenance.VERIFIED,
    EvidenceProvenance.OBSERVED,
    EvidenceProvenance.ATTESTED,
})


_PROVENANCE_STRENGTH: dict[EvidenceProvenance, int] = {
    EvidenceProvenance.VERIFIED: 60,
    EvidenceProvenance.OBSERVED: 50,
    EvidenceProvenance.ATTESTED: 40,
    EvidenceProvenance.EVALUATED: 30,
    EvidenceProvenance.CLAIMED: 20,
    EvidenceProvenance.DEFAULTED: 10,
    EvidenceProvenance.MISSING: 0,
}


_PROVENANCE_COLORS: dict[str, str] = {
    "verified": "#2e7d32",
    "observed": "#1976d2",
    "attested": "#6a1b9a",
    "evaluated": "#ef6c00",
    "claimed": "#c62828",
    "defaulted": "#8d6e63",
    "missing": "#9e9e9e",
    "unverified": "#9e9e9e",
}


_DECISION_COLORS: dict[str, str] = {
    "ACCEPT": "#2e7d32",
    "RETRY": "#ef6c00",
    "REPLAN": "#1565c0",
    "ROLLBACK": "#c62828",
}


def _load_policy_file(path: str) -> tuple[BoundPolicyConfig | None, str | None, str | None]:
    """Load and validate a ``bound-policy.yaml`` file from ``path``.

    Returns ``(policy, error, error_kind)``.  ``error`` and ``error_kind``
    are ``None`` when the file parses and validates cleanly.

    Args:
        path: Path to the policy YAML file.

    Returns:
        ``(policy, None, None)`` on success or
        ``(None, error_message, error_kind)`` on failure.
    """
    try:
        policy = load_policy_yaml(path)
    except FileNotFoundError:
        return None, f"policy file not found: {path}", "usage"
    except ValidationError as exc:
        return None, _format_validation_error(exc), "invalid"
    except ValueError as exc:
        return None, str(exc), "invalid"
    except yaml.YAMLError as exc:
        return None, f"invalid YAML: {exc}", "invalid"
    return policy, None, None


def _format_validation_error(exc: ValidationError) -> str:
    """Render a Pydantic ``ValidationError`` as a concise multi-line message."""
    lines: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err.get("loc", ()))
        msg = err.get("msg", "")
        lines.append(f"  {loc}: {msg}" if loc else f"  {msg}")
    return "; ".join(lines) if lines else str(exc)


def _policy_identity(policy: BoundPolicyConfig) -> PolicyIdentity:
    """Build a :class:`PolicyIdentity` from a validated policy config.

    Args:
        policy: A validated :class:`BoundPolicyConfig`.

    Returns:
        A :class:`PolicyIdentity` with id, version, and canonical hash.
    """
    return PolicyIdentity(
        id=policy.policy.id,
        version=policy.policy.version,
        hash=compute_policy_hash(policy),
    )


def _policy_warnings(policy: BoundPolicyConfig) -> list[str]:
    """Return validation warnings about a policy's checks.

    The schema is *syntactically* valid, but a policy can still encode
    decisions that BOUND cannot independently back.  These warnings surface
    blockers/signals that bind no collector (unmeasurable), checks that
    reference an unknown collector, checks relying *only* on CLAIMED evidence,
    and subjective checks.

    Args:
        policy: A validated :class:`BoundPolicyConfig`.

    Returns:
        An ordered list of warning strings (may be empty).
    """
    warnings: list[str] = []
    collector_ids = set(policy.collectors)

    def _check(
        check_id: str,
        collector: str | None,
        *,
        is_blocker: bool,
        accepted: list[EvidenceProvenance] | None,
    ) -> None:
        if collector is None:
            kind = "blocker" if is_blocker else "check"
            warnings.append(
                f"{kind} '{check_id}' binds no collector \u2014 BOUND cannot "
                "independently collect this evidence"
            )
        elif collector not in collector_ids:
            kind = "blocker" if is_blocker else "check"
            warnings.append(
                f"{kind} '{check_id}' references unknown collector "
                f"'{collector}' \u2014 this evidence will always be MISSING"
            )
        acc = _provenance_set(accepted)
        if acc == {EvidenceProvenance.CLAIMED}:
            warnings.append(
                f"check '{check_id}' accepts only CLAIMED evidence \u2014 "
                "this is agent self-report with no independent verification"
            )
        if collector is None and (not acc or EvidenceProvenance.EVALUATED in acc):
            warnings.append(
                f"check '{check_id}' appears subjective/unmeasurable; consider "
                "evaluating it in a separate human/judge step rather than a gate"
            )

    for gate in policy.acceptance_checks:
        _check(gate.id, gate.collector, is_blocker=True, accepted=gate.accepted_provenance)
    for gate in policy.risk_checks:
        _check(gate.id, gate.collector, is_blocker=True, accepted=gate.accepted_provenance)
    for sig in policy.quality_checks:
        if sig.importance == "ignore":
            continue
        _check(sig.id, sig.collector, is_blocker=False, accepted=sig.accepted_provenance)
    return warnings


def _provenance_set(values: list[EvidenceProvenance] | None) -> set[EvidenceProvenance]:
    """Return the set of accepted provenance values (empty when ``None``)."""
    return set(values) if values is not None else set()


def _get_store(store: LineageStore | None) -> LineageStore:
    """Resolve the store: explicit or process-wide default.

    Args:
        store: An explicit store or ``None``.

    Returns:
        A :class:`~bound.lineage_store.LineageStore` instance.
    """
    return store if store is not None else get_default_store()


def _build_evaluate_payload(result: EvaluationResult) -> dict[str, Any]:
    """Build the auditable JSON payload from an :class:`EvaluationResult`.

    Args:
        result: The :class:`EvaluationResult` to serialise.

    Returns:
        A JSON-serialisable dict with every term of the BOUND score.
    """
    payload: dict[str, Any] = {
        "scores": {
            "acceptance": result.scores.acceptance,
            "influence": result.scores.influence,
            "risk": result.scores.risk,
            "cost": result.scores.cost,
        },
        "weights": {
            "acceptance": result.weights.acceptance,
            "influence": result.weights.influence,
            "risk": result.weights.risk,
            "cost": result.weights.cost,
        },
        "weight": result.weights.acceptance,
        "threshold": result.threshold,
        "retry_margin": result.retry_margin,
        "rollback_risk_threshold": result.rollback_risk_threshold,
        "acceptance_component": result.acceptance_component,
        "influence_component": result.influence_component,
        "risk_component": result.risk_component,
        "cost_component": result.cost_component,
        "score": result.score,
        "distance_to_threshold": result.distance_to_threshold,
        "decision": result.decision,
    }
    if result.provenance is not None:
        payload["provenance"] = {
            dimension: [
                evidence.model_dump() if hasattr(evidence, "model_dump") else str(evidence)
                for evidence in evidence_list
            ]
            for dimension, evidence_list in result.provenance.items()
        }
    if result.candidate_decision is not None:
        payload["candidate_decision"] = result.candidate_decision
    if result.final_decision is not None:
        payload["final_decision"] = result.final_decision
    if result.assurance is not None:
        v = result.assurance.value if hasattr(result.assurance, "value") else result.assurance
        payload["assurance"] = v
    if result.active_policy_id is not None:
        payload["policy_id"] = result.active_policy_id
    if result.active_policy_version is not None:
        payload["policy_version"] = result.active_policy_version
    if result.active_policy_hash is not None:
        payload["policy_hash"] = result.active_policy_hash
    return payload


def _html_escape(text: str) -> str:
    """Escape a string for safe inclusion in HTML text content."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _sv(value: object) -> str:
    """Return the string value of an enum member or a plain string."""
    return value.value if hasattr(value, "value") else str(value)


def _checks_summary(evaluation: Evaluation) -> str:
    """Derive an ``n/total checks`` summary from the evaluation's reason code."""
    if evaluation.reason_code == ReasonCode.ALL_CHECKS_PASSED:
        return "3/3 checks"
    return "1/3 checks"


def _fmt_dt(dt: datetime | None) -> str:
    """Format a UTC datetime for human-readable output."""
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC") if dt else "-"


def _gate_summary_line(gate: HardGate) -> str:
    """Render one hard gate as a single human-readable summary line.

    Args:
        gate: The :class:`HardGate` to render.

    Returns:
        A single-line summary string.
    """
    parts = [f"- {gate.id}", f"[{gate.importance}]"]
    if gate.required:
        parts.append("required")
    parts.append(f"on_failure={gate.on_failure}")
    parts.append(f"on_missing={gate.on_missing}")
    parts.append(f"on_claimed={gate.on_claimed}")
    if gate.minimum_assurance is not None:
        parts.append(f"minimum_assurance={gate.minimum_assurance}")
    if gate.accepted_provenance is not None:
        provs = ",".join(p.value for p in gate.accepted_provenance)
        parts.append(f"accepted_provenance=[{provs}]")
    if gate.collector is not None:
        parts.append(f"collector={gate.collector}")
    return "  ".join(parts)


def _signal_summary_line(sig: WeightedSignal) -> str:
    """Render one weighted signal as a single human-readable summary line.

    Args:
        sig: The :class:`WeightedSignal` to render.

    Returns:
        A single-line summary string.
    """
    parts = [f"- {sig.id}", f"[{sig.importance}]"]
    override = f" (override {sig.weight})" if sig.weight is not None else ""
    parts.append(f"effective_weight={sig.effective_weight}{override}")
    if sig.accepted_provenance is not None:
        provs = ",".join(p.value for p in sig.accepted_provenance)
        parts.append(f"accepted_provenance=[{provs}]")
    if sig.collector is not None:
        parts.append(f"collector={sig.collector}")
    return "  ".join(parts)


def _budget_summary_line(name: str, dim: Any) -> str:
    """Render one budget dimension as a single human-readable summary line.

    Args:
        name: The dimension name.
        dim: The budget dimension config.

    Returns:
        A single-line summary string.
    """
    parts = [f"- {name}"]
    if not dim.enabled:
        parts.append("disabled")
    soft = dim.soft_limit if dim.soft_limit is not None else "-"
    hard = dim.hard_limit if dim.hard_limit is not None else "-"
    parts.append(f"soft={soft}")
    parts.append(f"on_soft={dim.on_soft}")
    parts.append(f"hard={hard}")
    parts.append(f"on_hard={dim.on_hard}")
    return "  ".join(parts)


def _policy_identity_json(policy: BoundPolicyConfig) -> dict[str, object]:
    """Return the ``{id, version, hash}`` identity dict for a policy."""
    return {
        "id": policy.policy.id,
        "version": policy.policy.version,
        "hash": compute_policy_hash(policy),
    }


def _check_json(event: EvidenceCollectedEvent) -> dict[str, object]:
    """Serialize one collected-evidence event for the inspect JSON payload."""
    return {
        "check_id": event.check_id,
        "provenance": event.provenance.value,
        "passed": event.passed,
        "status": event.status.value if event.status else None,
        "collector": event.collector,
        "collector_version": event.collector_version,
        "source": event.source,
        "artifact_hash": event.artifact_hash,
        "observed_at": event.observed_at.isoformat() if event.observed_at else None,
        "independently_verified": event.provenance in _INDEPENDENTLY_VERIFIED,
    }


def _policy_from_run(config: Any) -> dict[str, object] | None:
    """Extract the policy identity from a run config snapshot."""
    if config is None or config.policy_id is None:
        return None
    return {
        "id": config.policy_id,
        "version": config.policy_version,
        "hash": config.policy_hash,
    }


# =========================================================================
# Services
# =========================================================================


class PolicyService:
    """Service for policy validation, explanation, and hashing.

    Every method returns a typed response; none print to stdout/stderr.
    """

    @staticmethod
    def validate(request: PolicyValidateRequest) -> PolicyValidateResponse:
        """Validate a policy YAML file.

        Args:
            request: The :class:`PolicyValidateRequest`.

        Returns:
            A :class:`PolicyValidateResponse` with validation results.
        """
        policy, error, error_kind = _load_policy_file(request.path)
        if error is not None:
            return PolicyValidateResponse(
                valid=False,
                errors=[error],
                error_kind=error_kind,  # type: ignore[arg-type]
            )
        identity = _policy_identity(policy)
        warnings = _policy_warnings(policy)
        return PolicyValidateResponse(
            valid=True,
            policy=identity,
            warnings=warnings,
            errors=[],
        )

    @staticmethod
    def explain(request: PolicyExplainRequest) -> PolicyExplainResponse:
        """Explain a policy file's effective gates, weights, and budgets.

        Args:
            request: The :class:`PolicyExplainRequest`.

        Returns:
            A :class:`PolicyExplainResponse`.

        Raises:
            PolicyLoadError: If the file cannot be loaded.
            PolicyValidationError: If the file fails schema validation.
        """
        policy, error, error_kind = _load_policy_file(request.path)
        if error is not None:
            if error_kind == "usage":
                raise PolicyLoadError(error)
            raise PolicyValidationError(error)

        identity = _policy_identity(policy)
        human_readable = _render_explain(policy)
        return PolicyExplainResponse(
            policy=identity,
            collectors={
                name: c.model_dump(mode="json")
                for name, c in policy.collectors.items()
            },
            acceptance_checks=[
                g.model_dump(mode="json") for g in policy.acceptance_checks
            ],
            quality_checks=[
                s.model_dump(mode="json") for s in policy.quality_checks
            ],
            risk_checks=[
                g.model_dump(mode="json") for g in policy.risk_checks
            ],
            budgets={
                n: d.model_dump(mode="json")
                for n, d in policy.budgets.items()
            },
            change_scope=policy.change_scope.model_dump(mode="json"),
            approvals=policy.approvals.model_dump(mode="json"),
            human_readable=human_readable,
        )

    @staticmethod
    def hash(request: PolicyHashRequest) -> PolicyHashResponse:
        """Compute the canonical hash of a policy file.

        Args:
            request: The :class:`PolicyHashRequest`.

        Returns:
            A :class:`PolicyHashResponse`.

        Raises:
            PolicyLoadError: If the file cannot be loaded.
            PolicyValidationError: If the file fails schema validation.
        """
        policy, error, error_kind = _load_policy_file(request.path)
        if error is not None:
            if error_kind == "usage":
                raise PolicyLoadError(error)
            raise PolicyValidationError(error)

        identity = _policy_identity(policy)
        return PolicyHashResponse(hash=identity.hash, policy=identity)


# ---------------------------------------------------------------------------
# Policy explain rendering
# ---------------------------------------------------------------------------


def _render_explain(policy: BoundPolicyConfig) -> str:
    """Build a human-readable explanation of a policy config.

    Args:
        policy: The validated :class:`BoundPolicyConfig`.

    Returns:
        A multi-line human-readable explanation string.
    """
    out: list[str] = []
    out.append(f"Policy: {policy.policy.id}@{policy.policy.version}")
    out.append(f"Hash: {compute_policy_hash(policy)}")
    out.append("")

    if policy.collectors:
        out.append("Collectors:")
        for name, c in policy.collectors.items():
            cmd = c.command if c.command else "(none)"
            interval = f" timeout {c.timeout_seconds}s" if c.timeout_seconds else ""
            out.append(f"  - {name}: {cmd}{interval}")
        out.append("")

    out.append("Acceptance checks:")
    for g in policy.acceptance_checks:
        out.append(f"  {_gate_summary_line(g)}")
    out.append("")

    out.append("Quality signals (weighted):")
    for sig in policy.quality_checks:
        out.append(f"  {_signal_summary_line(sig)}")
    out.append("")

    out.append("Risk checks:")
    for g in policy.risk_checks:
        out.append(f"  {_gate_summary_line(g)}")
    out.append("")

    if any(d.enabled for d in policy.budgets.values()):
        out.append("Budgets:")
        for name, dim in policy.budgets.items():
            out.append(f"  {_budget_summary_line(name, dim)}")
        out.append("")

    scope = policy.change_scope
    out.append("Change scope:")
    if scope.allowed_paths:
        out.append(f"  allowed: {', '.join(scope.allowed_paths)}")
    else:
        out.append("  allowed: (any)")
    if scope.forbidden_paths:
        out.append(f"  forbidden: {', '.join(scope.forbidden_paths)}")
    if scope.dependency_file_patterns:
        out.append(f"  dependency files: {', '.join(scope.dependency_file_patterns)}")
    out.append("")

    appr = policy.approvals
    out.append("Approvals:")
    if appr.commands_requiring_approval:
        out.append(f"  requiring approval: {', '.join(appr.commands_requiring_approval)}")
    if appr.destructive_actions:
        out.append(f"  destructive: {', '.join(appr.destructive_actions)}")
    out.append(
        f"  require_rollback_availability={appr.require_rollback_availability} "
        f"on_missing_rollback={appr.on_missing_rollback}"
    )

    return "\n".join(out)


class RunService:
    """Service for managing lineage runs.

    Every method returns a typed response; none print to stdout/stderr.
    """

    @staticmethod
    def start(request: RunStartRequest) -> RunStartResponse:
        """Start a new lineage run.

        Args:
            request: The :class:`RunStartRequest`.

        Returns:
            A :class:`RunStartResponse` with the new run's identity.
        """
        store = _get_store(request.store)
        event = store.start_run(request.task, metadata=request.metadata)
        return RunStartResponse(
            run_id=event.run_id,
            task=event.task,
            started_at=event.timestamp.isoformat(),
            status=RunStatus.STARTED.value,
            schema_version=event.schema_version,
        )

    @staticmethod
    def finish(request: RunFinishRequest) -> RunFinishResponse:
        """Finish a lineage run.

        Args:
            request: The :class:`RunFinishRequest`.

        Returns:
            A :class:`RunFinishResponse`.

        Raises:
            RunNotFoundError: If the run does not exist.
        """
        store = _get_store(request.store)
        try:
            event = store.finish_run(
                request.run_id, status=request.status, note=request.note
            )
        except RunNotFound as exc:
            raise RunNotFoundError(str(exc)) from exc
        return RunFinishResponse(
            run_id=request.run_id,
            status=request.status,
            finished_at=event.timestamp.isoformat(),
        )

    @staticmethod
    def list_runs(request: RunListRequest) -> RunListResponse:
        """List all lineage runs.

        Args:
            request: The :class:`RunListRequest`.

        Returns:
            A :class:`RunListResponse` with the runs (newest first).
        """
        store = _get_store(request.store)
        summaries = store.list_runs()
        return RunListResponse(runs=summaries)

    @staticmethod
    def delete(request: RunDeleteRequest) -> RunDeleteResponse:
        """Delete a lineage run.

        Args:
            request: The :class:`RunDeleteRequest`.

        Returns:
            A :class:`RunDeleteResponse`.

        Raises:
            RunNotFoundError: If the run does not exist.
        """
        store = _get_store(request.store)
        try:
            store.delete_run(request.run_id)
        except RunNotFound as exc:
            raise RunNotFoundError(str(exc)) from exc
        return RunDeleteResponse(run_id=request.run_id)

    @staticmethod
    def inspect(request: RunInspectRequest) -> RunInspectResponse:
        """Inspect a lineage run.

        Args:
            request: The :class:`RunInspectRequest`.

        Returns:
            A :class:`RunInspectResponse` with the full log.

        Raises:
            RunNotFoundError: If the run does not exist.
        """
        store = _get_store(request.store)
        try:
            log = store.read_run(request.run_id)
        except RunNotFound as exc:
            raise RunNotFoundError(str(exc)) from exc
        return RunInspectResponse(log=log)


class EvaluationService:
    """Service for evaluating actions and workflow signals.

    Every method returns a typed response; none print to stdout/stderr.
    """

    @staticmethod
    def evaluate(request: EvaluateRequest) -> EvaluateResponse:
        """Evaluate an action with pre-supplied scores.

        Args:
            request: The :class:`EvaluateRequest`.

        Returns:
            An :class:`EvaluateResponse`.

        Raises:
            EvaluationInputError: If the inputs fail validation.
        """
        try:
            policy = BoundPolicy(StaticEvaluator(request.scores))
            result = policy.evaluate(request.action, request.criteria)
        except ValueError as exc:
            raise EvaluationInputError(str(exc)) from exc

        payload = _build_evaluate_payload(result)
        prompt = generate_prompt(result)

        lineage_info = None
        if request.run_id is not None:
            lineage_info = EvaluationService._record_evaluation(
                run_id=request.run_id,
                step=request.step or "default",
                attempt=request.attempt,
                description=request.description,
                result=result,
                store=request.store,
            )

        return EvaluateResponse(
            result=result,
            prompt=prompt,
            payload=payload,
            lineage=lineage_info,
        )

    @staticmethod
    def evaluate_workflow(
        request: EvaluateWorkflowRequest,
    ) -> EvaluateWorkflowResponse:
        """Evaluate using coding-workflow signals.

        Args:
            request: The :class:`EvaluateWorkflowRequest`.

        Returns:
            An :class:`EvaluateWorkflowResponse`.

        Raises:
            EvaluationInputError: If the inputs fail validation.
        """
        try:
            evaluator = CodingWorkflowEvaluator(
                request.signals, influence=request.influence
            )
            policy = BoundPolicy(evaluator)
            result = policy.evaluate(request.action, request.criteria)
        except (ValueError, ValidationError) as exc:
            raise EvaluationInputError(str(exc)) from exc

        payload = _build_evaluate_payload(result)
        payload["signals"] = request.signals.model_dump()
        prompt = generate_prompt(result)

        lineage_info = None
        if request.run_id is not None:
            lineage_info = EvaluationService._record_evaluation(
                run_id=request.run_id,
                step=request.step or "default",
                attempt=request.attempt,
                description=request.description,
                result=result,
                store=request.store,
            )

        return EvaluateWorkflowResponse(
            result=result,
            prompt=prompt,
            payload=payload,
            signals=request.signals.model_dump(),
        )

    @staticmethod
    def _record_evaluation(
        *,
        run_id: str,
        step: str,
        attempt: int,
        description: str | None,
        result: EvaluationResult,
        store: LineageStore | None,
    ) -> dict[str, Any]:
        """Record step_started + evaluation_recorded lineage for a run.

        Args:
            run_id: The owning run.
            step: The step/contract id.
            attempt: The attempt number.
            description: Optional step description.
            result: The evaluation result.
            store: Optional explicit store.

        Returns:
            A dict with lineage recording info.

        Raises:
            RunNotFoundError: If the run does not exist.
        """
        resolved_store = _get_store(store)
        try:
            resolved_store.read_run(run_id)
        except RunNotFound as exc:
            raise RunNotFoundError(str(exc)) from exc

        step_id = generate_step_id(
            run_id=run_id, contract_id=step, attempt=attempt
        )
        resolved_store.start_step(
            run_id,
            contract_id=step,
            attempt=attempt,
            step_id=step_id,
            description=description,
        )
        evaluation_id = generate_evaluation_id(
            run_id=run_id, step_id=step_id, attempt=attempt
        )
        resolved_store.record_evaluation(
            run_id,
            step_id=step_id,
            attempt=attempt,
            scores=result.scores,
            score=result.score,
            threshold=result.threshold,
            decision=result.decision,
            reason_code=_DECISION_TO_EVAL_REASON.get(
                result.decision, ReasonCode.ACCEPT
            ),
            evaluation_id=evaluation_id,
        )
        return {
            "run_id": run_id,
            "step_id": step_id,
            "evaluation_id": evaluation_id,
            "attempt": attempt,
        }


class OutcomeService:
    """Service for recording outcomes in lineage.

    Every method returns a typed response; none print to stdout/stderr.
    """

    @staticmethod
    def record(request: OutcomeRecordRequest) -> OutcomeRecordResponse:
        """Record an outcome for a step evaluation.

        Args:
            request: The :class:`OutcomeRecordRequest`.

        Returns:
            An :class:`OutcomeRecordResponse`.

        Raises:
            RunNotFoundError: If the run does not exist.
        """
        store = _get_store(request.store)
        try:
            store.read_run(request.run_id)
        except RunNotFound as exc:
            raise RunNotFoundError(str(exc)) from exc

        next_action = request.next_action or _DECISION_NEXT_ACTION.get(
            request.decision, "retry"
        )
        reason_code = request.reason_code or str(
            _NEXT_ACTION_REASON.get(next_action, ReasonCode.RETRIED)
        )

        store.record_outcome(
            request.run_id,
            step_id=request.step_id,
            evaluation_id=request.evaluation_id,
            decision=request.decision,
            next_action=next_action,
            reason_code=reason_code,
            note=request.note,
        )
        return OutcomeRecordResponse(
            run_id=request.run_id,
            step_id=request.step_id,
            evaluation_id=request.evaluation_id,
            decision=request.decision,
            next_action=next_action,
            reason_code=str(reason_code),
        )


class EvidenceService:
    """Service for recording evidence collection in lineage.

    Every method returns a typed response; none print to stdout/stderr.
    """

    @staticmethod
    def collect(request: EvidenceCollectRequest) -> EvidenceCollectResponse:
        """Record a collected-evidence event in lineage.

        Args:
            request: The :class:`EvidenceCollectRequest`.

        Returns:
            An :class:`EvidenceCollectResponse`.

        Raises:
            RunNotFoundError: If the run does not exist.
        """
        store = _get_store(request.store)

        # Guard: the run must exist before we can append events to it.
        try:
            store.read_run(request.run_id)
        except RunNotFound as exc:
            raise RunNotFoundError(str(exc)) from exc

        event = store.record_evidence_collected(
            run_id=request.run_id,
            step_id=request.step_id,
            check_id=request.check_id,
            provenance=request.provenance,
            passed=request.passed,
            status=request.status,
            collector=request.collector,
            collector_version=request.collector_version,
            source=request.source,
            artifact_hash=request.artifact_hash,
            observed_at=request.observed_at,
        )
        return EvidenceCollectResponse(
            event_id=event.event_id,
            run_id=request.run_id,
            check_id=request.check_id,
        )


class BoundaryService:
    """Service for evaluating steps against a full policy configuration.

    Wraps :class:`~bound.bound_workflow.BoundWorkflow` in a typed service
    interface so adapters (CLI, MCP, hooks, UI) can evaluate steps without
    importing the workflow internals.
    """

    @staticmethod
    def evaluate(
        request: BoundaryEvaluateRequest,
    ) -> BoundaryEvaluateResponse:
        """Evaluate an executed step against its contract and policy config.

        Args:
            request: The :class:`BoundaryEvaluateRequest`.

        Returns:
            A :class:`BoundaryEvaluateResponse`.

        Raises:
            EvaluationInputError: If the evaluation fails.
        """
        from bound.bound_workflow import BoundWorkflow
        from bound.integration import _DECISION_TO_ACTION, render_feedback

        try:
            workflow = BoundWorkflow()
            result = workflow.evaluate_step(
                contract=request.contract,
                evidence=request.evidence,
                criteria=request.criteria,
                policy=request.policy_config,
                run=request.run,
                attempt=request.attempt,
                step_id=request.step_id,
                description=request.description,
            )
        except (ValueError, ValidationError) as exc:
            raise EvaluationInputError(str(exc)) from exc

        next_action = _DECISION_TO_ACTION.get(result.decision, "retry")
        feedback = render_feedback(
            result, contract=request.contract, evidence=request.evidence
        )

        return BoundaryEvaluateResponse(
            result=result,
            next_action=next_action,
            feedback=feedback,
        )

    @staticmethod
    def prepare(
        goal: str,
        plan: BoundPlan,
        *,
        generator: ContractGenerator | None = None,
    ) -> BoundPlan:
        """Prepare a validated plan from a goal.

        Args:
            goal: The natural-language goal.
            plan: The initial :class:`BoundPlan`.
            generator: Optional :class:`ContractGenerator`.

        Returns:
            The validated :class:`BoundPlan`.
        """
        from bound.bound_workflow import BoundWorkflow
        from bound.contracts import StaticContractGenerator

        wf = BoundWorkflow(
            contract_generator=generator or StaticContractGenerator(),
        )
        return wf.prepare(goal=goal, plan=plan)  # type: ignore[arg-type]


class CheckpointService:
    """Service for creating, inspecting, and restoring BOUND checkpoints.

    Every method returns a typed response; none print to stdout/stderr.

    Checkpoints are stored under ``.bound/checkpoints/<run_id>/`` as
    JSON files.  Rollback is **scoped**: only files recorded in the
    checkpoint are touched, and unrelated/pre-existing changes are
    **never** discarded.

    The implementation **never** uses ``git reset --hard``.
    """

    @staticmethod
    def create(
        request: CheckpointCreateRequest,
    ) -> CheckpointCreateResponse:
        """Create a BOUND-owned checkpoint for a run step.

        Args:
            request: The :class:`CheckpointCreateRequest`.

        Returns:
            A :class:`CheckpointCreateResponse`.

        Raises:
            RunNotFoundError: If the run does not exist.
            CheckpointError: If the state cannot be captured safely.
        """
        from bound.checkpoint import capture_checkpoint, save_checkpoint

        store = _get_store(request.store)
        try:
            store.read_run(request.run_id)
        except RunNotFound as exc:
            raise RunNotFoundError(str(exc)) from exc

        try:
            cp = capture_checkpoint(
                run_id=request.run_id,
                step_id=request.step_id,
                scope=request.scope,
                metadata={"message": request.message} if request.message else None,
            )
        except RuntimeError as exc:
            raise CheckpointError(str(exc)) from exc

        saved_path = save_checkpoint(cp)
        return CheckpointCreateResponse(
            run_id=request.run_id,
            step_id=request.step_id,
            checkpoint_id=cp.checkpoint_id,
            path=str(saved_path),
            changed_files_count=len(cp.changed_files),
            untracked_files_count=len(cp.untracked_files),
        )

    @staticmethod
    def rollback(
        request: CheckpointRollbackRequest,
    ) -> CheckpointRollbackResponse:
        """Roll back to a previously created checkpoint.

        Args:
            request: The :class:`CheckpointRollbackRequest`.

        Returns:
            A :class:`CheckpointRollbackResponse`.

        Raises:
            RunNotFoundError: If the run does not exist.
            CheckpointError: If the checkpoint cannot be loaded or restored.
        """
        from bound.checkpoint import (
            load_checkpoint,
            verify_checkpoint_integrity,
            restore_checkpoint_files,
            compute_rollback_preview,
        )

        store = _get_store(request.store)
        try:
            store.read_run(request.run_id)
        except RunNotFound as exc:
            raise RunNotFoundError(str(exc)) from exc

        try:
            cp = load_checkpoint(request.run_id, request.checkpoint_id)
        except FileNotFoundError as exc:
            raise CheckpointError(str(exc)) from exc
        except RuntimeError as exc:
            raise CheckpointError(str(exc)) from exc

        # Verify integrity first
        is_valid, issues = verify_checkpoint_integrity(cp)
        if not is_valid:
            return CheckpointRollbackResponse(
                run_id=request.run_id,
                checkpoint_id=request.checkpoint_id,
                is_valid=False,
                issues=issues,
            )

        # Compute preview
        preview = compute_rollback_preview(cp)

        # Restore files
        try:
            restored, failed = restore_checkpoint_files(cp)
        except RuntimeError as exc:
            return CheckpointRollbackResponse(
                run_id=request.run_id,
                checkpoint_id=request.checkpoint_id,
                is_valid=False,
                issues=[str(exc)],
                preview=preview,
            )

        all_issues = list(issues)
        if failed:
            all_issues.append(
                f"Failed to restore {len(failed)} file(s): {', '.join(failed[:5])}"
            )

        return CheckpointRollbackResponse(
            run_id=request.run_id,
            checkpoint_id=request.checkpoint_id,
            is_valid=len(failed) == 0,
            issues=all_issues,
            preview=preview,
        )
    @staticmethod
    def inspect(
        request: CheckpointInspectRequest,
    ) -> CheckpointInspectResponse:
        """Inspect a checkpoint's details.

        Args:
            request: The :class:`CheckpointInspectRequest`.

        Returns:
            A :class:`CheckpointInspectResponse`.

        Raises:
            RunNotFoundError: If the run does not exist.
            CheckpointError: If the checkpoint cannot be loaded.
        """
        from bound.checkpoint import load_checkpoint

        store = _get_store(request.store)
        try:
            store.read_run(request.run_id)
        except RunNotFound as exc:
            raise RunNotFoundError(str(exc)) from exc

        try:
            cp = load_checkpoint(request.run_id, request.checkpoint_id)
        except FileNotFoundError as exc:
            raise CheckpointError(str(exc)) from exc
        except RuntimeError as exc:
            raise CheckpointError(str(exc)) from exc

        return CheckpointInspectResponse(
            checkpoint_id=cp.checkpoint_id,
            run_id=cp.run_id,
            step_id=cp.step_id,
            head_commit=cp.head_commit,
            branch=cp.branch,
            timestamp=cp.timestamp,
            changed_files=[
                {"path": f.path, "status": f.status, "content_hash": f.content_hash}
                for f in cp.changed_files
            ],
            untracked_files=list(cp.untracked_files),
            scope=list(cp.scope),
            artifact_hashes_count=len(cp.artifact_hashes),
            metadata=dict(cp.metadata),
        )

    @staticmethod
    def list_checkpoints(
        request: CheckpointListRequest,
    ) -> CheckpointListResponse:
        """List all checkpoints for a run.

        Args:
            request: The :class:`CheckpointListRequest`.

        Returns:
            A :class:`CheckpointListResponse`.

        Raises:
            RunNotFoundError: If the run does not exist.
        """
        from bound.checkpoint import list_checkpoints as _list_checkpoints

        store = _get_store(request.store)
        try:
            store.read_run(request.run_id)
        except RunNotFound as exc:
            raise RunNotFoundError(str(exc)) from exc

        checkpoint_ids = _list_checkpoints(request.run_id)
        return CheckpointListResponse(
            run_id=request.run_id,
            checkpoint_ids=checkpoint_ids,
        )
