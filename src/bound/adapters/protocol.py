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
from typing import Any

# ---------------------------------------------------------------------------
# Canonical type constants
# ---------------------------------------------------------------------------

EVENT_TYPES: frozenset[str] = frozenset({
    "task.started",
    "step.completed",
    "evidence.collected",
    "evaluation.requested",
})

COMMAND_TYPES: frozenset[str] = frozenset({
    "continue",
    "retry",
    "replan",
    "rollback",
    "shutdown",
})

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
        raise ValueError(
            f"ACP message must be a JSON object, got {type(data).__name__}"
        )
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
            f"Unknown command type {cmd_type!r}; expected one of "
            f"{sorted(COMMAND_TYPES)}"
        )
    msg: dict[str, Any] = {
        "type": cmd_type,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    msg.update(extra)
    return msg


__all__ = [
    "ACPMessage",
    "ALL_TYPES",
    "COMMAND_TYPES",
    "EVENT_TYPES",
    "make_command",
    "make_task_start",
    "parse_line",
    "serialize",
]