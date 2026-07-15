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
_JSON_FIELDS = {
    "scores",
    "weight",
    "threshold",
    "acceptance_component",
    "influence_component",
    "risk_component",
    "cost_component",
    "score",
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
    """The JSON alone is enough to reconstruct S = (W x A) + I - R - C.

    Auditability requirement: a consumer reading only the JSON must be able to
    recompute the score. We recompute from the components and compare to the
    reported ``score``.
    """
    main(_DOD_ARGS)
    out, _ = capsys.readouterr()
    payload = json.loads(out)

    reconstructed = (
        (payload["weight"] * payload["scores"]["acceptance"])
        + payload["scores"]["influence"]
        - payload["scores"]["risk"]
        - payload["scores"]["cost"]
    )

    assert payload["score"] == pytest.approx(reconstructed, abs=1e-12)
    assert payload["score"] == pytest.approx(0.8, abs=1e-12)
    assert payload["acceptance_component"] == pytest.approx(0.9, abs=1e-12)
    assert payload["decision"] == "ACCEPT"


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
