# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.7.0] - 2026-07-18

BOUND v0.7.0 adds **Verified Evidence & Decision Lineage**: the honesty model
that makes a BOUND decision auditable end-to-end. Trust provenance now travels
with every piece of evidence, missing telemetry is never silently coerced to
zero, independent BOUND-controlled collectors actually execute verification,
and a decision-assurance layer gates an ACCEPT on independently verified
evidence — all recorded as a reproducible, append-only local lineage
(`contract → evidence → scores → decision → agent outcome`). Everything is
backwards compatible (old schema-1.0 traces and bare-number evidence still
load); lineage is opt-in per run and can be disabled with a single environment
variable.

### Added

- **Trust provenance** (`bound.evidence.EvidenceProvenance`): a 7-level trust
  enum (`observed` / `verified` / `attested` / `evaluated` / `claimed` /
  `defaulted` / `missing`) distinct from the free-form `source` string.
  `CheckEvidence` now carries `provenance`, `collector`, `collector_version`,
  timezone-aware `observed_at`, `artifact_hash`, `raw_artifact_ref`, and a
  `status` (`EvidenceStatus`: `failed` / `unverified` / `missing` / `invalid`).
  Stronger provenance is never silently fabricated from weaker provenance, and
  agent self-report is always `CLAIMED`, never `VERIFIED`.
- **Missing means missing, never zero** (`bound.evidence.EvidenceMetric`):
  execution telemetry (`retry_count`, `tool_call_count`, `token_usage`,
  `runtime_seconds`) is modelled as `EvidenceMetric | None` so a measured zero
  is distinguishable from an unmeasured signal (`value is None` ⇒ `MISSING`).
  Legacy schema-1.0 traces with bare-number telemetry auto-migrate on
  construction (provenance `MISSING`, never upgraded); the public
  `migrate_legacy_execution_evidence()` helper supports explicit pre-normalisation.
- **Provenance-aware contracts** (`bound.contracts`): `AcceptanceCheck` and
  `RiskCheck` gain `accepted_provenance`, `on_missing`, `on_claimed`; `RiskCheck`
  gains `decision_critical`. A check may restrict which provenances it accepts
  and declare how the policy reacts when evidence is missing or only claimed.
- **Evidence status** separating a genuine failure from missing/unverifiable/
  invalid evidence, so a failed command, a zero-test run, a stale artefact, or a
  collector crash are never silently flipped to a pass.
- **Independent collectors** (`bound.command_collector`) that EXECUTE
  verification, not just parse it: `CommandCollector` (no agent command
  injection — only pre-registered commands run by name), `PytestCollector`
  (runs pytest; a pass requires exit 0 AND >0 tests), `JUnitCollector` (hashes +
  freshness-checks a trusted artefact), `GitCollector` (proves a clean tree),
  `BudgetCollector` (+`BudgetMetrics`) and `ProcessRuntimeCollector` for
  observed telemetry, plus `default_redactor` and `sha256_hex`. All are
  fail-safe: timeout / crash / parse-fail / zero-tests / stale never yield a
  VERIFIED pass.
- **Decision assurance & gating** (`AssuranceAssessment` +
  `BoundPolicy.decide(..., assurance_assessment=...)`): a `DecisionAssurance`
  level (`verified` / `mixed` / `claimed` / `insufficient`) is computed from the
  restricted (provenance-restricted / decision-critical) checks. `CLAIMED` or
  `INSUFFICIENT` assurance gates a candidate `ACCEPT`, downgrading it to the
  contract's `on_missing` / `on_claimed` action; `VERIFIED` and `MIXED` leave it
  unchanged. `EvaluationResult` now exposes `candidate_decision`,
  `final_decision`, `assurance`, and `assurance_reasons`. Influence with no
  evidence source is recorded as `DEFAULTED` (`raw_value=None`,
  `effective_value=0.0`), never presented as `VERIFIED`.
- **Lineage schema 2.0** (`bound.lineage`): four append-only audit events —
  `evidence.collected` (independent collector proof), `evidence.collection_failed`
  (honest record of a collector crash), `decision.gated` (assurance gating of a
  candidate ACCEPT), and `action.reported` (agent CLAIMED self-report + optional
  independent observation) — plus per-event `sequence` / `parent_event_id`
  ordering and a `RunConfigSnapshot` on `run_started` carrying a SHA-256
  policy/config hash, contract hash, and collector versions (item 11).
  `build_run_config()` / `compute_policy_config_hash()` build the snapshot.
  Schema-1.0 traces remain readable (new fields are optional with safe defaults).
