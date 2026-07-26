"""Tests for ClaudeCodeAdapter — config, command building, event parsing."""

from __future__ import annotations

import json

import pytest

from bound.adapters.claude_code import (
    DEFAULT_CLAUDE_COMMAND,
    ClaudeCodeAdapter,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_default_command() -> None:
    """Default command includes required Claude Code flags."""
    adapter = ClaudeCodeAdapter()
    cmd = adapter.config.agent_command
    assert "npx" in cmd
    assert "@anthropic-ai/claude-code" in cmd
    assert "-p" in cmd
    assert "--dangerously-skip-permissions" in cmd
    assert "--output-format" in cmd
    assert "stream-json" in cmd


def test_model_flag_appended() -> None:
    """When a model is specified, --model is appended to the command."""
    adapter = ClaudeCodeAdapter(model="claude-sonnet-4-20250514")
    cmd = adapter.config.agent_command
    assert "--model" in cmd
    idx = cmd.index("--model")
    assert cmd[idx + 1] == "claude-sonnet-4-20250514"


def test_no_model_flag_by_default() -> None:
    """Without a model, --model is not in the command."""
    adapter = ClaudeCodeAdapter()
    assert "--model" not in adapter.config.agent_command


def test_model_property() -> None:
    """The model property returns the configured model."""
    adapter = ClaudeCodeAdapter(model="claude-opus-4-20250514")
    assert adapter.model == "claude-opus-4-20250514"

    adapter_no_model = ClaudeCodeAdapter()
    assert adapter_no_model.model is None


def test_agent_type_set() -> None:
    """Agent type is 'claude-code'."""
    adapter = ClaudeCodeAdapter()
    assert adapter.config.agent_type == "claude-code"


def test_working_dir_forwarded() -> None:
    """Working directory is forwarded to the config."""
    adapter = ClaudeCodeAdapter(working_dir="/tmp/test")
    assert adapter.config.working_dir == "/tmp/test"


def test_timeout_forwarded() -> None:
    """Timeout is forwarded to the config."""
    adapter = ClaudeCodeAdapter(timeout_seconds=600.0)
    assert adapter.config.timeout_seconds == 600.0


def test_extra_config_forwarded() -> None:
    """Extra config keys are folded into AdapterConfig (only known fields pass).

    AdapterConfig uses ``extra=\"forbid\"``, so unknown keys are rejected.
    This test verifies that the adapter construction fails cleanly on
    invalid extra keys rather than silently ignoring them.
    """
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ClaudeCodeAdapter(extra_key="value")


# ---------------------------------------------------------------------------
# Event parsing
# ---------------------------------------------------------------------------


def test_parse_assistant_message() -> None:
    """Assistant messages map to step.completed events."""
    adapter = ClaudeCodeAdapter()
    adapter._candidate_id = "cand-001"
    line = json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "I'll write tests."}]},
    })
    event = adapter._parse_claude_line(line)
    assert event is not None
    assert event.type == "step.completed"
    assert event.candidate_id == "cand-001"
    assert event.evidence is not None
    assert "content" in event.evidence


def test_parse_tool_use() -> None:
    """Tool use events map to evidence.collected events."""
    adapter = ClaudeCodeAdapter()
    adapter._candidate_id = "cand-001"
    line = json.dumps({
        "type": "tool_use",
        "tool": "execute_command",
        "input": {"command": "pytest"},
    })
    event = adapter._parse_claude_line(line)
    assert event is not None
    assert event.type == "evidence.collected"
    assert event.evidence is not None
    assert event.evidence["tool"] == "execute_command"
    assert event.evidence["input"] == {"command": "pytest"}


def test_parse_tool_result() -> None:
    """Tool result events map to evidence.collected events."""
    adapter = ClaudeCodeAdapter()
    adapter._candidate_id = "cand-001"
    line = json.dumps({
        "type": "tool_result",
        "tool": "execute_command",
        "output": "tests passed",
    })
    event = adapter._parse_claude_line(line)
    assert event is not None
    assert event.type == "evidence.collected"
    assert event.evidence["tool"] == "execute_command"
    assert event.evidence["output"] == "tests passed"


def test_parse_user_message() -> None:
    """User messages map to evidence.collected events."""
    adapter = ClaudeCodeAdapter()
    adapter._candidate_id = "cand-001"
    line = json.dumps({
        "type": "user",
        "message": "What should I do next?",
    })
    event = adapter._parse_claude_line(line)
    assert event is not None
    assert event.type == "evidence.collected"


def test_parse_unmapped_event_returns_none() -> None:
    """Unknown event types are skipped (return None)."""
    adapter = ClaudeCodeAdapter()
    adapter._candidate_id = "cand-001"
    line = json.dumps({"type": "system", "message": "starting"})
    event = adapter._parse_claude_line(line)
    assert event is None


def test_parse_invalid_json_returns_none() -> None:
    """Non-JSON output is skipped gracefully."""
    adapter = ClaudeCodeAdapter()
    adapter._candidate_id = "cand-001"
    event = adapter._parse_claude_line("not json at all")
    assert event is None


def test_parse_non_dict_json_returns_none() -> None:
    """JSON arrays are skipped."""
    adapter = ClaudeCodeAdapter()
    adapter._candidate_id = "cand-001"
    event = adapter._parse_claude_line("[1, 2, 3]")
    assert event is None


def test_parse_empty_type_returns_none() -> None:
    """Dicts without a recognised type are skipped."""
    adapter = ClaudeCodeAdapter()
    adapter._candidate_id = "cand-001"
    line = json.dumps({"something": "else"})
    event = adapter._parse_claude_line(line)
    assert event is None


def test_parse_event_with_raw_fallback() -> None:
    """Events without known evidence fields get raw fallback evidence."""
    adapter = ClaudeCodeAdapter()
    adapter._candidate_id = "cand-001"
    line = json.dumps({"type": "assistant"})
    event = adapter._parse_claude_line(line)
    assert event is not None
    assert event.type == "step.completed"
    assert event.evidence is not None
    assert "raw" in event.evidence


# ---------------------------------------------------------------------------
# DEFAULT_CLAUDE_COMMAND
# ---------------------------------------------------------------------------


def test_default_claude_command_structure() -> None:
    """DEFAULT_CLAUDE_COMMAND has the expected shape."""
    assert isinstance(DEFAULT_CLAUDE_COMMAND, list)
    assert len(DEFAULT_CLAUDE_COMMAND) == 6
    assert DEFAULT_CLAUDE_COMMAND[0] == "npx"
    assert DEFAULT_CLAUDE_COMMAND[1] == "@anthropic-ai/claude-code"
