# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-07-15

### Added

- **Evaluation contracts** (`bound.contracts`): machine-readable models that
  describe what success means *before* an agent executes a step —
  `AcceptanceCheck` (measurable, observable outcomes; `required` vs advisory),
  `RiskCheck` (named risk with `severity`), `StepBudget` (optional retries /
  tool-call / token / runtime ceilings), `StepContract`, and `BoundPlan`
  (validated, ordered sequence of step contracts plus a top-level goal).
- **`ContractGenerator` abstraction** (`bound.contracts`): a provider-agnostic
  Protocol that compiles a natural-language goal + plan into a validated
  `BoundPlan`. Ships a dependency-free `StaticContractGenerator` so the full
  contract pipeline runs with no API key, network, or LLM SDK. LLM-backed
  generators are optional and live outside the core (see the documented,
  import-free `bound.llm_adapters` seam).
- **Evidence models** (`bound.evidence`): `CheckEvidence` and
  `ExecutionEvidence` record what was *actually observed* after execution
  (which checks passed/failed, produced/unexpected artifacts, retry/tool/token/
  runtime usage, rollback availability), plus the environment-agnostic
  `EvidenceCollector` Protocol (typed against `object` so any execution handle
  flows through the same seam). The core never introspects the execution handle.
- **`ContractEvaluator`** (`bound.contract_evaluator`): the deterministic seam
  that turns a `StepContract` + `ExecutionEvidence` into `A / I / R / C` with
  full `ScoreEvidence` provenance — same contract + evidence → same scores, no
  network or LLM. Honest v0.3 reference heuristics (not calibrated weights):
  acceptance is reconciled by `id` with missing required evidence counted as
  FAILED; risk is additive and capped; cost is cap-normalized with conservative
  saturation for unmeasured declared budgets; influence defaults to `0.0` with
  an explicit honesty note.
- **`BoundWorkflow`** (`bound.bound_workflow`): thin orchestration of the
  contract pipeline via `prepare` (goal + plan → validated `BoundPlan`) and
  `evaluate_step` (executed step → `EvaluationResult`). It never decides; the
  decision comes from the deterministic `BoundPolicy`, and the contract scores
  are fed through the policy's unchanged decision pipeline via a throwaway
  `StaticEvaluator` (no double-scoring), with the `ContractEvaluator`
  provenance wired onto the result.
- **`ContractQualityReport`** (`bound.contract_quality`): a deterministic,
  structural smell test over a compiled `BoundPlan` (`measurable_ratio`,
  `acceptance_check_count`, `risk_check_count`, `has_budget`, `warnings`)
  detecting no checks, too many vague checks, duplicate ids, no observable
  verification method, and oversized contracts. No LLM, no semantic judgement —
  it judges whether a generated contract *appears* to define useful success
  criteria.
- **Automatic-contract experiment** (`bound.contract_quality`): a corpus of
  plans under `benchmarks/contracts` (≥10 fixtures, including a deliberate
  `measurable_but_irrelevant` blind spot) plus
  `run_contract_quality_experiment` and `summarize_contract_quality_experiment`
  that record per-plan findings and an honest account of what structural
  validation can and cannot judge.
- New public package exports: `AcceptanceCheck`, `RiskCheck`, `StepBudget`,
  `StepContract`, `BoundPlan`, `ContractGenerator`, `StaticContractGenerator`,
  `CheckEvidence`, `ExecutionEvidence`, `EvidenceCollector`,
  `ContractEvaluator`, `BoundWorkflow`, `ContractQualityReport`.

### Changed

- README gained a full v0.3 "Contract-based workflow" section documenting the
  `User goal → ContractGenerator → BoundPlan → StepContract → Agent executes →
  EvidenceCollector → ExecutionEvidence → ContractEvaluator → A/I/R/C → BOUND
  policy → Decision` architecture, the `prepare` / `evaluate_step` workflow, the
  no-LLM `StaticContractGenerator` path, the optional LLM-adapter boundary
  (structured data only — never a BOUND decision or A/I/R/C score), and the
  contract-quality report.
- The package module docstring lists the new v0.3 modules (`evidence`,
  `contract_evaluator`, `bound_workflow`, `contract_quality`, `llm_adapters`).
- Version bumped to `0.3.0`.

### Architecture invariants

- The package still works entirely without an LLM: no LLM SDK is a mandatory
  install dependency, importing `bound` loads no provider SDK, and the core
  reaches a deterministic decision with the socket primitive blocked. LLM-based
  contract generation is an optional convenience layer, not a requirement. No
  claim is made that BOUND improves agent performance; v0.3 produces
  reproducible evidence of the contract-based decision path.

## [0.2.0] - 2026-07-15

### Added

- Symmetric score weighting via `BoundWeights` (`acceptance`, `influence`,
  `risk`, `cost`, all default `1.0`). The score is now
  `S = (W_A×A) + (W_I×I) - (W_R×R) - (W_C×C)`; the v0.1 formula
  `S = (W×A) + I - R - C` is reproduced exactly by the defaults
  (`W_A = W`, `W_I = W_R = W_C = 1.0`).
