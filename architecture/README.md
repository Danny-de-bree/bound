# BOUND Architecture and Scoring

BOUND is a deterministic control harness for AI agent workflows.

This document describes the mechanics behind the harness: contracts, execution evidence, score calculation, thresholds, and the final policy decision.

## Architecture

```text
Agent / framework
        ↓
Goal + plan
        ↓
StepContract
        ↓
Agent executes
        ↓
ExecutionEvidence
        ↓
Evaluation layer
        ↓
A / I / R / C
        ↓
BoundPolicy
        ↓
ACCEPT / RETRY / REPLAN / ROLLBACK
        ↓
Agent control flow
```

Responsibilities stay separated:

```text
Agent                              → planning and execution
StepContract + ExecutionEvidence   → evaluation layer
BoundPolicy                        → deterministic decision engine
BOUND                              → deterministic control harness
```

BOUND does not decide what code an agent should write. It evaluates the result of a meaningful step and determines what the control loop should do next.

## Run lineage

A full run is one lineage from pre-run intent to a post-run execution record. The
architecture above is the inner per-step loop; the bookends are planner-owned
intent and an honest execution audit:

```text
PLAN.md                 → pre-run intent (planner-owned, at the repository root)
    ↓
StepContract            → machine-readable contract derived from the plan
    ↓
Agent executes          → the owning agent does the work
    ↓
ExecutionEvidence       → observed facts only — never fabricated
    ↓
BoundPolicy             → deterministic decision engine (ACCEPT / RETRY / REPLAN / ROLLBACK)
    ↓
Agent control action    → continue / retry / replan / rollback
    ↓
INTEGRATION_REPORT.md   → post-run execution record (an audit, not a rewrite of the plan)
```

`PLAN.md` records intent before the run; `INTEGRATION_REPORT.md` records what
actually happened after the run. The report references the plan and preserves the
same stable step IDs, but it is never a rewritten copy of the plan, and `PLAN.md`
is never placed inside `bound_integration/`.

## Mathematical formulation

BOUND evaluates outcomes using:

```text
S = (W_A × A) + (W_I × I) - (W_R × R) - (W_C × C)
```

| Variable | Description |
| --- | --- |
| `S` | Final score |
| `A` | Acceptance score |
| `I` | Downstream influence |
| `R` | Risk penalty |
| `C` | Resource penalty |
| `W_A` | Acceptance weight |
| `W_I` | Influence weight |
| `W_R` | Risk weight |
| `W_C` | Cost weight |
| `T` | Acceptance threshold |

The success condition is:

```text
S >= T
```

The objective is **not** to maximize `S` indefinitely.

The objective is to cross the configured threshold and continue making progress toward the larger goal.

## Default weights

With all weights set to `1.0`:

```text
S = A + I - R - C
```

Weights are configurable policy parameters. The defaults are reference values, not universally calibrated settings.

## Acceptance

Acceptance represents how well the current result satisfies the explicit requirements for a step.

For deterministic contract evaluation, required checks can be mapped into an acceptance score.

Example:

```text
Valid input succeeds       PASS
Invalid input is rejected  PASS
All tests pass             FAIL
```

Then:

```text
A = passed required checks / total required checks
A = 2 / 3
A ≈ 0.67
```

Missing required evidence must never silently count as success.

## Influence

Influence represents the effect of the current result on downstream goals.

```text
I ∈ [-1, 1]
```

Positive influence helps future progress. Negative influence makes future progress harder.

When no defensible downstream evidence exists, the deterministic path should not invent it. A neutral default is:

```text
I = 0.0
```

## Risk

Risk represents potential downside.

Examples include:

- violated safety constraints
- unexpected changes
- destructive operations
- unavailable rollback
- explicitly defined high-risk conditions

```text
R ∈ [0, 1]
```

Risk also has a hard boundary through `rollback_risk_threshold`. A high utility score therefore cannot override a configured rollback condition.

## Cost

Cost represents resource consumption.

Depending on the workflow, it can include:

- tool calls
- retries
- tokens
- runtime

