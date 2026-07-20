from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from bound.evidence import (
    CheckEvidence,
    EvidenceCollector,
    EvidenceMetric,
    EvidenceProvenance,
    EvidenceStatus,
    ExecutionEvidence,
    migrate_legacy_execution_evidence,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_PASSING_ACCEPT = CheckEvidence(
    check_id="acc-1",
    passed=True,
    source="pytest::tests/test_evidence.py",
    details="all green",
)
_FAILING_ACCEPT = CheckEvidence(
    check_id="acc-1",
    passed=False,
    source="pytest::tests/test_evidence.py",
    details="one assertion failed",
)
_RISK_PASSED = CheckEvidence(
    check_id="risk-secrets",
    passed=True,
    source="gitleaks",
)


# ---------------------------------------------------------------------------
# CheckEvidence
# ---------------------------------------------------------------------------


def test_check_evidence_valid_with_details() -> None:
    """A fully-specified CheckEvidence validates and round-trips its fields."""
    check = CheckEvidence(
        check_id="acc-1",
        passed=True,
        source="pytest",
        details="all green",
    )
    assert check.check_id == "acc-1"
    assert check.passed is True
    assert check.source == "pytest"
    assert check.details == "all green"


def test_check_evidence_details_defaults_to_none() -> None:
    """``details`` is optional; absence records 'no elaboration', not an error."""
    check = CheckEvidence(check_id="acc-1", passed=False, source="pytest")
    assert check.details is None


def test_check_evidence_rejects_unknown_fields() -> None:
    """``extra="forbid"`` keeps the record auditable — stray keys are a bug."""
    with pytest.raises(ValidationError):
        CheckEvidence(  # type: ignore[call-arg]
            check_id="acc-1",
            passed=True,
            source="pytest",
            severity="high",
        )


def test_check_evidence_provenance_defaults_to_missing() -> None:
    """Evidence without an explicit provenance defaults to MISSING.

    This is the v0.7 safety default: evidence that does not declare how it was
    obtained is never accidentally trusted as verified. ``source`` records *where*
    it came from; provenance records *how trustworthy* that is.
    """
    check = CheckEvidence(check_id="acc-1", passed=True, source="pytest")
    assert check.provenance is EvidenceProvenance.MISSING
    # New provenance/collector fields default to None / absent.
    assert check.collector is None
    assert check.collector_version is None
    assert check.observed_at is None
    assert check.artifact_hash is None
    assert check.raw_artifact_ref is None
    assert check.status is None


def test_check_evidence_carries_full_provenance() -> None:
    """A fully-provenanced CheckEvidence round-trips its trust metadata.

    Mirrors the todo.md example: a verified JUnit observation with a collector,
    collector version, observed_at timestamp, and an artifact hash.
    """
    observed = datetime(2026, 7, 19, 12, 30, 0, tzinfo=UTC)
    check = CheckEvidence(
        check_id="tests-pass",
        passed=True,
        source="junit.xml",
        provenance=EvidenceProvenance.VERIFIED,
        collector="bound.junit",
        collector_version="0.7.0",
        observed_at=observed,
        artifact_hash="sha256:deadbeef",
        raw_artifact_ref="runs/42/junit.xml",
    )
    assert check.provenance is EvidenceProvenance.VERIFIED
    assert check.collector == "bound.junit"
    assert check.collector_version == "0.7.0"
    assert check.observed_at == observed
    assert check.artifact_hash == "sha256:deadbeef"
    assert check.raw_artifact_ref == "runs/42/junit.xml"


def test_check_evidence_passed_may_be_undetermined() -> None:
    """``passed`` is ``bool | None``: ``None`` means the outcome is undetermined.

    v0.7 separates pass/fail from verifiability. A check whose outcome could not
    be determined records ``passed=None`` (optionally paired with a
    :class:`EvidenceStatus`); the model must not force a pass/fail guess.
    """
    check = CheckEvidence(
        check_id="flakey",
        source="pytest",
        passed=None,
        status=EvidenceStatus.UNVERIFIED,
    )
    assert check.passed is None
    assert check.status is EvidenceStatus.UNVERIFIED


def test_check_evidence_rejects_naive_observed_at() -> None:
    """A timezone-unaware (naive) ``observed_at`` is rejected.

    A timestamp without timezone is ambiguous as audit evidence; BOUND
    standardises on timezone-aware datetimes. ``None`` (unknown) is allowed.
    """
    naive = datetime(2026, 7, 19, 12, 30, 0)  # no tzinfo
    with pytest.raises(ValidationError):
        CheckEvidence(check_id="x", passed=True, source="s", observed_at=naive)


def test_check_evidence_accepts_string_provenance_from_json() -> None:
    """Provenance/status coerce from strings (e.g. deserialised JSON traces).

    Ensures schema-2.0 traces loaded from JSON round-trip into the enums rather
    than being rejected, while unknown values are still rejected.
    """
    check = CheckEvidence.model_validate(
        {
            "check_id": "x",
            "passed": True,
            "source": "s",
            "provenance": "verified",
            "status": "failed",
        }
    )
    assert check.provenance is EvidenceProvenance.VERIFIED
    assert check.status is EvidenceStatus.FAILED

    with pytest.raises(ValidationError):
        CheckEvidence.model_validate(
            {"check_id": "x", "passed": True, "source": "s", "provenance": "imagined"}
        )


def test_evidence_metric_records_value_and_provenance() -> None:
    """An EvidenceMetric pairs a raw value with trust provenance."""
    metric = EvidenceMetric(
        value=12, provenance=EvidenceProvenance.OBSERVED, source="cline.tool_events"
    )
    assert metric.value == 12
    assert metric.provenance is EvidenceProvenance.OBSERVED
    assert metric.source == "cline.tool_events"
    assert metric.collector is None


def test_evidence_metric_defaults_provenance_to_missing() -> None:
    """An EvidenceMetric without explicit provenance defaults to MISSING.

    Mirrors CheckEvidence: a value supplied without provenance is never
    accidentally treated as verified.
    """
    metric = EvidenceMetric(value=None)
    assert metric.value is None
    assert metric.provenance is EvidenceProvenance.MISSING


def test_evidence_metric_rejects_unknown_fields() -> None:
    """``extra="forbid"`` keeps the metric auditable."""
    with pytest.raises(ValidationError):
        EvidenceMetric(  # type: ignore[call-arg]
            value=1, provenance=EvidenceProvenance.OBSERVED, surprise="x"
        )


def test_provenance_enum_values() -> None:
    """The trust-provenance vocabulary matches the todo.md spec (lowercased)."""
    assert {p.value for p in EvidenceProvenance} == {
        "observed",
        "verified",
        "attested",
        "evaluated",
        "claimed",
        "defaulted",
        "missing",
    }


def test_evidence_status_enum_values() -> None:
    """The status vocabulary separates failure from unverifiable (todo 1.2).

    v0.7.0 canonicalises the vocabulary to ``PASSED`` / ``FAILED`` / ``MISSING``
    / ``INVALID`` / ``STALE``. The legacy ``UNVERIFIED`` member is retained as a
    deprecated alias so existing collectors and schema-2.0 traces keep loading.
    """
    assert {s.value for s in EvidenceStatus} == {
        "passed",
        "failed",
        "missing",
        "invalid",
        "stale",
        "unverified",
    }


def test_evidence_status_passed_and_stale_members() -> None:
    """New v0.7.0 members ``PASSED`` and ``STALE`` round-trip as strings."""
    assert EvidenceStatus.PASSED == "passed"
    assert EvidenceStatus.STALE == "stale"
    assert EvidenceStatus.PASSED is not EvidenceStatus.FAILED



# ---------------------------------------------------------------------------
# ExecutionEvidence — valid / empty / faithful recording
# ---------------------------------------------------------------------------


def test_execution_evidence_valid_full() -> None:
    """A fully-populated ExecutionEvidence validates and round-trips.

    This is the happy path: acceptance + risk outcomes, artifacts, and full
    telemetry all present and in range. The evidence layer must accept it
    unchanged so the evaluator receives exactly what was observed.

    v0.7: telemetry values are :class:`EvidenceMetric` instances carrying trust
    provenance. A measured value is recorded with its provenance (here OBSERVED
    for retry/tool/token/runtime); a ``None`` metric still means MISSING, never
    a silent zero.
    """
    evidence = ExecutionEvidence(
        acceptance=[_PASSING_ACCEPT, _RISK_PASSED],
        risks=[CheckEvidence(check_id="risk-1", passed=False, source="bandit")],
        produced_artifacts=["src/auth.py", "tests/test_auth.py"],
        unexpected_artifacts=["debug.log"],
        retry_count=EvidenceMetric(
            value=2, provenance=EvidenceProvenance.OBSERVED, source="harness.retries"
        ),
        tool_call_count=EvidenceMetric(
            value=17,
            provenance=EvidenceProvenance.OBSERVED,
            source="cline.tool_events",
        ),
        token_usage=EvidenceMetric(
            value=4096, provenance=EvidenceProvenance.OBSERVED, source="token_meter"
        ),
        runtime_seconds=EvidenceMetric(
            value=12.5, provenance=EvidenceProvenance.OBSERVED, source="wall_clock"
        ),
        rollback_available=True,
    )
    assert len(evidence.acceptance) == 2
    assert evidence.risks[0].passed is False
    assert evidence.produced_artifacts == ["src/auth.py", "tests/test_auth.py"]
    assert evidence.unexpected_artifacts == ["debug.log"]
    assert evidence.retry_count is not None
    assert evidence.retry_count.value == 2
    assert evidence.retry_count.provenance is EvidenceProvenance.OBSERVED
    assert evidence.tool_call_count is not None
    assert evidence.tool_call_count.value == 17
    assert evidence.token_usage is not None
    assert evidence.token_usage.value == 4096
    assert evidence.runtime_seconds is not None
    assert evidence.runtime_seconds.value == 12.5
    assert evidence.rollback_available is True


def test_execution_evidence_empty_is_valid() -> None:
    """Empty evidence is valid: a step with nothing recorded is still evidence.

    This matters because a collector that observed nothing must still return a
    well-formed record. The evaluator — not the model — decides that missing
    required evidence is a failure; here we only assert the record is accepted
    with all defaults, so the evaluator can score it conservatively.
    """
    evidence = ExecutionEvidence()
    assert evidence.acceptance == []
    assert evidence.risks == []
    assert evidence.produced_artifacts == []
    assert evidence.unexpected_artifacts == []
    # Missing telemetry is None (MISSING), never a silent 0: absence of a
    # measurement is distinct from a measured zero.
    assert evidence.retry_count is None
    assert evidence.tool_call_count is None
    assert evidence.token_usage is None
    assert evidence.runtime_seconds is None
    assert evidence.rollback_available is None


def test_unknown_check_ids_are_allowed() -> None:
    """A check_id absent from any contract is valid evidence.

    The collector records what it sees, including checks the contract did not
    declare. Rejecting unknown ids here would hide information from the
    evaluator; reconciliation against the contract (and the 'missing required
    evidence = failure' rule) is the evaluator's responsibility, not the
    model's.
    """
    evidence = ExecutionEvidence(
        acceptance=[
            CheckEvidence(check_id="not-in-any-contract", passed=True, source="ad-hoc"),
        ],
    )
    assert evidence.acceptance[0].check_id == "not-in-any-contract"


def test_duplicate_evidence_is_allowed() -> None:
    """Multiple CheckEvidence for the same check_id is valid.

    Different sources may report the same check independently (e.g. a fast and a
    slow test run). The evidence layer records them all faithfully; deduplication
    is the evaluator's job. Silently collapsing duplicates here would lose
    provenance and could mask disagreements between sources.
    """
    evidence = ExecutionEvidence(
        acceptance=[
            CheckEvidence(check_id="acc-1", passed=True, source="fast-run"),
            CheckEvidence(check_id="acc-1", passed=False, source="slow-run"),
        ],
    )
    assert [c.check_id for c in evidence.acceptance] == ["acc-1", "acc-1"]
    assert [c.passed for c in evidence.acceptance] == [True, False]


def test_failed_required_check_is_valid_evidence() -> None:
    """A failing check (``passed=False``) is valid, faithfully recorded evidence.

    This is the core integrity invariant: the collector must never silently flip
    a failure to a pass. A failed required check is legitimate evidence that the
    evaluator will score — it is not a validation error in the record.
    """
    evidence = ExecutionEvidence(acceptance=[_FAILING_ACCEPT])
    assert evidence.acceptance[0].passed is False


# ---------------------------------------------------------------------------
# Telemetry: EvidenceMetric, missing-vs-zero, legacy migration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("retry_count", 0),
        ("tool_call_count", 0),
        ("token_usage", 0),
        ("runtime_seconds", 0.0),
    ],
)
def test_measured_zero_is_recorded_not_silently_dropped(field: str, value: float) -> None:
    """A measured zero is a real observation, recorded with its provenance.

    v0.7 records telemetry as :class:`EvidenceMetric`. A measured zero
    (``value=0``, OBSERVED) is a genuine observation ("no retries happened") and
    must be preserved — it is never coerced to a missing signal and never
    silently dropped. This is the lower-bound counterpart to the missing-vs-zero
    distinction: the model keeps both faithfully.
    """
    evidence = ExecutionEvidence(
        **{field: EvidenceMetric(value=value, provenance=EvidenceProvenance.OBSERVED)}
    )
    metric = getattr(evidence, field)
    assert metric is not None
    assert metric.value == value
    assert metric.provenance is EvidenceProvenance.OBSERVED


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("retry_count", -1),
        ("tool_call_count", -1),
        ("token_usage", -1),
        ("runtime_seconds", -0.5),
    ],
)
def test_legacy_bare_number_is_coerced_to_missing_metric(
    field: str, value: float
) -> None:
    """Legacy bare-number telemetry coerces into an EvidenceMetric (provenance MISSING).

    Old (schema < 2.0) traces supplied telemetry as bare ints/floats. v0.7 wraps
    such values in ``EvidenceMetric(value=…, provenance=MISSING)``: the value is
    preserved (never silently flipped) but the provenance is MISSING because a
    legacy trace cannot retroactively prove independent observation — it is
    **never** upgraded to OBSERVED/VERIFIED. Range/sanity guards (e.g. rejecting
    impossible negatives) now live in the evaluator layer, not the data model,
    so bad values are recorded faithfully for the evaluator to flag rather than
    hidden by a validation error.
    """
    evidence = ExecutionEvidence(**{field: value})
    metric = getattr(evidence, field)
    assert metric is not None
    assert metric.value == value
    assert metric.provenance is EvidenceProvenance.MISSING


