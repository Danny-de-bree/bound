# BOUND — TODO v0.3

## Objective

BOUND v0.3 should remove the need for users to manually assign most `A / I / R / C` scores.

The core problem in v0.2 is:

```text
BOUND can evaluate scores,
but the user still has to decide what should be measured
and often provide the scores manually.
```

v0.3 introduces automatic evaluation contracts.

The intended architecture becomes:

```text
Natural-language goal + plan
          │
          ▼
   ContractGenerator
          │
          ▼
      BoundPlan
          │
          ▼
     StepContract
          │
          ▼
    Agent execution
          │
          ▼
   EvidenceCollector
          │
          ▼
   ContractEvaluator
          │
          ▼
      A / I / R / C
          │
          ▼
        BOUND
          │
          ▼
ACCEPT / RETRY / REPLAN / ROLLBACK
```

The key principle is:

> Use an LLM to translate intent into an explicit evaluation contract, not to make the final BOUND decision.

The final policy remains deterministic.

---

# Phase 0 — Repository cleanup

Complete this before implementing v0.3.

## Remove `/policies`

The entire repository directory:

```text
/policies
```

must be removed.

Requirements:

* [ ] Remove `/policies` from the current working tree.
* [ ] Remove `/policies` from all branches/tags that are part of the published repository history.
* [ ] Remove all historical blobs belonging to `/policies` from Git history.
* [ ] Verify the directory cannot be recovered from rewritten reachable Git history.
* [ ] Verify the current application does not import or depend on anything from `/policies`.

Do not merely:

```bash
git rm -r policies
```

The requirement is removal from Git history.

Use an appropriate history-rewriting tool such as:

```text
git filter-repo
```

Do not use deprecated `git filter-branch` unless no safer supported option exists.

Before rewriting history:

* [ ] Confirm the repository.
* [ ] Confirm the remote.
* [ ] Create a local backup or safety reference outside the rewritten refs.
* [ ] Record the current HEAD SHA.
* [ ] Confirm `/policies` is the exact path being removed.

After rewriting:

* [ ] Verify `/policies` is absent from the working tree.
* [ ] Verify no reachable commit contains `/policies`.
* [ ] Verify repository tests still pass.
* [ ] Force-push rewritten branches and tags only after verification.

Document that collaborators with existing clones must re-clone or carefully reset to the rewritten history.

---

## Remove `todo.md` and `roadmap.md` from Git

The files:

```text
todo.md
roadmap.md
```

are internal development documents.

They must no longer be tracked or published.

Requirements:

* [ ] Preserve local copies before rewriting history.
* [ ] Remove both files from the current Git index.
* [ ] Keep both files locally.
* [ ] Add them to `.gitignore`.
* [ ] Remove both files from all rewritten reachable Git history.
* [ ] Restore the local untracked copies after history rewriting.

Add to `.gitignore`:

```gitignore
# Internal development documents
todo.md
roadmap.md
```

If case variants have existed historically, inspect and remove those exact tracked paths as well.

Do not accidentally delete the local working copies permanently.

---

## History-cleanup verification

After the rewrite, verify:

```text
/policies      -> absent from current tree
/policies      -> absent from reachable rewritten history

todo.md        -> exists locally
todo.md        -> ignored
todo.md        -> untracked
todo.md        -> absent from reachable rewritten history

roadmap.md     -> exists locally
roadmap.md     -> ignored
roadmap.md     -> untracked
roadmap.md     -> absent from reachable rewritten history
```

Also verify:

```bash
git status --ignored
uv run ruff check .
uv run pytest -q
```

Do not begin v0.3 implementation until repository cleanup is complete.

---

# Phase 1 — Evaluation contract domain models

## Goal

Represent what success means before an agent executes a step.

Create models for explicit, machine-readable evaluation contracts.

Suggested module:

```text
src/bound/contracts.py
```

---

## AcceptanceCheck

Implement a model representing one expected outcome.

Example:

```python
class AcceptanceCheck(BaseModel):
    id: str
    description: str
    required: bool = True
```

Examples:

```text
valid_input_accepted
invalid_input_rejected
existing_tests_pass
lint_passes
```

