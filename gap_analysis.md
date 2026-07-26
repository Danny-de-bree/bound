# Gap Analysis: BOUND v1.0

**Date**: 2025-07-20
**Phase**: A — Inspection & Gap Analysis
**Base**: v0.9.1, Python 3.12+, UV package manager

---

## Summary

| Status | Count | Meaning |
|--------|-------|---------|
| **implemented** | 24 | Code exists AND has tests |
| **partial** | 9 | Some code exists but incomplete |
| **config-only** | 2 | Only config generation, no real agent interaction |
| **planned** | 14 | No implementation exists |

---

## 1. Current CLI Commands vs. Todo Requirements

### Existing top-level commands (all tested)

| Command | Status | Notes |
|---------|--------|-------|
| `bound evaluate` | implemented | Core evaluator, JSON output, lineage linkage |
| `bound evaluate-workflow` | implemented | Workflow signals → scores |
| `bound integration-spec` | implemented | Deterministic spec JSON |
| `bound run start/finish/list/delete` | implemented | Full lineage lifecycle |
| `bound run use` | implemented | Sets `.bound/current_run` (NOT agent selection) |
| `bound run current` | implemented | Shows current run |
| `bound inspect` | implemented | Text + HTML + JSON output |
| `bound ui` | implemented | Local HTTP dashboard |
| `bound outcome` | implemented | Record outcome events |
| `bound policy validate/explain/hash` | implemented | Policy management |
| `bound watch` | implemented | JSONL stdin event watcher |
| `bound checkpoint create/inspect/list` | implemented | Git-based checkpointing |
| `bound rollback` | implemented | Checkpoint rollback |
| `bound mcp` | implemented | Stdio MCP server |
| `bound init` | implemented | Tool detection + policy generation |
| `bound setup` | implemented | Full project setup flow |
| `bound doctor` | implemented | Read-only project diagnostics |
| `bound adapter install` | implemented | Adapter installation |
| `bound benchmark run/report/list` | implemented | Experiment harness |

### Required but MISSING commands

| Command | Status | Todo Ref |
|---------|--------|----------|
| `bound use <agent>` | planned | §§ 2.1, 2.4, 23.5 |
| `bound status` | planned | § 23.6 |
| `bound run --agent <name>` | planned | §§ 2.2, 23.3 |
| `bound run --agent-command <cmd>` | planned | §§ 23.3, 24 |
| `bound run --project <dir>` | planned | §§ 23.3, 24 |
| `bound run --plan <file>` | planned | §§ 23.3, 24 |

---

## 2. Detailed Feature Gap Table

### P0 — Must-have v1.0

| Feature | Todo Ref | Current State | Gap | Files Involved |
|---------|----------|---------------|-----|----------------|
| **Capability model** | § 23.1 | planned | No capability matrix, no adapter capability introspection. Adapters exist but don't declare their capabilities. | `adapters/__init__.py`, `adapters/protocol.py` |
| **`.bound/config.yaml`** | §§ 2.4, 23.2 | planned | No config file loading at all. `bound-policy.yaml` handles policy only. No `agent:` section, no `project_default`. | New: `config.py`, `cli.py` |
| **Explicit CLI flags `--agent`/`--agent-command`/`--project`/`--plan`** | §§ 23.3, 24 | planned | `bound run start` accepts `task` and `--metadata` only. No agent selection, no project override, no plan reference. | `cli.py` (lines ~391-467) |
| **Agent detection** | §§ 2.1, 23.4 | planned | `init_project.py` detects tooling (pytest, ruff, etc.) but NOT agent installations (Cline, Claude Code, Codex). No `AgentDetection` model exists. | `init_project.py`, new: agent detection module |
| **`bound use` (agent selection)** | §§ 2.1, 2.4, 23.5 | planned | No top-level `bound use` command. Existing `bound run use` sets lineage run, not agent. Name collision needs resolution. | `cli.py`, `setup.py` |
| **`bound status`** | § 23.6 | planned | No status command. `bound doctor` does health checks but doesn't show agent/project/runtime status as specified in § 2.1 output format. | `cli.py`, new: `status.py` |
| **`bound doctor`** | § 23.7 | implemented | Comprehensive checks: Python, version, policy, collectors, Git, checkpoints, integrations, lineage, stale config. 31 tests. | `doctor.py`, `cli.py` |

