"""BOUND — a deterministic bounded-utility policy for agentic systems.

The package is organised into focused modules:

* :mod:`bound.models` — Pydantic domain models (Action, EvaluationScores, ...).
* :mod:`bound.calculator` — the pure mathematical ``S = (W × A) + I - R - C`` core.
* :mod:`bound.evaluator` — the pluggable evaluator abstraction.
* :mod:`bound.policy` — the deterministic decision policy.
* :mod:`bound.prompt` — deterministic steering-prompt rendering.
* :mod:`bound.cli` — the command-line entrypoint.

The BOUND core is deliberately provider-agnostic and deterministic: once
evaluation scores are supplied, every downstream calculation is reproducible
and requires no network access or LLM SDK.
"""

from bound.models import (
    Action,
    BoundCriteria,
    Decision,
    EvaluationResult,
    EvaluationScores,
)

__all__ = [
    "Action",
    "BoundCriteria",
    "Decision",
    "EvaluationResult",
    "EvaluationScores",
]

__version__ = "0.1.0"
