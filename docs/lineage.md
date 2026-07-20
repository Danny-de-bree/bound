# Decision Lineage (BOUND v0.7.0)

BOUND v0.7.0 can record every evaluation as a reproducible, **append-only local
lineage** so the decision history of a run is auditable end to end:

```text
contract → evidence → scores → decision → agent outcome
```

Lineage is **opt-in per run** and fully backwards compatible. If you never start a
run, nothing is recorded. Disable it with `BOUND_LINEAGE_DISABLED=1`,
`bound.configure(enabled=False)`, or `LineageStore(enabled=False)`.

This document covers the [data model](#data-model), the
[Python API](#python-api), the [CLI](#cli), and
[what is and isn't stored](#what-is-and-isnt-stored).

## Data model

All models are Pydantic v2 (`extra='forbid'`), use timezone-aware UTC timestamps
(`UTCDateTime`; naive datetimes are rejected), and carry `schema_version="1.0"`
(`LINEAGE_SCHEMA_VERSION`). IDs are deterministic and reproducible
(truncated SHA-256, prefixed): `generate_run_id`, `generate_step_id`,
`generate_evaluation_id`, `generate_event_id`.

### Entities (current-state views)

| Entity | Key fields |
| --- | --- |
| `Run` | `run_id`, `task`, `started_at`, `finished_at`, `status` (`started`/`completed`/`interrupted`/`failed`), `step_ids[]`, `metadata` |
| `Step` | `step_id`, `run_id`, `contract_id`, `description`, `started_at`, `finished_at`, `status` (`started`/`completed`/`replanned`/`rolled_back`), `attempts[]` |
| `Attempt` | `attempt` (>=1), `started_at`, `evaluation_id` |
| `Evaluation` | `evaluation_id`, `run_id`, `step_id`, `attempt`, `scores` (`A/I/R/C`), `score`, `threshold`, `decision`, `reason_code`, `recorded_at` |
| `Outcome` | `run_id`, `step_id`, `evaluation_id`, `decision`, `next_action`, `reason_code`, `recorded_at`, `note` |

### Append-only events

`EVENT_NAMES` is the complete vocabulary, written in this order per run.
`LineageEvent` is the discriminated union; `parse_lineage_event(str|bytes|dict)`
parses any one line.

| Event | Key fields |
| --- | --- |
| `RunStartedEvent` | `run_id`, `task`, `metadata: dict[str,str]` |
| `StepStartedEvent` | `run_id`, `step_id`, `contract_id`, `attempt`, `description` |
| `EvaluationRecordedEvent` | `evaluation_id`, `run_id`, `step_id`, `attempt`, `scores`, `score`, `threshold`, `decision`, `reason_code` |
| `OutcomeRecordedEvent` | `run_id`, `step_id`, `evaluation_id`, `decision`, `next_action`, `reason_code`, `note` |
| `RunFinishedEvent` | `run_id`, `status` (`completed`/`interrupted`/`failed`), `reason_code`, `note` |

### Reason codes

`ReasonCode` is a `StrEnum` and the **only** admissible reason value on events
(there is no free-text `reason` field):

- Decision-derived: `ACCEPT`, `RETRY`, `REPLAN`, `ROLLBACK`
- Evaluation evidence: `ALL_CHECKS_PASSED`, `REQUIRED_CHECKS_FAILED`,
  `RISK_BOUNDARY_EXCEEDED`, `BELOW_THRESHOLD`, `WITHIN_RETRY_MARGIN`
- Outcome / control action: `CONTINUED`, `RETRIED`, `REPLANNED`, `ROLLED_BACK`
- Run lifecycle: `RUN_STARTED`, `RUN_COMPLETED`, `RUN_INTERRUPTED`, `RUN_FAILED`

### Multi-step / multi-attempt rule

A replan or retry emits a **new** `step_started` with `attempt+1` (and a
`-R<N>`-suffixed `contract_id` for replans, e.g. `PHASE-001` → `PHASE-001-R1`);
`evaluation_recorded` + `outcome_recorded` follow per attempt. History is
append-only — never rewritten. A missing `run_finished` marks an incomplete or
crashed run; storage keeps it readable.

`Decision` is `Literal["ACCEPT","RETRY","REPLAN","ROLLBACK"]` (uppercase
strings) and `NextAction` is `Literal["continue","retry","replan","rollback"]`
(lowercase strings). They are **not** enums — pass plain strings.

## Local storage

`LineageStore` writes to `.bound/runs/<run_id>/` by default: a `run.json`
metadata file and an append-only `events.jsonl` (one JSON event per line).
Writes are atomic (tmp + `os.replace`, fsync per line). Corrupt JSONL lines are
## Python API

```python
import bound

# start_run returns a RunContext (also a context manager that auto-finishes
# interrupted runs on exit).
with bound.start_run("Add input validation to the registration endpoint") as run:
    step = run.start_step(contract_id="PHASE-001", attempt=1,
                          description="Implement input validation")
    ev = run.record_evaluation(
        step_id=step.step_id, attempt=1,
        scores=bound.EvaluationScores(acceptance=0.3333, influence=0.0,
                                       risk=0.0, cost=0.0),
        score=0.3333, threshold=0.7, decision="REPLAN",
    )
    run.record_outcome(
        step_id=step.step_id, evaluation_id=ev.evaluation_id,
        decision="REPLAN", next_action="replan",
        note="switched strategy to validator + parametrized tests",
    )
    # ... attempt 2 (PHASE-001-R1) -> ACCEPT ...
    run.finish_run(status=bound.RunFinishStatus.COMPLETED,
                   reason_code=bound.ReasonCode.RUN_COMPLETED)
```

Module-level conveniences: `bound.record_outcome(run_id, *, step_id,
evaluation_id, decision, ...)` and `bound.finish_run(run_id, *, status=...,
note=None)`.

### Auto-instrumentation

`BoundWorkflow.evaluate_step(..., run=ctx)` automatically writes
`step_started` + `evaluation_recorded` + `outcome_recorded` when a run context
is supplied and the store is enabled, and is **unchanged** when `run` is `None`
(backwards compatible). `ContractEvaluator(run=ctx)` sets a default run;
explicit `run=` wins. Reason codes are derived deterministically: the evaluation
reason mirrors the decision; the outcome reason mirrors the control action.

## CLI

```bash
bound run start "<task>" [--metadata KEY=VALUE ...]   # prints run_id
bound evaluate --run <run_id> --step <contract_id> --attempt <n> \
    --action "..." --goal "..." \
    --acceptance A --influence I --risk R --cost C \
    --threshold T --retry-margin M        # records step_started + evaluation_recorded
bound outcome --run <run_id> --step <contract_id> --attempt <n> \
    --decision ACCEPT|RETRY|REPLAN|ROLLBACK [--note "..."]   # records the follow-up action
bound run finish <run_id> --status completed|interrupted|failed [--note "..."]
bound run list                              # newest-first table
bound inspect <run_id>                      # Step -> Attempt -> Outcome tree
bound run delete <run_id>
```

Every command accepts `--json` for machine-readable output. Exit codes: `0`
success, `1` run not found, `2` validation error. `BOUND_RUNS_DIR` overrides the
storage location (useful for tests/CI).

`bound inspect <run_id>` replays `events.jsonl` and renders the decision
lineage as a chronological tree: task, status, start/end time, each step and
attempt, the `ACCEPT/RETRY/REPLAN/ROLLBACK` decision, scores/threshold, reason
code, and the agent's recorded follow-up action. Incomplete runs are clearly
marked; truncated/corrupt lines are flagged.

## What is and isn't stored

**Stored** (under `.bound/runs/<run_id>/`): the task, stable contract/step ids,
attempt numbers, the four BOUND scores (`A/I/R/C`), the computed `score`,
`threshold`, `decision`, a fixed `reason_code`, and your recorded follow-up
`next_action` + `note`. Optional string `metadata` is allowed but scrubbed for
secrets by default (`scrub_secrets` masks `password`/`token`/`key` patterns).

**Never stored**: prompts, model outputs, tokens, source code, or anything not
in the lineage schema. Cost/token/runtime are only persisted if you put them in
`metadata` — they are not BOUND policy inputs and are not part of the schema.

## Disabling lineage

- Environment: `BOUND_LINEAGE_DISABLED=1`
- Python: `bound.configure(enabled=False)` or `LineageStore(enabled=False)`

When disabled, the store builders still construct and return typed events but
persist nothing, and callers that supply no run context are completely
unaffected.

## See also

- [`examples/lineage_demo.py`](../examples/lineage_demo.py) — runnable
  REPLAN → ACCEPT end-to-end demo.
- [`examples/lineage_demo_events.jsonl`](../examples/lineage_demo_events.jsonl) —
  a real captured 8-event log.
- [`docs/upgrade-guide.md`](upgrade-guide.md) — upgrading to v0.7.0.

skipped in lenient mode (`read_run`) and raised in strict mode
(`read_run(strict=True)`); a truncated final line is dropped and flagged.

```python
from bound import LineageStore

store = LineageStore(base_dir=".bound/runs", *, enabled=True,
                     redactors=[...], max_event_bytes=256_000,
                     max_file_bytes=16_000_000, stored_fields=None)
```

Builders (each constructs the event, persists it, and returns the typed event):
`start_run`, `start_step`, `record_evaluation`, `record_outcome`, `finish_run`.
Read/list/delete: `read_run(run_id) -> RunLog`, `list_runs() -> list[RunSummary]`
(newest first), `delete_run(run_id)`. Module-level: `get_default_store()`,
`configure(...)`, `register_redactor(fn)`, `scrub_secrets(dict)`.
