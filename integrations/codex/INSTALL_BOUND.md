# Integration prompt for Codex — Install BOUND

> This document is **not documentation for a human.** It is a prompt designed
> to be pasted directly into Codex. Paste everything below the line
> into a Codex session and let it run.

---

You are Codex. Your job is to integrate **BOUND** — a deterministic
bounded-utility policy for agentic systems — into *this* project's existing
workflow, so that BOUND evaluates meaningful execution boundaries and your
control flow reacts to its decision.

BOUND is framework-neutral. It does not know anything about your editor, your
task runner, your CI, or your agent loop. Codex is the execution surface that wires it in.

This is an integration prompt, not a native Codex plugin. BOUND ships no
Codex-specific code. Do not assume hooks, rules, modes, MCP servers, browser
actions, checkpoints, or lifecycle events. Inspect the installed version and
workspace, and use only mechanisms confirmed there.

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
> not decide what code to write.** You (Codex) decide what code to write; BOUND decides
> whether the step you just took is good enough to move on, close enough to
> retry once, too far off to keep the same strategy, or unsafe enough to roll
> back.

## Verified evidence & provenance (v0.7.0) — take the agent out of the evidence loop

BOUND v0.7.0 separates *who reports* evidence from *who verifies* it. The agent
is a participant, not the judge. Follow these rules for every step:

**1. Define the contract upfront, including provenance requirements.**
Each `AcceptanceCheck` / `RiskCheck` may declare:
- `accepted_provenance`: the trust provenances it accepts
  (`observed`/`verified`/`attested`/`evaluated`/`claimed`/`defaulted`/`missing`).
  `None` accepts any; a non-empty list rejects weaker evidence.
- `on_missing`: policy action when no evidence is collected (`retry`/`replan`/`rollback`/`accept`).
- `on_claimed`: action when the only evidence is the agent self-report.
- `decision_critical` (risk checks): when `True`, missing/claimed evidence forces
  `INSUFFICIENT` assurance and blocks a clean `ACCEPT`.

**2. Configure allowed collectors; BOUND performs objective verification.**
BOUND ships independent collectors (`PytestCollector`, `GitCollector`,
`JUnitCollector`, `BudgetCollector`, `ProcessRuntimeCollector`,
`CommandCollector`) that *execute* verification commands BOUND controls — never
commands the agent injects. The agent configures which collectors are allowed;
BOUND runs them and records `evidence.collected` audit events with
`VERIFIED`/`OBSERVED` provenance, a collector name, version, artefact hash and
a timezone-aware timestamp. A collector crash, stale artefact or zero-test run
is recorded honestly (`evidence.collection_failed` / `INVALID`), never a
verified pass.

**3. The agent CANNOT assign `VERIFIED` provenance.**
Agent self-report is **always `CLAIMED`**, never `VERIFIED`/`OBSERVED`. The
agent must not override, edit or suppress a collector result. `MISSING` means
"not collected" — never silently `0`. `None` on a telemetry metric is missing,
not a measured zero.

**4. Subjective criteria use a separate evaluator (`EVALUATED`).**
Criteria that cannot be independently re-run (e.g. UX quality) are scored by
the deterministic evaluator with `EVALUATED` provenance — honest but not
independently verified. They never become `VERIFIED`.

**5. BOUND logs what the agent reports; the agent executes control actions.**
BOUND records the agent self-reported action via `action.reported`
(`reported_provenance=CLAIMED`); an independent hook may add an
`observed_action` to confirm it. **The agent — not BOUND — executes control
actions and workspace rollback.** BOUND is a thin harness: it emits `ROLLBACK`
(and may independently verify the resulting state afterwards); it never
performs a workspace rollback itself.

**6. Assurance gates the candidate decision.**
BOUND computes a `DecisionAssurance` level (`VERIFIED`/`MIXED`/`CLAIMED`/
`INSUFFICIENT`) from the decision-critical checks provenance. A candidate
`ACCEPT` backed only by `CLAIMED`/`MISSING` critical evidence is downgraded to
the contract `on_missing`/`on_claimed` action. Inspect it with:

```bash
bound inspect <run_id>                  # per-check provenance + candidate vs final + assurance + coverage
bound inspect <run_id> --only-unverified # only unverified/claimed/missing/invalid evidence
bound inspect <run_id> --json           # machine-readable: provenance + assurance + coverage
```

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
   - `CheckEvidence` — `CheckEvidence(check_id, passed, source="", details=None, provenance=MISSING, collector=None, collector_version=None, observed_at=None, artifact_hash=None, raw_artifact_ref=None, status=None)`. `check_id` must match an acceptance/risk check id on the contract. v0.7: `provenance` is the trust provenance (agent self-report is always `CLAIMED`, never `VERIFIED`); `collector`/`collector_version`/`observed_at`/`artifact_hash` come from a BOUND-controlled collector; `status` distinguishes `failed`/`unverified`/`missing`/`invalid`.
   - `EvidenceCollector` — a Protocol (`collect(*, contract, execution) -> ExecutionEvidence`). You may implement your own collector; the core never introspects your `execution` handle. v0.7 ships ready collectors (`PytestCollector`, `GitCollector`, `JUnitCollector`, `BudgetCollector`, `ProcessRuntimeCollector`, `CommandCollector`) that grant `VERIFIED`/`OBSERVED` provenance — prefer them over hand-rolled self-report; the agent may configure allowed collectors but never inject arbitrary commands or override a collector result.
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
- Search for existing integration records such as `INTEGRATION.md`,
  `INTEGRATION_REPORT.md`, and files under `bound_integration/`. Treat them
  as prior claims to verify, not as authoritative evidence. Record which were
  found and whether they contain enough contract, evidence, criteria, and
  execution metadata to reproduce their reported score.
- Inspect whether this workspace actually provides Codex rules, modes, MCP
  tools, commands, task boundaries, or checkpoints. Treat each as unavailable
  until confirmed.

Record your findings. Be honest about what is observable and what is not.

## Step 2 — Establish the plan and BOUND execution lineage

Before implementation, establish the plan that BOUND will evaluate. For a
multi-step, multi-phase, or multi-agent task, create or maintain `PLAN.md` at
the repository root. A genuinely small one-step task may use an inline plan.

Use the strongest planning mechanism that this environment actually exposes;
do not invent one. Define each meaningful phase with a stable id, goal,
observable acceptance checks, risk checks, exact verification commands,
budget, expected artifacts, and—when relevant—owner and dependencies. Those
phases are the source of the corresponding `StepContract`s.

Keep intent, wiring, and observed results separate:

```text
PLAN.md                              what should happen
bound_integration/                   thin agent-to-BOUND wiring
bound_integration/INTEGRATION_REPORT.md
                                     what actually happened
```

The lineage must remain inspectable:

```text
Intent -> PLAN.md phase -> StepContract -> execution -> ExecutionEvidence
       -> BOUND EvaluationResult -> control action -> INTEGRATION_REPORT.md
```

When a strategy changes materially, preserve the original phase and create a
derived id instead of rewriting history solely to hide the deviation:

```text
PHASE-002 -> REPLAN -> PHASE-002-R1 -> RETRY -> PHASE-002-R1 -> ACCEPT
```

## Step 3 — Identify meaningful plan-step boundaries

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

## Step 4 — Identify observable evidence already available

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

