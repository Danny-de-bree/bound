"""Tests for benchmark infrastructure (benchmark.py)."""

from __future__ import annotations

import pytest

from bound.benchmark import (
    BUILTIN_SUITES,
    AggregateMetrics,
    BenchmarkRun,
    BenchmarkRunner,
    TaskBenchmarkResult,
    resolve_suite_tasks,
)
from bound.experiment import ExperimentResult

# ---------------------------------------------------------------------------
# resolve_suite_tasks
# ---------------------------------------------------------------------------


def test_resolve_builtin_suite_smoke() -> None:
    """Smoke suite resolves to 2 tasks: clean_accept and never_accept."""
    tasks = resolve_suite_tasks("smoke")
    assert tasks == ["clean_accept", "never_accept"]


def test_resolve_builtin_suite_full() -> None:
    """Full suite resolves to 5 tasks."""
    tasks = resolve_suite_tasks("full")
    assert len(tasks) == 5
    assert "clean_accept" in tasks
    assert "realistic_coding_task" in tasks


def test_resolve_custom_comma_separated() -> None:
    """Custom comma-separated list resolves correctly."""
    tasks = resolve_suite_tasks("clean_accept,never_accept")
    assert tasks == ["clean_accept", "never_accept"]


def test_resolve_custom_trims_whitespace() -> None:
    """Whitespace around task names is trimmed."""
    tasks = resolve_suite_tasks("  clean_accept ,  never_accept  ")
    assert tasks == ["clean_accept", "never_accept"]


def test_resolve_unknown_suite_returns_as_custom() -> None:
    """Unknown suite name is treated as a custom single-task suite."""
    tasks = resolve_suite_tasks("nonexistent")
    assert tasks == ["nonexistent"]


def test_resolve_empty_custom_raises() -> None:
    """Empty comma-separated string raises ValueError."""
    with pytest.raises(ValueError, match="Unknown suite"):
        resolve_suite_tasks(",,,")


# ---------------------------------------------------------------------------
# BenchmarkRun.from_experiment_results
# ---------------------------------------------------------------------------


def _make_result(
    task_id: str,
    accepted: bool = True,
    steps_saved: int | None = 2,
    tool_calls_saved: int | None = 7,
    tokens_saved: int | None = 6000,
    runtime_saved: float | None = 20.0,
) -> ExperimentResult:
    """Build a minimal ExperimentResult for testing."""
    return ExperimentResult(
        task_id=task_id,
        accepted=accepted,
        bound_stop_step=1 if accepted else None,
        actual_stop_step=3,
        steps_saved=steps_saved,
        tool_calls_saved=tool_calls_saved,
        tokens_saved=tokens_saved,
        runtime_saved=runtime_saved,
        post_solution_unnecessary_steps=steps_saved,
        tests_pass_at_bound_stop=True if accepted else None,
        required_checks_pass_at_bound_stop=True if accepted else None,
        regressions_after_accept=0,
    )


def test_from_experiment_results_basic() -> None:
    """Builds a BenchmarkRun with correct aggregate metrics."""
    results = [
        _make_result("task-a", accepted=True, steps_saved=2, tool_calls_saved=5, tokens_saved=1000, runtime_saved=10.0),
        _make_result("task-b", accepted=True, steps_saved=3, tool_calls_saved=8, tokens_saved=2000, runtime_saved=15.0),
    ]
    run = BenchmarkRun.from_experiment_results("test-suite", results)

    assert run.suite_name == "test-suite"
    assert len(run.tasks) == 2
    assert run.aggregate.total_tasks == 2
    assert run.aggregate.tasks_accepted == 2
    assert run.aggregate.acceptance_rate == 1.0
    assert run.aggregate.total_steps_saved == 5
    assert run.aggregate.total_tool_calls_saved == 13
    assert run.aggregate.total_tokens_saved == 3000
    assert run.aggregate.total_runtime_saved == 25.0
    assert run.aggregate.mean_steps_saved == 2.5


def test_from_experiment_results_mixed_acceptance() -> None:
    """Some tasks accepted, some not — acceptance rate is fractional."""
    results = [
        _make_result("task-a", accepted=True),
        _make_result("task-b", accepted=False, steps_saved=None, tool_calls_saved=None, tokens_saved=None, runtime_saved=None),
    ]
    run = BenchmarkRun.from_experiment_results("mixed", results)

    assert run.aggregate.acceptance_rate == 0.5
    assert run.aggregate.tasks_accepted == 1
    assert run.aggregate.total_steps_saved == 2  # only from task-a


