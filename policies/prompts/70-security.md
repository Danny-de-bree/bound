# Security

## Prompt - Security Hotspot Review
```text
You are a coding agent.
Task: Review this code for security hotspots.
Target:
{{TARGET}}
Deliver:
1. Vulnerabilities ranked by severity.
2. Exploit scenario per high issue.
3. Minimal mitigation patch.
Acceptance:
- High severity issues addressed first.
- No fear-based or speculative claims.
Stop after high-risk items are mitigated.
```

## Prompt - Secret Leakage Prevention
```text
You are a coding agent.
Task: Prevent secrets from being committed or logged.
Deliver:
1. Secret exposure points.
2. Code/config fixes.
3. Guardrails (hooks/scanners/policies).
Acceptance:
- Sensitive values removed from code paths.
- Practical guardrails in place.
Stop after leak risk is reduced to acceptable.
```

## Prompt - AuthZ Gap Fix
```text
You are a coding agent.
Task: Find and fix authorization gaps.
Area:
{{ENDPOINTS_OR_ACTIONS}}
Deliver:
1. Missing authz checks.
2. Code patch.
3. Tests proving denied/allowed behavior.
Acceptance:
- Unauthorized paths are blocked.
- Legitimate access still works.
Stop when authz matrix passes.
```