def test_legacy_zero_is_distinct_from_missing() -> None:
    """A legacy ``retry_count=0`` is MISSING-zero, not an observed zero.

    This pins the central v0.7 honesty rule for telemetry: a legacy trace that
    recorded ``retry_count=0`` cannot claim the retries were *observed* to be
    zero, so the migrated metric carries ``provenance=MISSING`` — distinct from
    an explicit ``EvidenceMetric(value=0, provenance=OBSERVED)``.
    """
    legacy = ExecutionEvidence(retry_count=0)
    assert legacy.retry_count is not None
    assert legacy.retry_count.value == 0
    assert legacy.retry_count.provenance is EvidenceProvenance.MISSING

    observed = ExecutionEvidence(
        retry_count=EvidenceMetric(value=0, provenance=EvidenceProvenance.OBSERVED)
    )
    assert observed.retry_count is not None
    assert observed.retry_count.value == 0
    assert observed.retry_count.provenance is EvidenceProvenance.OBSERVED


def test_migrate_legacy_execution_evidence_wraps_bare_numbers() -> None:
    """The migration helper wraps bare telemetry, leaving metrics/dicts untouched.

    Explicit pre-wrapped :class:`EvidenceMetric` values and metric-shaped dicts
    are passed through; bare ints/floats are wrapped with MISSING provenance;
    ``None`` stays ``None``; non-telemetry keys are untouched.
    """
    migrated = migrate_legacy_execution_evidence(
        {
            "retry_count": 3,
            "tool_call_count": EvidenceMetric(
                value=9, provenance=EvidenceProvenance.OBSERVED
            ),
            "token_usage": None,
            "runtime_seconds": {"value": 5.0, "provenance": "observed"},
            "rollback_available": True,
        }
    )
    assert isinstance(migrated["retry_count"], EvidenceMetric)
    assert migrated["retry_count"].value == 3
    assert migrated["retry_count"].provenance is EvidenceProvenance.MISSING
    # Pre-wrapped metric is left as-is.
    assert migrated["tool_call_count"].provenance is EvidenceProvenance.OBSERVED
    # None stays None (missing).
    assert migrated["token_usage"] is None
    # Metric-shaped dict is left for Pydantic to validate (not pre-wrapped).
    assert isinstance(migrated["runtime_seconds"], dict)
    # Non-telemetry keys untouched.
    assert migrated["rollback_available"] is True


