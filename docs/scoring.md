# Scoring — how evidence becomes a score

This is the detailed reference for the deterministic `ContractEvaluator`
heuristics that turn a `StepContract` + `ExecutionEvidence` into `A / I / R / C`.
All mappings are **v0.3 reference heuristics** — not scientifically calibrated.

## The compact picture

```text
2 / 3 required checks passed        ──▶  Acceptance A = 0.6667
5 / 20 tool-call budget used        ──▶  Cost contribution = 0.25  (C = 0.25)
no violated risk checks              ──▶  Risk R = 0.00
                                         Influence I = 0.0 (default)
                  ┌─────────────────────────────────────────────┐
                  │  BOUND applies weights + threshold:         │
                  │  S = (W_A×A) + (W_I×I) - (W_R×R) - (W_C×C)   │
                  │  then S vs T and the risk boundary →         │
                  │  ACCEPT / RETRY / REPLAN / ROLLBACK          │
                  └─────────────────────────────────────────────┘
```

## Default weights and where they are configured

| Weight | Default | Set via                                        |
| ------ | ------- | ---------------------------------------------- |
| `W_A`  | `1.0`   | `BoundWeights(acceptance=…)` or legacy `weight` |
| `W_I`  | `1.0`   | `BoundWeights(influence=…)`                    |
| `W_R`  | `1.0`   | `BoundWeights(risk=…)`                         |
| `W_C`  | `1.0`   | `BoundWeights(cost=…)`                         |

Weights are passed on `BoundCriteria` (`criteria=BoundCriteria(weights=…)`).
On the CLI, the `bound evaluate` and `bound evaluate-workflow` subcommands
accept `--acceptance-weight`, `--influence-weight`, `--risk-weight`,
`--cost-weight` (the deprecated `--weight` is an alias for `--acceptance-weight`).

Thresholds (`threshold`, `retry_margin`, `rollback_risk_threshold`) are also on
`BoundCriteria`; defaults are `retry_margin = 0.1` and
`rollback_risk_threshold = 0.8`. `threshold` has no default and **must** be
supplied per evaluation — it is workload-specific.

> **The defaults are reference defaults, not calibrated weights.** The score
> formula, the workflow heuristics, and the threshold defaults are hypotheses
> that have not yet been broadly validated across production agent workloads.
> `A / I / R / C` are not naturally commensurable; the weights are explicit
> policy parameters, and **thresholds require workload-specific calibration**.

## Acceptance (A) ∈ [0, 1]

`A = passed_required / total_required`. Each *required* `AcceptanceCheck` is
reconciled against `CheckEvidence` by `id`:

- a required check with **no** matching evidence counts as **FAILED** (never
  silently passing);
- duplicate evidence for one `id` is deduplicated **conservatively** — the check
  passes only when *every* matching record has `passed=True`;
- optional (`required=False`) checks are advisory only and do not affect `A`;
- when the contract defines no required checks, `A = 0.0` (acceptance cannot be
  established from advisory checks alone).

## Risk (R) ∈ [0, 1]

`R = min(1.0, Σ contributions)`, additive and capped:

- each **violated** `RiskCheck` contributes its `severity` (a check with no
  evidence is treated **conservatively as violated**);
- any `unexpected_artifacts` contributes a full `1.0` surprise indicator;
- `rollback_available is False` contributes a full `1.0` recovery-risk
  indicator (`rollback_available is None` is *skipped*, not invented).

## Cost (C) ∈ [0, 1]

`C` is the **mean of the available budget dimensions**. Each declared dimension
is `min(actual / max, 1.0)` (cap-zero rule: `cap == 0` ⇒ `1.0` when `actual > 0`
else `0.0`):

- `retry_cost = min(retry_count / max_retries, 1.0)`
- `tool_cost = min(tool_call_count / max_tool_calls, 1.0)`
- `token_cost = min(token_usage / max_tokens, 1.0)`
- `runtime_cost = min(runtime_seconds / max_runtime_seconds, 1.0)`

A dimension is *available* only when its budget maximum is defined. When a
**declared** budget dimension's telemetry is unmeasured (`token_usage` /
`runtime_seconds` is `None`), it is **conservatively saturated to `1.0`** — BOUND
cannot confirm the step stayed within the budget, so it does not silently score
it as zero cost. **No budget at all ⇒ `C = 0.0`** (cost cannot be assessed without
declared budgets).

## Influence (I) ∈ [-1, 1]

`I = 0.0` by default. v0.3 does **not** derive downstream influence from contract
evidence — honesty over invented sophistication. A caller may instead supply
influence externally at `ContractEvaluator` construction (or via
`CodingWorkflowEvaluator(influence=…)` on the workflow path); the default is an
explicit `0.0` with a provenance note.

## Provenance

Every dimension emits `ScoreEvidence` records (source, value, contribution,
description) collected on `ContractEvaluator.provenance` and forwarded onto
`EvaluationResult.provenance`. A consumer must be able to answer "why is
`A = 0.67`?" by reading the provenance — e.g. *"✓2 of 3 required check(s)
passed: …; ✗ edge_cases_handled. A = 2/3 = 0.6667."* Each `EvaluationScores`
also carries a structured `reasoning` string that self-explains without the
result object.