- **Privacy hardening**: raw command output is NOT stored in full by default —
  only a sha256 hash and a short, redacted, size-capped summary are kept on
  emitted evidence; full redacted output is retained only on opt-in
  `store_raw=True`. A secret redactor runs over captured output before hashing,
  summarising, or retention, so a secret can never reach a trace.
- **CLI provenance**: `bound inspect` shows provenance, assurance, and critical
  evidence coverage (`--only-unverified`, `--json`); the markdown report
  (`render_from_trace`) is provenance-aware and shows candidate vs final decision
  and assurance. Skills and integration prompts updated to keep the agent out of
  the evidence loop (it configures collectors; BOUND performs objective
  verification; agent self-report is always CLAIMED).
- **Verified-evidence demo & tests**: `examples/verified_evidence_demo.py` runs
  the canonical REPLAN → ACCEPT flow with live pytest + git collectors and
  prints a per-number trace proof; `tests/test_v07_verified_evidence.py` pins
  every todo §16 honesty invariant (claimed-vs-verified, observed-wins,
  missing-not-zero, defaulted-influence, zero-tests-no-pass, stale-JUnit
  rejection, crash → INSUFFICIENT, CLAIMED-risk blocks ACCEPT, verified+evaluated
  → MIXED, all-verified → VERIFIED, schema-1.0 reading, config hash, default
  redaction, determinism) plus the Definition-of-Done flow.

### Changed

- Version bumped to `0.7.0`. `BoundWorkflow.evaluate_step()` automatically
  passes the `ContractEvaluator`'s assurance assessment through to the policy,
  so the contract workflow gates a candidate ACCEPT without extra wiring.
- Cost scoring treats unmeasured telemetry for a declared budget dimension as
  conservatively saturated (not a silent zero), stamped `MISSING`.

### Security

- Collector-side redaction masks credential-looking `key=value` tokens in command
  output before any hashing, summarising, or raw retention, and raw output is
  not stored by default — reducing the risk of a secret leaking into a persisted
  lineage trace.

### Added — Decision Lineage (foundation)

- **Append-only lineage event model** (`bound.lineage`): `Run`, `Step`,
  `Attempt`, `Evaluation`, `Outcome` entities and five append-only event types
  (`run_started`, `step_started`, `evaluation_recorded`, `outcome_recorded`,
  `run_finished`) with `schema_version="1.0"`, timezone-aware UTC timestamps,
  deterministic SHA-256-based ids, and a fixed `ReasonCode` enum (no free-text
  reasons). `parse_lineage_event()` round-trips any event line.
- **Local storage + privacy** (`bound.lineage_store`): `LineageStore` writes
  `.bound/runs/<run_id>/{run.json,events.jsonl}` atomically; crashed/incomplete
  runs stay readable; corrupt JSONL lines are skipped (lenient) or raised
  (strict). Privacy by default: a stored-fields allowlist, `scrub_secrets`
  redactor, configurable max event/file size, and `bound run delete <run_id>`.
  Prompts, tokens, and source code are **never** stored (not in the schema).
- **Python API** (`bound.lineage_api`): `bound.start_run(task) -> RunContext`
  (context manager that auto-finishes interrupted runs), `RunContext`
  `.start_step` / `.record_evaluation` / `.record_outcome` / `.finish_run`, and
  module-level `bound.record_outcome()` / `bound.finish_run()`.
  `BoundWorkflow.evaluate_step(..., run=...)` now auto-writes
  `step_started + evaluation_recorded + outcome_recorded` when a run context is
  supplied, and is unchanged when `run` is `None` (backwards compatible).
  `ContractEvaluator(run=...)` sets a default run; explicit `run=` wins.
- **CLI lineage commands**: `bound run start/finish/list/delete`,
  `bound inspect <run_id>` (renders the Step → Attempt → Outcome decision tree
  with scores/thresholds/reason codes/agent follow-up action and marks
  incomplete runs), `bound outcome --run ...`, and `bound evaluate --run ...`
  (records `step_started + evaluation_recorded` and adds a `lineage` block to
  the JSON). `--json` everywhere; exit codes 0 / 1 (not found) / 2 (validation).
- **Agent integration prompts updated**: all six integration prompts
  (`codex`, `claude-code`, `cline`, `kilo-code`, `hermes-agent`, `generic`) and
  `skills/bound/SKILL.md` now teach the agent to start one run per task, use
  stable step/contract ids (`PHASE-NNN`, `PHASE-NNN-R1` after a replan),
  evaluate only meaningful boundaries, record the real follow-up action,
  explicitly close the run, and report the local lineage path +
  `bound inspect <run_id>`.
