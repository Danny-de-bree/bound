from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from bound.lineage import (
    EVENT_NAMES,
    LINEAGE_SCHEMA_VERSION,
    Attempt,
    Evaluation,
    EvaluationRecordedEvent,
    Outcome,
    OutcomeRecordedEvent,
    ReasonCode,
    Run,
    RunFinishedEvent,
    RunFinishStatus,
    RunStartedEvent,
    RunStatus,
    Step,
    StepStartedEvent,
    StepStatus,
    generate_evaluation_id,
    generate_event_id,
    generate_run_id,
    generate_step_id,
    parse_lineage_event,
    utc_now,
)
from bound.models import EvaluationScores

# A fixed, reproducible UTC instant used across serialization/determinism
# tests so ids and JSON are stable rather than wall-clock dependent.
T0 = datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC)
T1 = datetime(2025, 1, 2, 3, 4, 6, tzinfo=UTC)


def _scores() -> EvaluationScores:
    return EvaluationScores(acceptance=0.9, influence=0.1, risk=0.05, cost=0.1)


def _run_started() -> RunStartedEvent:
    return RunStartedEvent(
        event_id=generate_event_id(run_id="run_x", sequence=1),
        timestamp=T0,
        run_id="run_x",
        task="Implement CSV exporter",
    )


# ---------------------------------------------------------------------------
# Reason-code vocabulary
# ---------------------------------------------------------------------------


class TestReasonCodes:
    def test_decision_reasons_mirror_bound_decision(self) -> None:
        assert ReasonCode.ACCEPT.value == "ACCEPT"
        assert ReasonCode.RETRY.value == "RETRY"
        assert ReasonCode.REPLAN.value == "REPLAN"
        assert ReasonCode.ROLLBACK.value == "ROLLBACK"

    def test_full_vocabulary_is_fixed_and_complete(self) -> None:
        assert {r.value for r in ReasonCode} == {
            "ACCEPT",
            "RETRY",
            "REPLAN",
            "ROLLBACK",
            "ALL_CHECKS_PASSED",
            "REQUIRED_CHECKS_FAILED",
            "RISK_BOUNDARY_EXCEEDED",
            "BELOW_THRESHOLD",
            "WITHIN_RETRY_MARGIN",
            "CONTINUED",
            "RETRIED",
            "REPLANNED",
            "ROLLED_BACK",
            "RUN_STARTED",
            "RUN_COMPLETED",
            "RUN_INTERRUPTED",
            "RUN_FAILED",
        }

    def test_reason_code_is_str_serializable(self) -> None:
        assert ReasonCode.ACCEPT == "ACCEPT"



# ---------------------------------------------------------------------------
# Deterministic, reproducible identifiers
# ---------------------------------------------------------------------------


class TestDeterministicIds:
    def test_same_inputs_yield_same_id(self) -> None:
        a = generate_run_id(task="ship parser", started_at=T0)
        b = generate_run_id(task="ship parser", started_at=T0)
        assert a == b
        assert a.startswith("run_")

    def test_different_inputs_yield_different_id(self) -> None:
        assert generate_run_id(task="a", started_at=T0) != generate_run_id(
            task="b", started_at=T0
        )
        assert generate_run_id(task="a", started_at=T0) != generate_run_id(
            task="a", started_at=T1
        )

    def test_id_prefixes_are_distinct(self) -> None:
        run_id = generate_run_id(task="t", started_at=T0)
        step_id = generate_step_id(run_id=run_id, contract_id="PHASE-001", attempt=1)
        eval_id = generate_evaluation_id(run_id=run_id, step_id=step_id, attempt=1)
        evt_id = generate_event_id(run_id=run_id, sequence=1)
        assert run_id.startswith("run_")
        assert step_id.startswith("step_")
        assert eval_id.startswith("eval_")
        assert evt_id.startswith("evt_")

    def test_evaluation_salt_disambiguates(self) -> None:
        run_id = generate_run_id(task="t", started_at=T0)
        step_id = generate_step_id(run_id=run_id, contract_id="PHASE-001", attempt=1)
        a = generate_evaluation_id(run_id=run_id, step_id=step_id, attempt=1)
        b = generate_evaluation_id(
            run_id=run_id, step_id=step_id, attempt=1, salt="secondary"
        )
        assert a != b


# ---------------------------------------------------------------------------
# Timestamp handling
# ---------------------------------------------------------------------------


