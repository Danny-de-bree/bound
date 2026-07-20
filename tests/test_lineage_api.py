"""Tests for the lineage Python API (BOUND v0.7.0 §3).

Covers the public surface in :mod:`bound.lineage_api` and its wiring into
:meth:`bound.bound_workflow.BoundWorkflow.evaluate_step` /
:func:`bound.integration.evaluate_agent_step`:

* ``start_run`` -> :class:`RunContext` and its store-delegating builders.
* ``evaluate_step`` auto-writing ``step_started`` + ``evaluation_recorded`` +
  ``outcome_recorded`` when a run context is supplied.
* module-level ``record_outcome`` / ``finish_run`` round trip + ``read_run``
  reconstructing the decisions.
* disabled stores (and the ``BOUND_LINEAGE_DISABLED`` env var) writing nothing.
* backwards compatibility: no run context -> identical behaviour.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bound import (
    AcceptanceCheck,
    BoundCriteria,
    BoundWorkflow,
    CheckEvidence,
    ContractEvaluator,
    ExecutionEvidence,
    LineageStore,
    ReasonCode,
    RunNotFound,
    RunStatus,
    StepContract,
    finish_run,
    record_outcome,
    start_run,
)
from bound.evaluator import StaticEvaluator
from bound.integration import _DECISION_TO_ACTION, evaluate_agent_step
from bound.lineage_api import (
    RunContext,
    evaluation_reason_for,
    outcome_reason_for,
    record_step_evaluation,
)
from bound.policy import BoundPolicy
from tests.conftest import _ZERO_SCORES

# A zero-score EvaluationScores only used to satisfy BoundPolicy's constructor
# (the contract path scores via ContractEvaluator + policy.decide, never via the
# policy's injected evaluator). Mirrors tests/test_architecture.py.
_ZERO_SCORES = _ZERO_SCORES()

_CRITERIA = BoundCriteria(threshold=0.6)  # retry_margin=0.1, rollback_risk=0.8


def _contract(cid: str = "PHASE-001", desc: str = "Implement exporter") -> StepContract:
    return StepContract(
        id=cid,
        description=desc,
        goal="Ship the parser",
        acceptance_checks=[
            AcceptanceCheck(id="tests-pass", description="All unit tests pass"),
            AcceptanceCheck(id="lint-pass", description="The linter is clean"),
        ],
    )


def _evidence(pass_tests: bool, pass_lint: bool, rollback: bool = True) -> ExecutionEvidence:
    return ExecutionEvidence(
        acceptance=[
            CheckEvidence(check_id="tests-pass", passed=pass_tests, source="pytest"),
            CheckEvidence(check_id="lint-pass", passed=pass_lint, source="ruff"),
        ],
        rollback_available=rollback,
    )


def _workflow(run: RunContext | None = None) -> BoundWorkflow:
    return BoundWorkflow(
        evaluator=ContractEvaluator(run=run),
        policy=BoundPolicy(StaticEvaluator(_ZERO_SCORES)),
    )


# (label, evidence, expected decision, expected next_action)
_SCENARIOS = [
    ("accept", _evidence(True, True), "ACCEPT", "continue"),
    ("retry", _evidence(True, False), "RETRY", "retry"),
    ("replan", _evidence(False, False), "REPLAN", "replan"),
    ("rollback", _evidence(True, True, rollback=False), "ROLLBACK", "rollback"),
]


@pytest.fixture
def store(tmp_path: Path) -> LineageStore:
    """An enabled, isolated lineage store rooted in a temp directory."""
    return LineageStore(base_dir=tmp_path / "runs", enabled=True)




def test_start_run_returns_run_context_writing_run_json(store: LineageStore) -> None:
    """start_run returns a RunContext owning a fresh run_id and writes run.json."""
    ctx = start_run("CSV export", metadata={"repo": "bound"}, store=store)

    assert isinstance(ctx, RunContext)
    assert ctx.run_id.startswith("run_")
    assert ctx.enabled is True
    assert ctx.finished is False
    assert (store.base_dir / ctx.run_id / "run.json").exists()


def test_evaluate_step_auto_writes_lineage_and_returns_unchanged(store: LineageStore) -> None:
    """evaluate_step writes the full step lineage as a side effect, return unchanged."""
    wf = _workflow()
    contract, evidence = _contract(), _evidence(True, True)
    ctx = start_run("CSV export", store=store)

    result = wf.evaluate_step(contract=contract, evidence=evidence, criteria=_CRITERIA, run=ctx)

    # Return type/value is unchanged (ACCEPT, S=1.0).
    assert result.decision == "ACCEPT"
    assert result.score == pytest.approx(1.0)

    # Lineage: one step / evaluation / outcome, in append-only order.
    log = store.read_run(ctx.run_id)
    assert [e.event for e in log.events] == [
        "run_started",
        "step_started",
        "evaluation_recorded",
        "outcome_recorded",
    ]
    assert len(log.steps) == 1
    assert log.steps[0].contract_id == "PHASE-001"
    assert log.steps[0].description == "Implement exporter"
    assert len(log.evaluations) == 1
    assert len(log.outcomes) == 1
    assert log.evaluations[0].decision == "ACCEPT"
    assert log.evaluations[0].score == pytest.approx(1.0)
    assert log.outcomes[0].next_action == "continue"
    assert log.outcomes[0].reason_code == ReasonCode.CONTINUED
    # The context keeps the last written events for callers.
    assert ctx.last_evaluation_event is not None
    assert ctx.last_outcome_event is not None


def test_round_trip_finish_run_reconstructs_decisions(store: LineageStore) -> None:
    """start_run -> evaluate_step(auto) -> finish_run round trip reconstructs decisions."""
    wf = _workflow()
    contract, evidence = _contract(), _evidence(True, True)
    ctx = start_run("CSV export", store=store)

    result = wf.evaluate_step(contract=contract, evidence=evidence, criteria=_CRITERIA, run=ctx)
    finish_run(ctx.run_id, store=store)

    log = store.read_run(ctx.run_id)
    assert log.run.status == RunStatus.COMPLETED
    assert log.run.finished_at is not None
    assert log.incomplete is False
    assert log.evaluations[0].decision == result.decision
    assert log.outcomes[0].decision == result.decision


def test_module_level_record_outcome_manual_flow(store: LineageStore) -> None:
    """The module-level record_outcome/finish_run convenience works on a manual flow."""
    ctx = start_run("manual task", store=store)
    step = ctx.start_step(contract_id="PHASE-001", description="do the thing")
    evaluation = ctx.record_evaluation(
        step_id=step.step_id,
        attempt=1,
        scores=_ZERO_SCORES,
        score=0.0,
        threshold=0.6,
        decision="REPLAN",
    )

    # Module-level convenience (no explicit next_action/reason_code: derived).
    outcome = record_outcome(
        ctx.run_id,
        step_id=step.step_id,
        evaluation_id=evaluation.evaluation_id,
        decision="REPLAN",
        note="switched to csv.DictWriter",
        store=store,
    )
    assert outcome.next_action == "replan"
    assert outcome.reason_code == ReasonCode.REPLANNED

    finish_run(ctx.run_id, store=store)
    log = store.read_run(ctx.run_id)
    assert log.outcomes[0].next_action == "replan"
    assert log.outcomes[0].note == "switched to csv.DictWriter"
    assert log.outcomes[0].reason_code == ReasonCode.REPLANNED



def test_disabled_store_writes_nothing(tmp_path: Path) -> None:
    """A disabled store persists nothing while the evaluation still works."""
    disabled = LineageStore(base_dir=tmp_path / "disabled", enabled=False)
    ctx = start_run("task", store=disabled)
    assert ctx.enabled is False

    wf = _workflow()
    contract, evidence = _contract(), _evidence(True, True)
    result = wf.evaluate_step(contract=contract, evidence=evidence, criteria=_CRITERIA, run=ctx)

    # The decision is still computed (lineage is a side effect only).
    assert result.decision == "ACCEPT"
    # Nothing was persisted: no run directory / events file.
    assert not (tmp_path / "disabled").exists()
    with pytest.raises(RunNotFound):
        disabled.read_run(ctx.run_id)


def test_backwards_compatible_no_run_context_is_identical(store: LineageStore) -> None:
    """No run context -> identical EvaluationResult; nothing is written."""
    wf = _workflow()
    contract, evidence = _contract(), _evidence(True, True)

    without_run = wf.evaluate_step(contract=contract, evidence=evidence, criteria=_CRITERIA)
    ctx = start_run("task", store=store)
    with_run = wf.evaluate_step(contract=contract, evidence=evidence, criteria=_CRITERIA, run=ctx)

    # Supplying a run context does not change the deterministic result.
    assert without_run == with_run
    assert with_run.decision == "ACCEPT"


def test_context_manager_auto_finishes_interrupted(store: LineageStore) -> None:
    """Exiting a `with` block without an explicit finish marks the run INTERRUPTED."""
    with start_run("task", store=store) as ctx:
        pass  # no explicit finish_run
    assert ctx.finished is True
    log = store.read_run(ctx.run_id)
    assert log.run.status == RunStatus.INTERRUPTED
    # A run_finished event was written, so the run is not "incomplete".
    assert log.incomplete is False

    # An explicit finish inside the block is respected (not overwritten).
    with start_run("task2", store=store) as ctx2:
        ctx2.finish_run()
    log2 = store.read_run(ctx2.run_id)
    assert log2.run.status == RunStatus.COMPLETED


def test_context_manager_finishes_on_exception(store: LineageStore) -> None:
    """An exception inside the block still finishes the run (INTERRUPTED)."""
    with pytest.raises(RuntimeError), start_run("boom", store=store) as ctx:
        raise RuntimeError("kaboom")
    log = store.read_run(ctx.run_id)
    assert log.run.status == RunStatus.INTERRUPTED


def test_multi_step_run_records_each_step(store: LineageStore) -> None:
    """Multiple evaluate_step calls on one run record one step each."""
    wf = _workflow()
    ctx = start_run("multi-step task", store=store)
    wf.evaluate_step(
        contract=_contract("PHASE-001"), evidence=_evidence(True, True), criteria=_CRITERIA, run=ctx
    )
    wf.evaluate_step(
        contract=_contract("PHASE-002"), evidence=_evidence(True, True), criteria=_CRITERIA, run=ctx
    )
    finish_run(ctx.run_id, store=store)

    log = store.read_run(ctx.run_id)
    assert len(log.steps) == 2
    assert len(log.evaluations) == 2
    assert len(log.outcomes) == 2
    assert {s.contract_id for s in log.steps} == {"PHASE-001", "PHASE-002"}


@pytest.mark.parametrize("label,evidence,decision,next_action", _SCENARIOS)
def test_decision_maps_to_next_action_and_reason(
    store: LineageStore, label: str, evidence: ExecutionEvidence, decision: str, next_action: str
) -> None:
    """Every decision maps to the correct next_action + reason code in lineage."""
    wf = _workflow()
    ctx = start_run(label, store=store)
    result = wf.evaluate_step(contract=_contract(), evidence=evidence, criteria=_CRITERIA, run=ctx)

    assert result.decision == decision
    log = store.read_run(ctx.run_id)
    assert log.evaluations[0].decision == decision
    assert log.evaluations[0].reason_code == evaluation_reason_for(decision)
    assert log.outcomes[0].next_action == next_action
    assert log.outcomes[0].next_action == _DECISION_TO_ACTION[decision]
    assert log.outcomes[0].reason_code == outcome_reason_for(next_action)




def test_evaluator_lineage_run_fallback(store: LineageStore) -> None:
    """A run configured on the evaluator auto-records without a per-call run arg."""
    ctx = start_run("configured-on-evaluator", store=store)
    wf = _workflow(run=ctx)  # ContractEvaluator(run=ctx)
    contract, evidence = _contract(), _evidence(True, True)

    # No explicit run= argument: the evaluator's lineage_run is the fallback.
    result = wf.evaluate_step(contract=contract, evidence=evidence, criteria=_CRITERIA)

    assert result.decision == "ACCEPT"
    log = store.read_run(ctx.run_id)
    assert len(log.evaluations) == 1
    assert log.outcomes[0].next_action == "continue"


def test_explicit_run_overrides_evaluator_lineage_run(store: LineageStore) -> None:
    """An explicit run= argument takes precedence over the evaluator's lineage_run."""
    fallback_ctx = start_run("fallback", store=store)
    explicit_ctx = start_run("explicit", store=store)
    wf = _workflow(run=fallback_ctx)
    contract, evidence = _contract(), _evidence(True, True)

    wf.evaluate_step(contract=contract, evidence=evidence, criteria=_CRITERIA, run=explicit_ctx)

    # Lineage landed on the explicit run, not the evaluator's fallback run.
    explicit_log = store.read_run(explicit_ctx.run_id)
    assert len(explicit_log.evaluations) == 1
    # The fallback run only has its run_started (from start_run): no steps/evals.
    fallback_log = store.read_run(fallback_ctx.run_id)
    assert len(fallback_log.steps) == 0
    assert len(fallback_log.evaluations) == 0


