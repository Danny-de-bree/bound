from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from bound.lineage import (
    ReasonCode,
    RunFinishStatus,
    RunStatus,
    StepStatus,
    utc_now,
)
from bound.lineage_store import (
    LineageCorruptEvent,
    LineageEventTooLarge,
    LineageFileTooLarge,
    LineageStore,
    RunNotFound,
    get_default_store,
)
from bound.models import EvaluationScores


def _store(tmp_path) -> LineageStore:
    return LineageStore(base_dir=tmp_path / "runs")


def _scores() -> EvaluationScores:
    return EvaluationScores(acceptance=0.9, influence=0.1, risk=0.05, cost=0.1)


def _full_run(store: LineageStore, *, task: str = "Implement CSV exporter"):
    started = utc_now()
    run_evt = store.start_run(task, started_at=started)
    run_id = run_evt.run_id
    # Step 1, attempt 1 -> REPLAN (1/3 checks)
    s1a1 = store.start_step(run_id, contract_id="PHASE-001", attempt=1, started_at=started)
    ev1 = store.record_evaluation(
        run_id,
        step_id=s1a1.step_id,
        attempt=1,
        scores=_scores(),
        score=0.4,
        threshold=0.7,
        decision="REPLAN",
        reason_code=ReasonCode.BELOW_THRESHOLD,
    )
    store.record_outcome(
        run_id,
        step_id=s1a1.step_id,
        evaluation_id=ev1.evaluation_id,
        decision="REPLAN",
        next_action="replan",
        reason_code=ReasonCode.REPLANNED,
        note="switched to csv.DictWriter",
    )
    # Step 1, attempt 2 -> ACCEPT (3/3 checks)
    s1a2 = store.start_step(
        run_id, contract_id="PHASE-001-R1", attempt=1, started_at=started
    )
    ev2 = store.record_evaluation(
        run_id,
        step_id=s1a2.step_id,
        attempt=1,
        scores=_scores(),
        score=0.95,
        threshold=0.7,
        decision="ACCEPT",
        reason_code=ReasonCode.ALL_CHECKS_PASSED,
    )
    store.record_outcome(
        run_id,
        step_id=s1a2.step_id,
        evaluation_id=ev2.evaluation_id,
        decision="ACCEPT",
        next_action="continue",
        reason_code=ReasonCode.CONTINUED,
        note="continued to next step",
    )
    store.finish_run(run_id, status=RunFinishStatus.COMPLETED, reason_code=ReasonCode.RUN_COMPLETED)
    return run_id


class TestAtomicAndCrashRecovery:
    def test_truncated_final_line_keeps_run_readable(self, tmp_path) -> None:
        store = _store(tmp_path)
        run_id = _full_run(store)
        events_path = store._events_path(run_id)
        # Simulate a crash mid-write: append a partial line with no newline.
        with events_path.open("ab") as fh:
            fh.write(b'{"event": "run_started", "partial')
        log = store.read_run(run_id)
        assert log.truncated is True
        assert log.incomplete is True
        # The valid events before the crash are all still present.
        assert len(log.events) == 8
        assert log.run.status == RunStatus.COMPLETED

    def test_corrupt_line_in_middle_skipped_in_lenient_mode(self, tmp_path) -> None:
        store = _store(tmp_path)
        run_id = _full_run(store)
        events_path = store._events_path(run_id)
        lines = events_path.read_text().splitlines()
        # Inject a corrupt line between the first and second valid events.
        lines.insert(1, "{not valid json")
        events_path.write_text("\n".join(lines) + "\n")
        log = store.read_run(run_id, strict=False)
        assert log.corrupt_lines == 1
        assert log.incomplete is True

    def test_strict_mode_raises_on_corrupt_line(self, tmp_path) -> None:
        store = _store(tmp_path)
        run_id = _full_run(store)
        events_path = store._events_path(run_id)
        lines = events_path.read_text().splitlines()
        lines.insert(1, "{not valid json")
        events_path.write_text("\n".join(lines) + "\n")
        with pytest.raises(LineageCorruptEvent):
            store.read_run(run_id, strict=True)

    def test_run_json_is_atomic_temp_replaced(self, tmp_path) -> None:
        store = _store(tmp_path)
        run_id = _full_run(store)
        meta_path = store._meta_path(run_id)
        assert meta_path.exists()
        # No leftover temp file after the atomic replace.
        assert not meta_path.with_name(meta_path.name + ".tmp").exists()
        meta = json.loads(meta_path.read_text())
        assert meta["run_id"] == run_id
        assert meta["status"] == "completed"


