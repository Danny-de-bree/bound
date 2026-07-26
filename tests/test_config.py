"""Tests for the project configuration module (v1.0)."""

from __future__ import annotations

from pathlib import Path

import yaml

from bound.config import (
    AgentConfig,
    PlanConfig,
    PolicyConfig,
    ProjectConfig,
    WorkspaceConfig,
    _scrub_credentials,
    find_project_root,
    load_project_config,
)


class TestAgentConfig:
    """Tests for AgentConfig model."""

    def test_defaults(self) -> None:
        """AgentConfig defaults to 'auto' for all fields."""
        cfg = AgentConfig()
        assert cfg.name == "auto"
        assert cfg.executable == "auto"
        assert cfg.integration == "auto"
        assert cfg.command is None

    def test_with_generic_command(self) -> None:
        """AgentConfig accepts an explicit command list for generic agents."""
        cfg = AgentConfig(
            name="generic",
            command=["python", "-m", "my_agent", "--acp"],
        )
        assert cfg.name == "generic"
        assert cfg.command == ["python", "-m", "my_agent", "--acp"]

    def test_forbids_extra_fields(self) -> None:
        """AgentConfig rejects unknown keys."""
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AgentConfig(name="auto", unknown_key="value")  # type: ignore[call-arg]


class TestPlanConfig:
    """Tests for PlanConfig model."""

    def test_defaults(self) -> None:
        """PlanConfig defaults to plan.md with required=False."""
        cfg = PlanConfig()
        assert cfg.path == "plan.md"
        assert cfg.required is False

    def test_custom_path(self) -> None:
        """PlanConfig accepts a custom path."""
        cfg = PlanConfig(path="docs/task.md", required=True)
        assert cfg.path == "docs/task.md"
        assert cfg.required is True


class TestProjectConfig:
    """Tests for ProjectConfig model."""

    def test_all_defaults(self) -> None:
        """ProjectConfig produces sensible defaults for every sub-model."""
        cfg = ProjectConfig()
        assert cfg.project_root == "."
        assert isinstance(cfg.agent, AgentConfig)
        assert isinstance(cfg.plan, PlanConfig)
        assert isinstance(cfg.policy, PolicyConfig)
        assert isinstance(cfg.workspace, WorkspaceConfig)
        assert cfg.agent.name == "auto"


class TestLoadProjectConfig:
    """Tests for load_project_config()."""

    def test_defaults_when_no_config_file(self, tmp_path: Path) -> None:
        """Returns a default ProjectConfig when .bound/config.yaml does not exist."""
        cfg = load_project_config(tmp_path)
        assert cfg.project_root == str(tmp_path.resolve())
        assert cfg.agent.name == "auto"
        assert cfg.plan.path == "plan.md"

    def test_loads_valid_config(self, tmp_path: Path) -> None:
        """Loads and populates a ProjectConfig from a valid config.yaml."""
        bound_dir = tmp_path / ".bound"
        bound_dir.mkdir()
        config_file = bound_dir / "config.yaml"
        config_file.write_text(
            yaml.dump({
                "agent": {
                    "name": "claude-code",
                    "executable": "/usr/local/bin/claude",
                },
                "plan": {"path": "tasks/plan.md", "required": True},
                "workspace": {"mode": "worktree"},
            }),
            encoding="utf-8",
        )

        cfg = load_project_config(tmp_path)
        assert cfg.agent.name == "claude-code"
        assert cfg.agent.executable == "/usr/local/bin/claude"
        assert cfg.plan.path == "tasks/plan.md"
        assert cfg.plan.required is True
        assert cfg.workspace.mode == "worktree"

    def test_invalid_yaml_returns_defaults(self, tmp_path: Path) -> None:
        """Returns defaults when config.yaml is not valid YAML."""
        bound_dir = tmp_path / ".bound"
        bound_dir.mkdir()
        (bound_dir / "config.yaml").write_text("{not valid yaml: [", encoding="utf-8")

        cfg = load_project_config(tmp_path)
        assert cfg.agent.name == "auto"

    def test_non_dict_yaml_returns_defaults(self, tmp_path: Path) -> None:
        """Returns defaults when the YAML file is a list, not a mapping."""
        bound_dir = tmp_path / ".bound"
        bound_dir.mkdir()
        (bound_dir / "config.yaml").write_text("- item1\n- item2\n", encoding="utf-8")

        cfg = load_project_config(tmp_path)
        assert cfg.agent.name == "auto"

    def test_missing_config_not_a_file_returns_defaults(self, tmp_path: Path) -> None:
        """Returns defaults when the .bound directory exists but has no config.yaml."""
        bound_dir = tmp_path / ".bound"
        bound_dir.mkdir()

        cfg = load_project_config(tmp_path)
        assert cfg.agent.name == "auto"

    def test_roundtrip_write_read(self, tmp_path: Path) -> None:
        """Write a config dict, load it back, verify values survive."""
        bound_dir = tmp_path / ".bound"
        bound_dir.mkdir()

        original = {
            "agent": {
                "name": "cline",
                "integration": "mcp",
                "command": ["cline", "--mcp"],
            },
            "plan": {"path": "plan.md"},
            "policy": {"path": "bound-policy.yaml"},
            "workspace": {"mode": "inplace"},
        }
        (bound_dir / "config.yaml").write_text(
            yaml.dump(original), encoding="utf-8",
        )

        cfg = load_project_config(tmp_path)
        assert cfg.agent.name == "cline"
        assert cfg.agent.integration == "mcp"
        assert cfg.agent.command == ["cline", "--mcp"]
        assert cfg.workspace.mode == "inplace"

    def test_partial_config_merges_with_defaults(self, tmp_path: Path) -> None:
        """Keys not in the config file retain their defaults."""
        bound_dir = tmp_path / ".bound"
        bound_dir.mkdir()
        (bound_dir / "config.yaml").write_text(
            yaml.dump({"agent": {"name": "codex"}}),
            encoding="utf-8",
        )

        cfg = load_project_config(tmp_path)
        assert cfg.agent.name == "codex"
        # Unspecified fields keep defaults.
        assert cfg.plan.path == "plan.md"
        assert cfg.plan.required is False


