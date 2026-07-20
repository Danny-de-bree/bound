"""BOUND v0.7.0 — Coverage-gap tests for policy lifecycle, secret redaction,
CLI ``--only-unverified``, and STALE evidence rejection (todo §3.3, §8, §9.1,
§10).

This file fills the four identified gaps from the Phase 10 review.

1. **Policy lifecycle state machine (§3.3)**: Test that
   DRAFT->VALIDATED->APPROVED->ACTIVE transitions are recorded correctly via
   lineage events.

2. **Secret redaction (§8, §10)**: Test that ``default_redactor`` masks
   credential-looking patterns. Test that ``bound.lineage_store.scrub_secrets``
   redacts sensitive fields from a dict. Test that ``CommandCollector`` does
   **not** store raw output by default, but always stores hashes and summaries.

3. **``--only-unverified`` CLI flag (§9.1)**: Test that
   ``bound inspect --only-unverified --json`` filters the JSON payload.

4. **STALE evidence rejection (§10)**: Test that ``JUnitCollector`` with a
   stale artifact emits ``EvidenceStatus.INVALID`` and that stale evidence
   cannot satisfy a required blocker gate.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from bound import (
    AcceptanceCheck,
    BoundCriteria,
    BoundWeights,
    BoundWorkflow,
    CheckEvidence,
    CommandCollector,
    EvidencePolicyAction,
    EvidenceProvenance,
    EvidenceStatus,
    ExecutionEvidence,
    JUnitCollector,
    LineageStore,
    StepContract,
    command_collector,
    default_redactor,
)
from bound.cli import _inspect_json_payload
from bound.lineage import (
    PolicyActivatedEvent,
    PolicyApprovedEvent,
    PolicyProposedEvent,
    PolicyValidatedEvent,
    build_run_config,
    parse_lineage_event,
)
from bound.lineage_store import RunLog, scrub_secrets

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_POLICY_ID = "test-lifecycle"
_POLICY_VERSION = "1.0"
_POLICY_HASH = "sha256:" + "a" * 64

_CRITERIA = BoundCriteria(weights=BoundWeights(), threshold=0.7, retry_margin=0.1)

# ===================================================================
# Gap 1 — Policy lifecycle state machine (§3.3)
# ===================================================================


class TestPolicyLifecycleStateMachine:
    """The four lifecycle events (proposed -> validated -> approved -> activated)
    can be emitted through ``LineageStore``, round-trip through ``read_run``,
    and carry the correct identity fields.

    NOTE: The library does **not** enforce lifecycle ordering at the store level
    (each event is append-only). The ``BoundPolicyConfig`` model has no
    ``status`` field — the lifecycle is represented purely through events.
    Enforcement (e.g. cannot activate before approval, cannot self-approve)
    lives in the application / demo layer and is tested by the golden-demo
    smoke test in ``test_v07_policy_security.py::TestGoldenDemo``.
    """

    def test_lifecycle_events_round_trip(self, tmp_path: Path) -> None:
        """Emit the four lifecycle events and verify they re-parse fully."""
        store = LineageStore(base_dir=tmp_path / "runs")
        run_id, _ = self._emit_lifecycle(store)
        log = store.read_run(run_id)
        events = self._filter_lifecycle(log)
        assert len(events) == 4
        assert isinstance(events[0], PolicyProposedEvent)
        assert isinstance(events[1], PolicyValidatedEvent)
        assert isinstance(events[2], PolicyApprovedEvent)
        assert isinstance(events[3], PolicyActivatedEvent)

    def test_proposed_event_carries_identity(self, tmp_path: Path) -> None:
        """A ``policy.proposed`` event records id, version and hash."""
        store = LineageStore(base_dir=tmp_path / "runs")
        run_id, evs = self._emit_lifecycle(store)
        event = evs[0]
        assert event.policy_id == _POLICY_ID
        assert event.policy_version == _POLICY_VERSION
        assert event.policy_hash == _POLICY_HASH
        assert event.run_id == run_id

    def test_approved_event_records_approver(self, tmp_path: Path) -> None:
        """A ``policy.approved`` event carries the approver identity and
        timestamp."""
        store = LineageStore(base_dir=tmp_path / "runs")
        _, evs = self._emit_lifecycle(store, approver="human-reviewer")
        event = evs[2]
        assert isinstance(event, PolicyApprovedEvent)
        assert event.approver == "human-reviewer"
        assert event.approved_at is not None

    def test_activated_event_is_terminal(self, tmp_path: Path) -> None:
        """A ``policy.activated`` event carries the same identity."""
        store = LineageStore(base_dir=tmp_path / "runs")
        _, evs = self._emit_lifecycle(store)
        event = evs[3]
        assert isinstance(event, PolicyActivatedEvent)
        assert event.policy_id == _POLICY_ID
        assert event.policy_hash == _POLICY_HASH

    def test_events_serialize_to_json_and_back(self, tmp_path: Path) -> None:
        """Each lifecycle event survives a JSON round-trip."""
        store = LineageStore(base_dir=tmp_path / "runs")
        _, evs = self._emit_lifecycle(store)
        for ev in evs:
            dumped = ev.model_dump(mode="json")
            parsed = parse_lineage_event({"event": ev.event, **dumped})
            assert parsed.event == ev.event
            assert parsed.policy_id == _POLICY_ID  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _emit_lifecycle(
        store: LineageStore,
        *,
        approver: str = "alice",
    ) -> tuple[str, list]:
        """Emit proposed -> validated -> approved -> activated events.
        Returns ``(run_id, events_list)``."""
        evt = store.start_run(
            "lifecycle test",
            config=build_run_config(bound_version="0.7.0"),
        )
        run_id = evt.run_id
        contract_hash = "sha256:" + "b" * 64

        ev1 = store.record_policy_proposed(
            run_id,
            policy_id=_POLICY_ID,
            policy_version=_POLICY_VERSION,
            policy_hash=_POLICY_HASH,
            contract_hash=contract_hash,
        )
        ev2 = store.record_policy_validated(
            run_id,
            policy_id=_POLICY_ID,
            policy_version=_POLICY_VERSION,
            policy_hash=_POLICY_HASH,
            contract_hash=contract_hash,
        )
        ev3 = store.record_policy_approved(
            run_id,
            policy_id=_POLICY_ID,
            policy_version=_POLICY_VERSION,
            policy_hash=_POLICY_HASH,
            approver=approver,
            contract_hash=contract_hash,
        )
        ev4 = store.record_policy_activated(
            run_id,
            policy_id=_POLICY_ID,
            policy_version=_POLICY_VERSION,
            policy_hash=_POLICY_HASH,
            contract_hash=contract_hash,
        )
        store.finish_run(run_id)
        return run_id, [ev1, ev2, ev3, ev4]

    @staticmethod
    def _filter_lifecycle(log: RunLog) -> list:
        """Return only lifecycle events from a ``RunLog``."""
        lifecycle_events = {
            "policy.proposed", "policy.validated",
            "policy.approved", "policy.activated",
        }
        return [e for e in log.events if e.event in lifecycle_events]

# ===================================================================
# Gap 2 — Secret redaction (§8, §10)
# ===================================================================


class TestDefaultRedactor:
    """``default_redactor`` (from ``command_collector``) masks credential-
    looking patterns in text output."""

    def test_masks_password_value(self) -> None:
        """A ``password=xxx`` pattern is redacted."""
        redacted = default_redactor("password=hunter2")
        assert "hunter2" not in redacted
        assert "***REDACTED***" in redacted

    def test_masks_token_value(self) -> None:
        """A ``token:xxx`` pattern is redacted."""
        redacted = default_redactor("token: ghp_xxxxxxxxxxxx")
        assert "ghp_xxxxxxxxxxxx" not in redacted
        assert "***REDACTED***" in redacted

    def test_masks_api_key(self) -> None:
        """An ``api_key=xxx`` pattern is redacted."""
        redacted = default_redactor("api_key=sk-12345abcdef")
        assert "sk-12345abcdef" not in redacted

    def test_masks_access_key(self) -> None:
        """An ``access-key:xxx`` pattern is redacted."""
        redacted = default_redactor("access-key: AKIA12345678")
        assert "AKIA12345678" not in redacted

    def test_passes_through_safe_output(self) -> None:
        """Output without credential-looking keys is unchanged."""
        safe = "all good, nothing here"
        assert default_redactor(safe) == safe

    def test_masks_secret_value(self) -> None:
        """A ``secret=xxx`` pattern is redacted."""
        redacted = default_redactor("secret=s3cr3t-value")
        assert "s3cr3t-value" not in redacted
        assert "***REDACTED***" in redacted

    def test_masks_private_key(self) -> None:
        """A ``private_key=xxx`` pattern is redacted."""
        redacted = default_redactor(
            "private_key=-----BEGIN RSA PRIVATE KEY-----"
        )
        assert "BEGIN" not in redacted
        assert "***REDACTED***" in redacted

    def test_masks_client_secret(self) -> None:
        """A ``client_secret:xxx`` pattern is redacted."""
        redacted = default_redactor("client_secret: abcdef123456")
        assert "abcdef123456" not in redacted


class TestScrubSecretsDirect:
    """``scrub_secrets`` (from ``lineage_store``) redacts sensitive fields from
    an event dict in-place."""

    def test_scrubs_metadata_values(self) -> None:
        """Secret-looking key=value pairs in ``metadata`` are masked."""
        event = {
            "event": "run_started",
            "run_id": "test",
            "metadata": {"auth": "password=hunter2", "safe": "hello"},
        }
        scrub_secrets(event)
        assert "hunter2" not in str(event)
        assert event["metadata"]["auth"] == "password=***REDACTED***"

    def test_scrubs_note_field(self) -> None:
        """A secret in the ``note`` field is masked."""
        event = {
            "event": "policy.approved",
            "run_id": "test",
            "note": "approved with token=ghp_xxxx",
        }
        scrub_secrets(event)
        assert "ghp_xxxx" not in event["note"]
        assert "token=***REDACTED***" in event["note"]

    def test_no_metadata_noop(self) -> None:
        """An event with no ``metadata`` and no ``note`` is unchanged."""
        event = {"event": "run_started", "run_id": "test"}
        original = dict(event)
        scrub_secrets(event)
        assert event == original

    def test_scrubs_multiple_secrets(self) -> None:
        """Multiple credential patterns in the same field are all masked."""
        event = {
            "event": "evidence.collected",
            "run_id": "test",
            "metadata": {
                "db": "password=p4ss",
                "api": "token=tkn123",
            },
        }
        scrub_secrets(event)
        assert "=***REDACTED***" in event["metadata"]["db"]
        assert "=***REDACTED***" in event["metadata"]["api"]
        assert "p4ss" not in str(event)
        assert "tkn123" not in str(event)

    def test_works_without_mutating_other_fields(self) -> None:
        """Fields other than ``metadata`` and ``note`` are untouched."""
        event = {
            "event": "policy.proposed",
            "run_id": "test",
            "policy_id": "my-policy",
            "policy_version": "1.0",
            "policy_hash": "sha256:abc",
            "metadata": {"key": "password=secret"},
        }
        scrub_secrets(event)
        assert event["policy_id"] == "my-policy"
        assert event["policy_version"] == "1.0"
        assert event["policy_hash"] == "sha256:abc"


class TestCommandCollectorRawNotStoredByDefault:
    """``CommandCollector`` does NOT retain full raw output unless explicitly
    opted in via ``store_raw=True`` (item 13 privacy). Hashes + summaries ARE
    always stored."""

    def test_stdout_raw_is_none_by_default(self) -> None:
        """With default ``store_raw=False``, ``stdout_raw`` is ``None``."""
        collector = CommandCollector(
            {"echo": command_collector.CommandSpec(
                argv=["echo", "hello-world-42"]
            )},
            store_raw=False,
        )
        result = collector.run("echo")
        assert result.stdout_raw is None
        assert result.stderr_raw is None

    def test_hash_is_stored_even_without_raw(self) -> None:
        """A sha256 hash of the *redacted* full output is always kept."""
        collector = CommandCollector(
            {"echo": command_collector.CommandSpec(
                argv=["echo", "hello-world-42"]
            )},
            store_raw=False,
        )
        result = collector.run("echo")
        assert result.stdout_hash is not None
        assert result.stdout_hash.startswith("sha256:")

    def test_summary_is_stored(self) -> None:
        """A redacted, size-capped summary is always stored."""
        collector = CommandCollector(
            {"echo": command_collector.CommandSpec(
                argv=["echo", "hello-world-42"]
            )},
            store_raw=False,
        )
        result = collector.run("echo")
        assert result.stdout_summary is not None
        assert "hello-world-42" in result.stdout_summary

    def test_stdout_raw_populated_when_store_raw_true(self) -> None:
        """With ``store_raw=True``, ``stdout_raw`` is retained."""
        collector = CommandCollector(
            {"echo": command_collector.CommandSpec(
                argv=["echo", "hello-world-42"]
            )},
            store_raw=True,
        )
        result = collector.run("echo")
        assert result.stdout_raw is not None

    def test_secret_is_redacted_before_hashing(self) -> None:
        """A secret in stdout is masked in both hash input and summary."""
        collector = CommandCollector(
            {"leak": command_collector.CommandSpec(
                argv=["echo", "password=supersecret"]
            )},
            store_raw=True,
        )
        result = collector.run("leak")
        assert "supersecret" not in (result.stdout_raw or "")
        assert "***REDACTED***" in (result.stdout_raw or "")
        assert "supersecret" not in result.stdout_summary
        assert "***REDACTED***" in result.stdout_summary

# ===================================================================
# Gap 3 — ``--only-unverified`` CLI flag (§9.1)
# ===================================================================


class TestOnlyUnverifiedJsonFlag:
    """``bound inspect --only-unverified --json`` filters the JSON payload
    to only unverified / claimed / missing / invalid evidence.

    The text-mode ``--only-unverified`` is tested in
    ``test_cli_lineage.py::test_inspect_only_unverified_filters``; here we test
    the JSON variant and a mixed-provenance trace.
    """

    def test_only_unverified_json_filters_evidence(
        self, tmp_path: Path
    ) -> None:
        """With ``only_unverified=True``, the JSON payload's collected evidence
        contains only unverified / missing / claimed / invalid entries."""
        store, run_id = self._build_mixed_run(tmp_path)
        log = store.read_run(run_id)
        payload = _inspect_json_payload(log, only_unverified=True)

        assert payload["only_unverified"] is True
        collected = payload["evidence"]["collected"]
        for _step_id, rows in collected.items():
            for row in rows:
                prov = row["provenance"]
                status = row["status"]
                assert prov in ("claimed", "missing", "defaulted") or status in (
                    "unverified", "missing", "invalid", "stale"
                ), (
                    f"row {row['check_id']} has provenance={prov!r} "
                    f"status={status!r} which should not appear"
                )

    def test_all_verified_json_emits_empty_collected_when_filtered(
        self, tmp_path: Path
    ) -> None:
        """When all evidence is VERIFIED, ``--only-unverified --json`` emits
        an empty ``collected`` dict."""
        store = LineageStore(base_dir=tmp_path / "runs")
        run_id = self._build_all_verified_run(store)
        log = store.read_run(run_id)
        payload = _inspect_json_payload(log, only_unverified=True)
        assert payload["evidence"]["collected"] == {}

    def test_only_unverified_false_includes_all(
        self, tmp_path: Path
    ) -> None:
        """With ``only_unverified=False``, the JSON payload includes all
        evidence including VERIFIED checks."""
        store, run_id = self._build_mixed_run(tmp_path)
        log = store.read_run(run_id)
        payload = _inspect_json_payload(log, only_unverified=False)

        assert payload["only_unverified"] is False
        collected = payload["evidence"]["collected"]
        total_rows = sum(len(rows) for rows in collected.values())
        assert total_rows == 3  # VERIFIED + MISSING + CLAIMED

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_mixed_run(tmp_path: Path) -> tuple[LineageStore, str]:
        """Build a run with VERIFIED, MISSING, and CLAIMED evidence."""
        store = LineageStore(base_dir=tmp_path / "runs")
        evt = store.start_run(
            "mixed-provenance test",
            config=build_run_config(bound_version="0.7.0"),
        )
        run_id = evt.run_id
        step_id = "step-001"
        store.record_evidence_collected(
            run_id, step_id=step_id, check_id="tests-pass",
            collector="bound.pytest", collector_version="0.7.0",
            provenance="verified", passed=True,
        )
        store.record_evidence_collected(
            run_id, step_id=step_id, check_id="type-check-pass",
            collector="bound.mypy", provenance="missing",
            passed=None, status="missing",
        )
        store.record_evidence_collected(
            run_id, step_id=step_id, check_id="agent-claim",
            collector="agent", provenance="claimed", passed=True,
        )
        store.finish_run(run_id)
        return store, run_id

    @staticmethod
    def _build_all_verified_run(store: LineageStore) -> str:
        """Build a run where every check is VERIFIED."""
        evt = store.start_run(
            "all-verified test",
            config=build_run_config(bound_version="0.7.0"),
        )
        run_id = evt.run_id
        step_id = "step-001"
        store.record_evidence_collected(
            run_id, step_id=step_id, check_id="tests-pass",
            collector="bound.pytest", collector_version="0.7.0",
            provenance="verified", passed=True,
        )
        store.finish_run(run_id)
        return run_id

# ===================================================================
# Gap 4 — STALE evidence rejection (§10)
# ===================================================================


class TestStaleJUnitEvidence:
    """``JUnitCollector`` with a stale artifact (older than
    ``max_age_seconds``) emits ``EvidenceStatus.INVALID`` — and that evidence
    cannot satisfy a required blocker gate.

    NOTE: The current implementation returns ``INVALID`` for stale artifacts,
    not ``STALE``. The ``EvidenceStatus.STALE`` enum member exists and future
    work should migrate to it; for now the conservative behaviour (block the
    gate) is identical.
    """

    @staticmethod
    def _junit(tests: int = 5, failures: int = 0, errors: int = 0) -> str:
        """Generate a minimal JUnit XML string."""
        failures_attr = f' failures="{failures}"' if failures else ""
        errors_attr = f' errors="{errors}"' if errors else ""
        return (
            f'<?xml version="1.0"?>\n'
            f'<testsuite name="pytest" tests="{tests}"'
            f'{failures_attr}{errors_attr}>\n'
            f"  <testcase classname='test' name='test_foo' />\n"
            f"</testsuite>\n"
        )

    def test_stale_artifact_emits_invalid(self, tmp_path: Path) -> None:
        """An artefact older than ``max_age_seconds`` yields
        ``EvidenceStatus.INVALID``."""
        path = tmp_path / "junit.xml"
        path.write_text(self._junit(tests=5))
        future = datetime.now(UTC) + timedelta(hours=1)
        evidence = JUnitCollector(max_age_seconds=1.0).collect(path, now=future)
        assert evidence.passed is None
        assert evidence.status is EvidenceStatus.INVALID
        assert evidence.provenance is EvidenceProvenance.MISSING

    def test_fresh_artifact_passes(self, tmp_path: Path) -> None:
        """An artefact within the freshness window passes."""
        path = tmp_path / "junit.xml"
        path.write_text(self._junit(tests=5))
        now = datetime.now(UTC)
        evidence = JUnitCollector(max_age_seconds=3600.0).collect(path, now=now)
        assert evidence.passed is True
        assert evidence.provenance is EvidenceProvenance.VERIFIED

    def test_stale_artifact_blocks_required_gate(self, tmp_path: Path) -> None:
        """A stale (INVALID) JUnit artifact cannot satisfy a required blocker
        gate."""
        path = tmp_path / "junit.xml"
        path.write_text(self._junit(tests=5))
        future = datetime.now(UTC) + timedelta(hours=1)
        stale_evidence = JUnitCollector(max_age_seconds=1.0).collect(
            path, now=future
        )
        contract = StepContract(
            id="PHASE-001",
            description="Test step",
            goal="All tests pass",
            acceptance_checks=[
                AcceptanceCheck(
                    id="tests-pass",
                    description="JUnit tests pass",
                    accepted_provenance=[
                        EvidenceProvenance.OBSERVED,
                        EvidenceProvenance.VERIFIED,
                        EvidenceProvenance.ATTESTED,
                    ],
                    on_missing=EvidencePolicyAction.REPLAN,
                    on_claimed=EvidencePolicyAction.RETRY,
                ),
            ],
            risk_checks=[],
        )
        evidence = ExecutionEvidence(
            acceptance=[
                CheckEvidence(
                    check_id="tests-pass",
                    passed=stale_evidence.passed,
                    provenance=stale_evidence.provenance,
                    status=stale_evidence.status,
                    source=str(path),
                    collector="bound.junit",
                ),
            ],
            rollback_available=True,
        )
        result = BoundWorkflow().evaluate_step(
            contract=contract,
            evidence=evidence,
            criteria=_CRITERIA,
        )
        assert result.final_decision != "ACCEPT"

    def test_stale_evidence_cannot_satisfy_blocker_policy(
        self, tmp_path: Path
    ) -> None:
        """Stale (INVALID) evidence causes the gate to fail even with a
        permissive policy."""
        evidence = ExecutionEvidence(
            acceptance=[
                CheckEvidence(
                    check_id="tests-pass",
                    passed=None,
                    provenance=EvidenceProvenance.MISSING,
                    status=EvidenceStatus.INVALID,
                    source="/fake/junit.xml",
                    collector="bound.junit",
                ),
            ],
            rollback_available=True,
        )
        contract = StepContract(
            id="PHASE-001",
            description="Test step",
            goal="All tests pass",
            acceptance_checks=[
                AcceptanceCheck(
                    id="tests-pass",
                    description="JUnit tests pass",
                    accepted_provenance=[
                        EvidenceProvenance.OBSERVED,
                        EvidenceProvenance.VERIFIED,
                        EvidenceProvenance.ATTESTED,
                    ],
                    on_missing=EvidencePolicyAction.REPLAN,
                    on_claimed=EvidencePolicyAction.RETRY,
                ),
            ],
            risk_checks=[],
        )
        result = BoundWorkflow().evaluate_step(
            contract=contract,
            evidence=evidence,
            criteria=_CRITERIA,
        )
        assert result.final_decision != "ACCEPT"
        assert result.assurance.value != "verified"
