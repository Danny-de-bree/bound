"""Tests for the ACP (Adapter Control Protocol) module."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from bound.adapters.protocol import (
    ALL_TYPES,
    COMMAND_TYPES,
    EVENT_TYPES,
    ACPMessage,
    AgentCapabilities,
    AgentInstallation,
    make_command,
    make_task_start,
    parse_line,
    serialize,
)


class TestSerialize:
    """Serialisation of ACP messages."""

    def test_simple_dict(self) -> None:
        """A basic dict becomes a compact JSON line."""
        result = serialize({"type": "continue"})
        assert result == '{"type":"continue"}'

    def test_nested_values(self) -> None:
        """Nested dicts and lists are serialised correctly."""
        result = serialize({"type": "evidence.collected", "data": {"pass": True}})
        parsed = json.loads(result)
        assert parsed["type"] == "evidence.collected"
        assert parsed["data"] == {"pass": True}

    def test_no_trailing_newline(self) -> None:
        """Serialized output has no trailing newline."""
        result = serialize({"type": "continue"})
        assert not result.endswith("\n")


class TestParseLine:
    """Parsing JSONL lines into ACPMessage."""

    def test_valid_line(self) -> None:
        """A valid JSON line parses correctly."""
        msg = parse_line('{"type":"task.started","task":"Fix bug"}')
        assert msg.type == "task.started"
        assert msg.data["task"] == "Fix bug"

    def test_empty_line_raises(self) -> None:
        """Empty input raises ValueError."""
        with pytest.raises(ValueError, match="Empty line"):
            parse_line("")

    def test_whitespace_only_raises(self) -> None:
        """Whitespace-only input raises ValueError."""
        with pytest.raises(ValueError, match="Empty line"):
            parse_line("   \n")

    def test_invalid_json_raises(self) -> None:
        """Malformed JSON raises ValueError."""
        with pytest.raises(ValueError, match="Invalid JSON"):
            parse_line("{not valid")

    def test_array_raises(self) -> None:
        """A JSON array (not object) raises ValueError."""
        with pytest.raises(ValueError, match="must be a JSON object"):
            parse_line("[1, 2, 3]")

    def test_missing_type_raises(self) -> None:
        """A dict without 'type' raises ValueError."""
        ACPMessage.__new__(ACPMessage)  # bypass __init__
        with pytest.raises(ValueError, match="must contain a 'type'"):
            ACPMessage({"foo": "bar"})

    def test_extra_fields_preserved(self) -> None:
        """Unknown fields are preserved in data dict."""
        msg = parse_line('{"type":"continue","x":1}')
        assert msg.data["x"] == 1


class TestACPConstants:
    """Canonical type constants."""

    def test_event_types(self) -> None:
        """EVENT_TYPES contains expected event type strings."""
        assert "task.started" in EVENT_TYPES
        assert "step.completed" in EVENT_TYPES
        assert "evidence.collected" in EVENT_TYPES
        assert "evaluation.requested" in EVENT_TYPES

    def test_command_types(self) -> None:
        """COMMAND_TYPES contains expected command type strings."""
        assert "continue" in COMMAND_TYPES
        assert "retry" in COMMAND_TYPES
        assert "replan" in COMMAND_TYPES
        assert "rollback" in COMMAND_TYPES
        assert "shutdown" in COMMAND_TYPES

    def test_all_types_union(self) -> None:
        """ALL_TYPES is the union of events and commands."""
        assert ALL_TYPES == EVENT_TYPES | COMMAND_TYPES


class TestMakeTaskStart:
    """Building task.start messages."""

    def test_minimal(self) -> None:
        """Minimal task.start has required fields."""
        msg = make_task_start("Fix bug")
        assert msg["type"] == "task.start"
        assert msg["task"] == "Fix bug"
        assert "timestamp" in msg

    def test_with_plan(self) -> None:
        """Plan dict is included when provided."""
        plan = {"steps": 3}
        msg = make_task_start("Fix bug", plan=plan)
        assert msg["plan"] == plan

    def test_with_candidate_id(self) -> None:
        """Candidate ID is included when provided."""
        msg = make_task_start("Fix bug", candidate_id="cand-001")
        assert msg["candidate_id"] == "cand-001"


class TestMakeCommand:
    """Building command messages."""

    def test_valid_command(self) -> None:
        """A known command type produces a valid message."""
        msg = make_command("continue")
        assert msg["type"] == "continue"
        assert "timestamp" in msg

    def test_unknown_command_raises(self) -> None:
        """An unknown command type raises ValueError."""
        with pytest.raises(ValueError, match="Unknown command type"):
            make_command("fly_to_moon")

    def test_extra_kwargs(self) -> None:
        """Extra kwargs are merged into the message."""
        msg = make_command("retry", reason="test failure")
        assert msg["reason"] == "test failure"

    def test_all_known_commands(self) -> None:
        """Every command in COMMAND_TYPES can be built."""
        for cmd in COMMAND_TYPES:
            msg = make_command(cmd)
            assert msg["type"] == cmd


class TestACPMessageRepr:
    """String representation of ACPMessage."""

    def test_repr(self) -> None:
        """repr includes the message type."""
        msg = parse_line('{"type":"continue"}')
        assert "ACPMessage" in repr(msg)


# ---------------------------------------------------------------------------
# v1.0: capability model tests
# ---------------------------------------------------------------------------


class TestAgentCapabilities:
    """Tests for the AgentCapabilities dataclass."""

    def test_all_defaults_false(self) -> None:
        """Every capability defaults to False — an unknown agent has no capabilities."""
        caps = AgentCapabilities()
        assert caps.tool_integration is False
        assert caps.structured_events is False
        assert caps.process_ownership is False
        assert caps.bidirectional_control is False
        assert caps.interrupt is False
        assert caps.resume is False
        assert caps.checkpoint_awareness is False
        assert caps.plan_events is False

    def test_can_set_individual_capabilities(self) -> None:
        """Each capability can be independently enabled."""
        caps = AgentCapabilities(
            tool_integration=True,
            structured_events=True,
            process_ownership=True,
        )
        assert caps.tool_integration is True
        assert caps.structured_events is True
        assert caps.process_ownership is True
        # Others remain False.
        assert caps.interrupt is False

    def test_is_frozen(self) -> None:
        """AgentCapabilities is immutable (frozen dataclass)."""
        import pytest

        caps = AgentCapabilities(tool_integration=True)
        with pytest.raises(ValidationError):
            caps.tool_integration = False  # type: ignore[misc]

    def test_equality(self) -> None:
        """Two instances with the same flags are equal."""
        a = AgentCapabilities(tool_integration=True)
        b = AgentCapabilities(tool_integration=True)
        assert a == b
        assert a == b

    def test_hashable(self) -> None:
        """Frozen dataclasses are hashable; can be used in sets/dicts."""
        caps = AgentCapabilities()
        _ = {caps: "test"}


class TestAgentInstallation:
    """Tests for the AgentInstallation dataclass."""

    def test_minimal_installation(self) -> None:
        """An AgentInstallation can be constructed with all required fields."""
        inst = AgentInstallation(
            agent_id="test-agent",
            display_name="Test Agent",
            executable=None,
            version=None,
            installation_type="unknown",
            authenticated=None,
            project_config_paths=(),
            capabilities=AgentCapabilities(),
            confidence="possible",
        )
        assert inst.agent_id == "test-agent"
        assert inst.display_name == "Test Agent"
        assert inst.installation_type == "unknown"
        assert inst.confidence == "possible"

    def test_verified_cli_agent(self) -> None:
        """A verified CLI agent with detected executable and version."""
        from pathlib import Path

        inst = AgentInstallation(
            agent_id="claude-code",
            display_name="Claude Code (CLI)",
            executable=Path("/usr/local/bin/claude"),
            version="2.5.0",
            installation_type="cli",
            authenticated=True,
            project_config_paths=(Path(".claude/settings.json"),),
            capabilities=AgentCapabilities(
                tool_integration=True,
                structured_events=True,
                process_ownership=True,
            ),
            confidence="verified",
        )
        assert inst.agent_id == "claude-code"
        assert inst.executable == Path("/usr/local/bin/claude")
        assert inst.version == "2.5.0"
        assert inst.capabilities.tool_integration is True

    def test_is_frozen(self) -> None:
        """AgentInstallation is immutable."""
        import pytest

        inst = AgentInstallation(
            agent_id="test",
            display_name="Test",
            executable=None,
            version=None,
            installation_type="unknown",
            authenticated=None,
            project_config_paths=(),
            capabilities=AgentCapabilities(),
            confidence="possible",
        )
        with pytest.raises(ValidationError):
            inst.agent_id = "changed"  # type: ignore[misc]