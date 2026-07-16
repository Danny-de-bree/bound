# BOUND Architecture and Scoring

This document explains how BOUND turns execution evidence into a deterministic control decision.

## Control loop

```text
Goal + plan
    ↓
StepContract
    ↓
Agent executes
    ↓
ExecutionEvidence
    ↓
ContractEvaluator
    ↓
A / I / R / C
    ↓
BoundCalculator
    ↓
BoundPolicy
    ↓
ACCEPT / RETRY / REPLAN / ROLLBACK
```

BOUND is not an agent framework.

The agent owns planning and execution. BOUND evaluates meaningful step boundaries.

---

## Score

BOUND uses:

```text
S = (W_A × A) + (W_I × I) - (W_R × R) - (W_C × C)
```

Where:

| Variable | Meaning |
| --- | --- |
| `S` | Final BOUND score |
| `A` | Acceptance |
| `I` | Downstream influence |
| `R` | Risk |
| `C` | Resource cost |
| `W_A` | Acceptance weight |
| `W_I` | Influence weight |
| `W_R` | Risk weight |
| `W_C` | Cost weight |
| `T` | Acceptance threshold |

The objective is not to maximize `S` indefinitely.

The success condition is:

```text
S >= T
```

Once the threshold is reached, the current step is sufficiently good.

---

## Default weights

All four weights default to:

```text
1.0
```

So the default formula is:

```text
S = A + I - R - C
```

Weights are policy configuration.

They express how strongly each dimension affects the final decision.

The defaults are reference values, not scientifically calibrated universal settings.

---

## Evidence to acceptance

A `StepContract` defines required acceptance checks.

Example:

```text
valid input succeeds       PASS
invalid input is rejected  PASS
all tests pass             FAIL
```

Then:

```text
A = passed required checks / total required checks
A = 2 / 3
A = 0.67
```

Missing required evidence does not silently pass.

---

## Evidence to risk

Risk comes from explicit risk checks and observable safety signals.

Example:

```text
no plaintext secret committed  PASS
unexpected files changed       NO
rollback available              YES
```

This produces a low risk score.

A violated high-severity risk check increases `R`.

The final value remains bounded to:

```text
R ∈ [0, 1]
```

---

## Evidence to cost

Cost can be derived from explicit resource budgets.

Example contract budget:

```text
max tool calls = 20
```

Observed execution:

```text
tool calls = 5
```

Then the normalized tool-call contribution is:

```text
5 / 20 = 0.25
```

When multiple budget dimensions are available, BOUND combines the normalized values according to the deterministic contract evaluator.

Examples include:

- retries
- tool calls
- tokens
- runtime

---

## Downstream influence

Influence represents whether the current result helps or hurts later goals.

```text
I ∈ [-1, 1]
```

In the deterministic contract path, BOUND does not invent influence when no defensible downstream evidence exists.

The default is:

```text
I = 0.0
```

An external evaluator may provide influence evidence later without changing the final BOUND policy.

---

## Worked example

Suppose an agent step produces:

```text
Acceptance A = 0.90
Influence  I = 0.20
Risk       R = 0.10
Cost       C = 0.20
```

With default weights:

```text
W_A = 1.0
W_I = 1.0
W_R = 1.0
W_C = 1.0
```

Then:

```text
S = (1.0 × 0.90)
  + (1.0 × 0.20)
  - (1.0 × 0.10)
  - (1.0 × 0.20)

S = 0.80
```

With:

```text
T = 0.60
```

we get:

```text
0.80 >= 0.60
```

Therefore:

```text
ACCEPT
```

Further optimization of the current step is not required.

---

## Decision order

BOUND applies decisions in this order:

```text
1. risk >= rollback_risk_threshold  → ROLLBACK
2. S >= T                           → ACCEPT
3. T - S <= retry_margin            → RETRY
4. otherwise                        → REPLAN
```

### ROLLBACK

Triggered by a hard risk boundary.

A high utility score cannot override a configured safety limit.

### ACCEPT

The result crossed the configured threshold.

Stop optimizing the current step and continue.

### RETRY

The result is below threshold but close enough that one focused retry is justified.

### REPLAN

The result is too far below threshold.

Choose a materially different strategy.

---

## Threshold distance

BOUND exposes:

```text
distance_to_threshold = S - T
```

Therefore:

```text
positive  → above threshold
zero      → exactly at threshold
negative  → below threshold
```

Below threshold, retry routing may equivalently use:

```text
gap = T - S
```

For below-threshold results:

```text
gap = -distance_to_threshold
```

---

## Example multi-step agent workflow

```text
Step 1
tests failing
S = 0.31
→ REPLAN

Step 2
most checks pass
S = 0.64
T = 0.70
gap = 0.06
→ RETRY

Step 3
all required checks pass
S = 0.84
T = 0.70
→ ACCEPT
```

At that point the agent should stop optimizing the current objective.

A hypothetical later refactor is never executed.

---

## Objective and subjective evidence

BOUND works directly with observable evidence such as:

- tests
- lint
- type checks
- artifacts
- resource usage
- retries
- rollback state

Subjective goals require an external evidence source.

Examples:

```text
"the architecture is elegant"
"the UX feels polished"
"the writing is excellent"
```

Possible evidence sources include:

- human review
- deterministic rubrics
- static analysis
- reward models
- future optional semantic evaluators

The final BOUND policy can remain deterministic after the evidence is supplied.

---

## Architecture principles

### Deterministic final policy

Once inputs are available, calculation and decision selection are reproducible.

### Agent agnostic

BOUND does not depend on Cline, Claude Code, Kilo, Hermes, or another framework.

### Provider agnostic

No model provider is required by the deterministic core.

### Evidence before judgement

Prefer observable evidence over semantic judgement whenever possible.

### Thin integration

Agent frameworks should provide evidence and consume the decision.

They should not duplicate BOUND policy logic.

---

## What is configurable?

BOUND can be tuned through:

```text
acceptance threshold
retry margin
rollback risk threshold
acceptance weight
influence weight
risk weight
cost weight
```

This allows different operating modes.

For example:

```text
higher T
→ require a stronger result before accepting

higher W_C
→ penalize expensive execution more strongly

higher W_R
→ penalize risk more strongly

lower rollback risk threshold
→ enforce a stricter hard safety boundary
```

The correct settings depend on the workload.

BOUND does not claim that one universal configuration is optimal.
