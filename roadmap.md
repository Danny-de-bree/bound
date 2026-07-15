# BOUND — Roadmap

## Vision

BOUND starts as a small deterministic bounded-utility policy.

The long-term goal is broader:

> Give agents an explicit mechanism to decide when an action is good enough to continue, when to retry, when to replan, and when to roll back.

The core principle remains:

```text
S = (W_A × A) + (W_I × I) - (W_R × R) - (W_C × C)
```

Where:

| Variable | Meaning                     |
| -------- | --------------------------- |
| `S`      | Final bounded utility score |
| `A`      | Acceptance score            |
| `I`      | Downstream influence        |
| `R`      | Risk penalty                |
| `C`      | Resource penalty            |
| `W_A`    | Acceptance weight           |
| `W_I`    | Influence weight            |
| `W_R`    | Risk weight                 |
| `W_C`    | Cost weight                 |
| `T`      | Acceptance threshold         |

All weights default to `1.0`, so v0.1's `S = (W × A) + I - R - C` is reproduced
exactly (`W_A = W`, `W_I = W_R = W_C = 1.0`).

The success condition is:

```text
S >= T
```

The objective is not to maximize `S` indefinitely.

The objective is to cross the required threshold and continue making progress.

---

# Stage 1 — Deterministic BOUND core

## Goal

Build the mathematical and policy foundation without any model dependency.

Inputs:

```text
Action
Goal
Context
A
I
R
C
W
T
```

Output:

```text
BOUND score
Decision
Structured result
Steering prompt
```

Core pipeline:

```text
EvaluationScores
      │
      ▼
S = (W_A×A) + (W_I×I) - (W_R×R) - (W_C×C)
      │
      ▼
Fixed-order decision:
  1. risk >= rollback_risk_threshold -> ROLLBACK  (safety, checked first)
  2. S >= T                          -> ACCEPT
  3. gap = T - S <= retry_margin     -> RETRY
  4. otherwise                       -> REPLAN
```

At this stage, the caller provides:

```text
A
I
R
C
```

directly.

This keeps the policy:

* deterministic
* testable
* model-agnostic
* agent-agnostic
* easy to inspect

---

# Stage 2 — Pluggable evaluators

## Goal

Allow different systems to produce the four BOUND dimensions without changing the policy engine.

Introduce a generic evaluator interface:

```python
class Evaluator(Protocol):
    def evaluate(self, action: Action) -> EvaluationScores:
        ...
```

Possible evaluators:

```text
StaticEvaluator
HumanEvaluator
RuleBasedEvaluator
LLMEvaluator
RewardModelEvaluator
EnvironmentEvaluator
CompositeEvaluator
```

BOUND must not care where the scores came from.

The architecture becomes:

```text
Action
  │
  ▼
Evaluator
  │
  ▼
A / I / R / C
  │
  ▼
BOUND
  │
  ▼
Decision
```

LLM-based judging is only one possible implementation.

It must never become a required dependency of the core package.

---

# Stage 3 — Observable workflow signals

## Goal

Start deriving BOUND scores from measurable agent workflow data.

Instead of asking a model to estimate everything, collect objective signals directly from the environment.

Examples:

```text
test_pass_rate
goal_coverage
lint_status
type_check_status
number_of_failures
number_of_retries
diff_size
files_changed
unexpected_files_changed
blast_radius
rollback_available
execution_latency
tool_calls
token_usage
compute_usage
monetary_cost
remaining_budget
dependency_health
```

Example workflow state:

```text
test_pass_rate          = 1.00
goal_coverage           = 0.80
unexpected_file_changes = 0.00
blast_radius            = 0.20
reversibility           = 0.95
tool_cost               = 0.10
latency_cost            = 0.15
retry_pressure          = 0.30
```

These signals can feed deterministic scoring functions.

---

# Stage 4 — Deterministic component scoring

## Goal

Derive `A`, `I`, `R`, and `C` from lower-level workflow metrics.

Example:

```text
raw workflow signals
        │
        ▼
deterministic scoring functions
        │
        ▼
A / I / R / C
        │
        ▼
BOUND score
        │
        ▼
decision
```

Possible mappings:

```text
A = f(
    test_pass_rate,
    goal_coverage,
    acceptance_checks
)

I = f(
    dependency_health,
    future_optionality,
    downstream_blockers
)

R = f(
    blast_radius,
    reversibility,
    failure_probability,
    unexpected_changes
)

C = f(
    token_usage,
    tool_calls,
    latency,
    compute,
    retry_count
)
```

An example deterministic configuration could be:

```text
A =
    0.6 × test_pass_rate
    +
    0.4 × goal_coverage
```

And:

```text
C =
    0.4 × normalized_token_cost
    +
    0.3 × normalized_tool_cost
    +
    0.2 × normalized_latency
    +
    0.1 × retry_pressure
```

The exact weights should not be hard-coded as universal truth.

They should eventually be configurable per workflow or domain.

---

# Stage 5 — Hybrid evaluation

## Goal

Use deterministic signals wherever possible and model judgement only where necessary.

Some signals are naturally measurable:

```text
tests passed
runtime
tokens used
number of retries
diff size
files changed
rollback availability
```

Other signals are harder to determine mechanically:

```text
Does this actually satisfy the user's intent?

Does this architectural choice create future problems?

Is the implementation semantically correct?

Is the proposed action aligned with the broader goal?
```

The hybrid architecture becomes:

```text
                 ┌─────────────────────┐
                 │ Observable signals  │
                 └──────────┬──────────┘
                            │
                            ▼
                 Deterministic scoring
                            │
                            │
Action ────────► Semantic evaluator
                            │
                            ▼
                     Score fusion
                            │
                            ▼
                       A / I / R / C
                            │
                            ▼
                          BOUND
```

The preferred principle is:

> Use deterministic evidence first. Use model judgement only for uncertainty that cannot be measured directly.

---

# Stage 6 — Agent workflow integration

## Goal

Use BOUND as a continuation and stopping policy inside real agent loops.

Example:

```text
Agent proposes action
        │
        ▼
Agent executes action
        │
        ▼
Environment produces evidence
        │
        ▼
BOUND evaluates outcome
        │
        ├── ACCEPT ────► continue to next goal
        │
        ├── RETRY ─────► try cheaper/better execution
        │
        ├── REPLAN ────► choose another strategy
        │
        └── ROLLBACK ──► revert unsafe action
```

This makes BOUND more than an action scorer.

It becomes a control policy for the agent loop.

---

# Stage 7 — Explicit stopping policy

## Goal

Prevent endless agent optimization.

A common failure mode in agent systems is:

```text
working solution
    ↓
additional refinement
    ↓
more tool calls
    ↓
more changes
    ↓
new regressions
    ↓
more refinement
```

BOUND should provide an explicit stop condition:

```text
if S >= T:
    stop optimizing the current action
    continue toward the next goal
```

Example:

```text
Tests passed:              100%
Required changes complete: 100%
Unexpected changes:          0%
Rollback available:         yes
Remaining token budget:      72%

BOUND score: 0.84
Threshold:   0.70

Decision: ACCEPT

The current result is sufficiently good.
Further optimization is not required.
Continue.
```

This is a core long-term use case of BOUND.

---

# Stage 8 — Hierarchical BOUND

## Goal

Apply bounded utility at multiple levels of an agent workflow.

BOUND can potentially evaluate:

```text
single tool call
single code change
single task
sub-goal
plan
full mission
```

Example hierarchy:

```text
Mission
  │
  ├── Goal
  │    │
  │    ├── Task
  │    │    │
  │    │    ├── Action
  │    │    └── Action
  │    │
  │    └── Task
  │
  └── Goal
```

Each level may have its own:

```text
W
T
A
I
R
C
```

A local action may be acceptable while negatively affecting the larger mission.

This is where downstream influence becomes increasingly important.

---

# Stage 9 — Adaptive thresholds

## Goal

Allow acceptance thresholds to vary based on context.

Not every action should require the same level of confidence.

Examples:

```text
Low-risk formatting change
T = low
```

```text
Production database migration
T = high
```

Potential threshold inputs:

```text
reversibility
blast radius
environment
user importance
remaining budget
time pressure
mission criticality
```

Example:

```text
T = base_threshold
    + production_risk_adjustment
    + irreversibility_adjustment
```

This should remain deterministic where possible.

---

# Stage 10 — Learning from real workflow outcomes

