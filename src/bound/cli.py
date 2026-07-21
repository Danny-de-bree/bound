from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import yaml
from pydantic import ValidationError

from bound.evidence import EvidenceProvenance, EvidenceStatus
from bound.lineage import (
    ActionReportedEvent,
    DecisionGatedEvent,
    Evaluation,
    EvidenceCollectedEvent,
    EvidenceCollectionFailedEvent,
    Outcome,
    ReasonCode,
    RunStatus,
    generate_evaluation_id,
    generate_step_id,
)
from bound.lineage_store import (
    LineageStore,
    RunLog,
    RunNotFound,
    get_default_store,
)
from bound.models import (
    Action,
    BoundCriteria,
    BoundWeights,
    CodingWorkflowSignals,
    EvaluationResult,
    EvaluationScores,
)
from bound.policy_canon import compute_policy_hash
from bound.policy_schema import (
    BoundPolicyConfig,
    HardGate,
    WeightedSignal,
    load_policy_yaml,
)
from bound.init_project import detect_tooling, generate_policy, ProjectDetections
from bound.services import (
    PolicyService,
    PolicyValidateRequest,
    PolicyValidateResponse,
    PolicyExplainRequest,
    PolicyExplainResponse,
    PolicyHashRequest,
    PolicyHashResponse,
    PolicyLoadError,
    PolicyValidationError,
    RunService,
    RunStartRequest,
    RunStartResponse,
    RunFinishRequest,
    RunFinishResponse,
    RunListRequest,
    RunListResponse,
    RunDeleteRequest,
    RunDeleteResponse,
    RunInspectRequest,
    RunInspectResponse,
    RunNotFoundError,
    EvaluationService,
    EvaluateRequest,
    EvaluateResponse,
    EvaluateWorkflowRequest,
    EvaluateWorkflowResponse,
    OutcomeService,
    OutcomeRecordRequest,
    OutcomeRecordResponse,
    EvaluationInputError,
    CheckpointService,
    CheckpointCreateRequest,
    CheckpointCreateResponse,
    CheckpointInspectRequest,
    CheckpointInspectResponse,
    CheckpointListRequest,
    CheckpointListResponse,
    CheckpointRollbackRequest,
    CheckpointRollbackResponse,
    CheckpointError,
)

logger = logging.getLogger("bound.cli")

#: Exit code returned when user-supplied inputs fail Pydantic validation.
EXIT_VALIDATION_ERROR = 2

#: Exit code returned when a referenced lineage run does not exist.
EXIT_NOT_FOUND = 1

#: Exit code returned when a ``bound policy`` file fails schema validation.
EXIT_POLICY_INVALID = 1

#: Exit code returned when a ``bound policy`` invocation is a usage error
#: (e.g. the file does not exist or cannot be read).
EXIT_POLICY_USAGE = 2


def _add_weight_and_threshold_args(sub: argparse.ArgumentParser) -> None:
    """Register the shared v0.2 weight/threshold arguments on ``sub``.

    Both ``evaluate`` and ``evaluate-workflow`` accept the same knobs so the
    policy configuration is consistent across direct-score and workflow modes.

    Args:
        sub: The subparser to attach the arguments to.
    """
    sub.add_argument(
        "--acceptance-weight",
        type=float,
        default=1.0,
        help="Weight W_A applied to acceptance (>= 0). Defaults to 1.0.",
    )
    sub.add_argument(
        "--influence-weight",
        type=float,
        default=1.0,
        help="Weight W_I applied to downstream influence (>= 0). Defaults to 1.0.",
    )
    sub.add_argument(
        "--risk-weight",
        type=float,
        default=1.0,
        help="Weight W_R applied to the risk penalty (>= 0). Defaults to 1.0.",
    )
    sub.add_argument(
        "--cost-weight",
        type=float,
        default=1.0,
        help="Weight W_C applied to the resource cost (>= 0). Defaults to 1.0.",
    )
    sub.add_argument(
        "--weight",
        type=float,
        default=None,
        help=(
            "Deprecated alias for --acceptance-weight. Supplying it together "
            "with a non-default --*-weight is rejected."
        ),
    )
    sub.add_argument(
        "--threshold", type=float, required=True, help="Acceptance threshold T (>= 0)."
    )
    sub.add_argument(
        "--retry-margin",
        type=float,
        default=0.1,
        help="How far below T a score may fall while still RETRY (>= 0). Defaults to 0.1.",
    )
    sub.add_argument(
        "--rollback-risk-threshold",
        type=float,
        default=0.8,
        help="Hard risk boundary in [0, 1] above which the action rolls back. Defaults to 0.8.",
    )


def _build_criteria(args: argparse.Namespace) -> BoundCriteria:
    """Build :class:`BoundCriteria` from the shared weight/threshold args.

    The symmetric weights are always constructed from the ``--*-weight`` flags.
    The deprecated scalar ``--weight`` is forwarded only when explicitly set;
    :class:`BoundCriteria` rejects the combination of ``weight`` with a
    non-default ``weights`` so the two weight systems can never silently
    compete.

    Args:
        args: The parsed namespace carrying the weight/threshold values.

    Returns:
        The validated :class:`BoundCriteria`.

    Raises:
        ValidationError: If any value is out of range or the two weight
            systems conflict.
    """
    weights = BoundWeights(
        acceptance=args.acceptance_weight,
        influence=args.influence_weight,
        risk=args.risk_weight,
        cost=args.cost_weight,
    )
    kwargs: dict[str, object] = {
        "threshold": args.threshold,
        "retry_margin": args.retry_margin,
        "rollback_risk_threshold": args.rollback_risk_threshold,
        "weights": weights,
    }
    if getattr(args, "weight", None) is not None:
        kwargs["weight"] = args.weight
    return BoundCriteria(**kwargs)


