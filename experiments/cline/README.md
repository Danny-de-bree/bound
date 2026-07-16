# Cline dogfooding experiment (BOUND v0.4, Phase 8)

> **Status: PROTOCOL DEFINED — REAL CLINE RUN PENDING.**
> This directory defines a reproducible experiment protocol and the
> contract/evidence scaffolding a real Cline run must populate. **No real
> Cline run has been performed in this environment.** The `results/` directory
> contains templates and instructions only. **No improvement is claimed from a
> single experiment** — the purpose (per the spec) is only to establish the
> first reproducible real-agent trace.

## What this is

This is the first BOUND dogfooding experiment: can a **real** coding agent
(Cline) install BOUND, evaluate meaningful execution boundaries, and change its
control flow based on **deterministic** evidence?

The experiment runs the **same** small coding task under two comparable
trajectories:

1. **Baseline** — an ordinary, uncontrolled Cline run. Cline does whatever it
   normally does until it decides to stop.
2. **BOUND-controlled** — Cline is given the Cline integration prompt
   (`integrations/cline/INSTALL_BOUND.md`), wires in BOUND, and runs the same
   task with BOUND owning the stop signal (ACCEPT) and the retry/replan/rollback
   decisions.

Both runs attempt the task described in [`task.md`](./task.md).

## The task

> Add robust input validation to a small API endpoint, including tests for
> valid, invalid, and edge-case input.

This task is chosen because it is small but non-trivial: it has an
unambiguous, deterministic definition of success (a test suite), it takes more
than one agent step in practice, and — critically — an over-eager agent will
keep "optimizing" past the point where the task is already satisfactory. That is
exactly the behaviour BOUND's ACCEPT decision is designed to stop.

## The contract

[`expected_contract.json`](./expected_contract.json) is a machine-readable
BOUND `StepContract` + `BoundCriteria`. **Its field names match the installed
`bound` Pydantic models exactly**, so a real Cline run can load it directly:

```python
import json
from bound.contracts import StepContract
from bound.models import BoundCriteria

with open("experiments/cline/expected_contract.json") as f:
    data = json.load(f)

contract = StepContract(**data["contract"])
criteria = BoundCriteria(**data["criteria"])
```

The three required acceptance checks are **identical** to the ones in
`examples/agent_control_loop.py` and the Cline integration prompt, so a real
Cline trace is directly comparable to the deterministic example:

| check id                  | meaning                                              |
|---------------------------|------------------------------------------------------|
| `valid_input_passes`      | Valid input is accepted and handled correctly.       |
| `invalid_input_rejected`  | Invalid input is rejected with an appropriate error. |
| `edge_cases_handled`      | Edge-case input is handled without crashing.         |

With `W_A=0.9`, `T=0.7`, `retry_margin=0.2` and three required checks, the
**deterministic** BOUND pipeline yields (no decision is hardcoded — these come
from `ContractEvaluator` + `BoundPolicy`):

| checks passing | score `S` | decision | control action |
|-----------------|-----------|----------|----------------|
| 1 / 3           | 0.30      | REPLAN   | replan         |
| 2 / 3           | 0.60      | RETRY    | retry          |
| 3 / 3           | 0.90      | ACCEPT   | continue       |

This was verified against the installed public API at scaffolding time (see
`results/README.md`).

## Metrics captured

For each trajectory the experiment captures (see `results/` templates):

- **task success** — did the final state satisfy all three required acceptance
  checks? (boolean + which checks passed)
- **tests** — number of tests added / passing / failing / removed
- **number of agent steps** — distinct agent actions / messages in the run
- **retries** — how many times the same strategy was retried
- **replans** — how many times a materially different approach was chosen
- **tool calls** — if observable in the agent environment
- **token usage** — if observable; left unset if not
- **runtime** — wall-clock seconds, if observable; left unset if not
- **point where BOUND returned ACCEPT** — the step index at which BOUND first
  returned ACCEPT (BOUND-controlled trajectory only; `null` for baseline)
- **work performed after the task was already satisfactory** — agent steps taken
  *after* the three required checks were all passing. For the BOUND-controlled
  trajectory this should be **zero** (ACCEPT stops the loop). For the baseline
  this is the over-optimization BOUND is meant to prevent.

## Honest scope and limitations

- **A live Cline run has not been performed in this automated environment.** An
  automated sandbox cannot realistically drive a real Cline VS Code extension
  session end to end, observe its transcripts, or read its token/tool telemetry.
  Performing the run honestly requires a human operator with a real Cline
  installation.
- **Do not claim improvement from one experiment.** A single baseline-vs-BOUND
  pair establishes *a reproducible trace*, not a statistically supported
  improvement. The results README must report whatever is observed, including
  the possibility that BOUND-controlled performed no better (or worse) on any
  single metric.
- **Unobservable signals stay unobservable.** Token usage, tool-call counts,
  and runtime are recorded **only** if the agent environment actually exposes
  them. Templates leave these as `null`; a real run must never fabricate them.
- **No LLM-as-judge.** Task success is determined by whether the deterministic
  test suite passes the three required checks — never by an LLM deciding
  "looks good".

## How the two trajectories stay comparable

To make the comparison fair and reproducible, both runs must:

- Use the **same** task (`task.md`) and the **same** endpoint under test.
- Use the **same** starting repository state (a clean checkout at the same
  commit). Run the baseline first, record the result, reset the repo, then run
  the BOUND-controlled trajectory from the identical state.
- Use the **same** Cline version and the **same** model / configuration.
- Be scored against the **same** `expected_contract.json`.

The only intended difference is whether BOUND owns the control loop.

## Related artifacts

- Task definition: [`task.md`](./task.md)
- Machine-readable contract: [`expected_contract.json`](./expected_contract.json)
- Cline integration prompt: `integrations/cline/INSTALL_BOUND.md`
- Deterministic comparable example: `examples/agent_control_loop.py`
- How to run + populate results: [`results/README.md`](./results/README.md)

