"""Tests for the ``bound integration-spec`` CLI subcommand (Phase 3).

Pins that the command emits a framework-neutral, machine-readable integration
specification as structured JSON to STDOUT, with the four mandated sections
(WHEN TO CALL / WHEN NOT / REQUIRED FLOW / EVIDENCE RULE), the deterministic
decision->control mapping, and a clean process exit. The spec is produced
deterministically (no LLM, no network).
"""

from __future__ import annotations

import json

import pytest

from bound.cli import main
from bound.integration_spec import integration_spec


def test_integration_spec_emits_valid_json_to_stdout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``bound integration-spec`` writes valid JSON to STDOUT and nothing to STDERR."""
    rc = main(["integration-spec"])
    out, err = capsys.readouterr()

    assert rc == 0
    payload = json.loads(out)
    assert isinstance(payload, dict)
    assert err == ""


def test_integration_spec_contains_required_sections(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The JSON carries all four mandated sections plus the control mapping.

    Phase 3 requires WHEN TO CALL / WHEN NOT / REQUIRED FLOW / EVIDENCE RULE; the
    decision->control mapping is included so an agent can wire its control flow
    straight from the spec.
    """
    main(["integration-spec"])
    out, _ = capsys.readouterr()
    payload = json.loads(out)

    for key in (
        "spec_version",
        "when_to_call",
        "when_not_to_call",
        "required_flow",
        "evidence_rule",
        "decision_to_control",
    ):
        assert key in payload, f"missing section: {key}"


def test_integration_spec_when_to_call_matches_todo(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``when_to_call`` lists exactly the Phase 3 call-site guidance."""
    main(["integration-spec"])
    out, _ = capsys.readouterr()
    payload = json.loads(out)

    assert payload["when_to_call"] == [
        "after a meaningful plan step",
        "after implementation plus verification",
        "after a retry",
        "before deciding to continue refining the same objective",
    ]


def test_integration_spec_when_not_to_call_matches_todo(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``when_not_to_call`` lists exactly the Phase 3 anti-patterns."""
    main(["integration-spec"])
    out, _ = capsys.readouterr()
    payload = json.loads(out)

    assert payload["when_not_to_call"] == [
        "after every token",
        "after every file read",
        "after every shell command",
        "after every low-level tool call",
    ]


def test_integration_spec_required_flow_matches_todo(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``required_flow`` encodes the mandated StepContract -> apply-decision flow.

    The Phase 3 flow is: StepContract -> agent executes -> collect observable
    evidence -> evaluate with BOUND -> apply control decision. Each stage must
    be present and ordered.
    """
    main(["integration-spec"])
    out, _ = capsys.readouterr()
    payload = json.loads(out)

    flow = payload["required_flow"]
    names = [stage["name"] for stage in flow]
    assert names == [
        "define_contract",
        "execute",
        "collect_evidence",
        "evaluate",
        "apply_control_decision",
    ]
    assert [stage["step"] for stage in flow] == [1, 2, 3, 4, 5]


def test_integration_spec_evidence_rule_matches_todo(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``evidence_rule`` states the no-fabrication principle and its handling."""
    main(["integration-spec"])
    out, _ = capsys.readouterr()
    payload = json.loads(out)

    rule = payload["evidence_rule"]
    assert rule["principle"] == "Never fabricate unavailable evidence."
    assert rule["if_evidence_is_unavailable"] == [
        "represent it as unavailable",
        "allow the configured deterministic policy to handle it",
        "never convert assumptions into successful checks",
    ]


def test_integration_spec_decision_mapping(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The spec carries the exact deterministic decision->control mapping."""
    main(["integration-spec"])
    out, _ = capsys.readouterr()
    payload = json.loads(out)

    assert payload["decision_to_control"] == {
        "ACCEPT": "continue",
        "RETRY": "retry",
        "REPLAN": "replan",
        "ROLLBACK": "rollback",
    }


def test_integration_spec_is_deterministic(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Two runs emit byte-identical JSON (the spec is a pure function)."""
    main(["integration-spec"])
    out1, _ = capsys.readouterr()
    main(["integration-spec"])
    out2, _ = capsys.readouterr()

    assert out1 == out2


def test_integration_spec_help_lists_subcommand(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``bound --help`` advertises the ``integration-spec`` subcommand."""
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    out, _ = capsys.readouterr()

    assert exc.value.code == 0
    assert "integration-spec" in out


def test_integration_spec_function_is_pure() -> None:
    """``integration_spec()`` returns identical, JSON-serialisable dicts each call."""
    first = integration_spec()
    second = integration_spec()

    assert first == second
    # Round-trips through JSON without loss (no non-serialisable objects).
    assert json.loads(json.dumps(first)) == first
    assert first["spec_version"] == 1
