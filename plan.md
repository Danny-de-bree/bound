# BOUND v0.9.1 → v1.0.0

## Goal

Deliver a production-ready deterministic execution harness that any coding
agent can use. BOUND evaluates completed work and emits one of four decisions:
ACCEPT, RETRY, REPLAN, or ROLLBACK.

## v1.0 Phases (from todo.md § 22.1)

- [x] **Phase A: Inspection & gap analysis** — `gap_analysis.md` created.
- [x] **Phase B: Data model & configuration** — Pydantic config models, capability dataclasses.
- [x] **Phase C: Agent discovery & explicit overrides** — detect Cline/Claude/Codex, CLI flags.
- [x] **Phase D: `bound use` / `bound status`** — UX commands implemented.
- [x] **Phase E: Plan model & snapshots** — PlanVersion, immutable snapshots, plan parser enhanced.
- [x] **Phase F: Runtime event linkage** — 12 plan-step events, auto-discovery, implicit fallback.
- [x] **Phase G: Cline zero-friction integration** — MCP session lifecycle tools, honest README.
- [x] **Phase H: First supervised agent** — supervised runner for process-based agents.
- [ ] **Phase I: UI plan/run navigation** — plan vs reality views (deferred to v1.1).
- [ ] **Phase J: Worktrees & candidates** — candidate branching (deferred to v1.1).
- [x] **Phase K: Docs & E2E validation** — README with capability matrix, CLI reference.

## Summary

- **1567 tests pass**, lint clean.
- **P0 complete**: capability model, config, agent detection, bound use/status, CLI flags.
- **P1 complete**: plan model, immutable snapshots, plan events, Cline MCP, honest README.
- **P2 partial**: supervised runner created, UI/worktrees deferred to v1.1.
- New files: `config.py`, `agent_discovery.py`, `plan_model.py`, `test_config.py`, `test_plan_model.py`.
- Enhanced: `services.py` (SessionService + 7 models), `mcp_server.py` (7 new tools), `cli.py` (bound use/status commands), `lineage.py` (12 plan events), `plan_parser.py` (front matter + step parsing), `adapters/protocol.py` (AgentCapabilities + AgentInstallation Pydantic models).
