# Install BOUND into this project (generic self-integration prompt)

> This document is **not documentation for a human.** It is a prompt designed
> to be pasted directly into a coding agent. Paste everything below the line
> into the agent and let it run.

---

You are a coding agent. Your job is to integrate **BOUND** — a deterministic
bounded-utility policy for agentic systems — into *this* project's existing
workflow, so that BOUND evaluates meaningful execution boundaries and your
control flow reacts to its decision.

BOUND is framework-neutral. It does not know anything about your editor, your
task runner, your CI, or your agent loop. You are the one who wires it in.

## The BOUND control loop

    StepContract          (what "done" and "risk" mean for one step)
        ↓
    you execute the step
        ↓
    ExecutionEvidence     (only what you actually observed — never fabricated)
        ↓
    BOUND evaluates       (deterministic: BoundWorkflow.evaluate_step)
        ↓
    EvaluationResult      (.decision ∈ ACCEPT / RETRY / REPLAN / ROLLBACK)
        ↓
    you apply the control action

> **BOUND decides whether to continue, retry, replan, or rollback. BOUND does
> not decide what code to write.** You decide what code to write; BOUND decides
> whether the step you just took is good enough to move on, close enough to
> retry once, too far off to keep the same strategy, or unsafe enough to roll
> back.

## Step 0 — Install and inspect (do this before anything else)

1. Install the latest stable `bound-policy` into this project's environment:

   ```bash
   pip install bound-policy
   ```

   Do not pin a speculative version. Do not install from a fork unless
   explicitly instructed. Use the latest stable release.

2. **Inspect the installed public API; do not assume it.** The names below are
   accurate as of this writing, but you must confirm them against the
   *installed* package before using them. Run:

   ```bash
   python -c "import bound; print(bound.__version__); print(bound.__all__)"
   ```

   Then read the actual signatures you intend to call. For example:

   ```python
   import bound, inspect
   print(inspect.signature(bound.BoundWorkflow.evaluate_step))
   ```

   You should find these public names (confirm each exists):

   - `BoundWorkflow` — orchestration seam. Construct with `BoundWorkflow()`.
     - `workflow.prepare(*, goal, plan, context=None)` → `BoundPlan`
     - `workflow.evaluate_step(*, contract, evidence, criteria)` → `EvaluationResult`
   - `StepContract` — `StepContract(id, description, goal, acceptance_checks=[...], risk_checks=[], expected_artifacts=[], budget=None)`.
   - `AcceptanceCheck` — `AcceptanceCheck(id, description, required=True)`.
   - `RiskCheck` — `RiskCheck(id, description, severity)` (`severity ∈ [0,1]`; `1.0` is a hard safety boundary).
   - `StepBudget` — `StepBudget(max_retries=None, max_tool_calls=None, max_tokens=None, max_runtime_seconds=None)`. `None` means *no explicit budget*, not a zero budget.
   - `BoundPlan` — `BoundPlan(goal, steps=[...])`.
   - `StaticContractGenerator` — `StaticContractGenerator(plan)`. Returns the same plan every call. Use it for tests and deterministic paths.
   - `ExecutionEvidence` — `ExecutionEvidence(acceptance=[...], risks=[...], produced_artifacts=[...], unexpected_artifacts=[...], retry_count=0, tool_call_count=0, token_usage=None, runtime_seconds=None, rollback_available=None)`.
   - `CheckEvidence` — `CheckEvidence(check_id, passed, detail="")`. `check_id` must match an acceptance/risk check id on the contract.
   - `EvidenceCollector` — a Protocol (`collect(*, contract, execution) -> ExecutionEvidence`). You may implement your own collector; the core never introspects your `execution` handle.
   - `BoundCriteria` — `BoundCriteria(threshold, retry_margin=0.1, rollback_risk_threshold=0.8, weights=BoundWeights())`. `weights` defaults to all-`1.0`.
   - `EvaluationResult` — carries `.scores`, `.decision`, `.score`, `.threshold`, `.weights`, components, and `.provenance`.
   - `Decision` — `Literal["ACCEPT", "RETRY", "REPLAN", "ROLLBACK"]`.

   There may also be a higher-level helper intended for agent consumers:

   ```python
   evaluate_agent_step(contract, evidence, criteria, ...) -> AgentControlResult
   ```

   where `AgentControlResult` would carry `.evaluation` (the `EvaluationResult`),
   `.next_action` (`Literal["continue", "retry", "replan", "rollback"]`), and
   `.feedback` (deterministic, derived only from result + contract + evidence +
   provenance). **Inspect the installed API to confirm whether this helper
   exists and its exact name/signature.** If it exists, prefer it. If it does
   not, use `BoundWorkflow.evaluate_step(...)` directly and map the decision
   yourself with the exact, deterministic mapping:

   ```text
   ACCEPT   -> continue
   RETRY    -> retry
   REPLAN   -> replan
   ROLLBACK -> rollback
   ```

   Do not invent a different mapping. Do not invent a fifth action.

