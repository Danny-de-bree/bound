---
name: bound
description: Integrate and operate BOUND, the deterministic bounded-utility control policy for agent workflows. Use when an agent must install bound-policy, define StepContracts, collect real ExecutionEvidence, evaluate meaningful execution boundaries, react to ACCEPT/RETRY/REPLAN/ROLLBACK, audit an existing BOUND integration, or produce a reproducible numeric integration report.
---

# BOUND

Use BOUND after meaningful execution steps to decide whether the agent should
continue, retry, replan, or roll back. BOUND evaluates completed work; it does
not decide what code to write.

## Inspect before integrating

1. Install the latest stable package into the project's environment:

   ```bash
   pip install bound-policy
   ```

2. Inspect the installed API instead of relying on remembered signatures:

   ```bash
   python -c "import bound; print(bound.__version__); print(bound.__all__)"
   bound integration-spec
   ```

3. Confirm the project's real test, lint, type-check, and build commands.
4. Inspect the agent's actual hooks, modes, instructions, commands, task
   boundaries, telemetry, and checkpoint support. Do not invent a native BOUND
   integration or an undocumented framework mechanism.
5. Find existing `INTEGRATION.md`, `INTEGRATION_REPORT.md`, `PLAN.md`, and
   `bound_integration/` files. Treat recorded results as claims to reproduce,
   not authoritative evidence.

## Establish execution lineage

For multi-step work, maintain `PLAN.md` at the repository root. A small
one-step task may use an inline plan. Give each meaningful phase a stable id
and define its goal, observable acceptance checks, risk checks, verification
commands, budget, expected artifacts, owner, and dependencies where relevant.

Maintain this lineage:

```text
intent -> plan phase -> StepContract -> execution -> ExecutionEvidence
       -> BOUND evaluation -> control action -> integration report
```

Preserve a phase after execution. When the strategy changes materially, create
a derived id such as `PHASE-002-R1`; do not rewrite history to hide a replan.

## Choose meaningful boundaries

Call BOUND after implementation plus verification, after a focused retry, and
before deciding to keep refining the same objective. Do not call it after each
token, file read, shell command, or low-level tool call.

Each boundary must have observable completion criteria. Combine or redefine a
step when no meaningful success or risk signal can be observed.

## Report the proposal before modifying the project

Record these headings before implementation:

```text
Integration point:
Step boundary:
Available evidence:
Missing evidence:
Control-flow mapping:
Files to modify:
```

List exact verification commands and the small, removable set of integration
files. Represent unavailable metrics as unavailable rather than estimating
them.

## Build contracts and evidence

Map each phase to a `StepContract` with at least one `AcceptanceCheck`, optional
`RiskCheck`s, expected artifacts, and a `StepBudget`. Collect
`ExecutionEvidence` only from observations made during the run: command exit
codes, check results, changed artifacts, retries, tool calls, tokens, runtime,
and rollback availability when exposed.

Keep `check_id` values aligned between contract and evidence. Missing evidence
for a required check is not a pass. Distinguish an unset budget (`None`), an
unavailable measurement, and an observed zero.

Prefer the installed `evaluate_agent_step(...)` helper when its inspected API
provides it. Otherwise call `BoundWorkflow.evaluate_step(...)` and use exactly:

```text
ACCEPT   -> continue
RETRY    -> retry
REPLAN   -> replan
ROLLBACK -> rollback
```

Never add a fifth action or reproduce BOUND's scoring and decision formula.

## Verified evidence & provenance (v0.7.0)

BOUND v0.7.0 separates *who reports* evidence from *who verifies* it. The agent
is a participant, not the judge.

- **Define the contract upfront, including provenance requirements.** Each
  `AcceptanceCheck` / `RiskCheck` may declare `accepted_provenance` (which trust
  provenances it accepts), `on_missing`, `on_claimed`, and — for risk checks —
  `decision_critical` (missing/claimed evidence then forces `INSUFFICIENT`
  assurance and blocks a clean `ACCEPT`).
- **Configure allowed collectors; BOUND performs objective verification.** Use
  the BOUND collectors (`PytestCollector`, `GitCollector`, `JUnitCollector`,
  `BudgetCollector`, `ProcessRuntimeCollector`, `CommandCollector`) to *execute*
  verification BOUND controls. They record `evidence.collected` audit events with
  `VERIFIED`/`OBSERVED` provenance, a collector name/version, artefact hash and a
  timezone-aware timestamp. A collector crash or stale artefact is recorded
  honestly (`evidence.collection_failed` / `INVALID`), never a verified pass.
- **The agent CANNOT assign `VERIFIED` provenance.** Agent self-report is always
  `CLAIMED`, never `VERIFIED`/`OBSERVED`. The agent must not override or suppress
  a collector result. `MISSING` means "not collected" — never silently `0`; `None`
  on a telemetry metric is missing, not a measured zero.
- **Subjective criteria use a separate evaluator (`EVALUATED`).** Criteria that
  cannot be independently re-run (e.g. UX quality) are `EVALUATED` — honest but
  never `VERIFIED`.
- **BOUND logs what the agent reports; the agent executes control actions.**
  BOUND records `action.reported` (`reported_provenance=CLAIMED`); an independent
  hook may add `observed_action` to confirm it. **The agent — not BOUND — executes
  control actions and workspace rollback.** BOUND is a thin harness: it emits
  `ROLLBACK` and may independently verify the resulting state; it never performs a
  workspace rollback itself.