Example:

```text
tool-call budget = 20
observed calls   = 5

normalized contribution = 5 / 20 = 0.25
```

When multiple cost signals are used, their exact normalization and aggregation rules should remain explicit and deterministic.

## Worked calculation

Suppose:

```text
A = 0.90
I = 0.20
R = 0.10
C = 0.20
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

If:

```text
T = 0.60
```

then:

```text
0.80 >= 0.60
```

The policy returns:

```text
ACCEPT
```

The current step crossed the required threshold. Further optimization of that step is not required.

## Decision order

BOUND evaluates decisions in a fixed order:

```text
1. risk >= rollback_risk_threshold  → ROLLBACK
2. S >= T                           → ACCEPT
3. T - S <= retry_margin            → RETRY
4. otherwise                        → REPLAN
```

The ordering matters.

### ROLLBACK

A hard risk boundary has been exceeded.

```text
Return to a safe state where possible.
Then reconsider the approach.
```

### ACCEPT

The result crossed the acceptance threshold.

```text
Stop optimizing this step.
Continue toward the next goal.
```

### RETRY

The result is below threshold but within the configured retry margin.

```text
Keep the current strategy.
Make one focused correction.
```

### REPLAN

The result is too far below threshold for a focused retry.

```text
Stop iterating on the current strategy.
Choose a materially different approach.
```

## Distance to threshold

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

The retry rule uses:

```text
gap = T - S
```

For below-threshold results:

```text
gap = -distance_to_threshold
```

## Multi-step example

A real agent loop evaluates more than one attempt.

### Attempt 1 — REPLAN

```text
A = 0.40
I = 0.00
R = 0.05
C = 0.04

S = 0.40 + 0.00 - 0.05 - 0.04
S = 0.31

T = 0.70
```

The result is far below threshold:

```text
→ REPLAN
```

### Attempt 2 — RETRY

After changing strategy:

```text
S = 0.64
T = 0.70
retry_margin = 0.10

gap = T - S
gap = 0.70 - 0.64
gap = 0.06

0.06 <= 0.10
```

Therefore:

```text
→ RETRY
```

The approach is close enough for one focused correction.

### Attempt 3 — ACCEPT

After the correction:

```text
S = 0.84
T = 0.70

0.84 >= 0.70
```

Therefore:

```text
→ ACCEPT
```

The agent should now stop optimizing the current objective and continue toward the next goal.

## Objective and subjective evidence

BOUND works directly with observable evidence such as:

- tests
- lint
- type checks
- artifacts
- resource usage
- retries
- runtime
- rollback state

Subjective goals require an external evidence source.

Examples include:

```text
"The architecture is elegant."
"The UX feels polished."
"The writing is excellent."
```

Possible evidence sources include:

- human review
- deterministic rubrics
- static analysis
- reward models
- optional semantic evaluators

The evidence source may be probabilistic or model-based. The final `BoundPolicy` decision can still remain deterministic once the scores are supplied.

## Configuration

The main policy controls are:

```text
threshold
retry_margin
rollback_risk_threshold
acceptance weight
influence weight
risk weight
cost weight
```

Examples:

```text
higher threshold
→ require stronger evidence before accepting

higher cost weight
→ penalize expensive execution more strongly

higher risk weight
→ make risk more influential in the score

lower rollback risk threshold
→ trigger the hard safety boundary earlier
```

There is no universally optimal configuration. Thresholds and weights should be calibrated against the workload.

## Design principles

### Deterministic final control

Once evaluation inputs are available, score calculation and decision selection are reproducible.

### Agent agnostic

BOUND does not depend on a specific agent framework.

### Provider agnostic

The deterministic core does not depend on an LLM provider.

### Evidence first

Use observable execution evidence whenever possible.

### Thin integrations

Framework integrations should collect evidence and consume BOUND decisions rather than reimplementing the policy.

### Satisficing over endless optimization

BOUND exists to answer:

> **Is the current result good enough to continue?**

Not:

> Can this result be optimized forever?