- `BoundCriteria` now carries `retry_margin` (default `0.1`) and
  `rollback_risk_threshold` (default `0.8`).
- Coherent, fully-reachable decision semantics in `BoundPolicy`:
  `ROLLBACK` (hard risk boundary, checked first) → `ACCEPT` (`S >= T`) →
  `RETRY` (`gap = T - S <= retry_margin`) → `REPLAN` (fall-through). This
  replaces the v0.1 `risk > cost` / `cost > risk` / `risk == cost` rule; a
  high-scoring but unsafe action still rolls back.
- `distance_to_threshold` (signed `S - T`) on every `EvaluationResult`.
- `ScoreEvidence` and per-dimension `provenance` on `EvaluationResult`, so a
  consumer can answer "why is `A = 0.85`?".
- `CodingWorkflowSignals` and `WorkflowNormalization`: provider-agnostic
  signals captured from a coding-agent run (test pass rate, lint/type-check,
  retries, tool calls, token usage, file changes, …) with explicit caps.
- `CodingWorkflowEvaluator`: the first evaluator that derives `A / I / R / C`
  from deterministic workflow evidence (no LLM), exposing auditable
  `ScoreEvidence` provenance. Mappings are documented v0.2 reference
  heuristics, not calibrated weights.
- `AgentStep` and `AgentTrajectory` models for the experiment harness.
- Experiment harness and benchmark trajectories that replay recorded
  coding-agent trajectories and report where BOUND would have stopped
  (steps / tool-calls / tokens that would have been avoided). This is
  reproducible evidence of the stop point — not a claim that BOUND already
  improves agent performance.
- New public package exports: `BoundWeights`, `ScoreEvidence`,
  `WorkflowNormalization`, `CodingWorkflowSignals`, `AgentStep`,
  `AgentTrajectory`.

### Changed

- `EvaluationResult` now carries `weights`, `rollback_risk_threshold`,
  `retry_margin`, `distance_to_threshold`, and optional `provenance`. The
  deprecated scalar `weight` is retained as an alias for
  `weights.acceptance`; supplying both `weight` and a non-default `weights`
  raises `ValueError` (no two competing weight systems).
- `calculate_components` now emits the four weighted terms
  (`W_A×A`, `W_I×I`, `W_R×R`, `W_C×C`); `total` stays bit-identical to
  `calculate_bound_score`.
- Steering prompts and the CLI updated for the v0.2 formula and decision
  semantics: the CLI accepts per-dimension weight flags
  (`--acceptance-weight`, `--influence-weight`, `--risk-weight`,
  `--cost-weight`; `--weight` kept as a backward-compatible alias) and a new
  `evaluate-workflow` subcommand, and the JSON payload exposes `weights` and
  `distance_to_threshold`.
- README, roadmap, and CONTRIBUTING updated for the v0.2 score formula,
  decision semantics, deterministic workflow signals, the "what BOUND means"
  satisficing clarification, and competitive positioning.
- Version bumped to `0.2.0`.

### Removed

- The v0.1 decision rule (`risk > cost → ROLLBACK`, `cost > risk → RETRY`,
  `risk == cost → REPLAN`) and the diagrams/text implying `REPLAN` fails into
  `ROLLBACK`. `ROLLBACK` is now a peer outcome triggered by a hard safety
  boundary.

## [0.1.0] - 2026-07-15

### Added

- Deterministic BOUND bounded-utility score calculator: `S = (W × A) + I - R - C`.
- Pydantic v2 domain models: `Action`, `BoundCriteria`, `EvaluationScores`,
  `Decision`, `EvaluationResult`.
- `BoundCalculator` with `calculate_bound_score` and `calculate_components`
  (raw score — no clamping, normalization, rounding, or sigmoid).
- `Evaluator` protocol + `StaticEvaluator` for offline, deterministic scoring.
- `BoundPolicy` applying the deterministic decision rule:
  `S >= T → ACCEPT`, `risk > cost → ROLLBACK`, `cost > risk → RETRY`,
  else `REPLAN`.
- Deterministic steering-prompt rendering (no LLM), under 150 words.
- `bound evaluate` CLI: JSON to stdout, steering prompt to stderr, all inputs
  validated through Pydantic.
- `examples/flight_booking.py` reproducing the README example with no LLM.
- 121 tests, including architecture invariants enforced at runtime
  (no network, no API key, no LLM SDK).
- MIT license.

[Unreleased]: https://github.com/Danny-de-bree/bound/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/Danny-de-bree/bound/releases/tag/v0.3.0
[0.2.0]: https://github.com/Danny-de-bree/bound/releases/tag/v0.2.0
[0.1.0]: https://github.com/Danny-de-bree/bound/releases/tag/v0.1.0
