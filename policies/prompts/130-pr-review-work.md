# PR and Review Work

## Prompt - Address PR Review Comments
```text
You are a coding agent.
Task: Address open review comments in the active PR.
Deliver:
1. Implement requested fixes.
2. Explain deviations where not applied.
3. Keep diffs minimal.
Acceptance:
- Each comment is resolved or answered with rationale.
- No unrelated edits.
Stop when review threads are actionable.
```

## Prompt - PR Risk Review
```text
You are a coding agent.
Task: Review this PR for risk.
Deliver:
1. Findings ordered by severity.
2. Missing tests.
3. Merge risk summary.
Acceptance:
- Findings are specific with file evidence.
- Focus on bugs/regressions first.
Stop when reviewer can decide quickly.
```

## Prompt - PR Description Generator
```text
You are a coding agent.
Task: Generate a high-signal PR description.
Deliver:
1. What changed.
2. Why.
3. How validated.
4. Risks and rollback.
Acceptance:
- Description is concise and review-ready.
- No generic filler.
Stop when PR body is publishable.
```
