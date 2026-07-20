"""CLI integration tests for the lineage subcommands (BOUND v0.7.0 §8).

Exercises ``bound run start/finish/list/delete``, ``bound inspect`` and the
``--run``/``--step``/``--attempt`` lineage wiring on ``bound evaluate`` plus
``bound outcome`` through :func:`bound.cli.main`, redirecting storage to a
temp directory via ``BOUND_RUNS_DIR`` so no real ``.bound/runs/`` is touched.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from bound.cli import EXIT_NOT_FOUND, main
from bound.lineage import build_run_config
from bound.lineage_store import LineageStore
from bound.policy_canon import compute_policy_hash
from bound.policy_schema import load_policy_yaml


@pytest.fixture
def cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """Run lineage CLI commands against an isolated ``BOUND_RUNS_DIR``.

    Returns a helper that invokes :func:`bound.cli.main` and yields
    ``(rc, stdout, stderr)`` for a single invocation.
    """
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    monkeypatch.setenv("BOUND_RUNS_DIR", str(runs_dir))

    def run(argv: list[str]) -> tuple[int, str, str]:
        rc = main(argv)
        out, err = capsys.readouterr()
        return rc, out, err

    return run


# Shared knobs for the two evaluations in the smoke trajectory. With default
# weights (all 1.0), I=0.1, R=0.05, C=0.1, T=0.7:
#   attempt 1 (A=0.4): S=0.35 -> gap 0.35 > retry_margin 0.1 -> REPLAN
#   attempt 2 (A=0.95): S=0.9  -> S >= T -> ACCEPT
_EVAL_COMMON = [
    "--action", "Ship the CSV exporter",
    "--goal", "Add lineage to BOUND",
    "--influence", "0.1",
    "--risk", "0.05",
    "--cost", "0.1",
    "--threshold", "0.7",
]


def _build_full_run(cli, *, task: str = "Implement CSV exporter") -> str:
    """Drive the full REPLAN -> ACCEPT trajectory through the CLI.

    Returns the run id so individual tests can inspect/finish/delete it.
    """
    rc, out, _ = cli(["run", "start", task, "--json"])
    assert rc == 0
    run_id = json.loads(out)["run_id"]

    # Attempt 1: weak evidence -> REPLAN.
    rc, out, _ = cli(
        ["evaluate", "--run", run_id, "--step", "PHASE-001", "--attempt", "1",
         "--acceptance", "0.4", *_EVAL_COMMON]
    )
    assert rc == 0
    assert json.loads(out)["decision"] == "REPLAN"

    rc, _, _ = cli(
        ["outcome", "--run", run_id, "--step", "PHASE-001", "--attempt", "1",
         "--decision", "REPLAN", "--note", "switched to csv.DictWriter"]
    )
    assert rc == 0

    # Attempt 2 (replan, -R1 contract id): strong evidence -> ACCEPT.
    rc, out, _ = cli(
        ["evaluate", "--run", run_id, "--step", "PHASE-001-R1", "--attempt", "1",
         "--acceptance", "0.95", *_EVAL_COMMON]
    )
    assert rc == 0
    assert json.loads(out)["decision"] == "ACCEPT"

    rc, _, _ = cli(
        ["outcome", "--run", run_id, "--step", "PHASE-001-R1", "--attempt", "1",
         "--decision", "ACCEPT"]
    )
    assert rc == 0
    return run_id



# ---------------------------------------------------------------------------
# run start
# ---------------------------------------------------------------------------


def test_run_start_prints_run_id(cli) -> None:
    rc, out, _ = cli(["run", "start", "Implement CSV exporter"])
    assert rc == 0
    assert out.strip().startswith("run_")


def test_run_start_json_emits_valid_json_with_run_id(cli) -> None:
    rc, out, _ = cli(["run", "start", "Implement CSV exporter", "--json"])
    assert rc == 0
    payload = json.loads(out)
    assert payload["run_id"].startswith("run_")
    assert payload["task"] == "Implement CSV exporter"
    assert payload["status"] == "started"


# ---------------------------------------------------------------------------
# run list
# ---------------------------------------------------------------------------


def test_run_list_table_lists_runs(cli) -> None:
    cli(["run", "start", "first task"])
    cli(["run", "start", "second task"])
    rc, out, _ = cli(["run", "list"])
    assert rc == 0
    assert "RUN_ID" in out
    assert "first task" in out
    assert "second task" in out


def test_run_list_json_is_valid_json_list(cli) -> None:
    cli(["run", "start", "task one"])
    rc, out, _ = cli(["run", "list", "--json"])
    assert rc == 0
    summaries = json.loads(out)
    assert isinstance(summaries, list)
    assert len(summaries) == 1
    assert summaries[0]["task"] == "task one"
    assert summaries[0]["run_id"].startswith("run_")


def test_run_list_empty_reports_no_runs(cli) -> None:
    rc, out, _ = cli(["run", "list"])
    assert rc == 0
    assert "no lineage runs" in out


# ---------------------------------------------------------------------------
# run finish + inspect status
# ---------------------------------------------------------------------------


def test_run_finish_then_inspect_shows_completed(cli) -> None:
    run_id = _build_full_run(cli)
    rc, _, _ = cli(["run", "finish", run_id, "--status", "completed"])
    assert rc == 0
    rc, out, _ = cli(["inspect", run_id])
    assert rc == 0
    assert "Status: completed" in out


# ---------------------------------------------------------------------------
# inspect tree rendering
# ---------------------------------------------------------------------------


def test_inspect_tree_renders_attempts_decisions_and_scores(cli) -> None:
    run_id = _build_full_run(cli)
    rc, out, _ = cli(["inspect", run_id])
    assert rc == 0
    assert "Attempt 1" in out
    assert "REPLAN" in out
    assert "ACCEPT" in out
    assert "Outcome:" in out
    assert "Score S=" in out
    assert "T=" in out


def test_inspect_json_has_run_steps_evaluations_outcomes(cli) -> None:
    run_id = _build_full_run(cli)
    cli(["run", "finish", run_id, "--status", "completed"])
    rc, out, _ = cli(["inspect", run_id, "--json"])
    assert rc == 0
    payload = json.loads(out)
    for key in ("run", "steps", "evaluations", "outcomes"):
        assert key in payload
    assert len(payload["evaluations"]) == 2
    assert len(payload["outcomes"]) == 2
    assert [e["decision"] for e in payload["evaluations"]] == ["REPLAN", "ACCEPT"]


def test_inspect_incomplete_run_marked(cli) -> None:
    rc, out, _ = cli(["run", "start", "incomplete task", "--json"])
    run_id = json.loads(out)["run_id"]
    rc, out, _ = cli(["inspect", run_id])
    assert rc == 0
    assert "INCOMPLETE" in out


# ---------------------------------------------------------------------------
# run delete
# ---------------------------------------------------------------------------


def test_run_delete_then_inspect_returns_not_found(cli) -> None:
    run_id = _build_full_run(cli)
    rc, out, _ = cli(["run", "delete", run_id])
    assert rc == 0
    assert run_id in out
    rc, _, err = cli(["inspect", run_id])
    assert rc == EXIT_NOT_FOUND
    assert run_id in err


def test_run_delete_missing_run_returns_not_found(cli) -> None:
    rc, _, err = cli(["run", "delete", "run_does_not_exist"])
    assert rc == EXIT_NOT_FOUND
    assert "run_does_not_exist" in err


# ---------------------------------------------------------------------------
# evaluate --run lineage wiring
# ---------------------------------------------------------------------------


def test_evaluate_with_run_records_lineage_block(cli) -> None:
    rc, out, _ = cli(["run", "start", "task", "--json"])
    run_id = json.loads(out)["run_id"]
    rc, out, _ = cli(
        ["evaluate", "--run", run_id, "--step", "PHASE-001", "--attempt", "1",
         "--acceptance", "0.95", *_EVAL_COMMON]
    )
    assert rc == 0
    payload = json.loads(out)
    assert payload["decision"] == "ACCEPT"
    lineage = payload["lineage"]
    assert lineage["run_id"] == run_id
    assert lineage["step_id"].startswith("step_")
    assert lineage["evaluation_id"].startswith("eval_")
    assert lineage["attempt"] == 1


# ---------------------------------------------------------------------------
# inspect provenance visibility (item 14)
# ---------------------------------------------------------------------------


def _append_audit_events(run_id: str, step_id: str, evaluation_id: str) -> None:
    """Append v0.7 audit events (evidence.collected / decision.gated / failure)."""
    store = LineageStore(base_dir=os.environ["BOUND_RUNS_DIR"])
    store.record_evidence_collected(
        run_id,
        step_id=step_id,
        check_id="tests-pass",
        collector="bound.pytest",
        collector_version="0.7.0",
        provenance="verified",
        passed=True,
        source="junit.xml",
    )
    store.record_evidence_collected(
        run_id,
        step_id=step_id,
        check_id="lint-pass",
        collector="bound.command",
        provenance="verified",
        passed=True,
        source="ruff exitcode",
    )
    store.record_evidence_collected(
        run_id,
        step_id=step_id,
        check_id="type-check-pass",
        collector="bound.command",
        provenance="missing",
        passed=None,
        status="missing",
        source="mypy",
    )
    store.record_evidence_collection_failed(
        run_id,
        step_id=step_id,
        check_id="no-critical-security-findings",
        collector="bound.bandit",
        error="collector crash: bandit timed out",
    )
    store.record_decision_gated(
        run_id,
        step_id=step_id,
        evaluation_id=evaluation_id,
        candidate_decision="ACCEPT",
        final_decision="ACCEPT",
        assurance="verified",
        assurance_reasons=["all decision-critical checks independently verified"],
    )


def _run_with_audit_events(cli) -> tuple[str, str]:
    """Start a run, evaluate to ACCEPT, append audit events; return (run_id, step_id)."""
    rc, out, _ = cli(["run", "start", "audit task", "--json"])
    assert rc == 0
    run_id = json.loads(out)["run_id"]
    rc, out, _ = cli(
        ["evaluate", "--run", run_id, "--step", "PHASE-001", "--attempt", "1",
         "--acceptance", "0.95", *_EVAL_COMMON]
    )
    assert rc == 0
    lineage = json.loads(out)["lineage"]
    _append_audit_events(run_id, lineage["step_id"], lineage["evaluation_id"])
    return run_id, lineage["step_id"]


def test_inspect_renders_provenance_assurance_and_coverage(cli) -> None:
    run_id, _ = _run_with_audit_events(cli)
    rc, out, _ = cli(["inspect", run_id])
    assert rc == 0
    # Per-check provenance breakdown.
    assert "Provenance:" in out
    assert "VERIFIED" in out
    assert "tests-pass" in out
    assert "type-check-pass" in out
    # Coverage summary line.
    assert "Critical evidence coverage:" in out
    assert "independently verified" in out
    # Candidate vs final decision + assurance.
    assert "Candidate: ACCEPT → Final: ACCEPT" in out
    assert "Assurance: VERIFIED" in out
    assert "all decision-critical checks independently verified" in out
    # Collector failure surfaced.
    assert "Collector failures:" in out
    assert "bandit timed out" in out


def test_inspect_only_unverified_filters(cli) -> None:
    run_id, _ = _run_with_audit_events(cli)
    rc, out, _ = cli(["inspect", run_id, "--only-unverified"])
    assert rc == 0
    # The MISSING check is kept.
    assert "type-check-pass" in out
    assert "MISSING" in out
    # A verified check's provenance row is filtered out of the breakdown.
    assert "tests-pass" not in out
    # Collector failures still surface (inherently unverifiable).
    assert "Collector failures:" in out


def test_inspect_json_includes_coverage_provenance_and_gates(cli) -> None:
    run_id, _ = _run_with_audit_events(cli)
    rc, out, _ = cli(["inspect", run_id, "--json"])
    assert rc == 0
    payload = json.loads(out)
    for key in ("run", "steps", "evaluations", "outcomes", "evidence",
                "decision_gates", "coverage"):
        assert key in payload
    coverage = payload["coverage"]
    assert coverage["total"] == 3
    assert coverage["verified"] == 2
    assert coverage["percent"] == 67
    collected = payload["evidence"]["collected"]
    assert collected, "expected collected evidence in JSON payload"
    rows = next(iter(collected.values()))
    provs = {row["provenance"] for row in rows}
    assert "verified" in provs
    assert "missing" in provs
    gates = payload["decision_gates"]
    assert gates, "expected at least one decision gate"
    gate = next(iter(gates.values()))[0]
    assert gate["candidate_decision"] == "ACCEPT"
    assert gate["final_decision"] == "ACCEPT"
    assert gate["assurance"] == "verified"
    failures = payload["evidence"]["failures"]
    assert failures, "expected collector failures in JSON payload"


# ---------------------------------------------------------------------------
# Inspect policy display (Phase 9.1) + HTML timeline (Phase 9.3)
# ---------------------------------------------------------------------------

_DEFAULT_POLICY = (
    Path(__file__).resolve().parent.parent / "src" / "bound" / "default_policy.yaml"
)


def _start_run_with_policy(cli, task: str = "policy-governed task") -> tuple[str, str]:
    """Start a run that records a policy config snapshot; return (run_id, hash)."""
    store = LineageStore(base_dir=os.environ["BOUND_RUNS_DIR"])
    policy = load_policy_yaml(_DEFAULT_POLICY)
    cfg = build_run_config(bound_version="0.7.0", policy=policy)
    evt = store.start_run(task, config=cfg)
    return evt.run_id, compute_policy_hash(policy)


def test_inspect_shows_policy_identity_and_hash(cli) -> None:
    """``bound inspect`` shows ``Policy: <id>@<version>`` and the policy hash."""
    run_id, phash = _start_run_with_policy(cli)
    rc, out, _ = cli(["inspect", run_id])
    assert rc == 0
    assert "Policy: coding-default@1.0" in out
    assert f"Policy hash: {phash}" in out
    assert phash.startswith("sha256:")


def test_inspect_json_includes_policy(cli) -> None:
    """``bound inspect --json`` includes the policy id/version/hash block."""
    run_id, phash = _start_run_with_policy(cli)
    rc, out, _ = cli(["inspect", run_id, "--json"])
    assert rc == 0
    payload = json.loads(out)
    assert "policy" in payload
    policy = payload["policy"]
    assert policy["id"] == "coding-default"
    assert policy["version"] == "1.0"
    assert policy["hash"] == phash


def test_inspect_omits_policy_when_not_recorded(cli) -> None:
    """A run with no policy config does not fabricate a policy block."""
    rc, out, _ = cli(["run", "start", "no policy task", "--json"])
    run_id = json.loads(out)["run_id"]
    rc, out, _ = cli(["inspect", run_id])
    assert rc == 0
    assert "Policy:" not in out
    assert "Policy hash:" not in out


def test_inspect_html_writes_self_contained_timeline(cli, tmp_path) -> None:
    """``bound inspect --html`` writes a self-contained local HTML timeline."""
    run_id, _ = _start_run_with_policy(cli)
    html_path = tmp_path / "timeline.html"
    rc, out, _ = cli(["inspect", run_id, "--html", str(html_path)])
    assert rc == 0
    assert "wrote HTML timeline" in out
    text = html_path.read_text(encoding="utf-8")
    assert text.startswith("<!DOCTYPE html>")
    assert "</html>" in text
    # Self-contained: no external asset references.
    assert "http" not in text
    # Policy identity and the run id appear in the timeline.
    assert "coding-default" in text
    assert run_id in text


def test_inspect_html_renders_replan_to_accept_trajectory(cli, tmp_path) -> None:
    """The HTML timeline shows the plan -> step -> attempt REPLAN -> ACCEPT path."""
    run_id = _build_full_run(cli)
    html_path = tmp_path / "trajectory.html"
    rc, out, _ = cli(["inspect", run_id, "--html", str(html_path)])
    assert rc == 0
    text = html_path.read_text(encoding="utf-8")
    # Both decisions appear as colour-coded badges.
    assert "REPLAN" in text
    assert "ACCEPT" in text
    # Provenance badge styling is present.
    assert "class='badge'" in text
    # The two step ids from the trajectory are present.
    assert "PHASE-001" in text



