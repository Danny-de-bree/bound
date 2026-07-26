# BOUND v0.9.5 — Native Agent Execution Framework

**Design Document · 2025-07-26**
**Status: Design Phase · Target: v0.9.5**

---

## Table of Contents

1. [Overview & Motivation](#1-overview--motivation)
2. [Architecture](#2-architecture)
3. [Agent Adapter Interface](#3-agent-adapter-interface)
4. [Lifecycle Control Loop](#4-lifecycle-control-loop)
5. [Execution Event Protocol](#5-execution-event-protocol)
6. [Candidate Branching Model](#6-candidate-branching-model)
7. [Policy-driven Branching Configuration](#7-policy-driven-branching-configuration)
8. [Candidate Selection Algorithm](#8-candidate-selection-algorithm)
9. [Adapter Control Protocol (ACP)](#9-adapter-control-protocol-acp)
10. [Reference Adapter Implementations](#10-reference-adapter-implementations)
11. [Execution Engine](#11-execution-engine)
12. [Backward Compatibility](#12-backward-compatibility)
13. [Implementation Roadmap](#13-implementation-roadmap)
14. [Module Layout](#14-module-layout)

---

## 1. Overview & Motivation

### 1.1 Current State (v0.8.x)

Today, BOUND evaluates steps and returns decisions (ACCEPT / RETRY / REPLAN / ROLLBACK),
but the **agent owns the control loop**. Integration patterns are:

| Pattern | How Agent Calls BOUND | Who Owns the Loop |
|---------|----------------------|-------------------|
| **Manual CLI** | `bound evaluate --action "..."` from the agent's shell | Agent |
| **MCP Server** | Agent calls `bound_evaluate` / `bound_checkpoint` tools via JSON-RPC | Agent |
| **Watch Mode** | Agent streams JSONL events to `bound watch` stdin | Agent (BOUND reacts) |
| **Python API** | `BoundRuntime.evaluate(...)` called from agent code | Agent |

In all cases: the agent decides *when* to call BOUND, BOUND returns a decision,
the agent decides *whether* to follow it. BOUND is a passive evaluator.

### 1.2 Target State (v0.9.5)

BOUND becomes an **active execution harness** that controls the agent:

```
┌──────────────────────────────────────────────────┐
│                   BOUND Runtime                    │
│                                                    │
│  ┌──────────┐   ┌──────────┐   ┌───────────────┐  │
│  │ Execution │   │ Candidate │   │   Candidate   │  │
│  │  Engine   │──▶│  Manager  │──▶│   Selector    │  │
│  └──────────┘   └──────────┘   └───────────────┘  │
│       │               │                │           │
│       ▼               ▼                ▼           │
│  ┌──────────────────────────────────────────────┐  │
│  │           Agent Adapter Interface             │  │
│  │  ┌─────────┐ ┌──────────┐ ┌───────┐ ┌──────┐ │  │
│  │  │  Codex   │ │  Claude  │ │ Cline │ │Generic│ │  │
│  │  │ Adapter  │ │   Code   │ │Adapter│ │Adapter│ │  │
│  │  └─────────┘ └──────────┘ └───────┘ └──────┘ │  │
│  └──────────────────────────────────────────────┘  │
│                                                    │
│  The adapter is a *child process* controlled by     │
│  BOUND via stdin/stdout JSONL (ACP).                │
└──────────────────────────────────────────────────┘
```

Key changes:

1. **BOUND starts and controls** the agent process; the agent is a child, not the parent.
2. **Candidate branching** — REPLAN doesn't just tell the agent to try again;
   BOUND forks a new execution path with a distinct checkpoint.
3. **Policy limits branching** — maximum candidates, pruning, fork-on-retry.
4. **Deterministic selection** — when multiple candidates complete, BOUND picks
   the best according to a fixed, testable ordering.

### 1.3 Non-Goals

- BOUND does **not** become an LLM orchestrator. It never calls an LLM.
- BOUND does **not** replace the agent's own planning. It evaluates outcomes.
- The adapter interface does **not** require Python. Any language can implement it.

---

## 2. Architecture

### 2.1 Component Diagram

```
                    ┌──────────────────────┐
                    │  bound-policy.yaml    │
                    │  (branching config)   │
                    └──────────┬───────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────┐
│                     ExecutionEngine                           │
│                                                               │
│  task = {goal, plan, contracts, criteria}                     │
│                                                               │
│  for each step in plan:                                       │
│    1. Create initial Candidate(step, checkpoint)              │
│    2. while active_candidates:                                │
│       a. Run each active candidate via adapter                │
│       b. On event: evaluate → ACCEPT/RETRY/REPLAN/ROLLBACK   │
│       c. REPLAN → fork new Candidate (subject to limits)     │
│       d. ACCEPT → promote Candidate to completed              │
│       e. RETRY → increment attempt on same Candidate          │
│       f. ROLLBACK → restore checkpoint, mark failed           │
│    3. When step budget exhausted or all complete:             │
│       select_best_candidate(completed) → promote to plan      │
└──────────────┬───────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────┐
│                     CandidateManager                          │
│                                                               │
│  - candidate_id :: str (deterministic, "cand_a1b2c3")        │
│  - parent_candidate_id :: str | None                          │
│  - status :: PENDING | RUNNING | COMPLETED | FAILED | PRUNED  │
│  - checkpoint :: Checkpoint (git snapshot at fork)            │
│  - result :: EvaluationResult | None                          │
│  - signals :: CodingWorkflowSignals                           │
│  - forked_at :: int (attempt that triggered the fork)         │
│  - rank :: tuple (for deterministic ordering)                 │
│                                                               │
│  Operations: fork / promote / prune / active_count /          │
│              total_count / select_best                        │
└──────────────┬───────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────┐
│                  Agent Adapter Interface                       │
│                                                               │
│  Abstract (ABC) with concrete child-process implementations   │
│  communicating via ACP (stdin/stdout JSONL).                  │
│                                                               │
│  Lifecycle: start → send_control → on_event → stop            │
│  Checkpointing: capture / restore via git                     │
└──────────────────────────────────────────────────────────────┘
```

### 2.2 Design Principles

1. **Process-level, not import-level.** Adapters are child processes. BOUND
   communicates via stdin/stdout JSONL. No Python import of the agent.
2. **Deterministic execution graph.** Same inputs → same fork tree.
   Candidate IDs are content-addressed SHA-256 hashes.
3. **No LLM in the decision path.** The engine calls
   `BoundWorkflow.evaluate_step()` — the same deterministic path as today.
4. **Git-native checkpointing.** Every fork captures a `Checkpoint`.
   ROLLBACK restores it. Diff size is used as a selection tiebreaker.
5. **Backward compatible.** Existing `BoundRuntime`, MCP server, watch mode,
   and manual CLI continue to work unchanged.

---

## 3. Agent Adapter Interface

### 3.1 Abstract Base

```python
# src/bound/adapters/base.py

from __future__ import annotations
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any
from bound.adapters.protocol import AdapterCommand, AdapterEvent, AdapterConfig


class AgentAdapter(ABC):
    """Abstract interface for a BOUND-controlled coding agent.

    An adapter wraps a specific agent (Codex, Claude Code, Cline, etc.) as a
    child process that BOUND controls via the Adapter Control Protocol (ACP).
    The adapter is language-agnostic: concrete implementations launch the agent
    as a subprocess and communicate via stdin/stdout JSONL.
    """

    def __init__(
        self,
        config: AdapterConfig,
        on_event: Callable[[AdapterEvent], None] | None = None,
    ) -> None:
        self._config = config
        self._on_event = on_event

    @property
    def on_event(self) -> Callable[[AdapterEvent], None] | None:
        return self._on_event

    @on_event.setter
    def on_event(self, cb: Callable[[AdapterEvent], None] | None) -> None:
        self._on_event = cb

    @abstractmethod
    def launch(self, *, task: str, plan: str, candidate_id: str,
               step_contract: dict[str, Any]) -> None:
        """Launch the agent process for a candidate execution."""
        ...

    @abstractmethod
    def send_command(self, command: AdapterCommand) -> None:
        """Send a control command to the agent process."""
        ...

    @abstractmethod
    def terminate(self) -> None:
        """Gracefully terminate the agent process. Idempotent."""
        ...

    @abstractmethod
    def capture_checkpoint(self) -> str:
        """Capture current git state; return checkpoint_id."""
        ...

    @abstractmethod
    def restore_checkpoint(self, checkpoint_id: str) -> None:
        """Restore working tree to a previous checkpoint."""
        ...

    @property
    @abstractmethod
    def is_running(self) -> bool:
        ...

    @property
    @abstractmethod
    def candidate_id(self) -> str | None:
        ...
```

### 3.2 Adapter Configuration

```python
# src/bound/adapters/protocol.py (partial)

from pydantic import BaseModel, ConfigDict, Field


class AdapterConfig(BaseModel):
    """Configuration for an agent adapter instance."""

    model_config = ConfigDict(extra="forbid")

    agent_type: str = Field(min_length=1)
    working_dir: str = Field(min_length=1)
    env: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=600.0, gt=0)
    agent_command: str | None = None
    agent_args: list[str] = Field(default_factory=list)
```

### 3.3 Language-Agnostic Design

The `AgentAdapter` ABC describes the **Python-side lifecycle manager**. The
**actual agent communication** happens via the Adapter Control Protocol (ACP).

- A Python `CodexAdapter` wraps a subprocess running the Codex CLI.
- A Python `GenericAdapter` wraps any executable that speaks ACP.
- The adapter bridges: Python manages the process, ACP carries events/commands.

---

## 4. Lifecycle Control Loop

### 4.1 The BOUND→Agent→BOUND Cycle

```
  1. Create Candidate with initial checkpoint
  2. Launch Adapter for Candidate
         │
         ▼
     Agent executes the step ◀── step contract
         │
         ▼
     Agent emits step_completed + evidence (JSONL stdout)
         │
         ▼
  3. BOUND evaluates (BoundWorkflow.evaluate_step)
     → EvaluationResult(decision, score, ...)
         │
    ┌────┼────┐
    ▼    ▼    ▼
  ACCEPT RETRY REPLAN/ROLLBACK
    │     │      │
    ▼     ▼      ▼
  CONTINUE RETRY Fork new Candidate
  →promote →att++ →launch adapter
                  (subject to limits)

  4. When all candidates complete:
     select_best_candidate() → advance to next step
```

### 4.2 Decision Handling Rules

| Decision | Action on Candidate | Command to Adapter |
|----------|-------------------|-------------------|
| ACCEPT | promote to COMPLETED | CONTINUE |
| RETRY | increment attempt; fork if `fork_after_retry` reached | RETRY |
| REPLAN | fork new candidate; mark current FAILED | REPLAN then terminate |
| ROLLBACK | fork new candidate; restore checkpoint; mark current FAILED | ROLLBACK then terminate |

> **RETRY does NOT fork by default.** Same candidate, same process, attempt+1.
> Only when `fork_after_retry` is set (e.g. 3) and the attempt count reaches it
> does RETRY trigger a fork.


---

## 5. Execution Event Protocol

### 5.1 Event Types

The adapter emits structured JSONL events on stdout. These are a **superset**
of the existing watch protocol (`events_watch.py`) with additions for
candidate-aware native execution.

```python
# src/bound/adapters/protocol.py

from __future__ import annotations
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field

ACP_SCHEMA_VERSION: str = "1.0"


class _ACPBase(BaseModel):
    """Base for all ACP messages."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: str = Field(default=ACP_SCHEMA_VERSION)
    type: str
    timestamp: str = Field(min_length=1)


# --- Agent → BOUND events (stdout) ---

class CandidateStartedEvent(_ACPBase):
    type: Literal["candidate_started"] = "candidate_started"
    candidate_id: str = Field(min_length=1)
    task: str = Field(min_length=1)
    plan: str = Field(min_length=1)


class StepCompletedEvent(_ACPBase):
    """Agent completed a step. Carries ExecutionEvidence for evaluation."""
    type: Literal["step_completed"] = "step_completed"
    candidate_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    attempt: int = Field(ge=1)
    evidence: dict  # ExecutionEvidence serialised
    signals: dict | None = None  # CodingWorkflowSignals serialised


class VerificationRequestedEvent(_ACPBase):
    """Agent requests BOUND-run verification (e.g. re-run pytest)."""
    type: Literal["verification_requested"] = "verification_requested"
    candidate_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    check_ids: list[str] = Field(min_length=1)


class HeartbeatEvent(_ACPBase):
    type: Literal["heartbeat"] = "heartbeat"
    candidate_id: str = Field(min_length=1)
    progress: str | None = None


class CandidateErrorEvent(_ACPBase):
    type: Literal["candidate_error"] = "candidate_error"
    candidate_id: str = Field(min_length=1)
    error: str = Field(min_length=1)
    detail: str | None = None


class CandidateFinishedEvent(_ACPBase):
    type: Literal["candidate_finished"] = "candidate_finished"
    candidate_id: str = Field(min_length=1)
    outcome: Literal["completed", "interrupted", "abandoned", "cancelled"]
    summary: str | None = None


# Discriminated union
AdapterEvent = Annotated[
    CandidateStartedEvent
    | StepCompletedEvent
    | VerificationRequestedEvent
    | HeartbeatEvent
    | CandidateErrorEvent
    | CandidateFinishedEvent,
    Field(discriminator="type"),
]


# --- BOUND → Agent commands (stdin) ---

class ContinueCommand(_ACPBase):
    type: Literal["continue"] = "continue"
    candidate_id: str = Field(min_length=1)
    feedback: str = Field(min_length=1)


class RetryCommand(_ACPBase):
    type: Literal["retry"] = "retry"
    candidate_id: str = Field(min_length=1)
    attempt: int = Field(ge=1)
    feedback: str = Field(min_length=1)


class ReplanCommand(_ACPBase):
    type: Literal["replan"] = "replan"
    candidate_id: str = Field(min_length=1)
    feedback: str = Field(min_length=1)


class RollbackCommand(_ACPBase):
    type: Literal["rollback"] = "rollback"
    candidate_id: str = Field(min_length=1)
    checkpoint_id: str = Field(min_length=1)
    feedback: str = Field(min_length=1)


class ShutdownCommand(_ACPBase):
    type: Literal["shutdown"] = "shutdown"
    candidate_id: str = Field(min_length=1)
    reason: str = "candidate_pruned"


AdapterCommand = Annotated[
    ContinueCommand | RetryCommand | ReplanCommand
    | RollbackCommand | ShutdownCommand,
    Field(discriminator="type"),
]
```

### 5.2 Relationship to Existing Watch Protocol

The watch protocol (`events_watch.py`) is for agents that **own** their loop.
ACP is for agents **controlled by** BOUND.

| Aspect | Watch Protocol | ACP |
|--------|---------------|-----|
| Direction | Agent → BOUND only | Bidirectional (events + commands) |
| Control ownership | Agent | BOUND |
| Candidate awareness | None | Candidate IDs on every message |
| Checkpointing | Not covered | First-class (capture/restore) |
| Schema version | `WATCH_EVENT_SCHEMA_VERSION` | `ACP_SCHEMA_VERSION` |

Both use JSONL format and Pydantic discriminated unions.

---

## 6. Candidate Branching Model

### 6.1 Candidate Data Model

```python
# src/bound/candidates.py

from __future__ import annotations
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from bound.models import CodingWorkflowSignals, EvaluationResult


class CandidateStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PRUNED = "pruned"


class Candidate(BaseModel):
    """One execution path for a plan step.

    When BOUND returns REPLAN or ROLLBACK, a new candidate is forked.
    Multiple candidates may run concurrently subject to policy limits.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    parent_id: str | None = None
    root_id: str | None = None

    status: CandidateStatus = CandidateStatus.PENDING
    attempt: int = Field(default=1, ge=1)
    forked_at_attempt: int | None = None
    fork_reason: Literal["initial", "replan", "rollback", "retry_fork"] = "initial"

    checkpoint_id: str = Field(min_length=1)
    result: EvaluationResult | None = None
    signals: CodingWorkflowSignals | None = None

    created_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat())
    completed_at: str | None = None

    @property
    def is_active(self) -> bool:
        return self.status in (CandidateStatus.PENDING, CandidateStatus.RUNNING)

    @property
    def is_terminal(self) -> bool:
        return self.status in (
            CandidateStatus.COMPLETED,
            CandidateStatus.FAILED,
            CandidateStatus.PRUNED,
        )
```

### 6.2 Candidate ID Generation

```python
import hashlib
from datetime import UTC, datetime


def generate_candidate_id(
    *,
    parent_id: str | None,
    step_id: str,
    timestamp: datetime | None = None,
) -> str:
    """Deterministic candidate ID: SHA-256 of parent+step+timestamp."""
    ts = (timestamp or datetime.now(UTC)).isoformat()
    payload = f"{parent_id or 'root'}|{step_id}|{ts}"
    digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return f"cand_{digest}"
```

### 6.3 Branching Rules

```
                    ROOT (cand_aaaa) attempt=1
                      │
                      ├── RETRY → same candidate, attempt=2
                      │
                      ├── REPLAN → fork cand_bbbb (parent=aaaa, reason=replan)
                      │              │
                      │              ├── ACCEPT → promote cand_bbbb ✓
                      │              └── RETRY → attempt=2 on cand_bbbb
                      │
                      ├── ROLLBACK → fork cand_cccc (parent=aaaa, reason=rollback)
                      │              restore checkpoint → cand_cccc runs
                      │
                      └── RETRY × N (fork_after_retry=3) →
                           attempt 3 triggers fork cand_dddd (reason=retry_fork)
```

**Rules:**
1. **RETRY** increments attempt. No fork unless `fork_after_retry` triggers.
2. **REPLAN** always forks. Old candidate → FAILED. New starts from parent checkpoint.
3. **ROLLBACK** always forks. Checkpoint restored before new candidate runs.
4. **ACCEPT** promotes to COMPLETED. No further candidates for this step.
5. **Candidates are independent.** Each has its own attempt counter.


### 6.4 CandidateManager

```python
# src/bound/candidates.py (continued)

from bound.checkpoint import Checkpoint, capture_checkpoint


class CandidateManager:
    """Manages the candidate tree for one plan step.

    Enforces branching policy limits and provides selection.
    """

    def __init__(self, config: BranchingConfig) -> None:
        self._config = config
        self._candidates: dict[str, Candidate] = {}
        self._root_id: str | None = None

    def create_root(self, *, step_id: str, checkpoint_id: str) -> Candidate:
        if self._root_id is not None:
            raise ValueError("Root candidate already exists")
        cid = generate_candidate_id(parent_id=None, step_id=step_id)
        candidate = Candidate(
            id=cid, step_id=step_id, root_id=cid,
            checkpoint_id=checkpoint_id, fork_reason="initial")
        self._candidates[cid] = candidate
        self._root_id = cid
        return candidate

    def fork(self, parent: Candidate, *,
             reason: Literal["replan", "rollback", "retry_fork"],
             checkpoint_id: str | None = None) -> Candidate | None:
        if not self._can_fork():
            return None
        cid = generate_candidate_id(parent_id=parent.id,
                                     step_id=parent.step_id)
        forked = Candidate(
            id=cid, step_id=parent.step_id,
            parent_id=parent.id, root_id=parent.root_id or parent.id,
            checkpoint_id=checkpoint_id or parent.checkpoint_id,
            forked_at_attempt=parent.attempt, fork_reason=reason)
        self._candidates[cid] = forked
        return forked

    def promote(self, candidate: Candidate) -> None:
        candidate.status = CandidateStatus.COMPLETED
        candidate.completed_at = datetime.now(UTC).isoformat()

    def fail(self, candidate: Candidate) -> None:
        candidate.status = CandidateStatus.FAILED
        candidate.completed_at = datetime.now(UTC).isoformat()

    def prune(self, candidate: Candidate) -> None:
        candidate.status = CandidateStatus.PRUNED
        candidate.completed_at = datetime.now(UTC).isoformat()

    def active(self) -> list[Candidate]:
        return [c for c in self._candidates.values() if c.is_active]

    def completed(self) -> list[Candidate]:
        return [c for c in self._candidates.values()
                if c.status == CandidateStatus.COMPLETED]

    def active_count(self) -> int:
        return sum(1 for c in self._candidates.values() if c.is_active)

    def total_count(self) -> int:
        return len(self._candidates)

    def prune_if_needed(self) -> list[Candidate]:
        pruned: list[Candidate] = []
        if self._config.prune_failed_candidates:
            for c in list(self._candidates.values()):
                if c.status == CandidateStatus.FAILED:
                    self.prune(c); pruned.append(c)
        over = self.total_count() - self._config.max_total_candidates
        if over > 0:
            pending = [c for c in self._candidates.values()
                       if c.status == CandidateStatus.PENDING]
            for c in pending[:over]:
                self.prune(c); pruned.append(c)
        return pruned

    def _can_fork(self) -> bool:
        return (self.total_count() < self._config.max_total_candidates
                and self.active_count() < self._config.max_active_candidates)

    def select_best(self) -> Candidate | None:
        return select_best_candidate(self.completed())
```

---

## 7. Policy-driven Branching Configuration

### 7.1 YAML Schema Extension

```yaml
# bound-policy.yaml — new branching section

policy:
  id: my-project
  version: "1.0"

# ... existing collectors, checks, budgets, change_scope, approvals ...

branching:
  max_active_candidates: 3
  max_total_candidates: 10
  fork_after_retry: 3
  prune_failed_candidates: true
  default_candidate_limit: 1
```

### 7.2 Pydantic Model

```python
# src/bound/policy_schema.py — new BranchingConfig model

class BranchingConfig(BaseModel):
    """Candidate branching policy for native agent execution.

    Default (max_active=1, max_total=1) disables branching — BOUND
    behaves as v0.8.x: one task, one candidate, serial execution.
    """

    model_config = ConfigDict(extra="forbid")

    max_active_candidates: int = Field(default=1, ge=1)
    max_total_candidates: int = Field(default=1, ge=1)
    fork_after_retry: int | None = Field(default=None, ge=0)
    prune_failed_candidates: bool = Field(default=True)
    default_candidate_limit: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _validate_limits(self) -> BranchingConfig:
        if self.max_total_candidates < self.max_active_candidates:
            raise ValueError(
                f"max_total_candidates ({self.max_total_candidates}) must be "
                f">= max_active_candidates ({self.max_active_candidates})")
        return self
```

### 7.3 Addition to BoundPolicyConfig

```python
# In BoundPolicyConfig:
branching: BranchingConfig = Field(default_factory=BranchingConfig)
```

### 7.4 Backward Compatibility

The default `BranchingConfig()` (max_active=1, max_total=1) means:
- No parallel execution
- No forking
- One candidate per step
- Identical behaviour to v0.8.x

Existing `bound-policy.yaml` files without a `branching` section use defaults.


---

## 8. Candidate Selection Algorithm

### 8.1 Selection Ordering (Deterministic)

When multiple candidates complete for the same step, BOUND selects the **best**
according to this fixed, testable ordering:

```
1. Hard requirements passed        (all required acceptance checks have passing evidence)
2. Verified correctness            (VERIFIED > ATTESTED > OBSERVED > EVALUATED)
3. Final assurance level           (DecisionAssurance ordinal)
4. Fewest regressions              (tests_removed asc, tests_modified asc)
5. Smallest valid diff             (files_changed asc, unexpected_files_changed asc)
6. Shortest runtime                (execution_time_seconds asc)
7. Fewest tool calls               (tool_call_count asc)
8. Lowest token usage              (token_usage asc)
9. Candidate ID                    (lexicographic, final tiebreaker)
```

### 8.2 Algorithm

```python
# src/bound/candidates.py

_ASSURANCE_ORDINAL: dict[str, int] = {
    "VERIFIED": 4, "ATTESTED": 3, "OBSERVED": 2,
    "EVALUATED": 1, "CLAIMED": 0, "INSUFFICIENT": -1,
}


def _all_hard_requirements_passed(candidate: Candidate) -> bool:
    if candidate.result is None:
        return False
    if candidate.result.assurance in ("INSUFFICIENT", "CLAIMED"):
        return False
    return True


def _build_selection_key(candidate: Candidate) -> tuple:
    """Build a sortable key tuple. Lower = better."""
    result = candidate.result
    signals = candidate.signals
    assurance = result.assurance if result else "INSUFFICIENT"

    # (1) Hard requirements: True→0, False→1 (lower is better)
    hard_pass = 0 if _all_hard_requirements_passed(candidate) else 1

    # (2) Verified correctness: higher ordinal → better → negate
    verified_ordinal = -_ASSURANCE_ORDINAL.get(assurance, -1)

    # (3) Final assurance: same ordinal, negated
    assurance_ordinal = -_ASSURANCE_ORDINAL.get(assurance, -1)

    # (4) Regressions: lower is better
    tests_removed = (
        signals.tests_removed
        if signals and signals.tests_removed is not None else 0)
    tests_modified = (
        signals.tests_modified
        if signals and signals.tests_modified is not None else 0)

    # (5) Diff size: lower is better
    files_changed = (
        signals.files_changed
        if signals and signals.files_changed is not None else 0)
    unexpected_files = (
        signals.unexpected_files_changed
        if signals and signals.unexpected_files_changed is not None else 0)

    # (6) Runtime: lower is better; None → worst case
    runtime = (
        signals.execution_time_seconds
        if signals and signals.execution_time_seconds is not None
        else float("inf"))

    # (7) Tool calls
    tool_calls = signals.tool_call_count if signals else 0

    # (8) Token usage: lower is better; None → worst case
    tokens = (
        signals.token_usage
        if signals and signals.token_usage is not None else float("inf"))

    # (9) Candidate ID: lexicographic tiebreaker
    cid = candidate.id

    return (
        hard_pass, verified_ordinal, assurance_ordinal,
        tests_removed, tests_modified,
        files_changed, unexpected_files,
        runtime, tool_calls, tokens, cid,
    )


def select_best_candidate(completed: list[Candidate]) -> Candidate | None:
    """Select the best candidate by deterministic ordering."""
    if not completed:
        return None
    return min(completed, key=_build_selection_key)


def rank_candidates(candidates: list[Candidate]) -> list[Candidate]:
    """Sort candidates from best (index 0) to worst."""
    return sorted(candidates, key=_build_selection_key)
```

### 8.3 Edge Cases

- **No completed candidates:** Step is marked as failed. Engine either aborts
  the task or enters manual intervention.
- **All candidates have identical keys:** Candidate ID (deterministic hash)
  ensures a stable, reproducible choice.
- **Missing signals:** Treated as worst-case (e.g. `float("inf")`). A
  candidate with measured signals beats one with missing signals at tiers 6-8,
  but only after hard requirements and assurance are compared.

---

## 9. Adapter Control Protocol (ACP)

### 9.1 Transport

- **stdin** (BOUND → agent): JSONL commands
- **stdout** (agent → BOUND): JSONL events
- **stderr** (agent): Opaque logs, not parsed

Each line is a complete JSON object with a `type` discriminator.

### 9.2 Handshake

```
AGENT → BOUND:  {"type":"candidate_started","candidate_id":"cand_...", ...}
BOUND → AGENT:  (no immediate response; waits for step_completed)
```

### 9.3 Example Session

```jsonl
# Agent emits on launch:
{"type":"candidate_started","candidate_id":"cand_a1b2","task":"Add email validation","plan":"1. Add validator 2. Add tests 3. Wire endpoint","timestamp":"2025-07-26T10:00:00Z","schema_version":"1.0"}

# Heartbeats:
{"type":"heartbeat","candidate_id":"cand_a1b2","progress":"Implementing validator","timestamp":"2025-07-26T10:01:00Z","schema_version":"1.0"}

# Step completed with evidence:
{"type":"step_completed","candidate_id":"cand_a1b2","step_id":"PHASE-001","attempt":1,"evidence":{"acceptance":[{"check_id":"tests-pass","passed":false,"evidence":[],"provenance":"verified"}],"risks":[],"artifacts":[],"metrics":{}},"signals":{"test_pass_rate":0.3,"tool_call_count":12,"execution_time_seconds":142.5},"timestamp":"2025-07-26T10:04:00Z","schema_version":"1.0"}

# BOUND evaluates → REPLAN:
{"type":"replan","candidate_id":"cand_a1b2","feedback":"Only 30% tests passing. Choose different strategy.","timestamp":"2025-07-26T10:04:01Z","schema_version":"1.0"}

# New candidate launched, eventually ACCEPT:
{"type":"continue","candidate_id":"cand_c3d4","feedback":"All checks passed. Step accepted.","timestamp":"2025-07-26T10:08:01Z","schema_version":"1.0"}

# Agent finishes:
{"type":"candidate_finished","candidate_id":"cand_c3d4","outcome":"completed","summary":"Email validation with 42 passing tests","timestamp":"2025-07-26T10:08:02Z","schema_version":"1.0"}
```

### 9.4 Timeout and Error Handling

- No event within `timeout_seconds` → `shutdown` command → candidate FAILED
- Agent process exits unexpectedly → candidate FAILED
- `candidate_error` event → may RETRY or FAIL depending on error and budget


---

## 10. Reference Adapter Implementations

### 10.1 Pattern: Bridge Script

Each adapter uses a **bridge script** pattern:

```
BOUND (Python) → subprocess.Popen → bridge script → Agent CLI/API
                                        ↑
                              Translates ACP ↔ Agent I/O
```

The bridge script:
1. Reads ACP commands from stdin
2. Translates them into agent-specific API calls / CLI arguments
3. Monitors agent output for meaningful boundaries
4. Runs BOUND evidence collectors (pytest, git, etc.)
5. Emits ACP events on stdout

### 10.2 Adapter Factory

```python
# src/bound/adapters/factory.py

from bound.adapters.base import AgentAdapter
from bound.adapters.protocol import AdapterConfig, AdapterEvent
from collections.abc import Callable


def create_adapter(
    config: AdapterConfig,
    on_event: Callable[[AdapterEvent], None] | None = None,
) -> AgentAdapter:
    """Create the appropriate adapter for the configured agent type."""
    match config.agent_type:
        case "generic":
            from bound.adapters.generic import GenericAdapter
            return GenericAdapter(config, on_event=on_event)
        case "codex":
            from bound.adapters.codex import CodexAdapter
            return CodexAdapter(config, on_event=on_event)
        case "claude-code":
            from bound.adapters.claude_code import ClaudeCodeAdapter
            return ClaudeCodeAdapter(config, on_event=on_event)
        case "cline":
            from bound.adapters.cline import ClineAdapter
            return ClineAdapter(config, on_event=on_event)
        case _:
            raise ValueError(f"Unknown agent type: {config.agent_type}")
```

### 10.3 GenericAdapter

```python
# src/bound/adapters/generic.py

"""Generic adapter for any executable that speaks ACP.

Launches an arbitrary command and communicates via ACP on stdin/stdout.
This is the escape hatch: any agent that emits ACP JSONL events and reads
ACP commands is compatible.

Usage:
    bound run --adapter generic --agent-cmd "my-agent --acp-mode"
"""
```

### 10.4 Codex / Claude Code / Cline Adapters

Each follows the bridge pattern:

- **CodexAdapter**: Wraps Codex CLI. Bridge script translates Codex output
  into ACP events and ACP commands into Codex instructions.
- **ClaudeCodeAdapter**: Same pattern for Claude Code CLI.
- **ClineAdapter**: Uses VS Code extension API via a Node.js bridge.

### 10.5 Reusing Existing Integration Prompts

The existing `integrations/<agent>/INSTALL_BOUND.md` prompts describe the
**manual** control loop. Bridge scripts reuse these prompts as the "what the
agent should do" instructions, adding the ACP protocol layer for "how to
communicate with BOUND."

---

## 11. Execution Engine

### 11.1 Engine Class

```python
# src/bound/engine.py

from __future__ import annotations
import logging
from pathlib import Path

from bound.adapters.base import AgentAdapter
from bound.adapters.factory import create_adapter
from bound.adapters.protocol import (
    AdapterCommand, AdapterConfig, AdapterEvent,
    ContinueCommand, ReplanCommand, RetryCommand,
    RollbackCommand, ShutdownCommand,
)
from bound.bound_workflow import BoundWorkflow
from bound.candidates import (
    Candidate, CandidateManager, CandidateStatus,
    select_best_candidate,
)
from bound.checkpoint import capture_checkpoint, restore_checkpoint
from bound.contracts import BoundPlan, StepContract
from bound.evidence import ExecutionEvidence
from bound.models import BoundCriteria, CodingWorkflowSignals
from bound.policy_schema import BranchingConfig, BoundPolicyConfig

logger = logging.getLogger(__name__)


class ExecutionEngine:
    """BOUND native agent execution engine.

    Owns the control loop: launches adapters, evaluates through the
    BOUND contract pipeline, manages branching, selects best outcomes.

    Deterministic: same inputs → same execution graph.
    """

    def __init__(
        self,
        *,
        policy_config: BoundPolicyConfig,
        workflow: BoundWorkflow | None = None,
        working_dir: Path | None = None,
    ) -> None:
        self._policy_config = policy_config
        self._workflow = workflow or BoundWorkflow()
        self._working_dir = working_dir or Path.cwd()
        self.branching = policy_config.branching

    @property
    def workflow(self) -> BoundWorkflow:
        return self._workflow

    @property
    def policy_config(self) -> BoundPolicyConfig:
        return self._policy_config

    def run_task(
        self,
        *,
        task: str,
        plan: BoundPlan,
        criteria: BoundCriteria,
        adapter_type: str = "generic",
        adapter_command: str | None = None,
    ) -> list[Candidate]:
        """Execute a complete task with the native control loop.

        For each step in `plan`, creates candidates, launches adapters,
        evaluates outcomes, manages branching, selects best result.

        Returns selected candidates (one per step).

        Raises RuntimeError if a step has no successful candidates after
        all branching limits are exhausted.
        """
        results: list[Candidate] = []
        for step in plan.steps:
            best = self._run_step(
                task=task, step=step, criteria=criteria,
                adapter_type=adapter_type,
                adapter_command=adapter_command)
            if best is None:
                raise RuntimeError(
                    f"Step {step.id} failed: no candidate met acceptance "
                    f"criteria after {self.branching.max_total_candidates} "
                    f"attempts.")
            results.append(best)
        return results

    def _run_step(
        self, *, task: str, step: StepContract,
        criteria: BoundCriteria,
        adapter_type: str, adapter_command: str | None,
    ) -> Candidate | None:
        manager = CandidateManager(config=self.branching)

        # Capture initial checkpoint
        cp = capture_checkpoint(
            run_id="run", step_id=step.id, cwd=self._working_dir)
        checkpoint_id = cp.checkpoint_id if cp else "cp_initial"

        root = manager.create_root(
            step_id=step.id, checkpoint_id=checkpoint_id)
        root.status = CandidateStatus.RUNNING

        while manager.active_count() > 0:
            for candidate in manager.active():
                if candidate.status == CandidateStatus.PENDING:
                    candidate.status = CandidateStatus.RUNNING

                adapter_config = AdapterConfig(
                    agent_type=adapter_type,
                    working_dir=str(self._working_dir),
                    timeout_seconds=600.0,
                    agent_command=adapter_command)
                adapter = create_adapter(adapter_config)

                event_received: list[AdapterEvent] = []

                def on_event(ev: AdapterEvent) -> None:
                    event_received.append(ev)

                adapter.on_event = on_event

                try:
                    adapter.launch(
                        task=task, plan=step.description,
                        candidate_id=candidate.id,
                        step_contract=step.model_dump(mode="json"))

                    event = _wait_for_decision_event(
                        adapter, timeout=adapter_config.timeout_seconds)

                    if event is None:
                        adapter.send_command(ShutdownCommand(
                            candidate_id=candidate.id, reason="timeout"))
                        manager.fail(candidate)
                        continue

                    if event.type == "step_completed":
                        result = self._workflow.evaluate_step(
                            contract=step,
                            evidence=ExecutionEvidence.model_validate(
                                event.evidence),
                            criteria=criteria,
                            attempt=candidate.attempt)
                        candidate.result = result

                        if event.signals:
                            candidate.signals = (
                                CodingWorkflowSignals.model_validate(
                                    event.signals))

                        match result.decision:
                            case "ACCEPT":
                                adapter.send_command(ContinueCommand(
                                    candidate_id=candidate.id,
                                    feedback="Step accepted."))
                                manager.promote(candidate)
                            case "RETRY":
                                if (self.branching.fork_after_retry
                                        and candidate.attempt >= self.branching.fork_after_retry):
                                    manager.fork(candidate, reason="retry_fork")
                                else:
                                    adapter.send_command(RetryCommand(
                                        candidate_id=candidate.id,
                                        attempt=candidate.attempt + 1,
                                        feedback="Retry the step."))
                                    candidate.attempt += 1
                            case "REPLAN":
                                adapter.send_command(ReplanCommand(
                                    candidate_id=candidate.id,
                                    feedback="Replan with different strategy."))
                                manager.fork(candidate, reason="replan")
                                adapter.terminate()
                            case "ROLLBACK":
                                adapter.send_command(RollbackCommand(
                                    candidate_id=candidate.id,
                                    checkpoint_id=candidate.checkpoint_id,
                                    feedback="Rollback to safe state."))
                                manager.fork(candidate, reason="rollback")
                                restore_checkpoint(
                                    checkpoint_id=candidate.checkpoint_id,
                                    cwd=self._working_dir)
                                adapter.terminate()

                    elif event.type == "candidate_error":
                        logger.error("Candidate %s error: %s",
                                     event.candidate_id, event.error)
                        manager.fail(candidate)

                    elif event.type == "candidate_finished":
                        if (candidate.result
                                and candidate.result.decision == "ACCEPT"):
                            manager.promote(candidate)
                        else:
                            manager.fail(candidate)

                finally:
                    if adapter.is_running:
                        adapter.terminate()

                manager.prune_if_needed()

        return select_best_candidate(manager.completed())
```

### 11.2 CLI Entry Point

```bash
# New CLI subcommand
bound run \
    --adapter codex \
    --policy bound-policy.yaml \
    --task "Add email validation to the registration endpoint" \
    --plan plan.md
```


---

## 12. Backward Compatibility

### 12.1 Unchanged Entry Points

All existing entry points continue to work exactly as before:

| Entry Point | v0.8.x Behaviour | v0.9.5 Behaviour |
|------------|-----------------|-----------------|
| `bound evaluate` | CLI one-shot evaluation | **Unchanged** |
| `bound mcp` | MCP server (agent controls loop) | **Unchanged** |
| `bound watch` | Watch mode (agent streams events) | **Unchanged** |
| `bound checkpoint` | Checkpoint capture/inspect | **Unchanged** |
| `bound rollback` | Rollback to checkpoint | **Unchanged** |
| `BoundRuntime.evaluate()` | Python API one-shot | **Unchanged** |
| `BoundWorkflow.evaluate_step()` | Contract evaluation | **Unchanged** |
| `evaluate_agent_step()` | Integration helper | **Unchanged** |

### 12.2 New Entry Points

| Entry Point | Description |
|------------|-------------|
| `bound run` | Native execution: BOUND controls the agent |
| `bound.candidates` | Candidate management module |
| `bound.adapters` | Adapter base + implementations |
| `bound.engine` | Execution engine |

### 12.3 Policy Compatibility

Existing `bound-policy.yaml` files **without** a `branching` section use the
default `BranchingConfig()`:

```yaml
# Implicit default:
branching:
  max_active_candidates: 1
  max_total_candidates: 1
  fork_after_retry: null
  prune_failed_candidates: true
  default_candidate_limit: 1
```

This means `max_total_candidates=1` → only one candidate ever exists → no
branching → identical behaviour to v0.8.x. Users opt into branching by adding
a `branching` section to their policy.

### 12.4 Integration Prompt Compatibility

The existing `integrations/*/INSTALL_BOUND.md` prompts describe the **manual**
control loop (agent calls BOUND). These remain valid for agents that want to
retain control. The new `bound run` command is an **alternative** for users
who want BOUND to own the loop.

---

## 13. Implementation Roadmap

### Phase 1: Data Models & Protocol (Week 1)

1. Add `BranchingConfig` to `policy_schema.py`
2. Add `Candidate`, `CandidateStatus`, `CandidateManager` to `candidates.py`
3. Define ACP event/command types in `adapters/protocol.py`
4. Tests for all new models

### Phase 2: Adapter Base (Week 2)

1. Implement `AgentAdapter` ABC in `adapters/base.py`
2. Implement `GenericAdapter` in `adapters/generic.py`
3. Implement `create_adapter()` factory
4. Integration test: GenericAdapter + mock agent speaking ACP

### Phase 3: Execution Engine (Week 3)

1. Implement `ExecutionEngine` in `engine.py`
   - Single-candidate path first (backward compat)
   - Then multi-candidate with branching
2. Wire checkpoint capture/restore into candidate lifecycle
3. Implement `bound run` CLI subcommand
4. Integration test: full task with mock agent, branching

### Phase 4: Reference Adapters (Week 4)

1. Codex adapter (`adapters/codex.py` + bridge script)
2. Claude Code adapter (`adapters/claude_code.py` + bridge script)
3. Cline adapter (`adapters/cline.py` + bridge script)
4. Conformance test: all adapters pass canonical scenario

### Phase 5: Backward Compatibility & Polish (Week 5)

1. Verify all existing tests pass unchanged
2. Verify existing MCP/watch/CLI modes unaffected
3. Documentation: user-facing docs from this design doc
4. Performance: concurrent candidate execution with asyncio/threads

---

## 14. Module Layout

```
src/bound/
├── adapters/                    # NEW: Agent adapter framework
│   ├── __init__.py              #   Public API
│   ├── base.py                  #   AgentAdapter ABC
│   ├── protocol.py              #   ACP event/command types, AdapterConfig
│   ├── factory.py               #   create_adapter() factory
│   ├── generic.py               #   GenericAdapter (any ACP executable)
│   ├── codex.py                 #   CodexAdapter
│   ├── claude_code.py           #   ClaudeCodeAdapter
│   └── cline.py                 #   ClineAdapter
│
├── candidates.py                # NEW: Candidate, CandidateManager, selection
├── engine.py                    # NEW: ExecutionEngine, bound run
│
├── models.py                    # UNCHANGED (Decision type already defined)
├── policy_schema.py             # MODIFIED: +BranchingConfig, +branching field
├── policy.py                    # UNCHANGED
├── policy_canon.py              # UNCHANGED
├── bound_workflow.py            # UNCHANGED (engine calls evaluate_step)
├── contracts.py                 # UNCHANGED
├── integration.py               # UNCHANGED (backward compat)
├── integration_spec.py          # UNCHANGED
├── checkpoint.py                # UNCHANGED (engine uses capture/restore)
├── runtime.py                   # UNCHANGED (backward compat public API)
├── mcp_server.py                # UNCHANGED
├── events.py                    # UNCHANGED
├── events_watch.py              # UNCHANGED
├── services.py                  # UNCHANGED
├── cli.py                       # MODIFIED: +bound run subcommand
└── ...
```

---

## Appendix A: Decision → AdapterCommand Mapping

```python
# src/bound/adapters/protocol.py

_DECISION_TO_COMMAND: dict[str, type[AdapterCommand]] = {
    "ACCEPT": ContinueCommand,
    "RETRY": RetryCommand,
    "REPLAN": ReplanCommand,
    "ROLLBACK": RollbackCommand,
}


def decision_to_command(
    decision: str, candidate_id: str, feedback: str, **kwargs,
) -> AdapterCommand:
    """Convert a BOUND decision into an ACP command."""
    command_cls = _DECISION_TO_COMMAND.get(decision)
    if command_cls is None:
        raise ValueError(f"Unknown decision: {decision}")
    from datetime import UTC, datetime
    return command_cls(
        candidate_id=candidate_id, feedback=feedback,
        timestamp=datetime.now(UTC).isoformat(), **kwargs)
```

## Appendix B: Branching Tree Example

```
Task: "Add email validation"
Policy: max_active=2, max_total=5, fork_after_retry=3

Step PHASE-001:
  │
  ├── cand_aaaa (root, attempt=1)
  │     │ RETRY (attempt=2)
  │     │ RETRY (attempt=3)
  │     │ REPLAN → fork cand_bbbb (reason=replan)
  │     └── (FAILED)
  │
  ├── cand_bbbb (parent=aaaa, attempt=1)
  │     │ RETRY (attempt=2)
  │     │ ACCEPT ✓
  │     └── (COMPLETED, score=0.92, files_changed=3, runtime=45.2s)
  │
  └── cand_cccc (forked concurrently with bbbb from aaaa, reason=retry_fork)
        │ ACCEPT ✓
        └── (COMPLETED, score=0.88, files_changed=5, runtime=62.1s)

Selection: cand_bbbb beats cand_cccc
  - Both pass hard requirements ✓
  - Both VERIFIED ✓
  - cand_bbbb has fewer files_changed (3 < 5) → wins at tier 5
```

---

## Appendix C: Key Design Decisions

1. **Why process-level instead of library-level?**
   - Agents run in different languages (Python, Node.js, Rust, shell).
   - A process boundary enforces the separation of concerns: BOUND never
     imports agent code, agents never import BOUND internals.
   - Stdin/stdout JSONL is the simplest universal IPC.

2. **Why does RETRY not fork by default?**
   - RETRY means "close to the threshold, same strategy might work."
     Forking would create unnecessary checkpoint overhead.
   - `fork_after_retry` exists for cases where repeated retries indicate
     the strategy itself is flawed despite marginal scores.

3. **Why is candidate selection deterministic?**
   - BOUND's core promise: same inputs → same outputs.
   - A non-deterministic selection would make the execution graph
     unreproducible, undermining audit and debugging.
   - The ordering is designed so that objective quality signals (hard
     requirements, verification, assurance) always dominate resource
     consumption signals (runtime, tokens).

4. **Why no LLM in candidate selection?**
   - The LLM is the agent's domain. BOUND evaluates, it doesn't generate.
   - "Which candidate is best?" is answered by structured comparison of
     evaluation results and signals — a pure function.

5. **Why a new `bound run` command instead of modifying existing ones?**
   - The existing MCP/watch/manual modes are genuinely useful for
     different integration topologies (agent-owned loop vs BOUND-owned
     loop). Both should coexist.
   - `bound run` is the "BOUND owns the loop" entry point, symmetric to
     `bound mcp` and `bound watch` as "agent owns the loop" entry points.

---

*End of design document.*