3. Read the machine-readable integration spec if the CLI exposes it:

   ```bash
   bound integration-spec
   ```

   If the subcommand exists, use it as the authoritative "when to call BOUND"
   / "when not to" / "required flow" reference. If it does not exist yet, fall
   back to the rules in "Step 2" below.

## Step 1 — Inspect this project and its workflow

Before writing any integration code, understand the environment you are
integrating into:

- What is this project? What language, build tool, and test runner does it use?
- How is work already organized (tasks, subtasks, todos, plan files)?
- What verification commands already exist and are run today? (tests, lint,
  type-check, build). List the **exact commands**.
- What is observable *for free* after a step runs, and what is not? (e.g. a
  pytest exit code is observable; "code quality" is not).
- Is there a notion of git rollback / a clean checkpoint you can return to?
  Confirm it, do not assume it.

Record your findings. Be honest about what is observable and what is not.

## Step 2 — Identify meaningful plan-step boundaries

BOUND must be called at **meaningful** boundaries, not after every tool call.

Call BOUND after:
- a meaningful plan step completes,
- implementation plus verification,
- a retry,
- before deciding to continue refining the same objective.

Do **not** call BOUND after:
- every token,
- every file read,
- every shell command,
- every low-level tool call.

Choose step granularity such that each step has a real, observable definition
of "done" (acceptance checks) and at least one observable risk worth guarding
against. A step that has no observable success criteria is too small or too
vague to evaluate — do not map it to a `StepContract`.

## Step 3 — Identify observable evidence already available

For each step boundary you chose, enumerate the evidence that is **already**
observable in this project. The deterministic `ExecutionEvidence` model holds:

- `acceptance`: a `CheckEvidence` per acceptance check (pass/fail + detail).
- `risks`: a `CheckEvidence` per risk check that was probed.
- `produced_artifacts`: paths/ids of expected artifacts that appeared.
- `unexpected_artifacts`: paths/ids of artifacts that appeared but were *not*
  expected (a real risk signal).
- `retry_count`, `tool_call_count`, `token_usage` (optional), `runtime_seconds`
  (optional).
- `rollback_available`: whether a clean rollback is still possible.

Map each `CheckEvidence.check_id` to a check id you declared on the
`StepContract`. Evidence for a check you did not declare is allowed (the
evaluator reconciles it), but missing evidence for a *required* acceptance
check is treated as failure — never silently passing.

**Never fabricate unavailable evidence.** If a signal cannot be observed in
this project, represent it as unavailable (`passed`/`rollback_available`/etc.
left unset or set honestly to what you observed), and let the configured
deterministic policy handle it. Never convert an assumption into a passing
check.

## Step 4 — Report the proposed integration BEFORE modifying anything

Do not write or change any file until you have printed the following report and
waited for it to be accepted (or, in an autonomous run, recorded it in a
clearly labeled section). The report must contain exactly these headings:

```text
Integration point:
    Where in the workflow BOUND is called (e.g. after `pytest` + `ruff` for
    the "implement feature X" step).

Step boundary:
    The concrete steps you will map to StepContracts, with their granularity
    and why each is meaningful.

Available evidence:
    The observable signals this project already produces per step (exact
    commands and what they yield).

Missing evidence:
    The signals that are NOT observable here, and how you will represent them
    as unavailable rather than fabricating them.

Control-flow mapping:
    How each BOUND decision will change what you do next:
        ACCEPT   -> continue to next plan objective (stop refining this one)
        RETRY    -> preserve strategy, make one focused correction, re-evaluate
        REPLAN   -> abandon current strategy, choose a materially different one
        ROLLBACK -> restore a safe state, then replan
    Reference the exact mapping (ACCEPT->continue, RETRY->retry,
    REPLAN->replan, ROLLBACK->rollback).

Files to modify:
    The exact list of files you intend to create or change to wire BOUND in.
    Keep this list small. The integration must be thin and removable.
```

Only after this report is produced may you begin implementation.
## Step 5 — Implement the integration

1. **Create or map meaningful steps to `StepContract`.** Each step needs an
   `id`, `description`, `goal`, at least one `AcceptanceCheck` (the contract
   rejects an empty acceptance list), optional `RiskCheck`s, optional
   `expected_artifacts`, and an optional `StepBudget`.

   ```python
   from bound import (
       AcceptanceCheck, RiskCheck, StepBudget, StepContract,
   )

   contract = StepContract(
       id="add-validation-endpoint",
       description="Add robust input validation to the /items POST endpoint",
       goal="Reject invalid input with a clear 400 response",
       acceptance_checks=[
           AcceptanceCheck(id="tests-pass", description="pytest is green"),
           AcceptanceCheck(id="lint-clean", description="ruff is clean"),
           AcceptanceCheck(id="rejects-invalid",
                           description="invalid input returns 400"),
       ],
       risk_checks=[
           RiskCheck(id="no-tests-removed",
                      description="No existing tests were deleted",
                      severity=0.8),
       ],
       expected_artifacts=["src/app/items.py", "tests/test_items_validation.py"],
       budget=StepBudget(max_retries=3, max_tool_calls=40),
   )
   ```

