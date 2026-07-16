"""Framework-neutral integration specification (BOUND v0.4 Phase 3).

A single, machine-readable description of how an agent should consume BOUND,
expressed purely in framework-neutral terms so any agent — Cline, Claude Code,
Codex, Cursor, or a custom loop — can read the same spec and wire itself in.

The spec is produced deterministically by :func:`integration_spec` (a pure
function: no network, no LLM) and exposed via the ``bound integration-spec`` CLI
subcommand as structured JSON. It defines exactly the four sections mandated by
Phase 3 of the v0.4 TODO:

* WHEN TO CALL BOUND
* WHEN NOT TO CALL BOUND
* THE REQUIRED FLOW
* EVIDENCE RULE

It also carries the deterministic decision -> control-action mapping so a
consumer can wire its control flow straight from the spec.
"""

from __future__ import annotations

from typing import Any

#: The deterministic BOUND decision -> agent control action mapping.
_DECISION_TO_CONTROL: dict[str, str] = {
    "ACCEPT": "continue",
    "RETRY": "retry",
    "REPLAN": "replan",
    "ROLLBACK": "rollback",
}


def integration_spec() -> dict[str, Any]:
    """Build the framework-neutral BOUND integration specification.

    The function is pure and deterministic: the same call always returns the
    same structure, with no network access and no LLM. The structure is
    JSON-serialisable so the CLI can emit it verbatim.

    Returns:
        A dict with the version, the four mandated sections (``when_to_call``,
        ``when_not_to_call``, ``required_flow``, ``evidence_rule``), the
        deterministic ``decision_to_control`` mapping, and a short set of
        ``invariants`` an integration must uphold.
    """
    return {
        "spec_version": 1,
        "tool": "bound-policy",
        "summary": (
            "BOUND evaluates one executed plan step against an explicit "
            "StepContract using collected ExecutionEvidence and returns a "
            "deterministic control decision. BOUND decides whether to continue, "
            "retry, replan, or rollback. BOUND does not decide what code to "
            "write."
        ),
        "when_to_call": [
            "after a meaningful plan step",
            "after implementation plus verification",
            "after a retry",
            "before deciding to continue refining the same objective",
        ],
        "when_not_to_call": [
            "after every token",
            "after every file read",
            "after every shell command",
            "after every low-level tool call",
        ],
        "required_flow": [
            {
                "step": 1,
                "name": "define_contract",
                "action": "Create a StepContract describing success and risk for the step.",
            },
            {
                "step": 2,
                "name": "execute",
                "action": "The agent executes the step.",
            },
            {
                "step": 3,
                "name": "collect_evidence",
                "action": "Collect observable ExecutionEvidence from the execution.",
            },
            {
                "step": 4,
                "name": "evaluate",
                "action": "Evaluate the step with BOUND (ContractEvaluator -> BoundPolicy).",
            },
            {
                "step": 5,
                "name": "apply_control_decision",
                "action": (
                    "Apply the returned control decision; the agent owns the "
                    "control flow."
                ),
            },
        ],
        "evidence_rule": {
            "principle": "Never fabricate unavailable evidence.",
            "if_evidence_is_unavailable": [
                "represent it as unavailable",
                "allow the configured deterministic policy to handle it",
                "never convert assumptions into successful checks",
            ],
        },
        "decision_to_control": _DECISION_TO_CONTROL,
        "invariants": [
            "BOUND decides whether to continue, retry, replan, or rollback.",
            "BOUND does not decide what code to write.",
            "The evaluation is deterministic: no LLM-as-judge, no network in the core.",
            "Never duplicate BOUND's policy logic in the agent.",
            "Keep the integration thin and removable.",
        ],
    }