## Step 5 — Report the proposed integration BEFORE modifying anything

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
## Step 6 — Implement the integration

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
   from bound import CheckEvidence, EvidenceProvenance, ExecutionEvidence
   # Agent-collected evidence is CLAIMED — the agent can never grant VERIFIED.
   # For VERIFIED evidence use the BOUND collectors (bound.PytestCollector,
   # bound.GitCollector, ...), which run verification BOUND controls.

   def collect_evidence(contract, *, subprocess_results) -> ExecutionEvidence:
       # Read real observations: test exit code, lint exit code, git status, etc.
       ...
       return ExecutionEvidence(
           acceptance=[
               CheckEvidence(check_id="tests-pass", passed=tests_ok,
                             provenance=EvidenceProvenance.CLAIMED),
               CheckEvidence(check_id="lint-clean", passed=lint_ok,
                             provenance=EvidenceProvenance.CLAIMED),
               CheckEvidence(check_id="rejects-invalid",
                             passed=invalid_rejected, detail=detail,
                             provenance=EvidenceProvenance.CLAIMED),
           ],
           risks=[
               CheckEvidence(check_id="no-tests-removed",
                             passed=no_tests_removed,
                             provenance=EvidenceProvenance.CLAIMED),
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

4. **Print and persist the complete numeric evaluation.** Read every value
   from the returned BOUND objects; do not calculate or estimate these values
   yourself. If using `evaluate_agent_step`, read scores from
   `agent_result.evaluation` and the action from
   `agent_result.next_action`. Otherwise apply only the exact deterministic
   decision-to-action mapping defined above.

   Emit this block for every evaluation:

   ```text
   BOUND evaluation
   Acceptance (A): <evaluation.scores.acceptance to 4 decimals>
   Influence (I): <evaluation.scores.influence to 4 decimals>
   Risk (R): <evaluation.scores.risk to 4 decimals>
   Cost (C): <evaluation.scores.cost to 4 decimals>
   Score (S): <evaluation.score to 4 decimals>
   Threshold (T): <evaluation.threshold to 4 decimals>
   Decision: <evaluation.decision>
   Next action: <continue|retry|replan|rollback>
   ```

   Also preserve the exposed weights, weighted components, retry margin,
   rollback-risk threshold, and score provenance/reasoning so the calculation
   is auditable. Numeric output without the real underlying evidence is not a
   valid report.

5. **Validate any existing integration report.** When `INTEGRATION.md`,
   `INTEGRATION_REPORT.md`, or equivalent prior output exists:

   - compare its contract, evidence, criteria, budgets, scores, decision, and
     next action with the current run;
   - when the recorded inputs are complete, deserialize/reconstruct them and
     call BOUND again, then compare the returned A/I/R/C/S/T, decision, and
     next action with the recorded values;
   - never reproduce BOUND's formula in report code as a second evaluator;
   - if inputs are incomplete, mark the historical score `not reproducible`
     and list the missing fields instead of declaring it correct;
   - if evidence or configuration changed, mark the old evaluation stale and
     append a new evaluation rather than silently overwriting history;
   - treat any mismatch as a failed consistency check, record both values, and
     investigate before continuing.

6. **Apply the returned control action.** Branch on `result.decision` (or on
   `agent_result.next_action` if you used the helper). Implement exactly the
   four behaviors:

   - `ACCEPT` / `continue`: stop refining this step; move to the next plan
     objective. Explicitly do **not** keep optimizing an already-accepted step.
   - `RETRY` / `retry`: keep the current strategy; make one focused correction;
     re-collect evidence; re-evaluate.
   - `REPLAN` / `replan`: stop iterating on the current strategy; choose a
     materially different approach; build a new `StepContract` for it.
   - `ROLLBACK` / `rollback`: restore only a previously confirmed safe
     checkpoint, without discarding unrelated or pre-existing user changes;
     then replan. If no safe rollback exists, report that honestly and do not
     perform a destructive approximation. BOUND does **not** execute rollback.

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

## Step 7 — Add an end-to-end test

Add one end-to-end test that exercises the real public API against this
project's own verification commands (or a deterministic stub of them when a
real command is not available in CI). The test must:

1. Build a `StepContract` for a real step in this project.
2. Run the real verification commands and collect `ExecutionEvidence` from
   their observed results. If a command is unavailable in CI, test the
   collector separately with explicitly labeled deterministic fixtures, but
   retain at least one end-to-end path based on real observed evidence.
3. Evaluate via `BoundWorkflow.evaluate_step` (or `evaluate_agent_step`).
4. Assert that the returned decision is one of the four valid decisions and
   that the score and threshold are present.
5. Assert the control-flow branch you would take for that decision.

Do not assert a hardcoded "ACCEPT" unless the evidence genuinely satisfies the
contract. The test must reflect real evidence, not a wish.

## Step 8 — Record decision lineage (BOUND v0.7.0)

BOUND v0.7.0 can record every evaluation as a reproducible, append-only local
lineage — `contract → evidence → scores → decision → agent outcome` — under
`.bound/runs/<run_id>/`. Lineage is **opt-in per run** and backwards compatible:
if you never start a run nothing is recorded. Record it for any non-trivial task
so the decision history is auditable and reproducible.

Follow these rules for every task you evaluate with BOUND:

1. **Start ONE run per task.** Begin the task with:

   ```bash
   bound run start "<task>" --metadata phase=PHASE-001
   ```

   This prints a `run_id` (e.g. `run_ae5ed2f7e7cd4b2bb820`) and writes the
   `run_started` event. (In Python, use `with bound.start_run("<task>") as run:`.)

2. **Use STABLE step / contract ids.** Carry the same id from `PLAN.md` →
   `StepContract(id=...)` → lineage. A replan of the same step appends `-R<N>`
   (`PHASE-001` → `PHASE-001-R1`); never invent an unrelated id, and never
   rewrite history to hide a replan.

3. **Evaluate only MEANINGFUL boundaries** (after implementation + verification,
   not after every token/file/command). Record the evaluation into the run:

   ```bash
   bound evaluate --run <run_id> --step PHASE-001 --attempt 1 \
       --action "..." --goal "..." \
       --acceptance <A> --influence <I> --risk <R> --cost <C> \
       --threshold 0.7 --retry-margin 0.1
   ```

   This writes `step_started` + `evaluation_recorded` and adds a `lineage`
   block to the JSON output. `--step` is the stable contract id; `--attempt`
   is the one-based attempt number.

4. **Record the REAL follow-up action** you actually took (not what BOUND
   "should" have produced). Even if the decision was REPLAN, if you chose to
   retry instead, record `retry`; if you rolled back, record `rollback`:

   ```bash
   bound outcome --run <run_id> --step PHASE-001 --attempt 1 \
       --decision REPLAN --note "switched strategy to validator + parametrized tests"
   ```

   `--decision` is `ACCEPT|RETRY|REPLAN|ROLLBACK`; `--next-action` and
   `--reason-code` are derived from the decision when omitted. Use `--note` for
   a short, honest description of what you did next.

5. **Explicitly CLOSE the run** when the task ends — completed, interrupted, or
   failed — so no run is left silently "started":

   ```bash
   bound run finish <run_id> --status completed|interrupted|failed --note "..."
   ```

6. **REPORT the local lineage path** in your final summary: point to
   `.bound/runs/<run_id>/` (`run.json` + the append-only `events.jsonl`) and show
   the decision tree with:

   ```bash
   bound inspect <run_id>
   ```

### REPLAN → ACCEPT example

The canonical v0.7.0 flow is a single run with two attempts:

```text
Attempt 1  →  evidence 1/3 (A=0.3333)  →  REPLAN  → switch strategy
            (new step PHASE-001-R1, attempt 2)
Attempt 2  →  evidence 3/3 (A=1.0000)  →  ACCEPT  → continue to next step
```

`bound inspect <run_id>` renders this as:

```text
Run run_feb444156b42b838db38
Task: Add input validation to the registration endpoint
Status: completed

Step 1 · Implement input validation · replanned
└── Attempt 1 · REPLAN · 1/3 checks
    └── Outcome: switched strategy to validator + parametrized tests
        Action: replan (REPLANNED)
        Score S=0.3333 (A=0.33 I=0.00 R=0.00 C=0.00) T=0.7000

Step 2 · Implement input validation (replan) · completed
└── Attempt 2 · ACCEPT · 3/3 checks
    └── Outcome: continued to next step
        Action: continue (CONTINUED)
        Score S=1.0000 (A=1.00 I=0.00 R=0.00 C=0.00) T=0.7000
```

The event log is append-only: a replan emits a *new* `step_started` with
`attempt+1` (and a `-R<N>`-suffixed contract id); the earlier attempt's history
is never rewritten. A missing `run_finished` marks an incomplete/crashed run
(the log stays readable). Prompts, tokens, and source code are **never** stored
— only contract id, scores, threshold, decision, reason code, and your follow-up
action/note.

To disable lineage entirely (CI, ephemeral environments), set
`BOUND_LINEAGE_DISABLED=1` or call `bound.configure(enabled=False)`.

## Done

When finished, summarize:
- the workflow mechanisms you inspected, used, and confirmed to exist,
- the files you created/modified,
- the `bound-policy` version you installed,
- one example `StepContract` + its decision from a real run,
- the resulting workflow action and final verification,
- confirmation that no evidence was fabricated and no BOUND policy logic was
  duplicated.

For a significant run, write
`bound_integration/INTEGRATION_REPORT.md`. Preserve the stable plan ids and
record planned versus actual outcome, real and unavailable evidence, decisions,
score data exposed by BOUND, retries/replans, resulting actions, deviations,
produced and unexpected artifacts, and final verification. Never invent token,
runtime, cost, or other metrics the environment does not expose.

For every evaluated phase, the report must also contain:

```text
Execution configuration:
    Agent:
    Model:
    Model settings:
    Bound-policy version:
    Retry budget:
    Tool-call budget:
    Token budget:
    Runtime budget:

Observed consumption:
    Retries:
    Tool calls:
    Tokens used:
    Runtime:

BOUND evaluation:
    Acceptance (A):
    Influence (I):
    Risk (R):
    Cost (C):
    Score (S):
    Threshold (T):
    Decision:
    Next action:

Consistency check:
    Previous integration record:
    Re-evaluation performed:
    Recorded values match:
    Missing/unobservable fields:
```

`Agent`, `Model`, and model settings are run metadata rather than BOUND
policy inputs. Record them when exposed so benchmark runs remain comparable.
Use `unavailable` for anything the environment does not expose. Distinguish
an unset budget (`None`) from unavailable observed consumption and from a
measured value of zero.

Remember: **BOUND decides whether to continue, retry, replan, or rollback.
BOUND does not decide what code to write.**
