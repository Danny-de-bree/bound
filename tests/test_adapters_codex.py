"""Tests for CodexAdapter and CodexMCPConfig."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from bound.adapters.codex import (
    CODEX_MCP_CONFIG_REL_PATH,
    DEFAULT_CODEX_COMMAND,
    CodexAdapter,
    CodexMCPConfig,
)

# ---------------------------------------------------------------------------
# CodexAdapter - configuration
# ---------------------------------------------------------------------------


def test_default_command() -> None:
    """Default command uses npx @openai/codex exec."""
    adapter = CodexAdapter()
    cmd = adapter.config.agent_command
    assert cmd == DEFAULT_CODEX_COMMAND
    assert "npx" in cmd
    assert "@openai/codex" in cmd
    assert "exec" in cmd


def test_agent_type_set() -> None:
    """Agent type is 'codex'."""
    adapter = CodexAdapter()
    assert adapter.config.agent_type == "codex"


def test_working_dir_forwarded() -> None:
    """Working directory is forwarded to the config."""
    adapter = CodexAdapter(working_dir="/tmp/test")
    assert adapter.config.working_dir == "/tmp/test"


def test_timeout_forwarded() -> None:
    """Timeout is forwarded to the config."""
    adapter = CodexAdapter(timeout_seconds=600.0)
    assert adapter.config.timeout_seconds == 600.0


# ---------------------------------------------------------------------------
# CodexMCPConfig - generation
# ---------------------------------------------------------------------------


def test_generate_default_config() -> None:
    """Default generation uses 'bound mcp' command."""
    config = CodexMCPConfig.generate()
    assert "mcpServers" in config
    assert "bound" in config["mcpServers"]
    server = config["mcpServers"]["bound"]
    assert server["command"] == "bound"
    assert server["args"] == ["mcp"]


def test_generate_with_project_dir() -> None:
    """When project_dir is given, cwd is included."""
    config = CodexMCPConfig.generate(project_dir="/home/test/project")
    server = config["mcpServers"]["bound"]
    assert server["cwd"] == "/home/test/project"


def test_generate_with_custom_command() -> None:
    """Custom MCP command overrides the default."""
    config = CodexMCPConfig.generate(mcp_command="python -m bound.mcp_server")
    server = config["mcpServers"]["bound"]
    assert server["command"] == "python"
    assert server["args"] == ["-m", "bound.mcp_server"]


# ---------------------------------------------------------------------------
# CodexMCPConfig - install / uninstall
# ---------------------------------------------------------------------------


def test_install_writes_config_file() -> None:
    """install() writes a valid JSON config to .codex/mcp.json."""
    with tempfile.TemporaryDirectory() as tmp:
        path = CodexMCPConfig.install(project_dir=tmp)
        assert path.exists()
        assert path.name == "mcp.json"
        assert ".codex" in str(path)

        data = json.loads(path.read_text())
        assert "mcpServers" in data
        assert "bound" in data["mcpServers"]


def test_install_with_custom_command() -> None:
    """install() with custom command writes the right config."""
    with tempfile.TemporaryDirectory() as tmp:
        path = CodexMCPConfig.install(
            project_dir=tmp,
            mcp_command="bound mcp --once",
        )
        data = json.loads(path.read_text())
        server = data["mcpServers"]["bound"]
        assert server["command"] == "bound"
        assert "--once" in server["args"]


def test_uninstall_removes_existing_file() -> None:
    """uninstall() removes an existing config file."""
    with tempfile.TemporaryDirectory() as tmp:
        CodexMCPConfig.install(project_dir=tmp)
        result = CodexMCPConfig.uninstall(project_dir=tmp)
        assert result is True
        assert not (Path(tmp) / CODEX_MCP_CONFIG_REL_PATH).exists()


def test_uninstall_returns_false_when_missing() -> None:
    """uninstall() returns False when no config file exists."""
    with tempfile.TemporaryDirectory() as tmp:
        result = CodexMCPConfig.uninstall(project_dir=tmp)
        assert result is False


def test_install_creates_directory() -> None:
    """install() creates .codex/ if it does not exist."""
    with tempfile.TemporaryDirectory() as tmp:
        codex_dir = Path(tmp) / ".codex"
        assert not codex_dir.exists()
        CodexMCPConfig.install(project_dir=tmp)
        assert codex_dir.is_dir()


# ---------------------------------------------------------------------------
# DEFAULT_CODEX_COMMAND
# ---------------------------------------------------------------------------


def test_default_codex_command_structure() -> None:
    """DEFAULT_CODEX_COMMAND has the expected shape."""
    assert isinstance(DEFAULT_CODEX_COMMAND, list)
    assert DEFAULT_CODEX_COMMAND == ["npx", "@openai/codex", "exec"]
