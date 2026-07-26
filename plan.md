# BOUND v0.9.1 → v1.0.0

## Goal

Deliver a production-ready deterministic execution harness that any coding
agent can use. BOUND evaluates completed work and emits one of four decisions:
ACCEPT, RETRY, REPLAN, or ROLLBACK.

## v1.0 Phases (from todo.md § 22.1)

- [x] **Phase A: Inspection & gap analysis** — `gap_analysis.md` created. 24 implemented, 9 partial, 2 config-only, 14 planned.
- [x] **Phase B: Data model & configuration** — `.bound/config.yaml` schema, `ProjectConfig`, `AgentConfig`, capability model (Pydantic)
- [x] **Phase C: Agent discovery & explicit overrides** — detect Cline/Claude Code/Codex, `--agent`/`--agent-command` flags
- [x] **Phase D: `bound use` / `bound status`** — UX commands implemented
- [x] **Phase E: Plan model & snapshots** — `PlanVersion`, immutable snapshots, plan→run links, plan parser enhanced
- [x] **Phase F: Runtime event linkage** — 12 plan-step events appended to lineage, plan auto-discovery, implicit plan fallback
- [ ] **Phase G: Cline zero-friction integration** — `bound use cline` full flow (IN PROGRESS)
- [ ] **Phase H: First supervised agent** — real agent execution (IN PROGRESS)
- [ ] **Phase I: UI plan/run navigation** — plan vs reality views
- [ ] **Phase J: Worktrees & candidates** — candidate branching
- [ ] **Phase K: Docs & E2E validation** — README, CHANGELOG, capability matrix

## Legacy Plan (v0.9.x)

### PHASE-001: Dashboard UX

**Goal:** Make `bound ui` fast, intuitive, and informative.

- [x] Auto-open browser on `bound ui` (no `--open` flag needed)
- [x] Filter bar (All/Active/Completed/Failed) + search
- [x] Active runs grouped by task name
- [x] Hide empty runs (event_count <= 1)
- [x] Cache step_count + latest_decision in run.json
- [x] Startup cache warm (index all runs once)
- [x] No emoji — SVG icon system
- [x] Execution/Evidence/Artifacts/Replay tabs
- [x] Plan progress visualization
- [x] Suppress broken-pipe/connection-reset noise
- [ ] Plan.md visible on detail page (load plan from run metadata)
- [ ] Plan vs Reality diff view

### PHASE-002: Stable Runtime (v0.9.0)

**Goal:** One runtime, one execution pipeline.

- [x] BoundRuntime class
- [x] Candidate abstraction with git worktrees
- [x] Unified event model
- [x] Dedup: hashing, decisions, display modules
- [x] Python entry points for plugin discovery
- [x] CI/CD: checkout@v4, setup-uv@v5, Python 3.12 + 3.13 matrix

### PHASE-003: Native Agent Execution (v0.9.5)

**Goal:** BOUND actively controls coding agents.

- [ ] Agent adapter interface (generic, Codex, Claude Code, Cline)
- [ ] Lifecycle control loop (stream events → evaluate → execute)
- [ ] Candidate branching (REPLAN forks new candidates)
- [ ] Policy-driven branching config
- [ ] Deterministic candidate selection

### PHASE-004: Production (v1.0.0)

**Goal:** Production-ready execution harness.

- [ ] Benchmark runner (uses same runtime)
- [ ] Paired execution (with/without BOUND)
- [ ] Controller evaluation (false ACCEPT/RETRY/REPLAN/ROLLBACK)
- [ ] HTML/Markdown/JSON reports
- [ ] Compatibility guarantees + migration guide
- [ ] Release automation

## Acceptance

- [ ] `bound ui` opens instantly, shows plan progress, filters work
- [ ] Runtime is deterministic
- [ ] One native agent adapter works end-to-end
- [ ] Benchmark suite produces reproducible reports
- [ ] 1300+ tests pass, lint clean
