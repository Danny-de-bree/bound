# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/Danny-de-bree/bound/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Danny-de-bree/bound/releases/tag/v0.2.0
[0.1.0]: https://github.com/Danny-de-bree/bound/releases/tag/v0.1.0
