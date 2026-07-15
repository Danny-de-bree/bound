"""Contract quality assessment + automatic-contract experiment (v0.3 Phases 14-15).

Phase 15 adds a *deterministic, structural* quality report over a compiled
:class:`~bound.contracts.BoundPlan`. Given a plan, :func:`assess_contract`
scores how *measurable* its acceptance checks read and flags obvious structural
problems (no checks, vague checks, duplicate ids, no observable verification
method, an extremely large contract). It deliberately performs **no LLM call and
no network access** — it is a lexical/structural smell test, not a semantic
judgement. The final BOUND decision remains the responsibility of the
deterministic evaluator and policy; this module only judges whether a generated
contract *appears* to define useful success criteria.

Phase 14 turns that report into a small experiment: a corpus of plans under
:mod:`benchmarks.contracts` is loaded and assessed, and
:func:`run_contract_quality_experiment` records per-plan findings plus an honest
account of what structural validation can and cannot judge. The central question
is:

    Did the generated contract define useful success criteria?

Structural validation can answer "are the checks *measurable-looking* and
non-vague?" but it cannot answer "are they *relevant* to the goal?" — that is
semantic. The ``measurable_but_irrelevant`` fixture exists precisely to make
that blind spot concrete: it scores perfectly on the structural report while
checking the wrong thing entirely.

As everywhere else in v0.3, this module is fully deterministic: standard library
plus pydantic only, no network access, no LLM SDK.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from bound.contracts import AcceptanceCheck, BoundPlan, StepBudget, StepContract

__all__ = [
    "ContractQualityReport",
    "ContractQualityFinding",
    "ContractQualityExperimentSummary",
    "assess_step",
    "assess_contract",
    "load_contract_corpus",
    "run_contract_quality_experiment",
    "summarize_contract_quality_experiment",
]

# ---------------------------------------------------------------------------
# Heuristic constants (documented, not calibrated)
# ---------------------------------------------------------------------------

#: Minimum length (characters) for an acceptance-check description before it is
#: considered too short to be meaningful. Below this the description is flagged
#: as vague regardless of its wording.
_VAGUE_DESCRIPTION_MIN_LENGTH = 10

#: Maximum number of acceptance checks a single step may declare before the
#: contract is flagged as "extremely large". A step that needs more than this is
#: likely encoding an integration-test suite rather than a focused success
#: criterion. Purely a documented smell threshold, not a tuned constant.
_LARGE_STEP_CHECK_THRESHOLD = 15

#: Descriptions that are generic placeholders rather than real criteria. A
#: description equal (after lowercasing and stripping) to any of these is vague
#: even if it happens to reach the minimum length, because words like "works" /
#: "ok" carry no observable meaning a deterministic collector could check.
_GENERIC_DESCRIPTIONS: frozenset[str] = frozenset(
    {
        "",
        "works",
        "ok",
        "okay",
        "done",
        "good",
        "fixed",
        "pass",
        "passes",
        "passed",
        "fail",
        "failed",
        "success",
        "successful",
        "yes",
        "no",
        "n/a",
        "na",
        "todo",
        "...",
        "test",
        "tests",
        "checked",
        "implemented",
        "it works",
        "works fine",
        "all good",
        "fine",
        "ready",
        "complete",
        "completed",
    }
)

#: Tokens (lowercase, alphanumeric) that strongly imply an *observable, binary*
#: outcome when they appear as word fragments in a check id or description. This
#: is a deliberately curated surface vocabulary — measurability is inferred purely
#: from wording, so the list favours verbs/states a deterministic evidence
#: collector could plausibly check (pass/fail, returns X, raises Y, equals Z,
#: artifact exists, ...). It is **not** exhaustive and **not** a proof of
#: measurability: a well-named check is not guaranteed to be verifiable, only
#: that it *reads* as verifiable. Kept honest and narrow on purpose.
_VERIFICATION_TOKENS: frozenset[str] = frozenset(
    {
        # accept / reject outcomes
        "accepted",
        "rejected",
        "accepts",
        "rejects",
        # pass / fail outcomes
        "pass",
        "passes",
        "passed",
        "fail",
        "fails",
        "failed",
        # observable predicate verbs (call -> boolean)
        "returns",
        "returned",
        "return",
        "raise",
        "raises",
        "raised",
        "thrown",
        "throws",
        "equals",
        "equal",
        "matches",
        "matched",
        "match",
        "contains",
        "contained",
        "contain",
        "exists",
        "exist",
        "emits",
        "emitted",
        "emit",
        "produces",
        "produced",
        "produce",
        "outputs",
        "output",
        "creates",
        "created",
        "create",
        "deletes",
        "deleted",
        "delete",
        "removes",
        "removed",
        "remove",
        "writes",
        "written",
        "write",
        "logs",
        "logged",
        "log",
        "validates",
        "validated",
        "validate",
        "verified",
        "verifies",
        "verify",
        "parses",
        "parsed",
        "parse",
        "compiles",
        "compiled",
        "compile",
        "runs",
        "ran",
        "run",
        "exits",
        "exited",
        "exit",
        "completes",
        "completed",
        "complete",
        "succeeds",
        "succeeded",
        "succeed",
        # observable boolean states
        "valid",
        "invalid",
        "empty",
        "null",
        "none",
        "true",
        "false",
    }
)

#: Compiled splitter used to turn an id or description into lowercase alphanumeric
#: word fragments. Underscores, hyphens, dots, slashes and whitespace all act as
#: separators, so ``parse_returns_ast`` and ``"parse() returns an AST"`` both
#: yield a ``returns`` fragment.
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


def _word_fragments(text: str) -> set[str]:
    """Return the lowercase alphanumeric word fragments of ``text``.

    Args:
        text: An id or description string.

    Returns:
        The set of non-empty fragments after splitting on every non-alphanumeric
        character. Empty fragments (e.g. from leading/trailing separators) are
        dropped.
    """
    return {frag for frag in _TOKEN_SPLIT.split(text.lower()) if frag}


def _looks_measurable(check_id: str, description: str) -> bool:
    """Return ``True`` if a check *appears* measurable by the lexical heuristic.

    A check reads as measurable when either its ``id`` or its ``description``
    contains at least one :data:`_VERIFICATION_TOKENS` fragment. This is a
    surface smell test only — it says "the wording implies an observable
    predicate", not "an executable assertion exists".

    Args:
        check_id: The acceptance-check identifier.
        description: The acceptance-check description.

    Returns:
        ``True`` when a verification token is present in the id or description.
    """
    fragments = _word_fragments(check_id) | _word_fragments(description)
    return bool(fragments & _VERIFICATION_TOKENS)


def _is_vague_description(description: str) -> bool:
    """Return ``True`` when a description is too short or generic to be a criterion.

    Args:
        description: The acceptance-check description.

    Returns:
        ``True`` when the stripped/lowercased description is a known generic
        placeholder, or when it is shorter than
        :data:`_VAGUE_DESCRIPTION_MIN_LENGTH` characters.
    """
    norm = description.strip().lower()
    if norm in _GENERIC_DESCRIPTIONS:
        return True
    return len(norm) < _VAGUE_DESCRIPTION_MIN_LENGTH


def _budget_is_meaningful(budget: StepBudget | None) -> bool:
    """Return ``True`` when ``budget`` declares at least one concrete limit.

    A ``StepBudget`` with every field ``None`` means "no explicit budget" — that
    is *not* a zero budget — so only a budget carrying at least one non-``None``
    dimension counts as meaningful.

    Args:
        budget: The step budget to inspect, or ``None``.

    Returns:
        ``True`` when ``budget`` is not ``None`` and at least one of its
        dimensions (retries, tool calls, tokens, runtime) is set.
    """
    if budget is None:
        return False
    return any(
        getattr(budget, field) is not None
        for field in (
            "max_retries",
            "max_tool_calls",
            "max_tokens",
            "max_runtime_seconds",
        )
    )


# ---------------------------------------------------------------------------
# Quality report model
# ---------------------------------------------------------------------------


class ContractQualityReport(BaseModel):
    """Deterministic structural quality assessment of a contract.

    Produced by :func:`assess_contract` (plan-level) or :func:`assess_step`
    (step-level). Every field is derived from the contract's structure and
    wording alone — no LLM, no network — so the same input always yields the
    same report.

    Attributes:
        measurable_ratio: Fraction of acceptance checks that *appear*
            measurable under the lexical :data:`_VERIFICATION_TOKENS` heuristic,
            in ``[0.0, 1.0]``. ``0.0`` when there are no acceptance checks.
        acceptance_check_count: Total number of acceptance checks assessed.
        risk_check_count: Total number of risk checks assessed.
        has_budget: Whether at least one assessed step declares a *meaningful*
            :class:`~bound.contracts.StepBudget` (a non-``None`` budget with at
            least one set dimension).
        warnings: Structural problems detected, each prefixed with the offending
            step id where applicable. One of: "no acceptance checks", "too many
            vague checks", "duplicate checks", "no observable verification
            method", or "extremely large contract".
    """

    model_config = ConfigDict(extra="forbid")

    measurable_ratio: float = Field(ge=0.0, le=1.0)
    acceptance_check_count: int = Field(ge=0)
    risk_check_count: int = Field(ge=0)
    has_budget: bool
    warnings: list[str] = []


# ---------------------------------------------------------------------------
# Assessment
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _StepAssessment:
    """Internal per-step breakdown shared by the two public assess entry points.

    Carrying raw integer counts (rather than a re-derived ratio) keeps the
    plan-level aggregation free of floating-point drift.

    Attributes:
        measurable: Number of acceptance checks that appear measurable.
        acceptance: Total acceptance checks on the step.
        risk: Total risk checks on the step.
        has_budget: Whether the step declares a meaningful budget.
        warnings: Structural warnings for this step, prefixed with the step id.
    """

    measurable: int
    acceptance: int
    risk: int
    has_budget: bool
    warnings: list[str]


def _evaluate_step(step: StepContract) -> _StepAssessment:
    """Compute the raw per-step assessment used by both public assess functions.

    Args:
        step: The :class:`~bound.contracts.StepContract` to evaluate.

    Returns:
        A :class:`_StepAssessment` carrying integer counts and the step's
        warnings. Handles a step with zero acceptance checks defensively: it
        emits a "no acceptance checks" warning rather than raising, even though
        :class:`~bound.contracts.StepContract` rejects that case at construction
        time.
    """
    checks: list[AcceptanceCheck] = step.acceptance_checks
    total = len(checks)
    measurable = sum(1 for c in checks if _looks_measurable(c.id, c.description))
    warnings: list[str] = []

    if total == 0:
        # StepContract rejects this upstream; handled defensively so a contract
        # that bypassed validation (e.g. model_construct) still reports cleanly.
        warnings.append(f"step '{step.id}' defines no acceptance checks")
        return _StepAssessment(
            measurable=0,
            acceptance=0,
            risk=len(step.risk_checks),
            has_budget=_budget_is_meaningful(step.budget),
            warnings=warnings,
        )

    vague = sum(1 for c in checks if _is_vague_description(c.description))
    if vague:
        warnings.append(
            f"step '{step.id}' has {vague} vague acceptance check(s) "
            f"(description shorter than {_VAGUE_DESCRIPTION_MIN_LENGTH} chars "
            "or generic)"
        )

    seen: set[str] = set()
    duplicates: set[str] = set()
    for check in checks:
        if check.id in seen:
            duplicates.add(check.id)
        else:
            seen.add(check.id)
    if duplicates:
        warnings.append(
            f"step '{step.id}' has duplicate acceptance check id(s): "
            f"{sorted(duplicates)}"
        )

    if measurable == 0:
        warnings.append(
            f"step '{step.id}' has no acceptance check whose id or description "
            "suggests an observable verification method"
        )

    if total > _LARGE_STEP_CHECK_THRESHOLD:
        warnings.append(
            f"step '{step.id}' is extremely large ({total} acceptance checks > "
            f"{_LARGE_STEP_CHECK_THRESHOLD})"
        )

    return _StepAssessment(
        measurable=measurable,
        acceptance=total,
        risk=len(step.risk_checks),
        has_budget=_budget_is_meaningful(step.budget),
        warnings=warnings,
    )


def assess_step(step: StepContract) -> ContractQualityReport:
    """Assess a single :class:`~bound.contracts.StepContract` structurally.

    A per-step variant of :func:`assess_contract`; useful when an experiment
    wants to drill into one step at a time. The plan-level function aggregates
    these same per-step findings.

    Args:
        step: The step contract to assess.

    Returns:
        A :class:`ContractQualityReport` for ``step``. ``measurable_ratio`` is
        ``0.0`` when the step has no acceptance checks (a defensive case;
        :class:`~bound.contracts.StepContract` normally rejects that upstream).
    """
    assessment = _evaluate_step(step)
    ratio = assessment.measurable / assessment.acceptance if assessment.acceptance else 0.0
    return ContractQualityReport(
        measurable_ratio=ratio,
        acceptance_check_count=assessment.acceptance,
        risk_check_count=assessment.risk,
        has_budget=assessment.has_budget,
        warnings=list(assessment.warnings),
    )


def assess_contract(plan: BoundPlan) -> ContractQualityReport:
    """Assess a whole :class:`~bound.contracts.BoundPlan` structurally.

    Iterates every step, aggregates acceptance/risk check counts and the
    measurable-check count across the plan, and concatenates per-step warnings
    (each already prefixed with its step id for traceability).
    ``measurable_ratio`` is the plan-wide fraction of measurable acceptance
    checks; ``has_budget`` is ``True`` when at least one step declares a
    meaningful budget.

    No LLM, no network: the result is a pure, deterministic function of the
    contract's structure and wording.

    Args:
        plan: The compiled :class:`~bound.contracts.BoundPlan` to assess.

    Returns:
        A :class:`ContractQualityReport` aggregating every step in ``plan``.
    """
    total_acceptance = 0
    total_measurable = 0
    total_risk = 0
    any_budget = False
    warnings: list[str] = []
    for step in plan.steps:
        assessment = _evaluate_step(step)
        total_acceptance += assessment.acceptance
        total_measurable += assessment.measurable
        total_risk += assessment.risk
        any_budget = any_budget or assessment.has_budget
        warnings.extend(assessment.warnings)

    ratio = total_measurable / total_acceptance if total_acceptance else 0.0
    return ContractQualityReport(
        measurable_ratio=ratio,
        acceptance_check_count=total_acceptance,
        risk_check_count=total_risk,
        has_budget=any_budget,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Phase 14 experiment: corpus loading + findings
# ---------------------------------------------------------------------------


class ContractQualityFinding(BaseModel):
    """One plan's assessment within the contract-quality experiment.

    Attributes:
        name: Corpus key of the plan (the fixture file stem).
        goal: The plan's top-level goal (echoed so a reader can compare it to
            the assessed checks without re-opening the fixture).
        step_count: Number of steps in the plan.
        report: The :class:`ContractQualityReport` produced for the plan.
        note: A deterministic, honest plain-text summary of what the structural
            report does and does not say about this plan.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    goal: str
    step_count: int = Field(ge=0)
    report: ContractQualityReport
    note: str