### P1 — Plan + Runtime Linkage

| Feature | Todo Ref | Current State | Gap | Files Involved |
|---------|----------|---------------|-----|----------------|
| **Plan, PlanVersion, Run links** | §§ 23.8, 24 | partial | `PlanSnapshot` exists in `plan_parser.py`. `PlanLoadedEvent` exists in `lineage.py` with `plan_version` field. `lineage_store.py` stores plan snapshots. BUT: no `PlanVersion` Pydantic model, no plan→runs query API, no versioning on re-parse. | `plan_parser.py`, `lineage.py`, `lineage_store.py`, `ui_models.py` |
| **Immutable plan snapshots** | §§ 23.9, 24 | partial | `PlanSnapshot` is hash-identified. `load_plan()` is deterministic. BUT: snapshots are not stored immutably alongside runs; they're re-parsed each time. No snapshot directory/store exists. | `plan_parser.py`, `lineage_store.py` |
| **plan.md auto-discovery** | § 23.10 | implemented | `load_plan()` searches `.bound/plan.md`, `plan.md`, `PLAN.md`, `Plan.md`. Also accepts `explicit_path`. | `plan_parser.py` |
| **Implicit plan fallback** | § 23.11 | planned | When no `plan.md` exists, no implicit plan is created. The todo requires an explicit "implicit plan instance" for runs without a plan. | `plan_parser.py`, `services.py` |
| **Plan runtime events** | § 23.12 | partial | `PlanLoadedEvent` exists in lineage schema. BUT: only fires on explicit load; no plan-step-started/completed events, no plan→step linkage events. | `lineage.py`, `lineage_api.py` |
| **Cline safe MCP installation** | § 23.13 | implemented | `ClineMCPAdapter.install()` creates `.cline/mcp/bound.json`. Safe merge: overwrites only `bound.json` (not other MCP configs). 20 tests. | `adapters/cline.py` |
| **Honest README capability labels** | § 23.14 | planned | Current README describes BOUND as policy evaluator, not as agent runtime. Needs "what BOUND can/cannot do" section per v1.0 vision. | `README.md` |

### P2 — Supervised Execution

| Feature | Todo Ref | Current State | Gap | Files Involved |
|---------|----------|---------------|-----|----------------|
| **First supervised agent execution** | §§ 23.15, 6.13 | config-only | `BoundRuntime.run_with_adapter()` exists with a control loop that spawns agent, waits for events, evaluates, sends commands. BUT: it requires an ACP-speaking agent. No real agent has ACP built-in. It's a reference implementation that works in tests but not with real Cline/Claude Code/Codex. | `runtime.py`, `adapters/generic.py`, `adapters/claude_code.py` |
| **Retry/replan fallback** | § 23.16 | partial | Control loop sends `retry`/`replan` commands via ACP. Claude Code adapter logs warnings for unsupported commands. Real agents don't implement these. | `runtime.py`, `adapters/claude_code.py` |
| **Worktree manager** | § 23.17 | implemented | `Candidate` class creates/destroys git worktrees. Context manager ensures cleanup. Has `capture_checkpoint`/`restore_checkpoint`. 23 checkpoint tests. | `candidate.py`, `checkpoint.py` |
| **Candidate model** | § 23.18 | implemented | `Candidate` + `CandidateDecision` models exist. Worktree, evidence, decisions all tracked. | `candidate.py` |
| **Plan vs reality UI** | § 23.19 | partial | `PlanProgress` view model exists in `ui_models.py` with divergence tracking (`PlanDivergenceType`). `ui.py` renders runs. BUT: no side-by-side plan-vs-execution view, no divergence visualization. | `ui.py`, `ui_models.py` |

### P3 — Advanced

