# BOUND — Agent TODO v3

## Core objective

Build BOUND first as a standalone, agent-agnostic and model-agnostic Python package.

BOUND is a bounded utility policy.

It does not try to find the globally optimal action.

It evaluates whether a proposed action is sufficiently good to continue toward the larger goal.

The core mathematical rule is:

```text
S = (W × A) + I - R - C
```

Where:

| Variable | Meaning                     |
| -------- | --------------------------- |
| `S`      | Final bounded utility score |
| `W`      | Goal weight                 |
| `A`      | Acceptance score            |
| `I`      | Downstream influence        |
| `R`      | Risk penalty                |
| `C`      | Resource penalty            |

The success condition is:

```text
S >= T
```

Where:

```text
T = acceptance threshold
```

The objective is not:

```text
maximize S indefinitely
```

The objective is:

```text
cross the acceptance threshold
and continue making progress toward the final goal
```

This mathematical model is the core of BOUND.

Keep it independent from:

* LLM providers
* agent frameworks
* Cline
* Claude Code
* OpenAI
* Anthropic
* DeepSeek
* MCP
* IDE integrations

LLM-as-judge support will be added later as one possible source of evaluation scores.

---

# Development rules

Work strictly phase by phase.

Do not begin a new phase until:

```bash
uv run pytest
uv run ruff check .
```

both pass.

Use:

* Python 3.12+
* `uv`
* Pydantic v2
* pytest
* Ruff
* full type annotations

Do not:

* add an LLM SDK
* add a provider-specific dependency
* call external APIs
* hide policy logic inside prompts
* allow an evaluator to directly choose the final policy decision

The BOUND core must remain deterministic once evaluation scores are provided.

---

# Target architecture

```text
Input
  │
  ▼
Action
  │
  ▼
Evaluator
  │
  ▼
EvaluationScores
  │
  ▼
BoundCalculator
  │
  │  S = (W × A) + I - R - C
  ▼
BoundPolicy
  │
  ▼
EvaluationResult
  │
  ├── JSON
  │
  └── Steering prompt
```

The evaluator is replaceable.

The mathematical calculation is not.

---

# Phase 0 — Project setup

## Tasks

* [ ] Initialize the repository as a `uv` Python project.
* [ ] Use a `src/` package layout.
* [ ] Add Pydantic:

```bash
uv add pydantic
```

* [ ] Add development dependencies:

```bash
uv add --dev pytest ruff
```

* [ ] Do not add Anthropic, OpenAI, or any other LLM SDK.
* [ ] Add the CLI entrypoint:

```toml
[project.scripts]
bound = "bound.cli:main"
```

Target structure:

```text
bound/
├── pyproject.toml
├── README.md
├── src/
│   └── bound/
│       ├── __init__.py
│       ├── models.py
│       ├── calculator.py
│       ├── evaluator.py
│       ├── policy.py
│       ├── prompt.py
│       └── cli.py
├── tests/
│   ├── test_models.py
│   ├── test_calculator.py
│   ├── test_policy.py
│   ├── test_prompt.py
│   └── test_cli.py
└── examples/
    └── flight_booking.py
```

## Phase gate

The following must succeed:

```bash
uv sync
uv run pytest
uv run ruff check .
uv run bound --help
```

---

# Phase 1 — Pydantic domain models

Create:

```text
src/bound/models.py
```

## Action

```python
class Action(BaseModel):
    description: str
    goal: str
    context: str | None = None
```

Requirements:

* `description` must not be empty.
* `description` must not be whitespace only.
* `goal` must not be empty.
* `goal` must not be whitespace only.

---

## BoundCriteria

Use a name that reflects the mathematical model clearly.

```python
class BoundCriteria(BaseModel):
    threshold: float = Field(ge=0.0)
    weight: float = Field(default=1.0, ge=0.0)
```

Important:

Do not artificially restrict `threshold` to `[0, 1]`.

The final score:

```text
S = (W × A) + I - R - C
```

is not restricted to `[0, 1]`.

Therefore the threshold should not be assumed to be limited to `1.0`.

For example:

```text
W = 2.0
A = 1.0
I = 1.0
R = 0.0
C = 0.0

S = 3.0
```

A valid threshold could therefore be:

```text
T = 2.0
```

---

## EvaluationScores

