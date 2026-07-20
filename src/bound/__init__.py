"""BOUND — a deterministic bounded-utility policy for agentic systems.

The package is organised into focused modules:

* :mod:`bound.models` — Pydantic domain models (Action, EvaluationScores,
  BoundWeights, CodingWorkflowSignals, ...).
* :mod:`bound.contracts` — v0.3 evaluation contracts (AcceptanceCheck,
  RiskCheck, StepBudget, StepContract, BoundPlan) and the ContractGenerator
  Protocol with its dependency-free StaticContractGenerator.
* :mod:`bound.evidence` — v0.3 evidence models (CheckEvidence,
  ExecutionEvidence) and the environment-agnostic EvidenceCollector Protocol.
* :mod:`bound.contract_evaluator` — v0.3 deterministic ContractEvaluator that
  turns a StepContract + ExecutionEvidence into A / I / R / C with provenance.
* :mod:`bound.bound_workflow` — v0.3 BoundWorkflow orchestration
  (prepare + evaluate_step) wiring the contract pipeline end-to-end.
* :mod:`bound.contract_quality` — v0.3 ContractQualityReport + the
  automatic-contract experiment (structural, no LLM).
* :mod:`bound.llm_adapters` — documented (import-free) seam for optional LLM
  contract generators; never a mandatory dependency of the core.
* :mod:`bound.calculator` — the pure mathematical
  ``S = (W_A×A) + (W_I×I) - (W_R×R) - (W_C×C)`` core.
* :mod:`bound.evaluator` — the pluggable evaluator abstraction.
* :mod:`bound.policy` — the deterministic decision policy.
* :mod:`bound.workflow` — the deterministic CodingWorkflowEvaluator.
* :mod:`bound.prompt` — deterministic steering-prompt rendering.
* :mod:`bound.cli` — the command-line entrypoint.

The BOUND core is deliberately provider-agnostic and deterministic: once
evaluation scores are supplied, every downstream calculation is reproducible
and requires no network access or LLM SDK. v0.3 contract generation follows the
same rule — the :class:`~bound.contracts.ContractGenerator` seam is
provider-agnostic and ships a deterministic
:class:`~bound.contracts.StaticContractGenerator`; any LLM adapter is optional
and lives outside the core (see :mod:`bound.llm_adapters`). The package works
entirely without an LLM: LLM-based contract generation is an *optional*
convenience layer, never a requirement.
"""

from bound.bound_workflow import BoundWorkflow
from bound.command_collector import (
    BudgetCollector,
    BudgetMetrics,
    CommandCollector,
    CommandResult,
    CommandSpec,
    GitCollector,
    JUnitCollector,
    ProcessRuntimeCollector,
    PytestCollector,
    Redactor,
    default_redactor,
    sha256_hex,
)
from bound.contract_evaluator import (
    AssuranceAssessment,
    ContractEvaluator,
)
from bound.contract_quality import ContractQualityReport
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
from bound.evidence import (
    CheckEvidence,
    EvidenceCollector,
    EvidenceMetric,
    EvidenceProvenance,
    EvidenceStatus,
    ExecutionEvidence,
    migrate_legacy_execution_evidence,
)
from bound.integration import (
    AgentControlResult,
    NextAction,
    evaluate_agent_step,
    render_feedback,
)
from bound.integration_spec import integration_spec
from bound.lineage import (
    EVENT_NAMES,
    LINEAGE_SCHEMA_VERSION,
    ActionReportedEvent,
    Attempt,
    DecisionGatedEvent,
    Evaluation,
    EvaluationRecordedEvent,
    EvidenceCollectedEvent,
    EvidenceCollectionFailedEvent,
    LineageEvent,
    Outcome,
    OutcomeRecordedEvent,
    ReasonCode,
    Run,
    RunConfigSnapshot,
    RunFinishedEvent,
    RunFinishStatus,
    RunStartedEvent,
    RunStatus,
    Step,
    StepStartedEvent,
    StepStatus,
    UTCDateTime,
    build_run_config,
    compute_policy_config_hash,
    generate_evaluation_id,
    generate_event_id,
    generate_run_id,
    generate_step_id,
    parse_lineage_event,
    utc_now,
)
from bound.lineage_api import (
    RunContext,
    finish_run,
    record_outcome,
    record_step_evaluation,
    start_run,
)
from bound.lineage_store import (
    DEFAULT_MAX_EVENT_BYTES,
    DEFAULT_MAX_FILE_BYTES,
    DEFAULT_MAX_RUNS,
    DEFAULT_RETENTION_DAYS,
    DEFAULT_RUNS_DIR,
    LineageCorruptEvent,
    LineageEventTooLarge,
    LineageFileTooLarge,
    LineageStore,
    LineageStoreError,
    RunLog,
    RunNotFound,
    RunSummary,
    configure,
    get_default_store,
    register_redactor,
    scrub_secrets,
)
from bound.models import (
    Action,
    AgentStep,
    AgentTrajectory,
    BoundCriteria,
    BoundWeights,
    CodingWorkflowSignals,
    Decision,
    DecisionAssurance,
    EvaluationResult,
    EvaluationScores,
    ScoreEvidence,
    WorkflowNormalization,
)
from bound.policy_canon import (
    canonicalize_policy,
    compute_contract_hash,
    compute_policy_hash,
    policy_changed_since,
)
from bound.policy_schema import (
    BUDGET_DIMENSIONS,
    DEFAULT_WEIGHTS,
    POLICY_SCHEMA_VERSION,
    ApprovalsPolicy,
    BoundPolicyConfig,
    BudgetDimension,
    ChangeScope,
    CollectorConfig,
    HardGate,
    PolicyIdentity,
    UnexpectedArtifactsPolicy,
    WeightedSignal,
    load_policy_yaml,
    parse_policy_yaml,
    policy_json_schema,
)