Do not store executable arbitrary Python code inside contracts.

---

## RiskCheck

Implement:

```python
class RiskCheck(BaseModel):
    id: str
    description: str
    severity: float = Field(ge=0.0, le=1.0)
```

Example:

```text
No plaintext secrets are introduced.
```

---

## StepBudget

Implement explicit execution budgets.

Suggested fields:

```python
class StepBudget(BaseModel):
    max_retries: int | None = Field(default=None, ge=0)
    max_tool_calls: int | None = Field(default=None, ge=0)
    max_tokens: int | None = Field(default=None, ge=0)
    max_runtime_seconds: float | None = Field(default=None, ge=0.0)
```

All fields are optional.

Absence means:

```text
no explicit budget was defined
```

not:

```text
zero budget
```

---

## StepContract

Implement:

```python
class StepContract(BaseModel):
    id: str
    description: str
    goal: str

    acceptance_checks: list[AcceptanceCheck]

    risk_checks: list[RiskCheck] = []
    expected_artifacts: list[str] = []

    budget: StepBudget | None = None
```

Require at least one acceptance check.

A contract without any definition of success is invalid.

---

## BoundPlan

Implement:

```python
class BoundPlan(BaseModel):
    goal: str
    steps: list[StepContract]
```

Require at least one step.

---

# Phase 2 — Contract generator abstraction

## Goal

Separate contract generation from BOUND itself.

Define:

```python
class ContractGenerator(Protocol):
    def generate(
        self,
        *,
        goal: str,
        plan: str,
        context: str | None = None,
    ) -> BoundPlan:
        ...
```

BOUND core must not depend on a specific LLM provider.

Do not add Anthropic/OpenAI/DeepSeek-specific code to core modules.

Possible future implementations:

```text
OpenAIContractGenerator
AnthropicContractGenerator
DeepSeekContractGenerator
RuleBasedContractGenerator
```

The core only knows:

```text
ContractGenerator
```

---

# Phase 3 — Manual/static contract generator

## Goal

Make the entire contract pipeline testable without an LLM.

Implement:

```python
class StaticContractGenerator:
    def __init__(self, plan: BoundPlan):
        self.plan = plan

    def generate(...) -> BoundPlan:
        return self.plan
```

This implementation must be used throughout unit tests.

No v0.3 unit test may require:

```text
network access
API keys
an LLM provider
```

---

# Phase 4 — Optional LLM contract generation package boundary

## Goal

Prepare for automatic natural-language plan compilation without coupling BOUND to a provider.

The conceptual operation is:

```text
natural-language goal
+
natural-language plan
+
optional context
        │
        ▼
ContractGenerator
        │
        ▼
validated BoundPlan
```

The generated result must pass Pydantic validation before BOUND can use it.

The LLM must generate structured data only.

It must not return BOUND decisions.

It must not assign final `A / I / R / C` scores.

Its job is to define:

```text
what success looks like
what evidence should be collected
what risks matter
what artifacts are expected
what execution budgets apply
```

If provider integration is implemented during v0.3, keep it optional and outside the deterministic core.

Prefer an optional dependency group or separate adapter module/package.

Do not make an LLM SDK a mandatory installation dependency.

---

# Phase 5 — Evidence models

## Goal

Represent observations collected after an agent executes a step.

Suggested module:

```text
src/bound/evidence.py
```

Implement:

```python
class CheckEvidence(BaseModel):
    check_id: str
    passed: bool
    source: str
    details: str | None = None
```

Implement execution evidence:

```python
class ExecutionEvidence(BaseModel):
    acceptance: list[CheckEvidence] = []
    risks: list[CheckEvidence] = []

    produced_artifacts: list[str] = []
    unexpected_artifacts: list[str] = []

    retry_count: int = 0
    tool_call_count: int = 0
    token_usage: int | None = None
    runtime_seconds: float | None = None

    rollback_available: bool | None = None
```

All numeric values must use Pydantic range validation.

---

# Phase 6 — Evidence collector abstraction

## Goal

Allow different agent environments to collect evidence.

Define:

