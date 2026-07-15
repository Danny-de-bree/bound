"""Unit tests for the BOUND evidence models and EvidenceCollector (Phases 5–6).

These tests pin down the contracts the evidence layer must hold:

1. :class:`CheckEvidence` and :class:`ExecutionEvidence` are plain observation
   records — they record *what was seen*, never what it *means* for BOUND. So a
   failing required check (``passed=False``), an unknown check id, and duplicate
   evidence for the same id are all *valid*: the collector is faithful, and
   reconciliation/deduplication is the evaluator's job, not the model's.
2. Empty evidence is valid: a step with no checks recorded is still legitimate
   evidence (the evaluator scores it conservatively, never optimistically).
3. Numeric telemetry is range-validated (``ge=0``): negative retries, tool calls,
   tokens, or runtime are rejected because they are impossible observations.
4. ``extra="forbid"`` keeps the records auditable — stray fields are a modelling
   bug, not a silent extension point.
5. :class:`EvidenceCollector` is a structural ``runtime_checkable`` Protocol, so
   any object exposing ``collect`` satisfies it with no inheritance — this is the
   seam that lets future environment adapters (Cline, CI, ...) plug in without
   the core importing them.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bound.evidence import CheckEvidence, EvidenceCollector, ExecutionEvidence

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



# ---------------------------------------------------------------------------
# ExecutionEvidence — valid / empty / faithful recording
# ---------------------------------------------------------------------------


def test_execution_evidence_valid_full() -> None:
    """A fully-populated ExecutionEvidence validates and round-trips.

    This is the happy path: acceptance + risk outcomes, artifacts, and full
    telemetry all present and in range. The evidence layer must accept it
    unchanged so the evaluator receives exactly what was observed.
    """
    evidence = ExecutionEvidence(
        acceptance=[_PASSING_ACCEPT, _RISK_PASSED],
        risks=[CheckEvidence(check_id="risk-1", passed=False, source="bandit")],
        produced_artifacts=["src/auth.py", "tests/test_auth.py"],
        unexpected_artifacts=["debug.log"],
        retry_count=2,
        tool_call_count=17,
        token_usage=4096,
        runtime_seconds=12.5,
        rollback_available=True,
    )
    assert len(evidence.acceptance) == 2
    assert evidence.risks[0].passed is False
    assert evidence.produced_artifacts == ["src/auth.py", "tests/test_auth.py"]
    assert evidence.unexpected_artifacts == ["debug.log"]
    assert evidence.retry_count == 2
    assert evidence.tool_call_count == 17
    assert evidence.token_usage == 4096
    assert evidence.runtime_seconds == 12.5
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
    assert evidence.retry_count == 0
    assert evidence.tool_call_count == 0
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
# Numeric range validation (ge=0)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("retry_count", -1),
        ("tool_call_count", -1),
        ("token_usage", -1),
        ("runtime_seconds", -0.5),
    ],
)
def test_negative_numeric_fields_rejected(field: str, value: float) -> None:
    """Negative telemetry is impossible and must be rejected.

    Retries, tool calls, tokens, and runtime are all non-negative quantities;
    a negative value means the collector mis-recorded, and letting it through
    would corrupt the deterministic cost/risk math downstream. ``ge=0`` rejects
    it at the model boundary.
    """
    with pytest.raises(ValidationError):
        ExecutionEvidence(**{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("retry_count", 0),
        ("tool_call_count", 0),
        ("token_usage", 0),
        ("runtime_seconds", 0.0),
    ],
)
def test_zero_numeric_fields_are_valid(field: str, value: float) -> None:
    """Zero is the inclusive lower bound and must be accepted.

    A zero is a real observation (no retries, no tokens, instant runtime), not
    an error. Pinning the boundary keeps ``ge=0`` (not ``gt=0``) intentional.
    """
    evidence = ExecutionEvidence(**{field: value})
    assert getattr(evidence, field) == value


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
