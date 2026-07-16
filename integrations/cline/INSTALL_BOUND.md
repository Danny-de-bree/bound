# Integration prompt for Cline — Install BOUND

> This document is **not documentation for a human.** It is a prompt designed
> to be pasted directly into **Cline**. Paste everything below the line into a
> Cline session and let it run.

---

You are Cline. Your job is to integrate **BOUND** — a deterministic
bounded-utility policy for agentic systems — into *this* project's workflow,
so that BOUND evaluates meaningful execution boundaries and your control flow
reacts to its decision.

This is an **integration prompt for Cline**, not a native Cline plugin. BOUND
ships no Cline-specific code. Do not assume any undocumented Cline hook,
event, or extension point. Before relying on a mechanism, **inspect what is
actually available in this environment** and use only that.

## What BOUND is, and what it is not

BOUND is framework-neutral. It does not know about Cline, your workspace, your
MCP servers, or your tool set. It turns a `StepContract` + `ExecutionEvidence`
into a deterministic `EvaluationResult` with a `.decision` in
`ACCEPT / RETRY / REPLAN / ROLLBACK`.

> **BOUND decides whether to continue, retry, replan, or rollback. BOUND does
> not decide what code to write.** You (Cline) decide what code to write; BOUND
> decides whether the step you just took is good enough to move on.

## The conceptual integration boundary

    Cline executes a meaningful task / subtask
            ↓
    verification runs (tests, lint, type-check, build — whatever this project has)
            ↓
    evidence collected (only what was actually observed)
            ↓
    BOUND evaluates  (deterministic)
            ↓
    Cline reacts to the decision

You own the first and last boxes. BOUND owns the middle evaluation. The seam
between them is plain data: `StepContract` in, `ExecutionEvidence` in,
`EvaluationResult` out.

## Step 0 — Install and inspect (do this before anything else)

1. Install the latest stable `bound-policy`:

   ```bash
   pip install bound-policy
   ```

2. **Inspect the installed public API; do not assume it.** Confirm the names
   against the *installed* package:

   ```bash
   python -c "import bound; print(bound.__version__); print(bound.__all__)"
   ```

   You should find (confirm each): `BoundWorkflow` (with `prepare(...)` and
   `evaluate_step(*, contract, evidence, criteria)`), `StepContract`,
   `AcceptanceCheck`, `RiskCheck`, `StepBudget`, `BoundPlan`,
   `StaticContractGenerator`, `ExecutionEvidence`, `CheckEvidence`,
   `EvidenceCollector`, `BoundCriteria`, `EvaluationResult`, `Decision`.

   There may also be a higher-level helper for agent consumers,
   `evaluate_agent_step(...) -> AgentControlResult`, carrying `.evaluation`,
   `.next_action` (`continue`/`retry`/`replan`/`rollback`), and deterministic
   `.feedback`. **Inspect the installed API to confirm whether it exists and
   its exact name/signature.** If it exists, prefer it. If it does not, use
   `BoundWorkflow.evaluate_step(...)` and map the decision yourself with the
   exact deterministic mapping:

   ```text
   ACCEPT   -> continue
   RETRY    -> retry
   REPLAN   -> replan
   ROLLBACK -> rollback
   ```

   Do not invent a fifth action.

3. Read the machine-readable integration spec if the CLI exposes it:

   ```bash
   bound integration-spec
   ```

   If present, treat it as authoritative for "when to call BOUND" / "when not
   to" / "required flow".

## Step 1 — Inspect what Cline mechanisms are ACTUALLY available here

Do **not** assume undocumented Cline hooks (custom events, lifecycle callbacks,
tool interceptors, plugin APIs, hidden configuration keys). Cline's available
mechanisms vary by version and workspace configuration.

Inspect and record what is actually available in *this* environment:

- **Execution surface**: What can Cline run as a "meaningful step"? (A task,
  a subtask, a checkpoint between tool batches, an explicitly delimited
  phase in your plan.) Which of these is the right granularity to evaluate?
