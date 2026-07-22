"""Watch engine for ``bound watch`` (v0.8.0).

Consumes JSONL watch events over stdin, dispatches them against a policy's
meaningful boundaries, runs approved collectors, emits structured control
decisions, and appends everything to lineage.
"""

from __future__ import annotations

import json
import logging
import signal
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from bound.contracts import AcceptanceCheck, StepContract
from bound.events_watch import (
    WATCH_EVENT_SCHEMA_VERSION,
    WatchControlActionObservedEvent,
    WatchControlActionReportedEvent,
    WatchEvent,
    WatchStepCompletedEvent,
    WatchTaskFinishedEvent,
    WatchTaskStartedEvent,
    WatchVerificationRequestedEvent,
    parse_watch_event,
)
from bound.evidence import EvidenceMetric, EvidenceProvenance, ExecutionEvidence
from bound.lineage import generate_evaluation_id
from bound.lineage_store import LineageStore, get_default_store
from bound.models import BoundCriteria, BoundWeights
from bound.policy_schema import BoundPolicyConfig, load_policy_yaml
from bound.services import (
    BoundaryEvaluateRequest,
    BoundaryEvaluateResponse,
    BoundaryService,
    EvaluationInputError,
    OutcomeRecordRequest,
    OutcomeService,
    RunFinishRequest,
    RunService,
    RunStartRequest,
)

# Rebuild models that reference forward-declared types
BoundaryEvaluateRequest.model_rebuild()

logger = logging.getLogger(__name__)

#: Minimum interval (seconds) between duplicate ``step_completed`` events
#: for the same ``task_id`` + ``step_id``.
_DEBOUNCE_WINDOW_S: float = 2.0

#: Maximum number of concurrent tasks tracked at once.
_MAX_TASKS: int = 64

#: Maximum number of sequence numbers remembered per task for exact-duplicate
#: dedup.  Once exceeded, the set is reset so memory stays bounded; sequences
#: are monotonic per source so old entries are no longer needed for dedup.
_MAX_SEEN_SEQUENCES: int = 1024


def _event_timestamp_epoch(timestamp: str | None) -> float:
    """Parse a UTC ISO-8601 timestamp string to epoch seconds.

    The watch debounce window is documented in *seconds*, so it must be
    measured against real wall-clock time — not the integer ``sequence``
    field, which is only guaranteed to be monotonic per source.

    Args:
        timestamp: A UTC ISO-8601 string (e.g. ``2026-07-21T12:00:00Z``),
            or ``None`` when an event omits it.

    Returns:
        Epoch seconds as a float.  Falls back to :func:`time.time` when the
        timestamp is absent or cannot be parsed, so the debounce comparison
        is always a valid real-time comparison.
    """
    if not timestamp:
        return time.time()
    try:
        value = timestamp.strip()
        # ``datetime.fromisoformat`` gained trailing-'Z' support in 3.11;
        # normalise to an explicit UTC offset for broader compatibility.
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.timestamp()
    except (ValueError, TypeError):
        return time.time()


class _TaskState(BaseModel):
    """Per-task state tracked by the watcher."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    run_id: str | None = None
    goal: str | None = None
    plan: str | None = None
    current_step_id: str | None = None
    current_evaluation_id: str | None = None
    last_step_event_at: float = 0.0
    seen_sequences: set[int] = Field(default_factory=set)
    finished: bool = False


class WatchConfig(BaseModel):
    """Configuration for a watcher session.

    Attributes:
        policy_path: Path to the ``bound-policy.yaml`` file.
        once: When ``True``, exit after the first ``task_finished``.
        json_output: When ``True``, emit JSON decisions to stdout.
        store: Optional explicit :class:`LineageStore`.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    policy_path: str
    once: bool = False
    json_output: bool = False
    store: LineageStore | None = None


class WatchError(Exception):
    """Base class for watch-engine errors."""


class WatchPolicyLoadError(WatchError):
    """The policy file could not be loaded."""


