from __future__ import annotations

import importlib
import sys

import pytest


def test_agent_control_loop_trajectory(capsys: pytest.CaptureFixture[str]) -> None:
    """The example produces the mandated REPLAN -> RETRY -> ACCEPT trajectory.

    The decisions are computed by BOUND from StepContract + ExecutionEvidence +
    ContractEvaluator + BoundPolicy; this test pins the resulting trajectory and
    summary so a regression that breaks the loop or hardcodes a decision fails
    here.
    """
    # Import the example module from the examples/ directory (not on sys.path by
    # default), then run its main() capturing the printed trajectory.
    import pathlib

    examples_dir = pathlib.Path(__file__).resolve().parent.parent / "examples"
    sys.path.insert(0, str(examples_dir))
    try:
        module = importlib.import_module("agent_control_loop")
    finally:
        sys.path.remove(str(examples_dir))

    rc = module.main()
    out = capsys.readouterr().out

    assert rc == 0
    # The three mandated decisions, in order, computed by BOUND (not hardcoded).
    assert "decision: REPLAN" in out
    assert "decision: RETRY" in out
    assert "decision: ACCEPT" in out
    # The decisions appear in the required order.
    assert out.index("REPLAN") < out.index("RETRY") < out.index("ACCEPT")
    # Control-action translation is present for each.
    assert "control action: replan" in out
    assert "control action: retry" in out
    assert "control action: continue" in out
    # Summary fields mandated by Phase 7.
    assert "decisions observed:        ['REPLAN', 'RETRY', 'ACCEPT']" in out
    assert "attempts evaluated:        3" in out
    assert "final score (ACCEPT):      0.9" in out
    assert "acceptance threshold T:    0.7" in out
    # Avoided extra steps are explicitly labelled simulated (not measured).
    assert "[simulated]" in out
    assert "SIMULATED" in out
