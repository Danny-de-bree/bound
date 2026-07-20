from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from bound.integration import _DECISION_TO_ACTION, NextAction
from bound.lineage import (
    ActionObservedEvent,
    ActionReportedEvent,
    DecisionGatedEvent,
    EvaluationCompletedEvent,
    EvaluationRecordedEvent,
    EvidenceCollectedEvent,
    EvidenceCollectionFailedEvent,
    OutcomeRecordedEvent,
    PolicyActivatedEvent,
    PolicyApprovedEvent,
    PolicyProposedEvent,
    PolicyValidatedEvent,
    ReasonCode,
    RunConfigSnapshot,
    RunFinishedEvent,
    RunFinishStatus,
    StepCompletedEvent,
    StepStartedEvent,
)
from bound.lineage_store import LineageStore, get_default_store
from bound.models import Decision, DecisionAssurance, EvaluationResult, EvaluationScores

if TYPE_CHECKING:
    from bound.contracts import StepContract

__all__ = [
    "RunContext",
    "finish_run",
    "record_outcome",
    "record_step_evaluation",
    "start_run",
]

logger = logging.getLogger("bound.lineage_api")

#: BOUND decision -> evaluation reason code. The evaluation event mirrors the
#: deterministic decision rather than re-deriving free-text evidence, so the
#: recorded lineage is reproducible from the decision alone.
_DECISION_TO_EVAL_REASON: dict[str, ReasonCode] = {
    "ACCEPT": ReasonCode.ACCEPT,
    "RETRY": ReasonCode.RETRY,
    "REPLAN": ReasonCode.REPLAN,
    "ROLLBACK": ReasonCode.ROLLBACK,
}

#: Mapped control action -> outcome reason code.
_ACTION_TO_OUTCOME_REASON: dict[str, ReasonCode] = {
    "continue": ReasonCode.CONTINUED,
    "retry": ReasonCode.RETRIED,
    "replan": ReasonCode.REPLANNED,
    "rollback": ReasonCode.ROLLED_BACK,
}


def next_action_for(decision: Decision | str) -> NextAction:
    """Map a BOUND decision to its framework-neutral control action.

    Reuses :data:`bound.integration._DECISION_TO_ACTION` (the single runtime
    source of the decision->action translation) so lineage never invents a
    parallel mapping.
    """
    return _DECISION_TO_ACTION[str(decision)]


def evaluation_reason_for(decision: Decision | str) -> ReasonCode:
    """Return the evaluation reason code mirroring a BOUND decision."""
    return _DECISION_TO_EVAL_REASON[str(decision)]


def outcome_reason_for(next_action: NextAction | str) -> ReasonCode:
    """Return the outcome reason code mirroring a control action."""
    return _ACTION_TO_OUTCOME_REASON[str(next_action)]


