# BOUND Integration Report

## Run summary

- BOUND version: `0.6.0 (distribution `0.6.0`)`
- Plan: `PHASE-001`
- Step / contract id: `PHASE-001`
- Final outcome: `ACCEPT` → `continue`
- Score / threshold: `S = 1.0000` ≥ `T = 0.7500` (distance `+0.2500`)
- Run id: `223a89e9c33c4f4bb6c991f9bfb7cd6f`
- Timestamp: `2026-07-17T14:41:25.738700+00:00`

## PHASE-001 — Ship BOUND v0.6 reporting, a real demo trace, and README evidence links — verified by BOUND's own test suite.

### Planned goal

BOUND v0.6 is verified by a green full `uv run pytest -q` suite and a green service-specific `uv run pytest tests/test_calculator.py -q` run that executed >=1 test, with changes scoped to the v0.6 effort.

### Actual execution

| Command | Result (observed) |
| --- | --- |
| `uv run pytest -q` | exit `0` — 513 passed in 0.77s |
| `uv run pytest tests/test_calculator.py -q` | exit `0` — 34 passed in 0.10s |
| `git status --porcelain` | exit `0` — exit `0` |

### Observed acceptance evidence

| Check id | Source | Passed | Details |
| --- | --- | :---: | --- |
| `tests-pass` | `uv run pytest -q` | yes | exit_code=0; executed=513 |
| `service-tests-pass` | `uv run pytest tests/test_calculator.py -q` | yes | exit_code=0; executed=34 |

### Observed risk evidence

| Check id | Source | Passed | Details |
| --- | --- | :---: | --- |
| `no-unexpected-files` | `git status --porcelain` | yes | no unexpected paths |

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
- Threshold (T): `0.7500`
- Decision: `ACCEPT`
- Next action: `continue`

BOUND feedback (verbatim):

> Decision: ACCEPT. The step meets the acceptance threshold (S=1.0000 >= T=0.7500) and stays within the risk boundary. It is sufficiently complete. Continue to the next objective. Do not keep optimizing this step; further refinement is unnecessary and wastes effort.

### Decision history

| Step id | Attempt | Decision | Next action | Note |
| --- | :---: | :---: | :---: | --- |
| `PHASE-001` | 1 | `ACCEPT` | `continue` | first evaluation; no replan or retry |

0 replan(s), 0 retry/retries recorded — history preserved, never rewritten.

### Plan deviation

None. The step was evaluated with no replan or retry; the contract id `PHASE-001` is preserved unchanged from the plan.

### Produced artifacts

- `bound_integration/`
- `examples/reference_integration/`
- `src/bound/report.py`

### Unexpected artifacts

_(none observed)_

### Final verification

The verification commands recorded for this run:

```bash
$ uv run pytest -q
$ uv run pytest tests/test_calculator.py -q
$ git status --porcelain
```

Re-running the trace produces a fresh `run_id` / `timestamp` (a new run) while the deterministic evaluation outcome is stable.