def test_evaluate_agent_step_threads_run(store: LineageStore) -> None:
    """evaluate_agent_step forwards the run context so lineage is auto-recorded."""
    ctx = start_run("agent task", store=store)
    contract, evidence = _contract(), _evidence(True, True)

    result = evaluate_agent_step(contract, evidence, _CRITERIA, run=ctx)

    assert result.next_action == "continue"
    assert result.evaluation.decision == "ACCEPT"
    log = store.read_run(ctx.run_id)
    assert [e.event for e in log.events] == [
        "run_started",
        "step_started",
        "evaluation_recorded",
        "outcome_recorded",
    ]


def test_record_step_evaluation_helper_disabled_is_noop() -> None:
    """record_step_evaluation persists nothing when the run's store is disabled."""
    disabled = LineageStore(base_dir=Path("/tmp/bound-disabled-noop"), enabled=False)
    ctx = RunContext(disabled, "run_disabled")
    wf = _workflow()
    result = wf.evaluate_step(
        contract=_contract(), evidence=_evidence(True, True), criteria=_CRITERIA
    )
    record_step_evaluation(ctx, contract=_contract(), result=result)
    # No events recorded on the context (helper short-circuits when disabled).
    assert ctx.last_step_event is None
    assert ctx.last_evaluation_event is None


