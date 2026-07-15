"""Pydantic domain models for the BOUND bounded-utility policy.

This module defines the core data structures that flow through the BOUND
pipeline:

    Action → Evaluator → EvaluationScores → BoundCalculator → BoundPolicy → EvaluationResult

v0.2 adds symmetric weighting (:class:`BoundWeights`), threshold metadata
(``distance_to_threshold``, ``retry_margin``, ``rollback_risk_threshold``),
auditable provenance (:class:`ScoreEvidence`), coding-agent workflow signals
(:class:`CodingWorkflowSignals`, :class:`WorkflowNormalization`) and the
experiment-harness trajectory models (:class:`AgentStep`,
:class:`AgentTrajectory`).

The v0.2 score is ``S = (W_A×A) + (W_I×I) - (W_R×R) - (W_C×C)``. The v0.1
formula ``S = (W×A) + I - R - C`` is reproduced by the default
:class:`BoundWeights` (``W_A = W``, ``W_I = W_R = W_C = 1.0``).

All models are provider-agnostic and deterministic: no network access, no LLM
SDK.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Action(BaseModel):
    """A proposed action to be evaluated by the BOUND policy.

    Attributes:
        description: Human-readable description of the proposed action. Must
            not be empty or whitespace-only.
        goal: The larger goal the action is meant to advance. Must not be
            empty or whitespace-only.
        context: Optional additional context influencing the evaluation.
    """

    model_config = ConfigDict(extra="forbid")

    description: str
    goal: str
    context: str | None = None

    @field_validator("description", "goal")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        """Reject empty or whitespace-only strings.

        A BOUND evaluation is meaningless without a concrete action and goal
        to score against, so both fields must carry actual content.

        Args:
            value: The raw string supplied for the field.

        Returns:
            The validated, unmodified string.

        Raises:
            ValueError: If the string is empty or contains only whitespace.
        """
        if not value or not value.strip():
            raise ValueError("must not be empty or whitespace-only")
        return value


class BoundWeights(BaseModel):
    """Symmetric, non-negative weights for the four BOUND score dimensions.

    The v0.2 score is ``S = (W_A×A) + (W_I×I) - (W_R×R) - (W_C×C)``. Every
    weight defaults to ``1.0`` so the v0.1 formula ``S = (W×A) + I - R - C`` is
    reproduced exactly when only the (deprecated) acceptance ``weight`` is set.

    Attributes:
        acceptance: Weight ``W_A`` applied to the acceptance score ``A``.
        influence: Weight ``W_I`` applied to the downstream influence ``I``.
        risk: Weight ``W_R`` applied to the risk penalty ``R``.
        cost: Weight ``W_C`` applied to the resource cost ``C``.
    """

    model_config = ConfigDict(extra="forbid")

    acceptance: float = Field(default=1.0, ge=0.0)
    influence: float = Field(default=1.0, ge=0.0)
    risk: float = Field(default=1.0, ge=0.0)
    cost: float = Field(default=1.0, ge=0.0)


#: Sentinel equal to the default :class:`BoundWeights`. Used to detect whether a
#: caller supplied a *non-default* ``weights`` so the deprecated scalar ``weight``
#: alias can be reconciled without keeping two competing weight systems.
_DEFAULT_WEIGHTS = BoundWeights()


class BoundCriteria(BaseModel):
    """Threshold and weights for a BOUND evaluation.

    The threshold is intentionally **not** capped at ``1.0``. The final score
    ``S = (W_A×A) + (W_I×I) - (W_R×R) - (W_C×C)`` is unbounded above, so a
    legitimate threshold may exceed ``1.0`` (for example when ``W_A > 1``).

    The deprecated scalar ``weight`` remains accepted as an alias for
    ``weights.acceptance``. Supplying *both* a ``weight`` and a non-default
    ``weights`` is rejected (see :meth:`_reconcile_weights`) so the two weight
    systems can never silently compete.

    Attributes:
        threshold: Minimum score ``T`` required to accept the action
            (``S >= T``). Must be non-negative but may exceed ``1.0``.
        retry_margin: How far below ``T`` a score may fall while still
            justifying a ``RETRY`` rather than a ``REPLAN``. Defaults to ``0.1``.
        rollback_risk_threshold: Hard risk boundary ``[0, 1]`` above which the
            action is rolled back regardless of its score. Defaults to ``0.8``.
        weights: The four :class:`BoundWeights`. Defaults to all-``1.0``.
        weight: Deprecated alias for ``weights.acceptance``. When supplied alone
            (with default ``weights``) it is folded into ``weights.acceptance``.
    """

    model_config = ConfigDict(extra="forbid")

    threshold: float = Field(ge=0.0)
    retry_margin: float = Field(default=0.1, ge=0.0)
    rollback_risk_threshold: float = Field(default=0.8, ge=0.0, le=1.0)
    weights: BoundWeights = Field(default_factory=BoundWeights)
    weight: float | None = Field(default=None, ge=0.0)

    @model_validator(mode="after")
    def _reconcile_weights(self) -> BoundCriteria:
        """Reconcile the deprecated ``weight`` alias with ``weights``.

        Exactly one weight system may be in effect:

        * If ``weight`` is supplied *and* ``weights`` is non-default →
          :class:`ValueError` (two competing weight systems).
        * If ``weight`` is supplied with default ``weights`` → fold ``weight``
          into ``weights.acceptance``.
        * In all cases ``weight`` is kept in sync with ``weights.acceptance`` so
          legacy callers reading ``criteria.weight`` observe the effective
          acceptance weight.

        Returns:
            The reconciled criteria (mutated in place).

        Raises:
            ValueError: If both ``weight`` and a non-default ``weights`` are
                supplied.
        """
        if self.weight is not None and self.weights != _DEFAULT_WEIGHTS:
            raise ValueError(
                "Cannot supply both 'weight' and a non-default 'weights'; "
                "use 'weights' (BoundWeights) only."
            )
        if self.weight is not None:
            self.weights = BoundWeights(acceptance=self.weight)
        self.weight = self.weights.acceptance
        return self


class EvaluationScores(BaseModel):
    """The four BOUND evaluation dimensions produced by an evaluator.

    Attributes:
        acceptance: ``A ∈ [0, 1]`` — how well the action satisfies the goal.
        influence: ``I ∈ [-1, 1]`` — downstream effect on future goals. May be
            negative (penalty) or positive (bonus); it is not a pure penalty.
        risk: ``R ∈ [0, 1]`` — potential downside if the action goes wrong.
        cost: ``C ∈ [0, 1]`` — normalized resource consumption.
        reasoning: Optional human-readable justification for the scores.
    """

    model_config = ConfigDict(extra="forbid")

    acceptance: float = Field(ge=0.0, le=1.0)
    influence: float = Field(ge=-1.0, le=1.0)
    risk: float = Field(ge=0.0, le=1.0)
    cost: float = Field(ge=0.0, le=1.0)
    reasoning: str | None = None


class ScoreEvidence(BaseModel):
    """A single piece of evidence backing a BOUND score dimension.

    Lets a consumer answer "why is A = 0.85?" by listing the underlying signals
    and their contribution. Provenance is optional for manually supplied scores
    but deterministic evaluators should populate it.

    Attributes:
        source: Short, human-readable name of the evidence source (e.g.
            ``"tests"``, ``"lint"``).
        value: The raw value observed for this source.
        contribution: Optional contribution this source made to the final
            dimension score. ``None`` when not applicable.
        description: Optional free-text explanation of the source/value.
    """

    model_config = ConfigDict(extra="forbid")

    source: str
    value: float
    contribution: float | None = None
    description: str | None = None


Decision = Literal["ACCEPT", "RETRY", "REPLAN", "ROLLBACK"]


class EvaluationResult(BaseModel):
    """The auditable outcome of a BOUND evaluation.

    Stores the final score ``S``, its weighted components, the threshold
    metadata, and optional provenance so the calculation
    ``S = (W_A×A) + (W_I×I) - (W_R×R) - (W_C×C)`` can be reconstructed and
    inspected from the result alone.

    The deprecated scalar ``weight`` is retained as an alias for
    ``weights.acceptance`` (see :meth:`_reconcile_weight`), mirroring
    :class:`BoundCriteria` so legacy callers reading ``result.weight`` keep
    working.

    Attributes:
        scores: The original :class:`EvaluationScores`.
        weights: The :class:`BoundWeights` used in the calculation.
        threshold: Acceptance threshold ``T``.
        acceptance_component: ``W_A × A``.
        influence_component: ``W_I × I``.
        risk_component: ``W_R × R``.
        cost_component: ``W_C × C``.
        score: Final bounded utility score ``S`` (unclamped, unrounded).
        distance_to_threshold: Signed ``S - T`` — positive means above
            threshold, zero means exactly at, negative means below.
        decision: The BOUND decision derived from ``S`` vs ``T`` and the risk
            boundary.
        rollback_risk_threshold: Hard risk boundary carried through for audit.
        retry_margin: Retry margin carried through for audit.
        provenance: Optional per-dimension evidence lists.
        weight: Deprecated alias for ``weights.acceptance``.
    """

    model_config = ConfigDict(extra="forbid")

    scores: EvaluationScores
    weights: BoundWeights
    threshold: float
    acceptance_component: float
    influence_component: float
    risk_component: float
    cost_component: float
    score: float
    distance_to_threshold: float
    decision: Decision
    rollback_risk_threshold: float = Field(default=0.8, ge=0.0, le=1.0)
    retry_margin: float = Field(default=0.1, ge=0.0)
    provenance: dict[str, list[ScoreEvidence]] | None = None
    weight: float | None = Field(default=None, ge=0.0)

    @model_validator(mode="after")
    def _reconcile_weight(self) -> EvaluationResult:
        """Reconcile the deprecated ``weight`` alias with ``weights``.

        Mirrors :meth:`BoundCriteria._reconcile_weights`: supplying both a
        ``weight`` and a non-default ``weights`` is rejected; otherwise
        ``weight`` is kept in sync with ``weights.acceptance``.

        Returns:
            The reconciled result (mutated in place).

        Raises:
            ValueError: If both ``weight`` and a non-default ``weights`` are
                supplied.
        """
        if self.weight is not None and self.weights != _DEFAULT_WEIGHTS:
            raise ValueError(
                "Cannot supply both 'weight' and a non-default 'weights'; "
                "use 'weights' (BoundWeights) only."
            )
        if self.weight is not None:
            self.weights = BoundWeights(acceptance=self.weight)
        self.weight = self.weights.acceptance
        return self


class WorkflowNormalization(BaseModel):
    """Explicit caps used to normalize coding-workflow signals to ``[0, 1]``.

    Normalization is configuration-driven (never against hidden constants), e.g.
    ``normalized_tool_calls = min(tool_call_count / max_expected_tool_calls, 1.0)``.

    Attributes:
        max_expected_retries: Cap for retry counts.
        max_expected_tool_calls: Cap for tool-call counts.
        max_expected_tokens: Cap for token usage.
        max_expected_runtime_seconds: Cap for execution time in seconds.
    """

    model_config = ConfigDict(extra="forbid")

    max_expected_retries: int = Field(default=5, ge=0)
    max_expected_tool_calls: int = Field(default=50, ge=0)
    max_expected_tokens: int = Field(default=100_000, ge=0)
    max_expected_runtime_seconds: float = Field(default=3600.0, ge=0.0)


class CodingWorkflowSignals(BaseModel):
    """Provider-agnostic signals collected from a coding-agent workflow.

    All fields are optional so an instance may carry only the signals that were
    actually observed; missing signals are ``None`` rather than silently zero.
    Ranges mirror the BOUND contract (rates in ``[0, 1]``, counts non-negative).

    Attributes:
        test_pass_rate: Fraction of tests passing, ``[0, 1]``.
        lint_passed: Whether the linter is clean.
        type_check_passed: Whether type-checking is clean.
        required_checks_passed: Fraction of required checks passing, ``[0, 1]``.
        retry_count: Number of retries performed so far.
        tool_call_count: Number of tool calls performed so far.
        token_usage: Total tokens consumed.
        execution_time_seconds: Wall-clock execution time in seconds.
        files_changed: Number of files changed.
        unexpected_files_changed: Number of *unexpected* files changed.
        rollback_available: Whether a clean rollback is available.
        tests_added: Number of tests added by the action. Adding tests is good
            evidence, so this is *not* a risk signal; it is recorded for
            auditability only.
        tests_removed: Number of tests removed by the action. Deleting failing
            tests to force a green suite is the canonical "mechanically correct,
            semantically wrong" pattern, so any removal is a strong risk signal.
        tests_modified: Number of tests modified by the action. Modification can
            be legitimate refactoring or a subtle weakening of assertions, so it
            is a *milder* risk signal than removal.
    """

    model_config = ConfigDict(extra="forbid")

    test_pass_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    lint_passed: bool | None = None
    type_check_passed: bool | None = None
    required_checks_passed: float | None = Field(default=None, ge=0.0, le=1.0)

    retry_count: int = Field(default=0, ge=0)
    tool_call_count: int = Field(default=0, ge=0)
    token_usage: int | None = Field(default=None, ge=0)
    execution_time_seconds: float | None = Field(default=None, ge=0.0)

    files_changed: int | None = Field(default=None, ge=0)
    unexpected_files_changed: int | None = Field(default=None, ge=0)
    rollback_available: bool | None = None

    # Test-mutation signals (v0.2 blind-spot mitigation). See the field
    # docstrings above for how each feeds the risk dimension.
    tests_added: int | None = Field(default=None, ge=0)
    tests_removed: int | None = Field(default=None, ge=0)
    tests_modified: int | None = Field(default=None, ge=0)


class AgentStep(BaseModel):
    """One observed state in a coding-agent trajectory.

    Attributes:
        step_index: Zero-based position of the step in the trajectory.
        signals: The :class:`CodingWorkflowSignals` observed at this step.
        scores: Optional pre-computed :class:`EvaluationScores` for the step.
    """

    model_config = ConfigDict(extra="forbid")

    step_index: int = Field(ge=0)
    signals: CodingWorkflowSignals
    scores: EvaluationScores | None = None


class AgentTrajectory(BaseModel):
    """A recorded coding-agent trajectory for the BOUND experiment harness.

    Attributes:
        task_id: Identifier of the task the trajectory attempts to solve.
        steps: Ordered :class:`AgentStep` states.
        actual_stop_step: The step at which the real agent stopped (for
            comparing against the BOUND stop step); ``None`` when unknown.
    """

    model_config = ConfigDict(extra="forbid")

    task_id: str
    steps: list[AgentStep]
    actual_stop_step: int | None = Field(default=None, ge=0)
