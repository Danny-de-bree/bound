# Results — Cline dogfooding experiment

> **STATUS: NO REAL RUN HAS BEEN PERFORMED. THESE ARE EMPTY TEMPLATES.**
>
> This directory is populated by a **real** Cline operator. The files
> `baseline_trajectory.json.template` and `bound_controlled_trajectory.json.template`
> are the exact schemas a real run fills in. To run the experiment, copy each
> template to its real result file and fill in only values that were **actually
> observed**. Leave any unobservable field as `null`.

## What is and is not here

- Present: the experiment protocol (`../README.md`), the task (`../task.md`),
  the machine-readable contract (`../expected_contract.json`), and **empty**
  trajectory templates.
- **Absent (and intentionally so): any real run output.** No baseline or
  BOUND-controlled trajectory has been recorded, because a real Cline session
  cannot be driven honestly inside this automated environment.
- **No improvement is claimed.** Nothing here asserts that BOUND-controlled
  beats baseline. A single experiment pair can only establish the *first
  reproducible real-agent trace*; it cannot establish improvement.

## How to run the experiment (for a human operator with real Cline)

### Prerequisites

- A real Cline installation (VS Code extension) with a working model.
- A clean checkout of the BOUND repository at a known commit.
- `bound-policy` installed (`pip install bound-policy`) and importable from the
  Cline workspace.

### Step 1 — Baseline trajectory

1. From a clean repo state, start a Cline session.
2. Give Cline **only** the task in `../task.md`. Do **not** give it the BOUND
   integration prompt. Do not mention BOUND.
3. Let Cline run until it stops on its own. Record the full transcript.
4. Once finished, capture the metrics below honestly. In particular:
   - Count every agent step until Cline stopped.
   - Record the point (step index) where all three required checks first passed.
   - Count **work performed after the task was already satisfactory** = steps
     taken after all three required checks were passing. This is the
     over-optimization the baseline is allowed to do.
5. Save the result as `baseline_trajectory.json` (copy the template).

### Step 2 — BOUND-controlled trajectory

1. Reset the repo to the identical clean state used for the baseline.
2. Start a fresh Cline session.
3. Paste the Cline integration prompt (`integrations/cline/INSTALL_BOUND.md`).
4. Let Cline inspect BOUND, build a `StepContract` from `expected_contract.json`,
   and run the **same** task with BOUND owning the decisions:
   - After each agent step, collect `ExecutionEvidence` from the real
     verification (tests / git-diff for the risk checks).
   - Evaluate with `evaluate_agent_step(contract, evidence, criteria)` (or
     `BoundWorkflow.evaluate_step`).
   - React to the mapped control action (`continue` / `retry` / `replan` /
     `rollback`). On `continue` (ACCEPT), **stop**.
5. Record the point where BOUND first returned ACCEPT, and confirm **zero** work
   was performed after that point.
6. Save the result as `bound_controlled_trajectory.json` (copy the template).

### Step 3 — Compare honestly

Fill in `comparison.md` from the two real trajectories. Report each metric for
both runs. If BOUND-controlled is no better (or worse) on any metric, say so
explicitly. Do not generalize from n=1.

## Scaffolding-time verification

At scaffolding time the `expected_contract.json` was loaded against the
installed public API and run through the deterministic pipeline. The recorded
decisions (REPLAN / RETRY / ACCEPT for 1 / 2 / 3 checks) were produced by the
real `ContractEvaluator` + `BoundPolicy` — **not** hardcoded. This proves the
contract scaffolding is correct and loadable, but it is **not** a Cline run.