class TestTimestamps:
    def test_utc_now_is_aware_utc(self) -> None:
        now = utc_now()
        assert now.tzinfo is not None
        assert now.utcoffset() == timedelta(0)

    def test_naive_timestamp_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RunStartedEvent(
                event_id="evt_x", timestamp=datetime(2025, 1, 1), run_id="r", task="t"
            )

    def test_non_utc_offset_normalized_to_utc(self) -> None:
        aware = T0.astimezone(timezone(timedelta(hours=2)))
        ev = RunStartedEvent(event_id="evt_x", timestamp=aware, run_id="r", task="t")
        assert ev.timestamp == T0
        assert ev.timestamp.tzinfo is UTC




# ---------------------------------------------------------------------------
# Event serialization round-trip
# ---------------------------------------------------------------------------


class TestEventSerialization:
    def test_run_started_round_trip(self) -> None:
        ev = _run_started()
        parsed = RunStartedEvent.model_validate_json(ev.model_dump_json())
        assert parsed == ev
        assert parsed.event == "run_started"
        assert parsed.schema_version == LINEAGE_SCHEMA_VERSION

    def test_step_started_round_trip(self) -> None:
        ev = StepStartedEvent(
            event_id="e1",
            timestamp=T0,
            run_id="r",
            step_id="s1",
            contract_id="PHASE-001-R1",
            attempt=2,
            description="retry with dict writer",
        )
        parsed = StepStartedEvent.model_validate_json(ev.model_dump_json())
        assert parsed == ev
        assert parsed.attempt == 2

    def test_evaluation_recorded_round_trip(self) -> None:
        ev = EvaluationRecordedEvent(
            event_id="e1",
            timestamp=T0,
            evaluation_id="ev1",
            run_id="r",
            step_id="s1",
            attempt=1,
            scores=_scores(),
            score=0.85,
            threshold=0.7,
            decision="ACCEPT",
            reason_code=ReasonCode.ALL_CHECKS_PASSED,
        )
        parsed = EvaluationRecordedEvent.model_validate_json(ev.model_dump_json())
        assert parsed == ev
        assert parsed.scores.acceptance == 0.9

    def test_outcome_recorded_round_trip(self) -> None:
        ev = OutcomeRecordedEvent(
            event_id="e1",
            timestamp=T0,
            run_id="r",
            step_id="s1",
            evaluation_id="ev1",
            decision="REPLAN",
            next_action="replan",
            reason_code=ReasonCode.REPLANNED,
            note="switched to csv.DictWriter",
        )
        parsed = OutcomeRecordedEvent.model_validate_json(ev.model_dump_json())
        assert parsed == ev

    def test_run_finished_round_trip(self) -> None:
        ev = RunFinishedEvent(
            event_id="e1",
            timestamp=T1,
            run_id="r",
            status=RunFinishStatus.COMPLETED,
            reason_code=ReasonCode.RUN_COMPLETED,
        )
        parsed = RunFinishedEvent.model_validate_json(ev.model_dump_json())
        assert parsed == ev
        assert parsed.status == "completed"

    def test_timestamp_serializes_iso8601_with_offset(self) -> None:
        ev = _run_started()
        payload = ev.model_dump_json()
        assert "2025-01-02T03:04:05" in payload
        assert "Z" in payload or "+00:00" in payload




# ---------------------------------------------------------------------------
# Discriminated union parsing & multi-step/multi-attempt REPLAN -> ACCEPT
# ---------------------------------------------------------------------------


def _build_full_log() -> list[object]:
    """An 8-event log: REPLAN attempt 1 then ACCEPT attempt 2, run finished."""
    return [
        RunStartedEvent(event_id="e1", timestamp=T0, run_id="r", task="ship parser"),
        StepStartedEvent(
            event_id="e2",
            timestamp=T0,
            run_id="r",
            step_id="s1",
            contract_id="PHASE-001",
            attempt=1,
        ),
        EvaluationRecordedEvent(
            event_id="e3",
            timestamp=T0,
            evaluation_id="ev1",
            run_id="r",
            step_id="s1",
            attempt=1,
            scores=_scores(),
            score=0.4,
            threshold=0.7,
            decision="REPLAN",
            reason_code=ReasonCode.BELOW_THRESHOLD,
        ),
        OutcomeRecordedEvent(
            event_id="e4",
            timestamp=T0,
            run_id="r",
            step_id="s1",
            evaluation_id="ev1",
            decision="REPLAN",
            next_action="replan",
            reason_code=ReasonCode.REPLANNED,
        ),
        StepStartedEvent(
            event_id="e5",
            timestamp=T1,
            run_id="r",
            step_id="s2",
            contract_id="PHASE-001-R1",
            attempt=2,
        ),
        EvaluationRecordedEvent(
            event_id="e6",
            timestamp=T1,
            evaluation_id="ev2",
            run_id="r",
            step_id="s2",
            attempt=2,
            scores=_scores(),
            score=0.9,
            threshold=0.7,
            decision="ACCEPT",
            reason_code=ReasonCode.ALL_CHECKS_PASSED,
        ),
        OutcomeRecordedEvent(
            event_id="e7",
            timestamp=T1,
            run_id="r",
            step_id="s2",
            evaluation_id="ev2",
            decision="ACCEPT",
            next_action="continue",
            reason_code=ReasonCode.CONTINUED,
        ),
        RunFinishedEvent(
            event_id="e8",
            timestamp=T1,
            run_id="r",
            status=RunFinishStatus.COMPLETED,
            reason_code=ReasonCode.RUN_COMPLETED,
        ),
    ]


