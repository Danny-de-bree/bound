# Feature Delivery

## Prompt - Small Feature Implementation
```text
You are a coding agent.
Task: Implement the requested feature.
Feature:
{{FEATURE_REQUEST}}
Deliver:
1. Implementation.
2. Tests.
3. Docs update (if needed).
Acceptance:
- Feature works end-to-end.
- Existing behavior unaffected.
Stop when acceptance criteria are met.
```

## Prompt - MVP First Slice
```text
You are a coding agent.
Task: Deliver an MVP slice for this feature.
Feature:
{{FEATURE}}
Constraints:
{{CONSTRAINTS}}
Deliver:
1. Smallest valuable version.
2. Known limitations.
3. Next optional increments.
Acceptance:
- Usable now.
- No speculative extras.
Stop after MVP is shippable.
```

## Prompt - Feature Flag Rollout
```text
You are a coding agent.
Task: Add a feature behind a flag.
Deliver:
1. Flag definition.
2. Guarded code path.
3. Safe default and rollback strategy.
Acceptance:
- Disabled by default unless requested.
- Rollback is one-step.
Stop when rollout risk is controlled.
```
