# BOUND v1.0.0 — Production Execution Harness: Design Plan

> **Status:** Design proposal. **Target:** v1.0.0. **Author:** Quality & Benchmarking Lead.
> **Date:** 2026-07-25. **Current codebase:** v0.8.1.

---

## Table of Contents

1. [Audit of Existing Code](#1-audit-of-existing-code)
2. [Benchmark Runner Architecture](#2-benchmark-runner-architecture)
3. [Benchmarking Capabilities Design](#3-benchmarking-capabilities-design)
4. [Controller Evaluation Metrics](#4-controller-evaluation-metrics)
5. [Report Format Designs](#5-report-format-designs)
6. [Release Quality Checklist](#6-release-quality-checklist)
7. [Implementation Phases](#7-implementation-phases)

---
## 1. Audit of Existing Code

### 1.1 `src/bound/experiment.py` — Current experiment harness

**What exists:**
- `StepRecord` — per-step BOUND decision, score, and scores recorded during replay.
- `ExperimentResult` — full replay result with `task_id`, `accepted`, `bound_stop_step`,
  `actual_stop_step`, `steps_saved`, `tool_calls_saved`, `tokens_saved`, `runtime_saved`,
  `post_solution_unnecessary_steps`, `tests_pass_at_bound_stop`,
  `required_checks_pass_at_bound_stop`, `regressions_after_accept`, `per_step`.
- `run_experiment(trajectory, criteria, normalization)` — replays a trajectory through
  `CodingWorkflowEvaluator` (or `StaticEvaluator` when scores are pre-supplied), then
  through `BoundPolicy`. Returns `ExperimentResult`.
- `load_trajectory`, `save_trajectory`, `load_trajectories` — JSON persistence.
- `summarize(result)` — Plain-text summary for CLI/notebook.

**What's missing for v1.0.0:**
- No live paired execution (with/without BOUND). Purely offline trajectory replay.
- No independent verification step — trusts trajectory signals without re-running real
  verification commands.
- No HTML/MD report generation from `ExperimentResult`.
- No first-satisfactory-state detection independent of BOUND's own ACCEPT decision.
- No efficiency metric aggregation across a suite.
- No experiment reproducibility infrastructure (no config hash, no policy hash in results).

### 1.2 `src/bound/report.py` — Current report generation

**What exists:**
- `RunTrace` — machine-readable record of one real BOUND step evaluation. Rich model
  with contract, evidence, evaluation, next_action, feedback, raw_commands,
  decision_history, retries, replans, trajectory, telemetry, config snapshot,
  reported_action, observed_action. Schema version 2.0.
- `render_from_trace(run)` — renders a `RunTrace` into **Markdown**. Sections: header,
  score breakdown (A/I/R/C table + evidence table), evaluation, evidence coverage,
  decision assurance, collector failures, decision history, plan deviation, artifacts,
  final verification.
- `DecisionHistoryEntry`, `RawCommandRecord` — supporting models.
- Provenance tracking: `_PROVENANCE_STRENGTH`, `_REPORT_INDEPENDENT`,
  `_UNVERIFIABLE_STATUS` — clean classification of evidence trustworthiness.

**What's missing for v1.0.0:**
- **No HTML renderer.** v1.0.0 requires self-contained single-file HTML reports.
- No JSON report format for machine consumption.
- No suite-level report (aggregating multiple traces into one document).
- No embeddable charts or visualizations.

### 1.3 `benchmarks/` directory

**What exists:**
- `trajectories/` — 5 JSON fixtures: `clean_accept.json`, `retry_then_accept.json`,
  `regression_after_accept.json`, `never_accept.json`, `realistic_coding_task.json`.
  Offline, hand-authored `AgentTrajectory` fixtures with signals per step.
- `contracts/` — 12 plan fixtures for contract-quality benchmarking.

**What's missing:**
- No live benchmark task definitions (repositories, prompts, verification scripts).
- No runner orchestration. No paired-execution metadata.
- No benchmark registry or discovery mechanism.

### 1.4 `src/bound/runtime.py` — Public runtime API (v0.9.0)

**What exists:**
- `BoundRuntime.from_policy(path)` — loads and validates a policy, returns a runtime.
- `runtime.evaluate(context)` — single-step evaluation through the service layer.
- `runtime.start_run()` / `finish_run()` / `record_outcome()` — lineage management.
- `EvaluationContext`, `RunHandle`, `FinishRunResult`, `OutcomeRecordContext`,
  `OutcomeResult` — public models.

**Critical for v1.0.0:** The runtime is already the **single execution engine**. CLI,
MCP, UI, Agent Adapter, and (future) Benchmark Runner all call the same
`BoundRuntime.evaluate()`. The benchmark runner is a **client** of `BoundRuntime`,
not a separate engine. It calls `runtime.evaluate()` for each step, exactly like
the Agent Adapter does.

### 1.5 `src/bound/contract_evaluator.py` — Logic under evaluation

The `ContractEvaluator` (~1900 lines) derives A/I/R/C from contract + evidence
with full provenance tracking. For controller evaluation, we measure whether its
decisions match what an independent verifier would conclude.

### 1.6 `src/bound/lineage.py` — Reproducibility foundation

Already has `RunConfigSnapshot`, `compute_policy_hash`, `compute_contract_hash`,
`build_run_config`. These are the building blocks for reproducible experiments.

### 1.7 Summary of gaps

| Capability | v0.8.1 Status | v1.0.0 Need |
|---|---|---|
| Benchmark runner as runtime client | No runner | BoundRuntime-based runner |
| Paired execution (with/without BOUND) | Not implemented | Core benchmark mode |
| Independent verification | Provenance model only | Independent verifier per step |
| HTML reports | Markdown only | Self-contained single-file HTML |
| JSON reports | model_dump_json, no suite agg | Suite-level JSON report |
| First satisfactory state detection | Only BOUND's ACCEPT | Independent assessment |
| Efficiency metrics | Per-experiment only | Suite-level aggregate metrics |
| Reproducible experiments | Config snapshot unused | Full reproducibility manifest |
| Controller self-evaluation | Not implemented | All error-type metrics |
| Deterministic replay verification | Not implemented | Replay every decision |

---

## 2. Benchmark Runner Architecture

### 2.1 Core principle: benchmark runner is a runtime client

The benchmark runner uses the **same `BoundRuntime`** as every other client.
There is no separate execution engine:

```
                    BoundRuntime
   (single shared evaluation + lineage engine)
     │         │        │        │         │
   CLI       MCP       UI     Agent    Benchmark
                             Adapter    Runner
```

The benchmark runner:
1. Loads a policy via `BoundRuntime.from_policy(path)`.
2. For each benchmark task, runs the agent **with** BOUND (using
   `runtime.evaluate()` at each step) and **without** BOUND (agent runs freely).
3. Records both trajectories.
4. Computes comparative metrics.
5. Generates reports.

### 2.2 Module layout

```
src/bound/
├── benchmark/                  # New package
│   ├── __init__.py             # Public exports
│   ├── runner.py               # BenchmarkRunner — orchestration
│   ├── tasks.py                # BenchmarkTask, TaskRegistry
│   ├── executor.py             # PairedExecutor — runs with/without BOUND
│   ├── verifier.py             # IndependentVerifier — post-step verification
│   ├── metrics.py              # BenchmarkMetrics, EfficiencyMetrics
│   ├── controller_eval.py      # ControllerEvaluator — self-evaluation
│   └── report_writer.py        # HTML/JSON/Markdown report generation
```

### 2.3 `BenchmarkRunner` design

```python
class BenchmarkRunner:
    """Orchestrates benchmark execution using the shared BoundRuntime.

    The runner is a thin orchestration layer. All evaluation goes through
    BoundRuntime.evaluate(); the runner manages task lifecycle, paired
    execution, independent verification, and report generation.
    """

    def __init__(self, runtime: BoundRuntime, registry: TaskRegistry):
        ...

    def run_suite(
        self,
        tasks: list[str] | None = None,       # task IDs; None = all
        *,
        paired: bool = True,                   # run with/without BOUND
        verify: bool = True,                   # run independent verification
        repeat: int = 1,                       # repetition count
    ) -> BenchmarkSuiteResult: ...

    def run_single(
        self,
        task_id: str,
        *,
        paired: bool = True,
        verify: bool = True,
    ) -> BenchmarkTaskResult: ...
```

### 2.4 Paired execution flow

```
For each task (repeat n times):
  1. PREPARE: Reset workspace to clean state (git reset --hard, git clean -fd)
  2. RUN BASELINE (without BOUND):
     a. Start agent, let it run freely until stop condition
     b. Record AgentTrajectory (all steps, all signals)
     c. Run independent verifier at each step
  3. RESET: Reset workspace to same clean state
  4. RUN BOUND (with BOUND):
     a. Start agent with BOUND integration active
     b. At each step, agent proposes action -> BOUND evaluates -> control returned
     c. Record AgentTrajectory + BOUND decisions
     d. Run independent verifier at each step
  5. COMPARE: Compute paired metrics
  6. RECORD: Write BenchmarkTaskResult
```

### 2.5 Task definition

```python
class BenchmarkTask(BaseModel):
    """Definition of one benchmark task."""
    task_id: str
    description: str
    repository: str                           # git URL or local path
    base_ref: str = "main"                    # git ref for clean state
    prompt: str                               # agent task prompt
    acceptance_criteria: list[str]            # human-readable success criteria
    verification_commands: list[str]          # e.g. ["uv run pytest -q"]
    expected_artifacts: list[str]             # files that should be produced
    max_steps: int = 20
    timeout_seconds: int = 600
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    tags: list[str] = Field(default_factory=list)

class TaskRegistry:
    """Discover and load benchmark tasks."""
    def __init__(self, directories: list[Path]): ...
    def list(self) -> list[str]: ...
    def get(self, task_id: str) -> BenchmarkTask: ...
    def register(self, task: BenchmarkTask) -> None: ...
```

### 2.6 How it uses BoundRuntime

The benchmark runner calls `BoundRuntime.evaluate()` with `EvaluationContext`
populated from the agent's proposed action and observed signals. The runtime
returns an `EvaluationResult` with `decision`, `score`, `scores`, `feedback`.
The runner maps `decision` to a control action via the same `DECISION_TO_CONTROL`
mapping used by the Agent Adapter. This is identical to how the Agent Adapter
works — the benchmark runner is just another consumer of the same interface.

```python
# Inside PairedExecutor._step_with_bound():
context = EvaluationContext(
    task_id=task.task_id,
    step_id=f"{task.task_id}-{step_index:03d}",
    attempt=retry_count + 1,
    action=agent_proposed_action,
    metadata={"signals_json": observed_signals.model_dump_json()},
)
result = self.runtime.evaluate(context)
control_action = DECISION_TO_CONTROL[result.decision]
```

---

## 3. Benchmarking Capabilities Design

### 3.1 Paired execution (with/without BOUND)

**Goal:** For the same task, same starting state, same agent — run once with
BOUND interposed and once without. Measure the difference.

**Metrics collected:**

| Metric | Baseline (no BOUND) | BOUND | Delta |
|---|---|---|---|
| Steps to completion | actual_stop_step | bound_stop_step | steps_saved |
| Tool calls | total across steps | total up to stop | tool_calls_saved |
| Tokens consumed | total | total up to stop | tokens_saved |
| Wall-clock time | total seconds | seconds up to stop | runtime_saved |
| Task success | verifier passes at end | verifier passes at BOUND stop | — |
| Quality score | verifier score at end | verifier score at BOUND stop | — |

**Data model:**

```python
class PairedResult(BaseModel):
    task_id: str
    baseline: SingleRunResult          # run without BOUND
    bound: SingleRunResult             # run with BOUND
    savings: EfficiencyMetrics
    quality_comparison: QualityComparison

class SingleRunResult(BaseModel):
    run_id: str
    condition: Literal["baseline", "bound"]
    trajectory: AgentTrajectory
    decisions: list[Decision] | None   # None for baseline
    verification_results: list[VerificationResult]
    first_satisfactory_step: int | None
    task_success: bool
    metrics: RunMetrics

class QualityComparison(BaseModel):
    baseline_success: bool
    bound_success: bool
    quality_regression: bool    # BOUND stopped too early (success degraded)
    quality_improvement: bool   # BOUND avoided degradation
    quality_same: bool
```

### 3.2 Independent verification

**Current state:** v0.8.1 has `EvidenceProvenance` distinguishing CLAIMED/
VERIFIED/OBSERVED, and collectors can produce VERIFIED evidence. But there's no
systematic independent verification pass per step.

**v1.0.0 design:** Each benchmark task defines `verification_commands`. After
each agent step (in both baseline and BOUND conditions), the `IndependentVerifier`
runs these commands and captures structured results:

```python
class VerificationResult(BaseModel):
    step_index: int
    commands: list[CommandResult]       # exit code, stdout, stderr
    tests_pass: bool
    lint_pass: bool
    type_check_pass: bool
    required_checks_pass: bool
    unexpected_files: list[str]
    produced_artifacts: list[str]
    overall_pass: bool                  # all gates green

class CommandResult(BaseModel):
    command: str
    returncode: int
    stdout: str
    stderr: str
    elapsed_seconds: float
```

The independent verifier is the **ground truth** for controller evaluation.
BOUND's decisions are compared against what the verifier actually found.

### 3.3 First satisfactory state detection

**Definition:** The earliest step where the independent verifier reports
`overall_pass = True`. This is **independent** of BOUND's ACCEPT decision —
it's the verifier's assessment, not BOUND's.

**Why this matters:** BOUND might ACCEPT too early (false ACCEPT — verifier still
failing) or too late (missed ACCEPT — verifier already passing, BOUND still saying
RETRY/REPLAN). The delta between `first_satisfactory_step` (verifier) and
`bound_stop_step` (BOUND) is a direct measure of BOUND's timing accuracy.

```python
class FirstSatisfactoryResult(BaseModel):
    task_id: str
    first_satisfactory_step: int | None   # None = never reached
    bound_stop_step: int | None
    bound_too_early: bool       # BOUND ACCEPTed before verifier was satisfied
    bound_too_late: int | None  # steps BOUND waited after verifier satisfied
    bound_right_on_time: bool   # BOUND stopped at the same step
```

### 3.4 Efficiency metrics (suite aggregation)

```python
class SuiteEfficiencyMetrics(BaseModel):
    """Aggregate efficiency across all tasks in a suite."""
    total_tasks: int
    tasks_with_baseline: int
    tasks_with_bound: int
    paired_tasks: int                      # tasks run in both conditions

    # Steps
    total_baseline_steps: int
    total_bound_steps: int
    total_steps_saved: int
    mean_steps_saved: float
    median_steps_saved: float
    steps_saved_p90: float

    # Tool calls
    total_tool_calls_saved: int
    mean_tool_calls_saved: float

    # Tokens
    total_tokens_saved: int
    mean_tokens_saved: float

    # Runtime
    total_runtime_saved_seconds: float
    mean_runtime_saved_seconds: float

    # Quality
    tasks_with_quality_regression: int     # BOUND stopped too early
    tasks_with_quality_preserved: int      # BOUND stopped earlier, same quality
    tasks_with_no_savings: int             # BOUND never accepted or same step

    # First satisfactory state
    tasks_below_bound_stop: int            # verifier satisfied before BOUND
    tasks_at_bound_stop: int               # verifier satisfied at same step
    tasks_above_bound_stop: int            # BOUND stopped before verifier
```

### 3.5 Reproducible experiments

Every benchmark run records a reproducibility manifest:

```python
class BenchmarkRunManifest(BaseModel):
    run_id: str
    timestamp: UTCDateTime
    bound_version: str
    bound_policy_hash: str                 # canonical policy hash
    policy_config_snapshot: RunConfigSnapshot
    task_registry_hash: str                # hash of all task definitions
    verifier_version: str                  # hash/version of verification scripts
    git_commits: dict[str, str]            # task_id -> commit SHA
    python_version: str
    platform: str
    seed: int | None
    suite_result: BenchmarkSuiteResult
```

The manifest is written alongside results. For BOUND-only replay, results are
fully deterministic.

---

## 4. Controller Evaluation Metrics

### 4.1 Ground truth definition

For controller evaluation, the **independent verifier** provides ground truth
at each step. After each agent step, the verifier runs `verification_commands`
and produces a `VerificationResult` with `overall_pass`.

### 4.2 Decision correctness definitions

Given:
- `B` = BOUND's decision at step `i`
- `V` = verifier's `overall_pass` at step `i`
- `V_prev` = verifier's `overall_pass` at step `i-1` (or `False` if step 0)
- `step_is_last` = whether this is the agent's last step

| BOUND decision | Verifier state | Correct? | Metric if wrong |
|---|---|---|---|
| ACCEPT | `V = True` AND (`step_is_last` or `V_prev = True`) | Correct accept | — |
| ACCEPT | `V = False` | False ACCEPT | `false_accept` |
| RETRY | `V = False` AND recoverable error | Correct retry | — |
| RETRY | `V = True` | False RETRY | `false_retry` |
| REPLAN | `V = False` AND approach wrong | Correct replan | — |
| REPLAN | `V = True` | False REPLAN | `false_replan` |
| ROLLBACK | `V = False` AND unrecoverable damage | Correct rollback | — |
| ROLLBACK | `V = True` or recoverable situation | False ROLLBACK | `false_rollback` |

### 4.3 Formal metric definitions

```python
class ControllerEvaluation(BaseModel):
    """BOUND self-evaluation metrics for one task."""
    task_id: str
    total_steps: int
    total_decisions: int

    # False positives (BOUND said stop/go but was wrong)
    false_accept: int = 0       # BOUND ACCEPTed but verifier was failing
    false_retry: int = 0        # BOUND said RETRY but verifier was passing
    false_replan: int = 0       # BOUND said REPLAN but verifier was passing
    false_rollback: int = 0     # BOUND said ROLLBACK unnecessarily

    # False negatives (BOUND missed the right moment)
    missed_accept: int = 0      # Verifier passing, BOUND didn't ACCEPT
    late_accept_steps: int = 0  # Steps between first verifier pass and BOUND ACCEPT

    # Composite scores (0.0 = worst, 1.0 = best)
    precision: float            # accepted / (accepted + false_accept)
    recall: float               # accepted / (accepted + missed_accept)
    f1_score: float             # harmonic mean of precision and recall
    decision_accuracy: float    # fraction of decisions matching ground truth
    timing_error_steps: float   # mean absolute error in stop-step timing

    # Per-step details
    per_step: list[StepEvaluation]

class StepEvaluation(BaseModel):
    step_index: int
    bound_decision: Decision
    verifier_pass: bool
    verifier_prev_pass: bool
    correct: bool
    error_type: Literal[
        "none",
        "false_accept", "false_retry", "false_replan", "false_rollback",
        "missed_accept",
    ] | None
    explanation: str            # human-readable explanation of the error
```

### 4.4 Composite controller health score

```python
class ControllerHealth(BaseModel):
    """Aggregate controller evaluation across a full benchmark suite."""
    total_decisions: int
    correct_decisions: int
    decision_accuracy: float        # correct / total

    total_false_accept: int
    total_false_retry: int
    total_false_replan: int
    total_false_rollback: int
    total_missed_accept: int

    # Weighted severity: false_accept and false_rollback are worse than
    # false_retry and false_replan (direct quality/safety impact)
    severity_weighted_error_rate: float

    # Timing
    mean_timing_error_steps: float
    median_timing_error_steps: float
    p90_timing_error_steps: float

    # Per-task breakdown
    per_task: dict[str, ControllerEvaluation]

    # Health grade
    grade: Literal["A", "B", "C", "D", "F"]
    grade_explanation: str
```

**Grading scale:**
- **A** (>=95% accuracy, 0 false_accept, 0 false_rollback): Production-ready.
- **B** (>=90% accuracy, <=1% false_accept+false_rollback): Minor tuning needed.
- **C** (>=80% accuracy, <=5% severe errors): Significant tuning needed.
- **D** (>=70% accuracy): Major issues.
- **F** (<70% accuracy): Not production-ready.

### 4.5 Deterministic replay verification

For every BOUND decision made during a benchmark run, we can deterministically
replay it:

1. **Record** the exact inputs (`EvaluationContext`, `CodingWorkflowSignals`,
   `BoundCriteria`) that produced each decision.
2. **Replay** by calling `BoundRuntime.evaluate()` with the same inputs.
3. **Verify** that the replayed decision, score, and scores are bit-identical.

```python
class ReplayVerification(BaseModel):
    total_decisions: int
    replayed_decisions: int
    identical_decisions: int
    identical_scores: int
    any_divergence: bool
    per_divergence: list[ReplayDivergence]

class ReplayDivergence(BaseModel):
    step_index: int
    original_decision: Decision
    replayed_decision: Decision
    original_score: float
    replayed_score: float
    input_hash: str
```

### 4.6 Policy consistency

Across repeated runs of the same task (same seed, same policy), BOUND must
produce identical decisions at identical steps. Any divergence indicates
non-determinism in the evaluation pipeline.

```python
class PolicyConsistency(BaseModel):
    task_id: str
    repeat_count: int
    decision_sequences: list[list[Decision]]
    all_identical: bool
    divergent_at_step: int | None
    divergence_details: list[str]
```

---

## 5. Report Format Designs

### 5.1 Self-contained HTML report (single-file)

The HTML report is the primary v1.0.0 deliverable. Requirements:
- **Single-file:** No external CSS, JS, images, or fonts. All styles inlined.
  All data embedded.
- **Self-contained:** Open `report.html` in any browser and see everything.
- **Printable:** CSS `@media print` for paper output.
- **Accessible:** Semantic HTML, ARIA labels, keyboard-navigable.

#### Report structure

```
BOUND Benchmark Report
├── Header (timestamp, bound version, policy hash, run_id)
├── Executive Summary
│   ├── Grade badge (A/B/C/D/F)
│   ├── Key numbers: tasks, decisions, accuracy, savings
│   └── Verdict paragraph
├── Controller Health
│   ├── Decision accuracy gauge (CSS-only)
│   ├── False accept/retry/replan/rollback counts
│   ├── Timing accuracy
│   └── Per-task health table
├── Efficiency
│   ├── Steps/tool-calls/tokens/runtime saved
│   ├── Per-task savings table
│   └── CSS bar charts (no JS)
├── Paired Execution
│   ├── Per-task comparison cards
│   └── Aggregate summary
├── First Satisfactory State
│   ├── Verifier vs BOUND stop-step comparison
│   └── Timing delta per task
├── Deterministic Replay
│   ├── All decisions identical? (pass/fail badge)
│   └── Divergences (if any)
├── Reproducibility Manifest
│   ├── Config hashes, Git commits, Environment
│   └── Task list with hashes
└── Footer (generated by BOUND vX.Y.Z, reproducible: <command>)
```

### 5.2 Markdown report

The existing `render_from_trace` produces per-step Markdown. For v1.0.0, add:
- **Suite-level Markdown:** `render_suite_md(suite) -> str` — same sections
  as HTML, in Markdown format.
- **Per-task Markdown:** Extended with controller evaluation sections.

### 5.3 JSON report (canonical format)

Machine-readable, complete, round-trippable:

```python
class BenchmarkReport(BaseModel):
    """Complete benchmark report, serializable to JSON."""
    schema_version: str = "1.0"
    run_id: str
    timestamp: str
    bound_version: str
    manifest: BenchmarkRunManifest
    controller_health: ControllerHealth
    efficiency: SuiteEfficiencyMetrics
    paired_results: list[PairedResult]
    replay_verification: ReplayVerification
    policy_consistency: PolicyConsistency | None  # when repeat > 1
    tasks: dict[str, BenchmarkTaskResult]
```

The JSON report is the canonical data format. HTML and Markdown are derived
views of this model.

#### CSS approach

Minimal, inline `<style>` block. No frameworks. Dark header, light body.
Color-coded badges:
- Green: ACCEPT, correct, savings
- Amber: RETRY, REPLAN, marginal
- Red: ROLLBACK, false accept, regression
- Blue: informational

CSS bar charts for efficiency metrics (pure CSS, no JS):

```css
.bar-chart { display: flex; align-items: end; gap: 4px; height: 120px; }
.bar { flex: 1; min-height: 2px; }
.bar-baseline { background: var(--color-neutral); }
.bar-bound { background: var(--color-accept); }
```

#### Data embedding

All data embedded as `<script type="application/json" id="benchmark-data">`.
HTML is fully renderable without JS, but the embedded JSON enables
programmatic extraction.

### 5.4 Report generation API

```python
class ReportWriter:
    """Generate reports from benchmark results."""

    def __init__(self, report: BenchmarkReport): ...

    def write_html(self, path: Path) -> None: ...
    def write_markdown(self, path: Path) -> None: ...
    def write_json(self, path: Path) -> None: ...
    def write_all(self, directory: Path) -> None:
        """Write report.html, report.md, report.json to directory."""
```

---

## 6. Release Quality Checklist

### 6.1 Compatibility guarantees

BOUND v1.0.0 must define explicit compatibility guarantees in a new
`COMPATIBILITY.md` document covering:

- **Semantic Versioning:** MAJOR (breaking API/schema/decision changes),
  MINOR (new features with safe defaults), PATCH (bug fixes only).
- **Public API coverage:** `bound.runtime`, `bound.models`, `bound.benchmark`,
  `bound.cli`, `bound.policy_schema` are covered by semver.
- **Internal modules NOT covered:** `bound.services`, `bound.calculator`,
  `bound.evaluator`, `bound.lineage_store`.
- **Policy schema compatibility:** New fields use safe defaults; existing
  policies remain valid. `POLICY_SCHEMA_VERSION` tracks format.
- **Decision output stability:** Same inputs produce same decision (verified
  by guardian tests in CI).
- **Python version support:** v1.0.0 supports Python 3.12+. Support dropped
  6 months after Python EOL.

### 6.2 Migration guide structure

Create `MIGRATION.md`:

- **v0.8.x -> v1.0.0:**
  - `bound.experiment` deprecated in favor of `bound.benchmark`.
  - `run_experiment()` -> `BenchmarkRunner`.
  - `summarize()` -> `ReportWriter`.
  - New benchmark runner with paired execution.
  - Self-contained HTML reports.
  - Controller self-evaluation.
  - Independent verification pass.
  - Reproducible experiment manifests.
  - Policy schema: no breaking changes. New optional `benchmark:` section.

### 6.3 Documentation needs

| Document | Status | v1.0.0 Action |
|---|---|---|
| `README.md` | Exists | Update quickstart with benchmark commands |
| `docs/python-usage.md` | Exists | Add benchmark runner API docs |
| `CONTRIBUTING.md` | Exists | Add benchmark contribution guide |
| `CHANGELOG.md` | Exists | v1.0.0 entry |
| `COMPATIBILITY.md` | Missing | **Create** — compatibility guarantees |
| `MIGRATION.md` | Missing | **Create** — v0.x -> v1.0 migration |
| `docs/benchmarking.md` | Missing | **Create** — how to run/write benchmarks |
| `docs/reports.md` | Missing | **Create** — report format documentation |
| `docs/release-process.md` | Missing | **Create** — release checklist/process |

### 6.4 Packaging requirements

All currently in place via `pyproject.toml`:
- hatchling build backend
- `bound = "bound.cli:main"` entry point
- `bound.benchmark` added as subpackage via `[tool.hatch.build.targets.wheel]`
- Dependencies: pydantic>=2.0, pyyaml>=6.0, coverage>=7.15.2

**New v1.0.0 packaging constraints:**
- **No new dependencies.** Benchmark runner uses only stdlib + existing deps.
- HTML report generation is pure Python string templating — no Jinja2, no
  markdown library.
- `src/bound/benchmark/` included in wheel package.

### 6.5 Release automation

Current `release.yml` is already excellent:
- Manual workflow dispatch with version input
- Version verification (pyproject.toml, bound.__version__, CHANGELOG.md)
- Quality gate (ruff, pytest, build)
- Build once: wheel + sdist + Skills ZIP + checksums
- GitHub Release with assets
- PyPI publish via OIDC trusted publishing

**v1.0.0 additions to release gate:**
- **Benchmark smoke test:** Run the minimal benchmark suite (5 existing
  trajectories) and verify all decisions are correct and deterministic.
- **Controller health check:** Verify `decision_accuracy >= 0.95` on the
  smoke suite. If BOUND's own decisions degrade, the release is blocked.
- **Deterministic replay check:** Verify that replaying all decisions
  produces identical results.

### 6.6 Release validation steps

Pre-release checklist (automated where possible):

```
[ ] Version bumped in pyproject.toml, bound/__init__.py
[ ] CHANGELOG.md entry complete for this version
[ ] All tests pass (uv run pytest -q)
[ ] Linter clean (uv run ruff check .)
[ ] Type checker clean (uv run mypy src/)
[ ] Package builds (uv build)
[ ] Skills ZIP builds and is deterministic
[ ] Benchmark smoke suite passes
[ ] Controller health grade A on smoke suite
[ ] Deterministic replay verified (all decisions identical)
[ ] All docs updated (README, docs/*, COMPATIBILITY.md, MIGRATION.md)
[ ] Migration guide covers all breaking changes
[ ] COMPATIBILITY.md reflects current guarantees
[ ] CI passes on main branch
[ ] Tag created and pushed
[ ] GitHub Release published
[ ] PyPI published
[ ] Post-release: smoke test pip install bound-policy==X.Y.Z
```

---

## 7. Implementation Phases

### 7.1 What can be built now (on v0.8.1)

These have no dependencies on future versions:

#### Phase 1a: Benchmark data models
- `BenchmarkTask`, `TaskRegistry` — task definition and discovery.
- `BenchmarkSuiteResult`, `BenchmarkTaskResult` — result models.
- `EfficiencyMetrics`, `SuiteEfficiencyMetrics` — aggregation models.
- `VerificationResult`, `CommandResult` — independent verifier output.
- `PairedResult`, `SingleRunResult`, `QualityComparison` — paired execution.
- `ControllerEvaluation`, `StepEvaluation`, `ControllerHealth` — self-eval.
- `ReplayVerification`, `ReplayDivergence`, `PolicyConsistency` — replay.
- `BenchmarkRunManifest` — reproducibility manifest.
- `BenchmarkReport` — unified report model.

**Dependencies:** Only `bound.models`, `bound.evidence`, `bound.lineage` —
all already stable.

#### Phase 1b: Independent verifier
- `IndependentVerifier` class that runs `verification_commands` in subprocesses.
- Produces `VerificationResult` for each step.
- Reuses `bound.command_collector` infrastructure where possible.

**Dependencies:** `bound.command_collector` (exists), `subprocess` (stdlib).

#### Phase 1c: Report writer
- `ReportWriter` class generating HTML, Markdown, JSON.
- HTML template as Python string with inline CSS.
- All report sections implemented.
- Can be tested with synthetic `BenchmarkReport` data.

**Dependencies:** `BenchmarkReport` model (Phase 1a). No runtime dependencies.

#### Phase 1d: Controller evaluator
- `ControllerEvaluator` — compares BOUND decisions against verifier results.
- Implements all metrics: false ACCEPT/RETRY/REPLAN/ROLLBACK, missed ACCEPT,
  precision/recall/F1.
- `ControllerHealth` grading logic.
- Replay verification (re-runs `BoundRuntime.evaluate()` with recorded inputs).

**Dependencies:** `BoundRuntime` (v0.9.0 API stable), `VerificationResult`.

#### Phase 1e: CLI commands
- `bound benchmark run [--tasks TASK...] [--repeat N] [--paired/--no-paired]`
- `bound benchmark list` — list available tasks.
- `bound benchmark report <run-id>` — generate reports from recorded results.
- `bound benchmark smoke` — run smoke benchmark suite (5 existing trajectories).
- `bound benchmark controller-health` — run controller self-evaluation.

**Dependencies:** `BenchmarkRunner` (Phase 2a), `TaskRegistry` (Phase 1a).

#### Phase 1f: Smoke benchmark tasks
- Port the 5 existing trajectory fixtures into `BenchmarkTask` definitions.
- Add verification commands that match the signals in the fixtures.
- Add 3–5 new realistic coding benchmark tasks (todo app, input validation,
  error handling).

### 7.2 What depends on v0.9.0 (runtime stabilization)

#### Phase 2a: Benchmark runner
- `BenchmarkRunner` orchestrating the full benchmark lifecycle.
- Uses `BoundRuntime` as the evaluation engine.
- Paired execution orchestration.

**Depends on:** `BoundRuntime` API stability. v0.9.0 already declares semver
coverage for the runtime module — this is effectively ready now.

#### Phase 2b: Deterministic replay verification integration
- Systematic replay of every decision from a benchmark run.
- Integration with `RunConfigSnapshot` for reproducibility.
- Policy hash recording in manifests.

**Depends on:** `compute_policy_hash` (exists), `RunConfigSnapshot` (exists).

### 7.3 What depends on v0.9.5 (collector/provenance hardening)

#### Phase 3a: Live paired execution with real agents
- Integration with agent adapters (Codex, Claude Code, Cline) for live runs.
- Real agent -> BOUND -> collector -> verification cycle.
- Currently possible in principle but needs collector reliability work
  tracked for v0.9.x.

#### Phase 3b: Production benchmark suite
- 20+ benchmark tasks across difficulty levels.
- Tasks covering: bug fixes, feature additions, refactoring, test writing,
  documentation.
- Reproducible environments (Docker or git-based).

### 7.4 Release readiness gates

Before v1.0.0 can ship:

```
[ ] All Phase 1 items complete and tested
[ ] All Phase 2 items complete and tested
[ ] Phase 3a: at least smoke-tested with one real agent
[ ] Controller health grade A on smoke suite
[ ] Deterministic replay: 100% identical decisions on smoke suite
[ ] All documentation complete (COMPATIBILITY.md, MIGRATION.md, docs/*)
[ ] Release automation updated with benchmark smoke gate
[ ] CI passes with benchmark gate enabled
[ ] One full manual release dry-run completed
```

### 7.5 Timeline estimate

| Phase | Scope | Effort |
|---|---|---|
| 1a | Benchmark data models | Small — pure Pydantic models |
| 1b | Independent verifier | Small — wraps existing collectors |
| 1c | Report writer | Medium — HTML templating + CSS |
| 1d | Controller evaluator | Medium — metric logic + replay |
| 1e | CLI commands | Small — thin wrappers |
| 1f | Smoke benchmark tasks | Small — port existing + add 3–5 |
| 2a | Benchmark runner | Medium — orchestration logic |
| 2b | Replay integration | Small — wiring existing pieces |
| 3a | Live agent integration | Large — depends on v0.9.x |
| 3b | Production suite | Large — 20+ task creation |
| Docs | All documentation | Medium |
| Release | Automation + gates | Small |

**Total estimated effort:** 3–4 weeks for a single developer, assuming
v0.9.0/v0.9.5 collector work proceeds in parallel.

---

## Appendices

### A. Key architectural invariants

1. **Benchmark runner is a BoundRuntime client.** It never imports internal
   service modules. It never duplicates evaluation logic. It never creates a
   separate execution path.

2. **Independent verifier is the ground truth for controller evaluation.**
   BOUND's own collectors produce evidence, but verifier results are the
   benchmark's source of truth.

3. **Reports are derived from `BenchmarkReport` model.** JSON is canonical;
   HTML and Markdown are views.

4. **Every benchmark run records a reproducibility manifest.** No result is
   publishable without the exact config that produced it.

5. **Controller evaluation uses verifier, not trajectory signals.** BOUND's
   decisions are compared against what the independent verifier found, not
   against what the agent claimed.

6. **No new dependencies.** v1.0.0 adds zero new package dependencies. HTML
   generation uses only Python stdlib string templating.

### B. Test strategy

Each new module is tested following existing patterns:

- **Unit tests** for all models, metric calculations, grade logic.
- **Integration tests** for verifier (runs real commands against fixture
  projects).
- **Integration tests** for benchmark runner (runs smoke suite end-to-end).
- **Controller evaluation tests** with synthetic decision sequences covering
  all error types.
- **Report tests** verifying HTML validity, JSON round-trip, Markdown rendering.
- **Architecture tests** enforcing no runtime imports internal modules.
- **Determinism tests** verifying identical results from identical inputs.
