# BOUND — TODO v0.2

## Objective

BOUND v0.2 should turn the current v0.1 foundation into a more credible agent-control policy.

The goal is not to add many features.

The goal is to fix the weakest parts of v0.1:

1. Make decision semantics coherent.
2. Make `REPLAN` a real reachable outcome.
3. Separate safety rollback from ordinary below-threshold decisions.
4. Make score weighting explicit and symmetric.
5. Make threshold behavior inspectable.
6. Add the first real coding-agent workflow signals.
7. Add an experiment harness to test whether BOUND actually reduces unnecessary agent work.

Do not add an LLM provider dependency in v0.2.

Do not implement Cline integration yet.

Do not claim that BOUND improves agent performance until the experiment harness produces evidence.

---

# Core principle

BOUND remains based on satisficing:

```text
S >= T
```

Once the acceptance threshold is crossed, further optimization is not required.

The score remains:

```text
S = weighted utility - penalties
```

The exact v0.2 formulation should become:

```text
S = (W_A × A) + (W_I × I) - (W_R × R) - (W_C × C)
```

Where:

| Variable | Meaning              |
| -------- | -------------------- |
| `S`      | Final BOUND score    |
| `A`      | Acceptance           |
| `I`      | Downstream influence |
| `R`      | Risk                 |
| `C`      | Resource cost        |
| `W_A`    | Acceptance weight    |
| `W_I`    | Influence weight     |
| `W_R`    | Risk weight          |
| `W_C`    | Cost weight          |
| `T`      | Acceptance threshold |

The v0.1 formula:

```text
S = (W × A) + I - R - C
```

must remain expressible as the default configuration:

```text
W_A = W
W_I = 1.0
W_R = 1.0
W_C = 1.0
```

Do not break the mathematical behavior of existing users unless explicitly documented.

---

# Phase 1 — Rework criteria and weights

## Goal

Remove the arbitrary assumption that only acceptance can be weighted.

Replace or extend the existing criteria model.

Suggested model:

```python
class BoundWeights(BaseModel):
    acceptance: float = Field(default=1.0, ge=0.0)
    influence: float = Field(default=1.0, ge=0.0)
    risk: float = Field(default=1.0, ge=0.0)
    cost: float = Field(default=1.0, ge=0.0)
```

And:

```python
class BoundCriteria(BaseModel):
    threshold: float
    retry_margin: float = Field(default=0.1, ge=0.0)
    rollback_risk_threshold: float = Field(default=0.8, ge=0.0, le=1.0)
    weights: BoundWeights = Field(default_factory=BoundWeights)
```

If backward compatibility with the existing `weight` field is desired, support it only through a clearly documented migration path.

Do not silently keep two competing weight systems.

---

## Required score formula

Implement exactly:

```python
score = (
    criteria.weights.acceptance * scores.acceptance
    + criteria.weights.influence * scores.influence
    - criteria.weights.risk * scores.risk
    - criteria.weights.cost * scores.cost
)
```

Do not:

* clamp
* normalize
* apply nonlinear transforms
* round internally

---

## Required tests

Test:

```text
all default weights = 1.0
```

Test independent effects of:

```text
W_A
W_I
W_R
W_C
```

Test that increasing:

```text
W_R
```

makes high-risk actions score lower.

Test that increasing:

```text
W_C
```

makes expensive actions score lower.

Test negative influence with influence weighting.

---

# Phase 2 — Fix decision semantics

## Goal

Make all four decisions distinct, meaningful, and reachable.

The current v0.1 rule:

```text
risk > cost -> ROLLBACK
cost > risk -> RETRY
risk == cost -> REPLAN
```

must be removed.

Exact float equality must never determine whether `REPLAN` is reachable.

---

# New decision model

Use the following deterministic order.

## 1. ROLLBACK

Rollback is a safety condition.

It is independent from whether risk happens to be greater than cost.

Use:

```python
if scores.risk >= criteria.rollback_risk_threshold:
    return "ROLLBACK"
```

This condition should be evaluated before ordinary retry/replan behavior.

Document explicitly:

```text
ROLLBACK means the proposed or executed action exceeds the configured acceptable risk boundary.
```

Do not define rollback as:

```text
risk is the largest negative component
```

---

## 2. ACCEPT

If rollback is not required:

```python
if score >= criteria.threshold:
    return "ACCEPT"
```

Decision meaning:

```text
The action is sufficiently good.

Stop optimizing the current action and continue toward the larger goal.
```

---

## 3. RETRY

Calculate:

```python
gap = criteria.threshold - score
```

If:

```text
0 < gap <= retry_margin
```

return:

```text
RETRY
```

Decision meaning:

