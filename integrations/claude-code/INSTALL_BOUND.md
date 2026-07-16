# Integration prompt for Claude Code — Install BOUND

> This document is **not documentation for a human.** It is a prompt designed
> to be pasted directly into **Claude Code**. Paste everything below the line
> into a Claude Code session and let it run.

---

You are Claude Code. Your job is to integrate **BOUND** — a deterministic
bounded-utility policy for agentic systems — into *this* project's workflow,
so that BOUND evaluates meaningful execution boundaries and your control flow
reacts to its decision.

This is an **integration prompt for Claude Code**, not a native Claude Code
plugin. BOUND ships no Claude-Code-specific code and no native integration.
Do not pretend a native integration exists. Do not assume any undocumented
hook, slash command, subagent contract, or lifecycle event. Before relying on
a mechanism, **inspect what is actually available in this environment** and use
only that.

## What BOUND is, and what it is not

BOUND is framework-neutral. It turns a `StepContract` + `ExecutionEvidence`
into a deterministic `EvaluationResult` with a `.decision` in
`ACCEPT / RETRY / REPLAN / ROLLBACK`.

> **BOUND decides whether to continue, retry, replan, or rollback. BOUND does
> not decide what code to write.** You (Claude Code) decide what code to
> write; BOUND decides whether the step you just took is good enough to move on.

## The conceptual integration boundary

    Claude Code executes a meaningful task / subtask
            ↓
    verification runs (tests, lint, type-check, build — whatever this project has)
            ↓
    evidence collected (only what was actually observed)
            ↓
    BOUND evaluates  (deterministic)
            ↓
    Claude Code reacts to the decision

You own the first and last boxes. BOUND owns the middle evaluation. The seam is
plain data: `StepContract` in, `ExecutionEvidence` in, `EvaluationResult` out.

## Step 0 — Install and inspect (do this before anything else)

1. Install the latest stable `bound-policy`:

   ```bash
   pip install bound-policy
   ```

2. **Inspect the installed public API; do not assume it.** Confirm against the
   *installed* package:

   ```bash
   python -c "import bound; print(bound.__version__); print(bound.__all__)"
   ```

   You should find (confirm each): `BoundWorkflow` (`prepare(...)`,
   `evaluate_step(*, contract, evidence, criteria)`), `StepContract`,
   `AcceptanceCheck`, `RiskCheck`, `StepBudget`, `BoundPlan`,
   `StaticContractGenerator`, `ExecutionEvidence`, `CheckEvidence`,
   `EvidenceCollector`, `BoundCriteria`, `EvaluationResult`, `Decision`.
## Step 1 — Inspect what Claude Code mechanisms are ACTUALLY available here

Do **not** assume undocumented hooks, slash commands, subagent contracts, or
lifecycle events. Claude Code's available mechanisms vary by version and
workspace configuration.

Inspect and record what is actually available in *this* environment:

- **Execution surface**: What can Claude Code run as a "meaningful step"?
  (A user task, an explicitly delimited phase, a checkpoint between tool
  batches, a subagent invocation.) Which granularity is right to evaluate?
- **Available mechanisms**: What does this environment actually expose? Check
  for: hooks (PreToolUse/PostToolUse/Stop/SubagentStop, etc., only if present),
  slash commands (only the ones that actually exist here), `CLAUDE.md` /
  memory instructions, subagents. Use only what you confirm exists.
- **Verification commands**: What exact commands does this project use for
  tests, lint, type-check, build? Run them and capture real exit codes/stdout.
- **Observability**: tests, lint, type checks, expected/unexpected files
  (`git diff --name-only`), failed commands, retries (your counter), tool
  calls where observable, tokens where observable, runtime where observable,
  rollback availability (clean checkpoint?).
- **Rollback**: Confirm concretely whether a clean checkpoint exists.

Record these honestly. Unobservable signals stay unobservable.

## Step 2 — Identify meaningful step boundaries

Call BOUND at **meaningful** boundaries: after a task/subtask completes and its
verification runs, after a retry, before deciding to keep refining the same
objective. Do **not** call BOUND after every token, file read, shell command,
or low-level tool call.

## Step 3 — Report the proposed integration BEFORE modifying anything

Do not change any file until you have printed (or recorded) this report with
exactly these headings:

