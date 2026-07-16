# Evaluation contracts

BOUND's contract layer lets you describe what "done" and "risk" mean **before**
an agent executes a step, then evaluate the executed step against that contract
using only what was actually observed. It runs entirely without an LLM.

## Why contracts

Without a contract, an agent has no explicit, machine-readable definition of
"good enough for this step". The contract makes that definition first-class:

```text
StepContract          (what "done" and "risk" mean for one step)
    ↓
agent executes the step
    ↓
ExecutionEvidence     (only what you actually observed — never fabricated)
    ↓
ContractEvaluator     (deterministic: contract + evidence → A / I / R / C)
    ↓
BoundPolicy           (deterministic decision)
```

## Contract models (`bound.contracts`)

- **`AcceptanceCheck`** — a measurable, observable outcome. `id` (must match
  evidence by id), `description`, and `required` (default `True`). Required checks
  drive acceptance; optional checks are advisory only.
- **`RiskCheck`** — a named risk with a `severity ∈ [0, 1]` (`1.0` is a hard
  safety boundary). A risk check with no evidence is treated conservatively as
  **violated**.
- **`StepBudget`** — optional ceilings: `max_retries`, `max_tool_calls`,
  `max_tokens`, `max_runtime_seconds`. `None` means *no explicit budget*, not a
  zero budget. Declared-but-unmeasured dimensions are conservatively saturated.
- **`StepContract`** — one step: `id`, `description`, `goal`,
  `acceptance_checks`, `risk_checks`, `expected_artifacts`, `budget`.
- **`BoundPlan`** — a validated, ordered sequence of `StepContract`s plus a
  top-level `goal`.

## Generating contracts

The `ContractGenerator` Protocol compiles a natural-language goal + plan into a
validated `BoundPlan`. It must never produce a BOUND decision or `A / I / R / C`
scores. BOUND ships a dependency-free `StaticContractGenerator(plan)` that
returns the same plan every call — so the full contract pipeline runs with **no
API key, no network, no LLM SDK**. LLM-backed generators are optional and live
outside the core (see the documented, import-free `bound.llm_adapters` seam).

## Evidence models (`bound.evidence`)

- **`CheckEvidence`** — `check_id` (must match an acceptance/risk check id),
  `passed`, optional `detail`, and `source` (what produced the evidence, e.g.
  `"test-runner"`, `"mypy"`).
- **`ExecutionEvidence`** — what was *actually observed*: `acceptance=[…]`,
  `risks=[…]`, `produced_artifacts`, `unexpected_artifacts`, `retry_count`,
  `tool_call_count`, `token_usage`, `runtime_seconds`, `rollback_available`.

> **Never fabricate unavailable evidence.** If token usage or runtime is not
> observable, leave those fields unset (`None`); the deterministic policy
> handles unmeasured declared budgets conservatively rather than inventing a
> pass.

## Contract quality (structural, no LLM)

`ContractQualityReport` (via `assess_contract`) is a deterministic, structural
smell test over a compiled `BoundPlan`: it scores how *measurable* the acceptance
checks read and flags obvious problems (no checks, too many vague checks,
duplicate ids, no observable verification method, an oversized contract). It
performs **no LLM call and no semantic judgement** — it can answer "are the
checks *measurable-looking*?" but not "are they *relevant* to the goal?" That
blind spot is made explicit in the bundled experiment corpus under
`benchmarks/contracts` (≥10 fixtures, including a deliberate
`measurable_but_irrelevant` blind spot).
