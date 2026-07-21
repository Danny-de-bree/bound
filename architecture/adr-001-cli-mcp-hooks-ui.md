# ADR-001: CLI, MCP, Hooks, Watch, and UI Architecture Separation

**Status:** Accepted (v0.8.0 Sprint 1)
**Date:** 2026-07-20
**Author:** Sprint Lead
**Source:** [task_0019 / S1-ARCH-1]

## Context

BOUND v0.7.1 ships a monolithic CLI (`bound.cli`) that handles evaluation, policy
management, run lifecycle, inspect, and lineage. v0.8.0 must bring BOUND **into
the agent loop** with four new surface areas:

1. **`bound watch`** — a daemon that detects meaningful-boundary events.
2. **`bound ui`** — a local read-only dashboard.
3. **`bound mcp`** — a stdio-based MCP server for typed tool-calling.
4. **`bound init`** — interactive policy scaffolding.
5. **Hooks (pre/post)** — git-hook-like lifecycle callbacks.

If each adapter duplicates evaluation, policy-loading, and lineage logic, we get
five inconsistent implementations. The CLI's `argparse.Namespace` glue is already
tightly coupled to `sys.stdout`/`sys.stderr`/`sys.exit`.

## Decision

Create **one shared application/service layer** (`bound.services`) that
encapsulates all orchestration logic behind typed request/response dataclasses.
Every adapter imports the same services and never reimplements business logic.

```text
                     ┌──────────────────┐
                     │  BOUND core      │
                     │  + services      │
                     │  (bound.services)│
                     └──────┬───────────┘
          ┌──────────────────┼──────────────────┐
          ↓                  ↓                  ↓
        CLI              local MCP          hooks/watch
          ↓                  ↓                  ↓
     (thin argparse     (thin stdio          (event-driven
      → service call)    → service call)      → service call)
                                                  ↓
                                              local UI
                                         (read-only HTTP
                                          → service call)
```

### Service layer contract

Every service method:

1. Accepts a typed **request** dataclass (or plain kwargs for simple lookups).
2. Returns a typed **response** dataclass (never `None`).
3. Raises a typed **error** (never `print`, `sys.exit`, or `logging` for control flow).
4. Imports the same domain models as every adapter.

Services never write to `stdout`/`stderr`, never call `sys.exit`, and never
parse `argparse.Namespace`.

### Adapter responsibilities

| Adapter       | Transport           | Responsibility                                    |
|---------------|---------------------|---------------------------------------------------|
| CLI           | `argparse` → stdio  | Parse argv, call service, format response to stdout/stderr, exit(code) |
| MCP           | stdio JSON-RPC      | Parse JSON-RPC call → service → JSON-RPC response  |
| `bound watch` | filesystem events   | Detect meaningful-boundary change → service evaluation → optional UI notif. |
| `bound ui`    | HTTP (localhost)    | Serve read-only dashboard via service calls for run/step/evaluation data |
| `bound init`  | interactive prompt  | Ask questions → call policy service → write `bound-policy.yaml` + display |

### Services defined (v0.8.0)

| Service              | Key methods                                          |
|----------------------|------------------------------------------------------|
| `PolicyService`      | `validate`, `explain`, `hash`, `init_template`       |
| `RunService`         | `start`, `finish`, `list_runs`, `delete`, `inspect`  |
| `EvaluationService`  | `evaluate`, `evaluate_workflow`, `boundary_evaluate`, `prepare` |
| `InspectService`     | `inspect_text`, `inspect_json`, `inspect_html`        |
| `CheckpointService`  | `create`, `restore`, `list` (scaffold; deferred to v0.8.x) |
| `WatchService`       | `setup`, `poll`, `shutdown` (scaffold)               |
                                                  ↓
                                              local UI
                                         (read-only HTTP
                                          → service call)
```


## Supported Platforms & Matrix

### Python versions

| Version | Support     | Notes                              |
|---------|-------------|------------------------------------|
| 3.12    | **Primary** | Required minimum (from pyproject.toml, requires-python = ">=3.12") |
| 3.13    | **Tested**  | Explicit classifier in pyproject.toml            |

All CI runs on both versions. The service layer uses only stdlib + pydantic/pyyaml;
no platform-specific C extensions are required.

### Operating systems

| OS      | Support    | Notes                                                  |
|---------|------------|--------------------------------------------------------|
| Linux   | **Primary**| CI runs on Ubuntu latest. bound watch uses inotify.  |
| macOS   | **Tested** | Darwin in CI. bound watch uses FSEvents or polling.  |
| Windows | **Community** | No CI gate. bound watch uses watchdog or polling.  |

### Agent integrations (v0.8.0 tested matrix)

| Agent / Framework | Integration type          | Status          |
|-------------------|---------------------------|-----------------|
| Cline             | Prompt + MCP              | Tested          |
| Claude Code       | Prompt + MCP              | Tested          |
| Kilo Code         | Prompt + MCP              | Tested          |
| Codex             | Prompt                    | Prompt          |
| Hermes Agent      | Prompt                    | Prompt          |
| Generic           | integration-spec + MCP    | Tested          |


## Canonical Scenario (frozen for v0.8.0)

The **P0 scenario** that every adapter must pass before v0.8.0 ships:

```
Agent: Cline (default: Claude Sonnet 4)
Project: A Python project with pytest tests, a linter config, and a README
Task: "Add input validation to the registration endpoint"