#: Structural validation's honest blind spots, surfaced on every experiment
#: summary so the experiment never over-claims. These are the things lexical /
#: structural assessment fundamentally cannot judge.
_HONEST_LIMITATIONS: tuple[str, ...] = (
    "Relevance of a check to the plan goal is semantic; structural validation "
    "cannot judge it (see the 'measurable_but_irrelevant' fixture, which scores "
    "well structurally while checking the wrong domain entirely).",
    "'Required checks missing' cannot be determined without ground-truth "
    "success criteria; structural validation only sees what was declared.",
    "'Unnecessary checks introduced' cannot be determined structurally; an "
    "oversized contract is flagged, but a small extra relevant check is "
    "indistinguishable from a necessary one.",
    "Whether a risk check is *meaningful* is semantic; structural validation "
    "only confirms presence and a valid severity range, not that the risk is "
    "the right one to guard against.",
    "Measurability is inferred from wording (id/description tokens), not from "
    "the existence of an executable assertion; a well-named check is not "
    "guaranteed to be verifiable in practice.",
)


class ContractQualityExperimentSummary(BaseModel):
    """Aggregate result of running :func:`run_contract_quality_experiment`.

    Attributes:
        findings: One :class:`ContractQualityFinding` per corpus plan, in sorted
            file-stem order.
        total_plans: Number of plans assessed.
        plans_with_warnings: Number of plans carrying at least one warning.
        aggregate_measurable_ratio: Corpus-wide fraction of measurable
            acceptance checks, in ``[0.0, 1.0]``; ``0.0`` when the corpus
            declares no acceptance checks.
        honest_limitations: The structural blind spots every consumer of this
            summary must keep in mind.
    """

    model_config = ConfigDict(extra="forbid")

    findings: list[ContractQualityFinding]
    total_plans: int = Field(ge=0)
    plans_with_warnings: int = Field(ge=0)
    aggregate_measurable_ratio: float = Field(ge=0.0, le=1.0)
    honest_limitations: list[str]


