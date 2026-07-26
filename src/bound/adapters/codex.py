"""Codex agent adapter for BOUND (v0.9.5).

Spawns ``npx @openai/codex exec`` as a child process and parses its output
into ACP events. Also provides ``CodexMCPConfig`` for generating MCP server
configuration compatible with Codex's native MCP support.

Usage::

    from bound.adapters.codex import CodexAdapter, CodexMCPConfig

    adapter = CodexAdapter(
        working_dir="/path/to/workspace",
    )
    adapter.launch("Implement validation", candidate_id="cand-001")

    # MCP config for Codex's native MCP mode:
    config = CodexMCPConfig.generate()
    print(config)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from bound.adapters import AdapterEvent
from bound.adapters.generic import GenericProcessAdapter

logger = logging.getLogger(__name__)

#: Default Codex exec CLI command fragments.
DEFAULT_CODEX_COMMAND: list[str] = [
    "npx",
    "@openai/codex",
    "exec",
]

#: Path (relative to project root) where Codex MCP config lives.
CODEX_MCP_CONFIG_REL_PATH: str = ".codex/mcp.json"


class CodexAdapter(GenericProcessAdapter):
    """Adapter for OpenAI's Codex CLI agent (exec mode).

    Spawns ``codex exec`` as a child process and converts the output
    into standard ACP events. Extends :class:`GenericProcessAdapter`
    with Codex-specific output parsing.
    """

    def __init__(
        self,
        working_dir: str | Path | None = None,
        timeout_seconds: float = 300.0,
        **extra_config: Any,
    ) -> None:
        """Initialise the Codex adapter.

        Args:
            working_dir: Working directory for the agent process.
            timeout_seconds: Default wait timeout in seconds.
            **extra_config: Additional key-value pairs folded into
                :class:`AdapterConfig`.
        """
        super().__init__(
            agent_command=list(DEFAULT_CODEX_COMMAND),
            working_dir=str(working_dir) if working_dir else None,
            timeout_seconds=timeout_seconds,
            agent_type="codex",
            **extra_config,
        )

    def launch(
        self,
        task: str,
        plan: dict[str, Any] | None = None,
        candidate_id: str | None = None,
    ) -> None:
        """Spawn Codex exec and send the task.

        The task string is appended to the command line after the ``exec``
        subcommand.

        Args:
            task: Human-readable task description for the agent.
            plan: Optional structured plan dict.
            candidate_id: The candidate identifier for event tagging.

        Raises:
            RuntimeError: If the agent process cannot be spawned.
        """
        import subprocess
        import threading

        cmd = list(self.config.agent_command) + [task]
        working_dir = self.config.working_dir or "."

        if not Path(working_dir).is_dir():
            raise RuntimeError(f"Working directory does not exist: {working_dir}")

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
                f"Codex CLI not found: {cmd[0]}. Is @openai/codex installed?"
            ) from exc

        self._running = True
        self._candidate_id = candidate_id
        self._events.clear()

        self._reader_thread = threading.Thread(
            target=self._read_codex_stdout_loop,
            daemon=True,
        )
        self._reader_thread.start()

        stderr_thread = threading.Thread(
            target=self._read_stderr_loop,
            daemon=True,
        )
        stderr_thread.start()

    def _read_codex_stdout_loop(self) -> None:
        """Background thread: read Codex exec output from stdout.

        Codex exec outputs plain text (not JSONL). Each meaningful block
        of output is wrapped as an ``evidence.collected`` event.
        """
        assert self._process is not None
        assert self._process.stdout is not None

        try:
            for line in self._process.stdout:
                line = line.strip()
                if not line:
                    continue

                event = AdapterEvent(
                    type="evidence.collected",
                    evidence={"output": line},
                    candidate_id=self._candidate_id,
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
            logger.debug("Codex stdout reader loop exiting", exc_info=True)
        finally:
            self._running = False
            with self._condition:
                self._condition.notify_all()


class CodexMCPConfig:
    """Generator for Codex MCP server configuration.

    Codex supports a native MCP client mode. The generated config tells
    Codex to connect to the BOUND MCP server so it can use BOUND tools
    (``bound_evaluate``, ``bound_checkpoint_create``, etc.) directly.

    Usage::

        config = CodexMCPConfig.generate(project_dir="/path/to/project")
        CodexMCPConfig.install(project_dir="/path/to/project")
        CodexMCPConfig.uninstall(project_dir="/path/to/project")
    """

    #: Default command used to start the BOUND MCP server.
    DEFAULT_MCP_COMMAND: str = "bound mcp"

    @staticmethod
    def generate(
        project_dir: str | Path | None = None,
        mcp_command: str | None = None,
    ) -> dict[str, Any]:
        """Generate the Codex MCP configuration dict.

        Args:
            project_dir: Project root directory (used as working directory).
            mcp_command: Override the MCP server command.
                Defaults to ``"bound mcp"``.

        Returns:
            A dict suitable for writing to ``.codex/mcp.json``.
        """
        cmd = mcp_command or CodexMCPConfig.DEFAULT_MCP_COMMAND
        config: dict[str, Any] = {
            "mcpServers": {
                "bound": {
                    "command": cmd.split()[0] if " " in cmd else cmd,
                    "args": cmd.split()[1:] if " " in cmd else [],
                }
            }
        }
        if project_dir:
            config["mcpServers"]["bound"]["cwd"] = str(project_dir)
        return config

    @staticmethod
    def install(
        project_dir: str | Path = ".",
        mcp_command: str | None = None,
    ) -> Path:
        """Write the MCP config file to ``.codex/mcp.json``.

        Args:
            project_dir: Project root directory.
            mcp_command: Override the MCP server command.

        Returns:
            The path to the written config file.

        Raises:
            OSError: If the file cannot be written.
        """
        root = Path(project_dir).resolve()
        codex_dir = root / ".codex"
        codex_dir.mkdir(parents=True, exist_ok=True)

        config = CodexMCPConfig.generate(
            project_dir=str(root),
            mcp_command=mcp_command,
        )
        config_path = codex_dir / "mcp.json"
        config_path.write_text(
            json.dumps(config, indent=2) + "\n",
            encoding="utf-8",
        )
        logger.info("Codex MCP config installed at %s", config_path)
        return config_path

    @staticmethod
    def uninstall(project_dir: str | Path = ".") -> bool:
        """Remove the ``.codex/mcp.json`` file if it exists.

        Args:
            project_dir: Project root directory.

        Returns:
            ``True`` if the file was removed, ``False`` if it did not exist.
        """
        config_path = Path(project_dir).resolve() / CODEX_MCP_CONFIG_REL_PATH
        if config_path.exists():
            config_path.unlink()
            logger.info("Codex MCP config removed from %s", config_path)
            return True
        return False


__all__ = [
    "CodexAdapter",
    "CodexMCPConfig",
    "CODEX_MCP_CONFIG_REL_PATH",
    "DEFAULT_CODEX_COMMAND",
]
