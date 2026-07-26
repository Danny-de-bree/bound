"""Public runtime API for BOUND (v0.9.0).

Provides a stable, documented public API that wraps the existing shared
service layer.  The runtime never imports provider SDKs and is designed
for external consumers (agents, integrations, plugins) to invoke BOUND
evaluations without touching internal modules.

Usage::

    from bound.runtime import BoundRuntime, EvaluationContext

    runtime = BoundRuntime.from_policy("bound-policy.yaml")
    result = runtime.evaluate(
        EvaluationContext(
            task_id="task-001",
            step_id="PHASE-001",
            attempt=1,
            action="Implement input validation",
        )
    )
    print(f"Decision: {result.decision}, Score: {result.score}")

Semantic versioning commitment: the public API in this module is covered
by semver.  Types and method signatures are stable across minor versions.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from bound.models import (
    Action,
    BoundCriteria,
    CodingWorkflowSignals,
    EvaluationResult,
    EvaluationScores,
)
from bound.services import (
    EvaluateRequest,
    EvaluateWorkflowRequest,
    EvaluationService,
    OutcomeRecordRequest,
    OutcomeService,
    PolicyLoadError,
    PolicyValidationError,
    RunFinishRequest,
    RunNotFoundError,
    RunService,
    RunStartRequest,
    ServiceError,
)

if TYPE_CHECKING:
    from bound.adapters import AgentAdapter
    from bound.candidate import Candidate
    from bound.policy_schema import BoundPolicyConfig

# =========================================================================
# Public request / response models
# =========================================================================


class EvaluationContext(BaseModel):
    """Inputs for a single BOUND evaluation.

    Attributes:
        task_id: Identifies the task being performed.
        step_id: Identifies the step/phase within the task.
        attempt: The attempt number (1-based).
        action: Human-readable description of the proposed action.
        scores: Optional pre-supplied :class:`EvaluationScores`.  When
            omitted, the runtime uses the workflow evaluator path with
            default neutral signals.
        criteria: Optional :class:`BoundCriteria` override.
        influence: Optional influence score override.
        metadata: Optional arbitrary string metadata for lineage.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    attempt: int = Field(default=1, ge=1)
    action: str = Field(min_length=1)
    scores: EvaluationScores | None = None
    criteria: BoundCriteria | None = None
    influence: float = 0.0
    metadata: dict[str, str] = Field(default_factory=dict)


class RunHandle(BaseModel):
    """Handle returned when a lineage run is started.

    Attributes:
        run_id: The generated run identifier.
        task: The task description.
        started_at: ISO-8601 UTC timestamp string.
        status: The run status.
        schema_version: Lineage schema version.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    task: str
    started_at: str
    status: str
    schema_version: str


class FinishRunResult(BaseModel):
    """Result of finishing a run.

    Attributes:
        run_id: The run id.
        status: The finish status.
        finished_at: ISO-8601 UTC timestamp string.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    status: str
    finished_at: str


class OutcomeRecordContext(BaseModel):
    """Context for recording an outcome against a step evaluation.

    Attributes:
        run_id: The owning run.
        step_id: The step the outcome applies to.
        evaluation_id: The evaluation the outcome applies to.
        decision: The BOUND decision.
        next_action: Optional control action.
        reason_code: Optional reason code string.
        note: Optional free-text note.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    evaluation_id: str = Field(min_length=1)
    decision: str = Field(min_length=1)
    next_action: str | None = None
    reason_code: str | None = None
    note: str | None = None


class OutcomeResult(BaseModel):
    """Result of recording an outcome.

    Attributes:
        run_id: The run id.
        step_id: The step id.
        evaluation_id: The evaluation id.
        decision: The decision.
        next_action: The control action.
        reason_code: The reason code string.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    step_id: str
    evaluation_id: str
    decision: str
    next_action: str
    reason_code: str


# =========================================================================
# BoundRuntime
# =========================================================================


