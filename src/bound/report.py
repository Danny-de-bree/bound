from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from bound.contracts import StepContract
from bound.evidence import ExecutionEvidence
from bound.integration import NextAction
from bound.models import Decision, EvaluationResult

__all__ = [
    "DecisionHistoryEntry",
    "RawCommandRecord",
    "RunTrace",
    "render_from_trace",
]


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
        schema_version: Trace schema version. Defaults to ``"1.0"``.
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
        next_action: The mapped control action.
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
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
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
        acc_rows = [
            (c.check_id, c.source or "(unspecified)", c.passed, c.details or "")
            for c in ev.acceptance
        ]
        out.append(_check_table(acc_rows))
    else:
        out.append("_(no acceptance evidence observed)_")
    out.append("")

    # Observed risk evidence
    out.append("### Observed risk evidence")
    out.append("")
    if ev.risks:
        risk_rows = [
            (c.check_id, c.source or "(unspecified)", c.passed, c.details or "")
            for c in ev.risks
        ]
        out.append(_check_table(risk_rows))
    else:
        out.append("_(no risk evidence observed)_")
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
    out.append("")
    if run.feedback:
        out.append("BOUND feedback (verbatim):")
        out.append("")
        out.append(f"> {run.feedback}")
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




