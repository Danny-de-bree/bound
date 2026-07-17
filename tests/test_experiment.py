from __future__ import annotations

from pathlib import Path

import pytest

from bound.experiment import (
    ExperimentResult,
    StepRecord,
    load_trajectories,
    load_trajectory,
    run_experiment,
    save_trajectory,
    summarize,
)
from bound.models import (
    AgentStep,
    AgentTrajectory,
    BoundCriteria,
    CodingWorkflowSignals,
    EvaluationScores,
    WorkflowNormalization,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

#: A single, conservative criteria used across the benchmark replays. Threshold
#: ``T = 0.6`` with default weights and margins. The fixtures were authored so
#: that a satisfying state crosses this threshold and an unsatisfying one does
#: not, making the stop step and savings unambiguous.
_CRITERIA = BoundCriteria(threshold=0.6)

#: Default v0.2 normalization caps (made explicit so the cost terms are
#: reproducible from the fixtures alone).
_NORMALIZATION = WorkflowNormalization()

_TRAJECTORIES_DIR = Path(__file__).resolve().parent.parent / "benchmarks" / "trajectories"


def _run_fixture(name: str) -> ExperimentResult:
    """Load and replay one benchmark trajectory fixture by stem name."""
    trajectory = load_trajectory(_TRAJECTORIES_DIR / f"{name}.json")
    return run_experiment(trajectory, _CRITERIA, _NORMALIZATION)



# ---------------------------------------------------------------------------
# Correct first ACCEPT step
# ---------------------------------------------------------------------------


def test_correct_first_accept_step() -> None:
    """The BOUND stop step is the first step whose decision is ``ACCEPT``.

    ``clean_accept.json`` reaches a satisfying state at step 1 (all gates green)
    and then keeps polishing. BOUND must stop at step 1 — not step 0 (still
    failing) and not later (which would waste work). This is the foundational
    claim the harness exists to evidence.
    """
    result = _run_fixture("clean_accept")

    assert result.accepted is True
    assert result.bound_stop_step == 1

    # Step 0 is below threshold; step 1 is the first ACCEPT.
    decisions = [record.decision for record in result.per_step]
    assert decisions[0] != "ACCEPT"
    assert decisions[1] == "ACCEPT"


def test_first_accept_is_the_earliest_accepting_step() -> None:
    """No later step can be reported as the bound stop if an earlier one accepted.

    Guards against an off-by-one that would record the *last* ACCEPT instead of
    the *first*, which would understate BOUND's savings.
    """
    result = _run_fixture("clean_accept")

    assert result.bound_stop_step == 1
    assert result.per_step[1].decision == "ACCEPT"
    assert result.per_step[2].decision == "ACCEPT"
    assert result.per_step[3].decision == "ACCEPT"


# ---------------------------------------------------------------------------
# Correct steps saved
# ---------------------------------------------------------------------------


def test_correct_steps_saved() -> None:
    """Steps, tool calls, tokens and runtime saved are computed from the replay.

    In ``clean_accept`` the agent stopped at step 3 but BOUND would have stopped
    at step 1, so the two post-solution steps (2 and 3) are pure waste. The
    cumulative signal deltas must reflect exactly the work spent on those steps.
    """
    result = _run_fixture("clean_accept")

    assert result.actual_stop_step == 3
    assert result.bound_stop_step == 1
    assert result.steps_saved == 2
    assert result.post_solution_unnecessary_steps == 2
    # tool_call_count: 15 (step 3) - 8 (step 1) = 7
    assert result.tool_calls_saved == 7
    # token_usage: 12000 - 6000 = 6000
    assert result.tokens_saved == 6000
    # execution_time_seconds: 35 - 15 = 20
    assert result.runtime_saved == pytest.approx(20.0)


def test_retry_then_accept_saves_zero_steps() -> None:
    """When the agent only reaches the threshold on its final step, BOUND saves 0.

    ``retry_then_accept`` accepts exactly at the real stop step (step 2), so
    BOUND adds no early-stop savings. This is the no-regression baseline: BOUND
    must not invent savings where none exist.
    """
    result = _run_fixture("retry_then_accept")

    assert result.bound_stop_step == 2
    assert result.actual_stop_step == 2
    assert result.steps_saved == 0
    assert result.post_solution_unnecessary_steps == 0
    assert result.regressions_after_accept == 0


def test_realistic_coding_task_savings() -> None:
    """A multi-step test/lint/fix iteration stops as soon as all gates pass.

    ``realistic_coding_task`` crosses the threshold at step 3 (types finally
    pass); the agent then refactors (step 4) and writes docs (step 5) without
    improving acceptance. BOUND should stop at step 3 and save 2 steps.
    """
    result = _run_fixture("realistic_coding_task")

    assert result.bound_stop_step == 3
    assert result.actual_stop_step == 5
    assert result.steps_saved == 2
    assert result.tool_calls_saved == 8  # 23 - 15
    assert result.tokens_saved == 7000  # 19000 - 12000
    assert result.runtime_saved == pytest.approx(28.0)  # 68 - 40
    assert result.regressions_after_accept == 0


# ---------------------------------------------------------------------------
# Regression after accept
# ---------------------------------------------------------------------------


def test_regression_after_accept_scenario() -> None:
    """BOUND stops before the agent regresses, avoiding the broken later steps.

    ``regression_after_accept`` reaches a satisfying state at step 1, then the
    agent keeps editing and breaks tests (step 2 rolls back, step 3 replans).
    The harness must (a) still report step 1 as the bound stop, (b) count the
    two non-ACCEPT steps as regressions, and (c) confirm tests passed at the
    bound stop — i.e. BOUND stops at a genuinely good state, not the broken one.
    """
    result = _run_fixture("regression_after_accept")

    assert result.bound_stop_step == 1
    assert result.actual_stop_step == 3
    assert result.steps_saved == 2
    assert result.regressions_after_accept == 2
    # The two post-accept steps are ROLLBACK and REPLAN, never ACCEPT.
    assert result.per_step[2].decision != "ACCEPT"
    assert result.per_step[3].decision != "ACCEPT"
    # BOUND stopped at a clean state.
    assert result.tests_pass_at_bound_stop is True
    assert result.required_checks_pass_at_bound_stop is True


# ---------------------------------------------------------------------------
# Trajectory with no ACCEPT result
# ---------------------------------------------------------------------------


def test_trajectory_with_no_accept_result() -> None:
    """A trajectory that never crosses the threshold yields no bound stop.

    ``never_accept`` improves monotonically but never reaches ``T``. BOUND must
    report ``accepted=False`` with ``bound_stop_step=None`` and *no* claimed
    savings (``steps_saved=None``), while treating post-solution work as zero
    because no solution was ever reached.
    """
    result = _run_fixture("never_accept")

    assert result.accepted is False
    assert result.bound_stop_step is None
    assert result.steps_saved is None
    assert result.tool_calls_saved is None
    assert result.tokens_saved is None
    assert result.runtime_saved is None
    # No satisfying state existed, so nothing was "unnecessary after success".
    assert result.post_solution_unnecessary_steps == 0
    assert result.regressions_after_accept == 0
    assert result.tests_pass_at_bound_stop is None
    assert all(record.decision != "ACCEPT" for record in result.per_step)


# ---------------------------------------------------------------------------
# Pre-supplied scores path + determinism + IO
# ---------------------------------------------------------------------------


def test_pre_supplied_scores_are_used() -> None:
    """Steps carrying ``EvaluationScores`` bypass the workflow evaluator.

    The harness must route pre-computed scores through the same policy decision
    rule (rather than ignoring them), so a trajectory with manually scored steps
    still produces the correct ACCEPT. Here step 0 is given high-acceptance
    scores and must ACCEPT immediately.
    """
    trajectory = AgentTrajectory(
        task_id="scored",
        steps=[
            AgentStep(
                step_index=0,
                signals=CodingWorkflowSignals(test_pass_rate=1.0),
                scores=EvaluationScores(
                    acceptance=0.9,
                    influence=0.0,
                    risk=0.05,
                    cost=0.05,
                ),
            ),
        ],
        actual_stop_step=0,
    )

    result = run_experiment(trajectory, _CRITERIA)

    assert result.accepted is True
    assert result.bound_stop_step == 0
    assert result.per_step[0].decision == "ACCEPT"
    assert result.per_step[0].scores.acceptance == pytest.approx(0.9)
    assert result.steps_saved == 0


def test_run_experiment_is_deterministic() -> None:
    """Replaying the same trajectory twice yields identical results.

    The harness must be reproducible: same inputs -> same per-step decisions and
    savings, with no hidden state or randomness.
    """
    first = _run_fixture("clean_accept")
    second = _run_fixture("clean_accept")

    assert first.model_dump() == second.model_dump()


def test_per_step_length_matches_trajectory() -> None:
    """Every trajectory step produces exactly one ``StepRecord``.

    Guards against the harness skipping steps or emitting duplicate records.
    """
    result = _run_fixture("realistic_coding_task")

    assert len(result.per_step) == 6
    assert [r.step_index for r in result.per_step] == [0, 1, 2, 3, 4, 5]
    assert all(isinstance(r, StepRecord) for r in result.per_step)


def test_load_save_round_trip() -> None:
    """Saving then loading a trajectory is lossless.

    Ensures the JSON fixtures are round-trippable through the serialiser, so
    benchmark trajectories can be regenerated and audited.
    """
    original = load_trajectory(_TRAJECTORIES_DIR / "clean_accept.json")
    tmp_path = Path(__file__).resolve().parent / "_tmp_round_trip.json"
    try:
        save_trajectory(original, tmp_path)
        reloaded = load_trajectory(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    assert reloaded == original


def test_load_trajectories_loads_all_fixtures() -> None:
    """``load_trajectories`` loads every fixture and they all replay cleanly.

    Asserts the benchmark suite contains the five required scenario types and
    that each produces a consistent ``accepted`` flag, giving end-to-end
    evidence the harness runs over the whole suite.
    """
    trajs = load_trajectories(_TRAJECTORIES_DIR)

    expected = {
        "clean_accept",
        "retry_then_accept",
        "regression_after_accept",
        "never_accept",
        "realistic_coding_task",
    }
    assert expected.issubset(trajs.keys())

    accepted_flags = {
        name: run_experiment(t, _CRITERIA, _NORMALIZATION).accepted
        for name, t in trajs.items()
    }
    assert accepted_flags["clean_accept"] is True
    assert accepted_flags["retry_then_accept"] is True
    assert accepted_flags["regression_after_accept"] is True
    assert accepted_flags["realistic_coding_task"] is True
    assert accepted_flags["never_accept"] is False


def test_summarize_reports_key_evidence() -> None:
    """``summarize`` renders the headline metrics a human can read.

    The report is the v0.2 "evidence" surface: it must name the task, the BOUND
    vs actual stop steps, and the savings, so a reader can see at a glance where
    BOUND would stop and how much work it avoids.
    """
    result = _run_fixture("regression_after_accept")
    report = summarize(result)

    assert "task=regression_after_accept" in report
    assert "BOUND stop step=1" in report
    assert "actual stop step=3" in report
    assert "steps_saved=2" in report
    assert "regressions_after_accept=2" in report
    assert "tests_pass_at_bound_stop=yes" in report