- **Assurance gates the candidate decision.** BOUND computes a
  `DecisionAssurance` (`VERIFIED`/`MIXED`/`CLAIMED`/`INSUFFICIENT`) from the
  decision-critical checks' provenance. A candidate `ACCEPT` backed only by
  `CLAIMED`/`MISSING` critical evidence is downgraded to the contract's
  `on_missing`/`on_claimed` action. Inspect it:

  ```bash
  bound inspect <run_id>                  # per-check provenance + candidate vs final + assurance + coverage
  bound inspect <run_id> --only-unverified # only unverified/claimed/missing/invalid evidence
  bound inspect <run_id> --json           # machine-readable: provenance + assurance + coverage
  ```

## Persist the numeric evaluation

For every evaluation, read the numbers from the returned BOUND object and emit:

```text
BOUND evaluation
Acceptance (A): <4 decimals>
Influence (I): <4 decimals>
Risk (R): <4 decimals>
Cost (C): <4 decimals>
Score (S): <4 decimals>
Threshold (T): <4 decimals>
Decision: <ACCEPT|RETRY|REPLAN|ROLLBACK>
Next action: <continue|retry|replan|rollback>
```

Also persist exposed weights, weighted components, retry margin,
rollback-risk threshold, and score provenance. Read
`references/integration-report.md` for the required report structure.

## Validate earlier scores

When an earlier integration record exists, compare its contract, evidence,
criteria, budgets, scores, decision, and action with the current run. When its
inputs are complete, reconstruct them and call BOUND again. Compare returned
A/I/R/C/S/T, decision, and action with the record.

- Mark incomplete historical results `not reproducible` and list missing data.
- Mark evaluations `stale` when evidence or configuration changed.
- Append a new evaluation instead of silently overwriting history.
- Treat a mismatch as a failed consistency check and investigate it before
  continuing.
- Do not independently implement the formula as a second evaluator.

## React to the result

- `ACCEPT`: stop refining this phase and continue.
- `RETRY`: preserve the strategy, make one focused correction, collect fresh
  evidence, and re-evaluate within the budget.
- `REPLAN`: stop the current strategy, choose a materially different approach,
  and create a derived contract id.
- `ROLLBACK`: restore only a previously confirmed safe checkpoint without
  discarding unrelated or pre-existing changes, then replan. If no safe
  checkpoint exists, report that and avoid a destructive approximation.

## Record decision lineage (v0.7.0)

BOUND v0.7.0 can record every evaluation as a reproducible, append-only local
lineage — `contract → evidence → scores → decision → agent outcome` — under
`.bound/runs/<run_id>/`. It is **opt-in per run** and backwards compatible: if
you never start a run, nothing is recorded. Disable it with
`BOUND_LINEAGE_DISABLED=1`.

For any non-trivial task you evaluate with BOUND, follow these rules:

1. **Start ONE run per task.**
   ```bash
   bound run start "<task>" --metadata phase=PHASE-001
   ```
   (In Python: `with bound.start_run("<task>") as run:`.)

2. **Use STABLE step/contract ids.** Carry the same id from `PLAN.md` →
   `StepContract(id=...)` → lineage. A replan of the same step appends `-R<N>`
   (`PHASE-001` → `PHASE-001-R1`); never invent an unrelated id or rewrite
   history to hide a replan.

3. **Evaluate only MEANINGFUL boundaries** (after implementation + verification,
   not after every token/file/command), and record the evaluation into the run:
   ```bash
   bound evaluate --run <run_id> --step PHASE-001 --attempt 1 \
       --action "..." --goal "..." \
       --acceptance A --influence I --risk R --cost C \
       --threshold 0.7 --retry-margin 0.1
   ```
   This writes `step_started` + `evaluation_recorded` and adds a `lineage`
   block to the JSON.

4. **Record the REAL follow-up action** you actually took — not what BOUND
   "should" have produced:
   ```bash
   bound outcome --run <run_id> --step PHASE-001 --attempt 1 \
       --decision REPLAN --note "switched strategy to validator + parametrized tests"
   ```
   `--next-action` and `--reason-code` are derived from `--decision`
   (`ACCEPT|RETRY|REPLAN|ROLLBACK`) when omitted.

5. **Explicitly CLOSE the run** when the task ends:
   ```bash
   bound run finish <run_id> --status completed|interrupted|failed --note "..."
   ```
   A missing `run_finished` marks an incomplete/crashed run (the log stays
   readable). If you used `with bound.start_run(...)` the context manager
   auto-finishes an interrupted run on exit.

6. **REPORT the local lineage path** — `.bound/runs/<run_id>/`
   (`run.json` + the append-only `events.jsonl`) — and show the decision tree:
   ```bash
   bound inspect <run_id>
   ```

The canonical flow is one run, two attempts:

```text
Attempt 1  →  evidence 1/3 (A=0.3333)  →  REPLAN  → switch strategy
            (new step PHASE-001-R1, attempt 2)
Attempt 2  →  evidence 3/3 (A=1.0000)  →  ACCEPT  → continue to next step
```

The event log is append-only: a replan emits a *new* `step_started` with
`attempt+1` and a `-R<N>`-suffixed contract id; earlier attempts are never
rewritten. Prompts, tokens, and source code are **never** stored — only the
contract id, scores, threshold, decision, reason code, and your follow-up
action/note. See `docs/lineage.md` for the full data model and API.

## Verify the integration

Add an end-to-end test that builds a real contract, collects real observed
evidence, calls BOUND, asserts one of the four decisions, and asserts the exact
control-flow mapping. Do not hardcode `ACCEPT` unless the evidence genuinely
satisfies the contract.

Keep all wiring thin and outside `src/bound/` in consumer repositories. Never
fabricate evidence, duplicate policy logic, or use an LLM as the final judge.
