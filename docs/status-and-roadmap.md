# Current status and roadmap

## Current status

BOUND v0.4 is an experimental deterministic control policy. The score formula,
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
  agent-loop example, integration-first docs. *(this release)*
- **Later** — production data collection and threshold calibration; a real,
  documented Cline dogfooding run; hierarchical BOUND; adaptive/learned
  thresholds; an optional, out-of-core semantic evaluator; mission-level
  policies.

> v0.4 is the integration release. It proves an agent can install BOUND,
> evaluate meaningful execution boundaries, and change its control flow based on
> deterministic evidence. It does **not** claim BOUND already improves agent
> outcomes — that requires real workload calibration, which is later work.