def test_env_var_bound_lineage_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """BOUND_LINEAGE_DISABLED disables the default store (no persistence)."""
    import bound.lineage_store as lineage_store

    monkeypatch.setenv("BOUND_LINEAGE_DISABLED", "1")
    monkeypatch.setattr(lineage_store, "_default_store", None)

    ctx = start_run("env-disabled task")
    assert ctx.enabled is False

    wf = _workflow()
    contract, evidence = _contract(), _evidence(True, True)
    result = wf.evaluate_step(contract=contract, evidence=evidence, criteria=_CRITERIA, run=ctx)
    assert result.decision == "ACCEPT"
    with pytest.raises(RunNotFound):
        ctx.store.read_run(ctx.run_id)


def test_replan_to_accept_two_attempts(store: LineageStore) -> None:
    """End-to-end REPLAN -> ACCEPT across two attempts of one step (todo §8 DoD)."""
    ctx = start_run("csv export", store=store)
    wf = _workflow()
    contract = _contract("CSV-001", "Implement CSV exporter")

    # Attempt 1: 0/2 required checks pass -> A=0.0 -> REPLAN.
    r1 = wf.evaluate_step(
        contract=contract, evidence=_evidence(False, False), criteria=_CRITERIA, run=ctx, attempt=1
    )
    assert r1.decision == "REPLAN"
    # Attempt 2 (replan, -R1 contract id): 2/2 pass -> ACCEPT.
    r2 = wf.evaluate_step(
        contract=_contract("CSV-001-R1", "Use csv.DictWriter"),
        evidence=_evidence(True, True),
        criteria=_CRITERIA,
        run=ctx,
        attempt=2,
    )
    assert r2.decision == "ACCEPT"
    finish_run(ctx.run_id, store=store)

    log = store.read_run(ctx.run_id)
    assert len(log.steps) == 2
    assert [s.attempts[-1].attempt for s in log.steps] == [1, 2]
    assert [ev.decision for ev in log.evaluations] == ["REPLAN", "ACCEPT"]
    assert [o.next_action for o in log.outcomes] == ["replan", "continue"]