Step 1 -- The agent implements validation, runs tests -> 1/3 pass
         \u21d2 BOUND: REPLAN (far below threshold)

Step 2 -- The agent switches strategy, implements parametrized tests, fixes bugs
         \u21d2 BOUND: ACCEPT VERIFIED (all checks pass, independent evidence)
```

### Acceptance criteria for the scenario

1. [ ] `bound init` creates a valid bound-policy.yaml from project structure.
2. [ ] Agent reads INSTALL_BOUND.md and integrates BOUND without manual wiring.
3. [ ] First attempt produces REPLAN with evidence provenance CLAIMED + OBSERVED.
4. [ ] Second attempt produces ACCEPT with VERIFIED provenance (collectors run).
5. [ ] `bound inspect <run_id>` shows both decisions with full score breakdown.
6. [ ] `bound ui` opens and shows both attempts in the decision tree.
7. [ ] `bound watch` captures events within polling interval.
8. [ ] Demo can be recorded and replayed in < 5 minutes.

### Out of scope for P0 scenario

- Full rollback execution (scaffold only in v0.8.0).
- Multi-step plans beyond two attempts.
- LLM-based contract generation.
- Hosted trace storage.


## Backwards Compatibility with v0.7 Lineage

### Schema compatibility

The lineage event schema is **v2.0** (from v0.7.0). v0.8.0 does **not** bump the
lineage schema version. The service layer reads and writes the same event types:

- `run_started` / `step_started` / `evaluation_recorded` / `outcome_recorded` / `run_finished`
- `evidence.collected` / `evidence.collection_failed` / `decision.gated` / `action.reported`
- `policy.proposed` / `policy.validated` / `policy.approved` / `policy.activated`
- `evaluation.completed` / `action.observed` / `step.completed`

### Store format

The default store (`.bound/runs/` directory) is unchanged: one JSON-per-event,
append-only, with the same retention/compaction semantics. v0.8.0 runs are
interleavable with v0.7.1 runs in the same store directory.

### Policy schema

`bound-policy.yaml` schema **v1.0** is unchanged. The service layer uses the
same `BoundPolicyConfig` / `HardGate` / `WeightedSignal` / `BudgetDimension`
models. No new required fields. A v0.7.1 policy file loads into v0.8.0 without
migration.

### API surface

The public Python API (`bound.*`) retains every export from v0.7.1. Deprecated
class/function aliases remain accepted but emit deprecation guidance.

### What breaks (and is intentional)

| Change | Rationale |
|--------|-----------|
| CLI now calls services instead of inline logic | Internal refactor; behaviour identical for equivalent inputs |
| `services.py` owns orchestration, not `cli.py` | Enables MCP/hooks/watch/UI without the argparse dependency |
| `eval`/`eval-workflow` now raise typed errors instead of `sys.exit(2)` | Adapter catches and formats; MCP must not call `sys.exit` |
| `CheckpointService` raises `NotImplementedError` | Scaffold-only; calling code must handle |

### Migration path for v0.7 -> v0.8

1. No migration needed for policy files, lineage stores, or integration prompts.
2. Python scripts using `bound.evaluate(...)` / `bound.start_run(...)` continue
   to work (the module-level functions remain and delegate to services).
3. CLI users see identical output format for all existing subcommands.
4. New features (`bound ui`, `bound watch`, `bound mcp`, `bound init`) are
   additive -- they do not change existing command behaviour.


## Consequences

### Positive

1. **Single source of truth** for business logic -- five adapters share one codebase.
2. **Testable services** -- typed in/out means no mocking of `sys.argv` or `sys.stdout`.
3. **MCP is free** -- once services exist, the MCP adapter is a thin JSON-RPC bridge.
4. **Watch is clean** -- file-system events map directly to service calls, no argparse.
5. **UI is read-only** -- the dashboard calls `InspectService` and `RunService.list_runs`.

### Negative

1. **Short-term indirection** -- the CLI now calls a service that calls the same domain logic.
2. **Service method signatures must stabilise** before MCP and UI can commit to a protocol.
3. **`CheckpointService` is a scaffold** -- rollback execution is deferred to v0.8.x.

### Risks

| Risk | Mitigation |
|------|------------|
| Service layer grows too large | Split into focused service classes. |
| Adapters bypass services for "performance" | Code review gate: any adapter importing domain logic directly must be flagged. |
| MCP protocol changes after UI ships | MCP is `bound.services`-only; UI never depends on MCP protocol. |
| Watch event storms | Watch service deduplicates within a configurable cooldown window. |

## References

- todo.md: architecture decision section, service layer request/response models
- `src/bound/services.py` -- the service layer implementation
- `src/bound/cli.py` -- CLI adapter (being refactored to delegate to services)
- `src/bound/ui.py` -- UI adapter (already uses services for run listing)
- `src/bound/policy_schema.py` -- policy config schema (unchanged v1.0)
- `src/bound/policy_canon.py` -- canonical policy hashing (unchanged)
- `src/bound/lineage.py` -- lineage event schema (unchanged v2.0)