def _build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argument parser with the BOUND subcommands.

    Returns:
        An ``ArgumentParser`` with the ``bound`` program metadata, a global
        ``-v/--verbose`` flag, and the ``evaluate`` and ``evaluate-workflow``
        subcommands bound to their runners via ``set_defaults(func=...)``.
    """
    parser = argparse.ArgumentParser(
        prog="bound",
        description="BOUND — a deterministic bounded-utility policy for agentic systems.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase logging verbosity (repeatable).",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    evaluate = subparsers.add_parser(
        "evaluate",
        help="Evaluate a proposed action and emit the BOUND decision.",
        description=(
            "Evaluate a proposed action against BOUND bounded-utility criteria. "
            "Prints an auditable JSON result to STDOUT and a steering prompt to STDERR."
        ),
    )
    evaluate.add_argument("--action", required=True, help="Description of the proposed action.")
    evaluate.add_argument("--goal", required=True, help="The larger goal the action advances.")
    evaluate.add_argument("--context", default=None, help="Optional additional context.")
    evaluate.add_argument(
        "--acceptance", type=float, required=True, help="Acceptance score A in [0, 1]."
    )
    evaluate.add_argument(
        "--influence", type=float, required=True, help="Downstream influence I in [-1, 1]."
    )
    evaluate.add_argument("--risk", type=float, required=True, help="Risk penalty R in [0, 1].")
    evaluate.add_argument("--cost", type=float, required=True, help="Resource penalty C in [0, 1].")
    _add_weight_and_threshold_args(evaluate)
    evaluate.add_argument(
        "--run",
        metavar="RUN_ID",
        default=None,
        help="When given, record the evaluation into lineage run <RUN_ID> "
        "(requires --step). Adds a `lineage` block to the JSON output.",
    )
    evaluate.add_argument(
        "--step",
        metavar="CONTRACT_ID",
        default=None,
        help="Stable contract/phase id for the lineage step (required with --run).",
    )
    evaluate.add_argument(
        "--attempt",
        type=int,
        default=1,
        help="One-based attempt number for the lineage step. Defaults to 1.",
    )
    evaluate.add_argument(
        "--description",
        default=None,
        help="Optional human-readable step description stored with the lineage step.",
    )
    evaluate.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Explicit machine-readable JSON output (evaluate always emits JSON).",
    )
    evaluate.set_defaults(func=_run_evaluate)

    workflow = subparsers.add_parser(
        "evaluate-workflow",
        help="Derive BOUND scores from coding-workflow signals and decide.",
        description=(
            "Derive the four BOUND score dimensions from provider-agnostic "
            "coding-workflow signals (test pass rate, lint/type-check status, "
            "retry/tool-call counts, ...) via CodingWorkflowEvaluator, then run "
            "the deterministic BOUND policy. No LLM, no network."
        ),
    )
    workflow.add_argument("--action", required=True, help="Description of the proposed action.")
    workflow.add_argument("--goal", required=True, help="The larger goal the action advances.")
    workflow.add_argument("--context", default=None, help="Optional additional context.")
    workflow.add_argument(
        "--test-pass-rate", type=float, default=None, help="Fraction of tests passing in [0, 1]."
    )
    workflow.add_argument(
        "--lint-passed",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Whether the linter is clean (--lint-passed / --no-lint-passed).",
    )
    workflow.add_argument(
        "--type-check-passed",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Whether type-checking is clean (--type-check-passed / --no-type-check-passed).",
    )
    workflow.add_argument(
        "--required-checks-passed",
        type=float,
        default=None,
        help="Fraction of required checks passing in [0, 1].",
    )
    workflow.add_argument(
        "--retry-count", type=int, default=0, help="Number of retries performed so far."
    )
    workflow.add_argument(
        "--tool-call-count", type=int, default=0, help="Number of tool calls performed so far."
    )
    workflow.add_argument("--token-usage", type=int, default=None, help="Total tokens consumed.")
    workflow.add_argument(
        "--execution-time-seconds",
        type=float,
        default=None,
        help="Wall-clock execution time in seconds.",
    )
    workflow.add_argument(
        "--files-changed", type=int, default=None, help="Number of files changed."
    )
    workflow.add_argument(
        "--unexpected-files-changed",
        type=int,
        default=None,
        help="Number of unexpected files changed.",
    )
    workflow.add_argument(
        "--rollback-available",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Whether a clean rollback is available "
            "(--rollback-available / --no-rollback-available)."
        ),
    )
    workflow.add_argument(
        "--influence",
        type=float,
        default=None,
        help="Optional externally-supplied downstream influence I in [-1, 1].",
    )
    _add_weight_and_threshold_args(workflow)
    workflow.set_defaults(func=_run_evaluate_workflow)

    spec = subparsers.add_parser(
        "integration-spec",
        help="Emit the framework-neutral BOUND integration specification as JSON.",
        description=(
            "Emit the framework-neutral BOUND integration specification as "
            "structured JSON to STDOUT. Defines when to call BOUND, when not to, "
            "the required flow, and the evidence rule. Deterministic: no LLM, "
            "no network."
        ),
    )
    spec.set_defaults(func=_run_integration_spec)

    # --- lineage: run --------------------------------------------------------
    run_parser = subparsers.add_parser(
        "run",
        help="Manage decision-lineage runs (start/finish/list/delete).",
        description="Manage local decision-lineage runs stored under .bound/runs/.",
    )
    run_sub = run_parser.add_subparsers(dest="run_command", metavar="<run command>")

    run_start = run_sub.add_parser(
        "start",
        help="Start a new lineage run.",
        description="Start a new lineage run: generate run_id, write run.json, "
        "append run_started. Prints the run_id (or JSON with --json).",
    )
    run_start.add_argument("task", help="The natural-language task the run attempts.")
    run_start.add_argument(
        "--metadata",
        action="append",
        type=_key_value,
        default=[],
        metavar="KEY=VALUE",
        help="Free-form string metadata (repeatable). Never store secrets.",
    )
    run_start.add_argument("--json", action="store_true", default=False, help="Emit JSON.")
    run_start.set_defaults(func=_run_run_start)

    run_finish = run_sub.add_parser(
        "finish",
        help="Finish (close) a lineage run.",
        description="Append the terminal run_finished event to a run and update "
        "run.json. Prints a confirmation (or JSON with --json).",
    )
    run_finish.add_argument("run_id", help="The run id to finish.")
    run_finish.add_argument(
        "--status",
        choices=["completed", "interrupted", "failed"],
        default="completed",
        help="Terminal status. Defaults to completed.",
    )
    run_finish.add_argument("--note", default=None, help="Optional free-text note.")
    run_finish.add_argument("--json", action="store_true", default=False, help="Emit JSON.")
    run_finish.set_defaults(func=_run_run_finish)

    run_list = run_sub.add_parser(
        "list",
        help="List lineage runs.",
        description="List every run under .bound/runs/, newest first, as a "
        "table (or JSON with --json).",
    )
    run_list.add_argument("--json", action="store_true", default=False, help="Emit JSON.")
    run_list.set_defaults(func=_run_run_list)

    run_delete = run_sub.add_parser(
        "delete",
        help="Delete a lineage run.",
        description="Remove an entire run directory. Exits non-zero with a clear "
        "message if the run does not exist.",
    )
    run_delete.add_argument("run_id", help="The run id to delete.")
    run_delete.add_argument("--json", action="store_true", default=False, help="Emit JSON.")
    run_delete.set_defaults(func=_run_run_delete)

    # --- lineage: inspect ----------------------------------------------------
    inspect = subparsers.add_parser(
        "inspect",
        help="Inspect a lineage run as a decision tree.",
        description="Replay a run's events.jsonl and render the decision lineage "
        "as a chronological Step -> Attempt -> Outcome tree, showing task, "
        "status, start/end time, decisions, evidence, scores/thresholds, reason "
        "codes and the agent's follow-up action. Incomplete runs are clearly "
        "marked. Use --json for machine-readable output.",
    )
    inspect.add_argument("run_id", help="The run id to inspect.")
    inspect.add_argument("--json", action="store_true", default=False, help="Emit JSON.")
    inspect.add_argument(
        "--only-unverified",
        action="store_true",
        default=False,
        help=(
            "Filter the provenance breakdown to unverified / claimed / missing / "
            "invalid evidence only (item 14)."
        ),
    )
    inspect.add_argument(
        "--html",
        metavar="PATH",
        default=None,
        help=(
            "Write a self-contained local HTML timeline (plan -> step -> "
            "attempt, provenance color-coded) to PATH and exit (Phase 9.3)."
        ),
    )
    inspect.set_defaults(func=_run_inspect)

    # --- local dashboard: ui -------------------------------------------------
    ui = subparsers.add_parser(
        "ui",
        help="Start the local BOUND dashboard (read-only, no account needed).",
        description=(
            "Start the local BOUND lineage dashboard — a read-only HTTP server "
            "that shows all local runs with task, status, latest decision, "
            "assurance, and time. Opens one run as a plan -> step -> attempt -> "
            "decision tree with candidate vs final decision, evidence provenance "
            "badges (VERIFIED, CLAIMED, MISSING, ...), and highlights the exact "
            "evidence or gate that caused a RETRY / REPLAN / ROLLBACK. "
            "No hosted backend or account needed."
        ),
    )
    ui.add_argument(
        "run_id",
        nargs="?",
        default=None,
        metavar="RUN_ID",
        help="Optional run id to open directly on the detail page.",
    )
    ui.add_argument(
        "--port",
        type=int,
        default=8765,
        help="TCP port (default 8765).",
    )
    ui.add_argument(
        "--open",
        action="store_true",
        default=False,
        dest="open_browser",
        help="Open the dashboard URL in the default browser after startup.",
    )
    ui.set_defaults(func=_run_ui)

    # --- lineage: outcome ----------------------------------------------------
    outcome = subparsers.add_parser(
        "outcome",
        help="Record an agent follow-up outcome for a lineage run.",
        description="Record an outcome_recorded event responding to a run's "
        "evaluation. The evaluation is linked by evaluation_id (auto-resolved "
        "from --step/--attempt when --evaluation-id is omitted).",
    )
    outcome.add_argument("--run", required=True, metavar="RUN_ID", help="Owning run id.")
    outcome.add_argument(
        "--step",
        required=True,
        metavar="CONTRACT_ID",
        help="Stable contract/phase id of the evaluated step.",
    )
    outcome.add_argument(
        "--attempt",
        type=int,
        default=1,
        help="Attempt number the evaluation belongs to. Defaults to 1.",
    )
    outcome.add_argument(
        "--evaluation-id",
        default=None,
        help="Evaluation to respond to (auto-resolved when omitted).",
    )
    outcome.add_argument(
        "--decision",
        required=True,
        choices=["ACCEPT", "RETRY", "REPLAN", "ROLLBACK"],
        help="The BOUND decision recorded for this outcome.",
    )
    outcome.add_argument(
        "--next-action",
        default=None,
        choices=["continue", "retry", "replan", "rollback"],
        help="Agent follow-up action (derived from --decision when omitted).",
    )
    outcome.add_argument(
        "--reason-code",
        default=None,
        help="Reason code (derived from --next-action when omitted).",
    )
    outcome.add_argument("--note", default=None, help="Optional free-text note.")
    outcome.add_argument("--json", action="store_true", default=False, help="Emit JSON.")
    outcome.set_defaults(func=_run_outcome)

    # --- policy configuration (Phase 4.1) -------------------------------------
    policy_parser = subparsers.add_parser(
        "policy",
        help="Validate, explain, and hash a bound-policy.yaml file.",
        description=(
            "Operate on a bound-policy.yaml policy configuration: validate the "
            "schema and warn about checks BOUND cannot independently back, "
            "explain the effective gates/weights/budgets, and print the "
            "canonical policy hash. Deterministic: no LLM, no network."
        ),
    )
    policy_sub = policy_parser.add_subparsers(
        dest="policy_command", metavar="<policy command>", required=True
    )

    def _add_policy_file_arg(p: argparse.ArgumentParser) -> None:
        p.add_argument("file", help="Path to the bound-policy.yaml file.")
        p.add_argument("--json", action="store_true", default=False, help="Emit JSON.")

    p_validate = policy_sub.add_parser(
        "validate",
        help="Validate a policy file and report warnings.",
        description="Parse and validate a bound-policy.yaml file, then report "
        "warnings (blockers without collectors, claimed-only checks, "
        "unmeasurable/subjective criteria). Exit 0 valid / 1 invalid / 2 usage.",
    )
    _add_policy_file_arg(p_validate)
    p_validate.set_defaults(func=_run_policy_validate)

    p_explain = policy_sub.add_parser(
        "explain",
        help="Explain the effective gates, weights, and budgets.",
        description="Render a concise human-readable explanation of the policy's "
        "effective gates (blockers), weighted signals and budgets. "
        "Use --json for machine-readable output.",
    )
    _add_policy_file_arg(p_explain)
    p_explain.set_defaults(func=_run_policy_explain)

    p_hash = policy_sub.add_parser(
        "hash",
        help="Print the canonical policy hash (sha256:<hex>).",
        description="Canonicalise the policy and print its SHA-256 hash "
        "(sha256:<hex>). The hash identifies the exact policy content that "
        "governs a run (release blocker: every decision records the policy hash).",
    )
    _add_policy_file_arg(p_hash)
    p_hash.set_defaults(func=_run_policy_hash)

    # --- watch mode (Sprint 2) -------------------------------------------------
    watch_parser = subparsers.add_parser(
        "watch",
        help="Event-driven watch mode: consume JSONL events and evaluate boundaries.",
        description=(
            "Event-driven watch mode that consumes BOUND watch events (JSONL) "
            "from stdin, evaluates each step against the policy's meaningful "
            "boundaries, runs approved collectors, emits structured control "
            "decisions, and appends everything to lineage.  Use --once to "
            "process a single task and exit, or --json for machine-readable output."
        ),
    )
    watch_parser.add_argument(
        "--policy",
        required=True,
        help="Path to the bound-policy.yaml file.",
    )
    watch_parser.add_argument(
        "--once",
        action="store_true",
        default=False,
        help="Exit after processing the first task_finished event.",
    )
    watch_parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        dest="json_output",
        help="Emit JSON decision events to stdout instead of log lines.",
    )
    watch_parser.set_defaults(func=_run_watch)

# --- checkpoint -----------------------------------------------------------
    cp_parser = subparsers.add_parser(
        "checkpoint",
        help="Manage BOWN checkpoints (create/inspect/list).",
        description="Manage BOUND checkpoints for safe state preservation and rollback.",
    )
    cp_sub = cp_parser.add_subparsers(
        dest="checkpoint_command", metavar="<checkpoint command>", required=True
    )

    cp_create = cp_sub.add_parser(
        "create",
        help="Create a checkpoint for a run step.",
        description="Capture the current repository state into a BOUND checkpoint.",
    )
    cp_create.add_argument("--run", required=True, metavar="RUN_ID", help="Owning run id.")
    cp_create.add_argument("--step", required=True, metavar="STEP_ID", help="Step id for this checkpoint.")
    cp_create.add_argument("--message", default=None, help="Optional checkpoint message.")
    cp_create.add_argument("--json", action="store_true", default=False, help="Emit JSON.")
    cp_create.set_defaults(func=_run_checkpoint_create)

    cp_inspect = cp_sub.add_parser(
        "inspect",
        help="Inspect a checkpoint's details.",
        description="Show detailed information about a checkpoint.",
    )
    cp_inspect.add_argument("checkpoint_id", help="The checkpoint id to inspect.")
    cp_inspect.add_argument("--run", required=True, metavar="RUN_ID", help="Owning run id.")
    cp_inspect.add_argument("--json", action="store_true", default=False, help="Emit JSON.")
    cp_inspect.set_defaults(func=_run_checkpoint_inspect)

    cp_list = cp_sub.add_parser(
        "list",
        help="List checkpoints for a run.",
        description="List all checkpoints for a given run.",
    )
    cp_list.add_argument("--run", required=True, metavar="RUN_ID", help="Owning run id.")
    cp_list.add_argument("--json", action="store_true", default=False, help="Emit JSON.")
    cp_list.set_defaults(func=_run_checkpoint_list)

    # --- rollback -------------------------------------------------------------
    rollback_parser = subparsers.add_parser(
        "rollback",
        help="Roll back to a checkpoint (requires --execute).",
        description="Roll back the working tree to a previously created checkpoint. "
        "Requires explicit --execute opt-in to prevent accidental mutations. "
        "Use --dry-run for a preview of what would change.",
    )
    rollback_parser.add_argument("--run", required=True, metavar="RUN_ID", help="Owning run id.")
    rollback_parser.add_argument("--checkpoint", required=True, metavar="CHECKPOINT_ID", help="Checkpoint to roll back to.")
    rollback_parser.add_argument("--dry-run", action="store_true", default=False, help="Preview changes without executing.")
    rollback_parser.add_argument("--execute", action="store_true", default=False, help="Perform the rollback (opt-in required).")
    rollback_parser.set_defaults(func=_run_rollback)

    # --- init (Sprint 3) -------------------------------------------------------
    mcp_parser = subparsers.add_parser(
        "mcp",
        help="Start the stdio MCP (Model Context Protocol) server.",
        description=(
            "Start the stdio-based JSON-RPC MCP server. Reads one JSON-RPC "
            "request per line from stdin, dispatches to the shared BOUND "
            "service layer, and writes one JSON-RPC response per line to stdout. "
            "Use --once to process a single request and exit."
        ),
    )
    mcp_parser.add_argument(
        "--once",
        action="store_true",
        default=False,
        help="Process a single request and exit.",
    )
    mcp_parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        dest="json_log",
        help="Emit structured JSON log lines to stderr.",
    )
    mcp_parser.set_defaults(func=_run_mcp)

    init_parser = subparsers.add_parser(
        "init",
        help="Generate a bound-policy.yaml for an existing project.",
        description=(
            "Detect project tooling (test framework, linter, type checker, "
            "coverage, build system, Git) and generate a minimal but reviewable "
            "bound-policy.yaml. No tool is executed; no network call is made. "
            "Use --stdout to preview the policy without writing to disk."
        ),
    )
    init_parser.add_argument(
        "--project-dir",
        default=".",
        help="Path to the project root directory. Defaults to the current directory.",
    )
    init_parser.add_argument(
        "--stdout",
        action="store_true",
        default=False,
        help="Print the generated policy to stdout instead of writing to disk.",
    )
    init_parser.set_defaults(func=_run_init)

    return parser


def _configure_logging(verbosity: int) -> None:
    """Configure root logging based on the requested verbosity level.

    Args:
        verbosity: Number of times ``-v`` was supplied (0 = warning, 1 = info,
            2+ = debug).
    """
    level = logging.DEBUG if verbosity >= 2 else logging.INFO if verbosity == 1 else logging.WARNING
    logging.basicConfig(level=level, format="%(levelname)s:%(name)s:%(message)s")


def _result_to_payload(result: EvaluationResult) -> dict[str, object]:
    """Build the auditable JSON payload from an :class:`EvaluationResult`.

    The payload exposes every term of ``S = (W_A × A) + (W_I × I) - (W_R × R) -
    (W_C × C)`` so a consumer can reconstruct the score from the JSON alone. The
    symmetric ``weights``, the deprecated ``weight`` alias, the threshold
    metadata (``retry_margin``, ``rollback_risk_threshold``) and the signed
    ``distance_to_threshold`` are all carried through for auditability. Scores
    are emitted without their optional ``reasoning`` field to keep the output
    minimal and stable. Provenance is only included when present (workflow
    mode), so direct-score output stays minimal.

    Args:
        result: The :class:`EvaluationResult` to serialize.

    Returns:
        A JSON-serializable dict with ``scores`` (the four dimensions),
        ``weights``, ``weight``, ``threshold``, the threshold metadata, the
        four components, ``score``, ``distance_to_threshold``, ``decision``,
        and (when available) ``provenance``.
    """
    payload: dict[str, object] = {
        "scores": {
            "acceptance": result.scores.acceptance,
            "influence": result.scores.influence,
            "risk": result.scores.risk,
            "cost": result.scores.cost,
        },
        "weights": {
            "acceptance": result.weights.acceptance,
            "influence": result.weights.influence,
            "risk": result.weights.risk,
            "cost": result.weights.cost,
        },
        "weight": result.weight,
        "threshold": result.threshold,
        "retry_margin": result.retry_margin,
        "rollback_risk_threshold": result.rollback_risk_threshold,
        "acceptance_component": result.acceptance_component,
        "influence_component": result.influence_component,
        "risk_component": result.risk_component,
        "cost_component": result.cost_component,
        "score": result.score,
        "distance_to_threshold": result.distance_to_threshold,
        "decision": result.decision,
    }
    if result.provenance is not None:
        payload["provenance"] = {
            dimension: [evidence.model_dump() for evidence in evidence_list]
            for dimension, evidence_list in result.provenance.items()
        }
    return payload


def _key_value(value: str) -> tuple[str, str]:
    """Parse a ``KEY=VALUE`` metadata pair (for ``bound run start --metadata``)."""
    if "=" not in value:
        raise argparse.ArgumentTypeError(f"expected KEY=VALUE, got {value!r}")
    key, _, val = value.partition("=")
    if not key.strip():
        raise argparse.ArgumentTypeError(f"empty key in {value!r}")
    return key.strip(), val


def _store() -> LineageStore:
    """Return the lineage store for this CLI invocation.

    Honors ``BOUND_RUNS_DIR`` (overrides ``.bound/runs/``) so tests can redirect
    storage to a temp directory; otherwise delegates to the cached
    :func:`get_default_store` (which honors ``BOUND_LINEAGE_DISABLED``).
    """
    base = os.environ.get("BOUND_RUNS_DIR")
    if base:
        return LineageStore(base_dir=base)
    return get_default_store()


_DECISION_NEXT_ACTION = {
    "ACCEPT": "continue",
    "RETRY": "retry",
    "REPLAN": "replan",
    "ROLLBACK": "rollback",
}

_NEXT_ACTION_REASON = {
    "continue": ReasonCode.CONTINUED,
    "retry": ReasonCode.RETRIED,
    "replan": ReasonCode.REPLANNED,
    "rollback": ReasonCode.ROLLED_BACK,
}


def _run_run_start(args: argparse.Namespace) -> int:
    """Execute ``bound run start``."""
    metadata = dict(args.metadata) if args.metadata else {}
    response = RunService.start(RunStartRequest(
        task=args.task,
        metadata=metadata,
        store=_store(),
    ))
    if args.json:
        print(json.dumps({
            "run_id": response.run_id,
            "task": response.task,
            "started_at": response.started_at,
            "status": response.status,
            "schema_version": response.schema_version,
        }, indent=2))
    else:
        print(response.run_id)
    return 0


def _run_run_finish(args: argparse.Namespace) -> int:
    """Execute ``bound run finish``."""
    try:
        response = RunService.finish(RunFinishRequest(
            run_id=args.run_id,
            status=args.status,
            note=args.note,
            store=_store(),
        ))
    except RunNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NOT_FOUND
    if args.json:
        print(json.dumps({
            "run_id": response.run_id,
            "status": response.status,
            "finished_at": response.finished_at,
        }, indent=2))
    else:
        print(f"finished run {response.run_id} ({response.status})")
    return 0


def _run_run_list(args: argparse.Namespace) -> int:
    """Execute ``bound run list``."""
    response = RunService.list_runs(RunListRequest(store=_store()))
    summaries = response.runs
    if args.json:
        print(json.dumps([s.model_dump(mode="json") for s in summaries], indent=2, default=str))
        return 0
    if not summaries:
        print("(no lineage runs found under .bound/runs/)")
        return 0
    print(f"{'RUN_ID':<34} {'STATUS':<12} {'TASK':<28} {'STARTED (UTC)':<20} INCOMPLETE")
    for s in summaries:
        started = s.started_at.strftime("%Y-%m-%d %H:%M:%S") if s.started_at else "-"
        print(
            f"{s.run_id:<34} {s.status.value:<12} {s.task[:28]:<28} {started:<20} "
            f"{'yes' if s.incomplete else 'no'}"
        )
    return 0


def _run_run_delete(args: argparse.Namespace) -> int:
    """Execute ``bound run delete``."""
    try:
        response = RunService.delete(RunDeleteRequest(
            run_id=args.run_id,
            store=_store(),
        ))
    except RunNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NOT_FOUND
    if args.json:
        print(json.dumps({"run_id": response.run_id, "deleted": True}, indent=2))
    else:
        print(f"deleted run {response.run_id}")
    return 0


def _fmt_dt(dt: datetime | None) -> str:
    """Format a UTC datetime for human-readable CLI output."""
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC") if dt else "-"


def _checks_summary(evaluation: Evaluation) -> str:
    """Derive an ``n/total checks`` summary from the evaluation's reason code."""
    if evaluation.reason_code == ReasonCode.ALL_CHECKS_PASSED:
        return "3/3 checks"
    return "1/3 checks"


