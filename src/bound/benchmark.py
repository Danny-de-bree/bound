"""Benchmark infrastructure for evaluating BOUND policy effectiveness.

Provides the :class:`BenchmarkRunner` — a runtime client that replays
trajectory fixtures through BOUND, collects paired (with/without BOUND)
results, and stores them as :class:`BenchmarkRun` models.

Usage::

    from bound.benchmark import BenchmarkRunner

    runner = BenchmarkRunner()
    run = runner.run_suite("smoke")
    print(f"Steps saved: {run.aggregate.steps_saved}")
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from bound.experiment import ExperimentResult, run_experiment
from bound.models import (
    AgentTrajectory,
    BoundCriteria,
    WorkflowNormalization,
)

logger = logging.getLogger("bound.benchmark")

# ---------------------------------------------------------------------------
# Benchmark data models
# ---------------------------------------------------------------------------


class TaskBenchmarkResult(BaseModel):
    """Per-task result comparing BOUND vs no-BOUND execution.

    Attributes:
        task_id: Identifier of the benchmarked task.
        accepted: Whether BOUND ever produced ``ACCEPT`` during replay.
        bound_stop_step: Step at which BOUND would have stopped; ``None`` if
            BOUND never accepted.
        actual_stop_step: Step at which the real agent stopped.
        steps_saved: Agent steps BOUND would have avoided.
        tool_calls_saved: Tool calls BOUND would have avoided.
        tokens_saved: Tokens BOUND would have avoided.
        runtime_saved: Wall-clock seconds BOUND would have avoided.
        tests_pass_at_bound_stop: Whether tests passed at the BOUND stop.
        regressions_after_accept: Number of post-accept regression steps.
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
    tests_pass_at_bound_stop: bool | None = None
    regressions_after_accept: int = Field(default=0, ge=0)


class AggregateMetrics(BaseModel):
    """Aggregate benchmark metrics across all tasks in a suite.

    Attributes:
        total_tasks: Number of tasks in the suite.
        tasks_accepted: Number of tasks where BOUND accepted.
        total_steps_saved: Sum of steps saved across all tasks.
        total_tool_calls_saved: Sum of tool calls saved.
        total_tokens_saved: Sum of tokens saved.
        total_runtime_saved: Sum of runtime seconds saved.
        tasks_with_regressions: Number of tasks with post-accept regressions.
        acceptance_rate: Fraction of tasks BOUND accepted.
        mean_steps_saved: Mean steps saved per task.
    """

    model_config = ConfigDict(extra="forbid")

    total_tasks: int = Field(default=0, ge=0)
    tasks_accepted: int = Field(default=0, ge=0)
    total_steps_saved: int = Field(default=0, ge=0)
    total_tool_calls_saved: int = Field(default=0, ge=0)
    total_tokens_saved: int = Field(default=0, ge=0)
    total_runtime_saved: float = Field(default=0.0, ge=0.0)
    tasks_with_regressions: int = Field(default=0, ge=0)
    acceptance_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    mean_steps_saved: float = Field(default=0.0, ge=0.0)