def test_run_context_record_evidence_collected(store: LineageStore) -> None:
    """RunContext.record_evidence_collected appends an evidence.collected event."""
    ctx = start_run("task", store=store)
    s = ctx.start_step(contract_id="PHASE-001", attempt=1)
    evt = ctx.record_evidence_collected(
        step_id=s.step_id,
        check_id="tests-pass",
        collector="bound.pytest",
        provenance="verified",
        passed=True,
    )
    assert evt.event == "evidence.collected"
    assert evt.provenance.value == "verified"
    log = store.read_run(ctx.run_id)
    assert any(e.event == "evidence.collected" for e in log.events)


def test_run_context_record_action_reported(store: LineageStore) -> None:
    """RunContext.record_action_reported defaults to CLAIMED (item 12)."""
    ctx = start_run("task", store=store)
    s = ctx.start_step(contract_id="PHASE-001", attempt=1)
    ev = ctx.record_evaluation(
        step_id=s.step_id, attempt=1, scores=_ZERO_SCORES, score=0.4,
        threshold=0.6, decision="REPLAN",
    )
    evt = ctx.record_action_reported(
        step_id=s.step_id,
        evaluation_id=ev.evaluation_id,
        intended_action="replan",
        reported_action="Switched to csv.DictWriter",
        new_contract_id="PHASE-001-R1",
    )
    assert evt.event == "action.reported"
    assert evt.reported_provenance.value == "claimed"
    assert evt.new_contract_id == "PHASE-001-R1"