def _default_corpus_dir() -> Path:
    """Resolve the shipped contract corpus directory relative to this module.

    ``src/bound/contract_quality.py`` -> repo root -> ``benchmarks/contracts``.

    Returns:
        The absolute path to the bundled contract-quality fixture directory.
    """
    return Path(__file__).resolve().parents[2] / "benchmarks" / "contracts"


def load_contract_corpus(directory: str | Path) -> dict[str, BoundPlan]:
    """Load every ``*.json`` :class:`BoundPlan` fixture in a directory.

    Mirrors :func:`bound.experiment.load_trajectories`: files are keyed by stem
    (e.g. ``good_plan.json`` -> ``"good_plan"``) and iterated in sorted order so
    the experiment is reproducible.

    Args:
        directory: Directory containing BoundPlan JSON fixtures.

    Returns:
        A mapping from file stem to the parsed :class:`BoundPlan`.
    """
    base = Path(directory)
    return {
        path.stem: BoundPlan.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(base.glob("*.json"))
    }


def _finding_note(report: ContractQualityReport) -> str:
    """Render a deterministic, honest plain-text note for one plan's report.

    The note states what the structural report does say (warnings, observable
    fraction, budget, risk presence) and always closes by reminding the reader
    that goal-relevance is not structurally verifiable.

    Args:
        report: The plan's :class:`ContractQualityReport`.

    Returns:
        A single-line summary string.
    """
    parts: list[str] = []
    if report.acceptance_check_count == 0:
        parts.append(
            "No acceptance checks declared; the contract defines no success "
            "criteria (structurally this is rejected upstream by StepContract)."
        )
    else:
        if not report.warnings:
            parts.append(
                "No structural warnings; checks appear measurable and "
                "reasonably specified."
            )
        else:
            parts.append(f"Structural warnings: {'; '.join(report.warnings)}.")
        if report.measurable_ratio >= 0.8:
            parts.append("Most checks read as observable.")
        elif report.measurable_ratio >= 0.5:
            parts.append("Some checks read as observable.")
        else:
            parts.append(
                "Few checks read as observable; success criteria may not be "
                "evaluable by deterministic evidence."
            )
        if not report.has_budget:
            parts.append("No explicit execution budget declared.")
        if report.risk_check_count == 0:
            parts.append("No risk checks declared; the risk dimension is unguarded.")
    parts.append("Relevance to the goal is not structurally verifiable.")
    return " ".join(parts)