```python
class EvidenceCollector(Protocol):
    def collect(
        self,
        *,
        contract: StepContract,
        execution: object,
    ) -> ExecutionEvidence:
        ...
```

BOUND must not depend on:

```text
Cline
Claude Code
Codex
Cursor
GitHub Actions
pytest
```

Collectors may later integrate with those systems.

---

# Phase 7 — Deterministic contract evaluator

## Goal

Convert:

```text
StepContract
+
ExecutionEvidence
```

into:

```text
EvaluationScores
```

without an LLM.

Implement:

```python
class ContractEvaluator:
    def evaluate(
        self,
        contract: StepContract,
        evidence: ExecutionEvidence,
    ) -> EvaluationScores:
        ...
```

---

## Acceptance

Calculate acceptance primarily from explicit contract checks.

For example:

```text
A =
passed required acceptance checks
/
total required acceptance checks
```

Optional checks may contribute separately if desired.

Keep the exact formula visible and tested.

Never silently treat missing required evidence as passing.

---

## Cost

Calculate cost from the contract budget.

Example:

```text
retry_cost =
actual retries / max retries

tool_cost =
actual tool calls / max tool calls

token_cost =
actual tokens / max tokens

runtime_cost =
actual runtime / max runtime
```

Cap individual normalized values at:

```text
1.0
```

Calculate `C` as the mean of available budget dimensions.

If no budget exists:

```text
C = 0.0
```

and provenance must explain that no cost budget was defined.

---

## Risk

Derive risk from explicit failed risk checks and observable safety signals.

A failed risk check contributes according to its configured severity.

Also consider:

```text
unexpected artifacts
rollback unavailable
```

Keep the formula deterministic and documented.

---

## Influence

Do not invent downstream influence.

Default:

```text
I = 0.0
```

unless explicit downstream evidence exists.

Semantic influence evaluation may be added later.

---

# Phase 8 — Evidence provenance

## Goal

Every automatically derived score must be explainable.

For each dimension record:

```text
input evidence
normalization
contribution
final score
```

Example:

```text
Acceptance

✓ invalid_input_rejected
✓ valid_input_accepted
✓ existing_tests_pass
✗ lint_passes

A = 3 / 4
A = 0.75
```

Example:

```text
Cost

tool calls:
12 / 20 = 0.60

retries:
1 / 3 = 0.33

C = mean(0.60, 0.33)
C = 0.465
```

A user must be able to understand why BOUND produced every score.

---

# Phase 9 — Automatic plan workflow

## Goal

Provide a high-level orchestration API.

Suggested interface:

```python
workflow = BoundWorkflow(
    contract_generator=generator,
    evaluator=evaluator,
    policy=policy,
)
```

Then:

```python
bound_plan = workflow.prepare(
    goal=user_goal,
    plan=agent_plan,
    context=context,
)
```

The output is a validated:

```text
BoundPlan
```

Execution remains controlled by the consuming agent.

BOUND should not become an agent framework.

---

# Phase 10 — Step evaluation workflow

Provide a high-level operation such as:

```python
result = workflow.evaluate_step(
    contract=step_contract,
    evidence=execution_evidence,
)
```

Internally:

```text
StepContract
      +
ExecutionEvidence
      │
      ▼
ContractEvaluator
      │
      ▼
A / I / R / C
      │
      ▼
BoundPolicy
      │
      ▼
EvaluationResult
```

The final decision remains deterministic.

---

# Phase 11 — Multi-step workflow example

Add:

```text
examples/automatic_plan_workflow.py
```

The example must demonstrate an entire multi-step plan.

Example goal:

```text
Add safe input validation to the user registration endpoint.
```

Example generated or static plan:

```text
Step 1
Implement validation.

Step 2
Add validation tests.

Step 3
Run required verification.

Step 4
Optional additional refactoring.
```

Each step must have its own `StepContract`.

Simulate execution evidence.

The example should demonstrate at least:

```text
REPLAN
RETRY
ACCEPT
```

and show that after:

```text
ACCEPT
```

the current optimization loop stops.

---

# Phase 12 — Plan-to-contract example

