# BOUND Integration Report

## Run summary

- BOUND version: `0.7.0 (distribution `0.7.0`)`
- Plan: `PHASE-001`
- Step / contract id: `PHASE-001`
- Final outcome: `ACCEPT` → `continue`
- Score / threshold: `S = 1.0000` ≥ `T = 0.7000` (distance `+0.3000`)
- Run id: `9146e8437fb544919a872473ed08391f`
- Timestamp: `2026-07-19T20:02:20.850494+00:00`
- Critical evidence coverage: `100% independently verified` (1/1 decision-critical checks)

## PHASE-001 — BOUND v0.7.0 verified-evidence release boundary (self-integration)

### Planned goal

BOUND v0.7.0 is green and independently verified: the full pytest suite passes, ruff is clean, and the Definition-of-Done demo runs — all confirmed by BOUND's own collectors executing the real commands, with changes scoped to the v0.7.0 release surface.

### Actual execution

| Command | Result (observed) |
| --- | --- |
| `/home/danny/projects/bound/.venv/bin/python3 -m pytest -q` | exit `0` — 753 passed in 4.62s |
| `/home/danny/projects/bound/.venv/bin/python3 -m ruff check .` | exit `0` — exit `0` |
| `/home/danny/projects/bound/.venv/bin/python3 /home/danny/projects/bound/examples/verified_evidence_demo.py` | exit `0` — exit `0` |
| `git status --porcelain` | exit `0` — exit `0` |

### Observed acceptance evidence

| Check id | Passed | Provenance | Collector | Source | Details |
| --- | :---: | :---: | --- | --- | --- |
| `tests-pass` | yes | VERIFIED | `bound.pytest` | `/home/danny/projects/bound/.venv/bin/python3 -m pytest -q` | pytest exit=0; 753 passed, 0 failed, 0 errors, 0 skipped, 753 executed; stdout=sha256:1dfc885c2027ed0f0920aa2e0b5ad11af79e9881f0546961ed4650520f9965eb stderr=sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 |
| `lint-pass` | yes | VERIFIED | `bound.process` | `/home/danny/projects/bound/.venv/bin/python3 -m ruff check .` | process exit=0; runtime=0.065s; stdout=sha256:82b3e6a6c090a57601d22943bd23fca9218d1031dbe5a7b754092f9a156b4f18 |
| `dod-demo-passes` | yes | VERIFIED | `bound.process` | `/home/danny/projects/bound/.venv/bin/python3 /home/danny/projects/bound/examples/verified_evidence_demo.py` | process exit=0; runtime=1.098s; stdout=sha256:5a3f4d86dcc147a0f8b775bacb8f323aebd04635a41ab8a073a2e9f2fdef89ef |

### Observed risk evidence

| Check id | Passed | Provenance | Collector | Source | Details |
| --- | :---: | :---: | --- | --- | --- |
| `no-unsafe-changes` | yes | VERIFIED | `bound.git` | `git status --porcelain` | git status exit=0; changed=['.gitignore', 'CHANGELOG.md', 'README.md', 'docs/lineage.md', 'docs/upgrade-guide.md', 'examples/lineage_demo.py', 'examples/lineage_demo_events.jsonl', 'integrations/claude-code/INSTALL_BOUND.md', 'integrations/cline/INSTALL_BOUND.md', 'integrations/codex/INSTALL_BOUND.md', 'integrations/generic/INSTALL_BOUND.md', 'integrations/hermes-agent/INSTALL_BOUND.md', 'integrations/kilo-code/INSTALL_BOUND.md', 'pyproject.toml', 'skills/bound/SKILL.md', 'src/bound/__init__.py', 'src/bound/bound_workflow.py', 'src/bound/cli.py', 'src/bound/contract_evaluator.py', 'src/bound/contracts.py', 'src/bound/evidence.py', 'src/bound/integration.py', 'src/bound/lineage.py', 'src/bound/lineage_api.py', 'src/bound/lineage_store.py', 'src/bound/models.py', 'src/bound/policy.py', 'src/bound/report.py', 'tests/test_architecture.py', 'tests/test_cli_lineage.py', 'tests/test_collectors.py', 'tests/test_contract_evaluator.py', 'tests/test_contracts.py', 'tests/test_evidence.py', 'tests/test_lineage.py', 'tests/test_lineage_api.py', 'tests/test_lineage_e2e.py', 'tests/test_lineage_store.py', 'tests/test_models.py', 'tests/test_policy.py', 'tests/test_report.py', 'tests/test_v06_dod.py', 'uv.lock', '.github/workflows/release.yml', 'examples/verified_evidence_demo.py', 'scripts/self_integrate_bound.py', 'src/bound/command_collector.py', 'tests/test_v07_verified_evidence.py'], unexpected=[]; stdout=sha256:7a5cdbeca9cb9f798ea0ac8d103b7cf70e0f44f3ad42f26054bd237c71a75b5c stderr=sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 |

