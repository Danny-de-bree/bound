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

- [Codex](integrations/codex/INSTALL_BOUND.md)
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

## Decision Lineage (v0.7.0)

BOUND v0.7.0 can record every evaluation as a reproducible, **append-only
local lineage** — `contract → evidence → scores → decision → agent outcome` —
under `.bound/runs/<run_id>/` (`run.json` + `events.jsonl`). Lineage is
**opt-in per run** and fully backwards compatible: if you never start a run,
nothing is recorded. Disable it with `BOUND_LINEAGE_DISABLED=1`.

### CLI

```bash
# Start one run per task.
bound run start "Add input validation to the registration endpoint"

# Evaluate a meaningful boundary into the run.
bound evaluate --run <run_id> --step PHASE-001 --attempt 1 \
    --action "..." --goal "..." \
    --acceptance 0.3333 --influence 0.0 --risk 0.0 --cost 0.0 \
    --threshold 0.7 --retry-margin 0.1

# Record the REAL follow-up action you took.
bound outcome --run <run_id> --step PHASE-001 --attempt 1 \
    --decision REPLAN --note "switched strategy to validator + parametrized tests"

# Explicitly close the run, then inspect the decision tree.
bound run finish <run_id> --status completed
bound inspect <run_id>
bound run list
bound run delete <run_id>
```

### REPLAN → ACCEPT

The canonical v0.7.0 flow is one run, two attempts: attempt 1 scores `1/3`
(`A=0.3333`) → `REPLAN` (switch strategy, new `PHASE-001-R1` step); attempt 2
scores `3/3` (`A=1.0000`) → `ACCEPT` (continue to the next step). `bound inspect`
renders the whole history as a Step → Attempt → Outcome tree:

```text
Run run_feb444156b42b838db38
Task: Add input validation to the registration endpoint
Status: completed

Step 1 · Implement input validation · replanned
└── Attempt 1 · REPLAN · 1/3 checks
    └── Outcome: switched strategy to validator + parametrized tests
        Action: replan (REPLANNED)
        Score S=0.3333 (A=0.33 I=0.00 R=0.00 C=0.00) T=0.7000

Step 2 · Implement input validation (replan) · completed
└── Attempt 2 · ACCEPT · 3/3 checks
    └── Outcome: continued to next step
        Action: continue (CONTINUED)
        Score S=1.0000 (A=1.00 I=0.00 R=0.00 C=0.00) T=0.7000
```

The log is append-only: a replan emits a *new* `step_started` with `attempt+1`
(and a `-R<N>`-suffixed contract id); earlier attempts are never rewritten. A
missing `run_finished` marks an incomplete/crashed run, and the log stays
readable. See [`examples/lineage_demo.py`](examples/lineage_demo.py) for a
runnable end-to-end version and [`docs/lineage.md`](docs/lineage.md) for the
data model, Python API, and CLI reference.

### What is and isn't stored

**Stored** (under `.bound/runs/<run_id>/`): the task, stable contract/step ids,
attempt numbers, the four BOUND scores (`A/I/R/C`), the computed `score`,
`threshold`, `decision`, a fixed `reason_code`, and your recorded follow-up
`next_action` + `note`. Optional string `metadata` is allowed but scrubbed for
secrets by default.

**Never stored**: prompts, model outputs, tokens, source code, or anything not
in the lineage schema. Cost/token/runtime are only persisted if you put them in
`metadata` (they are not BOUND policy inputs).

### Verified Evidence & Trust Provenance

Every piece of evidence in a BOUND lineage trace carries **trust provenance** —
a 7-level enum that records *how much the source can be trusted*, deliberately
separate from the free-form `source` string (which records *where* it came from).

```python
EvidenceProvenance.OBSERVED   # Directly measured by an independent collector
EvidenceProvenance.VERIFIED   # Independently reproduced by a BOUND collector
EvidenceProvenance.ATTESTED   # Signed/attested by a trusted third party
EvidenceProvenance.EVALUATED  # Derived by the deterministic BOUND evaluator
EvidenceProvenance.CLAIMED    # Agent self-report — no independent confirmation
EvidenceProvenance.DEFAULTED  # Substituted policy-neutral value (no source existed)
EvidenceProvenance.MISSING    # No evidence collected at all for this signal
```

**Agent self-report is always CLAIMED, never VERIFIED.** The BOUND honesty model
forbids silently fabricating stronger provenance from weaker. A `VERIFIED` check
requires a BOUND-controlled collector to have independently executed the
verification.

Each check also records an `EvidenceStatus`:

```text
PASSED    — Check was observed and passed
FAILED    — Check was observed and did not pass
MISSING   — No evidence was collected for this check
INVALID   — Evidence was collected but is unusable (crash, tampered hash)
STALE     — Evidence was collected but is too old to trust
```

**Missing ≠ zero.** Execution telemetry (`retry_count`, `tool_call_count`,
`token_usage`, `runtime_seconds`) is modelled as `EvidenceMetric | None`. A
measured zero (`EvidenceMetric(value=0, provenance=OBSERVED)`) is distinct from
an unmeasured signal (`value is None` → `MISSING`). Legacy schema-1.0 traces
with bare-number telemetry auto-migrate on construction (provenance `MISSING`,
never upgraded).

Trust provenance is part of the lineage: `bound inspect` shows provenance per
check, and `--only-unverified` filters to only
CLAIMED/DEFAULTED/MISSING evidence.

### Policy Configuration (bound-policy.yaml)

A `bound-policy.yaml` file is the **single source of decision authority** for a
run. A human reviews and approves it before execution; the agent can never
weaken it mid-run.

```yaml
schema_version: "1.0"
policy:
  id: coding-default
  version: "1.0"
collectors:
  pytest:
    type: pytest
  lint:
    type: command
    command: ["python", "-m", "ruff", "check", "."]
```

