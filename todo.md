\# BOUND — v0.3 Addendum: Agent Integration

## Objective

Extend v0.3 so BOUND can be installed into an existing agent workflow without any LLM-based evaluator.

The goal is:

```text
Agent plans
    ↓
Agent executes
    ↓
Observable evidence is collected
    ↓
BOUND evaluates deterministically
    ↓
BOUND controls what happens next
```

v0.3 must be usable inside real agents before semantic LLM evaluation is introduced in v0.4.

---

# Core agent workflow

The intended control loop is:

```text
User goal
    ↓
Agent creates or receives a plan
    ↓
Current plan step
    ↓
StepContract
    ↓
Agent executes the step
    ↓
ExecutionEvidence
    ↓
ContractEvaluator
    ↓
A / I / R / C
    ↓
BOUND policy
    ↓
┌──────────┬──────────┬──────────┬──────────┐
│ ACCEPT   │ RETRY    │ REPLAN   │ ROLLBACK │
└──────────┴──────────┴──────────┴──────────┘
```

Decision behavior:

```text
ACCEPT
The current step is sufficiently complete.
Stop optimizing it and continue to the next plan step.

RETRY
The current approach is close enough to acceptable.
Make one focused retry using the same general strategy.

REPLAN
The current approach is not progressing sufficiently.
Choose a materially different strategy.

ROLLBACK
The current action exceeds the configured risk boundary.
Revert or avoid the risky change before continuing.
```

BOUND does not generate the next action.

The agent remains responsible for:

```text
planning
tool use
code changes
reasoning
execution
```

BOUND is responsible for:

```text
evaluating the result
deciding whether the current step should continue
```

---

# Phase A — Framework-neutral agent integration interface

Add a small framework-neutral integration API.

Suggested module:

```text
src/bound/integration.py
```

Provide a high-level result object such as:

```python
class AgentControlResult(BaseModel):
    evaluation: EvaluationResult
    next_action: Literal[
        "continue",
        "retry",
        "replan",
        "rollback",
    ]
    feedback: str
```

Provide a helper:

```python
def evaluate_agent_step(
    *,
    contract: StepContract,
    evidence: ExecutionEvidence,
    evaluator: ContractEvaluator,
    policy: BoundPolicy,
) -> AgentControlResult:
    ...
```

The mapping must remain deterministic:

```text
ACCEPT   -> continue
RETRY    -> retry
REPLAN   -> replan
ROLLBACK -> rollback
```

Do not introduce agent-framework types into the core package.

---

# Phase B — Integration specification

Add a machine-readable integration specification.

Expose it through:

```bash
bound integration-spec
```

The output should explain:

```text
when BOUND should be called
what data should be provided
what evidence should be collected
what every decision means
what evidence must never be fabricated
```

Example concepts:

```text
Call BOUND:
- after a meaningful plan step
- after a retry
- after verification checks
- before deciding to continue optimizing the same objective

Do not call BOUND:
- after every token
- after every trivial file read
- after every low-level tool call
```

---

# Phase C — Generic self-install prompt

Add:

```text
integrations/generic/INSTALL_BOUND.md
```

This file is a copy-paste prompt that can be given to an agent.

The prompt must instruct the agent to:

1. Install `bound-policy`.
2. Inspect the real installed BOUND API.
3. Inspect its own execution loop.
4. Identify meaningful step boundaries.
5. Identify available observable evidence.
6. Create or map steps to `StepContract`.
7. Build `ExecutionEvidence`.
8. Call BOUND after meaningful execution steps.
9. Use the BOUND decision to control the next action.
10. Never fabricate missing evidence.
11. Do not add an LLM evaluator.
12. Keep the integration thin and removable.
13. Run one end-to-end test.

Before changing anything, the agent must report:

```text
integration point
available evidence
unavailable evidence
control-flow mapping
files/configuration that will be changed
```

---

# Phase D — Cline self-install prompt

Add:

```text
integrations/cline/INSTALL_BOUND.md
```

This is the first priority framework integration.

The prompt should instruct Cline to inspect its own available workflow mechanisms and determine the cleanest place to integrate BOUND.

Focus on these boundaries:

```text
task/subtask completed
code changed
tests executed
verification finished
before further refinement
```

Potential evidence sources:

```text
test results
lint results
type-check results
files changed
unexpected files changed
retry count
tool calls
execution time
rollback availability
```

The integration must map:

```text
ACCEPT
-> mark the current objective sufficiently complete
-> move to the next plan step

RETRY
-> keep the current step
-> use BOUND feedback for one focused correction

REPLAN
-> stop iterating on the current approach
-> update the plan or strategy

ROLLBACK
-> revert or avoid the risky change where possible
-> replan
```

Do not duplicate BOUND policy logic inside Cline instructions.

---

# Phase E — Claude Code self-install prompt

Add:

```text
integrations/claude-code/INSTALL_BOUND.md
```

The agent must first inspect the actual available Claude Code mechanisms.

Do not assume undocumented hooks.

The intended integration point is:

```text
after meaningful execution
before deciding whether to continue refining the same task
```

---

# Phase F — Kilo Code self-install prompt

Add:

```text
integrations/kilo-code/INSTALL_BOUND.md
```

Follow the same framework-neutral principles.

Keep the adapter thin.

---

# Phase G — Hermes Agent self-install prompt

Add:

```text
integrations/hermes-agent/INSTALL_BOUND.md
```

Focus especially on:

```text
persistent goals
multi-step plans
retries across long workflows
tool usage
task completion
```

BOUND should evaluate meaningful task boundaries, not every internal operation.

---

# Phase H — Runnable agent-loop example

Add:

```text
examples/agent_control_loop.py
```

Demonstrate:

```text
Step 1 -> REPLAN
Step 2 -> RETRY
Step 3 -> ACCEPT
```

The example should use the real:

```text
StepContract
ExecutionEvidence
ContractEvaluator
BoundPolicy
```

Do not manually supply final decisions.

Pseudo-flow:

```python
for step in plan:
    while True:
        execution = execute(step)
        evidence = collect_evidence(execution)

        result = bound.evaluate_step(
            contract=step.contract,
            evidence=evidence,
        )

        if result.decision == "ACCEPT":
            break

        if result.decision == "RETRY":
            retry()

        if result.decision == "REPLAN":
            step = replan()

        if result.decision == "ROLLBACK":
            rollback()
            step = replan()
```

---

# Phase I — Cline test scenario

Add a documented manual test scenario specifically for Cline.

Example task:

```text
Add input validation to a small API endpoint.
```

Expected workflow:

```text
1. Cline creates or reads the plan.
2. BOUND contract defines success.
3. Cline implements the first attempt.
4. Tests fail.
5. BOUND returns RETRY or REPLAN.
6. Cline performs the next action.
7. Tests and required checks pass.
8. BOUND returns ACCEPT.
9. Cline stops refining that step.
```

Measure:

```text
number of agent steps
number of tool calls
number of retries
step where ACCEPT occurred
whether Cline attempted unnecessary work after acceptance
```

Store the test instructions under:

```text
examples/cline_manual_test.md
```

or:

```text
docs/cline-testing.md
```

---

# Phase J — Agent feedback output

Ensure BOUND can return concise feedback suitable for reinjection into an agent context.

Example:

```text
Decision: RETRY

The current step is close to acceptable.

2 of 3 required checks pass.
The remaining failing check is the test suite.

Stay with the current approach and make one focused correction.
```

The feedback must:

```text
explain the decision
reference available evidence
suggest the correct class of next action
avoid prescribing unnecessary implementation details
```

Keep it deterministic.

---

# Phase K — Integration tests

Add tests for:

```text
ACCEPT -> continue
RETRY -> retry
REPLAN -> replan
ROLLBACK -> rollback
```

Verify:

```text
the integration helper does not modify BOUND decisions
framework-neutral types only
no network required
no LLM dependency required
```

---

# v0.3 Definition of Done — updated

v0.3 is complete when:

```text
✓ plan-to-contract works

✓ execution evidence can be evaluated deterministically

✓ A / I / R / C are derived without an LLM

✓ BOUND produces deterministic control decisions

✓ a framework-neutral agent-control API exists

✓ a generic self-install prompt exists

✓ a Cline-specific self-install prompt exists

✓ at least one additional agent integration prompt exists

✓ a runnable multi-step agent-loop example exists

✓ a documented Cline manual test exists

✓ the complete workflow works without an API key

✓ no LLM call is required anywhere in the v0.3 integration path
```

The central v0.3 demo should be:

```text
Paste the BOUND integration prompt into Cline
        ↓
Cline installs BOUND
        ↓
Cline wires BOUND into meaningful task boundaries
        ↓
Cline executes a coding task
        ↓
BOUND returns RETRY / REPLAN / ACCEPT
        ↓
Cline changes its control flow
```

The central v0.3 question becomes:

> Can BOUND deterministically control a real agent workflow using observable execution evidence, without requiring an LLM judge?