2. **Implement an `EvidenceCollector`** (or a plain function that returns
   `ExecutionEvidence`) that reads *only* what this project actually observes.
   Do not import a framework the project does not have. Do not assume a hook
   that does not exist.

   ```python
   from bound import CheckEvidence, ExecutionEvidence

   def collect_evidence(contract, *, subprocess_results) -> ExecutionEvidence:
       # Read real observations: test exit code, lint exit code, git status, etc.
       ...
       return ExecutionEvidence(
           acceptance=[
               CheckEvidence(check_id="tests-pass", passed=tests_ok),
               CheckEvidence(check_id="lint-clean", passed=lint_ok),
               CheckEvidence(check_id="rejects-invalid",
                             passed=invalid_rejected, detail=detail),
           ],
           risks=[
               CheckEvidence(check_id="no-tests-removed",
                             passed=no_tests_removed),
           ],
           produced_artifacts=produced,
           unexpected_artifacts=unexpected,
           retry_count=retries,
           tool_call_count=tool_calls,
           rollback_available=git_clean,
       )
   ```

3. **Evaluate with BOUND.** Use the high-level helper if it exists
   (`evaluate_agent_step`), otherwise `BoundWorkflow.evaluate_step`. Pick a
   `BoundCriteria` whose threshold is calibrated to *this* workload; the
   defaults are reference defaults, not universal truths.

   ```python
   from bound import BoundWorkflow, BoundCriteria

   workflow = BoundWorkflow()
   result = workflow.evaluate_step(
       contract=contract,
       evidence=evidence,
       criteria=BoundCriteria(threshold=0.75),
   )
   print(result.decision, result.score, result.threshold)
   ```

4. **Apply the returned control action.** Branch on `result.decision` (or on
   `agent_result.next_action` if you used the helper). Implement exactly the
   four behaviors:

   - `ACCEPT` / `continue`: stop refining this step; move to the next plan
     objective. Explicitly do **not** keep optimizing an already-accepted step.
   - `RETRY` / `retry`: keep the current strategy; make one focused correction;
     re-collect evidence; re-evaluate.
   - `REPLAN` / `replan`: stop iterating on the current strategy; choose a
     materially different approach; build a new `StepContract` for it.
   - `ROLLBACK` / `rollback`: restore a safe state (e.g. `git checkout`/revert)
     where possible; then replan. BOUND does **not** execute the rollback for
     you — you do.

## Rules you must not break

- **Never fabricate evidence.** Unobservable signals stay unobservable.
- **Never duplicate BOUND's policy logic.** Do not reimplement the score
  formula, the decision rule, or the threshold semantics. Call BOUND and use
  its result.
- **Never add an LLM evaluator / LLM-as-judge.** BOUND's decision is
  deterministic. You may use an LLM only to *draft* contracts (turning intent
  into structured data), never to make the decision or assign A/I/R/C scores.
- **Keep the integration thin and removable.** All BOUND wiring should sit in
  a small number of clearly labeled files. Removing BOUND must not require
  restructuring the project.
- **Do not modify `src/bound/`** or the BOUND package itself. You are a
  consumer, not a maintainer of BOUND.

## Step 6 — Add an end-to-end test

Add one end-to-end test that exercises the real public API against this
project's own verification commands (or a deterministic stub of them when a
real command is not available in CI). The test must:

1. Build a `StepContract` for a real step in this project.
2. Collect real `ExecutionEvidence` (or honestly-stubbed evidence that is
   clearly labeled as such).
3. Evaluate via `BoundWorkflow.evaluate_step` (or `evaluate_agent_step`).
4. Assert that the returned decision is one of the four valid decisions and
   that the score and threshold are present.
5. Assert the control-flow branch you would take for that decision.

Do not assert a hardcoded "ACCEPT" unless the evidence genuinely satisfies the
contract. The test must reflect real evidence, not a wish.

## Done

When finished, summarize:
- the files you created/modified,
- the `bound-policy` version you installed,
- one example `StepContract` + its decision from a real run,
- confirmation that no evidence was fabricated and no BOUND policy logic was
  duplicated.

Remember: **BOUND decides whether to continue, retry, replan, or rollback.
BOUND does not decide what code to write.**

