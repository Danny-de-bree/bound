"""Unit and integration tests for the BOUND CLI (Phase 6).

Covers the ``bound evaluate`` subcommand:

* valid invocation writes an auditable JSON result to STDOUT and a readable
  steering prompt to STDERR, exiting ``0``;
* the JSON exposes every term of ``S = (W × A) + I - R - C`` so ``S`` can be
  reconstructed from it alone;
* all score inputs are validated through Pydantic — out-of-range values exit
  non-zero with no JSON on STDOUT;
* ``--help`` (top-level and per-subcommand) and missing-required-argument
  behaviour work as expected.

Tests call :func:`bound.cli.main` directly (capturing STDOUT/STDERR via
``capsys``) for speed and determinism, plus one subprocess test that runs the
real module end-to-end with separate streams.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from bound.cli import EXIT_VALIDATION_ERROR, main

# The exact ``bound evaluate`` invocation from the project's definition-of-done.
_DOD_ARGS = [
    "evaluate",
    "--action", "Book the direct flight",
    "--goal", "Travel from Paris to New York",
    "--acceptance", "0.9",
    "--influence", "0.2",
    "--risk", "0.1",
    "--cost", "0.2",
    "--weight", "1.0",
    "--threshold", "0.6",
]

# Fields the auditable JSON payload must expose (per the CLI JSON-output spec).
# Provenance is only present in workflow mode, so it is asserted separately
# rather than in this strict-equality set.
_JSON_FIELDS = {
    "scores",
    "weights",
    "weight",
    "threshold",
    "retry_margin",
    "rollback_risk_threshold",
    "acceptance_component",
    "influence_component",
    "risk_component",
    "cost_component",
    "score",
    "distance_to_threshold",
    "decision",
}

# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_evaluate_writes_json_to_stdout_and_prompt_to_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A valid evaluation emits JSON to STDOUT and the steering prompt to STDERR.

    This pins the core CLI contract: machine-readable JSON on STDOUT (so a
    consumer can parse it) and the human-readable prompt on STDERR (so the two
    streams never corrupt each other).
    """
    rc = main(_DOD_ARGS)
    out, err = capsys.readouterr()

    assert rc == 0
    # STDOUT is pure JSON.
    payload = json.loads(out)
    assert set(payload) == _JSON_FIELDS
    assert set(payload["scores"]) == {"acceptance", "influence", "risk", "cost"}
    # STDERR carries the steering prompt, not the JSON.
    assert "[BOUND evaluation]" in err
    assert "Decision: ACCEPT" in err
    assert "{" not in err.splitlines()[0]


def test_evaluate_json_is_auditable(capsys: pytest.CaptureFixture[str]) -> None:
    """The JSON alone is enough to reconstruct S = (W_A x A) + (W_I x I) - (W_R x R) - (W_C x C).

    Auditability requirement: a consumer reading only the JSON must be able to
    recompute the score from the four symmetric weights and the four scores.
    We recompute from the weights/scores and compare to the reported ``score``,
    and also confirm the signed distance_to_threshold matches ``S - T``.
    """
    main(_DOD_ARGS)
    out, _ = capsys.readouterr()
    payload = json.loads(out)

    reconstructed = (
        (payload["weights"]["acceptance"] * payload["scores"]["acceptance"])
        + (payload["weights"]["influence"] * payload["scores"]["influence"])
        - (payload["weights"]["risk"] * payload["scores"]["risk"])
        - (payload["weights"]["cost"] * payload["scores"]["cost"])
    )

    assert payload["score"] == pytest.approx(reconstructed, abs=1e-12)
    assert payload["score"] == pytest.approx(0.8, abs=1e-12)
    assert payload["acceptance_component"] == pytest.approx(0.9, abs=1e-12)
    assert payload["decision"] == "ACCEPT"
    assert payload["distance_to_threshold"] == pytest.approx(
        payload["score"] - payload["threshold"], abs=1e-12
    )
    assert payload["weights"] == {
        "acceptance": 1.0,
        "influence": 1.0,
        "risk": 1.0,
        "cost": 1.0,
    }
    assert payload["weight"] == payload["weights"]["acceptance"]