### Evidence provenance

Per-dimension trust provenance (strongest backing evidence). Agent self-report is always CLAIMED; only an independent collector grants OBSERVED/VERIFIED/ATTESTED.

- Acceptance (A): `VERIFIED`
  - `tests-pass` · VERIFIED · bound.pytest · /home/danny/projects/bound/.venv/bin/python3 -m pytest -q
  - `lint-pass` · VERIFIED · bound.process · /home/danny/projects/bound/.venv/bin/python3 -m ruff check .
  - `dod-demo-passes` · VERIFIED · bound.process · /home/danny/projects/bound/.venv/bin/python3 /home/danny/projects/bound/examples/verified_evidence_demo.py
- Influence (I): `DEFAULTED`
  - `default` · DEFAULTED v0.3 sets influence=0.0 by default: no downstream-influence evidence is derivable from contract evidence. Honesty over invented sophistication. (policy neutral value; no evidence source)
- Risk (R): `VERIFIED`
  - `no-unsafe-changes` · VERIFIED · bound.git · git status --porcelain
- Cost (C): `MISSING`
  - `retry_count` · MISSING
  - `tool_call_count` · MISSING
  - `token_usage` · MISSING
  - `runtime_seconds` · MISSING

### Unavailable evidence

Signals not instrumented by this integration are recorded as null and never fabricated:

- token_usage: unavailable (null)
- runtime_seconds: unavailable (null)
- tool_call_count: unavailable (null)
- model_metadata: unavailable (null)

### BOUND evaluation

- Acceptance (A): `1.0000`
- Influence (I): `0.0000`
- Risk (R): `0.0000`
- Cost (C): `0.0000`
- Score (S): `1.0000`
- Threshold (T): `0.7000`
- Decision: `ACCEPT`
- Next action: `continue`
- Candidate decision: `ACCEPT`
- Final decision: `ACCEPT`
- Decision assurance: `VERIFIED`

BOUND feedback (verbatim):

> Decision: ACCEPT. The step meets the acceptance threshold (S=1.0000 >= T=0.7000) and stays within the risk boundary. It is sufficiently complete. Continue to the next objective. Do not keep optimizing this step; further refinement is unnecessary and wastes effort.

### Decision assurance

Assurance reasons:

- check 'tests-pass' backed by verified-tier evidence (provenance: verified)
- check 'lint-pass' backed by verified-tier evidence (provenance: verified)
- check 'dod-demo-passes' backed by verified-tier evidence (provenance: verified)
- check 'no-unsafe-changes' (decision-critical) backed by verified-tier evidence (provenance: verified)

_No missing decision-critical evidence._

ROLLBACK and other control actions are executed by the agent / integration, not by BOUND. BOUND is a thin harness: it emits the decision and may independently verify the resulting state; it never performs a workspace rollback itself.

### Decision history

| Step id | Attempt | Decision | Next action | Note |
| --- | :---: | :---: | :---: | --- |
| `PHASE-001` | 1 | `ACCEPT` | `continue` | BOUND self-integration of the v0.7.0 release boundary |

0 replan(s), 0 retry/retries recorded — history preserved, never rewritten.

### Plan deviation

None. The step was evaluated with no replan or retry; the contract id `PHASE-001` is preserved unchanged from the plan.

### Produced artifacts

_(none observed)_

### Unexpected artifacts

_(none observed)_

### Final verification

The verification commands recorded for this run:

```bash
$ /home/danny/projects/bound/.venv/bin/python3 -m pytest -q
$ /home/danny/projects/bound/.venv/bin/python3 -m ruff check .
$ /home/danny/projects/bound/.venv/bin/python3 /home/danny/projects/bound/examples/verified_evidence_demo.py
$ git status --porcelain
```

Re-running the trace produces a fresh `run_id` / `timestamp` (a new run) while the deterministic evaluation outcome is stable.