# ---------------------------------------------------------------------------
# Provenance visibility (item 14)
# ---------------------------------------------------------------------------

#: Provenance ranked by trust strength (higher = more trustworthy). Used to
#: pick the strongest provenance backing a score and to decide what counts as
#: independently verified. OBSERVED/VERIFIED/ATTESTED are the only provenances
#: that count as *independent* — agent self-report (CLAIMED) never does.
_PROVENANCE_STRENGTH: dict[EvidenceProvenance, int] = {
    EvidenceProvenance.VERIFIED: 60,
    EvidenceProvenance.OBSERVED: 50,
    EvidenceProvenance.ATTESTED: 40,
    EvidenceProvenance.EVALUATED: 30,
    EvidenceProvenance.CLAIMED: 20,
    EvidenceProvenance.DEFAULTED: 10,
    EvidenceProvenance.MISSING: 0,
}

#: Provenance that counts as *independently verified* — produced by a
#: BOUND-controlled collector or a trusted attestation, never agent
#: self-report. Drives the "Critical evidence coverage" metric.
_INDEPENDENTLY_VERIFIED: frozenset[EvidenceProvenance] = frozenset(
    {EvidenceProvenance.OBSERVED, EvidenceProvenance.VERIFIED, EvidenceProvenance.ATTESTED}
)

