# BOUND integration report

Use this structure for every evaluated phase. Preserve prior evaluations when
rerunning a phase.

```text
Phase:
    Stable id:
    Goal:
    Planned outcome:
    Actual outcome:

Execution configuration:
    Agent:
    Model:
    Model settings:
    bound-policy version:
    Retry budget:
    Tool-call budget:
    Token budget:
    Runtime budget:

Verification and evidence:
    Commands and exit codes:
    Acceptance evidence:
    Risk evidence:
    Produced artifacts:
    Unexpected artifacts:
    Rollback available:
    Missing/unobservable evidence:

Observed consumption:
    Retries:
    Tool calls:
    Tokens used:
    Runtime:

BOUND evaluation:
    Acceptance (A):
    Influence (I):
    Risk (R):
    Cost (C):
    Score (S):
    Threshold (T):
    Weights:
    Weighted components:
    Retry margin:
    Rollback-risk threshold:
    Provenance:
    Decision:
    Next action:

Consistency check:
    Previous integration record:
    Inputs sufficient to reproduce:
    Re-evaluation performed:
    Recorded values match:
    Stale values:
    Missing fields:

Workflow result:
    Retries and replans:
    Plan deviations:
    Resulting action:
    Final verification:
```

Use `unavailable` for telemetry the environment does not expose. Use `None`
only for a deliberately unset budget and `0` only for an observed zero.

Agent, model, and model settings are benchmark metadata, not BOUND policy
inputs. All A/I/R/C/S/T values, decisions, actions, weights, components, and
provenance must come from the returned BOUND result rather than manual scoring.
