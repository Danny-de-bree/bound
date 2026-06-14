# Incident and Operations

## Prompt - Incident Triage
```text
You are a coding agent.
Task: Triage this production incident.
Incident:
{{INCIDENT}}
Deliver:
1. Impact assessment.
2. Likely causes.
3. Immediate mitigation.
4. Next verification steps.
Acceptance:
- User impact reduced quickly.
- Actions are safe and reversible.
Stop once service is stabilized.
```

## Prompt - Postmortem Draft
```text
You are a coding agent.
Task: Draft a postmortem from incident data.
Deliver:
1. Timeline.
2. Root cause.
3. Contributing factors.
4. Action items with owners.
Acceptance:
- Blameless and specific.
- Actions are testable and prioritized.
Stop when draft is ready for team review.
```

## Prompt - Alert Noise Reduction
```text
You are a coding agent.
Task: Reduce noisy alerts while preserving true positives.
Deliver:
1. Noisy alert candidates.
2. Threshold or rule changes.
3. Validation plan.
Acceptance:
- Lower alert fatigue.
- No blind spot for critical incidents.
Stop after first meaningful noise reduction.
```