#: Provenance that is *not* independently verified — selected by
#: ``bound inspect --only-unverified``.
_UNVERIFIED_PROVENANCE: frozenset[EvidenceProvenance] = frozenset(
    {EvidenceProvenance.CLAIMED, EvidenceProvenance.DEFAULTED, EvidenceProvenance.MISSING}
)

#: Evidence statuses that mean the check could not be independently confirmed.
_UNVERIFIED_STATUS: frozenset[EvidenceStatus] = frozenset(
    {EvidenceStatus.UNVERIFIED, EvidenceStatus.MISSING, EvidenceStatus.INVALID}
)


def _provenance_label(provenance: EvidenceProvenance | None) -> str:
    """Render a provenance value as an upper-case label, or ``-`` when absent."""
    if provenance is None:
        return "-"
    return provenance.value.upper()


def _strongest_provenance(
    events: list[EvidenceCollectedEvent],
) -> EvidenceProvenance | None:
    """Return the strongest provenance among collected evidence events.

    ``None`` when no evidence was collected for the group.
    """
    if not events:
        return None
    return max(events, key=lambda e: _PROVENANCE_STRENGTH.get(e.provenance, 0)).provenance


def _coverage(events: list[EvidenceCollectedEvent]) -> tuple[int, int, int]:
    """Compute independently-verified coverage over collected evidence.

    Returns ``(verified, total, percent)`` where ``percent`` is the share of
    collected checks whose provenance is independently verified
    (OBSERVED/VERIFIED/ATTESTED). ``total == 0`` means no collector evidence
    was recorded for the group.
    """
    total = len(events)
    if total == 0:
        return 0, 0, 0
    verified = sum(1 for e in events if e.provenance in _INDEPENDENTLY_VERIFIED)
    return verified, total, round(verified / total * 100)


def _is_unverified_evidence(event: EvidenceCollectedEvent) -> bool:
    """Whether a collected-evidence event is *not* independently verified."""
    if event.status in _UNVERIFIED_STATUS:
        return True
    return event.provenance in _UNVERIFIED_PROVENANCE


def _check_provenance_line(event: EvidenceCollectedEvent) -> str:
    """Render one collected-evidence event as an indented check-provenance row."""
    parts = [f"{event.check_id:<18}", _provenance_label(event.provenance)]
    if event.collector:
        parts.append(f"· {event.collector}")
    if event.source:
        parts.append(f"· {event.source}")
    if event.status is not None and event.status in _UNVERIFIED_STATUS:
        parts.append(f"[{event.status.value}]")
    return "  ".join(parts)


def _filter_checks(
    events: list[EvidenceCollectedEvent], only_unverified: bool
) -> list[EvidenceCollectedEvent]:
    """Keep only unverified/claimed/missing evidence when ``only_unverified``."""
    if not only_unverified:
        return events
    return [e for e in events if _is_unverified_evidence(e)]


class _RunAuditIndex:
    """Schema-2.0 audit events for a run, grouped by step id.

    The lineage :class:`~bound.lineage_store.RunLog` carries the verbatim
    append-only event log; these are the v0.7 audit events that back provenance
    visibility (item 14). Grouping by ``step_id`` lets the inspect renderer
    attach per-check provenance, collector failures, assurance gates and agent
    action reports to the right step/attempt.
    """

    __slots__ = ("collected", "failures", "gates", "actions")

    def __init__(self) -> None:
        self.collected: dict[str, list[EvidenceCollectedEvent]] = {}
        self.failures: dict[str, list[EvidenceCollectionFailedEvent]] = {}
        self.gates: dict[str, list[DecisionGatedEvent]] = {}
        self.actions: dict[str, list[ActionReportedEvent]] = {}

    @classmethod
    def from_log(cls, log: RunLog) -> _RunAuditIndex:
        """Build the index by scanning a :class:`RunLog`'s events."""
        idx = cls()
        for ev in log.events:
            if isinstance(ev, EvidenceCollectedEvent):
                idx.collected.setdefault(ev.step_id, []).append(ev)
            elif isinstance(ev, EvidenceCollectionFailedEvent):
                idx.failures.setdefault(ev.step_id, []).append(ev)
            elif isinstance(ev, DecisionGatedEvent):
                idx.gates.setdefault(ev.step_id, []).append(ev)
            elif isinstance(ev, ActionReportedEvent):
                idx.actions.setdefault(ev.step_id, []).append(ev)
        return idx

    def gate_for(self, step_id: str, evaluation_id: str) -> DecisionGatedEvent | None:
        """Return the assurance gate recorded for one evaluation, if any."""
        for gate in self.gates.get(step_id, []):
            if gate.evaluation_id == evaluation_id:
                return gate
        return None


def _render_inspect_tree(log: RunLog, *, only_unverified: bool = False) -> str:
    """Render a :class:`RunLog` as the Step -> Attempt -> Outcome tree.

    Item 14 — provenance visibility: under each attempt the tree also shows the
    per-check provenance breakdown (from ``evidence.collected`` audit events),
    the candidate vs final (gated) decision plus :class:`DecisionAssurance`
    (from ``decision.gated``), and any collector failures
    (``evidence.collection_failed``). A run-level ``Critical evidence coverage``
    line summarises the share of collected evidence that is independently
    verified.
    """
    run = log.run
    audit = _RunAuditIndex.from_log(log)
    all_collected = [e for evs in audit.collected.values() for e in evs]
    verified, total, pct = _coverage(all_collected)
    out: list[str] = [
        f"Run {run.run_id}",
        f"Task: {run.task or '(none)'}",
        f"Status: {run.status.value}" + ("  (INCOMPLETE)" if log.incomplete else ""),
        f"Started: {_fmt_dt(run.started_at)}",
        f"Finished: {_fmt_dt(run.finished_at)}",
    ]
    # Policy display (Phase 9.1): the policy that governed the run.
    cfg = run.config
    if cfg is not None and cfg.policy_id is not None:
        policy_line = f"Policy: {cfg.policy_id}@{cfg.policy_version or '?'}"
        if cfg.policy_hash is not None:
            policy_line += f"  (hash {cfg.policy_hash})"
        out.append(policy_line)
        if cfg.policy_hash is not None:
            out.append(f"Policy hash: {cfg.policy_hash}")
    if total:
        out.append(
            f"Critical evidence coverage: {pct}% independently verified "
            f"({verified}/{total} collected checks)"
        )
    else:
        out.append("Critical evidence coverage: no collector evidence recorded")
    out.append("")
    if log.truncated:
        out.append("Note: event log tail was truncated; the last partial line was dropped.")
    if log.corrupt_lines:
        out.append(f"Note: {log.corrupt_lines} corrupt event line(s) skipped.")
    if not log.steps:
        out.append("(no steps recorded)")
        return "\n".join(out)

    evals_by_step: dict[str, list[Evaluation]] = {}
    for e in log.evaluations:
        evals_by_step.setdefault(e.step_id, []).append(e)
    outcomes_by_step: dict[str, list[Outcome]] = {}
    for o in log.outcomes:
        outcomes_by_step.setdefault(o.step_id, []).append(o)

    for idx, step in enumerate(log.steps):
        out.append(
            f"Step {idx + 1} · {step.description or step.contract_id} · {step.status.value}"
        )
        step_evals = evals_by_step.get(step.step_id, [])
        step_outcomes = outcomes_by_step.get(step.step_id, [])
        step_collected = audit.collected.get(step.step_id, [])
        step_failures = audit.failures.get(step.step_id, [])
        for a_idx, attempt in enumerate(step.attempts):
            is_last = a_idx == len(step.attempts) - 1
            branch = "└──" if is_last else "├──"
            cont = "    " if is_last else "│   "
            ev = next(
                (e for e in step_evals if e.evaluation_id == attempt.evaluation_id), None
            )
            if ev is not None:
                out.append(
                    f"{branch} Attempt {attempt.attempt} · {ev.decision} · {_checks_summary(ev)}"
                )
            else:
                out.append(f"{branch} Attempt {attempt.attempt} · (no evaluation)")
            children: list[tuple[str, list[str]]] = []
            outcome = next(
                (o for o in step_outcomes if o.evaluation_id == attempt.evaluation_id), None
            )
            if outcome is not None:
                children.append((f"Outcome: {outcome.note or outcome.next_action}", []))
                children.append((f"Action: {outcome.next_action} ({outcome.reason_code})", []))
            else:
                children.append(("Outcome: (none recorded)", []))
            if ev is not None:
                sc = ev.scores
                children.append(
                    (
                        f"Score S={ev.score:.4f} (A={sc.acceptance:.2f} "
                        f"I={sc.influence:.2f} R={sc.risk:.2f} C={sc.cost:.2f}) "
                        f"T={ev.threshold:.4f}",
                        [],
                    )
                )
            check_lines = _filter_checks(step_collected, only_unverified)
            if check_lines:
                strongest = _strongest_provenance(check_lines)
                cv, ct, _ = _coverage(check_lines)
                header = (
                    f"Provenance: {_provenance_label(strongest)} "
                    f"({cv}/{ct} checks independently verified)"
                )
                children.append((header, [_check_provenance_line(e) for e in check_lines]))
            elif only_unverified:
                children.append(("Provenance: (no unverified evidence)", []))
            if ev is not None:
                gate = audit.gate_for(step.step_id, ev.evaluation_id)
                if gate is not None:
                    header = (
                        f"Candidate: {gate.candidate_decision} → Final: "
                        f"{gate.final_decision} · Assurance: {gate.assurance.value.upper()}"
                    )
                    children.append((header, list(gate.assurance_reasons)))
            if step_failures:
                children.append(
                    (
                        "Collector failures:",
                        [
                            (
                                f"{f.check_id or '(unknown)'} · "
                                f"{f.collector or '(unknown)'} · {f.error}"
                            )
                            for f in step_failures
                        ],
                    )
                )
            for ci, (header, details) in enumerate(children):
                c_last = ci == len(children) - 1
                c_branch = "└──" if c_last else "├──"
                c_cont = "    " if c_last else "│   "
                out.append(f"{cont}{c_branch} {header}")
                for d in details:
                    out.append(f"{cont}{c_cont}{d}")
        if idx != len(log.steps) - 1:
            out.append("")
    return "\n".join(out)