```python
class EvaluationScores(BaseModel):
    acceptance: float = Field(ge=0.0, le=1.0)
    influence: float = Field(ge=-1.0, le=1.0)
    risk: float = Field(ge=0.0, le=1.0)
    cost: float = Field(ge=0.0, le=1.0)
    reasoning: str | None = None
```

Definitions:

### Acceptance — `A`

```text
A ∈ [0, 1]
```

Measures:

```text
How well does this proposed action satisfy or advance the current goal?
```

Examples:

```text
0.0 = does not help satisfy the goal
0.5 = partially satisfies the goal
1.0 = fully satisfies the goal
```

---

### Influence — `I`

```text
I ∈ [-1, 1]
```

Measures:

```text
How does this action affect the probability of success of downstream goals?
```

Examples:

```text
-1.0 = strongly damages future progress
 0.0 = neutral downstream effect
 1.0 = strongly improves future progress
```

Influence is not a penalty.

It may either increase or decrease the final score.

---

### Risk — `R`

```text
R ∈ [0, 1]
```

Measures:

```text
What is the potential downside of taking this action?
```

Higher risk lowers the final utility score.

---

### Resource cost — `C`

```text
C ∈ [0, 1]
```

Measures normalized resource consumption.

Examples include:

* time
* tokens
* tool calls
* compute
* money
* operational complexity

Higher cost lowers the final utility score.

---

## Decision

```python
Decision = Literal[
    "ACCEPT",
    "RETRY",
    "REPLAN",
    "ROLLBACK",
]
```

---

## EvaluationResult

```python
class EvaluationResult(BaseModel):
    scores: EvaluationScores

    weight: float
    threshold: float

    acceptance_component: float
    influence_component: float
    risk_component: float
    cost_component: float

    score: float
    decision: Decision
```

The result should contain the individual score components.

This is important because BOUND should make its calculation visible and inspectable.

For:

```text
S = (W × A) + I - R - C
```

store:

```text
acceptance_component = W × A
influence_component = I
risk_component = R
cost_component = C
```

The final result can therefore clearly explain how `S` was calculated.

---

# Phase 2 — Pure mathematical calculator

Create:

```text
src/bound/calculator.py
```

This module contains the mathematical core of BOUND.

It must have no dependency on:

* CLI code
* LLMs
* providers
* prompts
* external APIs

---

## BoundCalculation

Implement a pure function:

```python
def calculate_bound_score(
    scores: EvaluationScores,
    criteria: BoundCriteria,
) -> float:
    ...
```

The implementation must be exactly equivalent to:

```python
return (
    criteria.weight * scores.acceptance
    + scores.influence
    - scores.risk
    - scores.cost
)
```

Do not:

* clamp the score
* normalize the score
* round the score
* apply a sigmoid
* rescale it to `[0, 1]`

The mathematical score must remain raw.

---

## Component calculation

Also expose a structured calculation.

For example:

```python
class ScoreComponents(BaseModel):
    weighted_acceptance: float
    influence: float
    risk: float
    cost: float
    total: float
```

And:

```python
def calculate_components(
    scores: EvaluationScores,
    criteria: BoundCriteria,
) -> ScoreComponents:
    ...
```

Required formulas:

```text
weighted_acceptance = W × A

total =
    weighted_acceptance
    + I
    - R
    - C
```

This makes the mathematical evaluation fully inspectable.

---

## Required calculator tests

### Basic formula

Given:

```text
W = 1.0
A = 0.8
I = 0.2
R = 0.1
C = 0.1
```

Expected:

```text
S = (1.0 × 0.8) + 0.2 - 0.1 - 0.1
S = 0.8
```

---

### Positive downstream influence

```text
W = 1.0
A = 0.5
I = 0.5
R = 0.0
C = 0.0
```

Expected:

```text
S = 1.0
```

---

### Negative downstream influence

```text
W = 1.0
A = 0.8
I = -0.5
R = 0.1
C = 0.1
```

Expected:

```text
S = 0.1
```

---

### Weight greater than one

```text
W = 2.0
A = 0.8
I = 0.0
R = 0.0
C = 0.0
```

Expected:

```text
S = 1.6
```

This test explicitly proves that the score is not restricted to `[0, 1]`.

---

### Negative final score

```text
W = 1.0
A = 0.1
I = -0.5
R = 0.8
C = 0.7
```

