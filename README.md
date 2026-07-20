<p align="center">
  <strong>The deterministic control harness for coding agents.</strong>
</p>

<p align="center">
  <a href="https://github.com/Danny-de-bree/bound/actions/workflows/ci.yml"><img src="https://github.com/Danny-de-bree/bound/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://pypi.org/project/bound-policy/"><img src="https://img.shields.io/pypi/v/bound-policy.svg?cacheSeconds=300" alt="PyPI version"></a>
  <a href="https://pypi.org/project/bound-policy/"><img src="https://img.shields.io/pypi/pyversions/bound-policy.svg" alt="Python versions"></a>
  <a href="https://github.com/Danny-de-bree/bound/blob/main/LICENSE"><img src="https://img.shields.io/github/license/Danny-de-bree/bound.svg" alt="License"></a>
  <a href="https://skills.sh/Danny-de-bree/bound"><img src="https://img.shields.io/badge/skills.sh-install_BOUND-black" alt="Install BOUND from skills.sh"></a>
</p>

# BOUND

Coding agents act fast but decide poorly. BOUND sits between a meaningful
execution step and the agent's next action: it collects objective evidence,
applies a human-approved policy, and emits one of four deterministic control
signals so the agent can stop, retry, change strategy, or return to safety.

**The model proposes. The harness decides.**

<p align="center">
  <img src="https://raw.githubusercontent.com/Danny-de-bree/bound/main/assets/bound-agent-workflow.png" alt="A coding agent executes work, BOUND collects evidence and emits a deterministic control decision" width="100%">
</p>

Self-reported evidence stays `CLAIMED` until an independent collector verifies
it. BOUND never uses an LLM as the final judge.

## Quick start: add BOUND to your agent (30 seconds)

Pick one option. The agent will inspect your project, propose a
`bound-policy.yaml`, and show it to you for review.

### Option A — skills.sh (Codex, Cline, Claude Code, Kilo Code, …)

```bash
npx skills add Danny-de-bree/bound --skill bound
```

Then ask your agent:

```text
Integrate BOUND into this repository. Inspect tests, linting, type checks,
build commands, git workflow, and task boundaries. Propose a bound-policy.yaml
and show me the plan before modifying the project.
```

### Option B — paste-ready prompt (any agent)

Open the repository, paste the prompt above, and let the agent generate your
policy. See the [generic integration guide](integrations/generic/INSTALL_BOUND.md)
for a version with inline instructions.

### Option C — ChatGPT / OpenAI Skills

Download [`BOUND-agent-skill.zip`](https://github.com/Danny-de-bree/bound/releases/latest/download/BOUND-agent-skill.zip),
upload it from ChatGPT's Skills menu, review the scan result, and install it.
Start a new chat and invoke `@BOUND`.

### What happens next

Your agent reads your project structure, identifies meaningful evaluation
boundaries, configures evidence collectors, and generates a policy. **You
review the policy before the agent changes anything.** Once approved, the agent
executes each step through BOUND — collecting evidence, scoring it against the
policy, and acting on the deterministic signal.

## The policy: what you get

The generated `bound-policy.yaml` defines the gate for every step: required
checks, allowed evidence, budgets, risk boundaries, and what happens when
evidence is missing or only claimed.

```yaml
schema_version: "1.0"
policy:
  id: coding-default
  version: "1.0"
collectors:
  pytest:
    type: pytest
acceptance_checks:
  - id: tests-pass
    importance: blocker
    required: true
    collector: pytest
    minimum_assurance: verified
    on_failure: retry
    on_missing: replan
```

Validate it with `bound policy validate bound-policy.yaml`. A fully documented
default is at [`src/bound/default_policy.yaml`](src/bound/default_policy.yaml).

## The four control signals

| Decision | Meaning | Agent action |
| --- | --- | --- |
| **ACCEPT** | Evidence satisfies the approved policy. | Stop optimizing, continue. |
| **RETRY** | The current approach is still viable. | Make one focused correction and collect fresh evidence. |
| **REPLAN** | The current strategy is no longer the right path. | Choose a materially different approach and derive a new step contract. |
| **ROLLBACK** | A hard risk boundary was exceeded. | Restore a previously confirmed safe checkpoint, then replan. |

BOUND emits the signal. The agent performs the action. BOUND never modifies or
rolls back the workspace itself.

## Agent integration guides

- [Codex](integrations/codex/INSTALL_BOUND.md)
- [Cline](integrations/cline/INSTALL_BOUND.md)
- [Claude Code](integrations/claude-code/INSTALL_BOUND.md)
- [Kilo Code](integrations/kilo-code/INSTALL_BOUND.md)
- [Hermes Agent](integrations/hermes-agent/INSTALL_BOUND.md)
- [Any other agent](integrations/generic/INSTALL_BOUND.md)

## Reference

- [Architecture and scoring model](architecture/README.md)
- [Decision lineage](docs/lineage.md)
- [Default policy](src/bound/default_policy.yaml)
- [BOUND skill](skills/bound/SKILL.md)
- [Python package & CLI usage](docs/python-usage.md)

## What BOUND does not do

BOUND does not decide what code the agent should write, replace its planner,
use an LLM as the final judge, fabricate telemetry, upgrade agent claims to
verified evidence, or perform retry, replan, or rollback actions. It is
deliberately a thin control harness around an existing agent workflow.

## Project status

BOUND is experimental alpha software. Its policy model and integrations need
broader validation on real agent workloads. The immediate goal is to measure
whether BOUND helps coding agents stop closer to the first satisfactory
solution, reduces unnecessary retries and tool use, and prevents weakly
evidenced results from being accepted without reducing task success.

## License

MIT © Danny de Bree
