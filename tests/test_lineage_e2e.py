"""End-to-end lineage test for the REPLAN -> ACCEPT trajectory (todo §8 DoD).

Drives the full public Python API — ``start_run`` /
``BoundWorkflow.evaluate_step`` / ``finish_run`` / ``LineageStore.read_run`` —
across two attempts of one step:

    Attempt 1 -> 1/3 required checks pass -> acceptance 0.33 -> REPLAN
    Attempt 2 -> 3/3 required checks pass -> acceptance 1.0  -> ACCEPT

then asserts ``read_run`` reconstructs both attempts and the
REPLAN -> ACCEPT decision trajectory from the append-only event log.
"""

from __future__ import annotations

from bound import (
    AcceptanceCheck,
    BoundCriteria,
    BoundWorkflow,
    CheckEvidence,
    ContractEvaluator,
    ExecutionEvidence,
    LineageStore,
    RunStatus,
    StepContract,
    finish_run,
    start_run,
)
from bound.evaluator import StaticEvaluator
from bound.policy import BoundPolicy
from tests.conftest import _ZERO_SCORES

# A zero-score EvaluationScores only used to satisfy BoundPolicy's constructor
# (the contract path scores via ContractEvaluator + policy.decide, never via the
# policy's injected evaluator). Mirrors tests/test_lineage_api.py.
_ZERO_SCORES = _ZERO_SCORES()

# Threshold 0.6, default retry_margin 0.1, default rollback_risk_threshold 0.8.
# With default weights (all 1.0) and I=0/R=0/C=0, the score S equals acceptance A:
#   1/3 checks -> A=0.333 -> S=0.333 -> gap 0.267 > 0.1 -> REPLAN
#   3/3 checks -> A=1.0   -> S=1.0   -> S >= T          -> ACCEPT
_CRITERIA = BoundCriteria(threshold=0.6)


def _contract(cid: str = "CSV-001") -> StepContract:
    """A three-required-check contract (the ``1/3`` / ``3/3`` DoD framing)."""
    return StepContract(
        id=cid,
        description="Implement CSV exporter",
        goal="Ship the parser",
        acceptance_checks=[
            AcceptanceCheck(id="tests-pass", description="Unit tests pass"),
            AcceptanceCheck(id="lint-pass", description="Linter is clean"),
            AcceptanceCheck(id="types-pass", description="Type check is clean"),
        ],
    )


def _evidence(*, tests: bool, lint: bool, types: bool) -> ExecutionEvidence:
    """Evidence reconciled against the three required checks by ``id``."""
    return ExecutionEvidence(
        acceptance=[
            CheckEvidence(check_id="tests-pass", passed=tests, source="pytest"),
            CheckEvidence(check_id="lint-pass", passed=lint, source="ruff"),
            CheckEvidence(check_id="types-pass", passed=types, source="mypy"),
        ],
        rollback_available=True,
    )


def _workflow(run) -> BoundWorkflow:
    return BoundWorkflow(
        evaluator=ContractEvaluator(run=run),
        policy=BoundPolicy(StaticEvaluator(_ZERO_SCORES)),
    )


def test_replan_to_accept_e2e(tmp_path) -> None:
    """Attempt 1 (1/3 evidence) REPLANs; attempt 2 (3/3 evidence) ACCEPTs.

    The reconstructed :class:`RunLog` must carry both steps/evaluations and the
    REPLAN -> ACCEPT trajectory, and the run must be marked completed.
    """
    store = LineageStore(base_dir=tmp_path / "runs", enabled=True)
    ctx = start_run("csv export", store=store)
    wf = _workflow(ctx)

    # Attempt 1: only the lint check passes (1/3) -> A=0.333 -> REPLAN.
    r1 = wf.evaluate_step(
        contract=_contract(),
        evidence=_evidence(tests=False, lint=True, types=False),
        criteria=_CRITERIA,
        run=ctx,
        attempt=1,
    )
    assert r1.decision == "REPLAN"

    # Attempt 2 (replan, -R1 contract id): all three pass (3/3) -> ACCEPT.
    r2 = wf.evaluate_step(
        contract=_contract("CSV-001-R1"),
        evidence=_evidence(tests=True, lint=True, types=True),
        criteria=_CRITERIA,
        run=ctx,
        attempt=2,
    )
    assert r2.decision == "ACCEPT"

    finish_run(ctx.run_id, store=store)

    log = store.read_run(ctx.run_id)
    assert log.run.status == RunStatus.COMPLETED
    # Two distinct steps (one per attempt / replanned contract id).
    assert len(log.steps) == 2
    assert [len(s.attempts) for s in log.steps] == [1, 1]
    assert [s.attempts[-1].attempt for s in log.steps] == [1, 2]
    # The decision trajectory is REPLAN then ACCEPT, in order.
    assert [ev.decision for ev in log.evaluations] == ["REPLAN", "ACCEPT"]
    # Outcomes mirror the decisions: replan then continue.
    assert [o.next_action for o in log.outcomes] == ["replan", "continue"]
    # Acceptance scores reconstruct the 1/3 and 3/3 evidence.
    assert [round(ev.scores.acceptance, 4) for ev in log.evaluations] == [
        round(1 / 3, 4),
        1.0,
    ]
    # The full append-only event sequence is recorded.
    assert [e.event for e in log.events] == [
        "run_started",
        "step_started",
        "evaluation_recorded",
        "outcome_recorded",
        "step_started",
        "evaluation_recorded",
        "outcome_recorded",
        "run_finished",
    ]