Expected:

```text
S = -1.9
```

This test explicitly proves that negative scores are valid.

---

# Phase 3 — Evaluator abstraction

Create:

```text
src/bound/evaluator.py
```

BOUND must not care how the four scores are produced.

Define:

```python
class Evaluator(Protocol):
    def evaluate(self, action: Action) -> EvaluationScores:
        ...
```

Do not implement an LLM evaluator yet.

Provide a simple implementation useful for:

* tests
* examples
* manual integrations

For example:

```python
class StaticEvaluator:
    def __init__(self, scores: EvaluationScores):
        self.scores = scores

    def evaluate(self, action: Action) -> EvaluationScores:
        return self.scores
```

This allows:

```python
policy = BoundPolicy(
    evaluator=StaticEvaluator(
        EvaluationScores(
            acceptance=0.8,
            influence=0.2,
            risk=0.1,
            cost=0.1,
        )
    )
)
```

Later implementations may include:

```text
LLMEvaluator
RuleBasedEvaluator
HumanEvaluator
RewardModelEvaluator
EnvironmentEvaluator
CompositeEvaluator
```

None belong in v0.1.

The interface must already support them without changing the policy layer.

---

# Phase 4 — BOUND policy

Create:

```text
src/bound/policy.py
```

Implement:

```python
class BoundPolicy:
    def __init__(self, evaluator: Evaluator):
        self.evaluator = evaluator

    def evaluate(
        self,
        action: Action,
        criteria: BoundCriteria,
    ) -> EvaluationResult:
        ...
```

Execution order:

```text
1. Receive Action
2. Ask Evaluator for EvaluationScores
3. Calculate S using the BOUND formula
4. Compare S against T
5. Determine the policy decision
6. Return EvaluationResult
```

---

# Primary success rule

The core BOUND decision is:

```text
if S >= T:
    ACCEPT
```

This rule is fundamental.

The objective is not to maximize `S`.

Once:

```text
S >= T
```

the action is sufficiently acceptable.

The system should continue.

---

# Below-threshold decision rule

When:

```text
S < T
```

use the negative components to determine the next action.

For v0.1:

```python
if score >= criteria.threshold:
    decision = "ACCEPT"
elif scores.risk > scores.cost:
    decision = "ROLLBACK"
elif scores.cost > scores.risk:
    decision = "RETRY"
else:
    decision = "REPLAN"
```

Interpretation:

### ACCEPT

```text
The bounded utility threshold has been reached.
Stop optimizing this action and continue.
```

### RETRY

```text
The action is below threshold primarily because resource cost is too high.

Stay within the same action space but attempt a cheaper or more efficient execution.
```

### ROLLBACK

```text
The action is below threshold primarily because its risk is too high.

Avoid or reverse the risky action when possible before continuing.
```

### REPLAN

```text
The action is below threshold but neither risk nor cost clearly dominates.

Choose a different approach.
```

Important:

The evaluator produces scores.

The BOUND policy produces the final decision.

Never allow an evaluator to return:

```text
ACCEPT
RETRY
REPLAN
ROLLBACK
```

directly.

---

# Policy tests

Test the acceptance boundary explicitly.

Given:

```text
S = 0.6
T = 0.6
```

Expected:

```text
ACCEPT
```

Because:

```text
S >= T
```

not:

```text
S > T
```

Also test:

```text
S = 0.599999
T = 0.6
```

Expected:

```text
not ACCEPT
```

---

# Phase 5 — Steering prompt

Create:

```text
src/bound/prompt.py
```

Generate deterministic plain text from `EvaluationResult`.

Do not use an LLM.

Example:

```text
[BOUND evaluation]

Decision: REPLAN

Bounded utility:
S = (W × A) + I - R - C
S = (1.00 × 0.70) + 0.10 - 0.30 - 0.20
S = 0.30

Acceptance threshold:
T = 0.60

The proposed action does not yet meet the required acceptance threshold.

Assessment:
The current approach advances the goal but introduces too much uncertainty.

Suggested next step:
Choose an alternative approach that improves goal satisfaction or downstream impact while reducing risk or resource cost.
```

For `ACCEPT`, make the core principle explicit:

```text
The proposed action meets the required acceptance threshold.

Further optimization is not required.

Proceed with the action and continue toward the larger goal.
```