class TestLineageEventParsing:
    def test_each_event_routes_to_correct_type(self) -> None:
        types = [
            RunStartedEvent,
            StepStartedEvent,
            EvaluationRecordedEvent,
            OutcomeRecordedEvent,
            StepStartedEvent,
            EvaluationRecordedEvent,
            OutcomeRecordedEvent,
            RunFinishedEvent,
        ]
        for ev, expected in zip(_build_full_log(), types, strict=True):
            parsed = parse_lineage_event(ev.model_dump_json())
            assert isinstance(parsed, expected)
            assert parsed == ev

    def test_parse_accepts_dict(self) -> None:
        ev = _run_started()
        assert parse_lineage_event(ev.model_dump()) == ev

    def test_jsonl_lines_round_trip(self) -> None:
        log = _build_full_log()
        text = "\n".join(ev.model_dump_json() for ev in log)
        lines = [ln for ln in text.splitlines() if ln]
        assert len(lines) == 8
        assert [parse_lineage_event(ln) for ln in lines] == log

    def test_replan_then_accept_trajectory_preserved(self) -> None:
        log = _build_full_log()
        evals = [e for e in log if isinstance(e, EvaluationRecordedEvent)]
        outcomes = [e for e in log if isinstance(e, OutcomeRecordedEvent)]
        # Attempt 1 -> REPLAN, attempt 2 -> ACCEPT; both recorded, neither overwritten.
        assert evals[0].decision == "REPLAN"
        assert evals[1].decision == "ACCEPT"
        assert outcomes[0].next_action == "replan"
        assert outcomes[1].next_action == "continue"
        assert evals[0].attempt == 1 and evals[1].attempt == 2




# ---------------------------------------------------------------------------
# Schema validation rejects bad events
# ---------------------------------------------------------------------------


class TestSchemaValidation:
    def test_unknown_event_tag_rejected(self) -> None:
        bad = {
            "event": "step_finished",
            "event_id": "e1",
            "timestamp": T0.isoformat(),
            "run_id": "r",
        }
        with pytest.raises(ValidationError):
            parse_lineage_event(bad)

    def test_extra_field_rejected(self) -> None:
        payload = _run_started().model_dump()
        payload["surprise"] = "nope"
        with pytest.raises(ValidationError):
            parse_lineage_event(payload)

    def test_bad_decision_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EvaluationRecordedEvent(
                event_id="e1",
                timestamp=T0,
                evaluation_id="ev1",
                run_id="r",
                step_id="s1",
                attempt=1,
                scores=_scores(),
                score=0.9,
                threshold=0.7,
                decision="PANIC",
                reason_code=ReasonCode.ACCEPT,
            )

    def test_bad_next_action_rejected(self) -> None:
        with pytest.raises(ValidationError):
            OutcomeRecordedEvent(
                event_id="e1",
                timestamp=T0,
                run_id="r",
                step_id="s1",
                evaluation_id="ev1",
                decision="ACCEPT",
                next_action="abort",
                reason_code=ReasonCode.CONTINUED,
            )

    def test_bad_reason_code_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EvaluationRecordedEvent(
                event_id="e1",
                timestamp=T0,
                evaluation_id="ev1",
                run_id="r",
                step_id="s1",
                attempt=1,
                scores=_scores(),
                score=0.9,
                threshold=0.7,
                decision="ACCEPT",
                reason_code="WHATEVER",
            )

    def test_missing_required_field_rejected(self) -> None:
        payload = _run_started().model_dump()
        del payload["run_id"]
        with pytest.raises(ValidationError):
            parse_lineage_event(payload)

    def test_attempt_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            StepStartedEvent(
                event_id="e1",
                timestamp=T0,
                run_id="r",
                step_id="s1",
                contract_id="PHASE-001",
                attempt=0,
            )

    def test_bad_run_finish_status_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RunFinishedEvent(
                event_id="e1",
                timestamp=T1,
                run_id="r",
                status="running",
                reason_code=ReasonCode.RUN_COMPLETED,
            )