def test_token_usage_and_runtime_accept_none() -> None:
    """Unmeasured telemetry is ``None``, distinct from a measured zero.

    This distinction matters for the cost dimension: ``None`` means 'we do not
    know' (score conservatively), while ``0`` means 'we measured zero'. The
    model must preserve the difference rather than coercing one to the other.
    """
    evidence = ExecutionEvidence(token_usage=None, runtime_seconds=None)
    assert evidence.token_usage is None
    assert evidence.runtime_seconds is None


# ---------------------------------------------------------------------------
# EvidenceCollector Protocol — structural typing
# ---------------------------------------------------------------------------


class _AdHocCollector:
    """Minimal collector used only to prove the Protocol is structural.

    It does not subclass :class:`EvidenceCollector`; it merely exposes a
    ``collect`` method. ``runtime_checkable`` checks method presence only, so
    this satisfies the Protocol with zero coupling to BOUND internals — the
    property future environment adapters (Cline, CI, ...) will rely on.
    """

    def collect(
        self,
        *,
        contract: object,
        execution: object,
    ) -> ExecutionEvidence:
        """Return empty evidence; the body is irrelevant to the structural check."""
        return ExecutionEvidence()


def test_ad_hoc_collector_satisfies_protocol() -> None:
    """A bare class with ``collect`` satisfies the EvidenceCollector Protocol."""
    assert isinstance(_AdHocCollector(), EvidenceCollector)


def test_object_without_collect_does_not_satisfy_protocol() -> None:
    """An object lacking ``collect`` must NOT satisfy the Protocol.

    Guards the negative side of structural typing: the seam is opt-in via method
    presence, so a random object is not silently treated as a collector.
    """
    assert not isinstance(object(), EvidenceCollector)


def test_ad_hoc_collector_collect_returns_evidence() -> None:
    """A structural collector's ``collect`` returns valid ExecutionEvidence.

    Confirms the Protocol's return-type expectation is honoured end-to-end by a
    duck-typed implementation, with ``execution`` accepted as a plain object.
    """
    collector = _AdHocCollector()
    evidence = collector.collect(contract=object(), execution="some-handle")
    assert isinstance(evidence, ExecutionEvidence)
    assert evidence.acceptance == []



def test_execution_evidence_rejects_unknown_fields() -> None:
    """``extra="forbid"`` prevents silent schema drift on the aggregate record."""
    with pytest.raises(ValidationError):
        ExecutionEvidence(  # type: ignore[call-arg]
            acceptance=[],
            acceptance_ratio=0.5,
        )
