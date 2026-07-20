from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict

from bound.bound_workflow import BoundWorkflow
from bound.contracts import StepContract
from bound.evidence import ExecutionEvidence
from bound.models import BoundCriteria, EvaluationResult

if TYPE_CHECKING:
    from bound.lineage_api import RunContext

logger = logging.getLogger("bound.integration")

NextAction = Literal["continue", "retry", "replan", "rollback"]

#: The deterministic BOUND decision -> agent control action mapping. This is
#: the **single runtime source** of that translation: :func:`evaluate_agent_step`
#: looks decisions up here and nowhere else. A *data-only* copy of the same
#: mapping is also published via :func:`bound.integration_spec.integration_spec`
#: (``decision_to_control``) so integrations can wire their control flow from
#: the published spec, but that copy must never be consulted to *make* a runtime
#: decision independently of BOUND — the decision is owned by the deterministic
#: :class:`~bound.policy.BoundPolicy` and only *translated* here.
_DECISION_TO_ACTION: dict[str, NextAction] = {
    "ACCEPT": "continue",
    "RETRY": "retry",
    "REPLAN": "replan",
    "ROLLBACK": "rollback",
}


class AgentControlResult(BaseModel):
    """A BOUND decision translated into a framework-neutral agent instruction.

    Wraps the deterministic :class:`~bound.models.EvaluationResult` together with
    the mapped control action and concise, deterministic feedback. An agent
    consumer reads ``next_action`` to choose its control flow and may feed
    ``feedback`` straight back into its own context.

    Attributes:
        evaluation: The deterministic :class:`~bound.models.EvaluationResult`
            produced by BOUND. It carries the scores, components, final score,
            threshold metadata, and the original decision.
        next_action: The agent control action mapped from
            :attr:`~bound.models.EvaluationResult.decision` — one of
            ``continue``, ``retry``, ``replan``, ``rollback``.
        feedback: Deterministic, concise feedback (under 150 words) derived
            only from the evaluation, contract, evidence, and provenance.
    """

    model_config = ConfigDict(extra="forbid")

    evaluation: EvaluationResult
    next_action: NextAction
    feedback: str


def _passed_check_ids(evidence: ExecutionEvidence) -> set[str]:
    """Return the ids of checks the evidence records as passed.

    Args:
        evidence: The execution evidence observed after the step ran.

    Returns:
        The set of ``check_id`` values whose :class:`~bound.evidence.CheckEvidence`
        records ``passed=True``.
    """
    return {ev.check_id for ev in evidence.acceptance if ev.passed}


def _failed_required_checks(contract: StepContract, evidence: ExecutionEvidence) -> list[str]:
    """List required acceptance checks with no passing evidence (failed or missing).

    Mirrors the :class:`~bound.contract_evaluator.ContractEvaluator` rule: a
    *required* acceptance check with no matching passing evidence is treated as
    failed — missing evidence is never silently assumed to pass.

    Args:
        contract: The step contract declaring the required checks.
        evidence: The execution evidence observed after the step ran.

    Returns:
        The ids of required acceptance checks lacking passing evidence,
        preserving declaration order.
    """
    passed = _passed_check_ids(evidence)
    return [
        check.id
        for check in contract.acceptance_checks
        if check.required and check.id not in passed
    ]


def _violated_risk_checks(contract: StepContract, evidence: ExecutionEvidence) -> list[str]:
    """List declared risk checks treated as violated (failed or unevidenced).

    Mirrors the :class:`~bound.contract_evaluator.ContractEvaluator` rule: a
    risk check with no matching evidence is treated conservatively as
    violated, as is one whose evidence records ``passed=False``. A risk check is
    therefore violated exactly when it has no passing evidence.

    Args:
        contract: The step contract declaring the risk checks.
        evidence: The execution evidence observed after the step ran.

    Returns:
        The ids of violated declared risk checks, preserving declaration order.
    """
    passed = {ev.check_id for ev in evidence.risks if ev.passed}
    return [
        check.id for check in contract.risk_checks if check.id not in passed
    ]


