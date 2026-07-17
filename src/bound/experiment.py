from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from bound.evaluator import StaticEvaluator
from bound.models import (
    Action,
    AgentTrajectory,
    BoundCriteria,
    Decision,
    EvaluationScores,
    WorkflowNormalization,
)
from bound.policy import BoundPolicy
from bound.workflow import CodingWorkflowEvaluator


class StepRecord(BaseModel):
    """The BOUND decision replayed for a single trajectory step.

    Stored on :attr:`ExperimentResult.per_step` so the whole replay is auditable:
    a reader can reconstruct *why* BOUND stopped where it did from the per-step
    score and decision alone.

    Attributes:
        step_index: Zero-based position of the step in the trajectory.
        decision: The BOUND decision the policy produced for this step.
        score: The raw BOUND score ``S`` for this step (unclamped, unrounded).
        scores: The :class:`EvaluationScores` used for the decision.
    """

    model_config = ConfigDict(extra="forbid")

    step_index: int = Field(ge=0)
    decision: Decision
    score: float
    scores: EvaluationScores



class ExperimentResult(BaseModel):
    """Evidence produced by replaying one trajectory through BOUND.

    Every field is either a concrete, comparable quantity or an explicit
    ``None`` when it cannot be measured, so a consumer never has to guess
    whether a zero means "nothing saved" or "unknown".

    The headline metric is :attr:`steps_saved`: the number of agent steps BOUND
    would have avoided by stopping at :attr:`bound_stop_step` instead of letting
    the agent run to :attr:`actual_stop_step`. The companion metric
    :attr:`post_solution_unnecessary_steps` is the same quantity framed from the
    agent's perspective: how many steps the *real* agent executed after it had
    already reached a state that satisfied the task's acceptance criteria.

    Attributes:
        task_id: Identifier of the replayed task (echoed from the trajectory).
        accepted: Whether BOUND ever produced ``ACCEPT`` during the replay.
        bound_stop_step: Index of the first step that produced ``ACCEPT``;
            ``None`` when BOUND never accepted.
        actual_stop_step: Index at which the real agent stopped (echoed from the
            trajectory); ``None`` when unknown.
        steps_saved: ``actual_stop_step - bound_stop_step`` when both are known
            and BOUND accepted; otherwise ``None``.
        tool_calls_saved: Tool calls the agent made after the BOUND stop step,
            when both stop steps are known; otherwise ``None``.
        tokens_saved: Tokens consumed after the BOUND stop step, when both stop
            steps are known and the signal was tracked; otherwise ``None``.
        runtime_saved: Wall-clock seconds spent after the BOUND stop step, when
            both stop steps are known and the signal was tracked; otherwise
            ``None``.
        post_solution_unnecessary_steps: Agent steps executed after the earliest
            state that already satisfied the acceptance criteria. ``0`` when no
            satisfying state was ever reached; ``None`` when a satisfying state
            was reached but the real stop step is unknown.
        tests_pass_at_bound_stop: Whether tests fully passed at the BOUND stop
            step; ``None`` when BOUND never accepted or the signal was absent.
        required_checks_pass_at_bound_stop: Whether all required checks passed at
            the BOUND stop step; ``None`` when BOUND never accepted or the signal
            was absent.
        regressions_after_accept: Number of replayed steps after the first
            ``ACCEPT`` whose decision was no longer ``ACCEPT`` — i.e. the agent
            kept working and the state degraded below the threshold. ``0`` when
            BOUND never accepted.
        per_step: The full per-step replay (index, decision, score, scores).
    """

    model_config = ConfigDict(extra="forbid")

    task_id: str
    accepted: bool
    bound_stop_step: int | None = Field(default=None, ge=0)
    actual_stop_step: int | None = Field(default=None, ge=0)
    steps_saved: int | None = Field(default=None, ge=0)
    tool_calls_saved: int | None = Field(default=None, ge=0)
    tokens_saved: int | None = Field(default=None, ge=0)
    runtime_saved: float | None = Field(default=None, ge=0.0)
    post_solution_unnecessary_steps: int | None = Field(default=None, ge=0)
    tests_pass_at_bound_stop: bool | None = None
    required_checks_pass_at_bound_stop: bool | None = None
    regressions_after_accept: int = Field(default=0, ge=0)
    per_step: list[StepRecord] = Field(default_factory=list)


