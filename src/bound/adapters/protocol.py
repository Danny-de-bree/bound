"""ACP — Adapter Control Protocol (v0.9.5).

JSONL-over-stdin/stdout protocol for BOUND ↔ agent communication. Every
line is a complete JSON object with a ``type`` field that determines its
semantics. One message per line, no framing, no streaming chunks.

Event types (agent → BOUND):
    ``task.started`` — Agent acknowledges the task.
    ``step.completed`` — Agent reports a finished step with evidence.
    ``evidence.collected`` — Agent reports evidence gathered during a step.
    ``evaluation.requested`` — Agent requests BOUND evaluation.

Command types (BOUND → agent):
    ``continue`` — Proceed to the next step.
    ``retry`` — Re-execute the current step.
    ``replan`` — Revise strategy, re-execute.
    ``rollback`` — Roll back to the last checkpoint.
    ``shutdown`` — Terminate cleanly.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

# ---------------------------------------------------------------------------
# Canonical type constants
# ---------------------------------------------------------------------------

EVENT_TYPES: frozenset[str] = frozenset(
    {
        "task.started",
        "step.completed",
        "evidence.collected",
        "evaluation.requested",
    }
)

COMMAND_TYPES: frozenset[str] = frozenset(
    {
        "continue",
        "retry",
        "replan",
        "rollback",
        "shutdown",
    }
)

ALL_TYPES: frozenset[str] = EVENT_TYPES | COMMAND_TYPES


# ---------------------------------------------------------------------------
# Message model
# ---------------------------------------------------------------------------


class ACPMessage:
    """A parsed ACP message (lightweight dict wrapper).

    Deliberately *not* a Pydantic model — the protocol is permissive to
    avoid rejecting messages from agents that add extra fields. Only
    ``type`` is required.

    Attributes:
        type: The message type string.
        data: The full parsed JSON dict.
    """

    __slots__ = ("type", "data")

    def __init__(self, data: dict[str, Any]) -> None:
        """Wrap a parsed JSON dict.

        Args:
            data: Parsed JSON object; must contain ``"type"``.

        Raises:
            ValueError: If ``data`` has no ``"type"`` key.
        """
        if "type" not in data:
            raise ValueError("ACP message must contain a 'type' field")
        self.type: str = data["type"]
        self.data: dict[str, Any] = data

    def __repr__(self) -> str:
        return f"ACPMessage(type={self.type!r})"


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def serialize(msg: dict[str, Any]) -> str:
    """Serialise a dict as a JSON line (compact, no trailing newline).

    Args:
        msg: The message dict to serialise.

    Returns:
        A single-line JSON string.
    """
    return json.dumps(msg, default=str, separators=(",", ":"))


def parse_line(line: str) -> ACPMessage:
    """Parse a single JSONL line into an :class:`ACPMessage`.

    Args:
        line: A raw line from the agent's stdout.

    Returns:
        The parsed :class:`ACPMessage`.

    Raises:
        ValueError: If the line is invalid JSON or missing ``type``.
    """
    stripped = line.strip()
    if not stripped:
        raise ValueError("Empty line is not a valid ACP message")
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in ACP line: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"ACP message must be a JSON object, got {type(data).__name__}")
    return ACPMessage(data)


# ---------------------------------------------------------------------------
# Message factory helpers
# ---------------------------------------------------------------------------


def make_task_start(
    task: str,
    plan: dict[str, Any] | None = None,
    candidate_id: str | None = None,
) -> dict[str, Any]:
    """Build a ``task.start`` message to send to the agent.

    Args:
        task: The task description.
        plan: Optional structured plan.
        candidate_id: Optional candidate identifier.

    Returns:
        A ready-to-serialise dict.
    """
    msg: dict[str, Any] = {
        "type": "task.start",
        "task": task,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    if plan is not None:
        msg["plan"] = plan
    if candidate_id is not None:
        msg["candidate_id"] = candidate_id
    return msg


def make_command(
    cmd_type: str,
    **extra: Any,
) -> dict[str, Any]:
    """Build a command message to send to the agent.

    Args:
        cmd_type: One of :data:`COMMAND_TYPES`.
        **extra: Additional key-value pairs for the message.

    Returns:
        A ready-to-serialise dict.

    Raises:
        ValueError: If ``cmd_type`` is unknown.
    """
    if cmd_type not in COMMAND_TYPES:
        raise ValueError(
            f"Unknown command type {cmd_type!r}; expected one of {sorted(COMMAND_TYPES)}"
        )
    msg: dict[str, Any] = {
        "type": cmd_type,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    msg.update(extra)
    return msg


# ---------------------------------------------------------------------------
# Capability model (v1.0)
# ---------------------------------------------------------------------------


class AgentCapabilities(BaseModel):
    """Declared capabilities of a BOUND-aware coding agent.

    Each flag represents a specific integration dimension that BOUND can
    use to decide how to interact with the agent at runtime. Agents that
    support more capabilities enable richer control loops.

    Attributes:
        tool_integration: Agent calls BOUND via MCP or CLI tools (Level A).
        structured_events: Agent emits machine-parseable events (ACP JSONL).
        process_ownership: BOUND can start/stop the agent process.
        bidirectional_control: BOUND can send commands during a session.
        interrupt: BOUND can interrupt the agent mid-task.
        resume: Agent supports resume from a saved checkpoint.
        checkpoint_awareness: Agent natively understands BOUND checkpoints.
        plan_events: Agent emits plan-lifecycle events.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_integration: bool = False
    structured_events: bool = False
    process_ownership: bool = False
    bidirectional_control: bool = False
    interrupt: bool = False
    resume: bool = False
    checkpoint_awareness: bool = False
    plan_events: bool = False


class AgentInstallation(BaseModel):
    """Describes a detected agent installation on the local system.

    Attributes:
        agent_id: Stable machine-readable identifier (e.g. ``"cline"``).
        display_name: Human-readable label (e.g. ``"Cline (VS Code)"``).
        executable: Resolved filesystem path to the agent binary, or
            ``None`` when the agent is managed by an editor / app server.
        version: Detected version string, or ``None`` when unknown.
        installation_type: How the agent is installed — one of ``"mcp"``,
            ``"cli"``, ``"app-server"``, or ``"unknown"``.
        authenticated: Whether the agent appears to have valid credentials.
            ``None`` when authentication status cannot be determined.
        project_config_paths: Paths to agent-specific project configuration
            files found in the current workspace.
        capabilities: The :class:`AgentCapabilities` declared by this agent.
        confidence: Detection confidence — ``"verified"``, ``"probable"``,
            or ``"possible"``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_id: str
    display_name: str
    executable: Path | None
    version: str | None
    installation_type: str
    authenticated: bool | None
    project_config_paths: tuple[Path, ...]
    capabilities: AgentCapabilities
    confidence: str


__all__ = [
    "ACPMessage",
    "AgentCapabilities",
    "AgentInstallation",
    "ALL_TYPES",
    "COMMAND_TYPES",
    "EVENT_TYPES",
    "make_command",
    "make_task_start",
    "parse_line",
    "serialize",
]
