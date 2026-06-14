# Testing

## Prompt - Add Missing Unit Tests
```text
You are a coding agent.
Task: Add unit tests for this module.
Target:
{{FILE_OR_MODULE}}
Deliver:
1. Happy path tests.
2. Edge case tests.
3. Failure mode tests.
Acceptance:
- Tests are deterministic.
- No over-mocking.
- Clear names and intent.
Stop when coverage is sufficient for critical paths.
```

## Prompt - Flaky Test Stabilization
```text
You are a coding agent.
Task: Stabilize flaky tests without reducing real coverage.
Deliver:
1. Root cause of flakiness.
2. Stable test changes.
3. Why reliability improved.
Acceptance:
- Test passes repeatedly.
- No disabled assertions hiding failures.
Stop when flake is resolved.
```

## Prompt - Contract Test Creation
```text
You are a coding agent.
Task: Create contract tests for API boundaries.
Boundary:
{{API_OR_SERVICE}}
Deliver:
1. Producer expectations.
2. Consumer expectations.
3. Contract test suite.
Acceptance:
- Contracts reflect current production behavior.
- Breaking changes are explicitly caught.
Stop after contract suite runs green.
```