def render_feedback(
    evaluation: EvaluationResult,
    *,
    contract: StepContract,
    evidence: ExecutionEvidence,
) -> str:
    """Render deterministic, LLM-free agent feedback from a BOUND result.

    The feedback is derived exclusively from the
    :class:`~bound.models.EvaluationResult`, the
    :class:`~bound.contracts.StepContract`, the
    :class:`~bound.evidence.ExecutionEvidence`, and the per-dimension
    ``provenance`` — never from an LLM. It follows the Phase 2 per-decision
    behaviour and stays under 150 words so it can be re-injected into an agent
    context.

    Args:
        evaluation: The deterministic BOUND :class:`EvaluationResult`.
        contract: The :class:`StepContract` that scoped the evaluation.
        evidence: The :class:`ExecutionEvidence` observed after the step ran.

    Returns:
        A deterministic, concise feedback string for the agent.
    """
    decision = evaluation.decision
    score = evaluation.score
    threshold = evaluation.threshold
    gap = threshold - score

    if decision == "ACCEPT":
        return (
            f"Decision: ACCEPT. The step meets the acceptance threshold "
            f"(S={score:.4f} >= T={threshold:.4f}) and stays within the risk "
            f"boundary. It is sufficiently complete. Continue to the next "
            f"objective. Do not keep optimizing this step; further refinement "
            f"is unnecessary and wastes effort."
        )

    if decision == "RETRY":
        failed = _failed_required_checks(contract, evidence)
        lines = [
            f"Decision: RETRY. The step is close to acceptable "
            f"(S={score:.4f}, T={threshold:.4f}, gap={gap:.4f})."
        ]
        if failed:
            lines.append(
                f"Remaining failed/missing required check(s): {', '.join(failed)}."
            )
        lines.append("Stay with the current approach and make one focused correction.")
        return " ".join(lines)

    if decision == "REPLAN":
        return (
            f"Decision: REPLAN. The step is too far below the threshold "
            f"(S={score:.4f}, T={threshold:.4f}, gap={gap:.4f}) to fix by "
            f"retrying. Choose a materially different approach that better "
            f"addresses the goal."
        )

    # ROLLBACK
    violated = _violated_risk_checks(contract, evidence)
    lines = [
        f"Decision: ROLLBACK. The risk boundary is exceeded "
        f"(R={evaluation.scores.risk:.4f} >= "
        f"rollback threshold={evaluation.rollback_risk_threshold:.4f})."
    ]
    if violated:
        lines.append(f"Violated risk check(s): {', '.join(violated)}.")
    lines.append("Return to a safe state before continuing.")
    return " ".join(lines)


def evaluate_agent_step(
    contract: StepContract,
    evidence: ExecutionEvidence,
    criteria: BoundCriteria,
    *,
    workflow: BoundWorkflow | None = None,
    run: RunContext | None = None,
    attempt: int = 1,
    step_id: str | None = None,
    description: str | None = None,
) -> AgentControlResult:
    """Evaluate an executed step and translate the BOUND decision into a control action.

    Runs BOUND's deterministic contract pipeline
    (``StepContract + ExecutionEvidence + BoundCriteria -> EvaluationResult``)
    and maps the resulting decision to a framework-neutral control action
    (``continue`` / ``retry`` / ``replan`` / ``rollback``) plus concise
    deterministic feedback. The mapping is exact and reproducible.

    This helper does not invent scores, modify the BOUND decision, call an LLM,
    know about any agent framework, or execute a rollback or retry — it only
    *translates* the decision into an instruction the owning agent acts on.

    When a ``run`` context is supplied it is forwarded to
    :meth:`BoundWorkflow.evaluate_step`, which auto-records the step's lineage
    (``step_started`` + ``evaluation_recorded`` + ``outcome_recorded``) when
    lineage is enabled. The return type and value are unchanged when no ``run``
    is supplied (backwards compatible).

    Args:
        contract: The :class:`StepContract` for the executed step.
        evidence: The :class:`ExecutionEvidence` observed after the step ran.
        criteria: The :class:`BoundCriteria` (threshold, weights, retry margin,
            rollback risk boundary) the policy evaluates against.
        workflow: Optional pre-built :class:`BoundWorkflow` (e.g. one sharing a
            specific :class:`~bound.contract_evaluator.ContractEvaluator`).
            Defaults to a fresh placeholder-free ``BoundWorkflow()``.
        run: Optional :class:`~bound.lineage_api.RunContext`; when supplied (and
            enabled) the step's lineage is recorded automatically by the
            workflow. Defaults to the workflow evaluator's configured
            ``lineage_run`` when omitted.
        attempt: One-based attempt number recorded in lineage (default 1).
        step_id: Optional explicit step id for lineage; otherwise derived.
        description: Optional step description for lineage; defaults to the
            contract's description.

    Returns:
        An :class:`AgentControlResult` carrying the deterministic evaluation,
        the mapped ``next_action``, and deterministic ``feedback``.
    """
    wf = workflow if workflow is not None else BoundWorkflow()
    evaluation = wf.evaluate_step(
        contract=contract,
        evidence=evidence,
        criteria=criteria,
        run=run,
        attempt=attempt,
        step_id=step_id,
        description=description,
    )
    next_action = _DECISION_TO_ACTION[evaluation.decision]
    feedback = render_feedback(evaluation, contract=contract, evidence=evidence)
    logger.debug(
        "agent step evaluated: decision=%s next_action=%s score=%s",
        evaluation.decision,
        next_action,
        evaluation.score,
    )
    return AgentControlResult(
        evaluation=evaluation,
        next_action=next_action,
        feedback=feedback,
    )

