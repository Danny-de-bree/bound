"""Agent adapter framework for BOUND (v0.9.5).

Provides the abstract base class and data models for the native agent
execution layer. A BOUND adapter spawns and controls a coding agent as a
child process via the ACP, a JSONL-over-stdin/stdout protocol.

The adapter layer is deliberately **language-agnostic**: adapters communicate
with agents exclusively through process I/O, never through Python imports.
This keeps the BOUND core free of provider dependencies.

Usage::

    from bound.adapters import GenericProcessAdapter

    adapter = GenericProcessAdapter(
        agent_command=["python", "-m", "my_agent", "--acp"],
        working_dir="/path/to/workspace",
    )
    adapter.launch(task="Implement validation", plan=plan, candidate_id="cand-001")
    adapter.send_command({"type": "continue"})
    event = adapter.wait_for_event(timeout=30.0)
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


# =========================================================================
# Data models
# =========================================================================


class AdapterConfig(BaseModel):
    """Configuration for an agent adapter instance.

    Attributes:
        agent_type: Human-readable label (e.g. ``"generic"``).
        working_dir: Working directory for the agent process.
        timeout_seconds: Default timeout for wait operations (≥ 0).
        agent_command: CLI list to spawn the agent process.
    """

    model_config = ConfigDict(extra="forbid")

    agent_type: str = Field(default="generic", min_length=1)
    working_dir: str | None = None
    timeout_seconds: float = Field(default=300.0, ge=0.0)
    agent_command: list[str] = Field(default_factory=list, min_length=1)


class AdapterEvent(BaseModel):
    """A single event received from an agent via ACP.

    Attributes:
        type: Event type string (e.g. ``"task.started"``).
        evidence: Optional structured evidence payload.
        decision: Optional decision payload (agent self-report).
        candidate_id: The candidate identifier.
        timestamp: ISO-8601 UTC timestamp of receipt.
    """

    model_config = ConfigDict(extra="allow")

    type: str = Field(min_length=1)
    evidence: dict[str, Any] | None = None
    decision: str | None = None
    candidate_id: str | None = None
    timestamp: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
    )


# =========================================================================
# Abstract base class
# =========================================================================


class AgentAdapter(ABC):
    """Abstract base class for all BOUND agent adapters.

    Every concrete adapter manages exactly one agent process and
    communicates with it through the ACP JSONL protocol.  The adapter is
    deliberately **provider-agnostic**: it knows nothing about the agent's
    internal implementation, only that it speaks ACP on stdin/stdout.

    Attributes:
        config: The :class:`AdapterConfig` used to create this adapter.
        on_event: Optional callback invoked when an :class:`AdapterEvent`
            is received.  Signature: ``(event: AdapterEvent) -> None``.
    """

    def __init__(self, config: AdapterConfig) -> None:
        """Initialise the adapter with the given configuration.

        Args:
            config: The :class:`AdapterConfig` for this adapter instance.
        """
        self.config = config
        self.on_event: Callable[[AdapterEvent], None] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @abstractmethod
    def launch(
        self,
        task: str,
        plan: dict[str, Any] | None = None,
        candidate_id: str | None = None,
    ) -> None:
        """Spawn the agent process and send the initial ``task.start`` message.

        Args:
            task: Human-readable task description for the agent.
            plan: Optional structured plan dict.
            candidate_id: The candidate identifier for event tagging.

        Raises:
            RuntimeError: If the agent process cannot be spawned.
        """
        ...

    @abstractmethod
    def send_command(self, command: dict[str, Any]) -> None:
        """Send a control command to the agent via ACP.

        Valid ``type`` values: ``continue``, ``retry``, ``replan``,
        ``rollback``, ``shutdown``.

        Args:
            command: The command dict (must carry a ``"type"`` key).

        Raises:
            RuntimeError: If the agent is not running or the pipe broke.
        """
        ...

    @abstractmethod
    def wait_for_event(
        self, timeout: float | None = None
    ) -> AdapterEvent | None:
        """Block until an ACP event arrives or time runs out.

        Args:
            timeout: Maximum seconds to wait (uses config default when
                ``None``).

        Returns:
            The parsed :class:`AdapterEvent`, or ``None`` on timeout.

        Raises:
            RuntimeError: If the agent exits unexpectedly while waiting.
        """
        ...

    @abstractmethod
    def terminate(self) -> None:
        """Send ``shutdown`` and cleanly stop the agent process.

        Idempotent — safe to call on an already-terminated adapter.
        """
        ...

    # ------------------------------------------------------------------
    # Checkpoint / rollback
    # ------------------------------------------------------------------

    @abstractmethod
    def capture_checkpoint(self) -> dict[str, Any]:
        """Capture the agent's current state as a checkpoint dict.

        Returns:
            A checkpoint dict for :meth:`restore_checkpoint`.

        Raises:
            RuntimeError: If the agent is not running or capture fails.
        """
        ...

    @abstractmethod
    def restore_checkpoint(self, checkpoint_id: str) -> None:
        """Restore the agent to a previously captured checkpoint.

        Args:
            checkpoint_id: Identifier of the checkpoint to restore.

        Raises:
            KeyError: If ``checkpoint_id`` is unknown.
            RuntimeError: If restoration fails.
        """
        ...


from bound.adapters.protocol import AgentCapabilities, AgentInstallation  # noqa: E402

__all__ = [
    "AdapterConfig",
    "AdapterEvent",
    "AgentAdapter",
    "AgentCapabilities",
    "AgentInstallation",
    "ClaudeCodeAdapter",
    "ClineMCPAdapter",
    "CodexAdapter",
    "CodexMCPConfig",
    "GenericProcessAdapter",
]


# ---------------------------------------------------------------------------
# Lazy imports — keep provider-specific adapters out of the critical path.
# ---------------------------------------------------------------------------

def __getattr__(name: str) -> object:
    """Lazy-load adapter classes to avoid importing provider modules at startup.

    Args:
        name: The attribute name being accessed.

    Returns:
        The requested class or module-level object.

    Raises:
        AttributeError: If the name is not a lazy-loadable adapter.
    """
    _LAZY = {
        "GenericProcessAdapter": "bound.adapters.generic",
        "ClaudeCodeAdapter": "bound.adapters.claude_code",
        "ClineMCPAdapter": "bound.adapters.cline",
        "CodexAdapter": "bound.adapters.codex",
        "CodexMCPConfig": "bound.adapters.codex",
    }
    if name in _LAZY:
        module = __import__(_LAZY[name], fromlist=[name])
        attr = getattr(module, name)
        # Cache in the module's global namespace for future lookups.
        globals()[name] = attr
        return attr
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")