class RunContext:
    """Handle for one BOUND run's lineage, wrapping a store + ``run_id``.

    Returned by :func:`start_run`. It is a thin, friendly facade over
    :class:`~bound.lineage_store.LineageStore`'s builders: each method delegates
    to the store, returning the typed lineage event. Usable as a context
    manager: on exit, if the run was not explicitly finished it is finished with
    status :attr:`~bound.lineage.RunFinishStatus.INTERRUPTED`, so a run is
    never left silently "started".

    When the backing store is disabled (``enabled=False``) the builders still
    construct and return events but persist nothing. The most recently written
    step / evaluation / outcome events are kept on the context as
    :attr:`last_step_event` / :attr:`last_evaluation_event` /
    :attr:`last_outcome_event` for callers (and
    :func:`record_step_evaluation`) that need their ids without re-reading.

    Attributes:
        run_id: The run identifier this context owns.
        store: The backing :class:`~bound.lineage_store.LineageStore`.
        enabled: Convenience alias for ``store.enabled``.
        finished: ``True`` once :meth:`finish_run` has been called.
    """

    def __init__(self, store: LineageStore, run_id: str) -> None:
        self._store = store
        self._run_id = run_id
        self._finished = False
        self.last_step_event: StepStartedEvent | None = None
        self.last_evaluation_event: EvaluationRecordedEvent | None = None
        self.last_outcome_event: OutcomeRecordedEvent | None = None

    @property
    def run_id(self) -> str:
        """The run identifier this context owns."""
        return self._run_id

    @property
    def store(self) -> LineageStore:
        """The backing lineage store."""
        return self._store

    @property
    def enabled(self) -> bool:
        """Whether the backing store persists lineage."""
        return self._store.enabled

    @property
    def finished(self) -> bool:
        """``True`` once :meth:`finish_run` has been called."""
        return self._finished

    def start_step(
        self,
        *,
        contract_id: str,
        attempt: int = 1,
        step_id: str | None = None,
        description: str | None = None,
        started_at: datetime | None = None,
    ) -> StepStartedEvent:
        """Append a ``step_started`` event (delegating to the store)."""
        event = self._store.start_step(
            self._run_id,
            contract_id=contract_id,
            attempt=attempt,
            step_id=step_id,
            description=description,
            started_at=started_at,
        )
        self.last_step_event = event
        return event

    def record_evaluation(
        self,
        *,
        step_id: str,
        attempt: int,
        scores: EvaluationScores,
        score: float,
        threshold: float,
        decision: Decision | str,
        reason_code: ReasonCode | str | None = None,
        evaluation_id: str | None = None,
        recorded_at: datetime | None = None,
        policy_id: str | None = None,
        policy_version: str | None = None,
        policy_hash: str | None = None,
        contract_hash: str | None = None,
        candidate_decision: Decision | str | None = None,
        final_decision: Decision | str | None = None,
        assurance: DecisionAssurance | str | None = None,
        effective_weights: dict[str, float] | None = None,
        collector_versions: dict[str, str] | None = None,
        raw_evidence_values: dict[str, float | None] | None = None,
        effective_evidence_values: dict[str, float] | None = None,
    ) -> EvaluationRecordedEvent:
        """Append an ``evaluation_recorded`` event (delegating to the store).

        ``reason_code`` defaults to the decision-mirroring reason code.
        Phase 7.2: the optional policy fields are forwarded so every
        evaluation records the policy id/version/hash (release blocker).
        """
        rc = reason_code if reason_code is not None else evaluation_reason_for(decision)
        event = self._store.record_evaluation(
            self._run_id,
            step_id=step_id,
            attempt=attempt,
            scores=scores,
            score=score,
            threshold=threshold,
            decision=decision,
            reason_code=rc,
            evaluation_id=evaluation_id,
            recorded_at=recorded_at,
            policy_id=policy_id,
            policy_version=policy_version,
            policy_hash=policy_hash,
            contract_hash=contract_hash,
            candidate_decision=candidate_decision,
            final_decision=final_decision,
            assurance=assurance,
            effective_weights=effective_weights,
            collector_versions=collector_versions,
            raw_evidence_values=raw_evidence_values,
            effective_evidence_values=effective_evidence_values,
        )
        self.last_evaluation_event = event
        return event

    def record_outcome(
        self,
        *,
        step_id: str,
        evaluation_id: str,
        decision: Decision | str,
        next_action: NextAction | str | None = None,
        reason_code: ReasonCode | str | None = None,
        note: str | None = None,
        recorded_at: datetime | None = None,
    ) -> OutcomeRecordedEvent:
        """Append an ``outcome_recorded`` event (delegating to the store).

        ``next_action`` defaults to the decision's mapped control action;
        ``reason_code`` defaults to the action-mirroring reason code.
        """
        na = next_action if next_action is not None else next_action_for(decision)
        rc = reason_code if reason_code is not None else outcome_reason_for(na)
        event = self._store.record_outcome(
            self._run_id,
            step_id=step_id,
            evaluation_id=evaluation_id,
            decision=decision,
            next_action=na,
            reason_code=rc,
            note=note,
            recorded_at=recorded_at,
        )
        self.last_outcome_event = event
        return event

    def record_evidence_collected(
        self,
        *,
        step_id: str,
        check_id: str,
        collector: str,
        provenance: str,
        passed: bool | None = None,
        status: str | None = None,
        artifact_hash: str | None = None,
        source: str | None = None,
        collector_version: str | None = None,
        observed_at: datetime | None = None,
        parent_event_id: str | None = None,
    ) -> EvidenceCollectedEvent:
        """Append an ``evidence.collected`` event (delegating to the store)."""
        return self._store.record_evidence_collected(
            self._run_id,
            step_id=step_id,
            check_id=check_id,
            collector=collector,
            provenance=provenance,
            passed=passed,
            status=status,
            artifact_hash=artifact_hash,
            source=source,
            collector_version=collector_version,
            observed_at=observed_at,
            parent_event_id=parent_event_id,
        )

    def record_evidence_collection_failed(
        self,
        *,
        step_id: str,
        error: str,
        check_id: str | None = None,
        collector: str | None = None,
        observed_at: datetime | None = None,
        parent_event_id: str | None = None,
    ) -> EvidenceCollectionFailedEvent:
        """Append an ``evidence.collection_failed`` event (delegating to the store)."""
        return self._store.record_evidence_collection_failed(
            self._run_id,
            step_id=step_id,
            error=error,
            check_id=check_id,
            collector=collector,
            observed_at=observed_at,
            parent_event_id=parent_event_id,
        )

    def record_decision_gated(
        self,
        *,
        step_id: str,
        evaluation_id: str,
        candidate_decision: Decision | str,
        final_decision: Decision | str,
        assurance: DecisionAssurance | str,
        assurance_reasons: list[str] | None = None,
        recorded_at: datetime | None = None,
        parent_event_id: str | None = None,
    ) -> DecisionGatedEvent:
        """Append a ``decision.gated`` event (delegating to the store)."""
        return self._store.record_decision_gated(
            self._run_id,
            step_id=step_id,
            evaluation_id=evaluation_id,
            candidate_decision=candidate_decision,
            final_decision=final_decision,
            assurance=assurance,
            assurance_reasons=assurance_reasons,
            recorded_at=recorded_at,
            parent_event_id=parent_event_id,
        )

    def record_action_reported(
        self,
        *,
        step_id: str,
        evaluation_id: str,
        intended_action: NextAction | str,
        reported_action: str,
        observed_action: str | None = None,
        observed_provenance: str | None = None,
        new_contract_id: str | None = None,
        note: str | None = None,
        recorded_at: datetime | None = None,
        parent_event_id: str | None = None,
    ) -> ActionReportedEvent:
        """Append an ``action.reported`` event (delegating to the store).

        The agent's self-report is always CLAIMED; an optional ``observed``
        confirmation from integration hooks can upgrade the provenance.
        """
        return self._store.record_action_reported(
            self._run_id,
            step_id=step_id,
            evaluation_id=evaluation_id,
            intended_action=intended_action,
            reported_action=reported_action,
            observed_action=observed_action,
            observed_provenance=observed_provenance,
            new_contract_id=new_contract_id,
            note=note,
            recorded_at=recorded_at,
            parent_event_id=parent_event_id,
        )

    # ------------------------------------------------- policy-lifecycle builders
    def record_policy_proposed(
        self,
        *,
        policy_id: str,
        policy_version: str,
        policy_hash: str,
        contract_hash: str | None = None,
        note: str | None = None,
        recorded_at: datetime | None = None,
        parent_event_id: str | None = None,
    ) -> PolicyProposedEvent:
        """Append a ``policy.proposed`` event (todo 7.1)."""
        return self._store.record_policy_proposed(
            self._run_id,
            policy_id=policy_id,
            policy_version=policy_version,
            policy_hash=policy_hash,
            contract_hash=contract_hash,
            note=note,
            recorded_at=recorded_at,
            parent_event_id=parent_event_id,
        )

    def record_policy_validated(
        self,
        *,
        policy_id: str,
        policy_version: str,
        policy_hash: str,
        contract_hash: str | None = None,
        note: str | None = None,
        recorded_at: datetime | None = None,
        parent_event_id: str | None = None,
    ) -> PolicyValidatedEvent:
        """Append a ``policy.validated`` event (todo 7.1)."""
        return self._store.record_policy_validated(
            self._run_id,
            policy_id=policy_id,
            policy_version=policy_version,
            policy_hash=policy_hash,
            contract_hash=contract_hash,
            note=note,
            recorded_at=recorded_at,
            parent_event_id=parent_event_id,
        )

    def record_policy_approved(
        self,
        *,
        policy_id: str,
        policy_version: str,
        policy_hash: str,
        approver: str,
        approved_at: datetime | None = None,
        contract_hash: str | None = None,
        note: str | None = None,
        parent_event_id: str | None = None,
    ) -> PolicyApprovedEvent:
        """Append a ``policy.approved`` event (todo 7.1).

        Records the human ``approver`` and approval time so the lineage proves
        *who* approved *what* and *when*.
        """
        return self._store.record_policy_approved(
            self._run_id,
            policy_id=policy_id,
            policy_version=policy_version,
            policy_hash=policy_hash,
            approver=approver,
            approved_at=approved_at,
            contract_hash=contract_hash,
            note=note,
            parent_event_id=parent_event_id,
        )

    def record_policy_activated(
        self,
        *,
        policy_id: str,
        policy_version: str,
        policy_hash: str,
        contract_hash: str | None = None,
        note: str | None = None,
        recorded_at: datetime | None = None,
        parent_event_id: str | None = None,
    ) -> PolicyActivatedEvent:
        """Append a ``policy.activated`` event (todo 7.1)."""
        return self._store.record_policy_activated(
            self._run_id,
            policy_id=policy_id,
            policy_version=policy_version,
            policy_hash=policy_hash,
            contract_hash=contract_hash,
            note=note,
            recorded_at=recorded_at,
            parent_event_id=parent_event_id,
        )

    # ------------------------------------------- evaluation/action/step builders
    def record_evaluation_completed(
        self,
        *,
        step_id: str,
        evaluation_id: str,
        policy_id: str | None = None,
        policy_version: str | None = None,
        policy_hash: str | None = None,
        contract_hash: str | None = None,
        candidate_decision: Decision | str | None = None,
        final_decision: Decision | str | None = None,
        assurance: DecisionAssurance | str | None = None,
        reason_code: ReasonCode | str | None = None,
        collector_versions: dict[str, str] | None = None,
        effective_weights: dict[str, float] | None = None,
        raw_evidence_values: dict[str, float | None] | None = None,
        effective_evidence_values: dict[str, float] | None = None,
        note: str | None = None,
        recorded_at: datetime | None = None,
        parent_event_id: str | None = None,
    ) -> EvaluationCompletedEvent:
        """Append an ``evaluation.completed`` event (todo 7.1/7.2).

        Marks the end of the full evaluation process and carries the complete
        trace-contents payload (policy id/version/hash, contract hash, collector
        versions, effective weights, raw/effective evidence, candidate/final
        decision, assurance).
        """
        return self._store.record_evaluation_completed(
            self._run_id,
            step_id=step_id,
            evaluation_id=evaluation_id,
            policy_id=policy_id,
            policy_version=policy_version,
            policy_hash=policy_hash,
            contract_hash=contract_hash,
            candidate_decision=candidate_decision,
            final_decision=final_decision,
            assurance=assurance,
            reason_code=reason_code,
            collector_versions=collector_versions,
            effective_weights=effective_weights,
            raw_evidence_values=raw_evidence_values,
            effective_evidence_values=effective_evidence_values,
            note=note,
            recorded_at=recorded_at,
            parent_event_id=parent_event_id,
        )

    def record_action_observed(
        self,
        *,
        step_id: str,
        evaluation_id: str,
        intended_action: NextAction | str,
        observed_action: str,
        observed_provenance: str,
        reported_action: str | None = None,
        matches_reported: bool | None = None,
        new_contract_id: str | None = None,
        note: str | None = None,
        recorded_at: datetime | None = None,
        parent_event_id: str | None = None,
    ) -> ActionObservedEvent:
        """Append an ``action.observed`` event (todo 7.1/7.3).

        Records an independent hook's observation of the agent's action — the
        proof that upgrades a ROLLBACK from CLAIMED to verified. When
        ``reported_action`` is supplied, ``matches_reported`` records whether
        the observation agrees with the agent's self-report (mismatch detection).
        """
        return self._store.record_action_observed(
            self._run_id,
            step_id=step_id,
            evaluation_id=evaluation_id,
            intended_action=intended_action,
            observed_action=observed_action,
            observed_provenance=observed_provenance,
            reported_action=reported_action,
            matches_reported=matches_reported,
            new_contract_id=new_contract_id,
            note=note,
            recorded_at=recorded_at,
            parent_event_id=parent_event_id,
        )

    def record_step_completed(
        self,
        *,
        step_id: str,
        outcome: str | None = None,
        note: str | None = None,
        recorded_at: datetime | None = None,
        parent_event_id: str | None = None,
    ) -> StepCompletedEvent:
        """Append a ``step.completed`` event (todo 7.1)."""
        return self._store.record_step_completed(
            self._run_id,
            step_id=step_id,
            outcome=outcome,
            note=note,
            recorded_at=recorded_at,
            parent_event_id=parent_event_id,
        )

    def finish_run(
        self,
        *,
        status: RunFinishStatus | str = RunFinishStatus.COMPLETED,
        reason_code: ReasonCode | str = ReasonCode.RUN_COMPLETED,
        note: str | None = None,
        finished_at: datetime | None = None,
    ) -> RunFinishedEvent:
        """Append the terminal ``run_finished`` event (delegating to the store)."""
        event = self._store.finish_run(
            self._run_id,
            status=status,
            reason_code=reason_code,
            note=note,
            finished_at=finished_at,
        )
        self._finished = True
        return event

    def __enter__(self) -> RunContext:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        # Safety net: a run that exits its `with` block without an explicit
        # finish is conservatively marked INTERRUPTED rather than left "started"
        # (which would read as an incomplete / crashed run).
        if not self._finished:
            self.finish_run(
                status=RunFinishStatus.INTERRUPTED,
                reason_code=ReasonCode.RUN_INTERRUPTED,
            )
        return False