def run_contract_quality_experiment(
    corpus_dir: str | Path | None = None,
) -> ContractQualityExperimentSummary:
    """Assess every plan in the contract corpus and record findings.

    Loads the corpus with :func:`load_contract_corpus`, runs
    :func:`assess_contract` on each plan, and aggregates the results. The
    aggregate measurable ratio is computed from raw integer counts across the
    whole corpus (not from per-plan ratios) to avoid floating-point drift.

    Args:
        corpus_dir: Directory of BoundPlan JSON fixtures. Defaults to the
            bundled ``benchmarks/contracts`` directory resolved relative to this
            module.

    Returns:
        A :class:`ContractQualityExperimentSummary` with one finding per plan
        and the corpus-wide aggregate signals.
    """
    directory = Path(corpus_dir) if corpus_dir is not None else _default_corpus_dir()
    corpus = load_contract_corpus(directory)

    findings: list[ContractQualityFinding] = []
    plans_with_warnings = 0
    total_measurable = 0
    total_acceptance = 0

    for name, plan in corpus.items():
        report = assess_contract(plan)
        if report.warnings:
            plans_with_warnings += 1
        total_acceptance += report.acceptance_check_count
        for step in plan.steps:
            total_measurable += sum(
                1 for c in step.acceptance_checks if _looks_measurable(c.id, c.description)
            )
        findings.append(
            ContractQualityFinding(
                name=name,
                goal=plan.goal,
                step_count=len(plan.steps),
                report=report,
                note=_finding_note(report),
            )
        )

    aggregate_ratio = total_measurable / total_acceptance if total_acceptance else 0.0
    return ContractQualityExperimentSummary(
        findings=findings,
        total_plans=len(findings),
        plans_with_warnings=plans_with_warnings,
        aggregate_measurable_ratio=aggregate_ratio,
        honest_limitations=list(_HONEST_LIMITATIONS),
    )


