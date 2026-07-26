# BOUND v0.8.1 → v0.9.0 Integration & Extension Audit

## Executive Summary

This audit covers the entire BOUND codebase at `src/bound/` and `integrations/`,
identifying every instance of logic duplication, API inconsistency, consolidation
opportunity, and routing violation. The findings are organised by audit area and
prioritised by impact on the v0.9.0 goals: a clean public extension API,
eliminated duplication, and one unified pipeline for everything.

---

## 1. Duplication Audit — Exact Logic Duplicated

### 1.1 `sha256_hex` / `_sha256_hex` — TRIPLICATED

| Location | Signature | Prefix |
|---|---|---|
| `bound/lineage.py:325` | `sha256_hex(data: str \| bytes) -> str` | `"sha256:"` |
| `bound/command_collector.py:88` | `sha256_hex(data: bytes) -> str` | `"sha256:"` |
| `bound/policy_canon.py:41` | `_sha256_hex(data: str \| bytes) -> str` | bare hex |

**Severity: HIGH.** Three identical implementations with subtle signature
differences. `command_collector.py` only accepts `bytes`; `lineage.py` accepts
both but re-encodes `str`. `policy_canon.py` produces bare hex without the
`"sha256:"` prefix, requiring callers to add it manually.

**Fix:** Consolidate into a single `bound.hashing` module with two functions:
`sha256_hex(data)` → `"sha256:<hex>"` and `sha256_hex_bare(data)` → `<hex>`.

### 1.2 `_normalize_capped` — DUPLICATED

| Location | Lines | Context |
|---|---|---|
| `bound/contract_evaluator.py:58-75` | 18 lines | Budget dimension normalisation |
| `bound/workflow.py:57-73` | 17 lines | WorkflowNormalization scaling |

**Severity: HIGH.** Byte-for-byte identical logic. Both implement "cap ≤ 0 →
1.0 if value>0 else 0.0; else min(value/cap, 1.0)."

**Fix:** Extract into `bound.calculator` (already the pure-math module) as a
public utility `normalize_capped()`.

### 1.3 `compute_contract_hash` — DUPLICATED

| Location | Signature | Hash format |
|---|---|---|
| `bound/lineage.py:355` | `compute_contract_hash(contract: BaseModel \| dict \| str) -> str` | bare 64-char hex |
| `bound/policy_canon.py:135` | `compute_contract_hash(contract: BaseModel \| dict[str, Any] \| str) -> str` | bare 64-char hex |

**Severity: MEDIUM.** Both produce the same bare-hex format and both canonicalise
through sorted-keys JSON. `policy_canon.py:135` explicitly documents it as
"consistent with `bound.lineage.compute_contract_hash`". Same algorithm,
different code paths.

**Fix:** Keep one implementation. `policy_canon.py` should import from
`lineage.py` or both should import from a shared `bound.hashing`.

### 1.4 Decision-to-Action Mapping — DUPLICATED

| Location | Name | Values |
|---|---|---|
| `bound/integration.py:28` | `_DECISION_TO_ACTION: dict[str, NextAction]` | ACCEPT→continue, RETRY→retry, REPLAN→replan, ROLLBACK→rollback |
| `bound/integration_spec.py:6` | `_DECISION_TO_CONTROL: dict[str, str]` | Same values, `str` instead of `NextAction` |
| `bound/lineage_api.py:47` | `_DECISION_TO_EVAL_REASON: dict[str, ReasonCode]` | ACCEPT→ACCEPT, RETRY→RETRY, ... |
| `bound/lineage_api.py:55` | `_ACTION_TO_OUTCOME_REASON: dict[str, ReasonCode]` | continue→CONTINUED, retry→RETRIED, ... |
| `bound/cli.py` | `_DECISION_COLORS` | ACCEPT→green, RETRY→amber, ... |

**Severity: MEDIUM.** The code comments claim `_DECISION_TO_ACTION` in
`integration.py` is the "single runtime source", but `integration_spec.py`
duplicates it. The lineage_api mappings are derived but scattered.