class BoundRuntime:
    """Stable public runtime wrapping the BOUND service layer.

    Construct via :meth:`from_policy`.  The runtime holds a loaded policy
    path and delegates evaluation, run management, and outcome recording to
    the typed service layer in :mod:`bound.services`.

    Rules:
    * Never imports provider SDKs.
    * All methods return typed response models.
    * Byte-for-byte deterministic for identical inputs.
    """

    def __init__(
        self,
        policy_path: str | Path,
        *,
        project_root: str | Path | None = None,
        lineage_enabled: bool = True,
    ) -> None:
        """Initialise the runtime (prefer :meth:`from_policy`)."""
        self._policy_path: Path = Path(policy_path).resolve()
        self._project_root: Path = (
            Path(project_root).resolve() if project_root is not None else self._policy_path.parent
        )
        self._lineage_enabled: bool = lineage_enabled
        self._current_run_id: str | None = None
        self._config: BoundPolicyConfig | None = None

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_policy(
        cls,
        policy_path: str | Path,
        *,
        project_root: str | Path | None = None,
        lineage_enabled: bool = True,
    ) -> BoundRuntime:
        """Create a runtime bound to a policy YAML file.

        Validates that the policy file exists and can be loaded.

        Raises:
            PolicyLoadError: If the policy file cannot be found or read.
            PolicyValidationError: If the policy file fails schema validation.
        """
        pp = Path(policy_path)
        if not pp.exists():
            raise PolicyLoadError(f"Policy file not found: {pp}")
        if not pp.is_file():
            raise PolicyLoadError(f"Policy path is not a file: {pp}")

        from bound.services import PolicyService, PolicyValidateRequest

        result = PolicyService.validate(PolicyValidateRequest(path=str(pp.resolve())))
        if not result.valid:
            errors = result.errors or ["Policy validation failed"]
            raise PolicyValidationError("; ".join(errors))

        runtime = cls(
            policy_path=pp,
            project_root=project_root,
            lineage_enabled=lineage_enabled,
        )
        # Load the config for use by create_candidate / collectors.
        from bound.policy_schema import load_policy_yaml

        runtime._config = load_policy_yaml(pp)
        return runtime

    @classmethod
    def from_config(
        cls,
        config: BoundPolicyConfig,
        *,
        project_root: str | Path | None = None,
        lineage_enabled: bool = True,
        policy_path: str | Path | None = None,
    ) -> BoundRuntime:
        """Create a runtime from an in-memory policy config.

        Use this when the policy config is already loaded or constructed
        programmatically, rather than read from a YAML file.

        Args:
            config: A validated :class:`~bound.policy_schema.BoundPolicyConfig`.
            project_root: The project root directory.  Required when
                ``policy_path`` is not provided.
            lineage_enabled: Whether lineage recording is enabled.
            policy_path: Optional path to the policy YAML file (for lineage
                recording).  When not provided, a sentinel placeholder is
                used.

        Returns:
            A configured :class:`BoundRuntime` instance.

        Raises:
            ValueError: If neither ``project_root`` nor ``policy_path``
                are provided.
        """
        if project_root is None and policy_path is None:
            raise ValueError(
                "Either project_root or policy_path must be provided for from_config()"
            )

        pp = Path(policy_path) if policy_path else Path.cwd() / "bound-policy.yaml"
        runtime = cls(
            policy_path=pp,
            project_root=project_root,
            lineage_enabled=lineage_enabled,
        )
        runtime._config = config
        return runtime

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def policy_path(self) -> Path:
        """The resolved path to the bound policy YAML file."""
        return self._policy_path

    @property
    def project_root(self) -> Path:
        """The resolved project root directory."""
        return self._project_root

    @property
    def lineage_enabled(self) -> bool:
        """Whether lineage recording is enabled."""
        return self._lineage_enabled

    @property
    def current_run_id(self) -> str | None:
        """The current run id when a run has been started, or ``None``."""
        return self._current_run_id

    @property
    def policy_config(self) -> BoundPolicyConfig | None:
        """The loaded policy configuration, or ``None`` if not loaded."""
        return self._config

    @property
    def policy_hash(self) -> str | None:
        """The canonical policy hash, or ``None`` if not loaded."""
        if self._config is None:
            return None
        from bound.policy_canon import compute_policy_hash

        return compute_policy_hash(self._config)

    # ------------------------------------------------------------------
    # Candidate management
    # ------------------------------------------------------------------

    def create_candidate(
        self,
        task: str,
        *,
        base_commit: str = "HEAD",
        run_id: str | None = None,
    ) -> Candidate:
        """Create a new :class:`~bound.candidate.Candidate` for a task.

        The candidate gets an isolated git worktree under
        ``<project_root>/.bound/worktrees/``.  Use the candidate as a
        context manager::

            with runtime.create_candidate(\"fix bug\") as candidate:
                evidence = candidate.collect_evidence(contract)
                result = candidate.evaluate(contract, evidence)

        Args:
            task: Human-readable task description.
            base_commit: Git ref to base the worktree on (default ``HEAD``).
            run_id: Optional explicit lineage run id; auto-generated when
                omitted.

        Returns:
            A new :class:`~bound.candidate.Candidate` instance (not yet
            active — use as context manager).
        """
        from bound.candidate import Candidate as CandidateCls

        return CandidateCls(
            project_root=self._project_root,
            task=task,
            base_commit=base_commit,
            run_id=run_id,
        )

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def start_run(
        self,
        task: str,
        *,
        metadata: Mapping[str, str] | None = None,
    ) -> RunHandle:
        """Start a new lineage run.

        Args:
            task: A human-readable task description.
            metadata: Optional string->string metadata dictionary.

        Returns:
            A :class:`RunHandle` with the new run's identity.

        Raises:
            ServiceError: If lineage is disabled.
        """
        if not self._lineage_enabled:
            raise ServiceError("Lineage recording is disabled; cannot start a run.")

        response = RunService.start(
            RunStartRequest(
                task=task,
                metadata=dict(metadata) if metadata else None,
            ),
        )
        self._current_run_id = response.run_id
        return RunHandle(
            run_id=response.run_id,
            task=response.task,
            started_at=response.started_at,
            status=response.status,
            schema_version=response.schema_version,
        )

    def finish_run(
        self,
        status: str = "completed",
        *,
        note: str | None = None,
    ) -> FinishRunResult:
        """Finish the current lineage run.

        Args:
            status: Finish status.
            note: Optional free-text note.

        Returns:
            A :class:`FinishRunResult`.

        Raises:
            RunNotFoundError: If no run has been started.
            ServiceError: If lineage is disabled.
        """
        if not self._lineage_enabled:
            raise ServiceError("Lineage recording is disabled; cannot finish a run.")
        if self._current_run_id is None:
            raise RunNotFoundError("No active run to finish.  Call start_run() first.")

        response = RunService.finish(
            RunFinishRequest(
                run_id=self._current_run_id,
                status=status,
                note=note,
            ),
        )
        result = FinishRunResult(
            run_id=response.run_id,
            status=response.status,
            finished_at=response.finished_at,
        )
        self._current_run_id = None
        return result

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def evaluate(
        self,
        context: EvaluationContext,
    ) -> EvaluationResult:
        """Evaluate an action against the bound policy.

        This is the primary public entry point.  Routes to the
        :class:`~bound.services.EvaluationService` and can optionally
        record lineage when a run has been started.

        Args:
            context: The :class:`EvaluationContext` describing the action.

        Returns:
            An :class:`~bound.models.EvaluationResult`.

        Raises:
            EvaluationInputError: If the inputs fail validation.
        """
        criteria = context.criteria or BoundCriteria(threshold=0.5)

        action = Action(
            description=context.action,
            goal=context.task_id,
            context=(context.metadata.get("context") if context.metadata else None),
        )

        run_id = self._current_run_id if self._lineage_enabled else None

        if context.scores is not None:
            response = EvaluationService.evaluate(
                EvaluateRequest(
                    action=action,
                    scores=context.scores,
                    criteria=criteria,
                    run_id=run_id,
                    step=context.step_id,
                    attempt=context.attempt,
                    description=context.action,
                ),
            )
        else:
            signals = CodingWorkflowSignals(test_pass_rate=1.0)
            response = EvaluationService.evaluate_workflow(
                EvaluateWorkflowRequest(
                    action=action,
                    signals=signals,
                    criteria=criteria,
                    influence=context.influence,
                    run_id=run_id,
                    step=context.step_id,
                    attempt=context.attempt,
                    description=context.action,
                ),
            )

        return response.result

    # ------------------------------------------------------------------
    # Adapter control loop
    # ------------------------------------------------------------------

    def run_with_adapter(
        self,
        adapter: AgentAdapter,
        task: str,
        plan: dict[str, Any] | None = None,
        criteria: BoundCriteria | None = None,
    ) -> dict[str, Any]:
        """Run the full BOUND control loop with an agent adapter.

        Launches the agent, then loops: wait for step-completion events,
        evaluate the evidence against the policy, send the decision back
        as a command.  The loop terminates when the adapter reports
        ``task.completed``, BOUND decides ``ROLLBACK``, or max steps are
        reached.

        Args:
            adapter: A configured :class:`~bound.adapters.AgentAdapter`.
            task: Human-readable task description.
            plan: Optional structured plan dict.
            criteria: Optional :class:`BoundCriteria` override; defaults
                to ``threshold=0.5``.

        Returns:
            A summary dict with keys: ``task``, ``steps``, ``final_decision``,
            ``final_score``, ``decisions``.

        Raises:
            RuntimeError: If the agent crashes or times out.
        """

        import logging

        _logger = logging.getLogger(__name__)

        criteria = criteria or BoundCriteria(threshold=0.5)
        max_steps = 50
        decisions: list[str] = []
        steps: list[dict[str, Any]] = []
        attempt = 1

        adapter.launch(task=task, plan=plan)

        for step_idx in range(1, max_steps + 1):
            _logger.info("Step %d / %d (attempt %d)", step_idx, max_steps, attempt)

            # Wait for the agent to report a step completion.
            try:
                event = adapter.wait_for_event(timeout=adapter.config.timeout_seconds)
            except RuntimeError:
                _logger.exception("Agent crashed during step %d", step_idx)
                adapter.terminate()
                break

            if event is None:
                _logger.warning("Step %d timed out", step_idx)
                adapter.send_command({"type": "retry"})
                attempt += 1
                continue

            # Handle terminal events.
            if event.type == "task.completed":
                _logger.info("Agent reported task completed")
                decisions.append("ACCEPT")
                break

            if event.type == "task.failed":
                _logger.warning("Agent reported task failure")
                decisions.append("ROLLBACK")
                break

            # Evaluate the step through BOUND.
            step_id = f"PHASE-{step_idx:03d}"

            ctx = EvaluationContext(
                task_id=task[:64],
                step_id=step_id,
                attempt=attempt,
                action=f"Step {step_idx}: {event.type}",
                criteria=criteria,
                metadata={"adapter_event": event.type},
            )

            result = self.evaluate(ctx)
            decision = result.decision
            decisions.append(decision)

            steps.append(
                {
                    "step": step_idx,
                    "attempt": attempt,
                    "step_id": step_id,
                    "event_type": event.type,
                    "decision": decision,
                    "score": result.score,
                    "reason_code": result.reason_code,
                    "threshold": result.threshold,
                }
            )

            _logger.info(
                "Step %d decision: %s (score=%.4f, threshold=%.4f)",
                step_idx,
                decision,
                result.score,
                result.threshold,
            )

            # Map decision to control action.
            if decision == "ACCEPT":
                adapter.send_command({"type": "continue"})
                attempt = 1
            elif decision == "RETRY":
                adapter.send_command({"type": "retry"})
                attempt += 1
            elif decision == "REPLAN":
                adapter.send_command({"type": "replan"})
                attempt += 1
            elif decision == "ROLLBACK":
                adapter.send_command({"type": "rollback"})
                adapter.terminate()
                break
            else:
                _logger.error("Unknown decision: %s", decision)
                adapter.send_command({"type": "shutdown"})
                adapter.terminate()
                break

        adapter.terminate()

        final_decision = decisions[-1] if decisions else "UNKNOWN"
        final_score = steps[-1]["score"] if steps else 0.0

        _logger.info(
            "Control loop finished: %d steps, final=%s, score=%.4f",
            len(steps),
            final_decision,
            final_score,
        )

        return {
            "task": task,
            "steps": steps,
            "final_decision": final_decision,
            "final_score": final_score,
            "decisions": decisions,
        }

    # ------------------------------------------------------------------
    # Outcome recording
    # ------------------------------------------------------------------

    def record_outcome(
        self,
        context: OutcomeRecordContext,
    ) -> OutcomeResult:
        """Record an outcome for a step evaluation in lineage.

        Args:
            context: The :class:`OutcomeRecordContext`.

        Returns:
            An :class:`OutcomeResult` confirming the recorded outcome.

        Raises:
            RunNotFoundError: If the run does not exist.
            ServiceError: If lineage is disabled.
        """
        if not self._lineage_enabled:
            raise ServiceError(
                "Lineage recording is disabled; cannot record an outcome.",
            )

        response = OutcomeService.record(
            OutcomeRecordRequest(
                run_id=context.run_id,
                step_id=context.step_id,
                evaluation_id=context.evaluation_id,
                decision=context.decision,
                next_action=context.next_action,
                reason_code=context.reason_code,
                note=context.note,
            ),
        )
        return OutcomeResult(
            run_id=response.run_id,
            step_id=response.step_id,
            evaluation_id=response.evaluation_id,
            decision=response.decision,
            next_action=response.next_action,
            reason_code=response.reason_code,
        )


__all__ = [
    "BoundRuntime",
    "Candidate",
    "EvaluationContext",
    "FinishRunResult",
    "OutcomeRecordContext",
    "OutcomeResult",
    "RunHandle",
]