def test_run_context_record_policy_lifecycle(store: LineageStore) -> None:
    """RunContext records the full policy lifecycle (todo 7.1)."""
    ctx = start_run("task", store=store)
    p = ctx.record_policy_proposed(
        policy_id="coding-default", policy_version="1.0", policy_hash="sha256:abc"
    )
    assert p.event == "policy.proposed"
    v = ctx.record_policy_validated(
        policy_id="coding-default", policy_version="1.0", policy_hash="sha256:abc"
    )
    assert v.event == "policy.validated"
    a = ctx.record_policy_approved(
        policy_id="coding-default",
        policy_version="1.0",
        policy_hash="sha256:abc",
        approver="alice",
    )
    assert a.event == "policy.approved"
    assert a.approver == "alice"
    act = ctx.record_policy_activated(
        policy_id="coding-default", policy_version="1.0", policy_hash="sha256:abc"
    )
    assert act.event == "policy.activated"
    log = store.read_run(ctx.run_id)
    policy_events = [e.event for e in log.events if e.event.startswith("policy.")]
    assert policy_events == [
        "policy.proposed",
        "policy.validated",
        "policy.approved",
        "policy.activated",
    ]


def test_run_context_record_evaluation_completed(store: LineageStore) -> None:
    """RunContext.record_evaluation_completed appends an evaluation.completed event."""
    ctx = start_run("task", store=store)
    s = ctx.start_step(contract_id="PHASE-001", attempt=1)
    ev = ctx.record_evaluation(
        step_id=s.step_id, attempt=1, scores=_ZERO_SCORES, score=0.4,
        threshold=0.6, decision="REPLAN",
    )
    ec = ctx.record_evaluation_completed(
        step_id=s.step_id,
        evaluation_id=ev.evaluation_id,
        policy_id="coding-default",
        policy_version="1.0",
        policy_hash="sha256:abc",
        candidate_decision="REPLAN",
        final_decision="REPLAN",
        assurance="verified",
    )
    assert ec.event == "evaluation.completed"
    assert ec.policy_hash == "sha256:abc"  # type: ignore[union-attr]
    assert ec.assurance.value == "verified"  # type: ignore[union-attr]


