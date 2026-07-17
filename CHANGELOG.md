# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.1] - 2026-07-17

- Fixed README image rendering on PyPI.
- Added working PyPI and skills.sh badges.
- Updated skill and integration prompt downloads to use GitHub Releases.
- Improved release packaging and documentation.

## [0.6.0] - 2026-07-17

BOUND v0.6 makes integrations easier to audit, harder to misconfigure, and
ships an evidence-backed demo. The focus is the plan-to-report lineage —
`PLAN.md → StepContract → ExecutionEvidence → BOUND decision →
INTEGRATION_REPORT.md` — preserved end to end without duplicating BOUND policy
logic or fabricating evidence.

### Added

- **Deterministic evidence collectors** (`bound.collectors`): pure,
  side-effect-free parsers for the seam where unavailable evidence most easily
  becomes a silent PASS. `parse_pytest_summary` / `PytestSummary` count *tests*
  and deliberately exclude warnings (so `30 passed, 2 warnings` is 30 tests,
  not 32); `parse_git_status_porcelain` / `GitInspection` track git command
  success *separately* from the path list, so a failed `git status` can never
  be read as a clean tree (`is_clean_proven()` returns `False`);
  `ServiceTestEvidence` keeps the *service-specific* `service-tests-pass` check
  distinct from the full-suite `tests-pass` check (it passes only when the
  service run executed ≥1 test *and* the command succeeded).
- **Added Skills** now you can install skills via skills.sh and via local skill for Openai.
- **Standardized execution report + run trace** (`bound.report`): `RunTrace`
  serializes one real BOUND step evaluation to JSON and round-trips losslessly;
  `render_from_trace` derives the `INTEGRATION_REPORT.md` structure from the
  trace (never maintained as a second source). The renderer records only values
  actually returned by BOUND — `token_usage` / `runtime_seconds` /
  `tool_call_count` / `model_metadata` default to `None` and stay `null` /
  *unavailable* when unobservable, never fabricated.
- **Reference integration** (`examples/reference_integration`): runs BOUND's own
  verification commands (`uv run pytest -q`, `uv run pytest
  tests/test_calculator.py -q`, `git status --porcelain`), builds a real
  `ExecutionEvidence` from the captured output via the pure collectors,
  evaluates it through BOUND's deterministic policy, and writes a real
  `bound_integration/run.json` (`RunTrace`) plus
  `bound_integration/INTEGRATION_REPORT.md` (rendered from the same trace).
- **README demo GIF from real stored evidence**
  (`scripts/generate_demo.py` + `assets/bound-demo.gif`): a reproducible,
  stdlib-only script renders the plan → execution → evidence → BOUND evaluation
  → decision → lineage frames from `bound_integration/run.json`. The GIF is a
  visualization; the raw evidence and report are the proof.
- **Plan-to-report lineage** documented in all five integration prompts
  (`generic`, `cline`, `claude-code`, `kilo-code`, `hermes-agent`) and the
  integration docs, with the canonical execution lifecycle (human intent →
  `PLAN.md` → `StepContract` → agent execution → `ExecutionEvidence` → BOUND
  `EvaluationResult` → control action → `INTEGRATION_REPORT.md`) and a stable
  plan-ID convention (`PHASE-NNN`, with `-R<n>` replan suffixes and nested
  forms) carried verbatim from plan to contract to report.
- **Tests** for the collectors (`tests/test_collectors.py`), the report
  renderer and `RunTrace` (`tests/test_report.py`), the single-source
  decision → control mapping and stable plan IDs
  (`tests/test_integration_mapping.py`), and the v0.6 Definition of Done
  (`tests/test_v06_dod.py`).

### Changed

- Positioned BOUND consistently as a **deterministic control harness** across
  the README and docs (`BOUND` → deterministic control harness; `BoundPolicy` →
  deterministic decision engine; `StepContract` + `ExecutionEvidence` →
  evaluation layer; `PLAN.md` → pre-run intent; `INTEGRATION_REPORT.md` →
  post-run execution record).