class TestReplay:
    def test_multi_step_multi_attempt_replay(self, tmp_path) -> None:
        store = _store(tmp_path)
        run_id = _full_run(store)
        log = store.read_run(run_id)
        assert log.run.run_id == run_id
        assert log.run.status == RunStatus.COMPLETED
        assert len(log.steps) == 2
        # First step ended with REPLAN, second with ACCEPT.
        assert log.steps[0].status == StepStatus.REPLANNED
        assert log.steps[1].status == StepStatus.COMPLETED
        assert len(log.evaluations) == 2
        assert len(log.outcomes) == 2
        # Each step records exactly one attempt whose evaluation_id is linked.
        for step in log.steps:
            assert len(step.attempts) == 1
            assert step.attempts[0].evaluation_id is not None

    def test_incomplete_run_without_run_finished(self, tmp_path) -> None:
        store = _store(tmp_path)
        run_evt = store.start_run("unfinished task")
        store.start_step(run_evt.run_id, contract_id="PHASE-001", attempt=1)
        log = store.read_run(run_evt.run_id)
        assert log.incomplete is True
        assert log.run.status == RunStatus.STARTED
        assert log.run.finished_at is None


class TestPrivacy:
    def test_default_secret_scrubber_masks_metadata(self, tmp_path) -> None:
        store = _store(tmp_path)
        run_evt = store.start_run(
            "task", metadata={"auth": "password=hunter2"}
        )
        events_path = store._events_path(run_evt.run_id)
        raw = events_path.read_text()
        assert "hunter2" not in raw
        assert "***REDACTED***" in raw

    def test_custom_redactor_drops_field(self, tmp_path) -> None:
        def drop_note(event_dict: dict) -> None:
            event_dict.pop("note", None)

        store = LineageStore(base_dir=tmp_path / "runs", redactors=[drop_note])
        run_id = _full_run(store)
        raw = store._events_path(run_id).read_text()
        assert "switched to csv.DictWriter" not in raw

    def test_stored_fields_allowlist_drops_optional(self, tmp_path) -> None:
        store = LineageStore(
            base_dir=tmp_path / "runs",
            stored_fields={
                "run_started": {"task"},
                "step_started": {"contract_id", "attempt"},
                "evaluation_recorded": {
                    "scores", "score", "threshold", "decision", "reason_code"
                },
                "outcome_recorded": {
                    "evaluation_id", "decision", "next_action", "reason_code"
                },
                "run_finished": {"status", "reason_code"},
            },
        )
        run_id = _full_run(store)
        raw = store._events_path(run_id).read_text()
        # description / note are optional and not in the allowlist -> dropped.
        assert "switched to csv.DictWriter" not in raw
        # read_run still reconstructs the run from the allowlisted fields.
        log = store.read_run(run_id)
        assert log.run.run_id == run_id


class TestSizeLimits:
    def test_max_event_bytes_exceeded(self, tmp_path) -> None:
        store = LineageStore(base_dir=tmp_path / "runs", max_event_bytes=64)
        with pytest.raises(LineageEventTooLarge):
            store.start_run("task", metadata={"k": "v" * 200})

    def test_max_file_bytes_exceeded(self, tmp_path) -> None:
        store = LineageStore(base_dir=tmp_path / "runs", max_file_bytes=10 * 1024 * 1024)
        run_evt = store.start_run("task")
        # Shrink the cap to exactly the current file size so the next append
        # (any non-empty event) must overflow it.
        store.max_file_bytes = store._events_path(run_evt.run_id).stat().st_size
        with pytest.raises(LineageFileTooLarge):
            store.start_step(run_evt.run_id, contract_id="PHASE-001", attempt=1)