- **Verification commands**: What exact commands does this project use for
  tests, lint, type-check, build? Run them and capture real exit codes /
  stdout. List them verbatim.
- **Observability**: Which of these can you actually observe after a step?
  - tests (pass/fail, counts)
  - lint (clean / not clean)
  - type checks (clean / not clean)
  - expected files present
  - unexpected files present (e.g. via `git status` / `git diff --name-only`)
  - failed commands (non-zero exit codes you ran)
  - retries (your own retry counter for this step)
  - tool calls where observable (your own count of tool invocations)
  - tokens where observable (only if Cline/the provider exposes it)
  - runtime where observable (wall-clock you can measure)
  - rollback availability (is the working tree clean / is there a checkpoint?)
- **Rollback**: Is there a clean checkpoint to return to? (e.g. a git commit,
  a stash, a Cline checkpoint.) Confirm it concretely; do not assume.

Record these findings honestly. Signals you cannot observe here stay
unobservable — never fabricated.

## Step 2 — Identify meaningful step boundaries

Call BOUND at **meaningful** boundaries: after a task/subtask completes and
its verification runs, after a retry, before deciding to keep refining the same
objective. Do **not** call BOUND after every token, file read, shell command,
or low-level tool call.

Choose task/subtask granularity such that each has a real, observable
definition of "done" (acceptance checks) and at least one observable risk worth
guarding against.

## Step 3 — Report the proposed integration BEFORE modifying anything

Do not change any file until you have printed (or recorded) this report with
exactly these headings:

```text
Integration point:
    Where in the Cline workflow BOUND is called (e.g. after the verification
    commands for a subtask, before deciding to refine or move on).

Step boundary:
    The concrete Cline tasks/subtasks you will map to StepContracts, and why
    each granularity is meaningful.

Available evidence:
    The observable signals this environment actually produces per step
    (exact commands and what they yield). Be specific to THIS Cline setup.

Missing evidence:
    The signals that are NOT observable here (e.g. token usage if not exposed),
    and how you will represent them as unavailable rather than fabricating them.

Control-flow mapping:
    How each BOUND decision changes what Cline does next (see Step 5).
    Reference the exact mapping (ACCEPT->continue, RETRY->retry,
    REPLAN->replan, ROLLBACK->rollback).

Files to modify:
    The exact list of files you intend to create or change. Keep it small;
    the integration must be thin and removable.
```

Only after this report is produced may you begin implementation.
## Step 4 — Build contracts and collect evidence

1. Map each meaningful Cline task/subtask to a `StepContract`:

   ```python
   from bound import (
       AcceptanceCheck, RiskCheck, StepBudget, StepContract,
   )

   contract = StepContract(
       id="implement-validation",
       description="Add input validation to the POST /items endpoint",
       goal="Invalid input is rejected with a 400",
       acceptance_checks=[
           AcceptanceCheck(id="tests-pass", description="pytest is green"),
           AcceptanceCheck(id="lint-clean", description="ruff is clean"),
           AcceptanceCheck(id="typecheck-clean", description="mypy is clean"),
           AcceptanceCheck(id="rejects-invalid",
                           description="invalid input returns 400"),
       ],
       risk_checks=[
           RiskCheck(id="no-tests-removed",
                      description="No existing tests deleted",
                      severity=0.8),
           RiskCheck(id="no-unexpected-files",
                      description="No files outside expected scope changed",
                      severity=0.6),
       ],
       expected_artifacts=["src/app/items.py"],
       budget=StepBudget(max_retries=3, max_tool_calls=40),
   )
   ```

