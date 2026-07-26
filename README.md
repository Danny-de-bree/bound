<p align="center">
  <a href="https://github.com/Danny-de-bree/bound/actions/workflows/ci.yml"><img src="https://github.com/Danny-de-bree/bound/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://pypi.org/project/bound-policy/"><img src="https://img.shields.io/pypi/v/bound-policy.svg?cacheSeconds=300" alt="PyPI version"></a>
  <a href="https://pypi.org/project/bound-policy/"><img src="https://img.shields.io/pypi/pyversions/bound-policy.svg" alt="Python versions"></a>
  <a href="https://github.com/Danny-de-bree/bound/blob/main/LICENSE"><img src="https://img.shields.io/github/license/Danny-de-bree/bound.svg" alt="License"></a>
  <a href="https://skills.sh/Danny-de-bree/bound"><img src="https://img.shields.io/badge/skills.sh-install_BOUND-black" alt="Install BOUND from skills.sh"></a>
</p>

# BOUND

BOUND is a deterministic decision harness for coding agents. The agent does the
work; BOUND decides whether to continue, retry, replan, or rollback. No LLM as
judge, no telemetry, no network. Language-neutral — works with any project,
any agent, any language. **The model proposes. The harness decides.**

<p align="center">
  <img src="https://raw.githubusercontent.com/Danny-de-bree/bound/main/assets/bound-agent-workflow.png" alt="A coding agent executes work, BOUND collects evidence and emits a deterministic control decision" width="100%">
</p>

## The four decisions

| Decision | Meaning | Agent action |
| --- | --- | --- |
| **ACCEPT** | Evidence satisfies the approved policy. | Stop optimizing, continue. |
| **RETRY** | The current approach is still viable. | Make one focused correction and collect fresh evidence. |
| **REPLAN** | The current strategy is no longer the right path. | Choose a materially different approach and derive a new step contract. |
| **ROLLBACK** | A hard risk boundary was exceeded. | Restore a previously confirmed safe checkpoint, then replan. |

BOUND emits the signal; the agent performs the action.

## Quickstart — use BOUND with your agent today

Pick your agent:

### Cline

```bash
pip install bound-policy
bound adapter install cline  # generates .cline/mcp/bound.json
cline                        # Cline auto-discovers BOUND's MCP tools
```

Cline sees `bound_evaluate`, `bound_checkpoint`, `bound_rollback` as tools.
No prompt needed — Cline calls BOUND automatically during execution.

### Codex

```bash
pip install bound-policy
bound adapter install codex   # validates Codex CLI, generates MCP config
npx @openai/codex exec "your task"   # Codex calls BOUND tools during exec
```

Requires `OPENAI_API_KEY` or `codex login` for authentication.

### Claude Code

```bash
pip install bound-policy
bound adapter install claude  # validates claude-code CLI
npx @anthropic-ai/claude-code -p "your task"  # use --print for non-interactive
```

Requires `claude login` for authentication. Use `--output-format stream-json`
for structured events that BOUND can evaluate.

### Any other agent (prompt-based)

```bash
pip install bound-policy
bound setup --agent generic
cat .bound/integration-prompt.md   # paste into your agent
```

### Watch it live

```bash
bound ui   # → http://127.0.0.1:8765
```

### Adjust the policy mid-run

Edit `bound-policy.yaml` anytime — the agent's next `bound evaluate` picks up
the new policy automatically.

```bash
bound policy explain bound-policy.yaml   # see what your policy does
```

### Three integration modes

| Mode | How |
|---|---|
| **Prompt** | Agent reads instructions from `bound setup`, calls `bound evaluate` |
| **MCP** | Agent uses BOUND tools via MCP server (`bound mcp`) |
| **Adapter** | BOUND spawns agent as child, controls loop via JSONL |

## License

MIT © Danny de Bree. See [LICENSE](LICENSE).

## Guides

- **[Python & CLI reference](docs/python-usage.md)** — install, `bound setup`, `bound doctor`, `bound init`, collectors, Python API
- **[Architecture & scoring model](architecture/README.md)** — how the bounded-utility formula works
- **[Decision lineage](docs/lineage.md)** — run history, evidence provenance, inspection
- **[Default policy](src/bound/default_policy.yaml)** — a fully documented starting point
- **[Agent integration guides](integrations/)** — Cline, Codex, Claude Code, Kilo Code, Hermes, and generic
- **[BOUND skill](skills/bound/SKILL.md)** — the agent-ready skill prompt
- **[Demo scenario](docs/demo-scenario.md)** — canonical end-to-end walkthrough
