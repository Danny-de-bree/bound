# Planning and Prioritization

## Prompt - Technical Debt Prioritization
```text
You are a coding agent.
Task: Prioritize technical debt in this repository.
Deliver:
1. Debt items with impact and effort.
2. Recommended top 5 actions.
3. Why now.
Acceptance:
- Prioritization is outcome-driven.
- No vanity refactors.
Stop when next actions are clear.
```

## Prompt - Sprint-Ready Task Breakdown
```text
You are a coding agent.
Task: Break this initiative into sprint-ready tasks.
Initiative:
{{INITIATIVE}}
Deliver:
1. Tasks with clear definition of done.
2. Dependencies.
3. Risks per task.
Acceptance:
- Tasks are executable without ambiguity.
- Scope is realistic.
Stop when backlog is ready.
```

## Prompt - Build vs Buy Analysis
```text
You are a coding agent.
Task: Evaluate build vs buy for this capability.
Capability:
{{CAPABILITY}}
Deliver:
1. Options compared.
2. Cost, risk, and time-to-value.
3. Recommendation.
Acceptance:
- Recommendation is practical and constrained.
- Assumptions are explicit.
Stop once a decision can be made.
```