Add:

```text
examples/plan_to_contract.py
```

Input:

```text
Goal:
Add JWT authentication.

Plan:
1. Add token creation.
2. Add authentication middleware.
3. Protect private endpoints.
4. Add tests.
```

Output:

```text
BoundPlan
```

with explicit contracts such as:

```text
Step 1

Acceptance checks:
- valid credentials produce a token
- generated token can be verified

Risk checks:
- no plaintext secret is committed
- token expiry is configured

Expected artifacts:
- authentication implementation
- authentication tests

Budget:
- max tool calls: 20
- max retries: 3
```

This example should work with `StaticContractGenerator`.

If an optional LLM adapter exists, document how the same interface can generate the contract automatically.

---

# Phase 13 — README workflow documentation

Add the full architecture:

```text
User goal
    │
    ▼
Agent plan
    │
    ▼
ContractGenerator
    │
    ▼
BoundPlan
    │
    ▼
StepContract
    │
    ▼
Agent executes step
    │
    ▼
EvidenceCollector
    │
    ▼
ExecutionEvidence
    │
    ▼
ContractEvaluator
    │
    ▼
A / I / R / C
    │
    ▼
BOUND policy
    │
    ▼
Decision
```

Explain clearly:

```text
The LLM may define what should be measured.

The environment provides evidence.

Deterministic code calculates the scores.

BOUND makes the final decision.
```

---

# Phase 14 — Automatic contract generation experiment

## Goal

Test whether automatically generated contracts are actually useful.

Create a small benchmark of at least 10 plans.

For each plan evaluate:

```text
Are the acceptance checks measurable?
Are they relevant to the goal?
Are required checks missing?
Are unnecessary checks introduced?
Are risk checks meaningful?
Can deterministic evidence evaluate the contract?
```

Do not evaluate only whether the JSON is valid.

The important question is:

```text
Did the generated contract define useful success criteria?
```

---

# Phase 15 — Contract quality model

Add a validation report:

```python
class ContractQualityReport(BaseModel):
    measurable_ratio: float
    acceptance_check_count: int
    risk_check_count: int
    has_budget: bool
    warnings: list[str]
```

Detect obvious problems such as:

```text
no acceptance checks
too many vague checks
duplicate checks
no observable verification method
extremely large contract
```

Do not use an LLM for basic structural validation.

---

# Phase 16 — Tests

By the end of v0.3, test at minimum:

## Contracts

* valid plan
* empty plan rejected
* step without acceptance checks rejected
* invalid risk severity rejected
* invalid budgets rejected

## Evidence

* valid evidence
* missing evidence
* unknown check IDs
* duplicate evidence
* failed required checks

## Contract evaluation

* all checks pass
* partial acceptance
* no cost budget
* budget exceeded
* failed high-severity risk check
* rollback unavailable
* deterministic repeatability

## Workflow

* plan preparation
* multi-step evaluation
* first ACCEPT stops current optimization loop
* RETRY keeps same step
* REPLAN requires new strategy
* ROLLBACK overrides acceptance

## Architecture

Verify:

```text
no mandatory LLM dependency
no network required
no API key required
final decision remains deterministic
```

---

# Definition of Done

BOUND v0.3 is complete when:

```bash
uv run ruff check .
uv run pytest -q
```

pass and the following workflow is possible:

```text
natural-language plan
        │
        ▼
evaluation contracts
        │
        ▼
execution evidence
        │
        ▼
automatic A / I / R / C
        │
        ▼
deterministic BOUND decision
```

A user should no longer need to manually provide all four scores for a contract-based workflow.

The package must still work entirely without an LLM.

LLM-based contract generation is an optional convenience layer, not a requirement.

---

# Out of scope for v0.3

Do not implement:

* LLM-based final BOUND decisions
* mandatory model provider dependencies
* deep Cline integration
* Cursor integration
* persistent mission memory
* learned thresholds
* reinforcement learning
* opaque learned scoring functions

The central v0.3 question is:

> Can BOUND automatically turn an explicit plan into measurable execution contracts and use real evidence to decide when an agent should continue, retry, replan, or roll back?
