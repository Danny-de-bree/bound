from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from bound.contracts import StepContract
from bound.evidence import (
    CheckEvidence,
    EvidenceMetric,
    EvidenceProvenance,
    EvidenceStatus,
    ExecutionEvidence,
)
from bound.integration import NextAction
from bound.lineage import RunConfigSnapshot
from bound.models import Decision, DecisionAssurance, EvaluationResult, ScoreEvidence

__all__ = [
    "DecisionHistoryEntry",
    "RawCommandRecord",
    "RunTrace",
    "render_from_trace",
]

#: Provenance that counts as *independently verified* for the report's coverage
#: metric and provenance breakdown — produced by a BOUND-controlled collector
#: or a trusted attestation, never agent self-report (CLAIMED).
_REPORT_INDEPENDENT: frozenset[EvidenceProvenance] = frozenset(
    {EvidenceProvenance.OBSERVED, EvidenceProvenance.VERIFIED, EvidenceProvenance.ATTESTED}
)

#: Evidence statuses that mean a check could not be independently confirmed —
#: a collector crash, a stale artefact, or an undetermined outcome.
_UNVERIFIABLE_STATUS: frozenset[EvidenceStatus] = frozenset(
    {EvidenceStatus.INVALID, EvidenceStatus.UNVERIFIED, EvidenceStatus.MISSING}
)

#: Provenance ranked by trust strength (higher = more trustworthy), used to pick
#: the strongest provenance backing a score dimension.
_PROVENANCE_STRENGTH: dict[EvidenceProvenance, int] = {
    EvidenceProvenance.VERIFIED: 60,
    EvidenceProvenance.OBSERVED: 50,
    EvidenceProvenance.ATTESTED: 40,
    EvidenceProvenance.EVALUATED: 30,
    EvidenceProvenance.CLAIMED: 20,
    EvidenceProvenance.DEFAULTED: 10,
    EvidenceProvenance.MISSING: 0,
}

#: Display label for each BOUND score dimension, in report order.
_DIMENSION_LABELS: dict[str, str] = {
    "acceptance": "Acceptance (A)",
    "influence": "Influence (I)",
    "risk": "Risk (R)",
    "cost": "Cost (C)",
}


class RawCommandRecord(BaseModel):
    """Verbatim record of one verification subprocess (command + captured output).

    A reference integration runs real verification commands (``uv run pytest``,
    ``git status``) and stores their exact stdout / stderr / exit code alongside
    the structured :class:`ExecutionEvidence`, so a reader can audit the raw
    bytes that produced the structured evidence without a second, separate run.
    This model is the typed shape of one such record.

    Attributes:
        command: The command string that was executed.
        returncode: The process exit code (``0`` for success).
        stdout: Captured standard output, verbatim. Defaults to empty.
        stderr: Captured standard error, verbatim. Defaults to empty.
    """

    model_config = ConfigDict(extra="forbid")

    command: str
    returncode: int
    stdout: str = ""
    stderr: str = ""


class DecisionHistoryEntry(BaseModel):
    """One entry in a step's decision lineage (preserves retries and replans).

    BOUND's lineage rule is that replans append a ``-R<N>`` suffix rather than
    replacing the id, and that every evaluation attempt is recorded so history is
    never rewritten. This entry captures one attempt's decision and the control
    action BOUND mapped it to, so a report can show the full
    ``PHASE-001 → PHASE-001-R1 → … → ACCEPT`` trajectory when it came from a real
    run (and an empty-but-explicit history when it did not).

    Attributes:
        step_id: The stable step / contract id for this attempt (e.g.
            ``PHASE-001`` or ``PHASE-001-R1`` after a replan).
        attempt: One-based attempt number for this step.
        decision: The deterministic BOUND decision (``ACCEPT`` / ``RETRY`` /
            ``REPLAN`` / ``ROLLBACK``).
        next_action: The mapped control action (``continue`` / ``retry`` /
            ``replan`` / ``rollback``).
        note: Optional free-text context (e.g. ``"first evaluation; no replan"``).
    """

    model_config = ConfigDict(extra="forbid")

    step_id: str
    attempt: int = Field(ge=1)
    decision: Decision
    next_action: NextAction
    note: str | None = None


