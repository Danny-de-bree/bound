from __future__ import annotations

import json
import logging
import os
import re
import shutil
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from bound.evidence import SECRET_PATTERN
from bound.integration import NextAction
from bound.lineage import (
    LINEAGE_SCHEMA_VERSION,
    ActionObservedEvent,
    ActionReportedEvent,
    Attempt,
    DecisionGatedEvent,
    Evaluation,
    EvaluationCompletedEvent,
    EvaluationRecordedEvent,
    EvidenceCollectedEvent,
    EvidenceCollectionFailedEvent,
    Outcome,
    OutcomeRecordedEvent,
    PolicyActivatedEvent,
    PolicyApprovedEvent,
    PolicyProposedEvent,
    PolicyValidatedEvent,
    ReasonCode,
    Run,
    RunConfigSnapshot,
    RunFinishedEvent,
    RunFinishStatus,
    RunStartedEvent,
    RunStatus,
    Step,
    StepCompletedEvent,
    StepStartedEvent,
    StepStatus,
    generate_evaluation_id,
    generate_event_id,
    generate_run_id,
    generate_step_id,
    parse_lineage_event,
    utc_now,
)
from bound.models import Decision, DecisionAssurance, EvaluationScores

__all__ = [
    "DEFAULT_MAX_EVENT_BYTES",
    "DEFAULT_MAX_FILE_BYTES",
    "DEFAULT_MAX_RUNS",
    "DEFAULT_RUNS_DIR",
    "DEFAULT_RETENTION_DAYS",
    "LineageCorruptEvent",
    "LineageEventTooLarge",
    "LineageFileTooLarge",
    "LineageStore",
    "LineageStoreError",
    "RunLog",
    "RunNotFound",
    "RunSummary",
    "configure",
    "get_default_store",
    "register_redactor",
    "scrub_secrets",
]

logger = logging.getLogger("bound.lineage_store")

#: Default on-disk lineage root, relative to the current working directory.
DEFAULT_RUNS_DIR: Path = Path(".bound/runs")

#: Default per-event byte cap. A single serialized event larger than this is
#: rejected rather than written, so a runaway payload cannot silently bloat a
#: log file.
DEFAULT_MAX_EVENT_BYTES: int = 256 * 1024

#: Default per-file byte cap for ``events.jsonl``. Appending past this raises,
#: keeping unbounded growth in check.
DEFAULT_MAX_FILE_BYTES: int = 16 * 1024 * 1024

#: Default maximum number of runs to retain (item 13 retention). ``None``
#: would mean unlimited; the default keeps a bounded local history. Runs
#: exceeding this count are pruned oldest-first by :meth:`enforce_retention`.
DEFAULT_MAX_RUNS: int | None = 200

#: Default maximum age in days for a run before it is eligible for pruning
#: (item 13 retention). ``None`` would mean no age-based expiry.
DEFAULT_RETENTION_DAYS: int | None = 90

#: Environment variable that, when set to a truthy value, disables lineage
#: persistence globally (see :func:`get_default_store`).
_ENV_DISABLED = "BOUND_LINEAGE_DISABLED"

#: Redaction hook type: a callable that mutates an event dict in place.
StoreRedactor = Callable[[dict], None]


class LineageStoreError(Exception):
    """Base class for lineage-storage errors."""


class LineageEventTooLarge(LineageStoreError):
    """A single serialized event exceeded :attr:`LineageStore.max_event_bytes`."""


class LineageFileTooLarge(LineageStoreError):
    """Appending an event would exceed :attr:`LineageStore.max_file_bytes`."""


class RunNotFound(LineageStoreError):
    """No lineage run exists for the requested ``run_id``."""


class LineageCorruptEvent(LineageStoreError):
    """A stored event line could not be parsed (raised in strict read mode)."""


def scrub_secrets(event_dict: dict) -> None:
    """Default redactor: mask secret-looking values in ``metadata`` and ``note``.

    Replaces the captured secret portion of any ``key=value`` / ``key: value``
    occurrence whose key looks like a credential name with ``***REDACTED***``.
    Mutates ``event_dict`` in place.

    Args:
        event_dict: The event as a plain dict (already JSON-decoded).
    """
    meta = event_dict.get("metadata")
    if isinstance(meta, dict):
        for k, v in list(meta.items()):
            if isinstance(v, str):
                meta[k] = SECRET_PATTERN.sub(_redact_sub, v)
    note = event_dict.get("note")
    if isinstance(note, str):
        event_dict["note"] = SECRET_PATTERN.sub(_redact_sub, note)