def record_step_evaluation(
    run: RunContext,
    *,
    contract: StepContract,
    result: EvaluationResult,
    attempt: int = 1,
    step_id: str | None = None,
    description: str | None = None,
) -> None:
    """Auto-write one step's full lineage (step_started + evaluation_recorded + outcome_recorded).

    Called by :meth:`bound.bound_workflow.BoundWorkflow.evaluate_step` when a run
    context is supplied. It derives the ``next_action`` and reason codes from the
    deterministic decision, so a caller gets a complete, reproducible lineage
    record with no extra effort. Persists nothing when the run's store is
    disabled.

    Args:
        run: The owning :class:`RunContext`.
        contract: The :class:`~bound.contracts.StepContract` (its ``id`` is the
            stable ``contract_id``; its ``description`` is the default step
            description).
        result: The deterministic :class:`~bound.models.EvaluationResult` whose
            scores / score / threshold / decision are recorded.
        attempt: One-based attempt number for this step (default ``1``).
        step_id: Optional explicit step id; otherwise derived deterministically
            from ``run_id`` + ``contract_id`` + ``attempt``.
        description: Optional step description; defaults to ``contract.description``.
    """
    if not run.enabled:
        return
    step_event = run.start_step(
        contract_id=contract.id,
        attempt=attempt,
        step_id=step_id,
        description=description if description is not None else contract.description,
    )
    evaluation_event = run.record_evaluation(
        step_id=step_event.step_id,
        attempt=attempt,
        scores=result.scores,
        score=result.score,
        threshold=result.threshold,
        decision=result.decision,
    )
    run.record_outcome(
        step_id=step_event.step_id,
        evaluation_id=evaluation_event.evaluation_id,
        decision=result.decision,
    )