- **Demo & docs**: `examples/lineage_demo.py` runs the REPLAN → ACCEPT flow
  end-to-end and prints the inspect tree; `examples/lineage_demo_events.jsonl`
  ships a real 8-event captured log. New `docs/lineage.md` (data model, Python
  API, CLI, what is/isn't stored) and `docs/upgrade-guide.md` (opt-in, backwards
  compatible, disable via `BOUND_LINEAGE_DISABLED=1`).

### Changed — Decision Lineage (foundation)

- Existing integrations that never pass a `run`
  context are completely unaffected — lineage only activates when a run is
  explicitly started. To disable lineage entirely (CI, ephemeral environments),
  set `BOUND_LINEAGE_DISABLED=1`, call `bound.configure(enabled=False)`, or
  construct `LineageStore(enabled=False)`: the builders still construct and
  return typed events but persist nothing.

### Added — Policy Configuration system

- **Canonical `bound-policy.yaml` schema** (`bound.policy_schema`): the
  declarative policy a human reviews and *approves* before a run. Strict Pydantic
  v2 models with `extra="forbid"` reject unknown fields; duplicate check IDs
  (across acceptance/quality/risk lists) and duplicate collector IDs (YAML keys)
  are rejected at load. `load_policy_yaml()` / `parse_policy_yaml()` (with
  duplicate-key detection) and `policy_json_schema()` are the canonical loaders;
  a documented `src/bound/default_policy.yaml` (`coding-default@1.0`) ships as
  the default.
- **Three policy mechanisms** (todo 2.2):
  - **Hard gates / blockers** (`HardGate`, `importance: blocker`) carry `required`,
    `on_failure` / `on_missing` / `on_claimed`, `minimum_assurance`, and
    `accepted_provenance`. A failed blocker can **never** be compensated by
    positive scores — the active-policy gate forces the most conservative
    decision.
  - **Weighted signals** (`WeightedSignal`, importance `high` / `medium` / `low`
    / `ignore`) map through `DEFAULT_WEIGHTS`, with an optional numeric `weight`
    override; the resolved `effective_weight` is stored on the model and is part
    of the canonical form/hash.
  - **Budgets** (`BudgetDimension`) for `attempts` / `tool_calls` / `tokens` /
    `runtime` / `financial_cost`, each with soft/hard limits, a configurable
    `EvidencePolicyAction` at each limit, and an `enabled` flag. Missing
    telemetry can never silently satisfy a declared budget.
- **Scope & safety** (`ChangeScope`, `UnexpectedArtifactsPolicy`,
  `ApprovalsPolicy`): configurable allowed/forbidden paths, dependency-file
  detection, unexpected-artifact handling, commands/destructive actions
  requiring approval, and rollback-availability guardrails.
- **Canonicalisation & SHA-256 hashing** (`bound.policy_canon`):
  `canonicalize_policy()` produces a formatting-independent (key-sorted,
  comment/whitespace-agnostic) form; `compute_policy_hash()` returns
  `sha256:<hex>`; `compute_contract_hash()` is the bare-hex contract hash
  consistent with `bound.lineage.compute_contract_hash`; `policy_changed_since()`
  detects a material policy change between two snapshots (model or hash string).
- **Policy lifecycle & approval rules** (todo 3.3): a policy moves
  DRAFT → VALIDATED → APPROVED → ACTIVATED; only an activated policy controls
  decisions. Renewed approval is required after any meaningful change (a blocker
  removed, a weight lowered, a budget increased, path scope expanded, or a
  provenance requirement narrowed) — each changes the canonical hash so
  `policy_changed_since()` flags it. An agent cannot approve its own policy or
  weaken the active policy mid-run.

- **Policy-aware `bound inspect`**: `inspect` shows `Policy: <id>@<version>` and
  the policy hash for runs governed by an active policy.
- **New lineage events** (`bound.lineage`): `policy.proposed`, `policy.validated`,
  `policy.approved` (records the approver + approval time), `policy.activated`,
  `evaluation.completed` (the terminal evaluation event carrying policy
  id/version/hash, contract hash, effective weights, collector versions,
  raw/effective evidence, and candidate/final decision + assurance),
  `action.observed` (an independent hook's observation, including mismatch
  detection — the proof that upgrades a ROLLBACK from CLAIMED), and
  `step.completed`. `RunConfigSnapshot` gains optional `policy_version` /
  `policy_hash`; `build_run_config(policy=...)` derives them automatically.
  `record_evaluation()` forwards the policy fields so every decision records the
  policy hash (release blocker). Append-only: the store never rewrites events.
