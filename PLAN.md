# BOUND v0.9.0 — Stable Runtime Implementation Plan

**Status:** In Progress  
**Target:** v0.9.0 ("Stable Runtime")  
**Architect:** Runtime Architect

---

## 1. Problem Statement

BOUND v0.8.1 has all the pieces of a deterministic decision harness but they are wired through multiple parallel paths. The v0.9.0 mandate: **one runtime, one execution pipeline, one decision path**.

## 2. Architecture: The Unified Runtime

See architecture/README.md for the v0.8.1 architecture. v0.9.0 replaces the scattered entry points with a single BoundRuntime that owns the policy config, lineage store, and default criteria. Every operation — evidence collection, evaluation, checkpoint capture, decision recording — flows through one pipeline.
### 2.1 `BoundRuntime` — The Single Public API

```python
class BoundRuntime:
    """The single public entry point for all BOUND operations."""

    @classmethod
    def from_policy(cls, path: str | Path) -> BoundRuntime: ...
    @classmethod
    def from_config(cls, config: BoundPolicyConfig) -> BoundRuntime: ...

    def create_candidate(self, task: str, *, ...) -> Candidate: ...
    def evaluate(self, context: EvaluationContext) -> EvaluationResult: ...

    policy_config: BoundPolicyConfig
    policy_hash: str
    lineage_store: LineageStore | None
```

### 2.2 `Candidate` — One Execution Attempt

A candidate owns a git worktree (isolated workspace), a RunContext (lineage
run), checkpoints, evidence, and decision history.

```python
class Candidate:
    workspace: Path          # The git worktree path
    run_id: str              # Lineage run id
    candidate_id: str        # Unique identifier

    def collect_evidence(self, contract: StepContract) -> ExecutionEvidence: ...
    def evaluate(self, contract, evidence, *, criteria=None) -> EvaluationResult: ...
    def capture_checkpoint(self, step_id: str) -> Checkpoint: ...
    def restore_checkpoint(self, checkpoint_id: str) -> Checkpoint: ...

    def __enter__(self) -> Candidate: ...   # creates worktree
    def __exit__(self, ...) -> None: ...     # removes worktree
```

### 2.3 Git-Native Execution Strategy

Every candidate gets an isolated git worktree under `.bound/worktrees/<id>/`.

Worktree lifecycle:
1. **Create:** `git worktree add --detach .bound/worktrees/<id> <base-commit>`
2. **Use:** Agent executes inside the worktree
3. **Checkpoint:** Records HEAD, diff, artifact hashes, untracked content
4. **Rollback:** `git checkout` + restore untracked from checkpoint
5. **Cleanup:** `git worktree remove --force` on context manager exit

### 2.4 Event Model (v3.0)

The public event schema (`bound/events.py`) and internal lineage schema
(`bound/lineage.py`) converge on a unified v3.0 vocabulary.

| Event tag | Lifecycle | When emitted |
|-----------|-----------|-------------|
| `run.started` | Run | New run created |
| `run.finished` | Run | Run completed/interrupted |
| `candidate.created` | Candidate | Worktree allocated |
| `candidate.destroyed` | Candidate | Worktree removed |
| `step.started` | Step | Step evaluation begins |
| `step.completed` | Step | Step evaluation completes |
| `evidence.collected` | Evidence | Collector produced evidence |
| `evidence.collection_failed` | Evidence | Collector failed |
| `evaluation.recorded` | Evaluation | A/I/R/C scores computed |
| `decision.gated` | Decision | Final decision emitted |
| `outcome.recorded` | Outcome | Decision + next_action recorded |
| `checkpoint.captured` | Checkpoint | State snapshot taken |
| `checkpoint.restored` | Checkpoint | State restored |
| `action.reported` | Agent | Agent claimed its action |
| `action.observed` | Agent | Independent observation |

All events carry: `schema_version`, `run_id`, `candidate_id`, `timestamp`
(ISO-8601 UTC), `sequence` (monotonic), `parent_event_id` (causal chain).

### 2.5 Evaluation Pipeline (Single Path)

```
Contract + Evidence
        │
        ▼
┌───────────────────┐
│ ContractEvaluator │  ← scores A/I/R/C (single source)
│ .evaluate()       │
└───────┬───────────┘
        │ EvaluationScores + provenance + assurance + policy_gate
        ▼
┌───────────────────┐
│ BoundPolicy       │  ← deterministic decision rule
│ .decide()         │     ROLLBACK > ACCEPT > RETRY > REPLAN
└───────┬───────────┘
        │ EvaluationResult
        ▼
┌───────────────────┐
│ Lineage recording │  ← step_started → evidence → eval → outcome
└───────────────────┘
```