Three policy mechanisms work together:

**HardGate (blockers)** — uncompensable checks that can never be overridden by
positive weighted scores. A failing blocker always produces its configured
outcome (`RETRY` / `REPLAN` / `ROLLBACK`), regardless of the score.

**WeightedSignal (quality)** — soft contributions to the score with importance
tiers: `high` / `medium` / `low` / `ignore`. These map to effective weights
that affect the acceptance metric but can never override a blocker.

**BudgetDimension (limits)** — soft/hard limits per dimension (attempts, tool
calls, tokens, runtime, financial cost). A soft limit triggers a warning
action; a hard limit enforces a terminal action.

```bash
# CLI subcommands for policy configuration
bound policy validate bound-policy.yaml   # Validate schema + warn about gaps
bound policy explain  bound-policy.yaml   # Show effective gates/weights/budgets
bound policy hash     bound-policy.yaml   # Print canonical sha256 hash
```

All three accept `--json` for machine-readable output. A documented
[`default_policy.yaml`](src/bound/default_policy.yaml) (`coding-default@1.0`)
ships with BOUND and exercises every section of the schema.

### Command Collectors

BOUND-controlled collectors **execute** verification independently of the agent,
not merely parse its self-report. An agent cannot inject arbitrary commands:
only pre-registered commands (declared in `bound-policy.yaml`) run by name.

```python
CommandCollector      # Execute a preconfigured command, capture output + exit code
PytestCollector       # Run pytest; a pass requires exit 0 AND > 0 tests
JUnitCollector        # Hash + freshness-check a trusted JUnit XML artefact
GitCollector          # Prove a clean tree (git status --porcelain)
BudgetCollector       # Collect observed budget telemetry
ProcessRuntimeCollector # Record the runtime of a process
```

All collectors are **fail-safe**: timeout, crash, parse-fail, zero-tests, or
stale artefact never yield a `VERIFIED` pass. They produce evidence with
`VERIFIED` or `OBSERVED` provenance — the strongest trust levels available.

### Decision Assurance & Gating

The `AssuranceAssessment` computes a `DecisionAssurance` level from the
provenance-restricted and decision-critical checks:

```text
VERIFIED     — All critical evidence is independently verified
MIXED        — A mix of verified and weaker (claimed) evidence
CLAIMED      — Decision leans on agent self-report for critical evidence
INSUFFICIENT — Required evidence is missing or invalid
```

When assurance is `CLAIMED` or `INSUFFICIENT`, a candidate `ACCEPT` may be
**gated (downgraded)** to the contract's `on_missing` / `on_claimed` action.
The `EvaluationResult` exposes both `candidate_decision` (what the score
implies) and `final_decision` (what the policy emits after gating), so a trace
always answers "was this ACCEPT backed by verified evidence?"

`bound inspect` shows the candidate vs final decision and the assurance level
for every evaluation in the run.

### Privacy Hardening

Raw command output is **not stored by default**. Only a `sha256:` content hash
and a short, redacted, size-capped summary are retained on emitted evidence:

```text
stdout_hash: sha256:1dfc885c2027ed0f0920aa2e0b5ad11...
stdout_summary: "3 passed, 0 failed in 0.42s"
stdout_raw:   None   # ← not stored by default
```

**Secret redaction** runs over captured output *before* hashing, summarising, or
retention, so a secret can never reach a trace. Redaction patterns mask access
keys, tokens, environment variables, and other common secret formats.

To retain the full redacted output for debugging, pass `store_raw=True` to the
collector constructor or per-run invocation. Even with `store_raw=True`, the
retained output is still redacted before storage.

### Policy Lifecycle

A policy moves through four lifecycle states, recorded as append-only events in
the lineage trace:

```text
PROPOSED → VALIDATED → APPROVED → ACTIVATED
```

1. **PROPOSED** — The policy has been authored (parsed from `bound-policy.yaml`)
   but not yet reviewed.
2. **VALIDATED** — The schema has been validated and warnings surfaced.
3. **APPROVED** — A human has reviewed and approved the policy (records the
   approver and approval time).
4. **ACTIVATED** — The policy is the active governing policy for the run.

The `policy_changed_since()` function detects a **material policy change**
between two snapshots by comparing canonical hashes. Any material weakening
(blocker removal, weight reduction, budget increase, scope expansion, provenance
narrowing, or collector replacement) changes the hash and requires renewed human
approval. An agent cannot approve its own policy or weaken the active one
mid-run.

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
bound-policy.yaml     → policy configuration layer
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

### Demo examples

BOUND ships with runnable examples that demonstrate the v0.7 feature set with
no LLM, no network, and no hardcoded decisions:

- [`examples/golden_demo.py`](examples/golden_demo.py) — End-to-end
  policy-configured flow: user intent → generated `bound-policy.yaml` →
  validation → human explanation → PROPOSED → APPROVED → ACTIVATED → canonical
  hash → attempt 1 (pytest 1/3 VERIFIED → REPLAN) → attempt 2 (pytest 3/3
  VERIFIED → ACCEPT). Every number is proven by a local append-only lineage
  trace and a generated `INTEGRATION_REPORT.md`.

- [`examples/verified_evidence_demo.py`](examples/verified_evidence_demo.py) —
  REPLAN → ACCEPT with live, independent collectors. An agent claims "all tests
  pass", but BOUND's `PytestCollector` actually runs pytest and `GitCollector`
  actually inspects the tree. The final decision does not depend on CLAIMED
  evidence.

- [`examples/lineage_demo.py`](examples/lineage_demo.py) — The canonical
  REPLAN → ACCEPT lineage trace with `bound run start`, `bound evaluate`,
  `bound outcome`, and `bound inspect`.

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