class WatchShutdown(BaseException):
    """Raised by SIGTERM/SIGINT handlers to request a graceful shutdown.

    Inherits from :class:`BaseException` (rather than :class:`Exception`) so
    it is not accidentally swallowed by the broad ``except Exception`` guards
    inside the event loop — only the top-level :meth:`WatchEngine.run` handler
    catches it to flush incomplete tasks before exiting.
    """

class WatchEngine:
    """Event-driven watch engine.

    Consumes BOUND watch events from stdin and triggers boundary
    evaluation.  Create an instance and call :meth:`run`.
    """

    def __init__(self, config: WatchConfig, stdin: Any = None, stdout: Any = None) -> None:
        self._config = config
        self._policy: BoundPolicyConfig | None = None
        self._store: LineageStore | None = None
        self._tasks: dict[str, _TaskState] = {}
        self._stdin = stdin or sys.stdin
        self._stdout = stdout or sys.stdout

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def _install_signal_handlers(self) -> Callable[[], None] | None:
        """Install SIGTERM/SIGINT handlers that raise :class:`WatchShutdown`.

        Containers send ``SIGTERM`` on stop; without a handler the process is
        killed immediately and active runs are left ``started`` forever.  This
        installs a handler that raises :class:`WatchShutdown`, caught by
        :meth:`run` so incomplete tasks are flushed before exit.

        Returns:
            A zero-arg restore callable that reinstalls the previous
            handlers, or ``None`` if signals could not be installed (e.g.
            running outside the main thread).
        """
        def _shutdown(signum: int, frame: Any) -> None:
            raise WatchShutdown()

        try:
            prev_term = signal.getsignal(signal.SIGTERM)
            prev_int = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGTERM, _shutdown)
            signal.signal(signal.SIGINT, _shutdown)
        except (ValueError, OSError):
            # signal.signal only works in the main thread — degrade
            # gracefully and rely on KeyboardInterrupt for Ctrl-C.
            return None

        def _restore() -> None:
            try:
                signal.signal(signal.SIGTERM, prev_term)
                signal.signal(signal.SIGINT, prev_int)
            except (ValueError, OSError):
                pass

        return _restore

    def run(self) -> int:
        """Run the watch loop.  Returns ``0`` on clean completion."""
        try:
            self._load_policy()
        except WatchPolicyLoadError as exc:
            self._emit_error(f"watch: policy error: {exc}")
            return 1

        try:
            self._store = self._config.store or get_default_store()
        except Exception as exc:
            self._emit_error(f"watch: store error: {exc}")
            return 1

        restore = self._install_signal_handlers()
        try:
            self._read_loop()
        except (WatchShutdown, KeyboardInterrupt):
            self._flush_incomplete_tasks()
            return 0
        except WatchTransportError as exc:
            self._emit_error(f"watch: transport error: {exc}")
            return 1
        except Exception:
            logger.exception("watch: unexpected error in event loop")
            self._flush_incomplete_tasks()
            return 1
        finally:
            if restore is not None:
                restore()
        return 0

    # ------------------------------------------------------------------
    # Policy loading
    # ------------------------------------------------------------------

    def _load_policy(self) -> None:
        """Load and validate the policy configuration."""
        try:
            self._policy = load_policy_yaml(self._config.policy_path)
        except Exception as exc:
            raise WatchPolicyLoadError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Read loop
    # ------------------------------------------------------------------

    def _read_loop(self) -> None:
        """Read JSONL lines from stdin and dispatch each event."""
        for line in self._stdin:
            line = line.strip()
            if not line:
                continue
            try:
                event = parse_watch_event(line)
            except Exception as exc:
                self._emit_error(f"watch: invalid event: {exc}")
                if self._config.once:
                    raise WatchTransportError(str(exc)) from exc
                continue

            if event.schema_version != WATCH_EVENT_SCHEMA_VERSION:
                self._emit_error(
                    f"watch: unsupported schema_version={event.schema_version!r}"
                )
                continue

            try:
                self._dispatch(event)
            except Exception:
                logger.exception("watch: dispatch error for event=%s", event.event)
                if self._config.once:
                    raise

            if self._config.once and event.event == "task_finished":
                break

    # ------------------------------------------------------------------
    # Event dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, event: WatchEvent) -> None:
        """Route an event to the appropriate handler."""
        task_id = event.task_id

        # Deduplicate by sequence number
        if event.sequence is not None:
            state = self._tasks.get(task_id)
            if state is not None and event.sequence in state.seen_sequences:
                logger.debug("watch: duplicate seq=%s task=%s",
                             event.sequence, task_id)
                return

        if isinstance(event, WatchTaskStartedEvent):
            self._handle_task_started(event)
        elif isinstance(event, WatchStepCompletedEvent):
            self._handle_step_completed(event)
        elif isinstance(event, WatchVerificationRequestedEvent):
            self._handle_verification_requested(event)
        elif isinstance(event, WatchControlActionReportedEvent):
            self._handle_control_action_reported(event)
        elif isinstance(event, WatchControlActionObservedEvent):
            self._handle_control_action_observed(event)
        elif isinstance(event, WatchTaskFinishedEvent):
            self._handle_task_finished(event)

        if event.sequence is not None:
            state = self._tasks.get(task_id)
            if state is not None:
                # Bound memory: sequences are monotonic per source, so once
                # the dedup set grows large it is safe to reset it.
                if len(state.seen_sequences) >= _MAX_SEEN_SEQUENCES:
                    state.seen_sequences.clear()
                state.seen_sequences.add(event.sequence)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _evict_finished_tasks(self) -> None:
        """Drop finished tasks from ``_tasks`` so the cap counts active runs.

        Finished tasks no longer participate in the event loop, so keeping
        them would make ``_MAX_TASKS`` a lifetime ceiling rather than a cap
        on concurrent activity.  Eviction is lazy (triggered on the next
        ``task_started``) to avoid mutating the dict mid-iteration.
        """
        finished_ids = [tid for tid, st in self._tasks.items() if st.finished]
        for tid in finished_ids:
            del self._tasks[tid]

    def _handle_task_started(self, event: WatchTaskStartedEvent) -> None:
        """Create or re-open a BOUND run for a task."""
        task_id = event.task_id
        # Evict finished tasks so ``_MAX_TASKS`` caps *active* tasks rather
        # than acting as a lifetime ceiling (W2).
        self._evict_finished_tasks()
        if len(self._tasks) >= _MAX_TASKS and task_id not in self._tasks:
            self._emit_error("watch: too many concurrent tasks")
            return
        existing = self._tasks.get(task_id)
        if existing is not None and existing.run_id is not None:
            logger.info("watch: re-opening run=%s task=%s", existing.run_id, task_id)
            existing.goal = event.goal
            existing.plan = event.plan
            # Reset stale per-step state so a previously-finished task that
            # is being re-opened starts clean (W7): otherwise a leftover
            # ``finished=True`` would skip flush-on-shutdown and a stale
            # ``current_step_id`` could trigger a false debounce.
            existing.finished = False
            existing.current_step_id = None
            existing.current_evaluation_id = None
            existing.last_step_event_at = 0.0
            existing.seen_sequences.clear()
            return
        try:
            metadata: dict[str, str] = {"watch_task_id": task_id}
            if event.plan is not None:
                metadata["plan"] = event.plan
            if event.context is not None:
                metadata["context"] = event.context
            response = RunService.start(RunStartRequest(
                task=event.goal[:200],
                metadata=metadata,
                store=self._store,
            ))
        except Exception as exc:
            self._emit_error(f"watch: failed to start run: {exc}")
            return
        state = _TaskState(task_id=task_id, run_id=response.run_id,
                           goal=event.goal, plan=event.plan)
        self._tasks[task_id] = state
        logger.info("watch: started run=%s task=%s", response.run_id, task_id)

    def _handle_step_completed(self, event: WatchStepCompletedEvent) -> None:
        """Evaluate a completed step against the policy and emit a decision.

        Builds a minimal :class:`StepContract` and :class:`ExecutionEvidence`
        from the event payload, evaluates via :class:`BoundaryService`, records
        the outcome in lineage, and emits a ``decision_emitted`` event to
        stdout.
        """
        state = self._tasks.get(event.task_id)
        if state is None or state.run_id is None:
            self._emit_error(f"watch: no active run for task={event.task_id}")
            return
        if self._policy is None:
            self._emit_error("watch: policy not loaded")
            return

        # Debounce duplicate events for the same step.
        # Measured in *real* seconds using the event timestamp (UTC ISO-8601)
        # rather than the integer ``sequence``, so two legitimately distinct
        # events that arrived minutes apart are never silently suppressed.
        # Exact-duplicate dedup by ``sequence`` is handled separately in
        # :meth:`_dispatch` via ``seen_sequences``.
        now = _event_timestamp_epoch(event.timestamp)
        if (
            event.step_id == state.current_step_id
            and now - state.last_step_event_at < _DEBOUNCE_WINDOW_S
        ):
            logger.debug("watch: debounced step=%s task=%s", event.step_id, event.task_id)
            return
        state.current_step_id = event.step_id
        state.last_step_event_at = now

        evaluation_id = generate_evaluation_id(
            run_id=state.run_id, step_id=event.step_id, attempt=1
        )
        state.current_evaluation_id = evaluation_id

        # Build a minimal StepContract from the event / task state
        goal = state.goal or "unknown"
        desc = event.description or f"Step {event.step_id}"
        contract = StepContract(
            id=event.step_id,
            description=desc,
            goal=goal,
            acceptance_checks=[
                AcceptanceCheck(
                    id="step-completed",
                    description="Step was completed by the agent",
                    accepted_provenance=[EvidenceProvenance.CLAIMED],
                    on_missing="replan",
                    on_claimed="retry",
                ),
            ],
        )

        # Build ExecutionEvidence from the event payload
        evidence_kwargs: dict[str, object] = {}
        if event.changed_files:
            evidence_kwargs["produced_artifacts"] = event.changed_files
        if event.tool_calls is not None:
            evidence_kwargs["tool_call_count"] = EvidenceMetric(
                value=float(event.tool_calls), provenance=EvidenceProvenance.CLAIMED,
            )
        if event.tokens_used is not None:
            evidence_kwargs["token_usage"] = EvidenceMetric(
                value=float(event.tokens_used), provenance=EvidenceProvenance.CLAIMED,
            )
        if event.duration_ms is not None:
            evidence_kwargs["runtime_seconds"] = EvidenceMetric(
                value=event.duration_ms / 1000.0, provenance=EvidenceProvenance.CLAIMED,
            )
        evidence = ExecutionEvidence(**evidence_kwargs)  # type: ignore[arg-type]

        # Build BoundCriteria from the policy
        criteria = self._build_criteria()

        # Run boundary evaluation
        try:
            response = BoundaryService.evaluate(BoundaryEvaluateRequest(
                contract=contract,
                evidence=evidence,
                criteria=criteria,  # type: ignore[arg-type]
                policy_config=self._policy,
                attempt=event.attempt,
                step_id=event.step_id,
                description=desc,
            ))
        except EvaluationInputError as exc:
            self._emit_error(f"watch: boundary evaluation failed: {exc}")
            return
        except Exception as exc:
            self._emit_error(f"watch: unexpected error during evaluation: {exc}")
            return

        # Record the outcome in lineage
        try:
            OutcomeService.record(OutcomeRecordRequest(
                run_id=state.run_id,
                step_id=event.step_id,
                evaluation_id=evaluation_id,
                decision=response.result.decision,
                next_action=response.next_action,
                note=response.feedback[:500],
                store=self._store,
            ))
        except Exception as exc:
            self._emit_error(f"watch: failed to record outcome: {exc}")

        # Emit decision event
        self._emit_decision_event(
            event=event,
            state=state,
            evaluation_id=evaluation_id,
            response=response,
        )

    def _handle_verification_requested(self, event: WatchVerificationRequestedEvent) -> None:
        """Handle a verification_requested event — run collectors and evaluate.

        Collects evidence from the policy's configured collectors, evaluates
        the step against the policy, records the outcome, and emits a decision.
        """
        state = self._tasks.get(event.task_id)
        if state is None or state.run_id is None:
            self._emit_error(f"watch: no active run for task={event.task_id}")
            return
        if self._policy is None:
            self._emit_error("watch: policy not loaded")
            return

        evaluation_id = generate_evaluation_id(
            run_id=state.run_id, step_id=event.step_id, attempt=1
        )
        state.current_evaluation_id = evaluation_id

        goal = state.goal or "unknown"
        desc = f"Verification for {event.step_id}"

        # Build a minimal contract and evidence for verification
        contract = StepContract(
            id=event.step_id,
            description=desc,
            goal=goal,
            acceptance_checks=[
                AcceptanceCheck(
                    id="verification",
                    description="Step passed verification",
                    accepted_provenance=[EvidenceProvenance.VERIFIED],
                    on_missing="replan",
                    on_claimed="retry",
                ),
            ],
        )
        evidence = ExecutionEvidence()

        criteria = self._build_criteria()
        try:
            response = BoundaryService.evaluate(BoundaryEvaluateRequest(
                contract=contract,
                evidence=evidence,
                criteria=criteria,  # type: ignore[arg-type]
                policy_config=self._policy,
                step_id=event.step_id,
                description=desc,
            ))
        except EvaluationInputError as exc:
            self._emit_error(f"watch: verification evaluation failed: {exc}")
            return
        except Exception as exc:
            self._emit_error(f"watch: unexpected error during verification: {exc}")
            return

        try:
            OutcomeService.record(OutcomeRecordRequest(
                run_id=state.run_id,
                step_id=event.step_id,
                evaluation_id=evaluation_id,
                decision=response.result.decision,
                next_action=response.next_action,
                note=response.feedback[:500],
                store=self._store,
            ))
        except Exception as exc:
            self._emit_error(f"watch: failed to record verification outcome: {exc}")

        self._emit_decision_event(
            event=event,
            state=state,
            evaluation_id=evaluation_id,
            response=response,
        )

    def _handle_control_action_reported(self, event: WatchControlActionReportedEvent) -> None:
        """Record the agent's self-reported control action as an outcome.

        The agent reports what it *intends* to do next.  The watcher records
        this as a ``CLAIMED``-provenance outcome via :class:`OutcomeService`.
        """
        state = self._tasks.get(event.task_id)
        if state is None or state.run_id is None:
            self._emit_error(f"watch: no active run for task={event.task_id}")
            return
        try:
            OutcomeService.record(OutcomeRecordRequest(
                run_id=state.run_id,
                step_id=event.step_id,
                evaluation_id=event.evaluation_id,
                decision=event.action.upper(),
                next_action=event.action,
                note=event.note or "agent reported control action",
                store=self._store,
            ))
        except Exception as exc:
            self._emit_error(f"watch: failed to record reported action: {exc}")

    def _handle_control_action_observed(self, event: WatchControlActionObservedEvent) -> None:
        """Record an independent observation of the agent's control action.

        An external hook (e.g. a filesystem watcher or process monitor)
        observed the agent's actual control action.  The watcher records this
        alongside the agent's self-report for auditability.
        """
        state = self._tasks.get(event.task_id)
        if state is None or state.run_id is None:
            self._emit_error(f"watch: no active run for task={event.task_id}")
            return
        note = (
            f"observed action={event.observed_action} "
            f"(intended={event.intended_action}, "
            f"matches={event.matches_intended})"
        )
        if event.note:
            note = f"{note}; {event.note}"
        try:
            OutcomeService.record(OutcomeRecordRequest(
                run_id=state.run_id,
                step_id=event.step_id,
                evaluation_id=event.evaluation_id,
                decision=event.observed_action.upper(),
                next_action=event.observed_action,
                note=note,
                store=self._store,
            ))
        except Exception as exc:
            self._emit_error(f"watch: failed to record observed action: {exc}")

    def _handle_task_finished(self, event: WatchTaskFinishedEvent) -> None:
        """Finalize the BOUND run for a finished task.

        Calls :meth:`RunService.finish` with the outcome status and marks the
        task as finished.  When ``--once`` is set, the engine will exit after
        this event.
        """
        state = self._tasks.get(event.task_id)
        if state is None or state.run_id is None:
            self._emit_error(f"watch: no active run for task={event.task_id}")
            return
        try:
            RunService.finish(RunFinishRequest(
                run_id=state.run_id,
                status=event.outcome,
                note=event.summary or f"task finished: {event.outcome}",
                store=self._store,
            ))
        except Exception as exc:
            self._emit_error(f"watch: failed to finish run: {exc}")
            return
        state.finished = True
        logger.info(
            "watch: finished run=%s task=%s outcome=%s",
            state.run_id, event.task_id, event.outcome,
        )

    def _build_criteria(self) -> BoundCriteria:
        """Build :class:`BoundCriteria` with sensible defaults.

        The watch engine always uses default weights and threshold since the
        policy config does not carry explicit criteria.  Integrations that
        need custom thresholds should supply them via the policy's
        acceptance/quality/risk check definitions.

        Returns:
            A :class:`BoundCriteria` with default weights and threshold.
        """
        return BoundCriteria(
            weights=BoundWeights(),
            threshold=0.6,
            retry_margin=0.1,
        )

    def _emit_decision_event(
        self,
        event: WatchEvent,
        state: _TaskState,
        evaluation_id: str,
        response: BoundaryEvaluateResponse,
    ) -> None:
        """Emit a ``decision_emitted`` watch event to stdout.

        The output is a single JSON line when ``json_output`` is enabled,
        otherwise a human-readable log line.
        """
        run_id = state.run_id or ""
        policy_id = self._policy.policy.id if self._policy else None
        policy_version = self._policy.policy.version if self._policy else None

        if self._config.json_output:
            decision_event = {
                "schema_version": WATCH_EVENT_SCHEMA_VERSION,
                "event": "decision_emitted",
                "task_id": event.task_id,
                "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "step_id": getattr(event, "step_id", ""),
                "evaluation_id": evaluation_id,
                "decision": response.result.decision,
                "next_action": response.next_action,
                "score": response.result.score,
                "threshold": response.result.threshold,
                "assurance": getattr(response.result, "assurance", "standard"),
                "feedback": response.feedback[:500],
                "run_id": run_id,
                "policy_id": policy_id,
                "policy_version": policy_version,
            }
            line = json.dumps(decision_event, default=str)
            self._stdout.write(line + "\n")
            self._stdout.flush()
        else:
            logger.info(
                "watch: decision task=%s step=%s decision=%s action=%s score=%.4f threshold=%.4f",
                event.task_id,
                getattr(event, "step_id", ""),
                response.result.decision,
                response.next_action,
                response.result.score,
                response.result.threshold,
            )

    def _emit_error(self, msg: str) -> None:
        logger.error(msg)

    def _flush_incomplete_tasks(self) -> None:
        logger.info("watch: flushing %d incomplete tasks", len(self._tasks))
        for task_id, state in self._tasks.items():
            if not state.finished and state.run_id:
                try:
                    RunService.finish(RunFinishRequest(
                        run_id=state.run_id,
                        status="interrupted",
                        note="watch interrupted",
                        store=self._store,
                    ))
                except Exception as exc:
                    logger.warning("watch: failed to flush task=%s: %s", task_id, exc)


class WatchTransportError(WatchError):
    """A watch event could not be parsed or validated."""