## Goal

Improve scoring functions using observed agent behavior.

Collect data such as:

```text
input signals
calculated A/I/R/C
BOUND decision
actual outcome
rollback required
human override
task success
cost
time to completion
```

This can later be used to calibrate:

```text
scoring weights
thresholds
risk models
cost models
decision heuristics
```

The first versions should remain understandable and hand-configurable.

Machine learning should only be introduced once enough real-world data exists.

---

# Long-term architecture

```text
               Agent workflow
                     │
                     ▼
              Proposed action
                     │
                     ▼
       ┌──────────────────────────┐
       │ Workflow instrumentation │
       └─────────────┬────────────┘
                     │
           Observable signals
                     │
          ┌──────────┴───────────┐
          │                      │
          ▼                      ▼
 Deterministic scoring    Semantic evaluator
          │                      │
          └──────────┬───────────┘
                     ▼
                  A I R C
                     │
                     ▼
          S = (W_A×A) + (W_I×I) - (W_R×R) - (W_C×C)
                     │
                     ▼
   Fixed-order decision:
     1. risk >= rollback_risk_threshold -> ROLLBACK  (safety, first)
     2. S >= T                          -> ACCEPT
     3. gap = T - S <= retry_margin     -> RETRY
     4. otherwise                       -> REPLAN
```

`ROLLBACK` is a peer outcome triggered by a hard safety boundary — it is not an
escalation that `REPLAN` fails into. A future multi-step controller *could*
escalate a sequence of `REPLAN` outcomes to a `ROLLBACK`, but that is not how the
single-action v0.2 policy behaves.

---

# Design principles

## 1. Deterministic where possible

Do not use an LLM to estimate something that the environment can measure directly.

---

## 2. Model-agnostic

No model provider should be required by the BOUND core.

---

## 3. Observable

A BOUND decision should be explainable from its inputs.

The user should be able to inspect:

```text
W_A / W_I / W_R / W_C
A
I
R
C
T
S
distance_to_threshold
provenance (per-dimension evidence)
```

and understand why a decision occurred.

---

## 4. Bounded, not optimal

BOUND should not encourage endless optimization.

The primary question is:

```text
Is this good enough to continue?
```

Not:

```text
Is this the best possible result?
```

"Bounded" here means optimization is bounded by an explicit acceptance
threshold (a satisficing policy: once `S >= T`, stop optimizing this step). It
does **not** mean the utility function itself has a bounded or concave
mathematical shape — the score is a plain linear combination.

---

## 5. Evidence over judgement

Prefer:

```text
10/10 tests passed
```

over:

```text
A model thinks the implementation probably works.
```

Use judgement only where evidence is incomplete.

---

## 6. The policy remains simple

Even as evaluation becomes more sophisticated, the core policy should remain understandable.

The complexity should live primarily in:

```text
how evidence becomes A / I / R / C
```

not in an opaque final decision layer.

---

# Near-term roadmap

## v0.1

* deterministic BOUND calculator
* Pydantic models
* explicit `A / I / R / C`
* deterministic decisions
* CLI
* unit tests
* steering prompts

## v0.2

* symmetric `BoundWeights` (`W_A / W_I / W_R / W_C`), v0.1 `weight` kept as alias
* coherent decision semantics — `ROLLBACK` (safety) → `ACCEPT` → `RETRY` →
  `REPLAN`, all four reachable (no float-equality trap)
* `retry_margin` and `rollback_risk_threshold` criteria
* `distance_to_threshold` (`S - T`) on every result
* deterministic coding-workflow signals (`CodingWorkflowSignals`,
  `WorkflowNormalization`)
* `CodingWorkflowEvaluator` with auditable `ScoreEvidence` provenance
* experiment harness + benchmark trajectories (where BOUND would stop)
* updated prompts and CLI (per-dimension weight flags, `evaluate-workflow`)

## v0.3

* composite evaluators
* optional semantic (LLM-as-judge) evaluator behind the protocol
* first hybrid deterministic/model scoring

## v0.4

* integration into a real coding-agent workflow
* explicit stop/continue loop
* production usage data collection
* threshold calibration

## Later

* hierarchical BOUND
* adaptive / learned thresholds
* mission-level policies
* provider-specific optional evaluator packages