def _redact_sub(match: re.Match) -> str:
    return f"{match.group(1)}=***REDACTED***"


class RunSummary(BaseModel):
    """One row of ``bound run list``: a run's lightweight metadata.

    Attributes:
        run_id: The run identifier.
        task: The natural-language task the run attempts.
        schema_version: Lineage schema version recorded in ``run.json``.
        started_at: UTC instant the run began.
        finished_at: UTC instant the run finished, or ``None`` while open.
        status: Current :class:`~bound.lineage.RunStatus`.
        step_count: Number of distinct steps in the event log.
        event_count: Number of events in the event log.
        incomplete: ``True`` when the run has no ``run_finished`` event or a
            truncated / corrupt tail.
        path: Absolute path to the run directory.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    task: str
    schema_version: str = LINEAGE_SCHEMA_VERSION
    started_at: datetime
    finished_at: datetime | None = None
    status: RunStatus
    step_count: int
    event_count: int
    incomplete: bool
    path: str


class RunLog(BaseModel):
    """A fully replayed run: the current-state snapshots plus the raw events.

    Reconstructed from ``events.jsonl`` by :meth:`LineageStore.read_run`. The
    :attr:`run` / :attr:`steps` / :attr:`evaluations` / :attr:`outcomes`
    snapshots are derived views; :attr:`events` is the verbatim append-only log.

    Attributes:
        run: The :class:`~bound.lineage.Run` snapshot.
        steps: Ordered :class:`~bound.lineage.Step` snapshots.
        evaluations: Ordered :class:`~bound.lineage.Evaluation` snapshots.
        outcomes: Ordered :class:`~bound.lineage.Outcome` snapshots.
        events: The parsed append-only events, in log order.
        incomplete: ``True`` when the run is missing ``run_finished`` or has a
            truncated / corrupt tail.
        corrupt_lines: Number of malformed lines skipped during a lenient read.
        truncated: ``True`` when the final line lacked a trailing newline
            (dropped as a truncated write).
    """

    model_config = ConfigDict(extra="forbid")

    run: Run
    steps: list[Step]
    evaluations: list[Evaluation]
    outcomes: list[Outcome]
    events: list[object]
    incomplete: bool
    corrupt_lines: int = 0
    truncated: bool = False


class LineageStore:
    """Append-only local lineage store with privacy controls.

    Args:
        base_dir: Root directory for run storage. Defaults to
            :data:`DEFAULT_RUNS_DIR`.
        enabled: When ``False`` the convenience builders still construct and
            return events but persist nothing. Defaults to ``True``.
        redactors: Ordered redaction hooks applied to each event before
            writing. Defaults to ``[scrub_secrets]``.
        max_event_bytes: Per-event byte cap.
        max_file_bytes: Per-``events.jsonl`` byte cap.
        stored_fields: Optional allowlist mapping ``event`` name -> set of
            field names to persist. ``None`` (default) persists every schema
            field. Required/common fields (``event``, ``event_id``,
            ``timestamp``, ``schema_version``, ``run_id``) are always kept.
    """

    def __init__(
        self,
        base_dir: Path | str = DEFAULT_RUNS_DIR,
        *,
        enabled: bool = True,
        redactors: list[StoreRedactor] | None = None,
        max_event_bytes: int = DEFAULT_MAX_EVENT_BYTES,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        stored_fields: dict[str, set[str]] | None = None,
        max_runs: int | None = DEFAULT_MAX_RUNS,
        retention_days: int | None = DEFAULT_RETENTION_DAYS,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.enabled = enabled
        self.redactors: list[StoreRedactor] = (
            [scrub_secrets] if redactors is None else list(redactors)
        )
        self.max_event_bytes = max_event_bytes
        self.max_file_bytes = max_file_bytes
        self.stored_fields = stored_fields
        self.max_runs = max_runs
        self.retention_days = retention_days
        self._mem_seq = 0  # in-memory sequence counter for disabled mode

    # ------------------------------------------------------------------ paths
    def _run_dir(self, run_id: str) -> Path:
        return self.base_dir / run_id

    def _events_path(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "events.jsonl"

    def _meta_path(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "run.json"

    @staticmethod
    def _count_events(path: Path) -> int:
        if not path.exists():
            return 0
        with path.open("rb") as fh:
            return sum(1 for line in fh if line.strip())

    # ------------------------------------------------------------------ emit
    def _emit(
        self,
        event_cls: type,
        for_run_id: str,
        *,
        timestamp: datetime | None = None,
        parent_event_id: str | None = None,
        **fields: object,
    ) -> object:
        """Build, persist, and return one event.

        When the store is disabled the event is still constructed and returned
        (preserving the programmatic contract) but nothing is written.

        Schema 2.0: the event's ``sequence`` is set to its one-based position
        in the run's log, and ``parent_event_id`` is forwarded when supplied
        (item 10).
        """
        ts = timestamp or utc_now()
        if not self.enabled:
            self._mem_seq += 1
            return event_cls(
                event_id=generate_event_id(run_id=for_run_id, sequence=self._mem_seq),
                timestamp=ts,
                sequence=self._mem_seq,
                parent_event_id=parent_event_id,
                **fields,
            )
        run_dir = self._run_dir(for_run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        events_path = run_dir / "events.jsonl"
        seq = self._count_events(events_path) + 1
        event = event_cls(
            event_id=generate_event_id(run_id=for_run_id, sequence=seq),
            timestamp=ts,
            sequence=seq,
            parent_event_id=parent_event_id,
            **fields,
        )
        self._write_event(events_path, event)
        return event

    def _write_event(self, events_path: Path, event: object) -> None:
        data = json.loads(event.model_dump_json())  # type: ignore[attr-defined]
        self._redact(data)
        encoded = (json.dumps(data) + "\n").encode("utf-8")
        if len(encoded) > self.max_event_bytes:
            raise LineageEventTooLarge(
                f"event ({len(encoded)} bytes) exceeds max_event_bytes "
                f"({self.max_event_bytes})"
            )
        size = events_path.stat().st_size if events_path.exists() else 0
        if size + len(encoded) > self.max_file_bytes:
            raise LineageFileTooLarge(
                f"appending would exceed max_file_bytes ({self.max_file_bytes})"
            )
        with events_path.open("ab") as fh:
            fh.write(encoded)
            fh.flush()
            os.fsync(fh.fileno())

    def _redact(self, data: dict) -> None:
        if self.stored_fields is not None:
            name = data.get("event")
            allowed = self.stored_fields.get(name, set())
            keep = set(allowed) | {
                "event",
                "event_id",
                "timestamp",
                "schema_version",
                "run_id",
                "sequence",
                "parent_event_id",
            }
            for k in list(data.keys()):
                if k not in keep:
                    del data[k]
        for redactor in self.redactors:
            redactor(data)

    # ----------------------------------------------------------- run.json meta
    def _write_run_meta(self, run_id: str, meta: dict) -> None:
        if not self.enabled:
            return
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        meta_path = self._meta_path(run_id)
        tmp = meta_path.with_name(meta_path.name + ".tmp")
        tmp.write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, meta_path)

    def _read_run_meta(self, run_id: str) -> dict | None:
        meta_path = self._meta_path(run_id)
        if not meta_path.exists():
            return None
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    # ---------------------------------------------------------- public builders
    def start_run(
        self,
        task: str,
        *,
        started_at: datetime | None = None,
        metadata: dict[str, str] | None = None,
        config: RunConfigSnapshot | None = None,
    ) -> RunStartedEvent:
        """Start a new run: generate ``run_id``, write ``run.json``, append ``run_started``.

        The optional ``config`` (item 11) logs the policy/config version
        snapshot so the run can be replayed without re-running agent actions.
        """
        started = started_at or utc_now()
        run_id = generate_run_id(task=task, started_at=started)
        event = self._emit(
            RunStartedEvent,
            run_id,
            timestamp=started,
            run_id=run_id,
            task=task,
            metadata=metadata or {},
            config=config,
        )
        meta: dict[str, object] = {
            "run_id": run_id,
            "task": task,
            "schema_version": LINEAGE_SCHEMA_VERSION,
            "started_at": started.isoformat(),
            "status": RunStatus.STARTED.value,
        }
        if config is not None:
            meta["config"] = config.model_dump(mode="json")
        self._write_run_meta(run_id, meta)
        return event  # type: ignore[return-value]

    def start_step(
        self,
        run_id: str,
        *,
        contract_id: str,
        attempt: int = 1,
        step_id: str | None = None,
        description: str | None = None,
        started_at: datetime | None = None,
    ) -> StepStartedEvent:
        """Append a ``step_started`` event for one attempt of a step."""
        sid = step_id or generate_step_id(
            run_id=run_id, contract_id=contract_id, attempt=attempt
        )
        return self._emit(  # type: ignore[return-value]
            StepStartedEvent,
            run_id,
            timestamp=started_at,
            run_id=run_id,
            step_id=sid,
            contract_id=contract_id,
            attempt=attempt,
            description=description,
        )

    def record_evaluation(
        self,
        run_id: str,
        *,
        step_id: str,
        attempt: int,
        scores: EvaluationScores,
        score: float,
        threshold: float,
        decision: Decision | str,
        reason_code: ReasonCode | str,
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
        """Append an ``evaluation_recorded`` event for an attempt.

        Phase 7.2: the optional policy fields (``policy_id`` / ``policy_version``
        / ``policy_hash`` / ``contract_hash``) and the decision-assurance fields
        are forwarded to the event so every evaluation records the policy hash.
        """
        eid = evaluation_id or generate_evaluation_id(
            run_id=run_id, step_id=step_id, attempt=attempt
        )
        return self._emit(  # type: ignore[return-value]
            EvaluationRecordedEvent,
            run_id,
            timestamp=recorded_at,
            evaluation_id=eid,
            run_id=run_id,
            step_id=step_id,
            attempt=attempt,
            scores=scores,
            score=score,
            threshold=threshold,
            decision=decision,
            reason_code=reason_code,
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

    def record_outcome(
        self,
        run_id: str,
        *,
        step_id: str,
        evaluation_id: str,
        decision: Decision | str,
        next_action: NextAction | str,
        reason_code: ReasonCode | str,
        note: str | None = None,
        recorded_at: datetime | None = None,
    ) -> OutcomeRecordedEvent:
        """Append an ``outcome_recorded`` event responding to an evaluation."""
        return self._emit(  # type: ignore[return-value]
            OutcomeRecordedEvent,
            run_id,
            timestamp=recorded_at,
            run_id=run_id,
            step_id=step_id,
            evaluation_id=evaluation_id,
            decision=decision,
            next_action=next_action,
            reason_code=reason_code,
            note=note,
        )

    def finish_run(
        self,
        run_id: str,
        *,
        status: RunFinishStatus | str = RunFinishStatus.COMPLETED,
        reason_code: ReasonCode | str = ReasonCode.RUN_COMPLETED,
        note: str | None = None,
        finished_at: datetime | None = None,
    ) -> RunFinishedEvent:
        """Append the terminal ``run_finished`` event and update ``run.json``."""
        event = self._emit(  # type: ignore[return-value]
            RunFinishedEvent,
            run_id,
            timestamp=finished_at,
            run_id=run_id,
            status=status,
            reason_code=reason_code,
            note=note,
        )
        meta = self._read_run_meta(run_id) or {}
        meta["status"] = RunStatus(status).value
        meta["finished_at"] = (finished_at or event.timestamp).isoformat()  # type: ignore[union-attr]
        self._write_run_meta(run_id, meta)
        return event

    # ----------------------------------------------------- schema-2.0 builders
    def record_evidence_collected(
        self,
        run_id: str,
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
        """Append an ``evidence.collected`` event (item 10).

        Records that a BOUND-controlled collector produced evidence for a
        check, with its trust provenance.
        """
        return self._emit(  # type: ignore[return-value]
            EvidenceCollectedEvent,
            run_id,
            timestamp=observed_at,
            parent_event_id=parent_event_id,
            run_id=run_id,
            step_id=step_id,
            check_id=check_id,
            collector=collector,
            collector_version=collector_version,
            provenance=provenance,
            passed=passed,
            status=status,
            artifact_hash=artifact_hash,
            source=source,
            observed_at=observed_at or utc_now(),
        )

    def record_evidence_collection_failed(
        self,
        run_id: str,
        *,
        step_id: str,
        error: str,
        check_id: str | None = None,
        collector: str | None = None,
        observed_at: datetime | None = None,
        parent_event_id: str | None = None,
    ) -> EvidenceCollectionFailedEvent:
        """Append an ``evidence.collection_failed`` event (item 10).

        Records a collector crash / timeout / parse failure so the trace is
        honest about why evidence is missing.
        """
        return self._emit(  # type: ignore[return-value]
            EvidenceCollectionFailedEvent,
            run_id,
            timestamp=observed_at,
            parent_event_id=parent_event_id,
            run_id=run_id,
            step_id=step_id,
            check_id=check_id,
            collector=collector,
            error=error,
            observed_at=observed_at or utc_now(),
        )

    def record_decision_gated(
        self,
        run_id: str,
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
        """Append a ``decision.gated`` event (item 10/12).

        Records the assurance assessment that may downgrade a candidate
        ACCEPT to a different final decision.
        """
        return self._emit(  # type: ignore[return-value]
            DecisionGatedEvent,
            run_id,
            timestamp=recorded_at,
            parent_event_id=parent_event_id,
            run_id=run_id,
            step_id=step_id,
            evaluation_id=evaluation_id,
            candidate_decision=candidate_decision,
            final_decision=final_decision,
            assurance=assurance,
            assurance_reasons=assurance_reasons or [],
        )

    def record_action_reported(
        self,
        run_id: str,
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
        """Append an ``action.reported`` event (item 12).

        Records the agent's self-reported action (always CLAIMED) and an
        optional independent observation from integration hooks. For REPLAN,
        ``new_contract_id`` records the new plan/contract id.
        """
        return self._emit(  # type: ignore[return-value]
            ActionReportedEvent,
            run_id,
            timestamp=recorded_at,
            parent_event_id=parent_event_id,
            run_id=run_id,
            step_id=step_id,
            evaluation_id=evaluation_id,
            intended_action=intended_action,
            reported_action=reported_action,
            observed_action=observed_action,
            observed_provenance=observed_provenance,
            new_contract_id=new_contract_id,
            note=note,
        )

    # ------------------------------------------------- policy-lifecycle builders
    def record_policy_proposed(
        self,
        run_id: str,
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
        return self._emit(  # type: ignore[return-value]
            PolicyProposedEvent,
            run_id,
            timestamp=recorded_at,
            parent_event_id=parent_event_id,
            run_id=run_id,
            policy_id=policy_id,
            policy_version=policy_version,
            policy_hash=policy_hash,
            contract_hash=contract_hash,
            note=note,
        )

    def record_policy_validated(
        self,
        run_id: str,
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
        return self._emit(  # type: ignore[return-value]
            PolicyValidatedEvent,
            run_id,
            timestamp=recorded_at,
            parent_event_id=parent_event_id,
            run_id=run_id,
            policy_id=policy_id,
            policy_version=policy_version,
            policy_hash=policy_hash,
            contract_hash=contract_hash,
            note=note,
        )

    def record_policy_approved(
        self,
        run_id: str,
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
        """Append a ``policy.approved`` event (todo 7.1)."""
        return self._emit(  # type: ignore[return-value]
            PolicyApprovedEvent,
            run_id,
            timestamp=approved_at,
            parent_event_id=parent_event_id,
            run_id=run_id,
            policy_id=policy_id,
            policy_version=policy_version,
            policy_hash=policy_hash,
            approver=approver,
            approved_at=approved_at or utc_now(),
            contract_hash=contract_hash,
            note=note,
        )

    def record_policy_activated(
        self,
        run_id: str,
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
        return self._emit(  # type: ignore[return-value]
            PolicyActivatedEvent,
            run_id,
            timestamp=recorded_at,
            parent_event_id=parent_event_id,
            run_id=run_id,
            policy_id=policy_id,
            policy_version=policy_version,
            policy_hash=policy_hash,
            contract_hash=contract_hash,
            note=note,
        )

    # ------------------------------------------- evaluation/action/step builders
    def record_evaluation_completed(
        self,
        run_id: str,
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
        """Append an ``evaluation.completed`` event (todo 7.1/7.2)."""
        return self._emit(  # type: ignore[return-value]
            EvaluationCompletedEvent,
            run_id,
            timestamp=recorded_at,
            parent_event_id=parent_event_id,
            run_id=run_id,
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
        )

    def record_action_observed(
        self,
        run_id: str,
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
        """Append an ``action.observed`` event (todo 7.1/7.3)."""
        return self._emit(  # type: ignore[return-value]
            ActionObservedEvent,
            run_id,
            timestamp=recorded_at,
            parent_event_id=parent_event_id,
            run_id=run_id,
            step_id=step_id,
            evaluation_id=evaluation_id,
            intended_action=intended_action,
            observed_action=observed_action,
            observed_provenance=observed_provenance,
            reported_action=reported_action,
            matches_reported=matches_reported,
            new_contract_id=new_contract_id,
            note=note,
        )

    def record_step_completed(
        self,
        run_id: str,
        *,
        step_id: str,
        outcome: str | None = None,
        note: str | None = None,
        recorded_at: datetime | None = None,
        parent_event_id: str | None = None,
    ) -> StepCompletedEvent:
        """Append a ``step.completed`` event (todo 7.1)."""
        return self._emit(  # type: ignore[return-value]
            StepCompletedEvent,
            run_id,
            timestamp=recorded_at,
            parent_event_id=parent_event_id,
            run_id=run_id,
            step_id=step_id,
            outcome=outcome,
            note=note,
        )

    # ----------------------------------------------------------------- read
    def read_run(self, run_id: str, *, strict: bool = False) -> RunLog:
        """Replay ``events.jsonl`` for ``run_id`` into a :class:`RunLog`.

        Args:
            run_id: The run to read.
            strict: When ``True`` a malformed line raises
                :class:`LineageCorruptEvent`; when ``False`` (default) it is
                skipped and counted in :attr:`RunLog.corrupt_lines`.
        """
        events_path = self._events_path(run_id)
        if not events_path.exists():
            raise RunNotFound(f"no lineage run for run_id={run_id!r}")
        text = events_path.read_text(encoding="utf-8")
        truncated = bool(text) and not text.endswith("\n")
        lines = text.splitlines()
        if truncated and lines:
            lines = lines[:-1]  # drop the trailing partial line
        events: list[object] = []
        corrupt = 0
        for line in lines:
            if not line.strip():
                continue
            try:
                events.append(parse_lineage_event(line))
            except Exception as exc:  # noqa: BLE001 - any parse/validation error
                if strict:
                    raise LineageCorruptEvent(f"unparseable event line: {line!r}") from exc
                corrupt += 1
                logger.warning("skipping corrupt lineage event line: %r", line)
        return self._replay(run_id, events, corrupt=corrupt, truncated=truncated)

    @staticmethod
    def _replay(
        run_id: str, events: list[object], *, corrupt: int, truncated: bool
    ) -> RunLog:
        run: Run | None = None
        steps: dict[str, Step] = {}
        step_order: list[str] = []
        evals: dict[str, Evaluation] = {}
        outcomes: list[Outcome] = []
        step_outcomes: dict[str, Decision] = {}
        for ev in events:
            name = getattr(ev, "event", None)
            if name == "run_started":
                run = Run(
                    run_id=ev.run_id,  # type: ignore[attr-defined]
                    task=ev.task,  # type: ignore[attr-defined]
                    schema_version=ev.schema_version,  # type: ignore[attr-defined]
                    started_at=ev.timestamp,  # type: ignore[attr-defined]
                    status=RunStatus.STARTED,
                    metadata=ev.metadata,  # type: ignore[attr-defined]
                    config=getattr(ev, "config", None),
                )
            elif name == "step_started":
                sid = ev.step_id  # type: ignore[attr-defined]
                if sid not in steps:
                    steps[sid] = Step(
                        step_id=sid,
                        run_id=ev.run_id,  # type: ignore[attr-defined]
                        schema_version=ev.schema_version,  # type: ignore[attr-defined]
                        contract_id=ev.contract_id,  # type: ignore[attr-defined]
                        description=ev.description,  # type: ignore[attr-defined]
                        started_at=ev.timestamp,  # type: ignore[attr-defined]
                        status=StepStatus.STARTED,
                        attempts=[],
                    )
                    step_order.append(sid)
                steps[sid].attempts.append(
                    Attempt(
                        attempt=ev.attempt,  # type: ignore[attr-defined]
                        started_at=ev.timestamp,  # type: ignore[attr-defined]
                        evaluation_id=None,
                    )
                )
            elif name == "evaluation_recorded":
                eid = ev.evaluation_id  # type: ignore[attr-defined]
                evaluation = Evaluation(
                    evaluation_id=eid,
                    run_id=ev.run_id,  # type: ignore[attr-defined]
                    step_id=ev.step_id,  # type: ignore[attr-defined]
                    attempt=ev.attempt,  # type: ignore[attr-defined]
                    scores=ev.scores,  # type: ignore[attr-defined]
                    score=ev.score,  # type: ignore[attr-defined]
                    threshold=ev.threshold,  # type: ignore[attr-defined]
                    decision=ev.decision,  # type: ignore[attr-defined]
                    reason_code=ev.reason_code,  # type: ignore[attr-defined]
                    recorded_at=ev.timestamp,  # type: ignore[attr-defined]
                )
                evals[eid] = evaluation
                step = steps.get(ev.step_id)  # type: ignore[attr-defined]
                if step is not None:
                    for att in step.attempts:
                        if att.attempt == ev.attempt:  # type: ignore[attr-defined]
                            att.evaluation_id = eid
                            break
            elif name == "outcome_recorded":
                outcomes.append(
                    Outcome(
                        run_id=ev.run_id,  # type: ignore[attr-defined]
                        step_id=ev.step_id,  # type: ignore[attr-defined]
                        evaluation_id=ev.evaluation_id,  # type: ignore[attr-defined]
                        decision=ev.decision,  # type: ignore[attr-defined]
                        next_action=ev.next_action,  # type: ignore[attr-defined]
                        reason_code=ev.reason_code,  # type: ignore[attr-defined]
                        recorded_at=ev.timestamp,  # type: ignore[attr-defined]
                        note=ev.note,  # type: ignore[attr-defined]
                    )
                )
                step_outcomes[ev.step_id] = ev.decision  # type: ignore[attr-defined]
            elif name == "run_finished" and run is not None:
                run.status = RunStatus(ev.status)  # type: ignore[attr-defined]
                run.finished_at = ev.timestamp  # type: ignore[attr-defined]
        if run is None:
            run = Run(run_id=run_id, task="", started_at=utc_now())
        for sid, step in steps.items():
            last_decision = step_outcomes.get(sid)
            if last_decision == "REPLAN":
                step.status = StepStatus.REPLANNED
            elif last_decision is not None:
                step.status = StepStatus.COMPLETED
        run.step_ids = step_order
        incomplete = run.status == RunStatus.STARTED or truncated or corrupt > 0
        return RunLog(
            run=run,
            steps=[steps[s] for s in step_order],
            evaluations=list(evals.values()),
            outcomes=outcomes,
            events=events,
            incomplete=incomplete,
            corrupt_lines=corrupt,
            truncated=truncated,
        )

    # --------------------------------------------------------------- listing
    def list_runs(self) -> list[RunSummary]:
        """List every run under :attr:`base_dir`, newest first."""
        summaries: list[RunSummary] = []
        if not self.base_dir.exists():
            return summaries
        for meta_path in sorted(self.base_dir.glob("*/run.json")):
            run_id = meta_path.parent.name
            meta = self._read_run_meta(run_id) or {}
            started = _parse_dt(meta.get("started_at"))
            finished = _parse_dt(meta.get("finished_at"))
            status = RunStatus(meta.get("status", RunStatus.STARTED.value))
            events_path = self._events_path(run_id)
            event_count = self._count_events(events_path) if events_path.exists() else 0
            log = self._safe_replay(run_id)
            summaries.append(
                RunSummary(
                    run_id=run_id,
                    task=meta.get("task", ""),
                    schema_version=meta.get("schema_version", LINEAGE_SCHEMA_VERSION),
                    started_at=started,
                    finished_at=finished,
                    status=status,
                    step_count=len(log.steps) if log is not None else 0,
                    event_count=event_count,
                    incomplete=(log is None) or log.incomplete,
                    path=str(meta_path.parent.resolve()),
                )
            )
        summaries.sort(key=lambda s: s.started_at or datetime.fromtimestamp(0), reverse=True)
        return summaries

    def _safe_replay(self, run_id: str) -> RunLog | None:
        try:
            return self.read_run(run_id, strict=False)
        except (RunNotFound, LineageCorruptEvent):
            return None

    # ---------------------------------------------------------------- delete
    def delete_run(self, run_id: str) -> None:
        """Remove an entire run directory (backs ``bound run delete``)."""
        run_dir = self._run_dir(run_id)
        if not run_dir.exists():
            raise RunNotFound(f"no lineage run for run_id={run_id!r}")
        shutil.rmtree(run_dir)

    # ------------------------------------------------------- retention (item 13)
    def enforce_retention(self) -> list[str]:
        """Prune runs exceeding the configured retention limits (item 13).

        Removes the oldest runs when the run count exceeds
        :attr:`max_runs` and/or any run older than
        :attr:`retention_days` days. Returns the ids of the pruned runs.

        Returns:
            The list of pruned run ids (empty when nothing was pruned).
        """
        pruned: list[str] = []
        if not self.enabled or not self.base_dir.exists():
            return pruned
        summaries = self.list_runs()
        now = utc_now()
        # Age-based pruning: remove runs older than retention_days.
        if self.retention_days is not None:
            cutoff = now - timedelta(days=self.retention_days)
            for s in summaries:
                if s.started_at is not None and s.started_at < cutoff:
                    try:
                        self.delete_run(s.run_id)
                        pruned.append(s.run_id)
                    except RunNotFound:
                        pass
        if self.max_runs is not None:
            remaining = [s for s in self.list_runs() if s.run_id not in set(pruned)]
            remaining.sort(
                key=lambda s: s.started_at or datetime.fromtimestamp(0), reverse=True
            )
            for s in remaining[self.max_runs :]:
                try:
                    self.delete_run(s.run_id)
                    pruned.append(s.run_id)
                except RunNotFound:
                    pass
        return pruned

    # ----------------------------------------------------- safe export (item 13)
    def safe_export(self, run_id: str) -> dict[str, object]:
        """Export a run as a privacy-safe dict (item 13).

        Returns the run's config, metadata, and a redacted event log. Raw
        command output is replaced by its hash (when present); free-text
        ``note`` / ``error`` / ``reported_action`` fields are kept (they are
        already redacted by the store's redactor chain at write time), but
        any ``raw_artifact_ref`` or full raw output is omitted. This is the
        shape suitable for sharing outside the local ``.bound/`` directory.

        Args:
            run_id: The run to export.

        Returns:
            A dict with ``run``, ``events``, and ``config`` keys.

        Raises:
            RunNotFound: If the run does not exist.
        """
        log = self.read_run(run_id)
        config = log.run.config.model_dump(mode="json") if log.run.config else None
        safe_events: list[dict[str, object]] = []
        for ev in log.events:
            data = json.loads(ev.model_dump_json())  # type: ignore[attr-defined]
            _redact_for_export(data)
            safe_events.append(data)
        return {
            "run": log.run.model_dump(mode="json"),
            "events": safe_events,
            "config": config,
        }


def _redact_for_export(data: dict[str, object]) -> None:
    """Remove sensitive fields from an event dict for safe export (item 13).

    Drops ``raw_artifact_ref`` and replaces any full raw output with its hash
    only. Fields already redacted by the store's write-time redactor chain
    (``note``, ``metadata``, ``error``, ``reported_action``) are left as-is
    since they were scrubbed before persistence.
    """
    data.pop("raw_artifact_ref", None)
    # Full raw command output is never exported — only the hash remains.
    for key in ("stdout", "stderr", "raw_output"):
        if key in data and data[key]:
            data.pop(key, None)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


# --------------------------------------------------------------------- module API
_default_store: LineageStore | None = None


def configure(
    *,
    base_dir: Path | str = DEFAULT_RUNS_DIR,
    enabled: bool | None = None,
    redactors: list[StoreRedactor] | None = None,
    max_event_bytes: int = DEFAULT_MAX_EVENT_BYTES,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    stored_fields: dict[str, set[str]] | None = None,
    max_runs: int | None = DEFAULT_MAX_RUNS,
    retention_days: int | None = DEFAULT_RETENTION_DAYS,
) -> LineageStore:
    """Create and install the process-wide default :class:`LineageStore`.

    Subsequent :func:`get_default_store` calls return this instance. When
    ``enabled`` is ``None`` it is derived from the ``BOUND_LINEAGE_DISABLED``
    environment variable.
    """
    global _default_store  # noqa: PLW0603
    if enabled is None:
        enabled = not _env_disabled()
    _default_store = LineageStore(
        base_dir=base_dir,
        enabled=enabled,
        redactors=redactors,
        max_event_bytes=max_event_bytes,
        max_file_bytes=max_file_bytes,
        stored_fields=stored_fields,
        max_runs=max_runs,
        retention_days=retention_days,
    )
    return _default_store


def get_default_store() -> LineageStore:
    """Return the process-wide default store, creating it lazily.

    Respects :data:`_ENV_DISABLED` (``BOUND_LINEAGE_DISABLED``) on first
    construction.
    """
    global _default_store  # noqa: PLW0603
    if _default_store is None:
        _default_store = LineageStore(enabled=not _env_disabled())
    return _default_store


def register_redactor(redactor: StoreRedactor) -> None:
    """Append a redaction hook to the default store's redactor chain."""
    get_default_store().redactors.append(redactor)


def _env_disabled() -> bool:
    return os.environ.get(_ENV_DISABLED, "").strip().lower() in {"1", "true", "yes", "on"}