def test_run_context_record_action_observed(store: LineageStore) -> None:
    """RunContext.record_action_observed records proof of a ROLLBACK (todo 7.3)."""
    ctx = start_run("task", store=store)
    s = ctx.start_step(contract_id="PHASE-001", attempt=1)
    ev = ctx.record_evaluation(
        step_id=s.step_id, attempt=1, scores=_ZERO_SCORES, score=0.4,
        threshold=0.6, decision="ROLLBACK",
    )
    ao = ctx.record_action_observed(
        step_id=s.step_id,
        evaluation_id=ev.evaluation_id,
        intended_action="rollback",
        observed_action="Files restored to HEAD",
        observed_provenance="observed",
        reported_action="Rolled back",
        matches_reported=True,
    )
    assert ao.event == "action.observed"
    assert ao.observed_provenance.value == "observed"  # type: ignore[union-attr]
    assert ao.matches_reported is True  # type: ignore[union-attr]


def test_run_context_record_action_observed_mismatch(store: LineageStore) -> None:
    """Todo 7.3: action mismatch (reported != observed) is recorded."""
    ctx = start_run("task", store=store)
    s = ctx.start_step(contract_id="PHASE-001", attempt=1)
    ev = ctx.record_evaluation(
        step_id=s.step_id, attempt=1, scores=_ZERO_SCORES, score=0.4,
        threshold=0.6, decision="ROLLBACK",
    )
    ao = ctx.record_action_observed(
        step_id=s.step_id,
        evaluation_id=ev.evaluation_id,
        intended_action="rollback",
        observed_action="No files changed",
        observed_provenance="observed",
        reported_action="Rolled back successfully",
        matches_reported=False,
    )
    assert ao.matches_reported is False  # type: ignore[union-attr]


def test_run_context_record_step_completed(store: LineageStore) -> None:
    """RunContext.record_step_completed appends a step.completed event."""
    ctx = start_run("task", store=store)
    s = ctx.start_step(contract_id="PHASE-001", attempt=1)
    sc = ctx.record_step_completed(step_id=s.step_id, outcome="ACCEPTED")
    assert sc.event == "step.completed"
    assert sc.outcome == "ACCEPTED"  # type: ignore[union-attr]
    log = store.read_run(ctx.run_id)
    assert any(e.event == "step.completed" for e in log.events)


def test_run_context_record_evaluation_with_policy_fields(store: LineageStore) -> None:
    """Phase 7.2: record_evaluation forwards policy fields to the event."""
    ctx = start_run("task", store=store)
    s = ctx.start_step(contract_id="PHASE-001", attempt=1)
    ev = ctx.record_evaluation(
        step_id=s.step_id, attempt=1, scores=_ZERO_SCORES, score=1.0,
        threshold=0.6, decision="ACCEPT",
        policy_id="coding-default", policy_version="1.0", policy_hash="sha256:abc",
        assurance="verified", collector_versions={"bound.pytest": "0.7.0"},
    )
    assert ev.policy_id == "coding-default"
    assert ev.policy_hash == "sha256:abc"
    assert ev.assurance.value == "verified"


def test_start_run_with_config(store: LineageStore) -> None:
    """start_run accepts a RunConfigSnapshot (item 11)."""
    from bound.lineage import build_run_config

    cfg = build_run_config(bound_version="0.7.0", policy_id="default", threshold=0.6)
    ctx = start_run("task", store=store, config=cfg)
    log = store.read_run(ctx.run_id)
    assert log.run.config is not None
    assert log.run.config.bound_version == "0.7.0"
    assert log.run.config.threshold == 0.6