def test_from_experiment_results_empty() -> None:
    """Empty results produce zero metrics."""
    run = BenchmarkRun.from_experiment_results("empty", [])

    assert run.aggregate.total_tasks == 0
    assert run.aggregate.acceptance_rate == 0.0
    assert run.aggregate.mean_steps_saved == 0.0


def test_from_experiment_results_with_regressions() -> None:
    """Tasks with post-accept regressions are counted."""
    results = [
        ExperimentResult(
            task_id="regr",
            accepted=True,
            bound_stop_step=1,
            actual_stop_step=3,
            steps_saved=2,
            tool_calls_saved=5,
            tokens_saved=1000,
            runtime_saved=10.0,
            post_solution_unnecessary_steps=2,
            tests_pass_at_bound_stop=True,
            required_checks_pass_at_bound_stop=True,
            regressions_after_accept=3,
        ),
    ]
    run = BenchmarkRun.from_experiment_results("regr-suite", results)

    assert run.aggregate.tasks_with_regressions == 1


# ---------------------------------------------------------------------------
# BenchmarkRunner
# ---------------------------------------------------------------------------


def test_runner_creates_run() -> None:
    """BenchmarkRunner.run_suite returns a BenchmarkRun."""
    runner = BenchmarkRunner()
    run = runner.run_suite("smoke")

    assert isinstance(run, BenchmarkRun)
    assert run.suite_name == "smoke"
    assert len(run.tasks) == 2
    assert run.aggregate.total_tasks == 2
    assert len(run.run_id) == 12


def test_runner_run_all_suites() -> None:
    """run_all_suites returns both built-in suites."""
    runner = BenchmarkRunner()
    suites = runner.run_all_suites()

    assert set(suites.keys()) == {"smoke", "full"}
    for run in suites.values():
        assert isinstance(run, BenchmarkRun)


def test_runner_missing_fixture_raises() -> None:
    """Referencing a nonexistent fixture raises FileNotFoundError."""
    runner = BenchmarkRunner()
    with pytest.raises(FileNotFoundError):
        runner.run_suite("nonexistent_task")


def test_runner_custom_suite() -> None:
    """Custom comma-separated tasks work."""
    runner = BenchmarkRunner()
    run = runner.run_suite("clean_accept,never_accept")

    assert len(run.tasks) == 2
    assert run.tasks[0].task_id == "clean_accept"
    assert run.tasks[1].task_id == "never_accept"


# ---------------------------------------------------------------------------
# BUILTIN_SUITES
# ---------------------------------------------------------------------------


def test_builtin_suites_have_required_fixtures() -> None:
    """All tasks in built-in suites correspond to existing trajectory files."""
    from pathlib import Path

    traj_dir = Path(__file__).resolve().parent.parent / "benchmarks" / "trajectories"

    for suite_name, tasks in BUILTIN_SUITES.items():
        for task in tasks:
            assert (traj_dir / f"{task}.json").exists(), (
                f"Fixture {task}.json referenced by suite '{suite_name}' not found"
            )


# ---------------------------------------------------------------------------
# AggregateMetrics model
# ---------------------------------------------------------------------------


def test_aggregate_metrics_defaults() -> None:
    """AggregateMetrics defaults are zero."""
    m = AggregateMetrics()
    assert m.total_tasks == 0
    assert m.total_steps_saved == 0
    assert m.acceptance_rate == 0.0
    assert m.mean_steps_saved == 0.0


# ---------------------------------------------------------------------------
# TaskBenchmarkResult model
# ---------------------------------------------------------------------------


def test_task_result_model() -> None:
    """TaskBenchmarkResult accepts valid data."""
    t = TaskBenchmarkResult(task_id="test", accepted=True, steps_saved=3)
    assert t.task_id == "test"
    assert t.steps_saved == 3


def test_task_result_json_roundtrip() -> None:
    """TaskBenchmarkResult round-trips through JSON."""
    t = TaskBenchmarkResult(task_id="test", accepted=True, steps_saved=5)
    json_str = t.model_dump_json()
    t2 = TaskBenchmarkResult.model_validate_json(json_str)
    assert t2 == t