```text
The current approach is close enough to acceptable that another attempt within the same action space is justified.
```

---

## 4. REPLAN

Otherwise:

```text
REPLAN
```

Decision meaning:

```text
The current approach is too far below the acceptance threshold.

Choose a materially different strategy.
```

---

# Exact algorithm

Implement approximately:

```python
def decide(
    *,
    score: float,
    scores: EvaluationScores,
    criteria: BoundCriteria,
) -> Decision:
    if scores.risk >= criteria.rollback_risk_threshold:
        return "ROLLBACK"

    if score >= criteria.threshold:
        return "ACCEPT"

    gap = criteria.threshold - score

    if gap <= criteria.retry_margin:
        return "RETRY"

    return "REPLAN"
```

Keep it pure and fully unit tested.

---

# Important semantic rule

A high-scoring action may still produce:

```text
ROLLBACK
```

if it violates the configured hard risk threshold.

This is intentional.

BOUND should distinguish:

```text
utility threshold
```

from:

```text
safety boundary
```

Document this clearly.

---

# Phase 3 — Resolve ROADMAP inconsistencies

## Goal

Make code, README, TODO, and ROADMAP use the same decision semantics.

Update all documentation.

The canonical meanings become:

```text
ACCEPT
The action is good enough. Stop optimizing and continue.

RETRY
The action is close to acceptable. Try again within the same general approach.

REPLAN
The action is not close enough to acceptable. Choose a different strategy.

ROLLBACK
The action exceeds a configured hard risk boundary. Revert or avoid it where possible.
```

Remove diagrams or text that imply:

```text
REPLAN -> failed -> ROLLBACK
```

unless a future multi-step escalation policy is being explicitly described.

For v0.2:

```text
ROLLBACK
```

is a peer policy outcome triggered by a hard safety condition.

---

# Phase 4 — Make threshold behavior first-class

## Goal

Treat threshold selection as a core part of BOUND rather than an incidental CLI parameter.

Do not implement automatic learned threshold selection yet.

Add explicit threshold metadata to results.

Suggested result additions:

```python
class ThresholdAnalysis(BaseModel):
    threshold: float
    score: float
    gap: float
    margin_to_accept: float
    accepted: bool
```

For accepted results:

```text
margin_to_accept = score - threshold
```

For below-threshold results:

```text
gap = threshold - score
```

Avoid redundant fields if one signed value is clearer.

A simpler model is acceptable:

```python
distance_to_threshold: float
```

where:

```text
positive = above threshold
zero = exactly at threshold
negative = below threshold
```

Choose one representation and document it clearly.

---

## Required tests

Test:

```text
S > T
S == T
S just below T
S far below T
```

Test retry-margin boundaries exactly.

Example:

```text
T = 0.70
retry_margin = 0.10
```

Then:

```text
S = 0.70 -> ACCEPT
S = 0.60 -> RETRY
S = 0.599999 -> REPLAN
```

Unless floating-point tolerance is deliberately introduced.

If tolerance is introduced, make it explicit and deterministic.

---

# Phase 5 — Add workflow signal models

## Goal

Introduce the first deterministic inputs that can later produce BOUND scores.

Do not yet attempt to derive every possible workflow signal.

Focus on coding-agent workflows.

Create a model such as:

```python
class CodingWorkflowSignals(BaseModel):
    test_pass_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    lint_passed: bool | None = None
    type_check_passed: bool | None = None
    required_checks_passed: float | None = Field(default=None, ge=0.0, le=1.0)

    retry_count: int = Field(default=0, ge=0)
    tool_call_count: int = Field(default=0, ge=0)
    token_usage: int | None = Field(default=None, ge=0)
    execution_time_seconds: float | None = Field(default=None, ge=0.0)

    files_changed: int | None = Field(default=None, ge=0)
    unexpected_files_changed: int | None = Field(default=None, ge=0)

    rollback_available: bool | None = None
```

Keep this model generic enough to be populated by different coding agents.

Do not include provider-specific fields.

---

# Phase 6 — Deterministic signal evaluator v1

## Goal

Prove that at least some BOUND inputs can be derived without an LLM.

Create a new evaluator:

```python
class CodingWorkflowEvaluator:
    ...
```

It should take:

```text
Action
CodingWorkflowSignals
```

and produce:

```text
EvaluationScores
```

This first implementation must be intentionally simple and transparent.

Do not pretend the mappings are scientifically calibrated.

Mark them clearly as:

```text
v0.2 reference heuristic
```

---

## Suggested acceptance mapping

Example:

```text
A = mean of available completion signals
```

Possible inputs:

```text
test_pass_rate
required_checks_passed
lint status
type-check status
```

Boolean values may map to:

```text
True = 1.0
False = 0.0
```

Ignore unavailable signals rather than defaulting missing values to zero.

Raise a clear error if no acceptance evidence is available.

---

## Suggested risk mapping

Risk may include:

```text
unexpected file changes
rollback unavailable
large change surface
failed checks
```

Keep the exact rule visible in code.

Example only:

```text
risk increases when unexpected_files_changed > 0
risk increases when rollback_available is False
```

Do not create opaque magic constants without documenting them.

---

## Suggested cost mapping

Cost may use:

```text
retry count
tool calls
token usage
execution time
```

Normalization must be configuration-driven.

For example:

```python
class WorkflowNormalization(BaseModel):
    max_expected_retries: int = 5
    max_expected_tool_calls: int = 50
    max_expected_tokens: int = 100_000
    max_expected_runtime_seconds: float = 3600.0
```

Normalize using explicit caps.

For example:

```python
normalized_tool_calls = min(
    tool_call_count / max_expected_tool_calls,
    1.0,
)
```

Do not normalize against hidden constants.

---

## Influence

Do not fake downstream influence when no evidence exists.

For v0.2, either:

```text
influence = 0.0
```

with an explicit explanation,

or allow it to be provided externally.

Prefer honesty over invented sophistication.

---

# Phase 7 — Explain score provenance

## Goal

Make every score inspectable.

Add provenance metadata.

Suggested model:

```python
class ScoreEvidence(BaseModel):
    source: str
    value: float
    contribution: float | None = None
    description: str | None = None
```

Or a simpler structured equivalent.

The result should allow a consumer to understand:

```text
Why is A = 0.85?
Why is R = 0.30?
Why is C = 0.20?
```

Example:

```text
Acceptance:
- tests: 1.0
- lint: 1.0
- required checks: 0.75

Computed A: 0.92
```

Do not require provenance for manually supplied `StaticEvaluator` scores.

But deterministic evaluators should expose it.

---

# Phase 8 — Update steering prompts

## Goal

Make prompts reflect the new decision semantics.

Examples:

## ACCEPT

```text
The current result meets the required acceptance threshold and does not exceed the configured risk boundary.

Further optimization of this step is not required.

Continue toward the next goal.
```

## RETRY

```text
The current result is close to the required acceptance threshold.

Stay with the same general approach and make one focused attempt to close the remaining gap.
```

## REPLAN

```text
The current result is materially below the required acceptance threshold.

Do not keep iterating on the same approach.

Choose a different strategy that better addresses the goal.
```

## ROLLBACK

```text
The action exceeds the configured acceptable risk boundary.

Avoid or revert the action where possible before continuing.
```

The prompt should include:

```text
score
threshold
distance from threshold
risk
rollback threshold
decision
```

Keep it concise.

---

# Phase 9 — CLI v0.2

Keep direct-score mode.

Example:

```bash
bound evaluate \
  --action "Refactor authentication" \
  --goal "Ship secure login" \
  --acceptance 0.8 \
  --influence 0.1 \
  --risk 0.2 \
  --cost 0.3 \
  --threshold 0.7
```

Add optional weights:

```text
--acceptance-weight
--influence-weight
--risk-weight
--cost-weight
```

Add:

```text
--retry-margin
--rollback-risk-threshold
```

Do not remove the existing CLI without a migration path.

---

## Optional workflow mode

Add only if it can remain clean:

```bash
bound evaluate-workflow \
  --action "Implement feature X" \
  --goal "Complete issue #123" \
  --test-pass-rate 1.0 \
  --lint-passed \
  --type-check-passed \
  --retry-count 2 \
  --tool-call-count 14 \
  --rollback-available
```

This command should use:

```text
CodingWorkflowEvaluator
```

No LLM.

No network.

If adding a second CLI mode makes v0.2 too large, prioritize the Python API and experiment harness instead.

---

# Phase 10 — Real agent-loop experiment harness

## Goal

Test the actual BOUND hypothesis.

The central v0.2 research question is:

> Can BOUND stop a coding agent after a sufficiently good solution has been reached, reducing unnecessary work without materially reducing task success?

Build a small experiment harness.

Do not integrate deeply into an agent framework yet.

Use recorded or manually captured trajectories if necessary.

---

## Experiment input

Represent an agent trajectory as sequential states:

```python
class AgentStep(BaseModel):
    step_index: int
    signals: CodingWorkflowSignals
    scores: EvaluationScores | None = None
```

A trajectory:

```python
class AgentTrajectory(BaseModel):
    task_id: str
    steps: list[AgentStep]
```

---

## BOUND simulation

For each step:

```text
1. calculate scores
2. calculate S
3. apply BOUND decision
4. record the first step that produces ACCEPT
```