- The README demo is now self-sufficient: the evidence lives in BOUND's own
  `bound_integration/` (a real trace from BOUND's own verification commands),
  not in the sibling benchmark repository.
- Version bumped to `0.6.0`, also resolving the `0.4.0` (in `__init__.py`) /
  `0.5.0` (in `pyproject.toml`) version mismatch.

### Notes

- The deterministic, network-free core is unchanged. The new collectors and
  report modules import only the standard library and pydantic (plus sibling
  BOUND models); the forbidden-import architecture scan covers them.

## [0.5.0] - 2026-07-16

A small maintenance release: outdated examples and experiment artifacts were
retired, dead code pruned, and the README and architecture docs refreshed. No
public API changed and the deterministic core is unchanged. The benchmark
corpus (`benchmarks/contracts/`, `benchmarks/trajectories/`) and the
`examples/agent_control_loop.py` example remain part of the repository.

### Changed

- README and `architecture/README.md` rewritten for clarity and consistency.
- Version bumped to `0.5.0` (`pyproject.toml`, `src/bound/__init__.py`,
  `uv.lock`).

### Removed

- Outdated runnable examples: `examples/flight_booking.py`,
  `examples/plan_to_contract.py`, `examples/automatic_plan_workflow.py`, and
  `examples/semantic_blind_spot.py`.
- The Cline dogfooding experiment under `experiments/cline/`.
- The stale repository-root `todo.md` (superseded integration plans).
- Dead code in `src/bound/models.py` and `src/bound/workflow.py`.

## [0.4.0] - 2026-07-16

### Added

- **Framework-neutral agent-control layer** (`bound.integration`):
  `AgentControlResult` and `evaluate_agent_step` run BOUND's deterministic
  contract pipeline for one executed step and *translate* the resulting
  decision into a framework-neutral control action — `ACCEPT → continue`,
  `RETRY → retry`, `REPLAN → replan`, `ROLLBACK → rollback` — plus concise
  deterministic feedback (under 150 words) the agent can re-inject into its own
  context. The layer never invents scores, never modifies a BOUND decision,
  never calls an LLM, knows nothing about any agent framework, and never executes
  a rollback or retry itself.
- **Deterministic agent feedback** (`render_feedback`): ACCEPT / RETRY / REPLAN /
  ROLLBACK feedback derived exclusively from the `EvaluationResult`, the
  `StepContract`, the `ExecutionEvidence`, and per-dimension provenance. No LLM.
  Golden snapshot tests pin the exact wording.
- **Framework-neutral integration specification** (`bound.integration_spec` +
  `bound integration-spec` CLI subcommand): a pure, JSON-serialisable spec
  covering *when to call* BOUND, *when not to call*, the *required flow*, the
  *evidence rule* ("never fabricate unavailable evidence"), the decision →
  control mapping, and integration invariants. No network, no LLM.
- **Five integration prompts** under `integrations/`: `generic`, `cline`,
  `claude-code`, `kilo-code`, `hermes-agent` — each an `INSTALL_BOUND.md`
  *prompt* (not human docs) designed to be pasted into the named agent so it
  wires BOUND into its own workflow. Honest "Integration prompt for X" wording;
  no claims of native framework hooks.
- **Runnable multi-step agent-loop example**
  (`examples/agent_control_loop.py`): a real three-attempt trajectory
  (REPLAN → RETRY → ACCEPT) driven by the exact public API, with no hardcoded
  decisions, no LLM, and no network.
- New public package exports: `AgentControlResult`, `NextAction`,
  `evaluate_agent_step`, `render_feedback`, `integration_spec`.

### Changed

- **Placeholder-free public contract API.** A plain `BoundWorkflow()` followed
  by `evaluate_step(contract=…, evidence=…, criteria=…)` now runs the full
  contract pipeline with no vestigial `BoundPolicy(StaticEvaluator(placeholder))`
  requirement. The contract path reaches the decision via `BoundPolicy.decide`
  with pre-computed scores, so `BoundPolicy()` (no injected evaluator) is the
  default policy and `BoundWorkflow()` constructs one automatically. Backwards
  compatibility is preserved.
- **README rewritten integration-first** (~240 lines): hero → generated agent
  workflow image → install → "put BOUND in your agent" links → one tested
  end-to-end example with real executed output → how the four decisions work →
  how evidence becomes a score → objective vs subjective evidence → current
  status → documentation. Detailed material moved into `docs/`.
- **Documentation restructure:** new `docs/concepts.md`, `docs/contracts.md`,
  `docs/architecture.md`, `docs/scoring.md`, `docs/integrations.md`, and
  `docs/status-and-roadmap.md`; the README links to these instead of duplicating.
  Stale "agent integrations are being added" wording removed now that the prompts
  exist.
- **Generated workflow image** (`assets/bound-agent-workflow.png`) rendered
  deterministically and displayed prominently near the top of the README.
- Version bumped to `0.4.0`.

### Notes

- **No LLM-as-judge introduced.** An LLM may be used only to draft an evaluation
  contract (structured data only); the final decision and `A / I / R / C` scores
  remain the exclusive responsibility of the deterministic `ContractEvaluator`
  and `BoundPolicy`.
- The deterministic, network-free core is unchanged.

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

[Unreleased]: https://github.com/Danny-de-bree/bound/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/Danny-de-bree/bound/releases/tag/v0.6.0
[0.5.0]: https://github.com/Danny-de-bree/bound/releases/tag/v0.5.0
[0.4.0]: https://github.com/Danny-de-bree/bound/releases/tag/v0.4.0
[0.3.0]: https://github.com/Danny-de-bree/bound/releases/tag/v0.3.0
[0.2.0]: https://github.com/Danny-de-bree/bound/releases/tag/v0.2.0
[0.1.0]: https://github.com/Danny-de-bree/bound/releases/tag/v0.1.0