The prompt should make the bounded optimization philosophy visible.

Keep it under 150 words.

---

# Phase 6 — CLI

Required command:

```bash
uv run bound evaluate \
  --action "Book the direct flight" \
  --goal "Travel from Paris to New York" \
  --acceptance 0.9 \
  --influence 0.2 \
  --risk 0.1 \
  --cost 0.2 \
  --weight 1.0 \
  --threshold 0.6
```

For v0.1, accept the evaluation scores directly through the CLI.

This is intentional.

Do not call an LLM.

Required inputs:

```text
--action
--goal
--context
--acceptance
--influence
--risk
--cost
--weight
--threshold
```

This allows BOUND to be used today by any system that can produce the four evaluation dimensions.

Example integration:

```text
agent
  -> calculates or obtains A/I/R/C
  -> calls BOUND
  -> receives deterministic policy result
```

Later:

```text
agent
  -> LLMEvaluator
  -> A/I/R/C
  -> BOUND
```

The core does not change.

---

# CLI JSON output

Include the mathematical components explicitly.

Example:

```json
{
  "scores": {
    "acceptance": 0.9,
    "influence": 0.2,
    "risk": 0.1,
    "cost": 0.2
  },
  "weight": 1.0,
  "threshold": 0.6,
  "acceptance_component": 0.9,
  "influence_component": 0.2,
  "risk_component": 0.1,
  "cost_component": 0.2,
  "score": 0.8,
  "decision": "ACCEPT"
}
```

The result must be auditable.

A consumer should be able to reconstruct:

```text
S = (1.0 × 0.9) + 0.2 - 0.1 - 0.2
S = 0.8
```

from the JSON alone.

---

# Phase 7 — Flight example

Reproduce the existing README concept without an LLM.

Example:

```python
scores = EvaluationScores(
    acceptance=0.9,
    influence=0.2,
    risk=0.1,
    cost=0.2,
)

criteria = BoundCriteria(
    weight=1.0,
    threshold=0.6,
)
```

Expected:

```text
S = (1.0 × 0.9) + 0.2 - 0.1 - 0.2
S = 0.8
```

Since:

```text
0.8 >= 0.6
```

the result is:

```text
ACCEPT
```

The important behavior is:

```text
The flight does not need to be globally optimal.

It has crossed the acceptance threshold.

Continue.
```

---

# Final test requirements

At minimum test:

## Models

* valid and invalid `Action`
* valid and invalid score ranges
* positive weight
* thresholds above `1.0`

## Mathematics

* exact score formula
* positive influence
* negative influence
* weight above `1`
* negative final score
* no score clamping
* no score rounding internally

## Threshold

* `S > T`
* `S == T`
* `S < T`

## Decisions

* ACCEPT
* RETRY
* REPLAN
* ROLLBACK

## Architecture

* policy works with `StaticEvaluator`
* no network required
* no API key required
* no LLM SDK installed

## Prompt

* deterministic
* mathematically correct
* under 150 words
* contains `S`
* contains `T`
* contains decision

## CLI

* valid JSON to stdout
* readable prompt to stderr
* all score inputs validated through Pydantic

---

# Deferred

Do not implement yet:

## LLM-as-judge

Later add something like:

```text
bound-evaluator-openai
bound-evaluator-anthropic
bound-evaluator-deepseek
```

or provider adapters behind:

```python
Evaluator
```

The BOUND core must not depend on them.

## Agent integrations

Deferred:

* Cline
* Claude Code
* Codex
* Cursor
* MCP

## Rule-based evaluator

Deferred.

## Persistent mission state

Deferred.

## Automatic score generation

Deferred.

---

# Definition of done

BOUND v0.1 is complete when:

```bash
uv sync
uv run ruff check .
uv run pytest -v
```

all pass and this works without any API key:

```bash
uv run bound evaluate \
  --action "Book the direct flight" \
  --goal "Travel from Paris to New York" \
  --acceptance 0.9 \
  --influence 0.2 \
  --risk 0.1 \
  --cost 0.2 \
  --weight 1.0 \
  --threshold 0.6
```

The package must calculate:

```text
S = (W × A) + I - R - C
```

compare:

```text
S >= T
```

and return a deterministic BOUND decision.

Nothing model-specific belongs in the v0.1 core.
