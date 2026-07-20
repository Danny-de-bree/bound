# BOUND — Python & CLI Reference

This document covers everything you need to use BOUND as a Python library and
from the command line. It is self-contained — if you only read this, you can
install, configure, and run BOUND.

## Installation

```bash
pip install bound-policy
# or
uv add bound-policy
```

- **PyPI package:** `bound-policy`
- **Python import:** `import bound`
- **CLI executable:** `bound`
- Requires Python 3.11+.

## CLI Reference

All commands run as `bound <subcommand>`. Add `-v` / `-vv` for verbose logging.

### `bound policy validate <file>`

Parse and validate a `bound-policy.yaml`. Reports warnings about blockers
without collectors, claimed-only checks, and unmeasurable criteria. Exits `0`
valid / `1` invalid / `2` usage.

```bash
bound policy validate bound-policy.yaml
bound policy validate bound-policy.yaml --json
```

### `bound policy explain <file>`

Render a human-readable explanation of the policy's gates, signals, and budgets.

```bash
bound policy explain bound-policy.yaml
```

### `bound policy hash <file>`

Canonicalise the policy and print its SHA-256 hash (`sha256:<hex>`). Every
BOUND decision records this hash for reproducibility.

```bash
bound policy hash bound-policy.yaml
bound policy hash bound-policy.yaml --json
```

### `bound evaluate`

Evaluate a proposed action against BOUND's bounded-utility criteria. Prints
auditable JSON to STDOUT and a steering prompt to STDERR.

| Flag | Required | Description |
| --- | --- | --- |
| `--action` | ✅ | Description of the proposed action. |
| `--goal` | ✅ | The larger goal the action advances. |
| `--acceptance` | ✅ | Score A in `[0, 1]`. |
| `--influence` | ✅ | Downstream influence I in `[-1, 1]`. |
| `--risk` | ✅ | Risk penalty R in `[0, 1]`. |
| `--cost` | ✅ | Resource penalty C in `[0, 1]`. |
| `--acceptance-weight` | | Weight for A (default `1.0`). |
| `--influence-weight` | | Weight for I (default `1.0`). |
| `--risk-weight` | | Weight for R (default `1.0`). |
| `--cost-weight` | | Weight for C (default `1.0`). |
| `--weight` | | Deprecated alias for `--acceptance-weight`. |
| `--threshold` | ✅ | Decision threshold T (>= 0). |
| `--retry-margin` | | Margin below T for retry (default `0.1`). |
| `--run` | | Record into lineage run `<RUN_ID>` (requires `--step`). |
| `--step` | | Contract/phase id for the lineage step. |
| `--attempt` | | One-based attempt number (default `1`). |
| `--description` | | Optional step description for lineage. |
| `--context` | | Optional additional context. |
```bash
bound evaluate \
    --action "Add input validation to the registration endpoint" \
    --goal "Secure the user signup flow" \
    --acceptance 0.85 --influence 0.2 --risk 0.1 --cost 0.3 \
    --threshold 0.7 --retry-margin 0.1
```

### `bound evaluate-workflow`

Derive scores from coding-workflow signals (test pass rate, lint/type-check
status, retry/tool-call counts, …) via `CodingWorkflowEvaluator`.

Supports: `--action`, `--goal`, `--tests-pass-rate`, `--lint-passed` /
`--no-lint-passed`, `--type-check-passed` / `--no-type-check-passed`,
`--required-checks-passed`, `--retry-count`, `--tool-call-count`,
`--token-usage`, `--execution-time-seconds`, `--files-changed`,
`--unexpected-files-changed`, `--rollback-available` / `--no-rollback-available`,
`--influence`, plus all weight/threshold flags from `bound evaluate`.

### `bound outcome`

Record an agent's follow-up action within a lineage run.