| Feature | Todo Ref | Current State | Gap | Files Involved |
|---------|----------|---------------|-----|----------------|
| **Controlled session protocol** | § 23.20 | planned | ACP protocol exists but agents don't natively speak it. No adapter bridging real agent output → ACP. | `adapters/protocol.py` |
| **Third-party adapter entry points** | § 23.21 | planned | Adapters are hard-coded (Cline, Claude Code, Codex, Generic). No plugin/entry-point system. | `adapters/__init__.py` |
| **Candidate branching** | § 23.22 | planned | `Candidate` is single-worktree. No branching from a base candidate. | `candidate.py` |
| **Resume/reconnect** | § 23.23 | planned | No session persistence. No way to reconnect to a running agent. | None |
---

## 3. Backwards Compatibility Audit

Per § 22.4, these must be preserved:

| API | Status | Notes |
|-----|--------|-------|
| `bound adapter install` | implemented | 15 tests |
| `bound setup` | implemented | 47 tests |
| `bound mcp` | implemented | 31 tests |
| `bound watch` | implemented | In `test_watch.py` |
| `bound evaluate` | implemented | Core, ~31 CLI tests |
| `BoundRuntime.run_with_adapter` | implemented | 48 runtime tests |
| Existing lineage API | implemented | 62 lineage tests, `RunContext` facade |

## 4. Architecture Assessment

### Strengths
- Clean service layer: `services.py` with typed request/response models, no I/O in services
- Deterministic core: evaluator, calculator, contracts all pure functions
- Multiple event systems: internal lineage events, public watch events, AdapterEvent
- Git-based checkpointing with worktree isolation
- Stateless MCP server via stdio
- Lazy adapter loading for fast startup

### Key Gaps for v1.0
1. **No agent detection** — cannot discover installed agents
2. **No project configuration** — `.bound/config.yaml` does not exist
3. **No `bound use` / `bound status`** — key UX commands missing
4. **Plan model incomplete** — `PlanSnapshot` exists but no `PlanVersion`, no plan→runs linkage
5. **Supervised execution is config-only** — `run_with_adapter` works with ACP-speaking agents that don't exist
6. **CLI name collision** — `bound run use` (lineage) vs planned `bound use` (agent)

---

## 5. Test Coverage Summary

| Module | Test File | Tests | Lines |
|--------|-----------|-------|-------|
| CLI | test_cli.py | 31 | 789 |
| Runtime | test_runtime.py | 48 | 727 |
| Plan Parser | test_plan_parser.py | 17 | 188 |
| Setup | test_setup.py | 47 | 520 |
| Init Project | test_init_project.py | 48 | 530 |
| Cline Adapter | test_adapters_cline.py | 20 | 202 |
| Claude Code Adapter | test_adapters_claude_code.py | 18 | 209 |
| Codex Adapter | test_adapters_codex.py | 13 | 143 |
| Generic Adapter | test_adapters_generic.py | 22 | 329 |
| Lineage | test_lineage.py | 62 | 1052 |
| UI Models | test_ui_models.py | 22 | 340 |
| Bound Workflow | test_bound_workflow.py | 9 | 489 |
| Checkpoint | test_checkpoint.py | 23 | 714 |
| Services | test_services.py | 36 | 842 |
| MCP Server | test_mcp_server.py | 31 | 487 |

Plus: `test_doctor.py` (implicit in `test_cli.py`), `test_lineage_e2e.py`, `test_plan_events.py`, `test_v06_dod.py`, `test_v07_*`, `test_sprint*` — extensive regression suite.

---

## 6. Phase B Readiness

### Blockers: None — Phase A analysis is informational only.

### Key Decisions Needed Before Phase B
1. How to resolve `bound run use` vs `bound use` name collision?
2. Where `.bound/config.yaml` fits alongside existing `.bound/` structure (runs/, checkpoints/, plan.md)?
3. Agent detection strategy: filesystem probes? `which`/`npx` checks? Config file inspection?
4. Should `AgentDetection` be a new module or extend `init_project.py`?

### Recommended Phase B Scope
1. Create `.bound/config.yaml` schema + loader
2. Add `ProjectConfig` Pydantic model with `agent:` section
3. Wire config loading into CLI (shared by `bound use`, `bound status`, `bound doctor`)
4. Rename `bound run use` → `bound run activate` (deprecation path with warning)