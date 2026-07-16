<p align="center">
  <strong>Agents that know when good enough is enough.</strong>
</p>

<p align="center">
  <a href="https://github.com/Danny-de-bree/bound/actions/workflows/ci.yml"><img src="https://github.com/Danny-de-bree/bound/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://pypi.org/project/bound-policy/"><img src="https://img.shields.io/pypi/v/bound-policy.svg" alt="PyPI version"></a>
  <a href="https://pypi.org/project/bound-policy/"><img src="https://img.shields.io/pypi/pyversions/bound-policy.svg" alt="Python versions"></a>
  <a href="https://github.com/Danny-de-bree/bound/blob/main/LICENSE"><img src="https://img.shields.io/github/license/Danny-de-bree/bound.svg" alt="License"></a>
</p>

# BOUND

Coding agents are good at continuing. They are less good at knowing when to stop.

**BOUND adds a deterministic control layer between agent execution and the next decision.**

<p align="center">
  <img
    src="assets/bound-agent-workflow.png"
    alt="BOUND deterministic control loop for agent workflows"
    width="100%"
  >
</p>

## Put BOUND in your agent

Install BOUND:

Open the integration prompt for your agent and paste it into the agent's
instructions, skills, rules, or planning context.

- [Generic agent](integrations/generic/INSTALL_BOUND.md)
- [Cline](integrations/cline/INSTALL_BOUND.md)
- [Claude Code](integrations/claude-code/INSTALL_BOUND.md)
- [Kilo Code](integrations/kilo-code/INSTALL_BOUND.md)
- [Hermes Agent](integrations/hermes-agent/INSTALL_BOUND.md)

Recommended setup flow:

```text
1. Install bound-policy
2. Open the integration prompt for your agent
3. Paste it into the agent
4. Let the agent inspect its own workflow and available hooks
5. Let it wire BOUND into meaningful execution boundaries
6. Run a real task
```

For the initial setup, use your agent's strongest **architecture / planning mode**
or a stronger model if available.

That first pass should focus on:

```text
current goal
        ↓
plan and meaningful step boundaries
        ↓
what success means for each step
        ↓
what evidence can actually be observed
        ↓
where BOUND should evaluate
        ↓
how ACCEPT / RETRY / REPLAN / ROLLBACK affect control flow
```

Once that integration is in place, normal execution can use the resulting contracts,
evidence, and BOUND decisions deterministically.

BOUND does not decide what code the agent should write.

It decides whether the result of the current step is good enough to move on.

---

## What happens inside the agent?

BOUND belongs after a meaningful execution step and before the agent decides whether
to keep optimizing the same objective.

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

The agent still owns:

- planning
- reasoning
- tool use
- code changes
- execution

BOUND owns the control decision.

---

## What BOUND uses

BOUND can evaluate observable evidence such as:

- tests
- lint and type checks
- acceptance checks
- expected and unexpected files
- retries
- tool calls
- tokens
- runtime
- rollback availability

Next steps including more looking into semantic varibles.

---

## Decisions

```text
ACCEPT    Good enough. Continue.
RETRY     Make one focused correction.
REPLAN    Choose a materially different strategy.
ROLLBACK  Return to a safe state.
```

The goal is not to optimize forever.

The goal is to know when the current result is sufficient to continue.

> **Good enough is enough. Keep progressing.**

---

## Why BOUND?

A common agent loop looks like this:

```text
task solved
    ↓
tests pass
    ↓
agent keeps refining
    ↓
more calls and changes
    ↓
possible regression
```

BOUND adds an explicit stopping policy:

```text
task solved
    ↓
evidence collected
    ↓
BOUND evaluates
    ↓
ACCEPT
    ↓
continue
```

---

## Learn how BOUND works

The scoring model, evidence mapping, thresholds, weights, and decision rules are
documented separately:

- [Architecture and scoring](architecture/README.md)

---

## Current status

BOUND is experimental.

The formula, heuristics, weights, and thresholds still need validation on real agent
workloads.

The next milestone is dogfooding BOUND inside real coding agents and measuring whether
it reduces unnecessary steps, calls, tokens, retries, and regressions without reducing
task success.

## License

MIT © Danny de Bree