| Flag | Required | Description |
| --- | --- | --- |
| `--run` | ✅ | Owning run id. |
| `--step` | ✅ | Contract/phase id of the evaluated step. |
| `--decision` | ✅ | `ACCEPT`, `RETRY`, `REPLAN`, or `ROLLBACK`. |
| `--attempt` | | Attempt number (default `1`). |
| `--evaluation-id` | | Evaluation to respond to (auto-resolved when omitted). |
| `--next-action` | | Agent follow-up action (derived from `--decision`). |
| `--reason-code` | | Reason code (derived from `--next-action`). |
| `--note` | | Free-text note (e.g. "switched strategy"). |
| `--json` | | Emit JSON. |

```bash
bound outcome --run <run_id> --step PHASE-001 --attempt 1 \
    --decision REPLAN --note "switched strategy to parametrized tests"
```

### `bound run start` / `finish` / `list` / `delete`

```bash
bound run start "Implement authentication" --metadata phase=PHASE-001
bound run finish <run_id> --status completed
bound run finish <run_id> --status failed --note "out of retries"
bound run list
bound run list --json
bound run delete <run_id>
```

All accept `--json`. `run finish` accepts `--status` (`completed`,
`interrupted`, `failed`) and `--note`.

### `bound inspect <run_id>`

Replay `events.jsonl` and render the decision lineage as a Step → Attempt →
Outcome tree.

```bash
bound inspect <run_id>
bound inspect <run_id> --json                   # machine-readable
bound inspect <run_id> --only-unverified         # unverified/claimed/missing only
bound inspect <run_id> --html timeline.html      # self-contained HTML timeline
```

### `bound integration-spec`

Emit the framework-neutral BOUND integration specification as JSON.
Deterministic: no LLM, no network.

```bash
bound integration-spec
```

**Exit codes:** `0` success, `1` run not found / policy invalid, `2` validation error.

## Writing a Policy (YAML)

A `bound-policy.yaml` defines the gate for every agent step. The complete
documented default is at
[`src/bound/default_policy.yaml`](../src/bound/default_policy.yaml).

```yaml
schema_version: "1.0"
policy:
  id: coding-default
  version: "1.0"
collectors:
  pytest: { type: pytest }
  typecheck: { type: command, command: ["python", "-m", "mypy", "src"],
               timeout_seconds: 120, success_exit_codes: [0] }
  lint: { type: command, command: ["python", "-m", "ruff", "check", "."],
          timeout_seconds: 60 }
acceptance_checks:
  - { id: tests-pass, importance: blocker, required: true, collector: pytest,
      minimum_assurance: verified, accepted_provenance: [verified, observed],
      on_failure: retry, on_missing: retry, on_claimed: replan }
  - { id: typecheck-pass, importance: blocker, required: true, collector: typecheck,
      on_failure: retry, on_missing: retry, on_claimed: replan }
quality_checks:
  - { id: lint-clean, importance: medium, collector: lint }
risk_checks:
  - { id: no-secrets, importance: blocker, required: true,
      on_failure: rollback, on_missing: replan, on_claimed: rollback }
  - { id: scope-respected, importance: blocker, required: true,
      on_failure: replan, on_missing: replan }
budgets:
  attempts: { soft_limit: 2, hard_limit: 3, on_soft: retry, on_hard: replan }
  tool_calls: { soft_limit: 15, hard_limit: 20, on_soft: retry, on_hard: replan }
  tokens: { hard_limit: 200000, on_hard: replan }
  runtime: { soft_limit: 300, hard_limit: 600, on_soft: retry, on_hard: replan }
change_scope:
  allowed_paths: ["src/**", "tests/**"]
  forbidden_paths: [".git/**", "**/.env"]
  dependency_file_patterns: ["pyproject.toml", "uv.lock", "requirements*.txt"]
  unexpected_artifacts: { enabled: true, on_unexpected: replan, allowed_patterns: ["*.md"] }
approvals:
  commands_requiring_approval: ["rm", "git push --force"]
  destructive_actions: ["rm -rf", "git push --force"]
  require_rollback_availability: false
  on_missing_rollback: replan
```