def test_evaluate_default_weight_is_one(capsys: pytest.CaptureFixture[str]) -> None:
    """Omitting ``--weight`` defaults it to 1.0 (mirroring :class:`BoundCriteria`).

    Weight is the only score input with a sensible default, so it stays
    optional while the four dimensions and the threshold remain required.
    """
    args = [a for a in _DOD_ARGS if a not in ("--weight", "1.0")]
    rc = main(args)
    out, _ = capsys.readouterr()

    assert rc == 0
    assert json.loads(out)["weight"] == 1.0


def test_evaluate_context_is_optional(capsys: pytest.CaptureFixture[str]) -> None:
    """``--context`` is optional and, when given, is accepted without error."""
    args = list(_DOD_ARGS) + ["--context", "Direct flight, zero stops, within budget."]
    rc = main(args)
    out, _ = capsys.readouterr()

    assert rc == 0
    assert json.loads(out)["decision"] == "ACCEPT"


# ---------------------------------------------------------------------------
# Pydantic validation -> non-zero exit, no JSON on STDOUT
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("override", "bad_value"),
    [
        ("--acceptance", "1.5"),  # A must be in [0, 1]
        ("--acceptance", "-0.1"),  # A < 0
        ("--influence", "1.5"),  # I must be in [-1, 1]
        ("--risk", "-0.1"),  # R < 0
        ("--cost", "2.0"),  # C must be in [0, 1]
        ("--weight", "-1.0"),  # W must be >= 0
        ("--threshold", "-0.5"),  # T must be >= 0
    ],
)
def test_evaluate_rejects_invalid_scores_via_pydantic(
    capsys: pytest.CaptureFixture[str], override: str, bad_value: str
) -> None:
    """Out-of-range inputs are rejected by Pydantic with a non-zero exit code.

    argparse only checks the values are floats; range constraints are enforced
    by the same Pydantic models the core uses, so the CLI and the library share
    one validation contract. No JSON is written to STDOUT on failure.
    """
    # Replace the matching good value with the bad one.
    flag = override
    args: list[str] = []
    skip_next = False
    for tok in _DOD_ARGS:
        if skip_next:
            skip_next = False
            continue
        if tok == flag:
            args.append(flag)
            args.append(bad_value)
            skip_next = True
            continue
        args.append(tok)

    rc = main(args)
    out, err = capsys.readouterr()

    assert rc == EXIT_VALIDATION_ERROR
    assert rc != 0
    assert out == ""  # no JSON emitted
    assert "error" in err.lower() or "validation" in err.lower()


def test_evaluate_rejects_empty_action(capsys: pytest.CaptureFixture[str]) -> None:
    """An empty ``--action`` fails Action validation (non-empty requirement)."""
    args = list(_DOD_ARGS)
    args[args.index("Book the direct flight")] = "   "
    rc = main(args)
    out, err = capsys.readouterr()

    assert rc == EXIT_VALIDATION_ERROR
    assert out == ""
    assert err.strip() != ""


# ---------------------------------------------------------------------------
# argparse behaviour: --help and missing required arguments
# ---------------------------------------------------------------------------


def test_top_level_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    """``bound --help`` prints usage and exits 0 (argparse handles --help)."""
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    out, _ = capsys.readouterr()

    assert exc.value.code == 0
    assert "bound" in out
    assert "evaluate" in out


def test_evaluate_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    """``bound evaluate --help`` prints the subcommand usage and exits 0."""
    with pytest.raises(SystemExit) as exc:
        main(["evaluate", "--help"])
    out, _ = capsys.readouterr()

    assert exc.value.code == 0
    assert "--acceptance" in out
    assert "--threshold" in out