def summarize_contract_quality_experiment(
    summary: ContractQualityExperimentSummary,
) -> str:
    """Render an experiment summary as a deterministic human-readable report.

    Args:
        summary: The :class:`ContractQualityExperimentSummary` to render.

    Returns:
        A multi-line string: one line per finding plus the aggregate signals and
        the honest limitations. Reproducible for a given corpus.
    """
    lines: list[str] = [
        "BOUND contract-quality experiment",
        f"plans assessed: {summary.total_plans}",
        f"plans with warnings: {summary.plans_with_warnings}",
        f"aggregate measurable ratio: {summary.aggregate_measurable_ratio:.3f}",
        "",
        "findings:",
    ]
    for finding in summary.findings:
        r = finding.report
        lines.append(
            f"- {finding.name} (goal={finding.goal!r}, steps={finding.step_count}, "
            f"checks={r.acceptance_check_count}, risks={r.risk_check_count}, "
            f"measurable_ratio={r.measurable_ratio:.3f}, has_budget={r.has_budget}, "
            f"warnings={len(r.warnings)})"
        )
        lines.append(f"    note: {finding.note}")
    lines.append("")
    lines.append("honest limitations of structural validation:")
    for limitation in summary.honest_limitations:
        lines.append(f"- {limitation}")
    return "\n".join(lines)
