"""Sprint 1 exit criteria verification (v0.8.0).

Verifies that:

1. **Exit 1** — A new visitor can explain BOUND's purpose after watching the
   demo once.  (Documentation check: the README must describe BOUND's purpose
   clearly.)

2. **Exit 2** — Every decision visible in the dashboard can be traced to stored
   lineage.  We create a run, store decisions via the service layer, start the
   dashboard HTTP server, query ``/api/run/{id}``, and verify the returned
   decisions match the stored ones.

3. **Exit 3** — The demo reproduces from a clean checkout without manual
   evidence injection.  We simulate the full demo scenario: create a run,
   evaluate an action, record the outcome, and verify the complete lineage
   without any external data.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
from pathlib import Path
from urllib.request import urlopen, Request

import pytest

from bound.lineage_store import LineageStore
from bound.models import Action, BoundCriteria, EvaluationScores
from bound.services import (
    EvaluationService,
    EvaluateRequest,
    OutcomeRecordRequest,
    OutcomeService,
    RunService,
    RunStartRequest,
    RunFinishRequest,
    RunInspectRequest,
)

logger = logging.getLogger(__name__)

# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture
def temp_store(tmp_path: Path) -> LineageStore:
    """A temporary LineageStore isolated from the real filesystem."""
    return LineageStore(
        base_dir=str(tmp_path / ".bound" / "runs"),
        enabled=True,
    )


def _block_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch socket.socket and socket.create_connection to raise."""

    def _no_network(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError(
            "Sprint exit criteria test triggered a network connection"
        )

    monkeypatch.setattr(socket, "socket", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)


# =========================================================================
# Exit 1 — Documentation / demo readability
# =========================================================================


def test_readme_describes_bound_purpose() -> None:
    """The README must explain BOUND's purpose to a new visitor.

    This is a documentation check for Exit 1: "A new visitor can explain
    BOUND's purpose after watching the demo once."  The README is the first
    thing a visitor sees and must contain a clear statement of what BOUND
    does.  We verify key phrases are present.
    """
    readme = Path(__file__).resolve().parent.parent / "README.md"
    assert readme.exists(), "README.md must exist for the demo to be discoverable"

    text = readme.read_text(encoding="utf-8")

    # The README must state BOUND's core purpose.
    assert "deterministic control harness" in text.lower() or \
           "deterministic control signals" in text.lower() or \
           "deterministic decision harness" in text.lower(), (
        "README must describe BOUND as a deterministic control mechanism"
    )

    # The README must mention the four control decisions.
    assert "ACCEPT" in text, "README must mention the ACCEPT decision"
    assert "RETRY" in text, "README must mention the RETRY decision"
    assert "REPLAN" in text, "README must mention the REPLAN decision"
    assert "ROLLBACK" in text, "README must mention the ROLLBACK decision"

    # The README must mention evidence collection / verification.
    assert "evidence" in text.lower(), "README must mention evidence collection"

    # The README must clarify that BOUND is not an LLM judge.
    assert "LLM" in text, "README must clarify BOUND's relationship to LLMs"


# =========================================================================
# Exit 2 — Decision traceability from dashboard
# =========================================================================


def test_dashboard_api_returns_stored_decision_lineage(temp_store: LineageStore) -> None:
    """Every decision visible in the dashboard can be traced to stored lineage.

    This is the automated verification for Exit 2: we create a run, store a
    decision via the EvaluationService, start the dashboard on a random port,
    query ``/api/run/{id}``, and verify the returned JSON contains the
    expected decision, scores, and lineage.

    The test uses the real HTTP server on localhost to prove the full stack
    (store -> service -> HTTP handler -> JSON response) works end-to-end.
    """
    import random
    import time

    from bound.ui import serve, _DashboardHandler

    # --- Step 1: Create a run and store a decision via the service layer ---
    start_resp = RunService.start(RunStartRequest(
        task="Book the direct flight",
        metadata={"demo": "exit2"},
        store=temp_store,
    ))
    run_id = start_resp.run_id
    assert run_id, "RunService.start must return a run_id"

    scores = EvaluationScores(acceptance=0.9, influence=0.2, risk=0.1, cost=0.2)
    action = Action(
        description="Book the direct flight",
        goal="Travel from Paris to New York",
    )
    criteria = BoundCriteria(weight=1.0, threshold=0.6)

    eval_resp = EvaluationService.evaluate(EvaluateRequest(
        action=action,
        scores=scores,
        criteria=criteria,
        run_id=run_id,
        step="flight-booking",
        attempt=1,
        description="Book the direct flight",
        store=temp_store,
    ))
    assert eval_resp.result.decision == "ACCEPT"
    assert eval_resp.result.score == pytest.approx(0.8, abs=1e-12)
    assert eval_resp.lineage is not None, "Lineage must be recorded"
    expected_step_id = eval_resp.lineage["step_id"]
    expected_eval_id = eval_resp.lineage["evaluation_id"]

    # Record an outcome so the dashboard has complete traceability.
    OutcomeService.record(OutcomeRecordRequest(
        run_id=run_id,
        step_id=expected_step_id,
        evaluation_id=expected_eval_id,
        decision="ACCEPT",
        next_action="continue",
        store=temp_store,
    ))

    # Finish the run so the dashboard sees a complete state.
    RunService.finish(RunFinishRequest(run_id=run_id, store=temp_store))

    # --- Step 2: Start the dashboard server on a localhost port ---
    port = random.randint(20000, 30000)
    _DashboardHandler.lineage_store = temp_store

    server_thread = threading.Thread(
        target=serve,
        kwargs={"port": port, "store": temp_store},
        daemon=True,
    )
    server_thread.start()
    time.sleep(0.5)  # Give the server a moment to start.

    # --- Step 3: Query the dashboard API ---
    api_url = f"http://127.0.0.1:{port}/api/run/{run_id}"
    try:
        req = Request(api_url)
        with urlopen(req, timeout=5) as resp:
            assert resp.status == 200, f"Dashboard API returned {resp.status}"
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        pytest.fail(f"Failed to query dashboard API at {api_url}: {exc}")

    # --- Step 4: Verify the returned data matches the stored lineage ---
    assert "run" in data, "Dashboard API must return 'run' key"
    assert "steps" in data, "Dashboard API must return 'steps' key"
    assert "evaluations" in data, "Dashboard API must return 'evaluations' key"
    assert "outcomes" in data, "Dashboard API must return 'outcomes' key"

    # The run metadata must match.
    assert data["run"]["run_id"] == run_id
    assert data["run"]["task"] == "Book the direct flight"

    # The evaluations must contain the decision we stored.
    evaluations = data["evaluations"]
    assert len(evaluations) >= 1, "At least one evaluation must be stored"

    # Find the evaluation by matching the step_id from the lineage response.
    eval_found = False
    for ev in evaluations:
        if ev.get("step_id") == expected_step_id:
            eval_found = True
            assert ev["decision"] == "ACCEPT", (
                f"Expected ACCEPT, got {ev['decision']}"
            )
            assert ev["score"] == pytest.approx(0.8, abs=1e-12), (
                f"Expected score 0.8, got {ev['score']}"
            )
            assert "scores" in ev
            assert ev["scores"]["acceptance"] == 0.9
            break

    assert eval_found, (
        f"The dashboard API must return the evaluation for step {expected_step_id}"
    )

    # The outcomes must contain the recorded outcome.
    outcomes = data["outcomes"]
    assert len(outcomes) >= 1, "At least one outcome must be stored"


# =========================================================================
# Exit 3 — Demo reproducibility
# =========================================================================


def test_demo_scenario_reproduces_without_manual_evidence_injection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The demo scenario reproduces from a clean checkout without manual evidence.

    This is the automated verification for Exit 3: we simulate the exact demo
    flow that a viewer would watch -- create a run, evaluate an action with
    deterministic scores, inspect the run, and verify the complete lineage is
    reproducible.  No external data, no manual evidence injection, no network.

    The demo flow:
    1. Start a new lineage run.
    2. Evaluate an action (the flight example from the README).
    3. Record the outcome via OutcomeService.
    4. Finish the run.
    5. Inspect the run and verify all lineage is present and consistent.
    """
    _block_sockets(monkeypatch)

    # Ensure no API keys are set (the demo must work without credentials).
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY",
                "DEEPSEEK_API_KEY", "COHERE_API_KEY", "MISTRAL_API_KEY",
                "REPLICATE_API_TOKEN", "TOGETHER_API_KEY",
                "HUGGINGFACEHUB_API_TOKEN", "VERTEX_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    # Create a fresh lineage store (simulates a clean checkout).
    store = LineageStore(
        base_dir=str(tmp_path / ".bound" / "runs"),
        enabled=True,
    )

    # --- Step 1: Start a run ---
    start_resp = RunService.start(RunStartRequest(
        task="Book the direct flight",
        metadata={"demo": "exit3", "version": "0.8.0"},
        store=store,
    ))
    run_id = start_resp.run_id
    assert run_id, "Must get a run_id"
    assert start_resp.status == "started"
    assert start_resp.task == "Book the direct flight"

    # --- Step 2: Evaluate an action ---
    scores = EvaluationScores(acceptance=0.9, influence=0.2, risk=0.1, cost=0.2)
    action = Action(
        description="Book the direct flight",
        goal="Travel from Paris to New York",
    )
    criteria = BoundCriteria(weight=1.0, threshold=0.6)

    eval_resp = EvaluationService.evaluate(EvaluateRequest(
        action=action,
        scores=scores,
        criteria=criteria,
        run_id=run_id,
        step="flight-booking",
        attempt=1,
        description="Book the direct flight",
        store=store,
    ))

    # Verify the deterministic result.
    assert eval_resp.result.decision == "ACCEPT"
    assert eval_resp.result.score == pytest.approx(0.8, abs=1e-12)
    assert eval_resp.result.scores.acceptance == pytest.approx(0.9, abs=1e-12)
    assert eval_resp.result.scores.influence == pytest.approx(0.2, abs=1e-12)
    assert eval_resp.result.scores.risk == pytest.approx(0.1, abs=1e-12)
    assert eval_resp.result.scores.cost == pytest.approx(0.2, abs=1e-12)
    assert eval_resp.result.distance_to_threshold == pytest.approx(0.2, abs=1e-12)

    # The payload must be the standard auditable JSON.
    assert "score" in eval_resp.payload
    assert "decision" in eval_resp.payload
    assert "scores" in eval_resp.payload
    assert eval_resp.payload["decision"] == "ACCEPT"
    assert eval_resp.payload["score"] == pytest.approx(0.8, abs=1e-12)

    # The prompt must be generated (deterministic steering message).
    assert eval_resp.prompt, "A deterministic steering prompt must be generated"
    assert "ACCEPT" in eval_resp.prompt

    # Lineage info must be recorded when run_id is provided.
    assert eval_resp.lineage is not None, "Lineage must be recorded"
    assert "evaluation_id" in eval_resp.lineage, (
        "Lineage must contain an evaluation_id"
    )

    # --- Step 3: Record the outcome ---
    outcome_resp = OutcomeService.record(OutcomeRecordRequest(
        run_id=run_id,
        step_id=eval_resp.lineage["step_id"],
        evaluation_id=eval_resp.lineage["evaluation_id"],
        decision="ACCEPT",
        next_action="continue",
        store=store,
    ))
    assert outcome_resp.run_id == run_id
    assert outcome_resp.decision == "ACCEPT"
    assert outcome_resp.evaluation_id == eval_resp.lineage["evaluation_id"]

    # --- Step 4: Finish the run ---
    finish_resp = RunService.finish(RunFinishRequest(
        run_id=run_id,
        status="completed",
        store=store,
    ))
    assert finish_resp.run_id == run_id
    assert finish_resp.status == "completed"

    # --- Step 5: Inspect the run and verify complete lineage ---
    inspect_resp = RunService.inspect(RunInspectRequest(
        run_id=run_id,
        store=store,
    ))
    log = inspect_resp.log

    # The run metadata must match.
    assert log.run.run_id == run_id
    assert log.run.task == "Book the direct flight"

    # The run must have at least one step, evaluation, and outcome.
    assert len(log.steps) >= 1, "The run must have at least one step"
    assert len(log.evaluations) >= 1, "The run must have at least one evaluation"
    assert len(log.outcomes) >= 1, "The run must have at least one outcome"

    # The evaluation decision must match.
    last_eval = log.evaluations[-1]
    assert last_eval.decision == "ACCEPT", (
        f"Expected ACCEPT, got {last_eval.decision}"
    )
    assert last_eval.score == pytest.approx(0.8, abs=1e-12)

    # The outcome must reference the evaluation.
    last_outcome = log.outcomes[-1]
    assert last_outcome.decision == "ACCEPT"
    assert last_outcome.evaluation_id == last_eval.evaluation_id, (
        "Outcome must reference the evaluation it resulted from"
    )

    # The run must be marked as completed.
    assert log.run.status == "completed", (
        f"Expected completed, got {log.run.status}"
    )

    # --- Step 6: Reproducibility check ---
    # Re-running the exact same evaluation must produce the same result.
    replay = EvaluationService.evaluate(EvaluateRequest(
        action=action,
        scores=scores,
        criteria=criteria,
        store=store,
    ))
    assert replay.result.decision == "ACCEPT"
    assert replay.result.score == pytest.approx(0.8, abs=1e-12)
    assert replay.payload == eval_resp.payload, (
        "The evaluation payload must be reproducible (same inputs -> same output)"
    )