# ---------------------------------------------------------------------------
# Entity snapshots
# ---------------------------------------------------------------------------


class TestEntitySnapshots:
    def test_run_and_step_snapshots_build(self) -> None:
        run_id = generate_run_id(task="ship parser", started_at=T0)
        step_id = generate_step_id(run_id=run_id, contract_id="PHASE-001", attempt=1)
        run = Run(run_id=run_id, task="ship parser", started_at=T0)
        step = Step(
            step_id=step_id,
            run_id=run_id,
            contract_id="PHASE-001",
            started_at=T0,
            attempts=[Attempt(attempt=1, started_at=T0, evaluation_id="ev1")],
        )
        assert run.status == RunStatus.STARTED
        assert step.status == StepStatus.STARTED
        assert step.attempts[0].evaluation_id == "ev1"
        assert Run.model_validate_json(run.model_dump_json()) == run
        assert Step.model_validate_json(step.model_dump_json()) == step

    def test_evaluation_and_outcome_snapshots_build(self) -> None:
        ev = Evaluation(
            evaluation_id="ev1",
            run_id="r",
            step_id="s1",
            attempt=1,
            scores=_scores(),
            score=0.9,
            threshold=0.7,
            decision="ACCEPT",
            reason_code=ReasonCode.ALL_CHECKS_PASSED,
            recorded_at=T0,
        )
        out = Outcome(
            run_id="r",
            step_id="s1",
            evaluation_id="ev1",
            decision="ACCEPT",
            next_action="continue",
            reason_code=ReasonCode.CONTINUED,
            recorded_at=T0,
        )
        assert Evaluation.model_validate_json(ev.model_dump_json()) == ev
        assert Outcome.model_validate_json(out.model_dump_json()) == out

    def test_entity_extra_field_rejected(self) -> None:
        payload = Run(run_id="r", task="t", started_at=T0).model_dump()
        payload["extra"] = "x"
        with pytest.raises(ValidationError):
            Run.model_validate(payload)


class TestEventNames:
    def test_event_names_are_the_append_only_vocabulary(self) -> None:
        assert EVENT_NAMES == (
            "run_started",
            "policy.proposed",
            "policy.validated",
            "policy.approved",
            "policy.activated",
            "step_started",
            "evidence.collected",
            "evidence.collection_failed",
            "evaluation_recorded",
            "evaluation.completed",
            "decision.gated",
            "action.reported",
            "action.observed",
            "step.completed",
            "outcome_recorded",
            "run_finished",
        )

        assert str(ReasonCode.RUN_COMPLETED) == "RUN_COMPLETED"


# ---------------------------------------------------------------------------
# Schema 2.0 — TraceEvent append-only, sequence, parent-event-id (item 10)
# ---------------------------------------------------------------------------