class TestListingAndDelete:
    def test_list_runs_newest_first(self, tmp_path) -> None:
        store = _store(tmp_path)
        t0 = datetime(2025, 1, 1, tzinfo=UTC)
        t1 = datetime(2025, 6, 1, tzinfo=UTC)
        older = store.start_run("older", started_at=t0).run_id
        newer = store.start_run("newer", started_at=t1).run_id
        store.finish_run(older, finished_at=t0)
        store.finish_run(newer, finished_at=t1)
        summaries = store.list_runs()
        assert [s.run_id for s in summaries] == [newer, older]
        assert summaries[0].task == "newer"

    def test_delete_run_removes_directory(self, tmp_path) -> None:
        store = _store(tmp_path)
        run_id = _full_run(store)
        run_dir = store._run_dir(run_id)
        assert run_dir.exists()
        store.delete_run(run_id)
        assert not run_dir.exists()
        with pytest.raises(RunNotFound):
            store.read_run(run_id)
        with pytest.raises(RunNotFound):
            store.delete_run(run_id)


class TestDisabled:
    def test_disabled_store_writes_nothing(self, tmp_path) -> None:
        store = LineageStore(base_dir=tmp_path / "runs", enabled=False)
        evt = store.start_run("task")
        assert evt.run_id  # event still constructed
        assert not (tmp_path / "runs").exists()  # nothing persisted

    def test_env_var_disables_default_store(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("BOUND_LINEAGE_DISABLED", "1")
        monkeypatch.chdir(tmp_path)
        import bound.lineage_store as ls

        monkeypatch.setattr(ls, "_default_store", None)
        store = get_default_store()
        assert store.enabled is False


class TestSchema20Builders:
    """Tests for the new schema-2.0 store builder methods."""

    def test_start_run_with_config(self, tmp_path) -> None:
        from bound.lineage import build_run_config

        store = _store(tmp_path)
        cfg = build_run_config(bound_version="0.7.0", policy_id="default", threshold=0.6)
        evt = store.start_run("task", config=cfg)
        log = store.read_run(evt.run_id)
        assert log.run.config is not None
        assert log.run.config.bound_version == "0.7.0"

    def test_events_carry_sequence(self, tmp_path) -> None:
        store = _store(tmp_path)
        run_evt = store.start_run("task")
        store.start_step(run_evt.run_id, contract_id="PHASE-001", attempt=1)
        log = store.read_run(run_evt.run_id)
        assert log.events[0].sequence == 1  # type: ignore[union-attr]
        assert log.events[1].sequence == 2  # type: ignore[union-attr]

    def test_record_evidence_collected(self, tmp_path) -> None:
        store = _store(tmp_path)
        run_evt = store.start_run("task")
        s = store.start_step(run_evt.run_id, contract_id="PHASE-001", attempt=1)
        evt = store.record_evidence_collected(
            run_evt.run_id,
            step_id=s.step_id,
            check_id="tests-pass",
            collector="bound.pytest",
            provenance="verified",
            passed=True,
        )
        assert evt.event == "evidence.collected"
        assert evt.provenance.value == "verified"
        log = store.read_run(run_evt.run_id)
        assert any(e.event == "evidence.collected" for e in log.events)

    def test_record_evidence_collection_failed(self, tmp_path) -> None:
        store = _store(tmp_path)
        run_evt = store.start_run("task")
        evt = store.record_evidence_collection_failed(
            run_evt.run_id, step_id="s1", error="crashed", collector="bound.pytest"
        )
        assert evt.event == "evidence.collection_failed"
        log = store.read_run(run_evt.run_id)
        assert any(e.event == "evidence.collection_failed" for e in log.events)

    def test_record_decision_gated(self, tmp_path) -> None:
        store = _store(tmp_path)
        run_evt = store.start_run("task")
        evt = store.record_decision_gated(
            run_evt.run_id,
            step_id="s1",
            evaluation_id="ev1",
            candidate_decision="ACCEPT",
            final_decision="REPLAN",
            assurance="insufficient",
        )
        assert evt.event == "decision.gated"
        assert evt.candidate_decision == "ACCEPT"
        log = store.read_run(run_evt.run_id)
        assert any(e.event == "decision.gated" for e in log.events)

    def test_record_action_reported_claimed(self, tmp_path) -> None:
        store = _store(tmp_path)
        run_evt = store.start_run("task")
        evt = store.record_action_reported(
            run_evt.run_id,
            step_id="s1",
            evaluation_id="ev1",
            intended_action="replan",
            reported_action="Switched strategy",
            new_contract_id="PHASE-001-R1",
        )
        assert evt.event == "action.reported"
        assert evt.reported_provenance.value == "claimed"
        assert evt.observed_action is None
        assert evt.new_contract_id == "PHASE-001-R1"

    def test_record_action_reported_with_observation(self, tmp_path) -> None:
        store = _store(tmp_path)
        run_evt = store.start_run("task")
        evt = store.record_action_reported(
            run_evt.run_id,
            step_id="s1",
            evaluation_id="ev1",
            intended_action="rollback",
            reported_action="Reverted",
            observed_action="git reset confirmed",
            observed_provenance="verified",
        )
        assert evt.observed_action is not None
        assert evt.observed_provenance.value == "verified"


class TestRetention:
    """Tests for retention configuration (item 13)."""

    def test_max_runs_prunes_oldest(self, tmp_path) -> None:
        from datetime import UTC, datetime, timedelta

        store = LineageStore(base_dir=tmp_path / "runs", max_runs=2, retention_days=None)
        now = datetime.now(UTC)
        r1 = store.start_run("r1", started_at=now - timedelta(hours=3))
        store.start_run("r2", started_at=now - timedelta(hours=2))
        store.start_run("r3", started_at=now - timedelta(hours=1))
        pruned = store.enforce_retention()
        assert r1.run_id in pruned
        remaining = [s.run_id for s in store.list_runs()]
        assert len(remaining) == 2
        assert r1.run_id not in remaining

    def test_retention_days_prunes_old(self, tmp_path) -> None:
        from datetime import UTC, datetime, timedelta

        store = LineageStore(base_dir=tmp_path / "runs", max_runs=None, retention_days=1)
        now = datetime.now(UTC)
        old = store.start_run("old", started_at=now - timedelta(days=10))
        store.start_run("new", started_at=now)
        pruned = store.enforce_retention()
        assert old.run_id in pruned
        remaining = [s.run_id for s in store.list_runs()]
        assert old.run_id not in remaining

    def test_no_pruning_when_limits_disabled(self, tmp_path) -> None:
        store = LineageStore(base_dir=tmp_path / "runs", max_runs=None, retention_days=None)
        store.start_run("r1")
        store.start_run("r2")
        assert store.enforce_retention() == []
        assert len(store.list_runs()) == 2


class TestSafeExport:
    """Tests for privacy-safe export (item 13)."""

    def test_safe_export_returns_events(self, tmp_path) -> None:
        store = _store(tmp_path)
        run_evt = store.start_run("task", metadata={"key": "value"})
        s = store.start_step(run_evt.run_id, contract_id="PHASE-001", attempt=1)
        store.record_evidence_collected(
            run_evt.run_id,
            step_id=s.step_id,
            check_id="tests-pass",
            collector="bound.pytest",
            provenance="verified",
            passed=True,
        )
        exported = store.safe_export(run_evt.run_id)
        assert "run" in exported
        assert "events" in exported
        assert "config" in exported
        assert len(exported["events"]) > 0

    def test_safe_export_omits_raw_output(self, tmp_path) -> None:
        store = _store(tmp_path)
        run_evt = store.start_run("task")
        exported = store.safe_export(run_evt.run_id)
        for ev in exported["events"]:
            assert "stdout" not in ev
            assert "stderr" not in ev
            assert "raw_artifact_ref" not in ev
