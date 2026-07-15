"""BOUND — a deterministic bounded-utility policy for agentic systems.

The package is organised into focused modules:

* :mod:`bound.models` — Pydantic domain models (Action, EvaluationScores,
  BoundWeights, CodingWorkflowSignals, ...).
* :mod:`bound.calculator` — the pure mathematical
  ``S = (W_A×A) + (W_I×I) - (W_R×R) - (W_C×C)`` core.
* :mod:`bound.evaluator` — the pluggable evaluator abstraction.
* :mod:`bound.policy` — the deterministic decision policy.
* :mod:`bound.workflow` — the deterministic CodingWorkflowEvaluator.
* :mod:`bound.prompt` — deterministic steering-prompt rendering.
* :mod:`bound.cli` — the command-line entrypoint.

The BOUND core is deliberately provider-agnostic and deterministic: once
evaluation scores are supplied, every downstream calculation is reproducible
and requires no network access or LLM SDK.
"""

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
    "Action",
    "AgentStep",
    "AgentTrajectory",
    "BoundCriteria",
    "BoundWeights",
    "CodingWorkflowSignals",
    "Decision",
    "EvaluationResult",
    "EvaluationScores",
    "ScoreEvidence",
    "WorkflowNormalization",
]

__version__ = "0.2.0"