def test_missing_required_argument_exits_nonzero(capsys: pytest.CaptureFixture[str]) -> None:
    """A missing required argument makes argparse exit non-zero with a usage error."""
    args = [a for a in _DOD_ARGS if a not in ("--threshold", "0.6")]
    with pytest.raises(SystemExit) as exc:
        main(args)
    _, err = capsys.readouterr()

    assert exc.value.code != 0
    assert "threshold" in err


def test_no_subcommand_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    """Invoking ``bound`` with no subcommand exits 0 (no-op, as documented)."""
    rc = main([])
    out, _ = capsys.readouterr()

    assert rc == 0
    assert out == ""


# ---------------------------------------------------------------------------
# End-to-end subprocess test (separate STDOUT/STDERR in a real process)
# ---------------------------------------------------------------------------


def test_evaluate_subprocess_end_to_end() -> None:
    """The real ``bound.cli`` module runs end-to-end with separated streams.

    Spawns ``python -m bound.cli evaluate ...`` so the STDOUT/STDERR separation
    is verified in a genuine process (not just via in-process capture), and the
    ``__main__`` block is exercised.
    """
    repo_root = Path(__file__).resolve().parents[1]
    src_dir = repo_root / "src"
    existing_pythonpath = os.environ.get("PYTHONPATH", "")
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(src_dir), existing_pythonpath]),
    }
    proc = subprocess.run(
        [sys.executable, "-m", "bound.cli", *_DOD_ARGS],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        cwd=str(repo_root),
    )

    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert set(payload) == _JSON_FIELDS
    assert payload["score"] == pytest.approx(0.8, abs=1e-12)
    assert payload["decision"] == "ACCEPT"
    assert "[BOUND evaluation]" in proc.stderr
    assert "Decision: ACCEPT" in proc.stderr



# ---------------------------------------------------------------------------
# v0.2 symmetric weights, retry-margin, rollback threshold, deprecated alias
# ---------------------------------------------------------------------------


def test_evaluate_acceptance_weight_flag_sets_weight(capsys: pytest.CaptureFixture[str]) -> None:
    """``--acceptance-weight`` drives the symmetric weights and the ``weight`` alias.

    The v0.2 symmetric weights flow into the policy, and the deprecated
    ``weight`` alias stays in sync with ``weights.acceptance`` so legacy
    consumers keep working.
    """
    args = [
        "evaluate",
        "--action", "Refactor authentication",
        "--goal", "Ship secure login",
        "--acceptance", "0.9",
        "--influence", "0.2",
        "--risk", "0.1",
        "--cost", "0.2",
        "--acceptance-weight", "2.0",
        "--threshold", "0.6",
    ]
    rc = main(args)
    out, _ = capsys.readouterr()

    assert rc == 0
    payload = json.loads(out)
    assert payload["weights"] == {"acceptance": 2.0, "influence": 1.0, "risk": 1.0, "cost": 1.0}
    assert payload["weight"] == 2.0
    # S = (2.0 x 0.9) + (1.0 x 0.2) - (1.0 x 0.1) - (1.0 x 0.2) = 1.7
    assert payload["score"] == pytest.approx(1.7, abs=1e-12)
    assert payload["decision"] == "ACCEPT"


def test_evaluate_weight_flag_is_deprecated_alias(capsys: pytest.CaptureFixture[str]) -> None:
    """``--weight`` remains a working deprecated alias for ``--acceptance-weight``.

    Supplying ``--weight`` alone folds into ``weights.acceptance``, reproducing
    the v0.1 behaviour (migration path preserved).
    """
    args = [
        "evaluate",
        "--action", "Refactor authentication",
        "--goal", "Ship secure login",
        "--acceptance", "0.9",
        "--influence", "0.2",
        "--risk", "0.1",
        "--cost", "0.2",
        "--weight", "2.0",
        "--threshold", "0.6",
    ]
    rc = main(args)
    out, _ = capsys.readouterr()

    assert rc == 0
    payload = json.loads(out)
    assert payload["weights"]["acceptance"] == 2.0
    assert payload["weight"] == 2.0
    assert payload["score"] == pytest.approx(1.7, abs=1e-12)


