# Upgrade Guide

## v0.7.0 — Decision Lineage

BOUND v0.7.0 adds **Decision Lineage**: an optional, append-only local record of
every BOUND evaluation — `contract → evidence → scores → decision → agent
outcome` — stored under `.bound/runs/<run_id>/` (`run.json` + `events.jsonl`).

### It is opt-in and backwards compatible

Lineage **only activates when you explicitly start a run**. Existing integrations
that never call `bound.start_run(...)` and never pass `run=` to
`evaluate_step(...)` are completely unaffected — same return types, same values,
no new dependencies, no new side effects.

- `BoundWorkflow.evaluate_step(*, contract, evidence, criteria)` → unchanged
  when `run` is `None` (the default).
- `BoundWorkflow.evaluate_step(*, contract, evidence, criteria, run=ctx)` →
  additionally writes `step_started` + `evaluation_recorded` +
  `outcome_recorded` to the run's lineage. Return value is unchanged.
- `ContractEvaluator(run=ctx)` sets a default run for all its evaluations;
  explicit `run=` wins.

### Upgrading an agent integration

For any task you want auditable, wrap the work in one run:

```bash
bound run start "<task>"
# ... bound evaluate --run <run_id> --step <contract_id> --attempt <n> ...
# ... bound outcome --run <run_id> --step <contract_id> --attempt <n> --decision <...> ...
bound run finish <run_id> --status completed
bound inspect <run_id>
```

See the updated integration prompts (`integrations/*/INSTALL_BOUND.md`) and
`skills/bound/SKILL.md` — each now teaches: start one run per task, use stable
step/contract ids (`PHASE-NNN`, `PHASE-NNN-R1` after a replan), evaluate only
meaningful boundaries, record the real follow-up action, explicitly close the
run, and report the local lineage path.

### New public symbols

`bound.__all__` gained: `Run`, `Step`, `Attempt`, `Evaluation`, `Outcome`,
`RunStartedEvent`, `StepStartedEvent`, `EvaluationRecordedEvent`,
`OutcomeRecordedEvent`, `RunFinishedEvent`, `LineageEvent`, `parse_lineage_event`,
`ReasonCode`, `RunStatus`, `RunFinishStatus`, `StepStatus`, `EVENT_NAMES`,
`LINEAGE_SCHEMA_VERSION`, `UTCDateTime`, `generate_run_id`, `generate_step_id`,
`generate_evaluation_id`, `generate_event_id`, `utc_now`, `LineageStore`,
`RunLog`, `RunSummary`, `RunNotFound`, `LineageStoreError`, `LineageCorruptEvent`,
`LineageEventTooLarge`, `LineageFileTooLarge`, `DEFAULT_RUNS_DIR`,
`DEFAULT_MAX_EVENT_BYTES`, `DEFAULT_MAX_FILE_BYTES`, `get_default_store`,
`configure`, `register_redactor`, `scrub_secrets`, `start_run`, `RunContext`,
`record_outcome`, `record_step_evaluation`, `finish_run`.

### Disabling lineage

If you do not want any lineage written (CI, ephemeral environments):

- Set the environment variable `BOUND_LINEAGE_DISABLED=1`, or
- Call `bound.configure(enabled=False)`, or
- Construct `LineageStore(enabled=False)`.

When disabled, the builders still construct and return typed events but persist
nothing.

### Privacy

`.bound/runs/` should be in your `.gitignore`. Prompts, model outputs, tokens,
and source code are **never** stored — they are not part of the lineage schema.
Optional `metadata` is scrubbed for secrets by default (`scrub_secrets`). See
[`docs/lineage.md`](lineage.md) for what is and isn't stored.

### No breaking changes

No existing public symbol was removed or renamed. The deterministic policy,
score formula, decision rule, and `evaluate`/`evaluate-workflow` CLI commands are
unchanged. v0.7.0 only *adds* the lineage surface and the `run`/`inspect`/
`outcome`/`--run` CLI commands.
