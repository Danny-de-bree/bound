# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/Danny-de-bree/bound/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Danny-de-bree/bound/releases/tag/v0.1.0