**Fix:** Consolidate all decision/action/reason mappings into a single
`bound.decisions` module, imported everywhere else.
---

## 2. Policy System Audit

| Module | Role | Lines | Health |
|---|---|---|---|
| `bound/policy_schema.py` | YAML config schema, loading, validation | 632 | Clean — single responsibility |
| `bound/policy_canon.py` | Canonicalisation, hashing, change detection | 175 | Duplicates hashing (§1.1, §1.3) |
| `bound/policy.py` | Runtime decision engine (BoundPolicy) | 358 | Clean — thin over calculator + evaluator |

**Verdict: No merge needed.** Correctly separated. Only fix: `policy_canon.py`
should import `sha256_hex` and `compute_contract_hash` from shared `bound.hashing`.

---

## 3. Evaluation Logic Audit

```
evaluator.py (117 lines)     — Evaluator Protocol + StaticEvaluator
calculator.py (111 lines)    — calculate_bound_score(), calculate_components()
contract_evaluator.py (1906) — ContractEvaluator: v0.3 contract→scores pipeline
workflow.py (547 lines)      — CodingWorkflowEvaluator: OLD v0.2 direct-signal
bound_workflow.py (256)      — BoundWorkflow orchestrator
```

**Two competing evaluators — Severity: HIGH**

- **CodingWorkflowEvaluator** (v0.2): Raw `CodingWorkflowSignals` → hardcoded
  heuristics → `EvaluationScores`. Used by `services.py:EvaluateWorkflow` and
  `runtime.py:BoundRuntime.evaluate()` default path.
- **ContractEvaluator** (v0.3): `StepContract` + `ExecutionEvidence` +
  `BoundPolicyConfig` → `EvaluationScores` + `AssuranceAssessment` +
  `PolicyGateOutcome`. Used by `BoundWorkflow.evaluate_step()` and
  `integration.evaluate_agent_step()`.

The CLI `bound evaluate-workflow` routes through the v0.2 path. The v0.3
contract pipeline is only reached through `BoundaryService`, `integration.py`,
and `bound watch`.

**Recommendation:** Deprecate `CodingWorkflowEvaluator`, route everything
through `BoundWorkflow.evaluate_step()`. Keep old evaluator as compat shim.

---

## 4. Lineage Audit

| Module | Role | Lines | Health |
|---|---|---|---|
| `bound/lineage.py` | Event models, IDs, hashing, parsing | 1262 | Clean — fix: extract hash fns |
| `bound/lineage_store.py` | Disk persistence, replay, retention | 1226 | Clean |
| `bound/lineage_api.py` | RunContext, start_run, record_step | 729 | Clean |

**Verdict: No merge needed.** Domain → infra → application is correct.

---

## 5. Collector Pipeline Audit

```
collectors.py (919 lines)
    — Stateless parsers: parse_pytest_summary(), parse_git_status_porcelain(), etc.

command_collector.py (1194 lines)
    — Stateful runners: PytestCollector, GitCollector, BudgetCollector, etc.
    — Uses collectors.py parsers. Contains duplicated sha256_hex (§1.1).
```

---

## 6. Integration System Audit

| File | Role | Lines |
|---|---|---|
| `bound/integration.py` | Runtime bridge: evaluate_agent_step(), render_feedback() | 259 |
| `bound/integration_spec.py` | Static spec: integration_spec() — JSON-serialisable | 92 |

**Issues:**
1. Duplicated decision mapping (§1.4): `_DECISION_TO_ACTION` vs `_DECISION_TO_CONTROL`.
2. `render_feedback()` inline mapping uses local dict instead of canonical source.

**Integration installers (integrations/ directory):**
- 6 × `INSTALL_BOUND.md` (claude-code, cline, codex, generic, hermes-agent, kilo-code)
- Each ~690 lines, ~95% identical — differ only in agent name and disclaimers.
- **Severity: HIGH** for maintenance. Fix: template with placeholders.

---

## 7. Routing Audit — CLI, MCP, UI, Watch