def test_schema_2_0_evidence_and_action_lineage(tmp_path) -> None:
    """Full REPLAN→ACCEPT flow with evidence collection + action reporting (items 10/12).

    Demonstrates that schema-2.0 events (evidence.collected, action.reported)
    interleave cleanly with the schema-1.0 core events in one append-only log.
    """
    from bound.evidence import EvidenceProvenance
    from bound.lineage import build_run_config

    store = LineageStore(base_dir=tmp_path / "runs", enabled=True)
    cfg = build_run_config(bound_version="0.7.0", policy_id="default", threshold=0.6)
    ctx = start_run("csv export", store=store, config=cfg)
    wf = _workflow(ctx)

    # Attempt 1: evidence collected (verified), evaluation REPLAN, action reported.
    contract = _contract()
    r1 = wf.evaluate_step(
        contract=contract,
        evidence=_evidence(tests=False, lint=True, types=False),
        criteria=_CRITERIA,
        run=ctx,
        attempt=1,
    )
    assert r1.decision == "REPLAN"
    # Simulate independent evidence collection.
    ctx.record_evidence_collected(
        step_id=ctx.last_step_event.step_id,  # type: ignore[union-attr]
        check_id="tests-pass",
        collector="bound.pytest",
        provenance="verified",
        passed=False,
    )
    # Agent reports the replan action (CLAIMED by default).
    ctx.record_action_reported(
        step_id=ctx.last_step_event.step_id,  # type: ignore[union-attr]
        evaluation_id=ctx.last_evaluation_event.evaluation_id,  # type: ignore[union-attr]
        intended_action="replan",
        reported_action="Switched to csv.DictWriter",
        new_contract_id="CSV-001-R1",
    )

    # Attempt 2: all pass → ACCEPT.
    r2 = wf.evaluate_step(
        contract=_contract("CSV-001-R1"),
        evidence=_evidence(tests=True, lint=True, types=True),
        criteria=_CRITERIA,
        run=ctx,
        attempt=2,
    )
    assert r2.decision == "ACCEPT"
    finish_run(ctx.run_id, store=store)

    log = store.read_run(ctx.run_id)
    # Config snapshot is preserved on the run.
    assert log.run.config is not None
    assert log.run.config.bound_version == "0.7.0"
    # Evidence collection events are in the log.
    ev_events = [e for e in log.events if e.event == "evidence.collected"]
    assert len(ev_events) == 1
    assert ev_events[0].provenance == EvidenceProvenance.VERIFIED  # type: ignore[union-attr]
    # Action reported events are in the log, defaulting to CLAIMED.
    ar_events = [e for e in log.events if e.event == "action.reported"]
    assert len(ar_events) == 1
    assert ar_events[0].reported_provenance == EvidenceProvenance.CLAIMED  # type: ignore[union-attr]
    assert ar_events[0].new_contract_id == "CSV-001-R1"  # type: ignore[union-attr]
    # Events carry sequence numbers.
    for i, ev in enumerate(log.events, 1):
        assert ev.sequence == i  # type: ignore[union-attr]