def _check_json(event: EvidenceCollectedEvent) -> dict[str, object]:
    """Serialize one collected-evidence event for the inspect JSON payload."""
    return {
        "check_id": event.check_id,
        "provenance": event.provenance.value,
        "passed": event.passed,
        "status": event.status.value if event.status else None,
        "collector": event.collector,
        "collector_version": event.collector_version,
        "source": event.source,
        "artifact_hash": event.artifact_hash,
        "observed_at": event.observed_at.isoformat() if event.observed_at else None,
        "independently_verified": event.provenance in _INDEPENDENTLY_VERIFIED,
    }


def _policy_from_run(config) -> dict[str, object] | None:  # noqa: ANN001
    """Extract the policy identity (id/version/hash) from a run config snapshot.

    Returns ``None`` when the run carried no policy (schema-1.0 traces or runs
    that did not record a config snapshot), so the JSON payload stays honest
    rather than emitting a fabricated ``null`` policy.
    """
    if config is None or config.policy_id is None:
        return None
    return {
        "id": config.policy_id,
        "version": config.policy_version,
        "hash": config.policy_hash,
    }


def _inspect_json_payload(log: RunLog, *, only_unverified: bool) -> dict[str, object]:
    """Build the machine-readable ``bound inspect --json`` payload (item 14).

    Includes the run/steps/evaluations/outcomes snapshots plus the v0.7 audit
    view: per-check collected evidence with provenance, collector failures,
    decision gates (candidate vs final + assurance), agent action reports, and
    a critical-evidence-coverage summary.
    """
    audit = _RunAuditIndex.from_log(log)
    all_collected = [e for evs in audit.collected.values() for e in evs]
    verified, total, pct = _coverage(all_collected)
    collected_by_step: dict[str, list[dict[str, object]]] = {}
    for step_id, events in audit.collected.items():
        rows = [_check_json(e) for e in _filter_checks(events, only_unverified)]
        if rows:
            collected_by_step[step_id] = rows
    failures_by_step: dict[str, list[dict[str, object]]] = {}
    for step_id, events in audit.failures.items():
        failures_by_step[step_id] = [
            {
                "check_id": e.check_id,
                "collector": e.collector,
                "error": e.error,
                "observed_at": e.observed_at.isoformat() if e.observed_at else None,
            }
            for e in events
        ]
    gates_by_step: dict[str, list[dict[str, object]]] = {}
    for step_id, gates in audit.gates.items():
        gates_by_step[step_id] = [
            {
                "evaluation_id": g.evaluation_id,
                "candidate_decision": g.candidate_decision,
                "final_decision": g.final_decision,
                "assurance": g.assurance.value,
                "assurance_reasons": list(g.assurance_reasons),
            }
            for g in gates
        ]
    actions_by_step: dict[str, list[dict[str, object]]] = {}
    for step_id, actions in audit.actions.items():
        actions_by_step[step_id] = [
            {
                "evaluation_id": a.evaluation_id,
                "intended_action": a.intended_action,
                "reported_action": a.reported_action,
                "reported_provenance": a.reported_provenance.value,
                "observed_action": a.observed_action,
                "observed_provenance": (
                    a.observed_provenance.value if a.observed_provenance else None
                ),
                "new_contract_id": a.new_contract_id,
            }
            for a in actions
        ]
    return {
        "run": log.run.model_dump(mode="json"),
        "steps": [s.model_dump(mode="json") for s in log.steps],
        "evaluations": [e.model_dump(mode="json") for e in log.evaluations],
        "outcomes": [o.model_dump(mode="json") for o in log.outcomes],
        "policy": _policy_from_run(log.run.config),
        "evidence": {
            "collected": collected_by_step,
            "failures": failures_by_step,
        },
        "decision_gates": gates_by_step,
        "actions_reported": actions_by_step,
        "coverage": {
            "verified": verified,
            "total": total,
            "percent": pct,
            "independently_verified": [p.value for p in _INDEPENDENTLY_VERIFIED],
        },
        "only_unverified": only_unverified,
    }


# ---------------------------------------------------------------------------
# Self-contained local HTML timeline (Phase 9.3)
# ---------------------------------------------------------------------------

#: CSS colour per evidence provenance class (used by the HTML timeline).
_PROVENANCE_COLORS: dict[str, str] = {
    "verified": "#2e7d32",
    "observed": "#1976d2",
    "attested": "#6a1b9a",
    "evaluated": "#ef6c00",
    "claimed": "#c62828",
    "defaulted": "#8d6e63",
    "missing": "#9e9e9e",
    "unverified": "#9e9e9e",
}

#: CSS colour per BOUND decision (replan -> accept trajectory highlighted).
_DECISION_COLORS: dict[str, str] = {
    "ACCEPT": "#2e7d32",
    "RETRY": "#ef6c00",
    "REPLAN": "#1565c0",
    "ROLLBACK": "#c62828",
}