### CLI (`bound/cli.py` — 2737 lines)
Routes through `bound.services`. Clean adapter.
- **Issue:** `bound evaluate-workflow` → v0.2 path. Migrate to v0.3.
- **Issue:** Display constants `_DECISION_COLORS`, `_PROVENANCE_STRENGTH`,
  `_INDEPENDENTLY_VERIFIED` (lines 1055-1081) — extract to shared module.

### MCP Server (`bound/mcp_server.py` — 616 lines)
**Clean.** All ops through service layer. Thin adapter, no duplication.

### UI (`bound/ui.py` — 1019 lines)
**CRITICAL:** Imports private symbols from `bound.cli`:
```python
from bound.cli import (
    _DECISION_COLORS, _INDEPENDENTLY_VERIFIED, _PROVENANCE_COLORS,
    _fmt_dt, _html_escape, _RunAuditIndex, _sv,
)
```
All `_`-prefixed — encapsulation violation. **Fix:** Extract `bound.display`.

### Watch (`bound/watch.py` — 766 lines)
Routes through `BoundaryService`. Clean.
- **Issue:** `_build_criteria()` hardcodes `threshold=0.6`.

---

## 8. Public API Audit — Key Findings

### Events duplication (`events.py` vs `lineage.py`) — Severity: HIGH
Two separate event type systems for same vocabulary. `events.py` (245 lines, 9
events) mirrors `lineage.py` event types with simpler fields (string timestamps,
`frozen=True`, no `sequence`/`parent_event_id`).

**Fix:** Make `events.py` the public façade with `from_lineage_event()` converters.
Lineage events stay internal.

### Watch events (`events_watch.py`)
Separate watch-event system (229 lines). Different protocol (agent→watcher
stdin). Having separate types is correct — no duplication to fix.

### Runtime API (`runtime.py`)
Public API (461 lines). Clean wrapper. **Issue:** Default path uses old
`CodingWorkflowSignals(test_pass_rate=1.0)` — should route through
`BoundWorkflow.evaluate_step()`.

---

## 9. Entry Points — `pyproject.toml`

Current: only `[project.scripts]` with `bound = "bound.cli:main"`.

**Needed for v0.9.0 plugin discovery:**

```toml
[project.entry-points."bound.collectors"]
pytest = "bound.command_collector:PytestCollector"
git = "bound.command_collector:GitCollector"
junit = "bound.command_collector:JUnitCollector"
budget = "bound.command_collector:BudgetCollector"
process_runtime = "bound.command_collector:ProcessRuntimeCollector"
command = "bound.command_collector:CommandCollector"

[project.entry-points."bound.evaluators"]
contract = "bound.contract_evaluator:ContractEvaluator"
workflow = "bound.workflow:CodingWorkflowEvaluator"

[project.entry-points."bound.runtimes"]
default = "bound.runtime:BoundRuntime"
```
---

## 10. Unified Collector Pipeline Design

### Proposed flow for v0.9.0:
```
Policy YAML → CollectorRegistry.discover(policy_config)
                  ↓
         CollectorPipeline.run_all(contract, context)
                  ↓
    ┌─────────────┼─────────────┐
    │  for each enabled collector │
    │  collector.collect()        │
    │  → list[CheckEvidence]      │
    └─────────────┼─────────────┘
                  ↓
         ExecutionEvidence (aggregated)
                  ↓
         BoundWorkflow.evaluate_step()
```

### New public types:

```python
class CollectorResult(BaseModel):
    """Result from one collector run."""
    collector_name: str
    collector_version: str
    check_id: str
    evidence: CheckEvidence
    elapsed_ms: float

class CollectorPipeline:
    """Runs all policy-enabled collectors and aggregates evidence."""

    @classmethod
    def from_policy(cls, policy: BoundPolicyConfig) -> CollectorPipeline: ...

    def run(
        self, *, step_id: str, cwd: Path | None = None
    ) -> tuple[ExecutionEvidence, list[CollectorResult]]: ...
```

---

## 11. Public Extension API Design