```text
Integration point:
    Where in the Claude Code workflow BOUND is called.

Step boundary:
    The concrete Claude Code tasks/subtasks you will map to StepContracts, and
    why each granularity is meaningful.

Available evidence:
    The observable signals this environment actually produces per step
    (exact commands and what they yield). Be specific to THIS setup.

Missing evidence:
    The signals NOT observable here, and how you will represent them as
## Step 4 — Build contracts and collect evidence

Map each meaningful task to a `StepContract` (with `AcceptanceCheck`s, optional
`RiskCheck`s, `expected_artifacts`, optional `StepBudget`). Collect
`ExecutionEvidence` from only what you observed (tests, lint, type checks,
expected/unexpected files, failed commands, retries, tool calls, tokens where
observable, runtime where observable, rollback availability). Never fabricate;
leave unobservable fields unset.

```python
from bound import (
    AcceptanceCheck, BoundCriteria, BoundWorkflow, CheckEvidence,
    ExecutionEvidence, RiskCheck, StepBudget, StepContract,
)

contract = StepContract(
    id="implement-feature",
    description="Implement <step>",
    goal="<step goal>",
    acceptance_checks=[
        AcceptanceCheck(id="tests-pass", description="pytest is green"),
        AcceptanceCheck(id="lint-clean", description="ruff is clean"),
    ],
    risk_checks=[
        RiskCheck(id="no-tests-removed",
                  description="No existing tests deleted", severity=0.8),
    ],
    expected_artifacts=["src/app/feature.py"],
    budget=StepBudget(max_retries=3),
)

evidence = ExecutionEvidence(
    acceptance=[CheckEvidence(check_id="tests-pass", passed=tests_ok),
                CheckEvidence(check_id="lint-clean", passed=lint_ok)],
    risks=[CheckEvidence(check_id="no-tests-removed", passed=no_tests_removed)],
    produced_artifacts=produced,
    unexpected_artifacts=unexpected,
    retry_count=retries,
    tool_call_count=tool_calls,
    rollback_available=checkpoint_exists,
)

result = BoundWorkflow().evaluate_step(
    contract=contract, evidence=evidence,
    criteria=BoundCriteria(threshold=0.75),
)
```

## Step 5 — Evaluate and react to the decision

- **ACCEPT** → stop refining the current step; continue to the next objective.
- **RETRY** → preserve the strategy; make one focused correction; re-collect
  evidence; re-evaluate; respect `StepBudget`.
- **REPLAN** → stop iterating on the current strategy; choose a materially
  different approach; build a new `StepContract`.
- **ROLLBACK** → restore a safe state where possible (e.g. `git checkout` /
  restore a Claude Code checkpoint); then replan. BOUND does not execute the
  rollback; you do.

## Rules you must not break

- **Never assume undocumented Claude Code hooks.** Only use mechanisms you
  inspected and confirmed are available here.
- **Never fabricate evidence.** Unobservable signals stay unobservable.
- **Never duplicate BOUND's policy logic.** Call BOUND; use its result.
- **Never add an LLM evaluator / LLM-as-judge.** Deterministic decision only.
- **Do not hardcode Claude-Code-specific behavior into `src/bound/`.** BOUND
  stays framework-neutral; all wiring lives in this project's files.
- **Keep the integration thin and removable.**

## Step 6 — Add an end-to-end test

Build a real `StepContract`, collect real `ExecutionEvidence`, evaluate via
BOUND, assert the decision is one of the four valid decisions, and assert the
control-flow branch you would take. Do not hardcode "ACCEPT" unless the
evidence genuinely satisfies the contract.

## Done

Summarize: the Claude Code mechanisms you actually used (and confirmed exist),
files created/modified, `bound-policy` version installed, one real
`StepContract` + decision, and confirmation no evidence was fabricated and no
BOUND policy logic was duplicated.

Remember: **BOUND decides whether to continue, retry, replan, or rollback.
BOUND does not decide what code to write.**

    unavailable rather than fabricating them.

Control-flow mapping:
    How each BOUND decision changes what Claude Code does next (see Step 5).
    Reference the exact mapping (ACCEPT->continue, RETRY->retry,
    REPLAN->replan, ROLLBACK->rollback).

Files to modify:
    The exact list of files you intend to create or change. Keep it small;
    the integration must be thin and removable.
```

Only after this report is produced may you begin implementation.


   There may also be a higher-level helper `evaluate_agent_step(...) ->
   AgentControlResult` (carrying `.evaluation`, `.next_action`
   `continue`/`retry`/`replan`/`rollback`, deterministic `.feedback`).
   **Inspect the installed API to confirm whether it exists and its exact
   name/signature.** If it exists, prefer it. Otherwise use
   `BoundWorkflow.evaluate_step(...)` and map the decision with the exact
   deterministic mapping: `ACCEPT->continue`, `RETRY->retry`,
   `REPLAN->replan`, `ROLLBACK->rollback`. Do not invent a fifth action.

3. Read the machine-readable integration spec if exposed:

   ```bash
   bound integration-spec
   ```