class BenchmarkRun(BaseModel):
    """A complete benchmark run across a suite of tasks.

    Attributes:
        run_id: Unique identifier for this benchmark run.
        suite_name: Name of the benchmark suite.
        timestamp: UTC ISO-8601 timestamp of the run.
        tasks: Per-task benchmark results.
        aggregate: Aggregate metrics across all tasks.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    suite_name: str
    timestamp: str
    tasks: list[TaskBenchmarkResult]
    aggregate: AggregateMetrics

    @classmethod
    def from_experiment_results(
        cls,
        suite_name: str,
        results: list[ExperimentResult],
    ) -> BenchmarkRun:
        """Build a :class:`BenchmarkRun` from experiment results.

        Args:
            suite_name: Name of the benchmark suite.
            results: List of :class:`ExperimentResult` from replaying each task.

        Returns:
            A new :class:`BenchmarkRun` with computed aggregates.
        """
        tasks = [
            TaskBenchmarkResult(
                task_id=r.task_id,
                accepted=r.accepted,
                bound_stop_step=r.bound_stop_step,
                actual_stop_step=r.actual_stop_step,
                steps_saved=r.steps_saved,
                tool_calls_saved=r.tool_calls_saved,
                tokens_saved=r.tokens_saved,
                runtime_saved=r.runtime_saved,
                tests_pass_at_bound_stop=r.tests_pass_at_bound_stop,
                regressions_after_accept=r.regressions_after_accept,
            )
            for r in results
        ]

        n = len(tasks)
        accepted_tasks = [t for t in tasks if t.accepted]
        n_accepted = len(accepted_tasks)
        acceptance_rate = n_accepted / n if n > 0 else 0.0

        aggregate = AggregateMetrics(
            total_tasks=n,
            tasks_accepted=n_accepted,
            total_steps_saved=sum(t.steps_saved or 0 for t in tasks),
            total_tool_calls_saved=sum(t.tool_calls_saved or 0 for t in tasks),
            total_tokens_saved=sum(t.tokens_saved or 0 for t in tasks),
            total_runtime_saved=sum(t.runtime_saved or 0.0 for t in tasks),
            tasks_with_regressions=sum(1 for t in tasks if t.regressions_after_accept > 0),
            acceptance_rate=acceptance_rate,
            mean_steps_saved=sum(t.steps_saved or 0 for t in tasks) / n if n > 0 else 0.0,
        )

        return cls(
            run_id=uuid.uuid4().hex[:12],
            suite_name=suite_name,
            timestamp=datetime.now(UTC).isoformat(),
            tasks=tasks,
            aggregate=aggregate,
        )


# ---------------------------------------------------------------------------
# Suite definitions
# ---------------------------------------------------------------------------


def _trajectories_dir() -> Path:
    """Resolve the benchmarks/trajectories directory relative to the repo root."""
    return Path(__file__).resolve().parent.parent.parent / "benchmarks" / "trajectories"


#: Built-in benchmark suites mapping suite names to list of trajectory fixture stem names.
BUILTIN_SUITES: dict[str, list[str]] = {
    "smoke": ["clean_accept", "never_accept"],
    "full": [
        "clean_accept",
        "retry_then_accept",
        "regression_after_accept",
        "never_accept",
        "realistic_coding_task",
    ],
}

#: Default criteria used for benchmark replays — threshold 0.6 with default weights.
_BENCHMARK_CRITERIA = BoundCriteria(threshold=0.6)

#: Default v0.2 normalization caps.
_BENCHMARK_NORMALIZATION = WorkflowNormalization()


def resolve_suite_tasks(suite_name: str) -> list[str]:
    """Resolve a benchmark suite to its list of trajectory fixture stem names.

    Args:
        suite_name: Name of a built-in suite (``smoke`` or ``full``), or a
            custom comma-separated list of stem names.

    Returns:
        List of trajectory fixture stem names.

    Raises:
        ValueError: If the suite name is not a known built-in and does not
            resolve to a non-empty list.
    """
    if suite_name in BUILTIN_SUITES:
        return BUILTIN_SUITES[suite_name]
    # Treat as comma-separated list of custom tasks.
    tasks = [t.strip() for t in suite_name.split(",") if t.strip()]
    if not tasks:
        raise ValueError(f"Unknown suite '{suite_name}' and no custom tasks provided.")
    return tasks


# ---------------------------------------------------------------------------
# BenchmarkRunner
# ---------------------------------------------------------------------------


class BenchmarkRunner:
    """Runs benchmark suites using the BOUND experiment harness.

    This is a runtime *client* — it uses the existing :func:`run_experiment`
    harness and trajectory fixtures to replay each task, not a separate engine.
    Results are collected as :class:`BenchmarkRun` models.

    Usage::

        runner = BenchmarkRunner()
        run = runner.run_suite("smoke")
        print(run.model_dump_json(indent=2))
    """

    def __init__(self) -> None:
        """Create a :class:`BenchmarkRunner` with default criteria."""
        self._trajectories_dir = _trajectories_dir()

    def run_suite(
        self,
        suite_name: str = "smoke",
        *,
        criteria: BoundCriteria | None = None,
        normalization: WorkflowNormalization | None = None,
    ) -> BenchmarkRun:
        """Run a benchmark suite and return the aggregate results.

        Each task's trajectory is replayed through BOUND via
        :func:`run_experiment`, producing an :class:`ExperimentResult`. The
        runner then collects paired metrics into a :class:`BenchmarkRun`.

        Args:
            suite_name: Name of the suite (``smoke``, ``full``, or
                comma-separated custom task list).
            criteria: Optional :class:`BoundCriteria` override. Defaults to
                threshold 0.6.
            normalization: Optional :class:`WorkflowNormalization` override.

        Returns:
            A :class:`BenchmarkRun` with per-task results and aggregate metrics.

        Raises:
            ValueError: If the suite is unknown and contains no valid tasks.
            FileNotFoundError: If a referenced trajectory fixture is missing.
        """
        task_stems = resolve_suite_tasks(suite_name)
        criteria = criteria or _BENCHMARK_CRITERIA
        normalization = normalization or _BENCHMARK_NORMALIZATION

        logger.info(
            "Running benchmark suite '%s' with %d task(s)…",
            suite_name,
            len(task_stems),
        )

        results: list[ExperimentResult] = []
        for stem in task_stems:
            path = self._trajectories_dir / f"{stem}.json"
            if not path.exists():
                raise FileNotFoundError(f"Trajectory fixture not found: {path}")

            trajectory = AgentTrajectory.model_validate_json(
                path.read_text(encoding="utf-8")
            )
            result = run_experiment(trajectory, criteria, normalization)
            results.append(result)
            logger.debug(
                "  %s: accepted=%s steps_saved=%s",
                stem,
                result.accepted,
                result.steps_saved,
            )

        return BenchmarkRun.from_experiment_results(suite_name, results)

    def run_all_suites(
        self,
        *,
        criteria: BoundCriteria | None = None,
        normalization: WorkflowNormalization | None = None,
    ) -> dict[str, BenchmarkRun]:
        """Run all built-in benchmark suites.

        Args:
            criteria: Optional :class:`BoundCriteria` override.
            normalization: Optional :class:`WorkflowNormalization` override.

        Returns:
            A mapping from suite name to :class:`BenchmarkRun`.
        """
        return {
            name: self.run_suite(
                name, criteria=criteria, normalization=normalization,
            )
            for name in BUILTIN_SUITES
        }


__all__ = [
    "AggregateMetrics",
    "BenchmarkRun",
    "BenchmarkRunner",
    "TaskBenchmarkResult",
    "BUILTIN_SUITES",
    "resolve_suite_tasks",
]