### Stable public API (semver guarantees):
```
BoundRuntime          — from bound.runtime
EvaluationContext     — from bound.runtime
RunHandle             — from bound.runtime
FinishRunResult       — from bound.runtime
OutcomeRecordContext  — from bound.runtime
OutcomeResult         — from bound.runtime
BoundEvent            — from bound.events (public façade)
parse_bound_event     — from bound.events
EvaluationResult      — from bound.models
EvaluationScores      — from bound.models
BoundCriteria         — from bound.models
Decision              — from bound.models
evaluate_agent_step   — from bound.integration
AgentControlResult    — from bound.integration
render_feedback       — from bound.integration
integration_spec      — from bound.integration_spec
BoundPolicyConfig     — from bound.policy_schema
load_policy_yaml      — from bound.policy_schema
parse_policy_yaml     — from bound.policy_schema
CollectorPipeline     — from bound.collectors (NEW)
CollectorResult       — from bound.collectors (NEW)
RunContext            — from bound.lineage_api
start_run             — from bound.lineage_api
finish_run            — from bound.lineage_api
```

### Compatibility guarantees:
1. Public API types follow semver. Field additions backward-compatible.
2. Entry point groups are stable. New groups = minor bump.
3. `_`-prefixed names and non-listed modules may change without notice.
4. Event types carry `schema_version` for migration support.

---

## 12. Prioritised Refactoring Order

| # | Task | Priority | Risk | Depends On |
|---|---|---|---|---|
| 1 | Create `bound/hashing.py` — extract `sha256_hex`, `compute_contract_hash` | HIGH | Low | None |
| 2 | Extract `normalize_capped` to `bound/calculator.py` | HIGH | Low | None |
| 3 | Create `bound/decisions.py` — consolidate decision mappings | HIGH | Low | None |
| 4 | Create `bound/display.py` — extract display constants | MED | Low | None |
| 5 | Fix `ui.py` private imports | MED | Low | #4 |
| 6 | Add entry points to `pyproject.toml` | HIGH | Low | None |
| 7 | Resolve `events.py` vs `lineage.py` duplication | HIGH | Med | #1 |
| 8 | Deprecate `CodingWorkflowEvaluator`, unify evaluation paths | HIGH | High | #2, #3 |
| 9 | Template integration INSTALL_BOUND.md files | MED | Low | None |
| 10 | Create `CollectorPipeline` unified collector runner | HIGH | Med | #6 |
| 11 | Create `CollectorRegistry` with entry point discovery | HIGH | Med | #6 |
| 12 | Add conformance test CI | LOW | Low | None |

---

## Appendix A: Symbol Duplication Heat Map

```
sha256_hex:            lineage.py ★ | command_collector.py ★ | policy_canon.py ★
_normalize_capped:     contract_evaluator.py ★ | workflow.py ★
compute_contract_hash: lineage.py ★ | policy_canon.py ★
DECISION_TO_ACTION:    integration.py ★ | integration_spec.py ★ | lineage_api.py (derived)
_PROVENANCE_STRENGTH:  cli.py ★ | services.py (imported) | ui.py (private import)
_INDEPENDENTLY_VERIFIED: cli.py ★ | ui.py (private import)
_UNVERIFIED_PROVENANCE: cli.py ★
_SCHEMA_VERSION:       events.py ★ | events_watch.py ★ | lineage.py ★

★ = proposed canonical source
```

---

## Appendix B: Dependency Graph (simplified)

```
models ← evidence ← contracts
                ← collectors (stateless parsers)
                ← command_collector (runtime collectors)

evaluator (Protocol) ← workflow (CodingWorkflowEvaluator)
                     ← policy (BoundPolicy)
                     ← contract_evaluator (ContractEvaluator)

calculator ← policy ← contract_evaluator (normalization)

policy_schema ← policy_canon ← contract_evaluator (gate types)

contract_evaluator ← bound_workflow ← integration ← services

lineage ← lineage_store ← lineage_api ← services

services ← cli, mcp_server, watch, runtime, ui
```

---

*Audit completed: 2025-07-20. Refactoring Phase 1 begins with `bound/hashing.py`.*