# ---------------------------------------------------------------- module API


def start_run(
    task: str,
    *,
    metadata: dict[str, str] | None = None,
    config: RunConfigSnapshot | None = None,
    store: LineageStore | None = None,
) -> RunContext:
    """Start a new lineage run and return its :class:`RunContext`.

    Args:
        task: The natural-language task the run attempts.
        metadata: Optional free-form string metadata (never secrets; the privacy
            layer redacts before persistence).
        config: Optional :class:`~bound.lineage.RunConfigSnapshot` logging the
            policy/config version that governed this run (item 11).
        store: Optional explicit store; defaults to
            :func:`~bound.lineage_store.get_default_store` (which respects the
            ``BOUND_LINEAGE_DISABLED`` environment variable).

    Returns:
        A :class:`RunContext` owning the new ``run_id``.
    """
    backing = store if store is not None else get_default_store()
    event = backing.start_run(task, metadata=metadata, config=config)
    return RunContext(backing, event.run_id)


def record_outcome(
    run_id: str,
    *,
    step_id: str,
    evaluation_id: str,
    decision: Decision | str,
    next_action: NextAction | str | None = None,
    reason_code: ReasonCode | str | None = None,
    note: str | None = None,
    recorded_at: datetime | None = None,
    store: LineageStore | None = None,
) -> OutcomeRecordedEvent:
    """Module-level convenience appending an ``outcome_recorded`` event.

    Delegates to :func:`~bound.lineage_store.get_default_store` when no
    ``store`` is given. ``next_action`` / ``reason_code`` default to the
    decision-mirrored values.
    """
    backing = store if store is not None else get_default_store()
    na = next_action if next_action is not None else next_action_for(decision)
    rc = reason_code if reason_code is not None else outcome_reason_for(na)
    return backing.record_outcome(
        run_id,
        step_id=step_id,
        evaluation_id=evaluation_id,
        decision=decision,
        next_action=na,
        reason_code=rc,
        note=note,
        recorded_at=recorded_at,
    )


def finish_run(
    run_id: str,
    *,
    status: RunFinishStatus | str = RunFinishStatus.COMPLETED,
    reason_code: ReasonCode | str = ReasonCode.RUN_COMPLETED,
    note: str | None = None,
    finished_at: datetime | None = None,
    store: LineageStore | None = None,
) -> RunFinishedEvent:
    """Module-level convenience appending the terminal ``run_finished`` event.

    Delegates to :func:`~bound.lineage_store.get_default_store` when no
    ``store`` is given.
    """
    backing = store if store is not None else get_default_store()
    return backing.finish_run(
        run_id,
        status=status,
        reason_code=reason_code,
        note=note,
        finished_at=finished_at,
    )

