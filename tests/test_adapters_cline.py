"""Tests for ClineMCPAdapter — MCP config generation, install, uninstall."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from bound.adapters.cline import (
    CLINE_MCP_CONFIG_REL_PATH,
    DEFAULT_CLINE_MCP_COMMAND,
    EXPOSED_TOOLS,
    ClineMCPAdapter,
)

# ---------------------------------------------------------------------------
# Configuration generation
# ---------------------------------------------------------------------------


def test_generate_default_config() -> None:
    """Default generation uses 'bound mcp' command and resolves cwd."""
    config = ClineMCPAdapter.generate()
    assert config["command"] == "bound"
    assert config["args"] == ["mcp"]
    assert "cwd" in config
    assert Path(config["cwd"]).is_absolute()


def test_generate_with_custom_command() -> None:
    """Custom MCP command overrides the default."""
    config = ClineMCPAdapter.generate(mcp_command="python -m bound.mcp_server")
    assert config["command"] == "python"
    assert config["args"] == ["-m", "bound.mcp_server"]


def test_generate_with_project_dir() -> None:
    """When project_dir is given, cwd reflects it."""
    config = ClineMCPAdapter.generate(project_dir="/home/test/project")
    assert config["cwd"] == "/home/test/project"


def test_generate_with_env() -> None:
    """When env is given, it appears in the config."""
    config = ClineMCPAdapter.generate(env={"DEBUG": "1"})
    assert "env" in config
    assert config["env"] == {"DEBUG": "1"}


def test_generate_without_env() -> None:
    """Without env, the 'env' key is not in the config."""
    config = ClineMCPAdapter.generate()
    assert "env" not in config


def test_generate_output_is_valid_json_serializable() -> None:
    """The generated config round-trips through JSON."""
    config = ClineMCPAdapter.generate(project_dir="/tmp")
    serialized = json.dumps(config)
    parsed = json.loads(serialized)
    assert parsed == config


# ---------------------------------------------------------------------------
# install / uninstall
# ---------------------------------------------------------------------------


def test_install_writes_config_file() -> None:
    """install() writes a valid JSON config to .cline/mcp/bound.json."""
    with tempfile.TemporaryDirectory() as tmp:
        path = ClineMCPAdapter.install(project_dir=tmp)
        assert path.exists()
        assert path.name == "bound.json"
        assert ".cline" in str(path)
        assert "mcp" in str(path)

        data = json.loads(path.read_text())
        assert data["command"] == "bound"
        assert data["args"] == ["mcp"]
        assert "cwd" in data


def test_install_with_custom_command() -> None:
    """install() with custom command writes the right config."""
    with tempfile.TemporaryDirectory() as tmp:
        path = ClineMCPAdapter.install(
            project_dir=tmp,
            mcp_command="bound mcp --once",
        )
        data = json.loads(path.read_text())
        assert data["command"] == "bound"
        assert "--once" in data["args"]


def test_install_with_env() -> None:
    """install() with env writes env key into the config."""
    with tempfile.TemporaryDirectory() as tmp:
        path = ClineMCPAdapter.install(
            project_dir=tmp,
            env={"BOUND_DEBUG": "1"},
        )
        data = json.loads(path.read_text())
        assert "env" in data
        assert data["env"] == {"BOUND_DEBUG": "1"}


def test_install_creates_nested_directory() -> None:
    """install() creates .cline/mcp/ if it does not exist."""
    with tempfile.TemporaryDirectory() as tmp:
        cline_mcp_dir = Path(tmp) / ".cline" / "mcp"
        assert not cline_mcp_dir.exists()
        ClineMCPAdapter.install(project_dir=tmp)
        assert cline_mcp_dir.is_dir()


def test_uninstall_removes_existing_file() -> None:
    """uninstall() removes an existing config file."""
    with tempfile.TemporaryDirectory() as tmp:
        ClineMCPAdapter.install(project_dir=tmp)
        result = ClineMCPAdapter.uninstall(project_dir=tmp)
        assert result is True
        assert not (Path(tmp) / CLINE_MCP_CONFIG_REL_PATH).exists()


def test_uninstall_returns_false_when_missing() -> None:
    """uninstall() returns False when no config file exists."""
    with tempfile.TemporaryDirectory() as tmp:
        result = ClineMCPAdapter.uninstall(project_dir=tmp)
        assert result is False


def test_is_installed_positive() -> None:
    """is_installed() returns True after install."""
    with tempfile.TemporaryDirectory() as tmp:
        ClineMCPAdapter.install(project_dir=tmp)
        assert ClineMCPAdapter.is_installed(project_dir=tmp)


def test_is_installed_negative() -> None:
    """is_installed() returns False when not installed."""
    with tempfile.TemporaryDirectory() as tmp:
        assert not ClineMCPAdapter.is_installed(project_dir=tmp)


def test_is_installed_after_uninstall() -> None:
    """is_installed() returns False after uninstall."""
    with tempfile.TemporaryDirectory() as tmp:
        ClineMCPAdapter.install(project_dir=tmp)
        ClineMCPAdapter.uninstall(project_dir=tmp)
        assert not ClineMCPAdapter.is_installed(project_dir=tmp)


def test_install_is_idempotent() -> None:
    """Calling install() twice succeeds (overwrites)."""
    with tempfile.TemporaryDirectory() as tmp:
        path1 = ClineMCPAdapter.install(project_dir=tmp)
        path2 = ClineMCPAdapter.install(
            project_dir=tmp,
            mcp_command="bound mcp --json",
        )
        assert path1 == path2
        data = json.loads(path2.read_text())
        assert "--json" in data["args"]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_default_cline_mcp_command() -> None:
    """DEFAULT_CLINE_MCP_COMMAND is 'bound mcp'."""
    assert DEFAULT_CLINE_MCP_COMMAND == "bound mcp"


def test_exposed_tools_structure() -> None:
    """EXPOSED_TOOLS has name and description for each tool."""
    assert isinstance(EXPOSED_TOOLS, list)
    assert len(EXPOSED_TOOLS) > 0
    for tool in EXPOSED_TOOLS:
        assert "name" in tool, f"Tool {tool} missing name"
        assert "description" in tool, f"Tool {tool} missing description"
        assert tool["name"].startswith("bound_"), (
            f"Tool name {tool['name']!r} should start with 'bound_'"
        )


def test_cline_mcp_config_rel_path() -> None:
    """CLINE_MCP_CONFIG_REL_PATH points to .cline/mcp/bound.json."""
    assert CLINE_MCP_CONFIG_REL_PATH == ".cline/mcp/bound.json"


# ---------------------------------------------------------------------------
# ClineMCPAdapter is not a process adapter
# ---------------------------------------------------------------------------


def test_cline_adapter_is_not_generic_process_adapter() -> None:
    """ClineMCPAdapter does not extend GenericProcessAdapter."""
    from bound.adapters.generic import GenericProcessAdapter

    assert not issubclass(ClineMCPAdapter, GenericProcessAdapter)
