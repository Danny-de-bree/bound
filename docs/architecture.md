# Architecture

How BOUND is structured, and the boundaries that keep it deterministic and
framework-neutral.

## Pipeline

```text
goal + plan ──ContractGenerator──▶ BoundPlan (validated StepContracts)
        │
        ▼
agent executes one step
        │
        ▼
ExecutionEvidence (only what was observed — never fabricated)
        │
        ▼
ContractEvaluator ──▶ A / I / R / C  (with ScoreEvidence provenance)
        │
        ▼
BoundCalculator ──▶ S = (W_A×A) + (W_I×I) - (W_R×R) - (W_C×C)
        │
        ▼
BoundPolicy ──▶ EvaluationResult (ACCEPT / RETRY / REPLAN / ROLLBACK)
        │
        ▼
evaluate_agent_step ──▶ AgentControlResult (continue / retry / replan / rollback)
```

## Module layout

| Module                       | Responsibility                                            |
| ---------------------------- | --------------------------------------------------------- |
| `bound.models`               | Pydantic domain models (Action, EvaluationScores, …)     |
| `bound.calculator`           | Pure score math `S = (W_A×A)+(W_I×I)-(W_R×R)-(W_C×C)`    |
| `bound.contracts`            | Evaluation contracts + `ContractGenerator` Protocol      |
| `bound.evidence`             | Evidence models + `EvidenceCollector` Protocol           |
| `bound.contract_evaluator`  | Deterministic `ContractEvaluator` (contract → scores)    |
| `bound.bound_workflow`       | `BoundWorkflow` orchestration (`prepare` + `evaluate_step`) |
| `bound.policy`               | Deterministic `BoundPolicy` (the decision rule)          |
| `bound.evaluator`           | `Evaluator` Protocol + `StaticEvaluator` (the score seam) |
| `bound.workflow`            | `CodingWorkflowEvaluator` (deterministic workflow signals) |
| `bound.contract_quality`    | Structural `ContractQualityReport` (no LLM)             |
| `bound.integration`         | `AgentControlResult` + `evaluate_agent_step` (agent seam) |
| `bound.integration_spec`    | Framework-neutral integration spec (JSON)               |
| `bound.prompt`              | Deterministic steering-prompt rendering                  |
| `bound.cli`                 | `bound` command-line entrypoint                          |
| `bound.llm_adapters`        | Documented, **import-free** seam for optional LLM generators |

## The decision lives in one place

`BoundPolicy.decide` is the single assembly point for `EvaluationResult`. The
contract workflow scores an *executed* step with `ContractEvaluator` and feeds
the resulting `EvaluationScores` straight into `BoundPolicy.decide` — it never
re-scores, and it never needs an `Evaluator` placeholder. A plain
`BoundPolicy()` (no injected evaluator) is the default policy for the contract
pipeline, and `BoundWorkflow()` constructs one automatically.

## Invariants (enforced by tests)

- **Deterministic:** same inputs → same scores, same decision, same feedback.
- **Network-free core:** the evaluation path performs no network access.
- **No LLM SDK:** the `bound` package imports no LLM SDK; LLM-backed contract
  generators are optional and live outside the core.
- **Framework-neutral:** the core and the integration layer know nothing about
  Cline, Claude Code, Codex, Cursor, or any other agent.
- **Thin and removable:** BOUND only *translates* a decision into a control
  instruction; it never executes a rollback or a retry, and never duplicates an
  agent's control logic.

## The score seam

The `Evaluator` Protocol is the single seam where scores enter the Action-based
path. `StaticEvaluator` returns pre-supplied scores (tests, examples, CLI);
`CodingWorkflowEvaluator` derives `A / I / R / C` from deterministic
coding-agent signals with full provenance. Other evaluators (rule-based,
reward-model, a future optional semantic evaluator, …) implement the same
protocol without touching the decision rule. The contract path does not use
this seam — it scores an executed step directly via `ContractEvaluator`.

## Optional LLM contract generation

LLM-backed contract generators are an **optional convenience layer**, never a
requirement. They live **outside** the deterministic core — behind an optional
dependency group or a separate adapter module (see the documented,
import-free `bound.llm_adapters` seam). An LLM adapter's job is to emit
**structured data only** (what success looks like, what risks matter, expected
artifacts, budgets). It must **not** return a BOUND decision and must **not**
assign final `A / I / R / C` scores — those remain the exclusive responsibility
of the deterministic `ContractEvaluator` and `BoundPolicy`. Whatever an LLM
emits round-trips through Pydantic validation before BOUND can use it.