| Section | Purpose |
| --- | --- |
| `collectors` | Built-in (`pytest`) or custom `command` collectors with command, timeout, and exit codes. |
| `acceptance_checks` | Hard gates — must pass. Each specifies `on_failure`, `on_missing`, `on_claimed`. |
| `quality_checks` | Soft, weighted signals — contribute to score but never override a blocker. |
| `risk_checks` | Safety boundaries — violations escalate to `rollback`. |
| `budgets` | Soft/hard limits per dimension (`attempts`, `tool_calls`, `tokens`, `runtime`). |
| `change_scope` | Allowed/forbidden paths, dependency files, artifact handling. |
| `approvals` | Commands requiring approval, destructive actions, rollback guardrails. |

Validate: `bound policy validate bound-policy.yaml`

## Managing Lineage via CLI

Lineage is **opt-in** and stored under `.bound/runs/<run_id>/` as append-only
`events.jsonl`.

```bash
bound run start "Add CSV export" --metadata phase=PHASE-001
bound evaluate --run <run_id> --step PHASE-001 --attempt 1 \
    --action "Implement CSV export" --goal "Let users download CSV" \
    --acceptance 0.3333 --influence 0.0 --risk 0.0 --cost 0.2 \
    --threshold 0.7 --retry-margin 0.1
bound outcome --run <run_id> --step PHASE-001 --attempt 1 \
    --decision REPLAN --note "need pagination first"
bound evaluate --run <run_id> --step PHASE-001 --attempt 2 \
    --acceptance 1.0 --influence 0.0 --risk 0.0 --cost 0.2 \
    --threshold 0.7 --retry-margin 0.1
bound outcome --run <run_id> --step PHASE-001 --attempt 2 \
    --decision ACCEPT --note "all checks passed"
bound run finish <run_id> --status completed
bound inspect <run_id>
bound inspect <run_id> --html timeline.html
```

**Storage layout:** `.bound/runs/<run_id>/run.json` (current state) +
`events.jsonl` (append-only log).

**Environment:** `BOUND_RUNS_DIR` overrides the storage location;
`BOUND_LINEAGE_DISABLED=1` disables persistence.

**Python API:**

```python
from bound import LineageStore, start_run, ReasonCode, RunFinishStatus
store = LineageStore(base_dir=".bound/runs")
with start_run("Add CSV export", store=store) as run:
    run.finish_run(status=RunFinishStatus.COMPLETED,
                   reason_code=ReasonCode.RUN_COMPLETED)
log = store.read_run(run.run_id)
print(log.run.status)
```

## Runnable Demos

Three self-contained demos ship with the repository (no network, no LLM).

```bash
git clone https://github.com/Danny-de-bree/bound.git
cd bound
uv sync
```

| Demo | What it shows | Run it |
| --- | --- | --- |
| `golden_demo.py` | Policy flow: generate, validate, REPLAN → ACCEPT with real collectors (pytest, typecheck, lint). | `uv run python examples/golden_demo.py` |
| `verified_evidence_demo.py` | Independent collection: agent *claims* tests pass, BOUND re-runs pytest via `PytestCollector` and inspects git via `GitCollector`. | `uv run python examples/verified_evidence_demo.py` |
| `lineage_demo.py` | End-to-end lineage: produces `.bound/runs/`, prints `bound inspect` tree and `events.jsonl`. | `uv run python examples/lineage_demo.py` |

All exit `0` on success and clean up temporary files.

## Privacy & Safety

- **No outgoing network.** The CLI and library make **zero network requests**.
  No telemetry, analytics, or external validation.
- **No LLM as judge.** All scoring is deterministic. Evidence is independently
  verified (`VERIFIED`), observed (`OBSERVED`), claimed (`CLAIMED`), or
  missing (`MISSING`).
- **No secret storage.** Lineage events are scrubbed by default. The
  `scrub_secrets` redactor masks `password`, `token`, `key` patterns before
  anything touches disk. Command collectors store hashes and summaries; raw
  output is opt-in (`store_raw=True`) and still redacted.
- **Disabling lineage.** `BOUND_LINEAGE_DISABLED=1` in the environment, or
  `bound.configure(enabled=False)` / `LineageStore(enabled=False)` in Python.
  When disabled, store methods return typed events but persist nothing.
- **No code modification.** BOUND never modifies the workspace, runs rollbacks,
  or executes actions. It emits a deterministic signal — the agent decides
  what to do next.
