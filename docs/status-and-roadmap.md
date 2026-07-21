# Current status and roadmap

## Current status

BOUND v0.8 is an experimental deterministic control harness. The score formula,
the default workflow heuristics, and the threshold defaults are **hypotheses**.
They have not yet been broadly validated across production agent workloads.

`A / I / R / C` are not naturally commensurable quantities; the weights are
explicit policy parameters, and the defaults are not implied to be universally
correct. The contract-evaluation heuristics and the experiment harness are
designed to produce **reproducible evidence of where BOUND would stop a
trajectory and how much work would have been avoided** — not to assert that BOUND
already improves agent outcomes.

### What v0.4 added

- A framework-neutral agent-control layer: `AgentControlResult` and
  `evaluate_agent_step` (decision → `continue` / `retry` / `replan` /
  `rollback`) plus deterministic, re-injectable feedback.
- A placeholder-free public API: `BoundWorkflow()` followed by
  `evaluate_step(contract=…, evidence=…, criteria=…)` — no vestigial
  `BoundPolicy(StaticEvaluator(placeholder_scores))` required on the contract
  path.
- A machine-readable `bound integration-spec` CLI subcommand.
- Five integration prompts (generic, Cline, Claude Code, Kilo Code, Hermes
  Agent) — honest "integration prompt for X" wording, no claims of native hooks.
- A real multi-step agent-loop example (`examples/agent_control_loop.py`).
- An integration-first README with one tested end-to-end example and a generated
  workflow diagram.
- Detailed documentation moved into `docs/`.
- **No LLM-as-judge introduced.** The deterministic, network-free core is
  unchanged.

### What Sprints 1–3 added (v0.5–v0.8)

- **v0.5** — cleanup: retired outdated examples/experiments, refreshed README and
  architecture docs. No public API change.
- **v0.6** — plan-to-report lineage (`PLAN.md → StepContract →
  ExecutionEvidence → BOUND decision → INTEGRATION_REPORT.md`), pure
  side-effect-free evidence collectors, a standardised `RunTrace`/report renderer,
  and a reference integration running BOUND's own verification commands.
- **v0.7** — Verified Evidence & Decision Lineage: trust provenance,
  missing-means-missing telemetry, independent executing collectors,
  provenance-aware contracts, append-only local lineage
  (`.bound/runs/`), and the declarative `bound-policy.yaml` configuration
  (hard gates, weighted signals, budgets, scope, approvals) with
  `bound run` / `inspect` / `outcome` / `policy` CLI.
- **v0.8** — operator UX on a shared service layer: `bound init` (policy
  scaffolding), `bound ui` (local dashboard), `bound watch` (event-driven mode),
  `bound mcp` (stdio MCP server), `bound checkpoint` / `rollback` (safe state
  preservation), native collectors, and agent integrations. *(this release)*

## Competitive positioning

BOUND is not a model provider, a judge, or an agent framework. Its intended
differentiation is:

```text
provider-agnostic
deterministic final policy
auditable score decomposition
explicit stop condition
no mandatory LLM judge
workflow evidence before semantic judgement
```

The future value is primarily in signal collection, score derivation, threshold
calibration, and agent-loop integration — not in the one-line score formula
alone. Deterministic, inspectable workflow evidence (tests passing, files
changed, retries) is gathered *before* any optional semantic judgement, and an
LLM judge is never a required dependency of the core.

## Roadmap

- **v0.1** — deterministic core, Pydantic models, CLI, unit tests, prompts.
- **v0.2** — symmetric weights, coherent decision semantics, deterministic
  coding-workflow signals + `CodingWorkflowEvaluator` with provenance, threshold
  introspection, experiment harness.
- **v0.3** — evaluation contracts + `ContractGenerator` abstraction (with the
  dependency-free `StaticContractGenerator`), evidence models + `EvidenceCollector`,
  `ContractEvaluator` with provenance, `BoundWorkflow` orchestration
  (`prepare` + `evaluate_step`), `ContractQualityReport` + benchmark corpus,
  examples.
- **v0.4** — agent integration: framework-neutral control layer,
  `evaluate_agent_step`, `bound integration-spec`, integration prompts, a real
  agent-loop example, integration-first docs.
- **v0.5** — cleanup and doc refresh; retired outdated examples and experiments.
- **v0.6** — plan-to-report lineage, deterministic pure collectors, standardised
  run trace + report renderer, reference integration.
- **v0.7** — Verified Evidence & Decision Lineage: trust provenance,
  independent executing collectors, append-only local lineage, the
  `bound-policy.yaml` configuration system, and the `run` / `inspect` /
  `outcome` / `policy` CLI.
- **v0.8** — shared service layer + operator UX: `bound init`, `bound ui`,
  `bound watch`, `bound mcp`, `bound checkpoint` / `rollback`, native collectors,
  agent integrations. *(this release)*
- **Later** — production data collection and threshold calibration; a real,
  documented Cline dogfooding run; hierarchical BOUND; adaptive/learned
  thresholds; an optional, out-of-core semantic evaluator; mission-level
  policies.

> v0.8 completes Sprints 1–3. The deterministic, network-free core is unchanged;
> the new commands wrap a shared service layer rather than introducing an LLM
> judge. BOUND does **not** yet claim to improve agent outcomes — that requires
> real workload calibration, which is later work.