- **Collectors bound to the active policy**: the contract evaluator
  (`ContractEvaluator`) blends the contract's required checks with the policy's
  weighted signals (effective weights stored on the `PolicyGateOutcome`), and
  assesses hard gates + budgets into a `PolicyGateOutcome` consumed by
  `BoundPolicy.decide(..., policy_gate=...)`, which forces an uncompensable
  decision when a blocker fails or a budget is breached. The resolved effective
  weights and active policy id/version/hash are forwarded onto
  `EvaluationResult`.
- **`EvidenceStatus.STALE`**: the canonical evidence-status vocabulary is now
  `PASSED` / `FAILED` / `MISSING` / `INVALID` / `STALE`; the legacy `UNVERIFIED`
  member is retained as a deprecated alias so existing collectors and schema-2.0
  traces keep loading unchanged.
- **Policy CLI** (`bound policy`): `validate` (parses + validates + reports
  warnings about blockers without viable collectors, claimed-only checks, and
  unmeasurable criteria), `explain` (a concise human-readable view of effective
  gates/weights/budgets), and `hash` (the canonical `sha256:<hex>`), each with a
  machine-readable `--json` payload.
- **Documentation (Phase 11)**: README, architecture, scoring, provenance,
  assurance, policy-YAML, collector-security, missing-versus-zero, and privacy
  docs updated; integration prompts and `SKILL.md` refreshed; v0.6 migration
  notes added.
- **Golden demo** (`examples/golden_demo.py`): a reproducible, end-to-end
  policy-configured flow — user intent → generated `bound-policy.yaml` →
  validation → human explanation → PROPOSED → APPROVED → ACTIVATED → canonical
  hash → attempt 1 (pytest 1/3 VERIFIED → REPLAN) → attempt 2 (pytest 3/3,
  typecheck/lint/scope VERIFIED, 18/20 tool calls OBSERVED → ACCEPT) — proven by a
  real `.bound/runs/` trace, a generated `INTEGRATION_REPORT.md`, and a printed
  reproduction command. No hardcoded decisions, no fabricated evidence.
- **Tests** (`tests/test_v07_policy_security.py`) pinning the policy-security
  invariants: blockers cannot be compensated by positive weighted signals,
  `extra="forbid"` rejects unknown fields, duplicate IDs are rejected, a blocker
  without a viable collector is surfaced, the policy hash is stable across
  formatting and changes with material content, mid-run policy changes are
  detected, material weakenings require renewed approval, budget enforcement
  (breach + missing telemetry), weighted-signal scoring, and the full policy
  lifecycle — plus an end-to-end test that runs the golden demo and asserts it
  ends in ACCEPT.

### Security — policy configuration

- The policy is the single source of decision authority: only an
  APPROVED → ACTIVATED policy controls decisions, and the executing agent can
  neither approve its own policy nor weaken the active one mid-run. Any material
  weakening (blocker removal, weight reduction, budget increase, scope expansion,
  provenance narrowing, collector replacement) changes the canonical policy hash,
  so `policy_changed_since()` detects it and renewed human approval is required.
- The schema (`extra="forbid"`) rejects unknown fields, duplicate check IDs, and
  duplicate collector IDs at load, so a malformed or drifted policy is a clear
  error rather than silent schema drift. Collectors run only declarative,
  pre-registered commands — no agent command injection.

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

[Unreleased]: https://github.com/Danny-de-bree/bound/compare/v0.7.0...HEAD
[0.7.0]: https://github.com/Danny-de-bree/bound/releases/tag/v0.7.0
[0.6.1]: https://github.com/Danny-de-bree/bound/releases/tag/v0.6.1
[0.6.0]: https://github.com/Danny-de-bree/bound/releases/tag/v0.6.0
[0.5.0]: https://github.com/Danny-de-bree/bound/releases/tag/v0.5.0
[0.4.0]: https://github.com/Danny-de-bree/bound/releases/tag/v0.4.0
[0.3.0]: https://github.com/Danny-de-bree/bound/releases/tag/v0.3.0
[0.2.0]: https://github.com/Danny-de-bree/bound/releases/tag/v0.2.0
[0.1.0]: https://github.com/Danny-de-bree/bound/releases/tag/v0.1.0
