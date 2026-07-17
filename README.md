<p align="center">
  <strong>Agents that know when good enough is enough.</strong>
</p>

<p align="center">
  <a href="https://github.com/Danny-de-bree/bound/actions/workflows/ci.yml"><img src="https://github.com/Danny-de-bree/bound/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://pypi.org/project/bound-policy/">
  <img src="https://img.shields.io/pypi/v/bound-policy.svg?cacheSeconds=300" alt="PyPI version"></a>
  <a href="https://pypi.org/project/bound-policy/"><img src="https://img.shields.io/pypi/pyversions/bound-policy.svg" alt="Python versions"></a>
  <a href="https://github.com/Danny-de-bree/bound/blob/main/LICENSE"><img src="https://img.shields.io/github/license/Danny-de-bree/bound.svg" alt="License"></a>
  <a href="https://skills.sh/Danny-de-bree/bound">
  <img src="https://img.shields.io/badge/skills.sh-install_BOUND-black" alt="Install BOUND from skills.sh">
</a>
</p>

# BOUND

**The deterministic control harness for AI agents.**

Coding agents are good at continuing. They are less good at knowing when to stop.

BOUND sits between execution and the agent's next decision, turning observable evidence into a deterministic control signal:

**ACCEPT · RETRY · REPLAN · ROLLBACK**

<p align="center">
  <img src="https://raw.githubusercontent.com/Danny-de-bree/bound/main/assets/bound-agent-workflow.png" alt="BOUND deterministic control harness for AI agent workflows" width="100%">
</p>

## Put BOUND in your agent

### Install in ChatGPT / OpenAI Skills

1. Download [`BOUND-agent-skill.zip`](https://github.com/Danny-de-bree/bound/releases/latest/download/BOUND-agent-skill.zip).
2. In ChatGPT, open **Profile → Skills → Create → Upload from your computer**.
3. Select the ZIP, review the scan result, and install the skill.
4. Start a new chat and invoke it with `@BOUND`, or let ChatGPT select it when
   your request matches the skill description.

The Skills menu must be available for your ChatGPT account or workspace.
Personal Skills may need to be installed separately in ChatGPT on the web and
in the desktop app because those installations do not automatically sync.

### Install with skills.sh-compatible agents

BOUND includes an open `SKILL.md` skill for Codex, Claude Code, Cline, Kilo
Code, and other compatible coding agents:

```bash
npx skills add Danny-de-bree/bound --skill bound
```

To install only for Codex without interactive selection:

```bash
npx skills add Danny-de-bree/bound --skill bound --agent codex -y
```

The skill lives in [`skills/bound/`](skills/bound/) and teaches the agent to
install BOUND, establish meaningful evaluation boundaries, collect real
evidence, report the numeric A/I/R/C/S/T calculation, and react to
`ACCEPT / RETRY / REPLAN / ROLLBACK`.

### Or use a paste-ready integration prompt

Choose your agent, open its integration prompt, and paste it into a new session:

- [Cline](integrations/cline/INSTALL_BOUND.md)
- [Claude Code](integrations/claude-code/INSTALL_BOUND.md)
- [Kilo Code](integrations/kilo-code/INSTALL_BOUND.md)
- [Hermes Agent](integrations/hermes-agent/INSTALL_BOUND.md)
- [Any other agent](integrations/generic/INSTALL_BOUND.md)

**That's it.**

The prompt tells the agent to install BOUND, inspect its workflow, identify meaningful evaluation boundaries, and wire the harness into its control loop.

For the initial setup, use your agent's strongest **architecture or planning mode** — or a stronger model if available. This first pass should focus on defining the plan, meaningful step boundaries, acceptance criteria, risks, budgets, and observable evidence.

```text
Paste integration prompt into agent
              ↓
Agent installs BOUND
              ↓
Agent inspects project + workflow
              ↓
Agent defines goals, contracts, and evidence
              ↓
You review the integration plan
              ↓
Agent wires BOUND into the workflow
              ↓
Run your agent with BOUND
```

Once configured, the normal execution loop can use BOUND deterministically. No LLM judge is required for observable criteria.

## The control loop

BOUND belongs after a meaningful execution step and before the agent decides whether to keep optimizing the same objective.

```text
Agent executes
      ↓
Observable evidence
      ↓
BOUND evaluates
      ↓
ACCEPT / RETRY / REPLAN / ROLLBACK
      ↓
Agent changes its next action
```

Conceptually:

```python
result = workflow.evaluate_step(
    contract=contract,
    evidence=evidence,
    criteria=criteria,
)

match result.decision:
    case "ACCEPT":
        continue_to_next_step()
    case "RETRY":
        retry_current_approach()
    case "REPLAN":
        choose_new_strategy()
    case "ROLLBACK":
        rollback()
```

The agent still owns planning, reasoning, tool use, code changes, and execution.

**BOUND decides whether the current result is good enough to move on.**

## Four decisions

| Decision | Meaning |
| --- | --- |
| **ACCEPT** | Good enough. Stop optimizing this step and continue. |
| **RETRY** | Keep the current approach and make one focused correction. |
| **REPLAN** | Stop iterating on the current strategy and choose another approach. |
| **ROLLBACK** | A hard risk boundary was exceeded. Return to a safe state. |

BOUND can use observable evidence such as tests, lint and type checks, acceptance checks, expected changes, retries, tool calls, token usage, runtime, and rollback availability.

> **Good enough is enough. Keep progressing.**

## Why BOUND?

Without an explicit stopping policy, an agent can continue working after the task is already satisfactory:

```text
task solved
    ↓
tests pass
    ↓
more refinement
    ↓
more calls and changes
    ↓
possible regression
```

BOUND adds an explicit control point:

```text
task solved
    ↓
evidence collected
    ↓
BOUND evaluates
    ↓
ACCEPT
    ↓
continue to the next goal
```

BOUND does not replace the agent. It is a thin control harness around the agent's execution loop.

## How it works

BOUND is the **control harness**.

Under the hood:

```text
Contracts + evidence  → evaluation layer
BoundPolicy           → deterministic decision engine
BOUND                 → control harness
Integration prompts   → adoption layer
```

The scoring model, evidence mapping, thresholds, weights, calculations, and exact decision rules live in the technical documentation:

**[Read the architecture and scoring model →](architecture/README.md)**

## Manual installation

If you want to integrate BOUND directly:

```bash
pip install bound-policy
```

Or:

```bash
uv add bound-policy
```

The PyPI distribution is `bound-policy`; the Python import and CLI are `bound`.

## Current status

BOUND is experimental.

The scoring heuristics, weights, thresholds, and integration patterns still need broader validation on real agent workloads.

The next milestone is dogfooding BOUND inside real coding agents and measuring whether it reduces unnecessary post-solution work, calls, tokens, retries, and regressions without reducing task success.

## Development

```bash
git clone https://github.com/Danny-de-bree/bound.git
cd bound

uv sync
uv run pytest
uv run ruff check .
```

## License

MIT © Danny de Bree