# Canonical End-to-End Demo Scenario (S1-DEMO-1)

**Status:** Implemented (v0.8.0)
**Date:** 2026-07-20
**Author:** Sprint Lead
**Source:** [task_0018 / S1-DEMO-1]

## Objective

Demonstrate the complete v0.8.0 flow: a developer installs BOUND into a project,
an agent integrates BOUND, the agent implements a real task, BOUND evaluates each
attempt, and the developer inspects the verified decision lineage through the local
dashboard.

The demo must be **reproducible** (same steps produce the same outcome) and
**time-boxed** (under 5 minutes from clone to verified decision).

## Prerequisites

- Python 3.12+ installed on the machine
- Git installed
- A target project repository (open-source Python project with pytest + linter)
- A coding agent (default: Cline with Claude Sonnet 4)
- Internet access for PyPI and agent model

## Step-by-Step Plan

### Step 1: Clone the repository

```bash
git clone https://github.com/example/target-project.git
cd target-project
```

**Expected:** A clean Python project with pyproject.toml, pytest tests, a
linter config (ruff or pylint), and a README describing the project.

**Time:** ~30 seconds

**Check:** `python -m pytest tests/` passes before any changes.

---

### Step 2: Install BOUND via skills.sh

```bash
curl -fsSL https://raw.githubusercontent.com/Danny-de-bree/bound/main/skills/skills.sh | bash
```

Or install via pip:

```bash
pip install bound-policy
```

**Expected:** `bound --version` prints 0.8.0 (or later). The bound CLI is
available on PATH.

**Time:** ~30 seconds

**Check:** `bound policy validate --help` shows the policy subcommands.

---

### Step 3: Generate and approve a policy with `bound init`

```bash
bound init
```

The interactive prompt:

1. Scans the project structure (detects pytest, ruff, git).
2. Asks: "What verification commands should BOUND run?" (default: pytest,
   ruff check, mypy).
3. Asks: "What is the acceptance threshold?" (default: 0.7).
4. Writes `bound-policy.yaml` with:
   - Hard gates for critical checks (tests must pass)
   - Weighted signals for lint, type checks
   - Change scope: src/ and tests/
   - Budget defaults (5 attempts, 100 tool calls, no token limit)

**Expected:** `bound-policy.yaml` is created and valid.

```bash
bound policy validate bound-policy.yaml
# Output: Policy is valid.
# Policy identity: id=<id>, version=1.0, hash=sha256:<hex>
```

**Time:** ~1 minute

**Check:** `bound policy explain bound-policy.yaml` prints a readable summary.

---

### Step 4: Agent integrates BOUND

The developer pastes the **generic integration prompt** (INSTALL_BOUND.md) into
the agent. The agent:

1. Reads bound-policy.yaml to understand the policy.
2. Reads `bound integration-spec` output for the machine-readable contract.
3. Creates a StepContract for the task: "Add input validation to the registration
   endpoint."
4. Configures collectors: PytestCollector, CommandCollector for ruff, git.
5. Starts a BOUND run: `bound run start --task "Add input validation"`.

**Expected:** A run is created in .bound/runs/. `bound run list` shows it.

**Time:** ~1 minute

**Check:** `bound run list` shows one run with status `started`.

---

### Step 5: Attempt 1 -- Agent implements, BOUND evaluates REPLAN

The agent writes code for input validation, runs tests, and collects evidence:

```bash
bound evaluate \
  --action "Add input validation to registration endpoint" \
  --goal "Ensure all inputs are validated before processing" \
  --threshold 0.7 \
  --acceptance-score 0.33 \
  --risk-score 0.15 \
  --cost-score 0.05 \
  --run-id <run_id> \
  --step "PHASE-001"
```

**Expected BOUND decision:** REPLAN (score S=0.28 is far below threshold T=0.7).

The agent receives the decision and replans the strategy.