def _decide_step(
    step_scores: EvaluationScores | None,
    step_signals,
    normalization: WorkflowNormalization | None,
    action: Action,
    criteria: BoundCriteria,
) -> tuple[Decision, float, EvaluationScores]:
    """Run the BOUND policy for one step and return its decision, score, scores.

    When ``step_scores`` is supplied the scores are routed through a
    :class:`StaticEvaluator` so the deterministic decision rule in
    :class:`BoundPolicy` is the single source of truth. Otherwise a
    :class:`CodingWorkflowEvaluator` is built from the step's signals and the
    given normalization, deriving the scores from real workflow evidence.

    Args:
        step_scores: Pre-computed scores for the step, or ``None`` to derive them
            from signals.
        step_signals: The :class:`CodingWorkflowSignals` for the step (used only
            when ``step_scores`` is ``None``).
        normalization: Caps used by the workflow evaluator; ``None`` selects the
            v0.2 defaults.
        action: The :class:`Action` forwarded to the policy (unused for scoring
            by both evaluators but required by the policy seam).
        criteria: The BOUND criteria (threshold, weights, margins).

    Returns:
        A ``(decision, score, scores)`` triple for the step.
    """
    if step_scores is None:
        evaluator = CodingWorkflowEvaluator(step_signals, normalization)
    else:
        evaluator = StaticEvaluator(step_scores)
    result = BoundPolicy(evaluator).evaluate(action, criteria)
    return result.decision, result.score, result.scores


def _delta(bound_step, actual_step, attr: str) -> int | float | None:
    """Compute ``actual.<attr> - bound.<attr>`` for a cumulative signal.

    Returns ``None`` when either step lacks the signal (it is ``None``). The
    result is clamped at zero because cumulative counters never decrease, so a
    negative delta would indicate malformed fixtures rather than real savings.

    Args:
        bound_step: The :class:`AgentStep` at the BOUND stop.
        actual_step: The :class:`AgentStep` at the real stop.
        attr: The cumulative signal attribute name to diff.

    Returns:
        The non-negative delta, or ``None`` when unmeasurable.
    """
    bound_value = getattr(bound_step.signals, attr)
    actual_value = getattr(actual_step.signals, attr)
    if bound_value is None or actual_value is None:
        return None
    return max(actual_value - bound_value, 0)



def run_experiment(
    trajectory: AgentTrajectory,
    criteria: BoundCriteria,
    normalization: WorkflowNormalization | None = None,
) -> ExperimentResult:
    """Replay a trajectory through BOUND and measure the work it would avoid.

    For every step the harness either derives scores from the step's workflow
    signals (via :class:`CodingWorkflowEvaluator`) or uses the step's pre-supplied
    scores, then applies the deterministic :class:`BoundPolicy`. The first step
    producing ``ACCEPT`` is the BOUND stop step; it is compared against
    :attr:`AgentTrajectory.actual_stop_step` to quantify the steps, tool calls,
    tokens and runtime BOUND would have saved, and to flag regressions the agent
    introduced after it had already reached a satisfying state.

    Args:
        trajectory: The recorded coding-agent trajectory to replay.
        criteria: The BOUND criteria (threshold ``T``, weights, margins) used for
            every step's decision.
        normalization: Caps for the workflow evaluator; ``None`` selects the
            v0.2 default :class:`WorkflowNormalization`.

    Returns:
        An :class:`ExperimentResult` with the BOUND stop step, savings metrics,
        quality-gate status at the stop, and the full per-step replay.
    """
    per_step: list[StepRecord] = []
    bound_stop_step: int | None = None

    for step in trajectory.steps:
        action = Action(
            description=f"agent step {step.step_index}",
            goal=trajectory.task_id,
        )
        decision, score, scores = _decide_step(
            step.scores,
            step.signals,
            normalization,
            action,
            criteria,
        )
        per_step.append(
            StepRecord(
                step_index=step.step_index,
                decision=decision,
                score=score,
                scores=scores,
            )
        )
        if bound_stop_step is None and decision == "ACCEPT":
            bound_stop_step = step.step_index

    accepted = bound_stop_step is not None
    actual_stop_step = trajectory.actual_stop_step

    # Index steps by their step_index so metrics are robust to sparse indexing.
    steps_by_index = {step.step_index: step for step in trajectory.steps}

    # --- Savings metrics -------------------------------------------------
    steps_saved: int | None = None
    tool_calls_saved: int | None = None
    tokens_saved: int | None = None
    runtime_saved: float | None = None
    post_solution_unnecessary_steps: int | None = None

    if accepted and actual_stop_step is not None and bound_stop_step is not None:
        steps_saved = max(actual_stop_step - bound_stop_step, 0)
        bound_step = steps_by_index.get(bound_stop_step)
        actual_step = steps_by_index.get(actual_stop_step)
        if bound_step is not None and actual_step is not None:
            tool_calls_saved = _delta(bound_step, actual_step, "tool_call_count")
            tokens_saved = _delta(bound_step, actual_step, "token_usage")
            runtime_saved = _delta(bound_step, actual_step, "execution_time_seconds")
        post_solution_unnecessary_steps = steps_saved
    elif not accepted:
        # No satisfying state was ever reached: nothing the agent did was
        # "unnecessary after success".
        post_solution_unnecessary_steps = 0

    # --- Quality gates at the BOUND stop ---------------------------------
    tests_pass_at_bound_stop: bool | None = None
    required_checks_pass_at_bound_stop: bool | None = None
    if accepted and bound_stop_step is not None:
        bound_step = steps_by_index.get(bound_stop_step)
        if bound_step is not None:
            tpr = bound_step.signals.test_pass_rate
            tests_pass_at_bound_stop = None if tpr is None else tpr >= 1.0
            rcp = bound_step.signals.required_checks_passed
            required_checks_pass_at_bound_stop = None if rcp is None else rcp >= 1.0

    # --- Regressions after the first ACCEPT ------------------------------
    regressions_after_accept = 0
    if accepted and bound_stop_step is not None:
        regressions_after_accept = sum(
            1
            for record in per_step
            if record.step_index > bound_stop_step and record.decision != "ACCEPT"
        )

    return ExperimentResult(
        task_id=trajectory.task_id,
        accepted=accepted,
        bound_stop_step=bound_stop_step,
        actual_stop_step=actual_stop_step,
        steps_saved=steps_saved,
        tool_calls_saved=tool_calls_saved,
        tokens_saved=tokens_saved,
        runtime_saved=runtime_saved,
        post_solution_unnecessary_steps=post_solution_unnecessary_steps,
        tests_pass_at_bound_stop=tests_pass_at_bound_stop,
        required_checks_pass_at_bound_stop=required_checks_pass_at_bound_stop,
        regressions_after_accept=regressions_after_accept,
        per_step=per_step,
    )