This is the **only** evaluation path. The `services.py` paths
(`EvaluationService.evaluate`, `EvaluationService.evaluate_workflow`) are
deprecated and routed through this pipeline.
1. **Create:** `git worktree add --detach .bound/worktrees/<id> <base-commit>`
2. **Use:** Agent executes inside the worktree
3. **Checkpoint:** Records HEAD, diff, artifact hashes, untracked content
4. **Rollback:** `git checkout` + restore untracked from checkpoint
5. **Cleanup:** `git worktree remove --force` on context manager exit

Key rules:
- Never implement custom branching/versioning — always use git primitives
- Checkpoints record git state, not file copies (except untracked in-scope files)
- Worktree removal on candidate cleanup

## 3. Phased Implementation

### Phase 1: Foundation (Candidate + Worktree) — Days 1-2

**Goal:** Candidate abstraction with git worktree isolation exists and is tested.

**New files:**
- `src/bound/candidate.py` — `Candidate` class with worktree management

**Modified files:**
- `src/bound/runtime.py` — add `create_candidate()` method
- `src/bound/__init__.py` — export Candidate

**Tasks:**
1. Create `Candidate` class with: `workspace`, `run_id`, `candidate_id`,
   `_runtime` back-reference, `_run_context`, `_checkpoints` dict
2. Implement `Candidate._create_worktree()` using `git worktree add --detach`
3. Implement `Candidate._remove_worktree()` using `git worktree remove --force`
4. Implement `Candidate.__enter__` / `__exit__` context manager
5. Wire `BoundRuntime.create_candidate()` → new Candidate
6. Write tests: `tests/test_candidate.py`

### Phase 2: Unified Evidence Collection — Days 3-4

**Goal:** Evidence collection runs through the runtime pipeline.

**New files:**
- `src/bound/evidence_pipeline.py` — orchestrates collectors → ExecutionEvidence

**Modified files:**
- `src/bound/runtime.py` — `collect_evidence()` static/instance method
- `src/bound/candidate.py` — add `collect_evidence()` method

**Tasks:**
1. Create `EvidencePipeline` that reads collector config from BoundPolicyConfig,
   instantiates collectors with candidate workspace as cwd, runs them, aggregates
   into ExecutionEvidence
2. Each collector emits `evidence.collected` / `evidence.collection_failed` events
3. Wire `Candidate.collect_evidence(contract)` → ExecutionEvidence
4. Write tests: `tests/test_evidence_pipeline.py`

### Phase 3: Single Decision Pipeline — Days 5-6

**Goal:** All evaluations flow ContractEvaluator → BoundPolicy → Decision.

**Modified files:**
- `src/bound/runtime.py` — refactor `evaluate()` to use unified pipeline
- `src/bound/candidate.py` — add `evaluate()` method
- `src/bound/services.py` — deprecate old evaluation paths
- `src/bound/bound_workflow.py` — ensure single pipeline usage

**Tasks:**
1. Implement `Candidate.evaluate(contract, evidence, criteria)` that:
   - Runs ContractEvaluator.evaluate() → scores + provenance
   - Runs BoundPolicy.decide() → EvaluationResult
   - Records lineage events
2. Ensure `BoundWorkflow.evaluate_step()` uses same path
3. Ensure `evaluate_agent_step()` uses same path
4. Add deprecation warnings to `EvaluationService.evaluate()` paths
5. Write tests

### Phase 4: Event Model Unification — Days 7-8

**Goal:** Public and internal events share a common base schema.

**Modified files:**
- `src/bound/events.py` — v3.0 schema, new event types
- `src/bound/lineage.py` — consistency with events
- `src/bound/lineage_store.py` — handle new event types

**Tasks:**
1. Define `EventSchemaV3` base with: `schema_version: "3.0"`, `run_id`,
   `candidate_id`, `timestamp`, `sequence`, `parent_event_id`
2. Add `candidate.created`, `candidate.destroyed`, `checkpoint.captured`,
   `checkpoint.restored` event types
3. Update `BoundEvent` and `LineageEvent` discriminated unions
4. Write tests

### Phase 5: Checkpoint Integration — Days 9-10

**Goal:** Checkpoints captured/restored through Candidate in worktrees.

**Modified files:**
- `src/bound/checkpoint.py` — worktree-aware capture/restore
- `src/bound/candidate.py` — checkpoint methods

**Tasks:**
1. Update `capture_checkpoint()` to accept worktree path
2. `Candidate.capture_checkpoint(step_id)` wraps checkpoint capture
3. `Candidate.restore_checkpoint(checkpoint_id)` restores worktree state
4. Emit `checkpoint.captured` / `checkpoint.restored` events
5. Write tests

### Phase 6: Cleanup & Deprecation — Days 11-12

**Goal:** Remove dead code paths; ensure single pipeline everywhere.

