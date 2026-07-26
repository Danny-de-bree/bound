"""Claude Code agent adapter for BOUND (v0.9.5).

Spawns ``npx @anthropic-ai/claude-code`` as a child process and parses its
stream-json output into ACP events. Uses ``--dangerously-skip-permissions``
for fully non-interactive operation.

Usage::

    from bound.adapters.claude_code import ClaudeCodeAdapter

    adapter = ClaudeCodeAdapter(
        working_dir="/path/to/workspace",
        model="claude-sonnet-4-20250514",
    )
    adapter.launch("Implement validation", candidate_id="cand-001")
    event = adapter.wait_for_event()
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
from pathlib import Path
from typing import Any

from bound.adapters import AdapterEvent
from bound.adapters.generic import GenericProcessAdapter

logger = logging.getLogger(__name__)

#: Default Claude Code CLI command fragments (task is appended at runtime).
DEFAULT_CLAUDE_COMMAND: list[str] = [
    "npx",
    "@anthropic-ai/claude-code",
    "-p",
    "--dangerously-skip-permissions",
    "--output-format",
    "stream-json",
]

#: Mapping from Claude Code stream-json event type to ACP event type.
_STREAM_EVENT_MAP: dict[str, str] = {
    "assistant": "step.completed",
    "tool_use": "evidence.collected",
    "tool_result": "evidence.collected",
    "user": "evidence.collected",
}


class ClaudeCodeAdapter(GenericProcessAdapter):
    """Adapter for Anthropic's Claude Code CLI agent.

    Spawns ``claude-code`` with ``--output-format stream-json`` and converts
    the streaming events into standard ACP events. Extends
    :class:`GenericProcessAdapter` with Claude Code-specific output parsing.
    """

    def __init__(
        self,
        working_dir: str | Path | None = None,
        model: str | None = None,
        timeout_seconds: float = 300.0,
        **extra_config: Any,
    ) -> None:
        """Initialise the Claude Code adapter.

        Args:
            working_dir: Working directory for the agent process.
            model: Optional model identifier (e.g. ``"claude-sonnet-4-20250514"``).
            timeout_seconds: Default wait timeout in seconds.
            **extra_config: Additional key-value pairs folded into
                :class:`AdapterConfig`.
        """
        cmd = list(DEFAULT_CLAUDE_COMMAND)
        if model:
            cmd.append("--model")
            cmd.append(model)

        super().__init__(
            agent_command=cmd,
            working_dir=str(working_dir) if working_dir else None,
            timeout_seconds=timeout_seconds,
            agent_type="claude-code",
            **extra_config,
        )
        self._model: str | None = model

    @property
    def model(self) -> str | None:
        """The model identifier, or ``None`` if using default."""
        return self._model

    def launch(
        self,
        task: str,
        plan: dict[str, Any] | None = None,
        candidate_id: str | None = None,
    ) -> None:
        """Spawn Claude Code and send the initial ``task.start`` message.

        The task string is appended to the command line after the flags
        (Claude Code reads the prompt as a positional argument).

        Args:
            task: Human-readable task description for the agent.
            plan: Optional structured plan dict.
            candidate_id: The candidate identifier for event tagging.

        Raises:
            RuntimeError: If the agent process cannot be spawned.
        """
        cmd = list(self.config.agent_command) + [task]
        working_dir = self.config.working_dir or "."

        if not Path(working_dir).is_dir():
            raise RuntimeError(
                f"Working directory does not exist: {working_dir}"
            )

        try:
            self._process = subprocess.Popen(
                cmd,
                cwd=str(working_dir),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"Claude Code CLI not found: {cmd[0]}. "
                f"Is @anthropic-ai/claude-code installed?"
            ) from exc

        self._running = True
        self._candidate_id = candidate_id
        self._events.clear()

        self._reader_thread = threading.Thread(
            target=self._read_claude_stdout_loop,
            daemon=True,
        )
        self._reader_thread.start()

        stderr_thread = threading.Thread(
            target=self._read_stderr_loop,
            daemon=True,
        )
        stderr_thread.start()

    def _read_claude_stdout_loop(self) -> None:
        """Background thread: read Claude Code stream-json events from stdout.

        Each line from Claude Code is a JSON object with a ``type`` field
        describing the event (e.g. ``"assistant"``, ``"tool_use"``,
        ``"tool_result"``). Converts them to standard ACP events.
        """
        assert self._process is not None
        assert self._process.stdout is not None

        try:
            for line in self._process.stdout:
                line = line.strip()
                if not line:
                    continue
                acp_event = self._parse_claude_line(line)
                if acp_event is None:
                    continue

                with self._condition:
                    self._events.append(acp_event)
                    self._condition.notify_all()

                if self.on_event is not None:
                    try:
                        self.on_event(acp_event)
                    except Exception:
                        logger.exception("on_event callback raised")
        except Exception:
            logger.debug("Claude Code stdout reader loop exiting", exc_info=True)
        finally:
            self._running = False
            with self._condition:
                self._condition.notify_all()

    def _parse_claude_line(self, line: str) -> AdapterEvent | None:
        """Parse a single Claude Code stream-json line into an ACP event.

        Args:
            line: A raw JSON line from Claude Code's stdout.

        Returns:
            An :class:`AdapterEvent` if the line represents a meaningful
            event, or ``None`` if the line should be skipped.
        """
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            logger.debug("Non-JSON output from Claude Code: %s", exc)
            return None

        if not isinstance(data, dict):
            logger.debug("Non-dict JSON output from Claude Code: %r", type(data))
            return None

        event_type = data.get("type", "")
        acp_type = _STREAM_EVENT_MAP.get(event_type)

        if acp_type is None:
            if event_type:
                logger.debug("Unmapped Claude Code event type: %s", event_type)
            return None

        evidence: dict[str, Any] = {}
        if "message" in data:
            msg = data["message"]
            if isinstance(msg, dict) and "content" in msg:
                evidence["content"] = msg["content"]
            elif isinstance(msg, str):
                evidence["content"] = msg

        if "tool" in data:
            evidence["tool"] = data["tool"]
            if "input" in data:
                evidence["input"] = data["input"]
            if "output" in data:
                evidence["output"] = data["output"]

        if not evidence:
            evidence = {"raw": data}

        return AdapterEvent(
            type=acp_type,
            evidence=evidence,
            candidate_id=self._candidate_id,
        )

    def _write_line(self, line: str) -> None:
        """Write a JSONL line to Claude Code's stdin.

        Claude Code does not natively speak ACP, so control commands
        (continue, retry, rollback) are not supported in the same way.
        This adapter logs a warning for unsupported commands.

        Args:
            line: The JSONL line to write.
        """
        try:
            data = json.loads(line)
            cmd_type = data.get("type", "")
        except (json.JSONDecodeError, ValueError):
            cmd_type = ""

        if cmd_type in ("retry", "replan", "rollback"):
            logger.warning(
                "Claude Code does not support '%s' via stdin. "
                "Use adapter signals instead.",
                cmd_type,
            )

        super()._write_line(line)


__all__ = ["ClaudeCodeAdapter", "DEFAULT_CLAUDE_COMMAND"]
