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

```bash
pip install bound-policy
cd your-project
bound use cline       # auto-detects Cline, installs MCP config
bound ui              # → http://127.0.0.1:8765
```

For agents with a CLI:

```bash
bound run --agent claude "Fix the failing validation tests"
```

### Control modes

| Mode | Session owner | Description |
| --- | --- | --- |
| **Integrated** | The agent | Agent calls BOUND via MCP tools. Fastest compatibility. |
| **Supervised** | BOUND | BOUND starts agent as child process, reads structured output, evaluates independently. |
| **Controlled** | BOUND | BOUND owns a bidirectional session with interrupt, resume, and candidate branching. |

## Agent capability matrix

| Agent | Detection | Integration | Events | Process | Interrupt | Resume |
| --- | --- | --- | --- | --- | --- | --- |
| Cline | ✅ tested | MCP tools | ✅ partial | ❌ editor-managed | ❌ | ❌ |
| Claude Code | ✅ tested | subprocess | ✅ structured | ✅ tested | ❌ planned | ❌ planned |
| Codex | ✅ tested | MCP + CLI | ✅ structured | ✅ planned | ✅ planned | ✅ planned |
| Generic | ✅ tested | ACP via --agent-command | config-based | config-based | config-based | config-based |

### Capability status key

- ✅ **tested** — implemented and verified with real agent
- ⬜ **planned** — design exists, implementation pending
- ❌ **unsupported** — cannot be provided (e.g., Cline has no process API)

## CLI reference

| Command | Description | Status |
| --- | --- | --- |
| `bound use <agent>` | Configure agent as project default | ✅ v1.0 |
| `bound status` | Show project config and agent detection | ✅ v1.0 |
| `bound run --agent <agent> "task"` | Start a supervised run | ✅ v1.0 |
| `bound doctor` | Diagnose project and agent setup | ✅ existing |
| `bound ui` | Local dashboard (http://127.0.0.1:8765) | ✅ existing |
| `bound evaluate` | Evaluate action with pre-supplied scores | ✅ existing |
| `bound checkpoint` | Git-based checkpoint management | ✅ existing |
| `bound policy` | Validate, explain, or hash policy files | ✅ existing |
| `bound mcp` | Start stdio MCP server for agent tools | ✅ existing |

## Guides

- **[Architecture & scoring model](architecture/README.md)** — how the bounded-utility formula works
- **[Decision lineage](docs/lineage.md)** — run history, evidence provenance, inspection
- **[Default policy](bound-policy.yaml)** — a fully documented starting point
- **[Agent integration guides](integrations/)** — Cline, Codex, Claude Code, and generic
- **[BOUND skill](skills/bound/SKILL.md)** — the agent-ready skill prompt

## License

MIT © Danny de Bree. See [LICENSE](LICENSE).