class RunTrace(BaseModel):
    """Machine-readable record of one real BOUND step evaluation (Phase 9).

    A :class:`RunTrace` is the single source of truth for a run: it carries the
    contract, the observed evidence, BOUND's deterministic evaluation, the
    mapped control action, the verbatim verification-command output, and the
    decision lineage. It serializes to JSON via ``model_dump_json`` and
    deserializes via ``model_validate_json`` with no loss, so
    ``bound_integration/run.json`` is exactly a :class:`RunTrace`.

    Every value comes from a real run. The optional telemetry fields
    (``token_usage``, ``runtime_seconds``, ``tool_call_count``,
    ``model_metadata``) default to ``None`` and **remain null when
    unavailable** — they are never invented (Phase 9 honesty rule). The
    ``INTEGRATION_REPORT.md`` is derived from the trace via
    :func:`render_from_trace`, never maintained as a second source.

    Attributes:
        schema_version: Trace schema version. Defaults to ``"2.0"`` (v0.7).
        plan_id: The stable plan id (e.g. ``PHASE-001``), preserved from plan to
            contract to report.
        step_id: The step / contract id (may carry a ``-R<N>`` replan suffix).
        run_id: Unique run identifier (e.g. a ``uuid4`` hex) for this execution.
        bound_version: ``bound.__version__`` runtime string, or ``None``.
        bound_distribution_version: Installed ``bound-policy`` distribution
            version, or ``None`` when not resolvable.
        timestamp: UTC ISO-8601 timestamp of the run.
        contract: The :class:`~bound.contracts.StepContract` evaluated.
        evidence: The :class:`~bound.evidence.ExecutionEvidence` observed.
        evaluation: BOUND's deterministic :class:`~bound.models.EvaluationResult`.
        next_action: The mapped control action (intended action).
        feedback: BOUND's deterministic feedback string, or ``None``.
        raw_commands: Verbatim verification-command records keyed by name, or
            ``None`` when not captured.
        decision_history: Ordered decision-lineage entries (preserves retries).
        retries: Retry entries (empty when none occurred — explicit, not omitted).
        replans: Replan entries (empty when none occurred — explicit, not omitted).
        trajectory: Human-readable lineage summary lines.
        token_usage: Total tokens consumed, or ``None`` when unmeasured.
        runtime_seconds: Wall-clock runtime in seconds, or ``None``.
        tool_call_count: Agent tool-call count, or ``None`` when uninstrumented.
        model_metadata: Model identifiers / metadata, or ``None``.
        config: Optional :class:`~bound.lineage.RunConfigSnapshot` logging the
            policy/config version that governed this run (item 11). ``None``
            when not supplied (backwards compatible).
        reported_action: The agent's self-reported action description (item 12).
            Always CLAIMED provenance — the agent cannot grant itself verified
            provenance. ``None`` when not recorded.
        observed_action: What an independent integration hook observed the agent
            do (item 12). ``None`` when no hook observed the action, in which
            case the action stays CLAIMED / UNVERIFIED.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "2.0"
    plan_id: str
    step_id: str
    run_id: str
    bound_version: str | None = None
    bound_distribution_version: str | None = None
    timestamp: str

    contract: StepContract
    evidence: ExecutionEvidence
    evaluation: EvaluationResult
    next_action: NextAction
    feedback: str | None = None

    raw_commands: dict[str, RawCommandRecord] | None = None

    decision_history: list[DecisionHistoryEntry] = []
    retries: list[DecisionHistoryEntry] = []
    replans: list[DecisionHistoryEntry] = []
    trajectory: list[str] = []

    token_usage: int | None = Field(default=None, ge=0)
    runtime_seconds: float | None = Field(default=None, ge=0)
    tool_call_count: int | None = Field(default=None, ge=0)
    model_metadata: dict[str, str] | None = None

    config: RunConfigSnapshot | None = None
    reported_action: str | None = None
    observed_action: str | None = None


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def _fmt_score(value: float | None) -> str:
    """Format a score component for the report, or mark it unavailable.

    Args:
        value: A float score, or ``None`` when unmeasured.

    Returns:
        ``"unavailable (null)"`` for ``None`` (never fabricated), otherwise the
        value formatted to four decimal places.
    """
    if value is None:
        return "unavailable (null)"
    return f"{value:.4f}"


def _pytest_summary_line(stdout: str) -> str:
    """Extract the last pytest summary line from captured stdout.

    pytest ``-q`` prints a short summary (e.g. ``"38 passed, 27 skipped"``) on
    its final non-empty line. This returns that line for the report's "Actual
    execution" table, or ``"(no summary line)"`` when none is found.

    Args:
        stdout: Captured pytest ``-q`` stdout.

    Returns:
        The last non-empty line, or a placeholder.
    """
    for line in reversed(stdout.splitlines()):
        stripped = line.strip()
        if stripped:
            return stripped
    return "(no summary line)"


def _telemetry_line(label: str, value: object) -> str:
    """Render one telemetry field, honestly marking unavailable values.

    Args:
        label: Human-readable field name.
        value: The observed value, or ``None``.

    Returns:
        ``"``label``: unavailable (null)"`` for ``None``, otherwise
        ``"``label``: ``value``"``.
    """
    if value is None:
        return f"{label}: unavailable (null)"
    return f"{label}: {value}"


def _check_table(rows: list[tuple[str, str, bool, str]]) -> str:
    """Render a Markdown evidence table for acceptance / risk checks.

    Kept for backwards compatibility; the v0.7 report uses the provenance-aware
    :func:`_evidence_table` instead.

    Args:
        rows: Tuples of ``(check_id, source, passed, details)``.

    Returns:
        A Markdown table with a Passed column using ``yes`` / ``no``.
    """
    lines = [
        "| Check id | Source | Passed | Details |",
        "| --- | --- | :---: | --- |",
    ]
    for check_id, source, passed, details in rows:
        mark = "yes" if passed else "no"
        lines.append(f"| `{check_id}` | `{source}` | {mark} | {details} |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Provenance rendering helpers (item 14)
# ---------------------------------------------------------------------------


def _provenance_label(provenance: EvidenceProvenance | None) -> str:
    """Render a provenance value as an upper-case label, or ``-`` when absent."""
    if provenance is None:
        return "-"
    return provenance.value.upper()


def _passed_label(passed: bool | None) -> str:
    """Render a check pass state as ``yes`` / ``no`` / ``unknown``."""
    if passed is None:
        return "unknown"
    return "yes" if passed else "no"


def _evidence_table(checks: Sequence[CheckEvidence]) -> str:
    """Render a provenance-aware Markdown evidence table.

    Columns: Check id, Passed, Provenance, Collector, Source, Details. This is
    the v0.7 evidence view: the trust provenance and the collector that
    produced each check are first-class, so a reader can never mistake an agent
    self-report (CLAIMED) for an independently verified observation.

    Args:
        checks: :class:`~bound.evidence.CheckEvidence` rows.

    Returns:
        A Markdown table with Provenance / Collector columns.
    """
    lines = [
        "| Check id | Passed | Provenance | Collector | Source | Details |",
        "| --- | :---: | :---: | --- | --- | --- |",
    ]
    for c in checks:
        provenance = _provenance_label(c.provenance)
        collector = c.collector or "-"
        source = c.source or "-"
        details = c.details or ""
        status = f" [{c.status.value}]" if c.status in _UNVERIFIABLE_STATUS else ""
        lines.append(
            f"| `{c.check_id}` | {_passed_label(c.passed)} | {provenance} | "
            f"`{collector}` | `{source}` | {details}{status} |"
        )
    return "\n".join(lines)


def _is_independently_verified(
    provenance: EvidenceProvenance, status: EvidenceStatus | None
) -> bool:
    """Whether a piece of evidence counts as independently verified."""
    if status in _UNVERIFIABLE_STATUS:
        return False
    return provenance in _REPORT_INDEPENDENT


def _critical_check_ids(contract: StepContract) -> list[tuple[str, str]]:
    """Return the ``(check_id, kind)`` pairs that are decision-critical.

    Decision-critical risk checks (``decision_critical=True``) drive the
    coverage metric. When a contract declares none, every required acceptance
    check and every risk check is treated as critical so the report still
    surfaces an honest coverage figure rather than an empty ``0/0``.
    """
    critical = [(c.id, "risk") for c in contract.risk_checks if c.decision_critical]
    if critical:
        return critical
    return [(c.id, "acceptance") for c in contract.acceptance_checks if c.required] + [
        (c.id, "risk") for c in contract.risk_checks
    ]


def _collector_failures(ev: ExecutionEvidence) -> list[str]:
    """List checks whose evidence is unverifiable (collector crash / stale)."""
    failures: list[str] = []
    for c in (*ev.acceptance, *ev.risks):
        if c.status in _UNVERIFIABLE_STATUS:
            collector = c.collector or "(unknown collector)"
            suffix = f" — {c.details}" if c.details else ""
            failures.append(f"`{c.check_id}` · {collector} · {c.status.value}{suffix}")
    return failures


def _missing_critical_evidence(
    contract: StepContract, ev: ExecutionEvidence
) -> list[str]:
    """List decision-critical checks lacking independently verified evidence.

    A critical check is *missing* when no evidence was collected for it, or
    when the only evidence is not independently verified (CLAIMED/DEFAULTED/
    MISSING provenance, or an INVALID/UNVERIFIED/MISSING status). This is what
    the assurance gate blocks an ACCEPT on.
    """
    provenance_by_id: dict[str, EvidenceProvenance] = {}
    status_by_id: dict[str, EvidenceStatus | None] = {}
    for c in (*ev.acceptance, *ev.risks):
        provenance_by_id.setdefault(c.check_id, c.provenance)
        status_by_id.setdefault(c.check_id, c.status)
    missing: list[str] = []
    for cid, kind in _critical_check_ids(contract):
        if cid not in provenance_by_id:
            missing.append(f"`{cid}` ({kind}): no evidence collected")
        elif not _is_independently_verified(provenance_by_id[cid], status_by_id.get(cid)):
            prov = _provenance_label(provenance_by_id[cid])
            st = status_by_id.get(cid)
            note = f" status={st.value}" if st in _UNVERIFIABLE_STATUS else ""
            missing.append(f"`{cid}` ({kind}): only {prov} evidence{note}")
    return missing


def _critical_coverage(
    contract: StepContract, ev: ExecutionEvidence
) -> tuple[int, int, int]:
    """Compute independently-verified coverage over decision-critical checks.

    Returns ``(verified, total, percent)``. A critical check is *verified*
    when collected evidence exists for it whose provenance is independently
    verified (OBSERVED/VERIFIED/ATTESTED) and whose status is not
    INVALID/UNVERIFIED/MISSING. A critical check with no evidence, or only
    CLAIMED/DEFAULTED/MISSING evidence, counts as uncovered.
    """
    provenance_by_id: dict[str, EvidenceProvenance] = {}
    status_by_id: dict[str, EvidenceStatus | None] = {}
    for c in (*ev.acceptance, *ev.risks):
        provenance_by_id.setdefault(c.check_id, c.provenance)
        status_by_id.setdefault(c.check_id, c.status)
    ids = [cid for cid, _ in _critical_check_ids(contract)]
    total = len(ids)
    if total == 0:
        return 0, 0, 0
    verified = sum(
        1
        for cid in ids
        if cid in provenance_by_id
        and _is_independently_verified(provenance_by_id[cid], status_by_id.get(cid))
    )
    return verified, total, round(verified / total * 100)


def _dimension_provenance(evidence_list: list[ScoreEvidence]) -> EvidenceProvenance | None:
    """Return the strongest provenance backing one score dimension."""
    strongest: EvidenceProvenance | None = None
    for se in evidence_list:
        prov = se.provenance
        if prov is None:
            continue
        if strongest is None or _PROVENANCE_STRENGTH[prov] > _PROVENANCE_STRENGTH.get(
            strongest, 0
        ):
            strongest = prov
    return strongest


def _score_evidence_lines(evidence_list: list[ScoreEvidence]) -> list[str]:
    """Render the per-source breakdown under one score dimension."""
    lines: list[str] = []
    for se in evidence_list:
        prov = _provenance_label(se.provenance)
        parts = [f"  - `{se.source}` · {prov}"]
        if se.description:
            parts.append(se.description)
        if se.reason:
            parts.append(f"({se.reason})")
        lines.append(" ".join(parts))
    return lines


def _check_breakdown(
    checks: Sequence[CheckEvidence],
) -> tuple[EvidenceProvenance | None, list[str]]:
    """Derive the strongest provenance and per-check lines from CheckEvidence."""
    strongest: EvidenceProvenance | None = None
    lines: list[str] = []
    for c in checks:
        prov = c.provenance
        if strongest is None or _PROVENANCE_STRENGTH[prov] > _PROVENANCE_STRENGTH.get(
            strongest, 0
        ):
            strongest = prov
        parts = [f"  - `{c.check_id}` · {_provenance_label(prov)}"]
        if c.collector:
            parts.append(f"· {c.collector}")
        if c.source:
            parts.append(f"· {c.source}")
        if c.status in _UNVERIFIABLE_STATUS:
            parts.append(f"[{c.status.value}]")
        lines.append(" ".join(parts))
    return strongest, lines


def _cost_breakdown(ev: ExecutionEvidence) -> tuple[EvidenceProvenance | None, list[str]]:
    """Derive the strongest provenance and per-metric lines for the cost dimension."""
    metrics: list[tuple[str, EvidenceMetric | None]] = [
        ("retry_count", ev.retry_count),
        ("tool_call_count", ev.tool_call_count),
        ("token_usage", ev.token_usage),
        ("runtime_seconds", ev.runtime_seconds),
    ]
    strongest: EvidenceProvenance | None = None
    lines: list[str] = []
    for name, metric in metrics:
        prov = metric.provenance if metric is not None else EvidenceProvenance.MISSING
        if strongest is None or _PROVENANCE_STRENGTH[prov] > _PROVENANCE_STRENGTH.get(
            strongest, 0
        ):
            strongest = prov
        parts = [f"  - `{name}` · {_provenance_label(prov)}"]
        if metric is not None and metric.value is not None:
            parts.append(f"= {metric.value}")
        if metric is not None and metric.source:
            parts.append(f"· {metric.source}")
        lines.append(" ".join(parts))
    return strongest, lines


def _provenance_breakdown(run: RunTrace) -> list[str]:
    """Render the per-dimension (A/I/R/C) provenance breakdown (item 14).

    Acceptance and risk provenance are derived from the underlying
    :class:`CheckEvidence` (the source of truth for trust provenance); influence
    from the evaluator's per-dimension :class:`ScoreEvidence`; cost from the
    telemetry :class:`EvidenceMetric` values. Each dimension lists its strongest
    provenance and a per-check / per-metric breakdown.
    """
    ev = run.evidence
    score_prov = run.evaluation.provenance or {}
    out: list[str] = []
    a_strong, a_lines = _check_breakdown(ev.acceptance)
    out.append(f"- Acceptance (A): `{_provenance_label(a_strong)}`")
    out.extend(a_lines or ["  - _no acceptance evidence_"])
    i_list = score_prov.get("influence", [])
    i_strong = _dimension_provenance(i_list)
    out.append(f"- Influence (I): `{_provenance_label(i_strong)}`")
    out.extend(_score_evidence_lines(i_list) or ["  - _no downstream evidence source configured_"])
    r_strong, r_lines = _check_breakdown(ev.risks)
    out.append(f"- Risk (R): `{_provenance_label(r_strong)}`")
    out.extend(r_lines or ["  - _no risk evidence_"])
    c_strong, c_lines = _cost_breakdown(ev)
    out.append(f"- Cost (C): `{_provenance_label(c_strong)}`")
    out.extend(c_lines)
    return out


def _assurance_label(assurance: DecisionAssurance | None) -> str:
    """Render the decision-assurance level, or ``-`` when not computed."""
    if assurance is None:
        return "-"
    return assurance.value.upper()


def render_from_trace(run: RunTrace) -> str:
    """Render the standardized BOUND integration report from a :class:`RunTrace`.

    Produces ``INTEGRATION_REPORT.md`` as a pure, deterministic function of the
    trace. The report records **only** values actually observed or returned by
    BOUND: scores are carried verbatim from ``run.evaluation`` (never manually
    reconstructed), unavailable telemetry is rendered as *unavailable (null)*
    (never fabricated), and the original plan id, retries, and replans are
    preserved exactly. Re-running the renderer on the same trace yields the same
    report bit-for-bit.

    Structure:

    * ``## Run summary`` — BOUND version, plan, final outcome, score/threshold.
    * ``## <step_id> — <description>`` with subsections: Planned goal; Actual
      execution; Observed acceptance evidence; Observed risk evidence;
      Unavailable evidence; BOUND evaluation (A / I / R / C / S / T / decision /
      next action); Decision history; Plan deviation; Produced artifacts;
      Unexpected artifacts; Final verification.

    Args:
        run: The :class:`RunTrace` to render.

    Returns:
        The full Markdown report as a string.
    """
    ev = run.evidence
    contract = run.contract
    out: list[str] = ["# BOUND Integration Report", ""]
    c_verified, c_total, c_pct = _critical_coverage(contract, ev)

    # --- Run summary -------------------------------------------------------
    out.append("## Run summary")
    out.append("")
    version = run.bound_version or "unavailable (null)"
    if run.bound_distribution_version:
        version += f" (distribution `{run.bound_distribution_version}`)"
    out.append(f"- BOUND version: `{version}`")
    out.append(f"- Plan: `{run.plan_id}`")
    out.append(f"- Step / contract id: `{contract.id}`")
    out.append(f"- Final outcome: `{run.evaluation.decision}` → `{run.next_action}`")
    out.append(
        f"- Score / threshold: `S = {run.evaluation.score:.4f}` "
        f"{'≥' if run.evaluation.score >= run.evaluation.threshold else '<'} "
        f"`T = {run.evaluation.threshold:.4f}` "
        f"(distance `{run.evaluation.distance_to_threshold:+.4f}`)"
    )
    out.append(f"- Run id: `{run.run_id}`")
    out.append(f"- Timestamp: `{run.timestamp}`")
    # Policy that governed this run (todo 9.1): id@version + canonical hash.
    if run.config is not None and run.config.policy_id is not None:
        pid = run.config.policy_id
        pver = run.config.policy_version or "?"
        phash = run.config.policy_hash
        if phash is not None:
            out.append(f"- Policy: `{pid}@{pver}` · hash `{phash}`")
        else:
            out.append(f"- Policy: `{pid}@{pver}`")
    if c_total:
        out.append(
            f"- Critical evidence coverage: `{c_pct}% independently verified` "
            f"({c_verified}/{c_total} decision-critical checks)"
        )
    else:
        out.append("- Critical evidence coverage: `no decision-critical checks declared`")
    out.append("")

    # --- Step section ------------------------------------------------------
    out.append(f"## {contract.id} — {contract.description}")
    out.append("")

    # Planned goal
    out.append("### Planned goal")
    out.append("")
    out.append(contract.goal)
    out.append("")

    # Actual execution
    out.append("### Actual execution")
    out.append("")
    raw = run.raw_commands or {}
    if raw:
        out.append("| Command | Result (observed) |")
        out.append("| --- | --- |")
        for name, record in raw.items():
            if "pytest" in record.command or "pytest" in name:
                detail = _pytest_summary_line(record.stdout)
            elif "git status" in record.command or "git_status" in name:
                detail = f"exit `{record.returncode}`"
            else:
                detail = f"exit `{record.returncode}`"
            out.append(
                f"| `{record.command}` | exit `{record.returncode}` — {detail} |"
            )
        out.append("")
    else:
        out.append("_No raw command output was recorded for this run._")
        out.append("")

    # Observed acceptance evidence
    out.append("### Observed acceptance evidence")
    out.append("")
    if ev.acceptance:
        out.append(_evidence_table(ev.acceptance))
    else:
        out.append("_(no acceptance evidence observed)_")
    out.append("")

    # Observed risk evidence
    out.append("### Observed risk evidence")
    out.append("")
    if ev.risks:
        out.append(_evidence_table(ev.risks))
    else:
        out.append("_(no risk evidence observed)_")
    out.append("")

    # Evidence provenance (item 14): per-score A/I/R/C trust breakdown.
    out.append("### Evidence provenance")
    out.append("")
    out.append(
        "Per-dimension trust provenance (strongest backing evidence). Agent "
        "self-report is always CLAIMED; only an independent collector grants "
        "OBSERVED/VERIFIED/ATTESTED."
    )
    out.append("")
    out.extend(_provenance_breakdown(run))
    out.append("")

    # Unavailable evidence
    out.append("### Unavailable evidence")
    out.append("")
    out.append(
        "Signals not instrumented by this integration are recorded as null and "
        "never fabricated:"
    )
    out.append("")
    out.append(f"- {_telemetry_line('token_usage', run.token_usage)}")
    out.append(f"- {_telemetry_line('runtime_seconds', run.runtime_seconds)}")
    out.append(f"- {_telemetry_line('tool_call_count', run.tool_call_count)}")
    out.append(f"- {_telemetry_line('model_metadata', run.model_metadata)}")
    out.append("")

    # BOUND evaluation
    out.append("### BOUND evaluation")
    out.append("")
    scores = run.evaluation.scores
    out.append(f"- Acceptance (A): `{_fmt_score(scores.acceptance)}`")
    out.append(f"- Influence (I): `{_fmt_score(scores.influence)}`")
    out.append(f"- Risk (R): `{_fmt_score(scores.risk)}`")
    out.append(f"- Cost (C): `{_fmt_score(scores.cost)}`")
    out.append(f"- Score (S): `{run.evaluation.score:.4f}`")
    out.append(f"- Threshold (T): `{run.evaluation.threshold:.4f}`")
    out.append(f"- Decision: `{run.evaluation.decision}`")
    out.append(f"- Next action: `{run.next_action}`")
    out.append(f"- Candidate decision: `{run.evaluation.candidate_decision or '-'}`")
    out.append(f"- Final decision: `{run.evaluation.final_decision or '-'}`")
    out.append(f"- Decision assurance: `{_assurance_label(run.evaluation.assurance)}`")
    out.append("")
    if run.feedback:
        out.append("BOUND feedback (verbatim):")
        out.append("")
        out.append(f"> {run.feedback}")
        out.append("")

    # Decision assurance (item 14): assurance reasons, missing critical
    # evidence and collector failures.
    out.append("### Decision assurance")
    out.append("")
    assurance_reasons = run.evaluation.assurance_reasons
    if assurance_reasons:
        out.append("Assurance reasons:")
        out.append("")
        for reason in assurance_reasons:
            out.append(f"- {reason}")
        out.append("")
    else:
        out.append("_No assurance reasons recorded (assurance gating was not applied)._")
        out.append("")
    missing_critical = _missing_critical_evidence(contract, ev)
    if missing_critical:
        out.append("Missing decision-critical evidence:")
        out.append("")
        for item in missing_critical:
            out.append(f"- {item}")
        out.append("")
    else:
        out.append("_No missing decision-critical evidence._")
        out.append("")
    failures = _collector_failures(ev)
    if failures:
        out.append("Collector failures / unverifiable evidence:")
        out.append("")
        for item in failures:
            out.append(f"- {item}")
        out.append("")
    out.append(
        "ROLLBACK and other control actions are executed by the agent / "
        "integration, not by BOUND. BOUND is a thin harness: it emits the "
        "decision and may independently verify the resulting state; it never "
        "performs a workspace rollback itself."
    )
    out.append("")

    # Decision history
    out.append("### Decision history")
    out.append("")
    if run.decision_history:
        out.append("| Step id | Attempt | Decision | Next action | Note |")
        out.append("| --- | :---: | :---: | :---: | --- |")
        for entry in run.decision_history:
            note = entry.note or ""
            out.append(
                f"| `{entry.step_id}` | {entry.attempt} | `{entry.decision}` | "
                f"`{entry.next_action}` | {note} |"
            )
        out.append("")
    else:
        out.append("_(no decision history recorded)_")
        out.append("")

    out.append(
        f"{len(run.replans)} replan(s), {len(run.retries)} retry/retries "
        "recorded — history preserved, never rewritten."
    )
    out.append("")

    # Plan deviation
    out.append("### Plan deviation")
    out.append("")
    if not run.replans and not run.retries:
        out.append(
            "None. The step was evaluated with no replan or retry; the contract "
            f"id `{contract.id}` is preserved unchanged from the plan."
        )
    else:
        out.append(
            "Replan/retry history was preserved (see decision history above); "
            f"the root id `{contract.id}` was never replaced."
        )
    out.append("")

    # Produced artifacts
    out.append("### Produced artifacts")
    out.append("")
    if ev.produced_artifacts:
        for artifact in ev.produced_artifacts:
            out.append(f"- `{artifact}`")
    else:
        out.append("_(none observed)_")
    out.append("")

    # Unexpected artifacts
    out.append("### Unexpected artifacts")
    out.append("")
    if ev.unexpected_artifacts:
        for artifact in ev.unexpected_artifacts:
            out.append(f"- `{artifact}`")
    else:
        out.append("_(none observed)_")
    out.append("")

    # Final verification
    out.append("### Final verification")
    out.append("")
    if raw:
        out.append("The verification commands recorded for this run:")
        out.append("")
        out.append("```bash")
        for record in raw.values():
            out.append(f"$ {record.command}")
        out.append("```")
        out.append("")
    out.append(
        "Re-running the trace produces a fresh `run_id` / `timestamp` (a new "
        "run) while the deterministic evaluation outcome is stable."
    )
    out.append("")

    return "\n".join(out)