class TestSchema20Events:
    """Tests for the four new schema-2.0 event types and the ordering fields."""

    def test_run_started_carries_config(self) -> None:
        from bound.lineage import build_run_config

        cfg = build_run_config(
            bound_version="0.7.0",
            policy_id="default",
            threshold=0.6,
        )
        evt = RunStartedEvent(
            event_id=generate_event_id(run_id="r", sequence=1),
            timestamp=T0,
            run_id="r",
            task="ship parser",
            config=cfg,
        )
        assert evt.config is not None
        assert evt.config.bound_version == "0.7.0"
        assert evt.config.threshold == 0.6
        restored = parse_lineage_event(evt.model_dump_json())
        assert restored.config is not None  # type: ignore[union-attr]
        assert restored.config.bound_version == "0.7.0"  # type: ignore[union-attr]

    def test_run_started_config_is_optional(self) -> None:
        evt = RunStartedEvent(
            event_id=generate_event_id(run_id="r", sequence=1),
            timestamp=T0,
            run_id="r",
            task="ship parser",
        )
        assert evt.config is None
        restored = parse_lineage_event(evt.model_dump_json())
        assert restored.config is None  # type: ignore[union-attr]

    def test_sequence_and_parent_event_id_optional(self) -> None:
        evt = RunStartedEvent(
            event_id="evt_legacy",
            timestamp=T0,
            run_id="r",
            task="legacy run",
        )
        assert evt.sequence is None
        assert evt.parent_event_id is None

    def test_sequence_and_parent_event_id_set(self) -> None:
        evt = RunStartedEvent(
            event_id=generate_event_id(run_id="r", sequence=1),
            timestamp=T0,
            run_id="r",
            task="run",
            sequence=1,
        )
        assert evt.sequence == 1
        step = StepStartedEvent(
            event_id=generate_event_id(run_id="r", sequence=2),
            timestamp=T1,
            run_id="r",
            step_id="s1",
            contract_id="PHASE-001",
            attempt=1,
            sequence=2,
            parent_event_id=evt.event_id,
        )
        assert step.parent_event_id == evt.event_id
        assert step.sequence == 2

    def test_evidence_collected_event(self) -> None:
        from bound.evidence import EvidenceProvenance

        evt = parse_lineage_event(
            {
                "event": "evidence.collected",
                "event_id": "evt_1",
                "timestamp": T0.isoformat(),
                "run_id": "r",
                "step_id": "s1",
                "check_id": "tests-pass",
                "collector": "bound.pytest",
                "collector_version": "0.7.0",
                "provenance": "verified",
                "passed": True,
                "artifact_hash": "sha256:abc123",
                "source": "uv run pytest -q",
                "observed_at": T0.isoformat(),
            }
        )
        assert evt.event == "evidence.collected"  # type: ignore[union-attr]
        assert evt.provenance == EvidenceProvenance.VERIFIED  # type: ignore[union-attr]
        assert evt.passed is True  # type: ignore[union-attr]

    def test_evidence_collection_failed_event(self) -> None:
        evt = parse_lineage_event(
            {
                "event": "evidence.collection_failed",
                "event_id": "evt_1",
                "timestamp": T0.isoformat(),
                "run_id": "r",
                "step_id": "s1",
                "check_id": "tests-pass",
                "collector": "bound.pytest",
                "error": "timeout after 30s",
                "observed_at": T0.isoformat(),
            }
        )
        assert evt.event == "evidence.collection_failed"  # type: ignore[union-attr]
        assert evt.error == "timeout after 30s"  # type: ignore[union-attr]

    def test_decision_gated_event(self) -> None:
        from bound.models import DecisionAssurance

        evt = parse_lineage_event(
            {
                "event": "decision.gated",
                "event_id": "evt_1",
                "timestamp": T0.isoformat(),
                "run_id": "r",
                "step_id": "s1",
                "evaluation_id": "ev1",
                "candidate_decision": "ACCEPT",
                "final_decision": "REPLAN",
                "assurance": "insufficient",
                "assurance_reasons": ["tests-pass: MISSING evidence"],
            }
        )
        assert evt.event == "decision.gated"  # type: ignore[union-attr]
        assert evt.candidate_decision == "ACCEPT"  # type: ignore[union-attr]
        assert evt.final_decision == "REPLAN"  # type: ignore[union-attr]
        assert evt.assurance == DecisionAssurance.INSUFFICIENT  # type: ignore[union-attr]

    def test_action_reported_defaults_to_claimed(self) -> None:
        from bound.evidence import EvidenceProvenance

        evt = parse_lineage_event(
            {
                "event": "action.reported",
                "event_id": "evt_1",
                "timestamp": T0.isoformat(),
                "run_id": "r",
                "step_id": "s1",
                "evaluation_id": "ev1",
                "intended_action": "replan",
                "reported_action": "Switched to csv.DictWriter",
            }
        )
        assert evt.event == "action.reported"  # type: ignore[union-attr]
        assert evt.reported_provenance == EvidenceProvenance.CLAIMED  # type: ignore[union-attr]
        assert evt.observed_action is None  # type: ignore[union-attr]

    def test_action_reported_with_observed_confirmation(self) -> None:
        from bound.evidence import EvidenceProvenance

        evt = parse_lineage_event(
            {
                "event": "action.reported",
                "event_id": "evt_1",
                "timestamp": T0.isoformat(),
                "run_id": "r",
                "step_id": "s1",
                "evaluation_id": "ev1",
                "intended_action": "rollback",
                "reported_action": "Reverted to HEAD~1",
                "observed_action": "git reset confirmed",
                "observed_provenance": "verified",
            }
        )
        assert evt.observed_provenance == EvidenceProvenance.VERIFIED  # type: ignore[union-attr]

    def test_action_reported_replan_records_new_contract_id(self) -> None:
        evt = parse_lineage_event(
            {
                "event": "action.reported",
                "event_id": "evt_1",
                "timestamp": T0.isoformat(),
                "run_id": "r",
                "step_id": "s1",
                "evaluation_id": "ev1",
                "intended_action": "replan",
                "reported_action": "Replanned",
                "new_contract_id": "PHASE-001-R1",
            }
        )
        assert evt.new_contract_id == "PHASE-001-R1"  # type: ignore[union-attr]

    def test_rollback_without_proof_stays_claimed(self) -> None:
        evt = parse_lineage_event(
            {
                "event": "action.reported",
                "event_id": "evt_1",
                "timestamp": T0.isoformat(),
                "run_id": "r",
                "step_id": "s1",
                "evaluation_id": "ev1",
                "intended_action": "rollback",
                "reported_action": "Rolled back",
            }
        )
        assert evt.observed_action is None  # type: ignore[union-attr]
        assert evt.observed_provenance is None  # type: ignore[union-attr]

    def test_schema_1_0_trace_still_parses(self) -> None:
        legacy = {
            "event": "run_started",
            "schema_version": "1.0",
            "event_id": "evt_old",
            "timestamp": T0.isoformat(),
            "run_id": "r_old",
            "task": "legacy task",
            "metadata": {},
        }
        evt = parse_lineage_event(legacy)
        assert evt.event == "run_started"  # type: ignore[union-attr]
        assert evt.schema_version == "1.0"  # type: ignore[union-attr]
        assert evt.sequence is None  # type: ignore[union-attr]
        assert evt.config is None  # type: ignore[union-attr]