**Modified files:**
- `src/bound/services.py` — route through BoundRuntime
- `src/bound/cli.py` — use BoundRuntime
- `src/bound/watch.py` — use BoundRuntime
- `src/bound/mcp_server.py` — use BoundRuntime

**Tasks:**
1. Audit all imports of `EvaluationService`, `CodingWorkflowEvaluator`
2. Route everything through `BoundRuntime`
3. Add deprecation warnings to old paths
4. Run full test suite; fix regressions

### Phase 7: Documentation & Conformance — Days 13-14

## 4. File Manifest

### New files:
| File | Purpose |
|------|---------|
| `src/bound/candidate.py` | Candidate class with worktree, evidence, evaluation |
| `src/bound/evidence_pipeline.py` | Orchestrates collectors → ExecutionEvidence |
| `tests/test_candidate.py` | Candidate unit/integration tests |
| `tests/test_evidence_pipeline.py` | Evidence pipeline tests |

### Modified files:
| File | Changes |
|------|---------|
| `src/bound/runtime.py` | Add `create_candidate()`, refactor `evaluate()`, unified pipeline |
| `src/bound/checkpoint.py` | Worktree-aware capture/restore |
| `src/bound/events.py` | v3.0 schema, new event types |
| `src/bound/lineage.py` | Converge with events schema |
| `src/bound/lineage_store.py` | Handle new event types |
| `src/bound/services.py` | Deprecate old evaluation paths |
| `src/bound/bound_workflow.py` | Ensure single pipeline |
| `src/bound/__init__.py` | Export Candidate, new types |
| `src/bound/cli.py` | Use BoundRuntime |
| `src/bound/watch.py` | Use BoundRuntime |
| `src/bound/mcp_server.py` | Use BoundRuntime |
| `integrations/conformance_test.py` | Use new API |
| `architecture/README.md` | Updated architecture |

### Unchanged (stable) modules:
| File | Reason |
|------|--------|
| `src/bound/models.py` | Stable domain models |
| `src/bound/contracts.py` | Stable contract types |
| `src/bound/contract_evaluator.py` | Stable (routed through pipeline) |
| `src/bound/calculator.py` | Pure math, no changes needed |
| `src/bound/policy.py` | Stable decision rule |
| `src/bound/policy_schema.py` | Stable config schema |
| `src/bound/policy_canon.py` | Stable canonicalization |
| `src/bound/evidence.py` | Stable evidence models |
| `src/bound/command_collector.py` | Stable collectors (cwd parameterized) |
| `src/bound/collectors.py` | Stable parsers |
| `src/bound/lineage_api.py` | Stable RunContext |
**Goal:** Public API documented; conformance test passes.

**Modified files:**
- `integrations/conformance_test.py` — use new API
- `architecture/README.md` — update architecture diagram

**Tasks:**
1. Write docstrings for all public classes/methods
2. Update conformance test to use `BoundRuntime.create_candidate()`
3. Update architecture docs
4. Write migration guide from v0.8.1 → v0.9.0
## 5. Design Decisions

### D1: Git worktrees, not copies
Git worktrees share the object database — no storage duplication. `git worktree remove` handles cleanup correctly, including pruning admin files.

### D2: Candidate owns workspace lifecycle
The context manager pattern (`with candidate:`) ensures worktrees are always cleaned up, even on exceptions.

### D3: Single evaluation pipeline
The v0.8.1 codebase has 3+ parallel evaluation paths. The unified pipeline ensures provenance, assurance assessment, and policy gates are always applied consistently.

### D4: Events drive replayability
Every operation emits a typed event. Given the initial state (git tree) and the event log, the entire run can be replayed deterministically.

### D5: No LLM in decision path
The deterministic guarantee is BOUND's core value proposition. LLM-based evaluation may happen in collectors, but the A/I/R/C → decision path is pure math.

## 6. Risk Register

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Breaking existing integrations | High | Keep old API with deprecation warnings during v0.9.x |
| Git worktree bugs (e.g., detached HEAD issues) | Medium | Extensive tests; fall back to in-repo execution |
| Performance regression from unified pipeline | Low | Same components, just routed consistently |
| Event schema migration breaks lineage replay | Medium | Version discriminator; old events still parseable |

## 7. Success Criteria

1. `BoundRuntime.from_policy("bound-policy.yaml")` creates a working runtime
2. `runtime.create_candidate(task)` creates an isolated git worktree
3. `candidate.collect_evidence(contract)` runs collectors in the worktree
4. `candidate.evaluate(contract, evidence)` returns deterministic decision
5. All lineage events are recorded and replayable
6. `candidate.__exit__` cleans up the worktree
7. Conformance test passes with new API
8. Existing test suite passes (deprecated paths still work)
9. No duplicated evaluation logic exists
10. All public types have Google-style docstrings