__all__ = [
    "AcceptanceCheck",
    "Action",
    "AgentControlResult",
    "AgentStep",
    "AgentTrajectory",
    "Attempt",
    "BoundCriteria",
    "BoundPlan",
    "BoundPolicyConfig",
    "BoundWeights",
    "BoundWorkflow",
    "BUDGET_DIMENSIONS",
    "BudgetDimension",
    "ApprovalsPolicy",
    "ChangeScope",
    "CheckEvidence",
    "CodingWorkflowSignals",
    "CollectorConfig",
    "ContractEvaluator",
    "ContractGenerator",
    "ContractQualityReport",
    "DEFAULT_MAX_EVENT_BYTES",
    "DEFAULT_MAX_FILE_BYTES",
    "DEFAULT_RUNS_DIR",
    "Decision",
    "DecisionAssurance",
    "EVENT_NAMES",
    "Evaluation",
    "EvaluationRecordedEvent",
    "EvaluationResult",
    "EvaluationScores",
    "EvidenceCollector",
    "EvidenceMetric",
    "EvidencePolicyAction",
    "EvidenceProvenance",
    "EvidenceStatus",
    "ExecutionEvidence",
    "LINEAGE_SCHEMA_VERSION",
    "LineageCorruptEvent",
    "LineageEvent",
    "LineageEventTooLarge",
    "LineageFileTooLarge",
    "LineageStore",
    "LineageStoreError",
    "NextAction",
    "Outcome",
    "OutcomeRecordedEvent",
    "ReasonCode",
    "RiskCheck",
    "Run",
    "RunContext",
    "RunFinishedEvent",
    "RunFinishStatus",
    "RunLog",
    "RunNotFound",
    "RunStartedEvent",
    "RunStatus",
    "RunSummary",
    "ScoreEvidence",
    "StaticContractGenerator",
    "Step",
    "StepBudget",
    "StepContract",
    "StepStartedEvent",
    "StepStatus",
    "UTCDateTime",
    "WorkflowNormalization",
    "evaluate_agent_step",
    "configure",
    "finish_run",
    "generate_event_id",
    "generate_evaluation_id",
    "generate_run_id",
    "generate_step_id",
    "get_default_store",
    "integration_spec",
    "migrate_legacy_execution_evidence",
    "parse_lineage_event",
    "record_outcome",
    "record_step_evaluation",
    "register_redactor",
    "render_feedback",
    "scrub_secrets",
    "start_run",
    "utc_now",
    # --- v0.7.0 verified-evidence additions (parallel wave) ---
    "AssuranceAssessment",
    "ActionReportedEvent",
    "DecisionGatedEvent",
    "EvidenceCollectedEvent",
    "EvidenceCollectionFailedEvent",
    "RunConfigSnapshot",
    "build_run_config",
    "compute_contract_hash",
    "compute_policy_config_hash",
    "DEFAULT_MAX_RUNS",
    "DEFAULT_RETENTION_DAYS",
    "BudgetCollector",
    "BudgetMetrics",
    "CommandCollector",
    "CommandResult",
    "CommandSpec",
    "GitCollector",
    "JUnitCollector",
    "ProcessRuntimeCollector",
    "PytestCollector",
    "Redactor",
    "default_redactor",
    "sha256_hex",
    # --- v0.7.0 policy configuration schema + canonicalisation (todo 2/4) ---
    "DEFAULT_WEIGHTS",
    "POLICY_SCHEMA_VERSION",
    "HardGate",
    "PolicyIdentity",
    "UnexpectedArtifactsPolicy",
    "WeightedSignal",
    "load_policy_yaml",
    "parse_policy_yaml",
    "policy_json_schema",
    "canonicalize_policy",
    "compute_policy_hash",
    "policy_changed_since",
]

__version__ = "0.7.1"
