# Concepts

This document is the detailed reference for *what BOUND is and why*. The
[README](../README.md) is intentionally integration-first and concise; the
background lives here.

## What BOUND is

BOUND is a **deterministic control harness** for agentic systems. At its core sits
`BoundPolicy`, the deterministic decision engine that applies a bounded-utility
policy: most agents are optimised to find the *best possible* action, while BOUND
helps an agent decide whether a proposed action is **good enough to continue**
toward the larger goal — and when to retry, replan, or roll back.

The core is **deterministic, model-agnostic, and network-free**: once
evaluation scores are supplied, every downstream calculation is reproducible and
requires no LLM SDK and no network access.

## The bounded-utility score

```text
S = (W_A × A) + (W_I × I) - (W_R × R) - (W_C × C)
```

| Variable | Meaning                     | Range      |
| -------- | --------------------------- | ---------- |
| `S`      | Final bounded utility score  | unbounded  |
| `A`      | Acceptance score            | `[0, 1]`   |
| `I`      | Downstream influence        | `[-1, 1]`  |
| `R`      | Risk penalty                | `[0, 1]`   |
| `C`      | Resource cost               | `[0, 1]`   |
| `W_A`    | Acceptance weight           | `>= 0`     |
| `W_I`    | Influence weight            | `>= 0`     |
| `W_R`    | Risk weight                 | `>= 0`     |
| `W_C`    | Cost weight                 | `>= 0`     |
| `T`      | Acceptance threshold         | `>= 0`     |

Every weight defaults to `1.0`, so the v0.1 formula `S = (W × A) + I - R - C`
is reproduced exactly when only the legacy acceptance `weight` is set
(`W_A = W`, `W_I = W_R = W_C = 1.0`).

The success condition is **not** *maximise `S`* — it is *cross the threshold and
continue*:

```text
S >= T
```

The threshold is intentionally **not** capped at `1.0`: when a weight exceeds
`1.0`, `S` can exceed `1.0`, so a legitimate threshold may too.

## What "bounded" means

"BOUND" does **not** mean the utility function has a bounded or concave
mathematical shape. `S` is an ordinary linear combination, unbounded above and
below. "BOUND" means **optimisation is bounded by an explicit acceptance
threshold** — a satisficing policy:

```text
once S >= T:
    stop optimising this step
```

Once the threshold is crossed, further optimisation of the current action is not
required; the agent continues toward the larger goal. The value is in the
explicit stop condition and the auditable derivation of the inputs, not in the
one-line arithmetic.

## The four decisions

The deterministic `BoundPolicy` applies this rule, in order:

1. `risk >= rollback_risk_threshold` → **ROLLBACK** (a hard safety boundary,
   checked *first* so a high-scoring but unsafe action still rolls back).
2. `S >= T` → **ACCEPT** (boundary-inclusive: `S == T` accepts).
3. `gap = T - S` and `gap <= retry_margin` → **RETRY** (close enough to the
   threshold to justify one more attempt in the same strategy).
4. otherwise → **REPLAN** (too far below the threshold; choose a materially
   different strategy).

See [architecture.md](architecture.md) for where each of these lives in code.

## Objective vs subjective evidence

BOUND consumes **evidence** about what actually happened during a step. Not all
goals produce evidence of the same kind.

**OBJECTIVE / OBSERVABLE** (BOUND handles these directly):

- tests (pass/fail)
- lint (clean / not)
- type checks (clean / not)
- artifacts produced
- budgets (retries, tool calls, tokens, runtime)
- rollback state availability

**SUBJECTIVE / SEMANTIC** (BOUND does *not* magically convert these into
objective evidence):

- code quality
- architectural elegance
- UX quality
- whether prose is "good"

> BOUND does not magically convert subjective goals into objective evidence.

Subjective criteria require an **external evidence source**, such as:

- human review
- a deterministic rubric where one is possible
- static analysis
- a reward model
- an optional future semantic evaluator

Crucially, the final BOUND policy can still remain deterministic *after* that
evidence is supplied: whatever produces the `A / I / R / C` scores, the decision
rule is the same pure function. **There is no LLM-as-judge in v0.4.** An LLM may
be used only to *draft* an evaluation contract (structured data only), never to
make the final decision or assign `A / I / R / C`.

See [scoring.md](scoring.md) for exactly how evidence becomes scores.