**Check:** `bound inspect <run_id>` shows:
- Step 1: REPLAN with score breakdown (A=0.33, I=0.00, R=0.15, C=0.05, S=0.28)
- Reason code: BELOW_THRESHOLD
- Outcome: agent reported "switched strategy to parametrized tests"

**Time:** ~2 minutes

---

### Step 6: Attempt 2 -- Agent fixes, BOUND verifies and ACCEPTs

The agent changes strategy: implements parametrized tests, fixes the lint warnings,
and now runs BOUND collectors for independent verification:

```bash
bound evaluate-workflow \
  --test-pass-rate 1.0 \
  --lint-passed true \
  --type-check-passed true \
  --required-checks-passed 1.0 \
  --threshold 0.7 \
  --run-id <run_id> \
  --step "PHASE-001-R1" \
  --attempt 2
```

**Expected BOUND decision:** ACCEPT (score S=1.0 >= threshold T=0.7).

The agent receives ACCEPT and continues to the next step.

**Check:** `bound inspect <run_id>` shows:
- Step 2: ACCEPT with score breakdown (A=1.0, I=0.0, R=0.0, C=0.0, S=1.0)
- Assurance: VERIFIED (collectors ran independently)
- Decision gate: candidate ACCEPT -> final ACCEPT (assurance: FULL)
- Outcome: agent continued

**Time:** ~2 minutes

---

### Step 7: Finish the run

```bash
bound run finish <run_id> --status completed
```

**Expected:** Run status is now `completed`.

**Check:** `bound run list` shows the run with status `completed`.

---

### Step 8: Open in `bound ui`

```bash
bound ui --open
```

The dashboard opens in the default browser at http://127.0.0.1:8765.

**Expected on the overview page:**
- The completed run is listed with status badge "completed".
- Task name, start time, step count, and event count are visible.

**Expected on the run detail page (click into the run):**
- Decision tree showing:
  - Attempt 1 (REPLAN) -- red badge, score 0.28
  - Attempt 2 (ACCEPT) -- green badge, score 1.0
- Evidence provenance badges (VERIFIED, OBSERVED, CLAIMED).
- Score breakdown per dimension.
- Assurance level for the final decision.

**Time:** ~30 seconds

---

### Step 9 (optional): Record the demo

```bash
# Using asciinema or similar
asciinema rec bound-demo.cast
# ... run through steps 1-8 ...
exit
```

**Expected:** A replayable terminal recording.

## Success Criteria

| # | Criterion | Verification |
|---|-----------|--------------|
| 1 | bound init produces a valid policy | bound policy validate passes |
| 2 | Agent integrates without manual wiring | Agent reads INSTALL_BOUND.md and follows it |
| 3 | First attempt produces REPLAN | bound inspect shows REPLAN decision |
| 4 | Evidence provenance is CLAIMED + OBSERVED | Inspection shows provenance badges |
| 5 | Second attempt produces ACCEPT | bound inspect shows ACCEPT decision |
| 6 | ACCEPT is VERIFIED (independent collectors) | Assurance level is FULL or HIGH |
| 7 | bound ui shows both attempts | Decision tree has two nodes |
| 8 | Total time is under 5 minutes | Timer from git clone to bound ui |
| 9 | Demo is reproducible | Same project + same agent = same result |

## Variations

### MCP variant (if available in v0.8.0)

Replace steps 4-6 with MCP tool calls instead of CLI bound evaluate commands.
The agent uses MCP tools: bound_evaluate, bound_start_run, bound_finish_run.

### Watch variant (if available in v0.8.0)

Replace step 5 with `bound watch` automatically detecting file changes and
triggering evaluation. The agent only writes code; BOUND watches and evaluates.

## Files Referenced

- integrations/generic/INSTALL_BOUND.md -- the integration prompt
- bound_integration/INTEGRATION_REPORT.md -- the post-run execution record
- docs/architecture.md -- architecture documentation
- docs/status-and-roadmap.md -- status and roadmap
- bound-policy.yaml -- generated policy file
- .bound/runs/ -- lineage store
- CHANGELOG.md -- changelog
