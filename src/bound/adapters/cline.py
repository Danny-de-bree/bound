"""Cline MCP adapter for BOUND (v0.9.5).

Generates a Cline-compatible MCP server configuration that connects Cline
(VS Code extension) to the BOUND MCP server via stdio. Unlike the process
adapters, this adapter does NOT spawn or control an agent directly — it
uses the MCP protocol, which Cline natively supports.

Usage::

    from bound.adapters.cline import ClineMCPAdapter

    ClineMCPAdapter.install(project_dir="/path/to/project")
    # Cline now has access to bound_evaluate, bound_checkpoint_create, etc.
    ClineMCPAdapter.uninstall(project_dir="/path/to/project")
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Path (relative to project root) where Cline MCP config lives.
CLINE_MCP_CONFIG_REL_PATH: str = ".cline/mcp/bound.json"

#: Default MCP server command invoked by Cline.
DEFAULT_CLINE_MCP_COMMAND: str = "bound mcp"

#: BOUND MCP tools exposed to Cline (subset of the full MCP tool set).
EXPOSED_TOOLS: list[dict[str, str]] = [
    {
        "name": "bound_evaluate",
        "description": "Evaluate an action against BOUND policy with pre-supplied scores.",
    },
    {
        "name": "bound_evaluate_workflow",
        "description": "Evaluate using coding-workflow signals (tests, lint, coverage).",
    },
    {
        "name": "bound_checkpoint_create",
        "description": "Create a BOUND-owned checkpoint for a run step.",
    },
    {
        "name": "bound_checkpoint_list",
        "description": "List all checkpoints for a run.",
    },
    {
        "name": "bound_boundary_evaluate",
        "description": "Evaluate an executed step against its contract and policy config.",
    },
    {
        "name": "bound_run_start",
        "description": "Start a new lineage run.",
    },
    {
        "name": "bound_run_finish",
        "description": "Finish (close) a lineage run.",
    },
    {
        "name": "bound_run_inspect",
        "description": "Inspect a lineage run's full log.",
    },
    {
        "name": "bound_evidence_collect",
        "description": "Record a collected-evidence event in lineage.",
    },
]


class ClineMCPAdapter:
    """Generates and manages Cline MCP server configuration.

    Cline (VS Code extension) connects to MCP servers declared in
    ``.cline/mcp/*.json`` files. This adapter creates the ``bound.json``
    config that points Cline to the BOUND MCP server.

    Does NOT extend :class:`GenericProcessAdapter` — Cline is controlled
    through the MCP protocol, not through process I/O.
    """

    @staticmethod
    def generate(
        project_dir: str | Path = ".",
        mcp_command: str | None = None,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Generate the Cline MCP configuration dict.

        Args:
            project_dir: Project root directory.
            mcp_command: Override the MCP server command.
                Defaults to ``"bound mcp"``.
            env: Optional environment variables to pass to the MCP server.

        Returns:
            A dict suitable for writing to ``.cline/mcp/bound.json``.
        """
        root = Path(project_dir).resolve()

        cmd_parts = (mcp_command or DEFAULT_CLINE_MCP_COMMAND).split()
        command = cmd_parts[0]
        args = cmd_parts[1:] if len(cmd_parts) > 1 else []

        config: dict[str, Any] = {
            "command": command,
            "args": args,
            "cwd": str(root),
        }

        if env:
            config["env"] = dict(env)

        return config

    @staticmethod
    def install(
        project_dir: str | Path = ".",
        mcp_command: str | None = None,
        env: dict[str, str] | None = None,
    ) -> Path:
        """Write the MCP config file to ``.cline/mcp/bound.json``.

        Creates the ``.cline/mcp/`` directory if needed.

        Args:
            project_dir: Project root directory.
            mcp_command: Override the MCP server command.
            env: Optional environment variables for the MCP server process.

        Returns:
            The path to the written config file.

        Raises:
            OSError: If the file cannot be written.
        """
        root = Path(project_dir).resolve()
        mcp_dir = root / ".cline" / "mcp"
        mcp_dir.mkdir(parents=True, exist_ok=True)

        config = ClineMCPAdapter.generate(
            project_dir=str(root),
            mcp_command=mcp_command,
            env=env,
        )
        config_path = mcp_dir / "bound.json"
        config_path.write_text(
            json.dumps(config, indent=2) + "\n",
            encoding="utf-8",
        )
        logger.info("Cline MCP config installed at %s", config_path)
        return config_path

    @staticmethod
    def uninstall(project_dir: str | Path = ".") -> bool:
        """Remove the ``.cline/mcp/bound.json`` file if it exists.

        Does not remove the parent directories even if empty.

        Args:
            project_dir: Project root directory.

        Returns:
            ``True`` if the file was removed, ``False`` if it did not exist.
        """
        config_path = Path(project_dir).resolve() / CLINE_MCP_CONFIG_REL_PATH
        if config_path.exists():
            config_path.unlink()
            logger.info("Cline MCP config removed from %s", config_path)
            return True
        return False

    @staticmethod
    def is_installed(project_dir: str | Path = ".") -> bool:
        """Check whether the Cline MCP config is installed.

        Args:
            project_dir: Project root directory.

        Returns:
            ``True`` if ``.cline/mcp/bound.json`` exists.
        """
        return (Path(project_dir).resolve() / CLINE_MCP_CONFIG_REL_PATH).exists()


__all__ = [
    "ClineMCPAdapter",
    "CLINE_MCP_CONFIG_REL_PATH",
    "DEFAULT_CLINE_MCP_COMMAND",
    "EXPOSED_TOOLS",
]