This gives:

```text
BOUND stop step
```

Compare against:

```text
actual agent stop step
```

---

# Required experiment metrics

At minimum calculate:

```text
steps_saved
tool_calls_saved
tokens_saved, when available
runtime_saved, when available
```

Also track:

```text
did tests still pass at BOUND stop?
did required checks pass?
did later steps introduce regressions?
```

A particularly important metric:

```text
post-solution unnecessary steps
```

Define:

```text
the number of agent steps executed after the earliest state that already satisfied the task's acceptance criteria
```

BOUND should aim to reduce this.

---

# Phase 11 — Add benchmark fixtures

## Goal

Stop validating BOUND only on the flight example.

Add at least 5 coding-agent trajectory fixtures.

Examples:

```text
1. Agent solves task, then performs unnecessary refactor.
2. Agent gets close, retries once, then succeeds.
3. Agent repeatedly patches the same failing approach.
4. Agent proposes a high-risk destructive action.
5. Agent passes tests but changes unexpected files.
```

Each fixture should have expected policy behavior.

At least one fixture should demonstrate:

```text
ACCEPT
```

At least one:

```text
RETRY
```

At least one:

```text
REPLAN
```

At least one:

```text
ROLLBACK
```

---

# Phase 12 — Document assumptions honestly

Update README with a section:

```text
Current status
```

State clearly:

```text
BOUND v0.2 is an experimental deterministic control policy.

The score formula and default heuristics are hypotheses.

They have not yet been broadly validated across production agent workloads.
```

Document that:

```text
A/I/R/C are not naturally commensurable quantities.
```

The weights are explicit policy parameters.

Do not imply that the defaults are universally correct.

---

# Phase 13 — Clarify what "bounded" means

Add documentation explaining:

BOUND does not currently mean:

```text
the mathematical utility function itself has a bounded or concave shape
```

BOUND currently means:

```text
optimization is bounded by an explicit acceptance threshold
```

In other words:

```text
once S >= T:
    stop optimizing this step
```

This is a satisficing policy.

Use precise language.

Do not overclaim mathematical novelty.

---

# Phase 14 — Competitive positioning

Add a concise section to README.

Do not claim the formula is the moat.

BOUND's intended differentiation is:

```text
provider-agnostic
deterministic final policy
auditable score decomposition
explicit stop condition
no mandatory LLM judge
workflow evidence before semantic judgement
```

The future value is primarily in:

```text
signal collection
score derivation
threshold calibration
agent-loop integration
```

not in the one-line score formula alone.

---

# Required tests

By the end of v0.2, tests must cover:

## Score calculation

* all four independent weights
* default v0.1-equivalent behavior
* positive and negative influence
* negative final scores
* no clamping

## Decision policy

* hard risk rollback
* high score but unsafe -> rollback
* score exactly at threshold -> accept
* score above threshold -> accept
* score just below threshold within retry margin -> retry
* score outside retry margin -> replan
* exact retry-margin boundary
* every decision is reachable

## Workflow signals

* valid signals
* invalid ranges
* missing optional signals
* no acceptance evidence
* deterministic normalization

## Evaluator

* deterministic same-input same-output
* no network
* no model dependency
* explicit provenance

## Experiment harness

* correct first ACCEPT step
* correct steps saved
* regression-after-accept scenario
* trajectory with no ACCEPT result

---

# Definition of Done

BOUND v0.2 is complete when:

```bash
uv run ruff check .
uv run pytest -q
```

pass and all four decisions are meaningfully reachable.

The package must support:

```text
manual score input
```

and at least one:

```text
deterministic coding workflow evaluator
```

The repository must contain a reproducible experiment showing:

```text
where BOUND would stop an agent trajectory
```

and:

```text
how much work would have been avoided
```

Do not claim success based only on the existence of the framework.

The important v0.2 output is evidence.

---

# Out of scope for v0.2

Do not implement:

* Anthropic integration
* OpenAI integration
* DeepSeek integration
* model-specific judges
* Cline plugin
* Cursor integration
* MCP integration
* learned thresholds
* reinforcement learning
* production calibration
* automatic model routing
* persistent mission memory

These may come later.

---

# Priority order

If time or complexity becomes an issue, prioritize:

```text
1. Fix decision semantics
2. Add symmetric weights
3. Reconcile documentation
4. Add deterministic workflow signals
5. Add experiment harness
6. Add benchmark trajectories
7. Improve CLI
```

Do not sacrifice the experiment harness for extra packaging features.

The key question for v0.2 is no longer:

```text
Does the calculator work?
```

It is:

```text
Does BOUND stop an agent at a useful moment?
```