class TestFindProjectRoot:
    """Tests for find_project_root()."""

    def test_finds_bound_dir(self, tmp_path: Path) -> None:
        """Returns the parent of a .bound/ directory."""
        bound_dir = tmp_path / ".bound"
        bound_dir.mkdir()
        sub = tmp_path / "src" / "lib"
        sub.mkdir(parents=True)

        root = find_project_root(sub)
        assert root == tmp_path.resolve()

    def test_falls_back_to_git(self, tmp_path: Path) -> None:
        """When no .bound/ exists, falls back to .git/."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        sub = tmp_path / "deep" / "nested"
        sub.mkdir(parents=True)

        root = find_project_root(sub)
        assert root == tmp_path.resolve()

    def test_prefers_bound_over_git(self, tmp_path: Path) -> None:
        """When both .bound/ and .git/ exist, .bound/ wins."""
        # .bound is at tmp_path
        (tmp_path / ".bound").mkdir()
        # .git is at a parent level — simulate by creating tmp_path/.git too
        (tmp_path / ".git").mkdir()
        sub = tmp_path / "sub"
        sub.mkdir()

        root = find_project_root(sub)
        assert root == tmp_path.resolve()
        assert (root / ".bound").is_dir()

    def test_no_marker_returns_cwd(self, tmp_path: Path) -> None:
        """Returns start_dir when no .bound/ or .git/ is found."""
        root = find_project_root(tmp_path)
        assert root == tmp_path.resolve()

    def test_from_subdirectory(self, tmp_path: Path) -> None:
        """Finds project root from a deeply nested subdirectory."""
        (tmp_path / ".bound").mkdir()
        deep = tmp_path / "a" / "b" / "c" / "d"
        deep.mkdir(parents=True)

        root = find_project_root(deep)
        assert root == tmp_path.resolve()


class TestScrubCredentials:
    """Tests for _scrub_credentials()."""

    def test_removes_api_key(self) -> None:
        """Removes a top-level api_key."""
        data = {"api_key": "sk-secret", "name": "test"}
        _scrub_credentials(data)
        assert "api_key" not in data
        assert data["name"] == "test"

    def test_removes_token(self) -> None:
        """Removes a top-level token."""
        data = {"token": "ghp_abc123"}
        _scrub_credentials(data)
        assert "token" not in data

    def test_removes_password(self) -> None:
        """Removes a password key."""
        data = {"password": "s3cr3t"}
        _scrub_credentials(data)
        assert "password" not in data

    def test_removes_nested_credential(self) -> None:
        """Removes credential-like keys inside nested dicts."""
        data = {
            "agent": {
                "name": "cline",
                "api_secret": "shh",
            },
        }
        _scrub_credentials(data)
        assert "api_secret" not in data["agent"]
        assert data["agent"]["name"] == "cline"

    def test_preserves_non_credential_keys(self) -> None:
        """Keys that do not look like credentials are kept."""
        data = {
            "agent": {"name": "cline"},
            "plan": {"path": "plan.md"},
            "timeout": 30,
        }
        _scrub_credentials(data)
        assert data["agent"]["name"] == "cline"
        assert data["plan"]["path"] == "plan.md"
        assert data["timeout"] == 30

    def test_case_insensitive_match(self) -> None:
        """Credential detection is case-insensitive."""
        data = {"API_KEY": "sk-upper"}
        _scrub_credentials(data)
        assert "API_KEY" not in data


class TestNoCredentialsInConfig:
    """End-to-end test: credentials never survive load_project_config."""

    def test_credentials_scrubbed_on_load(self, tmp_path: Path) -> None:
        """A config.yaml containing api_key loads with it removed."""
        bound_dir = tmp_path / ".bound"
        bound_dir.mkdir()
        (bound_dir / "config.yaml").write_text(
            yaml.dump({
                "agent": {"name": "cline", "api_key": "sk-should-not-survive"},
            }),
            encoding="utf-8",
        )

        cfg = load_project_config(tmp_path)
        assert cfg.agent.name == "cline"
        # The credential should not appear anywhere in the config.
        assert "api_key" not in cfg.model_dump().get("agent", {})  # type: ignore[operator]

    def test_nested_credential_scrubbed(self, tmp_path: Path) -> None:
        """Credentials nested inside agent are removed."""
        bound_dir = tmp_path / ".bound"
        bound_dir.mkdir()
        (bound_dir / "config.yaml").write_text(
            yaml.dump({
                "agent": {
                    "name": "cline",
                    "auth_token": "secret-token-123",
                },
            }),
            encoding="utf-8",
        )

        cfg = load_project_config(tmp_path)
        # auth_token is not in the AgentConfig model at all, so it just disappears.
        assert cfg.agent.name == "cline"
