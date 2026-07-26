"""Generic process adapter for BOUND (v0.9.5).

Spawns any CLI agent as a child process and communicates via the ACP JSONL
protocol on stdin/stdout. This is the reference adapter implementation —
language-agnostic, import-free, and suitable for any agent that can speak
ACP.

Usage::

    from bound.adapters import GenericProcessAdapter

    adapter = GenericProcessAdapter(
        agent_command=["python", "-m", "my_agent", "--acp"],
        working_dir="/path/to/workspace",
    )
    adapter.launch("Implement validation", plan=plan, candidate_id="cand-001")
    event = adapter.wait_for_event(timeout=30.0)
    adapter.send_command({"type": "continue"})
    adapter.terminate()
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bound.adapters import AdapterConfig, AdapterEvent, AgentAdapter
from bound.adapters.protocol import make_command, make_task_start, parse_line, serialize

logger = logging.getLogger(__name__)

#: Grace period (seconds) after sending ``shutdown`` before force-killing.
_SHUTDOWN_GRACE: float = 5.0

#: Poll interval (seconds) when reading agent stdout lines.
_POLL_INTERVAL: float = 0.05


class GenericProcessAdapter(AgentAdapter):
    """Reference adapter that spawns a CLI agent as a subprocess.

    Communicates with the agent through ACP JSONL on stdin/stdout.
    All events are parsed from the agent's stdout; commands are written
    to the agent's stdin.  Stderr from the agent is logged at WARNING.

    This adapter is **language-agnostic**: the agent can be written in any
    language as long as it reads JSONL commands from stdin and writes JSONL
    events to stdout.
    """

    def __init__(
        self,
        agent_command: list[str] | None = None,
        working_dir: str | Path | None = None,
        timeout_seconds: float = 300.0,
        agent_type: str = "generic",
        **extra_config: Any,
    ) -> None:
        """Initialise the adapter.

        Args:
            agent_command: The CLI to spawn (e.g. ``["node", "agent.js"]``).
                When omitted, taken from ``extra_config``.
            working_dir: Working directory for the agent process.
            timeout_seconds: Default wait timeout in seconds.
            agent_type: Human-readable label for this agent type.
            **extra_config: Additional key-value pairs folded into the
                underlying :class:`AdapterConfig`.
        """
        cmd = agent_command or extra_config.pop("agent_command", ["echo", "noop"])
        config_data: dict[str, Any] = {
            "agent_type": agent_type,
            "timeout_seconds": timeout_seconds,
            "agent_command": cmd,
        }
        if working_dir is not None:
            config_data["working_dir"] = str(working_dir)
        config_data.update(extra_config)
        config = AdapterConfig(**config_data)
        super().__init__(config)

        self._process: subprocess.Popen[str] | None = None
        self._running: bool = False
        self._reader_thread: threading.Thread | None = None
        self._events: list[AdapterEvent] = []
        self._lock: threading.Lock = threading.Lock()
        self._condition: threading.Condition = threading.Condition(self._lock)
        self._checkpoints: dict[str, dict[str, Any]] = {}
        self._candidate_id: str | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def launch(
        self,
        task: str,
        plan: dict[str, Any] | None = None,
        candidate_id: str | None = None,
    ) -> None:
        """Spawn the agent process and send ``task.start``."""
        if self._running:
            raise RuntimeError("Adapter already running; call terminate() first")

        self._candidate_id = candidate_id
        cwd = Path(self.config.working_dir).resolve() if self.config.working_dir else Path.cwd()
        cmd = self.config.agent_command

        logger.info("Launching agent: %s (cwd=%s, candidate=%s)", cmd, cwd, candidate_id)

        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=cwd,
                env={**os.environ},
                start_new_session=True,
            )
        except OSError as exc:
            raise RuntimeError(f"Failed to spawn {cmd!r}: {exc}") from exc

        self._running = True
        self._events.clear()

        self._reader_thread = threading.Thread(
            target=self._read_stdout_loop,
            name=f"acp-reader-{os.getpid()}",
            daemon=True,
        )
        self._reader_thread.start()

        threading.Thread(
            target=self._read_stderr_loop,
            name=f"acp-stderr-{os.getpid()}",
            daemon=True,
        ).start()

        msg = make_task_start(task=task, plan=plan, candidate_id=candidate_id)
        self._write_line(serialize(msg))

    def send_command(self, command: dict[str, Any]) -> None:
        """Send a control command to the agent via ACP."""
        if not self._running or self._process is None:
            raise RuntimeError("Agent is not running")
        if "type" not in command:
            raise ValueError("Command dict must contain a 'type' key")
        if "timestamp" not in command:
            command = {**command, "timestamp": datetime.now(UTC).isoformat()}
        line = serialize(command)
        logger.debug("Sending command: %s", line)
        self._write_line(line)

    def wait_for_event(self, timeout: float | None = None) -> AdapterEvent | None:
        """Block until an ACP event arrives or time runs out."""
        deadline = time.monotonic() + (
            timeout if timeout is not None else self.config.timeout_seconds
        )

        with self._condition:
            while True:
                if self._events:
                    event = self._events.pop(0)
                    logger.debug("Dequeued event: %s", event.type)
                    return event

                if not self._running or (
                    self._process is not None and self._process.poll() is not None
                ):
                    rc = self._process.returncode if self._process else None
                    raise RuntimeError(f"Agent process exited unexpectedly (rc={rc})")

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    logger.debug("wait_for_event timed out")
                    return None

                self._condition.wait(timeout=min(remaining, _POLL_INTERVAL))

    def terminate(self) -> None:
        """Send ``shutdown`` and cleanly stop the agent process."""
        if not self._running:
            return

        try:
            self.send_command(make_command("shutdown"))
        except (RuntimeError, BrokenPipeError, OSError):
            logger.debug("Could not send shutdown (pipe already closed)")

        self._running = False
        if self._process is not None:
            try:
                self._process.wait(timeout=_SHUTDOWN_GRACE)
            except subprocess.TimeoutExpired:
                logger.warning("Agent did not exit; force-killing")
                self._kill_process()

        with self._condition:
            self._condition.notify_all()

        if self._reader_thread is not None and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=2.0)

        self._process = None
        logger.info("Agent terminated")

    # ------------------------------------------------------------------
    # Checkpoint / rollback
    # ------------------------------------------------------------------

    def capture_checkpoint(self) -> dict[str, Any]:
        """Capture the current agent state as a checkpoint dict."""
        if not self._running or self._process is None:
            raise RuntimeError("Agent is not running")

        cp_id = f"cp_{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"
        checkpoint: dict[str, Any] = {
            "checkpoint_id": cp_id,
            "candidate_id": self._candidate_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "source": "generic_adapter",
        }

        try:
            self.send_command(
                {
                    "type": "checkpoint.capture",
                    "checkpoint_id": cp_id,
                    "timestamp": checkpoint["timestamp"],
                }
            )
            event = self.wait_for_event(timeout=5.0)
            if event is not None and event.type == "checkpoint.captured":
                checkpoint.update(event.model_dump(exclude={"type"}))
        except Exception:
            logger.debug("Agent did not respond to checkpoint.capture; using synthetic")

        self._checkpoints[cp_id] = checkpoint
        logger.info("Checkpoint %s captured", cp_id)
        return checkpoint

    def restore_checkpoint(self, checkpoint_id: str) -> None:
        """Restore the agent to a previously captured checkpoint."""
        if not self._running or self._process is None:
            raise RuntimeError("Agent is not running")
        if checkpoint_id not in self._checkpoints:
            raise KeyError(
                f"Checkpoint {checkpoint_id!r} not found; available: {list(self._checkpoints)}"
            )
        cp = self._checkpoints[checkpoint_id]
        self.send_command(
            {
                "type": "checkpoint.restore",
                "checkpoint_id": checkpoint_id,
                "timestamp": datetime.now(UTC).isoformat(),
                "data": cp,
            }
        )
        logger.info("Checkpoint %s restore command sent", checkpoint_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write_line(self, line: str) -> None:
        """Write a JSONL line to the agent's stdin."""
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("Agent stdin is not available")
        try:
            self._process.stdin.write(line + "\n")
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self._running = False
            raise RuntimeError(f"Failed to write to agent stdin: {exc}") from exc

    def _read_stdout_loop(self) -> None:
        """Background thread: read JSONL events from agent stdout."""
        assert self._process is not None
        assert self._process.stdout is not None
        try:
            for line in self._process.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    acp_msg = parse_line(line)
                except ValueError as exc:
                    logger.warning("Bad ACP line from agent: %s", exc)
                    continue

                event = AdapterEvent(
                    type=acp_msg.type,
                    evidence=acp_msg.data.get("evidence"),
                    decision=acp_msg.data.get("decision"),
                    candidate_id=(acp_msg.data.get("candidate_id") or self._candidate_id),
                )
                with self._condition:
                    self._events.append(event)
                    self._condition.notify_all()

                if self.on_event is not None:
                    try:
                        self.on_event(event)
                    except Exception:
                        logger.exception("on_event callback raised")
        except Exception:
            logger.debug("Stdout reader loop exiting", exc_info=True)
        finally:
            self._running = False
            with self._condition:
                self._condition.notify_all()

    def _read_stderr_loop(self) -> None:
        """Background thread: log agent stderr at WARNING level."""
        assert self._process is not None
        assert self._process.stderr is not None
        try:
            for line in self._process.stderr:
                line = line.rstrip()
                if line:
                    logger.warning("Agent stderr: %s", line)
        except Exception:
            logger.debug("Stderr reader loop exiting", exc_info=True)

    def _kill_process(self) -> None:
        """Force-kill the agent process and its children."""
        if self._process is None:
            return
        with contextlib.suppress(ProcessLookupError, OSError):
            os.killpg(os.getpgid(self._process.pid), signal.SIGTERM)
        try:
            self._process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(self._process.pid), signal.SIGKILL)
                self._process.wait(timeout=1.0)
            except (ProcessLookupError, OSError, subprocess.TimeoutExpired):
                pass


__all__ = ["GenericProcessAdapter"]
