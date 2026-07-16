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
from bound.contract_evaluator import ContractEvaluator
from bound.contract_quality import ContractQualityReport
from bound.contracts import (
    AcceptanceCheck,
    BoundPlan,
    ContractGenerator,
    RiskCheck,
    StaticContractGenerator,
    StepBudget,
    StepContract,
)
from bound.evidence import (
    CheckEvidence,
    EvidenceCollector,
    ExecutionEvidence,
)
from bound.integration import (
    AgentControlResult,
    NextAction,
    evaluate_agent_step,
    render_feedback,
)
from bound.integration_spec import integration_spec
from bound.models import (
    Action,
    AgentStep,
    AgentTrajectory,
    BoundCriteria,
    BoundWeights,
    CodingWorkflowSignals,
    Decision,
    EvaluationResult,
    EvaluationScores,
    ScoreEvidence,
    WorkflowNormalization,
)

__all__ = [
    "AcceptanceCheck",
    "Action",
    "AgentControlResult",
    "AgentStep",
    "AgentTrajectory",
    "BoundCriteria",
    "BoundPlan",
    "BoundWeights",
    "BoundWorkflow",
    "CheckEvidence",
    "CodingWorkflowSignals",
    "ContractEvaluator",
    "ContractGenerator",
    "ContractQualityReport",
    "Decision",
    "EvaluationResult",
    "EvaluationScores",
    "EvidenceCollector",
    "ExecutionEvidence",
    "NextAction",
    "RiskCheck",
    "ScoreEvidence",
    "StaticContractGenerator",
    "StepBudget",
    "StepContract",
    "WorkflowNormalization",
    "evaluate_agent_step",
    "integration_spec",
    "render_feedback",
]

__version__ = "0.4.0"