class TestPolicyLifecycleAndNewEvents:
    """Tests for the todo 7.1 policy-lifecycle + new event types."""

    def test_policy_proposed_round_trip(self) -> None:
        evt = parse_lineage_event(
            {
                "event": "policy.proposed",
                "event_id": "evt_pp",
                "timestamp": T0.isoformat(),
                "run_id": "r",
                "policy_id": "coding-default",
                "policy_version": "1.0",
                "policy_hash": "sha256:abcd",
            }
        )
        assert evt.event == "policy.proposed"  # type: ignore[union-attr]
        assert evt.policy_id == "coding-default"  # type: ignore[union-attr]
        assert evt.policy_version == "1.0"  # type: ignore[union-attr]
        assert evt.policy_hash == "sha256:abcd"  # type: ignore[union-attr]
        restored = parse_lineage_event(evt.model_dump_json())  # type: ignore[union-attr]
        assert restored.event == "policy.proposed"  # type: ignore[union-attr]

    def test_policy_validated_round_trip(self) -> None:
        evt = parse_lineage_event(
            {
                "event": "policy.validated",
                "event_id": "evt_pv",
                "timestamp": T0.isoformat(),
                "run_id": "r",
                "policy_id": "coding-default",
                "policy_version": "1.0",
                "policy_hash": "sha256:abcd",
            }
        )
        assert evt.event == "policy.validated"  # type: ignore[union-attr]

    def test_policy_approved_records_approver_and_time(self) -> None:
        evt = parse_lineage_event(
            {
                "event": "policy.approved",
                "event_id": "evt_pa",
                "timestamp": T0.isoformat(),
                "run_id": "r",
                "policy_id": "coding-default",
                "policy_version": "1.0",
                "policy_hash": "sha256:abcd",
                "approver": "alice@example.com",
                "approved_at": T0.isoformat(),
            }
        )
        assert evt.event == "policy.approved"  # type: ignore[union-attr]
        assert evt.approver == "alice@example.com"  # type: ignore[union-attr]
        assert evt.approved_at == T0  # type: ignore[union-attr]

    def test_policy_approved_requires_approver(self) -> None:
        with pytest.raises(ValidationError):
            parse_lineage_event(
                {
                    "event": "policy.approved",
                    "event_id": "evt_pa2",
                    "timestamp": T0.isoformat(),
                    "run_id": "r",
                    "policy_id": "p",
                    "policy_version": "1.0",
                    "policy_hash": "sha256:x",
                    "approved_at": T0.isoformat(),
                }
            )

    def test_policy_activated_round_trip(self) -> None:
        evt = parse_lineage_event(
            {
                "event": "policy.activated",
                "event_id": "evt_pac",
                "timestamp": T0.isoformat(),
                "run_id": "r",
                "policy_id": "coding-default",
                "policy_version": "1.0",
                "policy_hash": "sha256:abcd",
            }
        )
        assert evt.event == "policy.activated"  # type: ignore[union-attr]

    def test_step_completed_round_trip(self) -> None:
        evt = parse_lineage_event(
            {
                "event": "step.completed",
                "event_id": "evt_sc",
                "timestamp": T0.isoformat(),
                "run_id": "r",
                "step_id": "s1",
                "outcome": "ACCEPTED",
            }
        )
        assert evt.event == "step.completed"  # type: ignore[union-attr]
        assert evt.outcome == "ACCEPTED"  # type: ignore[union-attr]

    def test_new_events_carry_sequence_and_parent(self) -> None:
        evt = parse_lineage_event(
            {
                "event": "policy.proposed",
                "event_id": "evt_seq",
                "sequence": 3,
                "parent_event_id": "evt_1",
                "timestamp": T0.isoformat(),
                "run_id": "r",
                "policy_id": "p",
                "policy_version": "1.0",
                "policy_hash": "sha256:x",
            }
        )
        assert evt.sequence == 3  # type: ignore[union-attr]
        assert evt.parent_event_id == "evt_1"  # type: ignore[union-attr]

    def test_unknown_event_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_lineage_event(
                {
                    "event": "policy.unknown",
                    "event_id": "evt_bad",
                    "timestamp": T0.isoformat(),
                    "run_id": "r",
                }
            )


    def test_evaluation_completed_round_trip(self) -> None:
        evt = parse_lineage_event(
            {
                "event": "evaluation.completed",
                "event_id": "evt_ec",
                "timestamp": T0.isoformat(),
                "run_id": "r",
                "step_id": "s1",
                "evaluation_id": "ev1",
                "policy_id": "coding-default",
                "policy_version": "1.0",
                "policy_hash": "sha256:abcd",
                "candidate_decision": "REPLAN",
                "final_decision": "REPLAN",
                "assurance": "verified",
            }
        )
        assert evt.event == "evaluation.completed"  # type: ignore[union-attr]
        assert evt.candidate_decision == "REPLAN"  # type: ignore[union-attr]
        assert evt.final_decision == "REPLAN"  # type: ignore[union-attr]
        assert evt.assurance == "verified"  # type: ignore[union-attr]

    def test_action_observed_round_trip(self) -> None:
        evt = parse_lineage_event(
            {
                "event": "action.observed",
                "event_id": "evt_ao",
                "timestamp": T0.isoformat(),
                "run_id": "r",
                "step_id": "s1",
                "evaluation_id": "ev1",
                "intended_action": "rollback",
                "observed_action": "Files restored to HEAD",
                "observed_provenance": "observed",
                "reported_action": "Rolled back",
                "matches_reported": True,
            }
        )
        assert evt.event == "action.observed"  # type: ignore[union-attr]
        assert evt.observed_action == "Files restored to HEAD"  # type: ignore[union-attr]
        assert evt.observed_provenance.value == "observed"  # type: ignore[union-attr]
        assert evt.matches_reported is True  # type: ignore[union-attr]

    def test_action_observed_mismatch_recorded(self) -> None:
        """Todo 7.3: action mismatches (reported vs observed) are recorded."""
        evt = parse_lineage_event(
            {
                "event": "action.observed",
                "event_id": "evt_ao2",
                "timestamp": T0.isoformat(),
                "run_id": "r",
                "step_id": "s1",
                "evaluation_id": "ev1",
                "intended_action": "rollback",
                "observed_action": "No files changed",
                "observed_provenance": "observed",
                "reported_action": "Rolled back successfully",
                "matches_reported": False,
            }
        )
        assert evt.matches_reported is False  # type: ignore[union-attr]

    def test_evaluation_recorded_with_policy_fields(self) -> None:
        """Phase 7.2: evaluation_recorded carries policy id/version/hash."""
        evt = parse_lineage_event(
            {
                "event": "evaluation_recorded",
                "event_id": "evt_er",
                "timestamp": T0.isoformat(),
                "evaluation_id": "ev1",
                "run_id": "r",
                "step_id": "s1",
                "attempt": 1,
                "scores": {"acceptance": 1.0, "influence": 0.0, "risk": 0.0, "cost": 0.0},
                "score": 1.0,
                "threshold": 0.6,
                "decision": "ACCEPT",
                "reason_code": "ACCEPT",
                "policy_id": "coding-default",
                "policy_version": "1.0",
                "policy_hash": "sha256:abcd",
                "assurance": "verified",
                "collector_versions": {"bound.pytest": "0.7.0"},
            }
        )
        assert evt.policy_id == "coding-default"  # type: ignore[union-attr]
        assert evt.policy_hash == "sha256:abcd"  # type: ignore[union-attr]
        assert evt.assurance == "verified"  # type: ignore[union-attr]
        assert evt.collector_versions == {"bound.pytest": "0.7.0"}  # type: ignore[union-attr]

    def test_evaluation_recorded_policy_fields_optional(self) -> None:
        """Schema-1.0 style evaluation (no policy fields) still parses."""
        evt = parse_lineage_event(
            {
                "event": "evaluation_recorded",
                "event_id": "evt_old_eval",
                "timestamp": T0.isoformat(),
                "evaluation_id": "ev1",
                "run_id": "r",
                "step_id": "s1",
                "attempt": 1,
                "scores": {"acceptance": 1.0, "influence": 0.0, "risk": 0.0, "cost": 0.0},
                "score": 1.0,
                "threshold": 0.6,
                "decision": "ACCEPT",
                "reason_code": "ACCEPT",
            }
        )
        assert evt.policy_id is None  # type: ignore[union-attr]
        assert evt.policy_hash is None  # type: ignore[union-attr]
        assert evt.assurance is None  # type: ignore[union-attr]