def load_trajectory(path: str | Path) -> AgentTrajectory:
    """Load an :class:`AgentTrajectory` from a JSON file.

    Args:
        path: Path to a JSON document matching the :class:`AgentTrajectory`
            schema.

    Returns:
        The parsed :class:`AgentTrajectory`.
    """
    text = Path(path).read_text(encoding="utf-8")
    return AgentTrajectory.model_validate_json(text)


def save_trajectory(trajectory: AgentTrajectory, path: str | Path) -> None:
    """Persist an :class:`AgentTrajectory` as pretty-printed JSON.

    Args:
        trajectory: The trajectory to serialise.
        path: Destination file path (parent directories are not created).
    """
    Path(path).write_text(
        trajectory.model_dump_json(indent=2),
        encoding="utf-8",
    )


def load_trajectories(directory: str | Path) -> dict[str, AgentTrajectory]:
    """Load every ``*.json`` trajectory fixture in a directory.

    Useful for replaying a whole benchmark suite. Files are keyed by stem (e.g.
    ``clean_accept.json`` -> ``"clean_accept"``). Non-JSON files are ignored.

    Args:
        directory: Directory containing trajectory JSON fixtures.

    Returns:
        A mapping from file stem to the parsed :class:`AgentTrajectory`.
    """
    base = Path(directory)
    return {
        path.stem: AgentTrajectory.model_validate_json(
            path.read_text(encoding="utf-8")
        )
        for path in sorted(base.glob("*.json"))
    }


def summarize(result: ExperimentResult) -> str:
    """Render an :class:`ExperimentResult` as a short human-readable report.

    Intended for CLI / notebook evidence output rather than machine consumption
    (use :meth:`ExperimentResult.model_dump_json` for that).

    Args:
        result: The experiment result to summarise.

    Returns:
        A multi-line string reporting task id, BOUND vs actual stop, savings,
        quality gates and regressions.
    """
    bound = result.bound_stop_step if result.bound_stop_step is not None else "never"
    actual = result.actual_stop_step if result.actual_stop_step is not None else "unknown"
    saved = result.steps_saved if result.steps_saved is not None else "n/a"
    tools = result.tool_calls_saved if result.tool_calls_saved is not None else "n/a"
    tokens = result.tokens_saved if result.tokens_saved is not None else "n/a"
    runtime = result.runtime_saved if result.runtime_saved is not None else "n/a"
    post = result.post_solution_unnecessary_steps
    post = post if post is not None else "n/a"
    tests = result.tests_pass_at_bound_stop
    tests = "n/a" if tests is None else ("yes" if tests else "no")
    checks = result.required_checks_pass_at_bound_stop
    checks = "n/a" if checks is None else ("yes" if checks else "no")
    return (
        f"task={result.task_id} accepted={result.accepted}\n"
        f"BOUND stop step={bound}  actual stop step={actual}\n"
        f"steps_saved={saved}  tool_calls_saved={tools}  "
        f"tokens_saved={tokens}  runtime_saved={runtime}\n"
        f"post_solution_unnecessary_steps={post}\n"
        f"tests_pass_at_bound_stop={tests}  "
        f"required_checks_pass_at_bound_stop={checks}\n"
        f"regressions_after_accept={result.regressions_after_accept}"
    )


__all__ = [
    "ExperimentResult",
    "StepRecord",
    "load_trajectory",
    "load_trajectories",
    "run_experiment",
    "save_trajectory",
    "summarize",
]