2. Collect `ExecutionEvidence` from only what you observed. Potential evidence
   in a Cline run: tests, lint, type checks, expected files present, unexpected
   files (e.g. `git diff --name-only`), failed commands, retries (your own
   counter), tool calls where observable, tokens where observable, runtime
   where observable, rollback availability.

   ```python
   from bound import CheckEvidence, ExecutionEvidence

   evidence = ExecutionEvidence(
       acceptance=[
           CheckEvidence(check_id="tests-pass", passed=tests_ok, detail=detail),
           CheckEvidence(check_id="lint-clean", passed=lint_ok),
           CheckEvidence(check_id="typecheck-clean", passed=mypy_ok),
           CheckEvidence(check_id="rejects-invalid",
                         passed=invalid_rejected, detail=detail),
       ],
       risks=[
           CheckEvidence(check_id="no-tests-removed",
                         passed=no_tests_removed),
           CheckEvidence(check_id="no-unexpected-files",
                         passed=no_unexpected, detail=detail),
       ],
       produced_artifacts=produced,
       unexpected_artifacts=unexpected,
       retry_count=retries,
       tool_call_count=tool_calls,
       rollback_available=checkpoint_exists,
   )
   ```

   Never fabricate. If token usage or runtime is not observable here, leave
   those fields unset.

## Step 5 — Evaluate and react to the decision

Evaluate with BOUND (use `evaluate_agent_step` if it exists, else
`BoundWorkflow.evaluate_step`), then react to the decision exactly as follows:

- **ACCEPT** → stop refining the current step. Continue to the next plan
  objective. Explicitly do not keep optimizing an already-accepted step.
- **RETRY** → preserve the current strategy. Make one focused correction
  targeting the remaining failed/missing evidence. Re-collect evidence and
  re-evaluate. Respect the step's `StepBudget` (e.g. `max_retries`).
- **REPLAN** → stop iterating on the current strategy. Choose a materially
  different approach and build a new `StepContract` for it.
- **ROLLBACK** → restore a safe state where possible (e.g. restore the Cline
  checkpoint / `git checkout`). Then replan. BOUND does not execute the
  rollback; you (Cline) do, using the mechanism you confirmed in Step 1.

```python
from bound import BoundWorkflow, BoundCriteria

result = BoundWorkflow().evaluate_step(
    contract=contract,
    evidence=evidence,
    criteria=BoundCriteria(threshold=0.75),
)
decision = result.decision  # ACCEPT | RETRY | REPLAN | ROLLBACK
```

## Rules you must not break

- **Never assume undocumented Cline hooks.** Only use mechanisms you inspected
  and confirmed are available in this environment.
- **Never fabricate evidence.** Unobservable signals stay unobservable.
- **Never duplicate BOUND's policy logic.** Do not reimplement the score
  formula or decision rule. Call BOUND and use its result.
- **Never add an LLM evaluator / LLM-as-judge.** BOUND's decision is
  deterministic. You may use an LLM only to draft contracts, never to make the
  decision or assign A/I/R/C scores.
- **Do not hardcode Cline-specific behavior into `src/bound/`.** All
  Cline-specific wiring lives in this project's integration files, not in the
  BOUND package. BOUND must remain framework-neutral.
- **Keep the integration thin and removable.** Removing BOUND must not require
  restructuring the project.

## Step 6 — Add an end-to-end test

Add one end-to-end test that exercises the real public API against this
project's real verification commands (or a clearly-labeled deterministic stub
when a command is unavailable in CI). The test must build a real
`StepContract`, collect real `ExecutionEvidence`, evaluate via BOUND, assert the
decision is one of the four valid decisions, and assert the control-flow branch
you would take. Do not hardcode "ACCEPT" unless the evidence genuinely
satisfies the contract.

## Done

Summarize: the Cline mechanisms you actually used (and confirmed exist), the
files you created/modified, the `bound-policy` version installed, one real
`StepContract` + decision from a run, and confirmation that no evidence was
fabricated and no BOUND policy logic was duplicated.

Remember: **BOUND decides whether to continue, retry, replan, or rollback.
BOUND does not decide what code to write.**

