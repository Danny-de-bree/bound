<p align="center">
  <a href="https://github.com/Danny-de-bree/bound/actions/workflows/ci.yml"><img src="https://github.com/Danny-de-bree/bound/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://pypi.org/project/bound-policy/"><img src="https://img.shields.io/pypi/v/bound-policy.svg?cacheSeconds=300" alt="PyPI version"></a>
  <a href="https://pypi.org/project/bound-policy/"><img src="https://img.shields.io/pypi/pyversions/bound-policy.svg" alt="Python versions"></a>
  <a href="https://github.com/Danny-de-bree/bound/blob/main/LICENSE"><img src="https://img.shields.io/github/license/Danny-de-bree/bound.svg" alt="License"></a>
</p>

# BOUND — Deterministic Agent Execution Runtime

BOUND is a deterministic control harness for coding agents. Use the coding
agent already installed on your machine. BOUND adds evidence-based acceptance,
retries, replanning, checkpoints, rollback, candidate isolation, and replay.

**The agent proposes changes. BOUND controls what happens next.**

No LLM as judge, no telemetry, no network required.

## The four decisions

| Decision | Meaning | Agent action |
| --- | --- | --- |
| **ACCEPT** | Evidence satisfies the approved policy. | Stop optimizing, continue. |
| **RETRY** | The current approach is still viable. | Make one focused correction and collect fresh evidence. |
| **REPLAN** | The current strategy is no longer the right path. | Choose a materially different approach and derive a new step contract. |
| **ROLLBACK** | A hard risk boundary was exceeded. | Restore a previously confirmed safe checkpoint, then replan. |

## Quickstart

Pick your agent:

### Cline (VS Code)
```bash
pip install bound-policy
cd your-project
bound use cline       # detects .cline/, writes .cline/mcp/bound.json
                       # Cline sees BOUND tools: evaluate, checkpoint, decide, etc.
bound ui              # → http://127.0.0.1:8765
```

### Claude Code
```bash
pip install bound-policy
bound use claude-code  # detects claude on PATH
bound run --agent claude-code "Fix the failing validation tests"
# BOUND spawns Claude Code, reads stream-json output, runs pytest as evidence,
# and decides ACCEPT/RETRY/REPLAN based on results.
```

### Codex
```bash
pip install bound-policy
bound use codex        # detects codex on PATH
bound run --agent codex "Implement input validation"
```

### Any other agent
```bash
bound use generic --command "my-agent --jsonl"
bound run --agent generic --agent-command "my-agent --jsonl" "task"
```

### Watch it live
```bash
bound ui              # → http://127.0.0.1:8765  (Runs + Plans tabs)
bound status          # shows project, agent, control mode, last run
```

## How it works

### Integrated mode (Cline)
```
Cline reads .cline/mcp/bound.json → connects to BOUND MCP server via stdio.
During execution, Cline calls MCP tools at boundaries:

  bound_session_start → bound_step_start → bound_evidence_collect
  → bound_decide → {decision: "ACCEPT|RETRY|REPLAN|ROLLBACK"}
  → bound_checkpoint_create → bound_session_finish
```

### Supervised mode (Claude Code, Codex, generic)
```
bound run --agent claude-code "task"
  → BOUND detects agent → spawns agent process
  → reads structured JSON output → detects step boundaries
  → runs pytest/lint as independent evidence
  → ACCEPT (pass) | RETRY (fix) | REPLAN (new approach) | ROLLBACK (restore checkpoint)
  → repeats until accepted or budgets exhausted
```

## Agent capability matrix

| Agent | Detection | MCP tools | Process control | Evidence | RETRY | REPLAN |
| --- | --- | --- | --- | --- | --- | --- |
| Cline | ✅ tested | ✅ 9 tools | ❌ editor-managed | via MCP | via MCP | via MCP |
| Claude Code | ✅ tested | via MCP | ✅ subprocess | ✅ pytest | ✅ reinvoke | ✅ reinvoke |
| Codex | ✅ tested | via MCP | ✅ subprocess | ✅ pytest | ✅ reinvoke | ✅ reinvoke |
| Generic | config | config | config | config | config | config |

✅ tested · ⬜ planned · ❌ unsupported

## CLI reference

| Command | Description |
| --- | --- |
| `bound use <agent>` | Detect agent, write project config, install MCP integration |
| `bound status` | Show agent, control mode, policy, last run, dashboard URL |
| `bound run --agent <agent> "task"` | Start supervised agent run with full control loop |
| `bound run --project <dir> --plan plan.md` | Explicit project/plan overrides |
| `bound run --agent-command "cmd"` | Custom agent binary for generic/unlisted agents |
| `bound doctor` | Diagnose Python, git, agent, MCP, policy, collectors |
| `bound ui` | Local dashboard with Runs + Plans tabs |
| `bound evaluate` | Score an action against policy (A/I/R/C) |
| `bound checkpoint create/inspect/list` | Git-based checkpoint management |
| `bound policy validate/explain/hash` | Policy file management |
| `bound mcp` | Start stdio MCP server (used by Cline/Codex) |

## Python API

```python
from bound.supervised_runner import SupervisedRunner, SupervisedConfig

cfg = SupervisedConfig(agent_id="claude-code", max_retries=2, max_replans=2)
runner = SupervisedRunner(cfg)
result = runner.run("Fix the failing validation tests")
print(f"Decision: {result.decision}, Attempts: {result.attempts}")
```

```python
from bound.config import load_project_config
from bound.agent_discovery import detect_all_agents

cfg = load_project_config()
agents = detect_all_agents(cfg.project_root)
for a in agents:
    print(f"{a.display_name} ({a.agent_id}) — {a.confidence}")
```

```python
from bound.plan_model import Plan, create_plan_version, require_replan

plan = Plan(plan_id="oauth", project_id="/my-project")
v1 = create_plan_version(plan, content="# Phase 1\n- [ ] Add endpoint", source="file")
v2 = require_replan(plan, v1, new_content="# Phase 1\n- [x] Add endpoint\n- [ ] Store session", decision_id="dec-1")
```