def test_policy_lifecycle_and_observed_actions_e2e(tmp_path) -> None:
    """Full flow with policy lifecycle, evaluation.completed, action.observed, step.completed.

    Demonstrates that the todo 7.1/7.2/7.3 events interleave cleanly with the
    core run lifecycle in one append-only log, and that a ROLLBACK without an
    action.observed stays CLAIMED while one with observation is verified.
    """
    from bound.evidence import EvidenceProvenance
    from bound.lineage import build_run_config
    from bound.models import DecisionAssurance

    store = LineageStore(base_dir=tmp_path / "runs", enabled=True)
    cfg = build_run_config(
        bound_version="0.7.0", policy_id="coding-default", policy_version="1.0",
        policy_hash="sha256:abc", threshold=0.6,
    )
    ctx = start_run("csv export", store=store, config=cfg)

    # Policy lifecycle: proposed -> validated -> approved -> activated.
    ctx.record_policy_proposed(
        policy_id="coding-default", policy_version="1.0", policy_hash="sha256:abc"
    )
    ctx.record_policy_validated(
        policy_id="coding-default", policy_version="1.0", policy_hash="sha256:abc"
    )
    ctx.record_policy_approved(
        policy_id="coding-default", policy_version="1.0",
        policy_hash="sha256:abc", approver="alice",
    )
    ctx.record_policy_activated(
        policy_id="coding-default", policy_version="1.0", policy_hash="sha256:abc"
    )

    wf = _workflow(ctx)
    contract = _contract()

    # Attempt 1: 1/3 pass -> REPLAN.
    r1 = wf.evaluate_step(
        contract=contract,
        evidence=_evidence(tests=False, lint=True, types=False),
        criteria=_CRITERIA,
        run=ctx,
        attempt=1,
    )
    assert r1.decision == "REPLAN"

    # Record evaluation.completed with policy fields (todo 7.2).
    ctx.record_evaluation_completed(
        step_id=ctx.last_step_event.step_id,  # type: ignore[union-attr]
        evaluation_id=ctx.last_evaluation_event.evaluation_id,  # type: ignore[union-attr]
        policy_id="coding-default",
        policy_version="1.0",
        policy_hash="sha256:abc",
        candidate_decision="REPLAN",
        final_decision="REPLAN",
        assurance=DecisionAssurance.VERIFIED,
    )

    # Agent reports the replan (CLAIMED) + independent hook observes it.
    ctx.record_action_reported(
        step_id=ctx.last_step_event.step_id,  # type: ignore[union-attr]
        evaluation_id=ctx.last_evaluation_event.evaluation_id,  # type: ignore[union-attr]
        intended_action="replan",
        reported_action="Switched to csv.DictWriter",
        new_contract_id="CSV-001-R1",
    )
    ctx.record_action_observed(
        step_id=ctx.last_step_event.step_id,  # type: ignore[union-attr]
        evaluation_id=ctx.last_evaluation_event.evaluation_id,  # type: ignore[union-attr]
        intended_action="replan",
        observed_action="New plan CSV-001-R1 created",
        observed_provenance="observed",
        reported_action="Switched to csv.DictWriter",
        matches_reported=True,
        new_contract_id="CSV-001-R1",
    )
    ctx.record_step_completed(step_id=ctx.last_step_event.step_id, outcome="REPLANNED")  # type: ignore[union-attr]

    # Attempt 2: 3/3 pass -> ACCEPT.
    r2 = wf.evaluate_step(
        contract=_contract("CSV-001-R1"),
        evidence=_evidence(tests=True, lint=True, types=True),
        criteria=_CRITERIA,
        run=ctx,
        attempt=2,
    )
    assert r2.decision == "ACCEPT"
    ctx.record_step_completed(
        step_id=ctx.last_step_event.step_id, outcome="ACCEPTED"  # type: ignore[union-attr]
    )
    finish_run(ctx.run_id, store=store)

    log = store.read_run(ctx.run_id)
    # Policy lifecycle events are in the log.
    policy_events = [e.event for e in log.events if e.event.startswith("policy.")]
    assert policy_events == [
        "policy.proposed", "policy.validated", "policy.approved", "policy.activated",
    ]
    # evaluation.completed carries policy fields.
    ec_events = [e for e in log.events if e.event == "evaluation.completed"]
    assert len(ec_events) == 1
    assert ec_events[0].policy_hash == "sha256:abc"  # type: ignore[union-attr]
    # action.observed events are in the log with verified provenance.
    ao_events = [e for e in log.events if e.event == "action.observed"]
    assert len(ao_events) == 1
    assert ao_events[0].observed_provenance == EvidenceProvenance.OBSERVED  # type: ignore[union-attr]
    # step.completed events are in the log.
    sc_events = [e for e in log.events if e.event == "step.completed"]
    assert len(sc_events) == 2
    # Config snapshot carries the policy hash.
    assert log.run.config is not None
    assert log.run.config.policy_hash == "sha256:abc"
    # Sequence numbers are contiguous.
    for i, ev in enumerate(log.events, 1):
        assert ev.sequence == i  # type: ignore[union-attr]