def test_evaluate_rejects_weight_and_symmetric_weights_conflict(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Supplying both ``--weight`` and a non-default ``--*-weight`` is rejected.

    The two weight systems must never silently compete; the conflict surfaces
    as a validation error with no JSON on STDOUT.
    """
    args = [
        "evaluate",
        "--action", "Refactor authentication",
        "--goal", "Ship secure login",
        "--acceptance", "0.9",
        "--influence", "0.2",
        "--risk", "0.1",
        "--cost", "0.2",
        "--weight", "2.0",
        "--risk-weight", "0.5",
        "--threshold", "0.6",
    ]
    rc = main(args)
    out, err = capsys.readouterr()

    assert rc == EXIT_VALIDATION_ERROR
    assert out == ""
    assert err.strip() != ""


def test_evaluate_retry_margin_flag_changes_decision(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--retry-margin`` widens/narrows the RETRY band.

    With default margin 0.1 a score 0.05 below T is RETRY; tightening the
    margin to 0.0 pushes the same score into REPLAN.
    """
    base = [
        "evaluate",
        "--action", "Refactor authentication",
        "--goal", "Ship secure login",
        "--acceptance", "0.55",
        "--influence", "0.0",
        "--risk", "0.0",
        "--cost", "0.0",
        "--threshold", "0.6",
    ]

    rc = main(base)
    out, _ = capsys.readouterr()
    assert rc == 0
    assert json.loads(out)["decision"] == "RETRY"

    rc = main([*base, "--retry-margin", "0.0"])
    out, _ = capsys.readouterr()
    assert rc == 0
    assert json.loads(out)["decision"] == "REPLAN"


def test_evaluate_rollback_threshold_flag_overrides_score(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--rollback-risk-threshold`` forces ROLLBACK even for an otherwise-ACCEPT score.

    ROLLBACK is a safety boundary checked before the utility threshold, so a
    high-scoring but too-risky action still rolls back.
    """
    args = [
        "evaluate",
        "--action", "Refactor authentication",
        "--goal", "Ship secure login",
        "--acceptance", "0.9",
        "--influence", "0.2",
        "--risk", "0.1",
        "--cost", "0.2",
        "--rollback-risk-threshold", "0.05",
        "--threshold", "0.6",
    ]
    rc = main(args)
    assert rc == 0
    out, _ = capsys.readouterr()

    assert rc == 0
    payload = json.loads(out)
    assert payload["score"] == pytest.approx(0.8, abs=1e-12)  # would ACCEPT normally
    assert payload["decision"] == "ROLLBACK"
    assert payload["rollback_risk_threshold"] == pytest.approx(0.05, abs=1e-12)


# ---------------------------------------------------------------------------
# evaluate-workflow subcommand
# ---------------------------------------------------------------------------


_WORKFLOW_BASE = [
    "evaluate-workflow",
    "--action", "Implement feature X",
    "--goal", "Complete issue #123",
    "--threshold", "0.6",
]

_WORKFLOW_FIELDS = _JSON_FIELDS | {"signals", "provenance"}


def test_evaluate_workflow_writes_json_and_prompt(capsys: pytest.CaptureFixture[str]) -> None:
    """``evaluate-workflow`` derives scores from signals and emits an auditable payload.

    The four score dimensions are derived from the workflow signals (no LLM,
    no network): a fully-green workflow yields acceptance 1.0. The payload
    carries the input ``signals`` and the per-dimension ``provenance`` evidence
    in addition to the shared auditable fields.
    """
    args = [
        *_WORKFLOW_BASE,
        "--test-pass-rate", "1.0",
        "--lint-passed",
        "--type-check-passed",
        "--retry-count", "2",
        "--tool-call-count", "14",
        "--rollback-available",
    ]
    rc = main(args)
    out, err = capsys.readouterr()

    assert rc == 0
    payload = json.loads(out)
    assert set(payload) == _WORKFLOW_FIELDS
    assert set(payload["scores"]) == {"acceptance", "influence", "risk", "cost"}
    # All-green completion signals -> acceptance is 1.0.
    assert payload["scores"]["acceptance"] == pytest.approx(1.0, abs=1e-12)
    # Score is reconstructable from the weights and the derived scores.
    reconstructed = (
        (payload["weights"]["acceptance"] * payload["scores"]["acceptance"])
        + (payload["weights"]["influence"] * payload["scores"]["influence"])
        - (payload["weights"]["risk"] * payload["scores"]["risk"])
        - (payload["weights"]["cost"] * payload["scores"]["cost"])
    )
    assert payload["score"] == pytest.approx(reconstructed, abs=1e-12)
    # Input signals are echoed for auditability.
    assert payload["signals"]["test_pass_rate"] == 1.0
    assert payload["signals"]["lint_passed"] is True
    assert payload["signals"]["tool_call_count"] == 14
    # Per-dimension provenance is present for every dimension.
    assert set(payload["provenance"]) == {"acceptance", "influence", "risk", "cost"}
    # STDERR carries the steering prompt.
    assert "[BOUND evaluation]" in err


def test_evaluate_workflow_no_acceptance_evidence_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No completion signals at all is a hard error, not a silent zero score.

    CodingWorkflowEvaluator raises when it has no acceptance evidence; the CLI
    surfaces that as a validation error with no JSON on STDOUT.
    """
    args = [
        "evaluate-workflow",
        "--action", "Implement feature X",
        "--goal", "Complete issue #123",
        "--retry-count", "1",
        "--tool-call-count", "3",
        "--threshold", "0.6",
    ]
    rc = main(args)
    out, err = capsys.readouterr()

    assert rc == EXIT_VALIDATION_ERROR
    assert out == ""
    assert err.strip() != ""


def test_evaluate_workflow_rejects_out_of_range_signal(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An out-of-range signal fails Pydantic validation with no JSON on STDOUT."""
    args = [
        "evaluate-workflow",
        "--action", "Implement feature X",
        "--goal", "Complete issue #123",
        "--test-pass-rate", "1.5",
        "--threshold", "0.6",
    ]
    rc = main(args)
    out, err = capsys.readouterr()

    assert rc == EXIT_VALIDATION_ERROR
    assert out == ""
    assert err.strip() != ""


def test_evaluate_workflow_help_lists_signal_flags(capsys: pytest.CaptureFixture[str]) -> None:
    """``bound evaluate-workflow --help`` documents the workflow signal flags."""
    with pytest.raises(SystemExit) as exc:
        main(["evaluate-workflow", "--help"])
    out, _ = capsys.readouterr()

    assert exc.value.code == 0
    assert "--test-pass-rate" in out
    assert "--rollback-available" in out
    assert "--threshold" in out


def test_evaluate_workflow_respects_weights(capsys: pytest.CaptureFixture[str]) -> None:
    """``evaluate-workflow`` honours the same ``--*-weight`` flags as ``evaluate``."""
    args = [
        *_WORKFLOW_BASE,
        "--test-pass-rate", "1.0",
        "--lint-passed",
        "--type-check-passed",
        "--retry-count", "0",
        "--tool-call-count", "0",
        "--rollback-available",
        "--cost-weight", "0.0",
    ]
    rc = main(args)
    out, _ = capsys.readouterr()

    assert rc == 0
    payload = json.loads(out)
    assert payload["weights"]["cost"] == 0.0
    # Zeroing the cost weight means the cost term cannot drag the score below 1.0.
    assert payload["score"] == pytest.approx(1.0, abs=1e-12)
    assert payload["decision"] == "ACCEPT"