class TestRunConfigSnapshot:
    def test_build_run_config_computes_hashes(self) -> None:
        from bound.contracts import AcceptanceCheck, StepContract
        from bound.lineage import build_run_config
        from bound.models import BoundWeights

        contract = StepContract(
            id="PHASE-001",
            description="test",
            goal="test goal",
            acceptance_checks=[AcceptanceCheck(id="t", description="t")],
        )
        weights = BoundWeights(acceptance=1.5, influence=0.5)
        cfg = build_run_config(
            bound_version="0.7.0",
            policy_id="default",
            policy_config_version="v1",
            policy_config={"threshold": 0.6},
            weights=weights,
            threshold=0.6,
            contract=contract,
            collector_versions={"bound.pytest": "0.7.0"},
        )
        assert cfg.bound_version == "0.7.0"
        assert cfg.policy_config_hash is not None
        assert len(cfg.policy_config_hash) == 64
        assert cfg.contract_hash is not None
        assert len(cfg.contract_hash) == 64
        assert cfg.weights == {"acceptance": 1.5, "influence": 0.5, "risk": 1.0, "cost": 1.0}

    def test_contract_hash_is_deterministic(self) -> None:
        from bound.lineage import compute_contract_hash

        h1 = compute_contract_hash({"id": "PHASE-001", "desc": "test"})
        h2 = compute_contract_hash({"id": "PHASE-001", "desc": "test"})
        assert h1 == h2
        h3 = compute_contract_hash({"desc": "test", "id": "PHASE-001"})
        assert h1 == h3

    def test_policy_config_hash_differs_for_different_configs(self) -> None:
        from bound.lineage import compute_policy_config_hash

        assert compute_policy_config_hash({"threshold": 0.6}) != compute_policy_config_hash(
            {"threshold": 0.7}
        )

    def test_build_run_config_from_bound_policy(self) -> None:
        """build_run_config derives policy_id/version/hash from a BoundPolicyConfig."""
        from bound.lineage import build_run_config
        from bound.policy_schema import parse_policy_yaml

        policy = parse_policy_yaml(
            "schema_version: '1.0'\n"
            "policy:\n  id: test-policy\n  version: '2.0'\n"
        )
        cfg = build_run_config(bound_version="0.7.0", policy=policy, threshold=0.6)
        assert cfg.policy_id == "test-policy"
        assert cfg.policy_version == "2.0"
        assert cfg.policy_hash is not None
        assert cfg.policy_hash.startswith("sha256:")

    def test_build_run_config_policy_hash_matches_compute_policy_hash(self) -> None:
        """The config's policy_hash equals compute_policy_hash of the same policy."""
        from bound.lineage import build_run_config
        from bound.policy_canon import compute_policy_hash
        from bound.policy_schema import parse_policy_yaml

        policy = parse_policy_yaml(
            "schema_version: '1.0'\n"
            "policy:\n  id: test-policy\n  version: '2.0'\n"
        )
        cfg = build_run_config(policy=policy, threshold=0.6)
        assert cfg.policy_hash == compute_policy_hash(policy)