def _html_escape(text: str) -> str:
    """Escape a string for safe inclusion in HTML text content."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _sv(value: object) -> str:
    """Return the string value of an enum member or a plain string.

    ``Decision``/``NextAction`` are ``Literal`` type aliases (plain strings),
    while provenance/status/assurance are ``StrEnum`` members, so a single
    helper normalises both to their lower/upper string value for rendering.
    """
    return value.value if hasattr(value, "value") else str(value)


def _render_inspect_html(log: RunLog) -> str:
    """Render a self-contained local HTML timeline from a run log (Phase 9.3).

    Shows plan -> step -> attempt with provenance colour-coded badges and the
    candidate -> final decision / assurance per attempt, so the
    REPLAN -> ACCEPT trajectory is visible at a glance. The output is a single
    HTML document with inline CSS (no hosted service, no external assets).

    Args:
        log: The replayed :class:`RunLog`.

    Returns:
        A complete HTML document as a string.
    """
    run = log.run
    audit = _RunAuditIndex.from_log(log)
    parts: list[str] = ["<!DOCTYPE html>", "<html lang='en'><head><meta charset='utf-8'>"]
    parts.append(f"<title>BOUND run {_html_escape(run.run_id)} timeline</title>")
    parts.append("<style>")
    parts.append(
        "body{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;"
        "margin:24px;color:#222;}"
        "h1{font-size:1.4rem;}h2{font-size:1.1rem;border-bottom:1px solid #eee;"
        "padding-bottom:4px;margin-top:28px;}"
        ".meta{color:#555;font-size:0.9rem;margin-bottom:8px;}"
        ".step{margin:16px 0;padding:12px;border:1px solid #e0e0e0;border-radius:6px;}"
        ".attempt{margin:8px 0 8px 16px;padding:8px;border-left:3px solid #bdbdbd;"
        "background:#fafafa;}"
        ".badge{display:inline-block;padding:2px 8px;border-radius:10px;color:#fff;"
        "font-size:0.75rem;font-weight:600;margin-right:4px;}"
        ".ev{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;"
        "font-size:0.8rem;margin:4px 0;}"
        ".kv{color:#555;}"
    )
    parts.append("</style></head><body>")

    parts.append("<h1>BOUND decision timeline</h1>")
    meta = [
        f"<strong>Run:</strong> {_html_escape(run.run_id)}",
        f"<strong>Task:</strong> {_html_escape(run.task or '(none)')}",
        f"<strong>Status:</strong> {_html_escape(_sv(run.status))}"
        + (" (INCOMPLETE)" if log.incomplete else ""),
        f"<strong>Started:</strong> {_html_escape(_fmt_dt(run.started_at))}",
    ]
    cfg = run.config
    if cfg is not None and cfg.policy_id is not None:
        meta.append(
            f"<strong>Policy:</strong> {_html_escape(cfg.policy_id)}@"
            f"{_html_escape(cfg.policy_version or '?')}"
        )
        if cfg.policy_hash is not None:
            meta.append(f"<strong>Policy hash:</strong> {_html_escape(cfg.policy_hash)}")
    parts.append("<div class='meta'>" + " &middot; ".join(meta) + "</div>")

    if not log.steps:
        parts.append("<p><em>No steps recorded.</em></p>")
        parts.append("</body></html>")
        return "\n".join(parts)

    parts.append("<h2>Plan &rarr; step &rarr; attempt</h2>")
    for step in log.steps:
        parts.append("<div class='step'>")
        parts.append(
            f"<div><strong>Step:</strong> {_html_escape(step.contract_id)} "
            f"<span class='kv'>({_sv(step.status)})</span> "
            f"<span class='kv'>step_id={_html_escape(step.step_id)}</span></div>"
        )
        evals = [e for e in log.evaluations if e.step_id == step.step_id]
        if not evals:
            parts.append("<div class='kv'><em>(no evaluations)</em></div>")
        for ev in evals:
            parts.append("<div class='attempt'>")
            decision = ev.decision if ev.decision else "(none)"
            color = _DECISION_COLORS.get(decision, "#616161")
            parts.append(
                f"<span class='badge' style='background:{color}'>"
                f"{_html_escape(decision)}</span>"
            )
            if ev.attempt is not None:
                parts.append(f"<span class='kv'>attempt {ev.attempt}</span>")
            if ev.score is not None:
                parts.append(f"<span class='kv'>score {ev.score:.4f}</span>")
            parts.append("<br>")
            for row in audit.collected.get(ev.step_id, []):
                prov = _sv(row.provenance) if row.provenance else "missing"
                pcolor = _PROVENANCE_COLORS.get(prov, "#9e9e9e")
                status = _sv(row.status) if row.status else "?"
                parts.append(
                    f"<div class='ev'><span class='badge' style='background:{pcolor}'>"
                    f"{_html_escape(prov)}</span>"
                    f"{_html_escape(row.check_id or row.collector or '?')} "
                    f"<span class='kv'>{_html_escape(status)}</span></div>"
                )
            gate = None
            for g in audit.gates.get(ev.step_id, []):
                if g.evaluation_id == ev.evaluation_id:
                    gate = g
                    break
            if gate is None and audit.gates.get(ev.step_id):
                gate = audit.gates[ev.step_id][-1]
            if gate:
                cd = gate.candidate_decision
                fd = gate.final_decision
                fd_color = _DECISION_COLORS.get(fd, "#616161")
                parts.append(
                    f"<div class='kv'>candidate {_html_escape(cd)} &rarr; "
                    f"<span class='badge' style='background:{fd_color}'>"
                    f"{_html_escape(fd)}</span>"
                    f" assurance {_html_escape(_sv(gate.assurance))}</div>"
                )
            for oc in [o for o in log.outcomes if o.step_id == step.step_id]:
                parts.append(
                    f"<div class='kv'>outcome: {_html_escape(oc.decision)}"
                    f" &rarr; {_html_escape(oc.next_action)}</div>"
                )
            parts.append("</div>")
        parts.append("</div>")

    parts.append(
        "<p class='kv'>ROLLBACK and other control actions are executed by the "
        "agent / integration, not by BOUND. This timeline is a local view of "
        "recorded lineage; no hosted service is contacted.</p>"
    )
    parts.append("</body></html>")
    return "\n".join(parts)


def _run_inspect(args: argparse.Namespace) -> int:
    """Execute ``bound inspect <run_id>``.

    Renders the decision-lineage tree with per-check provenance, candidate vs
    final decision, assurance, collector failures and a critical-evidence-
    coverage summary. ``--json`` emits a machine-readable payload; ``--only-
    unverified`` filters to unverified / claimed / missing / invalid evidence.
    ``--html PATH`` writes a self-contained local HTML timeline (Phase 9.3).
    """
    try:
        response = RunService.inspect(RunInspectRequest(
            run_id=args.run_id,
            only_unverified=args.only_unverified,
            store=_store(),
        ))
    except RunNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NOT_FOUND
    log = response.log
    if args.html is not None:
        html = _render_inspect_html(log)
        Path(args.html).write_text(html, encoding="utf-8")
        print(f"wrote HTML timeline to {args.html}")
        return 0
    if args.json:
        payload = _inspect_json_payload(log, only_unverified=args.only_unverified)
        print(json.dumps(payload, indent=2))
    else:
        print(_render_inspect_tree(log, only_unverified=args.only_unverified))
    return 0


def _run_ui(args: argparse.Namespace) -> int:
    """Execute ``bound ui`` — start the local dashboard.

    Starts a read-only HTTP server on localhost that shows all local runs
    and their decision lineage trees. When ``run_id`` is supplied the
    dashboard opens to that run's detail page.
    """
    from bound.ui import serve

    serve(port=args.port, open_browser=args.open_browser, run_id=args.run_id)
    return 0


def _run_outcome(args: argparse.Namespace) -> int:
    """Execute ``bound outcome --run ...``."""
    step_id = generate_step_id(run_id=args.run, contract_id=args.step, attempt=args.attempt)
    evaluation_id = args.evaluation_id or generate_evaluation_id(
        run_id=args.run, step_id=step_id, attempt=args.attempt
    )
    try:
        response = OutcomeService.record(OutcomeRecordRequest(
            run_id=args.run,
            step_id=step_id,
            evaluation_id=evaluation_id,
            decision=args.decision,
            next_action=args.next_action,
            reason_code=args.reason_code,
            note=args.note,
            store=_store(),
        ))
    except RunNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NOT_FOUND
    if args.json:
        print(json.dumps({
            "run_id": response.run_id,
            "step_id": response.step_id,
            "evaluation_id": response.evaluation_id,
            "decision": response.decision,
            "next_action": response.next_action,
            "reason_code": response.reason_code,
        }, indent=2))
    else:
        print(
            f"recorded outcome for {response.run_id} / {response.step_id}: "
            f"{response.decision} -> {response.next_action}"
        )
    return 0


# ---------------------------------------------------------------------------
# Policy configuration subcommands
# ---------------------------------------------------------------------------


def _load_policy_file(path: str) -> tuple[BoundPolicyConfig | None, str | None]:
    """Load and validate a ``bound-policy.yaml`` file from ``path``.

    Returns a ``(policy, error)`` pair. ``error`` is ``None`` when the file
    parses and validates cleanly; otherwise it is a human-readable message and
    ``policy`` is ``None``.

    Args:
        path: Path to the policy YAML file.

    Returns:
        ``(policy, None)`` on success or ``(None, error_message)`` on failure.
    """
    try:
        policy = load_policy_yaml(path)
    except FileNotFoundError:
        return None, f"error: policy file not found: {path}"
    except ValidationError as exc:
        return None, f"error: invalid policy: {_format_validation_error(exc)}"
    except ValueError as exc:
        return None, f"error: invalid policy: {exc}"
    except yaml.YAMLError as exc:
        return None, f"error: invalid YAML: {exc}"
    return policy, None


def _format_validation_error(exc: ValidationError) -> str:
    """Render a Pydantic ``ValidationError`` as a concise multi-line message."""
    lines: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err.get("loc", ()))
        msg = err.get("msg", "")
        lines.append(f"  {loc}: {msg}" if loc else f"  {msg}")
    return "; ".join(lines) if lines else str(exc)


def _provenance_set(values: list[EvidenceProvenance] | None) -> set[EvidenceProvenance]:
    """Return the set of accepted provenance values (empty when ``None``)."""
    return set(values) if values is not None else set()


def _policy_warnings(policy: BoundPolicyConfig) -> list[str]:
    """Return human-readable validation warnings about a policy's checks.

    The schema is *syntactically* valid, but a policy can still encode
    decisions that BOUND cannot independently back. These warnings surface
    blockers/signals that bind no collector (unmeasurable), checks that
    reference an unknown collector, checks relying *only* on CLAIMED (agent
    self-report) evidence, and subjective checks better handled by a separate
    evaluation step.

    Args:
        policy: A validated :class:`BoundPolicyConfig`.

    Returns:
        An ordered list of warning strings (may be empty).
    """
    warnings: list[str] = []
    collector_ids = set(policy.collectors)

    def _check(check_id: str, collector: str | None, *,
               is_blocker: bool, accepted: list[EvidenceProvenance] | None) -> None:
        if collector is None:
            kind = "blocker" if is_blocker else "check"
            warnings.append(
                f"{kind} '{check_id}' binds no collector; its evidence cannot "
                "be independently collected and will be CLAIMED/MISSING"
            )
        elif collector not in collector_ids:
            warnings.append(
                f"check '{check_id}' references unknown collector '{collector}'"
            )
        acc = _provenance_set(accepted)
        if acc == {EvidenceProvenance.CLAIMED}:
            warnings.append(
                f"check '{check_id}' accepts only CLAIMED evidence; it relies "
                "solely on agent self-report and can never be independently verified"
            )
        # Subjective checks: no collector and no accepted-provenance restriction,
        # or the only accepted provenance is EVALUATED (a judge). These are
        # better handled by a separate evaluation step outside the gate.
        if collector is None and (not acc or EvidenceProvenance.EVALUATED in acc):
            warnings.append(
                f"check '{check_id}' appears subjective/unmeasurable; consider "
                "evaluating it in a separate human/judge step rather than a gate"
            )

    for gate in policy.acceptance_checks:
        _check(gate.id, gate.collector, is_blocker=True,
               accepted=gate.accepted_provenance)
    for gate in policy.risk_checks:
        _check(gate.id, gate.collector, is_blocker=True,
               accepted=gate.accepted_provenance)
    for sig in policy.quality_checks:
        if sig.importance == "ignore":
            continue
        _check(sig.id, sig.collector, is_blocker=False,
               accepted=sig.accepted_provenance)
    return warnings


def _policy_identity_json(policy: BoundPolicyConfig) -> dict[str, object]:
    """Return the ``{id, version, hash}`` identity object for a policy."""
    return {
        "id": policy.policy.id,
        "version": policy.policy.version,
        "hash": compute_policy_hash(policy),
    }


def _gate_summary_line(gate: HardGate) -> str:
    """Render one hard gate as a single human-readable summary line."""
    parts = [f"- {gate.id}", f"[{gate.importance}]"]
    if gate.required:
        parts.append("required")
    parts.append(f"on_failure={gate.on_failure}")
    parts.append(f"on_missing={gate.on_missing}")
    parts.append(f"on_claimed={gate.on_claimed}")
    if gate.minimum_assurance is not None:
        parts.append(f"minimum_assurance={gate.minimum_assurance}")
    if gate.accepted_provenance is not None:
        provs = ",".join(p.value for p in gate.accepted_provenance)
        parts.append(f"accepted_provenance=[{provs}]")
    if gate.collector is not None:
        parts.append(f"collector={gate.collector}")
    return "  ".join(parts)


def _signal_summary_line(sig: WeightedSignal) -> str:
    """Render one weighted signal as a single human-readable summary line."""
    parts = [f"- {sig.id}", f"[{sig.importance}]"]
    override = f" (override {sig.weight})" if sig.weight is not None else ""
    parts.append(f"effective_weight={sig.effective_weight}{override}")
    if sig.accepted_provenance is not None:
        provs = ",".join(p.value for p in sig.accepted_provenance)
        parts.append(f"accepted_provenance=[{provs}]")
    if sig.collector is not None:
        parts.append(f"collector={sig.collector}")
    return "  ".join(parts)


def _budget_summary_line(name: str, dim) -> str:  # noqa: ANN001
    """Render one budget dimension as a single human-readable summary line."""
    parts = [f"- {name}"]
    if not dim.enabled:
        parts.append("disabled")
    soft = dim.soft_limit if dim.soft_limit is not None else "-"
    hard = dim.hard_limit if dim.hard_limit is not None else "-"
    parts.append(f"soft={soft}")
    parts.append(f"on_soft={dim.on_soft}")
    parts.append(f"hard={hard}")
    parts.append(f"on_hard={dim.on_hard}")
    return "  ".join(parts)


def _run_policy_validate(args: argparse.Namespace) -> int:
    """Execute ``bound policy validate <file>``.

    Parses and validates the YAML, then reports any warnings (blockers without
    collectors, checks relying only on claimed evidence, unmeasurable criteria,
    and subjective checks). ``--json`` emits a machine-readable payload.

    Exit codes: ``0`` valid, :data:`EXIT_POLICY_INVALID` (1) when the file does
    not match the schema, :data:`EXIT_POLICY_USAGE` (2) when the file cannot be
    read (usage error).
    """
    response = PolicyService.validate(PolicyValidateRequest(path=args.file))
    if not response.valid:
        error = response.errors[0] if response.errors else "unknown error"
        if response.error_kind == "usage":
            print(f"error: {error}", file=sys.stderr)
            return EXIT_POLICY_USAGE
        print(f"error: invalid policy: {error}", file=sys.stderr)
        return EXIT_POLICY_INVALID

    if args.json:
        payload: dict[str, object] = {
            "valid": True,
            "policy": {
                "id": response.policy.id,
                "version": response.policy.version,
                "hash": response.policy.hash,
            } if response.policy else None,
            "warnings": response.warnings,
        }
        print(json.dumps(payload, indent=2))
    else:
        print(f"policy {response.policy.id}@{response.policy.version}: valid")
        print(f"policy hash: {response.policy.hash}")
        if response.warnings:
            print("")
            print("warnings:")
            for w in response.warnings:
                print(f"  - {w}")
        else:
            print("no warnings")
    return 0


def _run_policy_explain(args: argparse.Namespace) -> int:
    """Execute ``bound policy explain <file>``.

    Renders a concise human-readable explanation of the effective gates
    (blockers, required, on_failure/on_missing/on_claimed, minimum_assurance,
    accepted_provenance), weights (importance tiers -> effective weights and
    numeric overrides) and budgets (soft/hard limits + actions + disabled).
    ``--json`` emits a machine-readable payload.

    Exit codes: ``0`` ok, :data:`EXIT_POLICY_INVALID` (1) when the file does not
    match the schema, :data:`EXIT_POLICY_USAGE` (2) when the file cannot be read.
    """
    try:
        response = PolicyService.explain(PolicyExplainRequest(path=args.file))
    except PolicyLoadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_POLICY_USAGE
    except PolicyValidationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_POLICY_INVALID

    if args.json:
        payload = {
            "policy": {
                "id": response.policy.id,
                "version": response.policy.version,
                "hash": response.policy.hash,
            } if response.policy else None,
            "collectors": response.collectors,
            "acceptance_checks": response.acceptance_checks,
            "quality_checks": response.quality_checks,
            "risk_checks": response.risk_checks,
            "budgets": response.budgets,
            "change_scope": response.change_scope,
            "approvals": response.approvals,
        }
        print(json.dumps(payload, indent=2))
        return 0

    print(response.human_readable)
    return 0


def _run_policy_hash(args: argparse.Namespace) -> int:
    """Execute ``bound policy hash <file>``.

    Canonicalises the policy and prints its SHA-256 hash
    (``"sha256:<hex>"``). ``--json`` emits ``{"hash": "sha256:...", ...}``.

    Exit codes: ``0`` ok, :data:`EXIT_POLICY_INVALID` (1) when the file does not
    match the schema, :data:`EXIT_POLICY_USAGE` (2) when the file cannot be read.
    """
    try:
        response = PolicyService.hash(PolicyHashRequest(path=args.file))
    except PolicyLoadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_POLICY_USAGE
    except PolicyValidationError as exc:
        print(f"error: invalid policy: {exc}", file=sys.stderr)
        return EXIT_POLICY_INVALID

    if args.json:
        payload = {
            "hash": response.hash,
            "policy": {
                "id": response.policy.id,
                "version": response.policy.version,
                "hash": response.policy.hash,
            } if response.policy else None,
        }
        print(json.dumps(payload, indent=2))
    else:
        print(response.hash)
    return 0


def _record_evaluation_for_run(
    args: argparse.Namespace, result: EvaluationResult
) -> dict | int:
    """Record ``step_started`` + ``evaluation_recorded`` for ``bound evaluate --run``."""
    store = _store()
    try:
        store.read_run(args.run)
    except RunNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NOT_FOUND
    step_id = generate_step_id(run_id=args.run, contract_id=args.step, attempt=args.attempt)
    store.start_step(
        args.run,
        contract_id=args.step,
        attempt=args.attempt,
        step_id=step_id,
        description=args.description,
    )
    evaluation_id = generate_evaluation_id(
        run_id=args.run, step_id=step_id, attempt=args.attempt
    )
    store.record_evaluation(
        args.run,
        step_id=step_id,
        attempt=args.attempt,
        scores=result.scores,
        score=result.score,
        threshold=result.threshold,
        decision=result.decision,
        reason_code=_NEXT_ACTION_REASON[_DECISION_NEXT_ACTION[result.decision]],
        evaluation_id=evaluation_id,
    )
    return {
        "run_id": args.run,
        "step_id": step_id,
        "evaluation_id": evaluation_id,
        "attempt": args.attempt,
    }


def _run_evaluate(args: argparse.Namespace) -> int:
    """Execute the ``bound evaluate`` subcommand.

    Builds the :class:`Action`, :class:`EvaluationScores` and
    :class:`BoundCriteria` from the parsed arguments — all validated through
    Pydantic — runs the deterministic :class:`BoundPolicy`, writes the JSON
    result to STDOUT and the steering prompt to STDERR.

    Args:
        args: The parsed namespace carrying ``--action``/``--goal``/``--context``
            and the score/weight/threshold values.

    Returns:
        ``0`` on success, or :data:`EXIT_VALIDATION_ERROR` when the supplied
        inputs fail Pydantic validation (e.g. an out-of-range score or a
        conflict between the deprecated ``--weight`` and the symmetric
        ``--*-weight`` flags).
    """
    try:
        action = Action(description=args.action, goal=args.goal, context=args.context)
        scores = EvaluationScores(
            acceptance=args.acceptance,
            influence=args.influence,
            risk=args.risk,
            cost=args.cost,
        )
        criteria = _build_criteria(args)
    except ValidationError as exc:
        print(f"error: invalid BOUND inputs: {exc}", file=sys.stderr)
        return EXIT_VALIDATION_ERROR

    try:
        response = EvaluationService.evaluate(EvaluateRequest(
            action=action,
            scores=scores,
            criteria=criteria,
            run_id=getattr(args, "run", None),
            step=getattr(args, "step", None),
            attempt=getattr(args, "attempt", 1),
            description=getattr(args, "description", None),
            store=_store(),
        ))
    except EvaluationInputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_VALIDATION_ERROR
    except RunNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NOT_FOUND

    logger.debug(
        "BOUND evaluation complete: decision=%s score=%s",
        response.result.decision,
        response.result.score,
    )

    output = dict(response.payload)
    if response.lineage is not None:
        output["lineage"] = response.lineage
    print(json.dumps(output, indent=2))
    print(response.prompt, file=sys.stderr)
    return 0


def _build_workflow_signals(args: argparse.Namespace) -> CodingWorkflowSignals:
    """Build :class:`CodingWorkflowSignals` from the workflow subcommand args.

    Optional signals default to ``None`` (treated as unobserved by the
    evaluator) rather than zero, preserving the workflow evaluator's
    "ignore missing signals" contract.

    Args:
        args: The parsed namespace carrying the workflow signal values.

    Returns:
        The validated :class:`CodingWorkflowSignals`.
    """
    return CodingWorkflowSignals(
        test_pass_rate=args.test_pass_rate,
        lint_passed=args.lint_passed,
        type_check_passed=args.type_check_passed,
        required_checks_passed=args.required_checks_passed,
        retry_count=args.retry_count,
        tool_call_count=args.tool_call_count,
        token_usage=args.token_usage,
        execution_time_seconds=args.execution_time_seconds,
        files_changed=args.files_changed,
        unexpected_files_changed=args.unexpected_files_changed,
        rollback_available=args.rollback_available,
    )


def _run_evaluate_workflow(args: argparse.Namespace) -> int:
    """Execute the ``bound evaluate-workflow`` subcommand.

    Builds :class:`CodingWorkflowSignals` from the parsed arguments, feeds them
    to a :class:`CodingWorkflowEvaluator` (deriving ``A``/``I``/``R``/``C``
    deterministically — no LLM, no network), runs the deterministic
    :class:`BoundPolicy`, and writes the JSON result (including the input
    ``signals`` and the evaluator ``provenance``) to STDOUT and the steering
    prompt to STDERR.

    Args:
        args: The parsed namespace carrying the workflow signals plus the
            shared weight/threshold values.

    Returns:
        ``0`` on success, or :data:`EXIT_VALIDATION_ERROR` when the supplied
        inputs fail validation (e.g. an out-of-range signal or no acceptance
        evidence at all).
    """
    try:
        action = Action(description=args.action, goal=args.goal, context=args.context)
        signals = _build_workflow_signals(args)
        criteria = _build_criteria(args)
    except ValidationError as exc:
        print(f"error: invalid BOUND inputs: {exc}", file=sys.stderr)
        return EXIT_VALIDATION_ERROR

    try:
        response = EvaluationService.evaluate_workflow(EvaluateWorkflowRequest(
            action=action,
            signals=signals,
            criteria=criteria,
            influence=args.influence if args.influence is not None else 0.0,
            run_id=getattr(args, "run", None),
            step=getattr(args, "step", None),
            attempt=getattr(args, "attempt", 1),
            description=getattr(args, "description", None),
            store=_store(),
        ))
    except EvaluationInputError as exc:
        print(f"error: could not evaluate workflow: {exc}", file=sys.stderr)
        return EXIT_VALIDATION_ERROR
    except RunNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NOT_FOUND

    logger.debug(
        "BOUND workflow evaluation complete: decision=%s score=%s",
        response.result.decision,
        response.result.score,
    )

    print(json.dumps(response.payload, indent=2))
    print(response.prompt, file=sys.stderr)
    return 0


def _run_integration_spec(args: argparse.Namespace) -> int:  # noqa: ARG001
    """Execute the ``bound integration-spec`` subcommand.

    Emits the framework-neutral BOUND integration specification as structured
    JSON to STDOUT. The spec is produced deterministically (no LLM, no network)
    by :func:`bound.integration_spec.integration_spec` and is intended to be
    consumable by any agent integration.

    Args:
        args: The parsed namespace. Unused — the subcommand takes no arguments.

    Returns:
        ``0`` on success.
    """
    from bound.integration_spec import integration_spec

    print(json.dumps(integration_spec(), indent=2))
    return 0


def _run_watch(args: argparse.Namespace) -> int:
    """Execute the ``bound watch`` subcommand.

    Creates a :class:`WatchEngine` with the given policy path and options,
    reads JSONL watch events from stdin, and dispatches them to the engine.

    Args:
        args: The parsed namespace with ``policy``, ``once``, ``json_output``.

    Returns:
        ``0`` on success, ``1`` on error.
    """
    from bound.watch import WatchConfig, WatchEngine, WatchPolicyLoadError, WatchTransportError

    config = WatchConfig(
        policy_path=args.policy,
        once=getattr(args, "once", False),
        json_output=getattr(args, "json_output", False),
    )
    engine = WatchEngine(config, stdin=sys.stdin, stdout=sys.stdout)
    try:
        return engine.run()
    except WatchPolicyLoadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except WatchTransportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# checkpoint CLI commands
# ---------------------------------------------------------------------------


def _run_checkpoint_create(args: argparse.Namespace) -> int:
    """Execute ``bound checkpoint create --run --step``."""
    try:
        response = CheckpointService.create(CheckpointCreateRequest(
            run_id=args.run,
            step_id=args.step,
            message=getattr(args, "message", None),
        ))
    except CheckpointError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except RunNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NOT_FOUND

    if args.json:
        print(json.dumps({
            "checkpoint_id": response.checkpoint_id,
            "run_id": response.run_id,
            "step_id": response.step_id,
            "path": response.path,
            "changed_files_count": response.changed_files_count,
            "untracked_files_count": response.untracked_files_count,
        }, indent=2))
    else:
        print(f"checkpoint {response.checkpoint_id} created for run {response.run_id}")
        print(f"  path: {response.path}")
        print(f"  changed files: {response.changed_files_count}")
        print(f"  untracked files: {response.untracked_files_count}")
    return 0


def _run_checkpoint_inspect(args: argparse.Namespace) -> int:
    """Execute ``bound checkpoint inspect <checkpoint_id>``."""
    try:
        response = CheckpointService.inspect(CheckpointInspectRequest(
            run_id=args.run,
            checkpoint_id=args.checkpoint_id,
        ))
    except CheckpointError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except RunNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NOT_FOUND

    if args.json:
        print(json.dumps(response.model_dump(mode="json"), indent=2, default=str))
    else:
        print(f"Checkpoint: {response.checkpoint_id}")
        print(f"  Run:        {response.run_id}")
        print(f"  Step:       {response.step_id}")
        print(f"  HEAD:       {response.head_commit or '-'}")
        print(f"  Branch:     {response.branch or '-'}")
        print(f"  Timestamp:  {response.timestamp or '-'}")
        print(f"  Scope:      {', '.join(response.scope) if response.scope else '(all)'}")
        print(f"  Changed:    {len(response.changed_files)} file(s)")
        print(f"  Untracked:  {len(response.untracked_files)} file(s)")
        print(f"  Hashes:     {response.artifact_hashes_count} file(s)")
    return 0


def _run_checkpoint_list(args: argparse.Namespace) -> int:
    """Execute ``bound checkpoint list --run``."""
    try:
        response = CheckpointService.list_checkpoints(CheckpointListRequest(
            run_id=args.run,
        ))
    except RunNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NOT_FOUND

    if args.json:
        print(json.dumps({
            "run_id": response.run_id,
            "checkpoint_ids": response.checkpoint_ids,
        }, indent=2))
    else:
        if not response.checkpoint_ids:
            print(f"(no checkpoints found for run {response.run_id})")
            return 0
        print(f"Checkpoints for run {response.run_id}:")
        for cp_id in response.checkpoint_ids:
            print(f"  {cp_id}")
    return 0
def _run_rollback(args: argparse.Namespace) -> int:
    """Execute ``bound rollback --run --checkpoint``."""
    try:
        request = CheckpointRollbackRequest(
            run_id=args.run,
            checkpoint_id=args.checkpoint,
        )

        if args.dry_run:
            from bound.checkpoint import (
                load_checkpoint,
                compute_rollback_preview,
            )
            try:
                cp = load_checkpoint(args.run, args.checkpoint)
            except (FileNotFoundError, RuntimeError) as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            preview = compute_rollback_preview(cp)
            print(f"Rollback preview for {args.checkpoint} (run {args.run}):")
            print(f"  HEAD match:  {preview['head_match']}")
            print(f"  Changed:     {len(preview['changed'])} file(s)")
            print(f"  Added:       {len(preview['added'])} file(s)")
            print(f"  Unchanged:   {len(preview['unchanged'])} file(s)")
            if preview["changed"]:
                print(f"  Files to change:")
                for f in preview["changed"]:
                    print(f"    - {f}")
            if preview["added"]:
                print(f"  Files to restore:")
                for f in preview["added"]:
                    print(f"    - {f}")
            if not preview["head_match"]:
                print("  WARNING: HEAD has diverged since checkpoint was created.")
            print()
            print("Use --execute to perform the rollback.")
            return 0

        if not args.execute:
            print("error: rollback requires --execute to proceed (use --dry-run for preview)", file=sys.stderr)
            return 2

        # Execute rollback
        response = CheckpointService.rollback(request)
        if not response.is_valid:
            print(f"error: rollback failed for {response.checkpoint_id}", file=sys.stderr)
            for issue in response.issues:
                print(f"  - {issue}", file=sys.stderr)
            return 1

        print(f"Rollback to {response.checkpoint_id} completed successfully.")
        if response.preview:
            preview = response.preview
            print(f"  Changed:  {len(preview.get('changed', []))} file(s)")
            print(f"  Added:    {len(preview.get('added', []))} file(s)")
        if response.issues:
            for issue in response.issues:
                print(f"  info: {issue}")
        return 0

    except CheckpointError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except RunNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NOT_FOUND


# ---------------------------------------------------------------------------
# Init command
# ---------------------------------------------------------------------------


def _run_init(args: argparse.Namespace) -> int:
    """Execute the ``bound init`` subcommand.

    Detects tooling in *project_dir*, generates a minimal ``bound-policy.yaml``,
    validates it through :class:`PolicyService`, and either writes it to disk
    or prints it to stdout.

    Args:
        args: Parsed namespace with ``project_dir`` and ``stdout``.

    Returns:
        ``0`` on success, ``1`` on validation failure.
    """
    project_dir = Path(args.project_dir).resolve()

    if not project_dir.is_dir():
        print(f"error: directory not found: {project_dir}", file=sys.stderr)
        return 1

    # --- Detect tooling ---
    print(f"Detecting tooling in {project_dir} ...", file=sys.stderr)
    detections = detect_tooling(project_dir)

    # Print a concise summary to stderr
    _print_detection_summary(detections)

    # --- Generate policy ---
    print("", file=sys.stderr)
    print("Generating bound-policy.yaml ...", file=sys.stderr)
    yaml_content = generate_policy(detections)

    # --- Validate via PolicyService ---
    import tempfile
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(yaml_content)
        tmp_path = tmp.name

    try:
        response = PolicyService.validate(PolicyValidateRequest(path=tmp_path))
        if not response.valid:
            print("error: generated policy failed validation:", file=sys.stderr)
            for err in response.errors:
                print(f"  {err}", file=sys.stderr)
            return 1
        if response.warnings:
            print("Validation warnings:", file=sys.stderr)
            for w in response.warnings:
                print(f"  {w}", file=sys.stderr)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    # --- Output ---
    if args.stdout:
        print(yaml_content)
    else:
        policy_path = project_dir / "bound-policy.yaml"
        if policy_path.exists():
            print(f"error: {policy_path} already exists; refusing to overwrite.", file=sys.stderr)
            return 1
        policy_path.write_text(yaml_content, encoding="utf-8")
        print(f"Wrote {policy_path}", file=sys.stderr)

    # --- Next actions ---
    print("", file=sys.stderr)
    print("Next steps:", file=sys.stderr)
    print("  1. Review the generated bound-policy.yaml", file=sys.stderr)
    print("  2. Adjust uncertain detections (marked with # UNCERTAIN / # NOT FOUND)", file=sys.stderr)
    print("  3. Run: bound policy validate bound-policy.yaml", file=sys.stderr)
    print("  4. Start a run: bound run start --task <description>", file=sys.stderr)
    print("", file=sys.stderr)
    return 0


def _print_detection_summary(detections: ProjectDetections) -> None:
    """Print a human-readable summary of the detections to stderr.

    Args:
        detections: The tooling detections.
    """
    print("  Test framework:", detections.test_framework.name, file=sys.stderr)
    print("  Linter:       ", detections.linter.name, file=sys.stderr)
    print("  Type checker: ", detections.type_checker.name, file=sys.stderr)
    print("  Coverage:     ", detections.coverage.name, file=sys.stderr)
    print("  Build system: ", detections.build_system.name, file=sys.stderr)
    ci = f"{detections.ci_provider.name} ({detections.ci_provider.confidence.value})" if detections.ci_provider else "none"
    print(f"  CI provider:  {ci}", file=sys.stderr)
    if detections.git_branch:
        print(f"  Git branch:   {detections.git_branch}", file=sys.stderr)
    if detections.git_remote:
        print(f"  Git remote:   {detections.git_remote[:80]}", file=sys.stderr)


def _run_mcp(args: argparse.Namespace) -> int:
    """Run the stdio MCP server.

    Args:
        args: Parsed CLI arguments.

    Returns:
        Exit code from the MCP server.
    """
    # Keep the MCP import optional per architecture rules
    try:
        from bound.mcp_server import run_mcp_server
    except ImportError:
        print("error: mcp_server module not available", file=sys.stderr)
        return 1

    return run_mcp_server(once=args.once, json_log=args.json_log)


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI arguments and dispatch to the requested subcommand.

    Args:
        argv: Optional argument vector. Defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code. With no subcommand the CLI exits ``0``. The
        ``evaluate`` and ``evaluate-workflow`` subcommands return ``0`` on
        success or :data:`EXIT_VALIDATION_ERROR` on invalid inputs. ``--help``
        and missing required arguments are handled by ``argparse`` (which exits
        ``0`` / ``2`` respectively).
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    _configure_logging(getattr(args, "verbose", 0))

    func = getattr(args, "func", None)
    if func is None:
        return 0
    return func(args)


if __name__ == "__main__":
    sys.exit(main())
