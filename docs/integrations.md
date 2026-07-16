# Integrations

How to wire BOUND into an agent. BOUND is framework-neutral: it ships **no**
native plugin for any agent. Each integration below is an *integration prompt*
you paste into an agent so it wires BOUND into its own workflow.

## The control loop

```text
StepContract + ExecutionEvidence + BoundCriteria
    → BoundWorkflow.evaluate_step → EvaluationResult
    → evaluate_agent_step → AgentControlResult
        .next_action  ∈ continue / retry / replan / rollback
        .feedback     (deterministic, < 150 words, re-injectable)
```

The decision → control-action mapping is exact and deterministic:

| BOUND decision | Agent control action |
| -------------- | -------------------- |
| `ACCEPT`       | `continue`          |
| `RETRY`        | `retry`             |
| `REPLAN`       | `replan`            |
| `ROLLBACK`     | `rollback`          |

The integration layer never invents scores, never modifies a BOUND decision,
never calls an LLM, never knows about a specific framework, and never executes a
rollback or retry itself — it only *translates* the decision into an
instruction the owning agent acts on.

## When to call BOUND

- after a meaningful plan step
- after implementation plus verification
- after a retry
- before deciding to continue refining the same objective

## When NOT to call BOUND

- after every token
- after every file read
- after every shell command
- after every low-level tool call

## The integration spec (machine-readable)

`bound integration-spec` emits a framework-neutral, JSON-serialisable
specification covering *when to call*, *when not to call*, the *required flow*,
the *evidence rule* ("never fabricate unavailable evidence"), the decision →
control mapping, and the invariants an integration must uphold. An agent can read
this spec and wire itself in:

```bash
bound integration-spec
```

## Add BOUND to your agent

Pick the prompt that matches your agent and paste it in. Each is **not
documentation for a human** — it is a prompt designed to be pasted directly into
the named agent.

| Agent | Integration prompt |
| ----- | ----------------- |
| Generic agent | [integrations/generic/INSTALL_BOUND.md](../integrations/generic/INSTALL_BOUND.md) |
| Cline | [integrations/cline/INSTALL_BOUND.md](../integrations/cline/INSTALL_BOUND.md) |
| Claude Code | [integrations/claude-code/INSTALL_BOUND.md](../integrations/claude-code/INSTALL_BOUND.md) |
| Kilo Code | [integrations/kilo-code/INSTALL_BOUND.md](../integrations/kilo-code/INSTALL_BOUND.md) |
| Hermes Agent | [integrations/hermes-agent/INSTALL_BOUND.md](../integrations/hermes-agent/INSTALL_BOUND.md) |

> Wording matters. These are *integration prompts* (e.g. "Integration prompt for
> Cline", "Use BOUND with Cline"), **not** "native X integration". BOUND ships no
> agent-specific code. An integration claims a framework hook only when that
> hook genuinely exists and was inspected in the target environment.

## Reacting to the decision

- **ACCEPT** → stop refining the current step; continue to the next plan
  objective. Explicitly do **not** keep optimising an already-accepted step.
- **RETRY** → keep the current strategy; make one focused correction targeting
  the remaining failed/missing evidence; re-collect evidence and re-evaluate.
  Respect the step's `StepBudget` (e.g. `max_retries`).
- **REPLAN** → stop iterating on the current strategy; choose a materially
  different approach and build a new `StepContract` for it.
- **ROLLBACK** → restore a safe state where possible (e.g. restore a checkpoint,
  `git checkout`), then replan. BOUND does not execute the rollback — the owning
  agent does, using whatever mechanism it confirmed is available.

## Rules you must not break

- Never assume undocumented framework hooks — only use mechanisms you inspected
  and confirmed are available.
- Never fabricate evidence — unobservable signals stay unobservable.
- Never duplicate BOUND's policy logic — call BOUND and use its result.
- Never add an LLM evaluator / LLM-as-judge for the final decision.
- Keep the integration thin and removable — removing BOUND must not require
  restructuring the project.

## A runnable end-to-end loop

`examples/agent_control_loop.py` runs a real three-attempt trajectory against the
exact public API (REPLAN → RETRY → ACCEPT), with no hardcoded decisions:

```bash
uv run python examples/agent_control_loop.py
```
