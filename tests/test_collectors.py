from __future__ import annotations

import sys
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from bound.collectors import (
    GitInspection,
    PytestSummary,
    ServiceTestEvidence,
    parse_git_status_porcelain,
    parse_pytest_summary,
)
from bound.command_collector import (
    BudgetCollector,
    BudgetMetrics,
    CommandCollector,
    CommandResult,
    CommandSpec,
    GitCollector,
    JUnitCollector,
    ProcessRuntimeCollector,
    PytestCollector,
    default_redactor,
    sha256_hex,
)
from bound.evidence import EvidenceMetric, EvidenceProvenance, EvidenceStatus

#: The contract's allowed-path prefixes, mirroring the Todo reference
#: integration. A changed path whose prefix is not in this set is unexpected.
_ALLOWED_PREFIXES: tuple[str, ...] = (
    "src/todo_app",
    "tests",
    "bound_integration",
    "pyproject.toml",
    "uv.lock",
)


# ---------------------------------------------------------------------------
# Phase 4 — pytest summary parsing (warnings are not tests)
# ---------------------------------------------------------------------------


class TestParsePytestSummary:
    """Pin down the Phase 4 fix: warnings/deselected/rerun are not tests."""

    def test_warnings_not_counted_as_tests(self) -> None:
        """``30 passed, 2 warnings`` reports 30 executed tests, not 32."""
        summary = parse_pytest_summary("30 passed, 2 warnings in 0.09s")
        assert summary.executed_test_count == 30
        assert summary.passed == 30
        # There is no warnings field: warnings are not a test outcome.
        assert not hasattr(summary, "warnings")

    def test_mixed_outcomes(self) -> None:
        """A line with several test outcomes sums every test-outcome token."""
        summary = parse_pytest_summary("5 passed, 1 failed, 2 skipped in 0.10s")
        assert summary.passed == 5
        assert summary.failed == 1
        assert summary.skipped == 2
        assert summary.executed_test_count == 8

    def test_errors_singular_and_plural(self) -> None:
        """``3 errors`` and ``1 error`` both parse into the ``errors`` field."""
        plural = parse_pytest_summary("3 errors in 0.01s")
        assert plural.errors == 3
        assert plural.executed_test_count == 3

        singular = parse_pytest_summary("1 error in 0.01s")
        assert singular.errors == 1
        assert singular.executed_test_count == 1

    def test_xfailed(self) -> None:
        """``N xfailed`` is counted as executed tests."""
        summary = parse_pytest_summary("2 xfailed in 0.02s")
        assert summary.xfailed == 2
        assert summary.executed_test_count == 2

    def test_xpassed(self) -> None:
        """``N xpassed`` is counted as executed tests."""
        summary = parse_pytest_summary("4 xpassed in 0.02s")
        assert summary.xpassed == 4
        assert summary.executed_test_count == 4

    def test_mixed_all_outcomes(self) -> None:
        """Every supported outcome on one line is summed into its own field."""
        summary = parse_pytest_summary(
            "1 passed, 1 failed, 2 errors, 3 skipped, 1 xfailed, 1 xpassed"
        )
        assert summary.passed == 1
        assert summary.failed == 1
        assert summary.errors == 2
        assert summary.skipped == 3
        assert summary.xfailed == 1
        assert summary.xpassed == 1
        assert summary.executed_test_count == 9

    def test_no_summary_line_is_zero(self) -> None:
        """No summary line (empty, or only progress output) yields zero tests."""
        assert parse_pytest_summary("").executed_test_count == 0
        assert parse_pytest_summary(
            "....s....s....                              [100%]\n"
        ).executed_test_count == 0
        assert parse_pytest_summary(
            "collection error: no tests collected"
        ).executed_test_count == 0

    def test_warnings_summary_block_ignored(self) -> None:
        """A trailing warnings-summary detail block does not affect the count."""
        text = (
            "....                              [100%]\n"
            "30 passed, 2 warnings in 0.09s\n"
            "\n"
            "warnings summary\n"
            "  tests/test_x.py: 4 warnings\n"
        )
        summary = parse_pytest_summary(text)
        assert summary.executed_test_count == 30
        assert summary.passed == 30

    @pytest.mark.parametrize(
        "line",
        [
            "30 passed, 2 warnings",
            "1 passed, 5 warnings",
            "0 passed, 10 warnings",
        ],
    )
    def test_warnings_never_counted_parametrized(self, line: str) -> None:
        """For every form, warnings never contribute to the executed count."""
        summary = parse_pytest_summary(line)
        assert summary.executed_test_count == summary.passed

    def test_extra_fields_forbidden(self) -> None:
        """The model is auditable: stray fields are rejected (extra=forbid)."""
        with pytest.raises(ValidationError):
            PytestSummary(passed=1, warnings=2)


# ---------------------------------------------------------------------------
# Phase 3 — git inspection (failed command must not become passing evidence)
# ---------------------------------------------------------------------------


class TestGitInspection:
    """Pin down the Phase 3 fix: a failed git command cannot pass."""

    def test_clean_status(self) -> None:
        """An empty porcelain output is a proven clean tree (command OK)."""
        inspection = parse_git_status_porcelain("", _ALLOWED_PREFIXES)
        assert inspection.command_succeeded is True
        assert inspection.changed_paths == []
        assert inspection.unexpected_paths == []
        assert inspection.is_clean_proven() is True

    def test_expected_changed_files(self) -> None:
        """Changed files whose prefixes are allowed yield no unexpected paths."""
        output = (
            " M src/todo_app/service.py\n"
            "?? tests/test_service.py\n"
            " M bound_integration/INTEGRATION_REPORT.md\n"
        )
        inspection = parse_git_status_porcelain(output, _ALLOWED_PREFIXES)
        assert inspection.command_succeeded is True
        assert inspection.changed_paths == [
            "src/todo_app/service.py",
            "tests/test_service.py",
            "bound_integration/INTEGRATION_REPORT.md",
        ]
        assert inspection.unexpected_paths == []
        assert inspection.is_clean_proven() is True

    def test_unexpected_changed_files(self) -> None:
        """A path outside the allowed set lands in unexpected_paths."""
        output = (
            " M src/todo_app/service.py\n"
            "?? secrets.env\n"
            " M README.md\n"
        )
        inspection = parse_git_status_porcelain(output, _ALLOWED_PREFIXES)
        assert inspection.command_succeeded is True
        assert "src/todo_app/service.py" in inspection.changed_paths
        assert "secrets.env" in inspection.changed_paths
        assert "README.md" in inspection.changed_paths
        assert "src/todo_app/service.py" not in inspection.unexpected_paths
        assert "secrets.env" in inspection.unexpected_paths
        assert "README.md" in inspection.unexpected_paths
        assert inspection.is_clean_proven() is False

    def test_failed_git_status_command(self) -> None:
        """A failed git command cannot be treated as a proven-clean tree.

        This is the load-bearing Phase 3 assertion: ``command_failed()`` yields
        empty path lists (because git could not report) AND
        :meth:`is_clean_proven` is ``False`` — proving a failed command can
        never become a passing ``no-unexpected-files`` check.
        """
        inspection = GitInspection.command_failed()
        assert inspection.command_succeeded is False
        assert inspection.changed_paths == []
        assert inspection.unexpected_paths == []
        # The whole point: empty unexpected_paths does NOT prove clean when
        # the command failed.
        assert inspection.is_clean_proven() is False

    def test_renamed_path_destination_parsed(self) -> None:
        """A rename line ``R  old -> new`` is parsed to the destination."""
        output = "R  src/todo_app/old.py -> src/todo_app/new.py\n"
        inspection = parse_git_status_porcelain(output, _ALLOWED_PREFIXES)
        assert inspection.changed_paths == ["src/todo_app/new.py"]
        assert inspection.unexpected_paths == []

    def test_extra_fields_forbidden(self) -> None:
        """The model is auditable: stray fields are rejected (extra=forbid)."""
        with pytest.raises(ValidationError):
            GitInspection(command_succeeded=True, returncode=0)
# ---------------------------------------------------------------------------
# Phase 5 — service-specific test evidence
# ---------------------------------------------------------------------------


class TestServiceTestEvidence:
    """Pin down the Phase 5 fix: service-tests-pass is service-specific."""

    def test_unrelated_passing_tests_do_not_satisfy(self) -> None:
        """A green command whose service module ran 0 tests does not pass.

        ``service-tests-pass`` requires the *service-specific* run to have
        executed >=1 test; a green full suite whose service module executed 0
        tests (e.g. the module was empty or all skipped) does not satisfy it,
        even though the command itself exited 0.
        """
        evidence = ServiceTestEvidence(command_succeeded=True, executed_test_count=0)
        assert evidence.command_succeeded is True
        assert evidence.executed_test_count == 0
        assert evidence.passed is False

    def test_empty_service_test_module_does_not_satisfy(self) -> None:
        """An empty service test module (0 executed, command OK) does not pass."""
        evidence = ServiceTestEvidence(command_succeeded=True, executed_test_count=0)
        assert evidence.passed is False

    def test_passing_service_tests_satisfy(self) -> None:
        """>=1 executed AND command succeeded => service-tests-pass satisfied."""
        evidence = ServiceTestEvidence(command_succeeded=True, executed_test_count=20)
        assert evidence.passed is True

    def test_failing_service_tests_do_not_satisfy(self) -> None:
        """A failed command does not satisfy service-tests-pass, even w/ tests."""
        evidence = ServiceTestEvidence(command_succeeded=False, executed_test_count=5)
        assert evidence.command_succeeded is False
        assert evidence.executed_test_count == 5
        assert evidence.passed is False

    def test_failed_command_with_zero_executed_does_not_satisfy(self) -> None:
        """The worst case: failed command and nothing ran => not satisfied."""
        evidence = ServiceTestEvidence(command_succeeded=False, executed_test_count=0)
        assert evidence.passed is False

    def test_from_pytest_summary_round_trip(self) -> None:
        """The executed count is normally derived from parse_pytest_summary."""
        summary = parse_pytest_summary("20 passed in 0.03s")
        evidence = ServiceTestEvidence(
            command_succeeded=True,
            executed_test_count=summary.executed_test_count,
        )
        assert evidence.executed_test_count == 20
        assert evidence.passed is True

    def test_extra_fields_forbidden(self) -> None:
        """The model is auditable: stray fields are rejected (extra=forbid)."""
        with pytest.raises(ValidationError):
            ServiceTestEvidence(
                command_succeeded=True, executed_test_count=1, failed=0
            )




# ---------------------------------------------------------------------------
# v0.7 — CommandCollector + official collectors + fail-safe + privacy
# ---------------------------------------------------------------------------


def _sys_argv(*args: str) -> list[str]:
    """A portable argv using the current interpreter (works on any platform)."""
    return [sys.executable, *args]


class TestSha256AndRedaction:
    """Unit-test the privacy primitives the collectors are built on."""

    def test_sha256_hex_prefix(self) -> None:
        """``sha256_hex`` returns a ``sha256:``-prefixed 64-char digest."""
        digest = sha256_hex(b"hello")
        assert digest.startswith("sha256:")
        assert len(digest) == len("sha256:") + 64

    def test_default_redactor_masks_secret(self) -> None:
        """A ``password=hunter2`` token is masked, leaving the key visible."""
        redacted = default_redactor("connect password=hunter2 now")
        assert "hunter2" not in redacted
        assert "***REDACTED***" in redacted
        assert "password" in redacted

    def test_default_redactor_masks_token_and_api_key(self) -> None:
        """Multiple credential-like keys are all redacted, harmless text kept."""
        redacted = default_redactor("api_key=abc123 secret:xyz token=def456 plain")
        assert "abc123" not in redacted
        assert "xyz" not in redacted
        assert "def456" not in redacted
        assert "plain" in redacted

    def test_default_redactor_passthrough_no_secret(self) -> None:
        """Output without credential-like keys is returned unchanged."""
        assert default_redactor("all good, nothing here") == "all good, nothing here"





class TestCommandCollector:
    """Item 6: execute preconfigured commands; no agent injection."""

    @pytest.fixture()
    def runner(self) -> CommandCollector:
        return CommandCollector(
            {
                "true": CommandSpec(argv=_sys_argv("-c", "pass")),
                "false": CommandSpec(argv=_sys_argv("-c", "raise SystemExit(1)")),
                "echo": CommandSpec(argv=_sys_argv("-c", "print('hello world')")),
                "secret": CommandSpec(
                    argv=_sys_argv("-c", "print('token=s3cr3t-value data')")
                ),
                "big": CommandSpec(argv=_sys_argv("-c", "print('x' * 1000)")),
                "sleep": CommandSpec(
                    argv=_sys_argv("-c", "import time; time.sleep(5)")
                ),
                "missing": CommandSpec(argv=["this-binary-does-not-exist-xyz"]),
            }
        )

    def test_known_commands_frozen(self, runner: CommandCollector) -> None:
        """The runnable command set is exactly what was preconfigured."""
        assert set(runner.known_commands) == {
            "true", "false", "echo", "secret", "big", "sleep", "missing"
        }

    def test_unknown_command_rejected(self, runner: CommandCollector) -> None:
        """The agent cannot name a command that was not preconfigured."""
        with pytest.raises(ValueError, match="unknown command"):
            runner.run("rm-rf-root")

    def test_exit_code_and_runtime_captured(self, runner: CommandCollector) -> None:
        """A successful command records exit 0 and a non-negative runtime."""
        result = runner.run("true")
        assert result.exit_code == 0
        assert result.runtime_seconds >= 0.0
        assert result.timed_out is False
        assert result.error is None

    def test_nonzero_exit_captured(self, runner: CommandCollector) -> None:
        """A failing command records its real exit code, never flipped to pass."""
        result = runner.run("false")
        assert result.exit_code == 1
        assert result.timed_out is False

    def test_stdout_hashed_and_summarised(self, runner: CommandCollector) -> None:
        """stdout is hashed (sha256:) and a redacted summary is retained."""
        result = runner.run("echo", store_raw=True)
        assert result.stdout_hash and result.stdout_hash.startswith("sha256:")
        assert "hello world" in result.stdout_summary
        assert result.stdout_raw is not None

    def test_raw_not_stored_by_default(self, runner: CommandCollector) -> None:
        """Item 13: raw output is NOT retained by default."""
        result = runner.run("echo")
        assert result.stdout_raw is None
        assert result.stderr_raw is None
        assert result.stdout_hash is not None
        assert result.stdout_summary != ""

    def test_secret_redacted_before_storage(self, runner: CommandCollector) -> None:
        """A secret in stdout is masked in summary and raw, never left intact."""
        result = runner.run("secret", store_raw=True)
        assert "s3cr3t-value" not in result.stdout_summary
        assert "s3cr3t-value" not in (result.stdout_raw or "")
        assert "***REDACTED***" in result.stdout_summary

    def test_max_output_size_truncates_summary(self, runner: CommandCollector) -> None:
        """The summary is capped to max_output_bytes; truncation is flagged."""
        result = runner.run("big", max_output_bytes=32)
        assert result.stdout_truncated is True
        assert len(result.stdout_summary.encode("utf-8")) <= 32

    def test_timeout_captured_not_raised(self, runner: CommandCollector) -> None:
        """Item 8: a timeout is surfaced (timed_out, exit_code None), not raised."""
        result = runner.run("sleep", timeout=0.1)
        assert result.timed_out is True
        assert result.exit_code is None

    def test_missing_executable_captured(self, runner: CommandCollector) -> None:
        """A missing command is captured as an error with no exit code."""
        result = runner.run("missing")
        assert result.exit_code is None
        assert result.error is not None
        assert "not found" in result.error

    def test_cwd_override(self, tmp_path) -> None:
        """A per-run cwd override is honoured and recorded."""
        runner = CommandCollector(
            {"pwd": CommandSpec(argv=_sys_argv("-c", "import os; print(os.getcwd())"))}
        )
        result = runner.run("pwd", cwd=str(tmp_path), store_raw=True)
        assert str(tmp_path) in (result.stdout_raw or "")

    def test_command_spec_extra_forbidden(self) -> None:
        """CommandSpec is auditable: stray fields are rejected."""
        with pytest.raises(ValidationError):
            CommandSpec(argv=["x"], oops=True)  # type: ignore[call-arg]

    def test_command_result_extra_forbidden(self) -> None:
        """CommandResult is auditable: stray fields are rejected."""
        with pytest.raises(ValidationError):
            CommandResult(
                name="x", argv=["x"], cwd=None, exit_code=0, runtime_seconds=0.0,
                bogus=True,  # type: ignore[call-arg]
            )




class TestPytestCollector:
    """Item 7 + 8: PytestCollector EXECUTES pytest and emits VERIFIED evidence."""

    @staticmethod
    def _runner(test_dir: str) -> CommandCollector:
        return CommandCollector(
            {
                "pytest": CommandSpec(
                    argv=_sys_argv(
                        "-m", "pytest", "-q", "-p", "no:cacheprovider", test_dir
                    ),
                    timeout=60.0,
                )
            }
        )

    @staticmethod
    def _write_test(path, body: str) -> None:
        path.write_text(textwrap.dedent(body))

    def test_passing_tests_verified(self, tmp_path) -> None:
        """A real green run yields VERIFIED pass with >0 executed tests."""
        self._write_test(tmp_path / "test_pass.py", """
            def test_one():
                assert 1 + 1 == 2
            def test_two():
                assert True
        """)
        collector = PytestCollector(self._runner(str(tmp_path)))
        evidence = collector.collect()
        assert evidence.passed is True
        assert evidence.provenance is EvidenceProvenance.VERIFIED
        assert evidence.collector == "bound.pytest"
        assert evidence.collector_version is not None
        assert evidence.artifact_hash and evidence.artifact_hash.startswith("sha256:")
        assert evidence.observed_at is not None
        assert evidence.observed_at.tzinfo is not None
        assert evidence.status is None

    def test_failing_tests_verified_failure(self, tmp_path) -> None:
        """A failing run yields passed=False with FAILED status, VERIFIED."""
        self._write_test(tmp_path / "test_fail.py", """
            def test_fail():
                assert False, "boom"
        """)
        collector = PytestCollector(self._runner(str(tmp_path)))
        evidence = collector.collect()
        assert evidence.passed is False
        assert evidence.provenance is EvidenceProvenance.VERIFIED
        assert evidence.status is EvidenceStatus.FAILED

    def test_zero_tests_no_proven_pass(self, tmp_path) -> None:
        """Item 8: pytest finds zero tests -> passed=False, UNVERIFIED (not pass)."""
        # An empty test file produces no collected tests.
        self._write_test(tmp_path / "test_empty.py", "\n")
        collector = PytestCollector(self._runner(str(tmp_path)))
        evidence = collector.collect()
        assert evidence.passed is False
        assert evidence.status is EvidenceStatus.UNVERIFIED
        assert evidence.provenance is EvidenceProvenance.VERIFIED

    def test_timeout_invalid_not_pass(self, tmp_path) -> None:
        """Item 8: a timed-out pytest run is INVALID/MISSING, never a pass."""
        self._write_test(tmp_path / "test_slow.py", """
            import time
            def test_slow():
                time.sleep(5)
        """)
        runner = CommandCollector(
            {
                "pytest": CommandSpec(
                    argv=_sys_argv(
                        "-m", "pytest", "-q", "-p", "no:cacheprovider",
                        str(tmp_path / "test_slow.py"),
                    ),
                    timeout=0.3,
                )
            }
        )
        collector = PytestCollector(runner)
        evidence = collector.collect()
        assert evidence.passed is None
        assert evidence.status is EvidenceStatus.INVALID
        assert evidence.provenance is EvidenceProvenance.MISSING

    def test_collector_crash_invalid(self, tmp_path) -> None:
        """A collector whose command cannot start yields INVALID, not a pass."""
        runner = CommandCollector(
            {"pytest": CommandSpec(argv=["this-pytest-does-not-exist-xyz"])}
        )
        collector = PytestCollector(runner)
        evidence = collector.collect()
        assert evidence.passed is None
        assert evidence.status is EvidenceStatus.INVALID
        assert evidence.provenance is EvidenceProvenance.MISSING




class TestJUnitCollector:
    """Item 7 + 8: JUnitCollector parses a trusted artefact directly (VERIFIED)."""

    @staticmethod
    def _junit(
        tests: int, failures: int = 0, errors: int = 0, skipped: int = 0
    ) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<testsuite name="suite" tests="{tests}" failures="{failures}" '
            f'errors="{errors}" skipped="{skipped}">\n'
            "</testsuite>\n"
        )

    def test_passing_artefact_verified(self, tmp_path) -> None:
        """A clean JUnit artefact yields VERIFIED pass with tests>0."""
        path = tmp_path / "junit.xml"
        path.write_text(self._junit(tests=5))
        evidence = JUnitCollector().collect(path)
        assert evidence.passed is True
        assert evidence.provenance is EvidenceProvenance.VERIFIED
        assert evidence.collector == "bound.junit"
        assert evidence.artifact_hash and evidence.artifact_hash.startswith("sha256:")
        assert evidence.raw_artifact_ref == str(path)
        assert evidence.status is None

    def test_failures_verified_failure(self, tmp_path) -> None:
        """Failures in the artefact yield passed=False, FAILED, VERIFIED."""
        path = tmp_path / "junit.xml"
        path.write_text(self._junit(tests=3, failures=1))
        evidence = JUnitCollector().collect(path)
        assert evidence.passed is False
        assert evidence.status is EvidenceStatus.FAILED
        assert evidence.provenance is EvidenceProvenance.VERIFIED

    def test_errors_verified_failure(self, tmp_path) -> None:
        """Errors in the artefact yield passed=False, FAILED."""
        path = tmp_path / "junit.xml"
        path.write_text(self._junit(tests=3, errors=1))
        evidence = JUnitCollector().collect(path)
        assert evidence.passed is False
        assert evidence.status is EvidenceStatus.FAILED

    def test_zero_tests_no_proven_pass(self, tmp_path) -> None:
        """Item 8: tests=0 -> passed=False, UNVERIFIED (no proven pass)."""
        path = tmp_path / "junit.xml"
        path.write_text(self._junit(tests=0))
        evidence = JUnitCollector().collect(path)
        assert evidence.passed is False
        assert evidence.status is EvidenceStatus.UNVERIFIED
        assert evidence.provenance is EvidenceProvenance.VERIFIED

    def test_stale_artefact_invalid(self, tmp_path) -> None:
        """Item 8: an artefact older than the freshness window is INVALID."""
        path = tmp_path / "junit.xml"
        path.write_text(self._junit(tests=5))
        future = datetime.now(UTC) + timedelta(hours=1)
        evidence = JUnitCollector(max_age_seconds=1.0).collect(path, now=future)
        assert evidence.passed is None
        assert evidence.status is EvidenceStatus.INVALID
        assert evidence.provenance is EvidenceProvenance.MISSING

    def test_fresh_artefact_accepted(self, tmp_path) -> None:
        """An artefact within the freshness window is still VERIFIED."""
        path = tmp_path / "junit.xml"
        path.write_text(self._junit(tests=5))
        now = datetime.now(UTC)
        evidence = JUnitCollector(max_age_seconds=3600.0).collect(path, now=now)
        assert evidence.passed is True
        assert evidence.provenance is EvidenceProvenance.VERIFIED

    def test_missing_artefact_missing(self, tmp_path) -> None:
        """A missing file yields MISSING status, not a pass."""
        evidence = JUnitCollector().collect(tmp_path / "absent.xml")
        assert evidence.passed is None
        assert evidence.status is EvidenceStatus.MISSING
        assert evidence.provenance is EvidenceProvenance.MISSING

    def test_oversized_artefact_invalid(self, tmp_path) -> None:
        """Item 13: an artefact above the size limit is INVALID."""
        path = tmp_path / "junit.xml"
        path.write_text(self._junit(tests=5) + "x" * 200)
        evidence = JUnitCollector(max_file_bytes=64).collect(path)
        assert evidence.passed is None
        assert evidence.status is EvidenceStatus.INVALID
        assert evidence.provenance is EvidenceProvenance.MISSING

    def test_malformed_xml_invalid(self, tmp_path) -> None:
        """Item 8: a parse failure yields INVALID, never a pass."""
        path = tmp_path / "junit.xml"
        path.write_text("<not><valid xml")
        evidence = JUnitCollector().collect(path)
        assert evidence.passed is None
        assert evidence.status is EvidenceStatus.INVALID
        assert evidence.provenance is EvidenceProvenance.MISSING

    def test_testsuites_root_parsed(self, tmp_path) -> None:
        """A ``<testsuites>`` wrapper with child suites is summed correctly."""
        path = tmp_path / "junit.xml"
        path.write_text(
            '<?xml version="1.0"?>\n<testsuites>\n'
            '<testsuite name="a" tests="2" failures="0" errors="0" skipped="0"/>\n'
            '<testsuite name="b" tests="3" failures="0" errors="0" skipped="0"/>\n'
            "</testsuites>\n"
        )
        evidence = JUnitCollector().collect(path)
        assert evidence.passed is True
        assert "tests=5" in (evidence.details or "")




def _git(repo: Path, *args: str) -> str:
    """Run a git command in *repo*, returning stdout; raises on failure."""
    import subprocess

    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _make_repo(tmp_path) -> Path:
    """Create a small git repo with one committed file and return its path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "src" / "todo_app").mkdir(parents=True)
    (repo / "src" / "todo_app" / "service.py").write_text("x = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


class TestGitCollector:
    """Item 7 + 8: GitCollector EXECUTES git and emits VERIFIED clean evidence."""

    def _collector(self, repo: Path, *, allowed: tuple[str, ...] = ()) -> GitCollector:
        runner = CommandCollector(
            {"git-status": CommandSpec(argv=["git", "status", "--porcelain"])}
        )
        return GitCollector(
            runner, allowed_prefixes=allowed, check_id="no-unexpected-files"
        )

    def test_clean_tree_verified(self, tmp_path) -> None:
        """A clean working tree yields VERIFIED pass."""
        repo = _make_repo(tmp_path)
        evidence = self._collector(repo).collect(cwd=str(repo))
        assert evidence.passed is True
        assert evidence.provenance is EvidenceProvenance.VERIFIED
        assert evidence.collector == "bound.git"
        assert evidence.status is None

    def test_unexpected_path_failed(self, tmp_path) -> None:
        """A change outside allowed prefixes yields passed=False, FAILED."""
        repo = _make_repo(tmp_path)
        (repo / "README.md").write_text("changed\n")
        evidence = self._collector(repo, allowed=("src/todo_app",)).collect(
            cwd=str(repo)
        )
        assert evidence.passed is False
        assert evidence.status is EvidenceStatus.FAILED
        assert evidence.provenance is EvidenceProvenance.VERIFIED

    def test_expected_path_still_clean(self, tmp_path) -> None:
        """A change inside allowed prefixes keeps a proven clean tree."""
        repo = _make_repo(tmp_path)
        (repo / "src" / "todo_app" / "service.py").write_text("x = 2\n")
        evidence = self._collector(repo, allowed=("src/todo_app",)).collect(
            cwd=str(repo)
        )
        assert evidence.passed is True
        assert evidence.provenance is EvidenceProvenance.VERIFIED

    def test_non_git_dir_invalid(self, tmp_path) -> None:
        """A non-repo cwd makes git exit non-zero -> INVALID, not a clean pass."""
        evidence = self._collector(tmp_path).collect(cwd=str(tmp_path))
        assert evidence.passed is False
        assert evidence.status is EvidenceStatus.INVALID
        assert evidence.provenance is EvidenceProvenance.MISSING

    def test_timeout_invalid(self, tmp_path) -> None:
        """Item 8: a timed-out git command is INVALID, never a clean pass."""
        runner = CommandCollector(
            {
                "git-status": CommandSpec(
                    argv=_sys_argv("-c", "import time; time.sleep(5)"),
                    timeout=0.2,
                )
            }
        )
        collector = GitCollector(runner)
        evidence = collector.collect(cwd=str(tmp_path))
        assert evidence.passed is None
        assert evidence.status is EvidenceStatus.INVALID
        assert evidence.provenance is EvidenceProvenance.MISSING




class TestBudgetCollector:
    """Item 7: BudgetCollector emits OBSERVED metrics; None is MISSING, not 0."""

    def test_measured_values_observed(self) -> None:
        """Measured telemetry is OBSERVED with the real value."""
        metrics = BudgetCollector().metrics(
            token_usage=1200, runtime_seconds=3.5, tool_call_count=12, retry_count=1
        )
        assert isinstance(metrics, BudgetMetrics)
        assert metrics.token_usage.value == 1200
        assert metrics.token_usage.provenance is EvidenceProvenance.OBSERVED
        assert metrics.tool_call_count.value == 12
        assert metrics.tool_call_count.provenance is EvidenceProvenance.OBSERVED
        assert metrics.retry_count.value == 1
        assert metrics.runtime_seconds.value == 3.5
        assert all(
            m.collector == "bound.budget"
            for m in (
                metrics.token_usage,
                metrics.runtime_seconds,
                metrics.tool_call_count,
                metrics.retry_count,
            )
        )

    def test_absent_values_missing_not_zero(self) -> None:
        """An unmeasured signal is MISSING with value None, never a silent 0."""
        metrics = BudgetCollector().metrics()
        assert metrics.token_usage.value is None
        assert metrics.token_usage.provenance is EvidenceProvenance.MISSING
        assert metrics.tool_call_count.value is None
        assert metrics.tool_call_count.provenance is EvidenceProvenance.MISSING

    def test_mixed_measured_and_missing(self) -> None:
        """A partially-instrumented harness mixes OBSERVED and MISSING honestly."""
        metrics = BudgetCollector().metrics(tool_call_count=0)
        assert metrics.tool_call_count.value == 0
        assert metrics.tool_call_count.provenance is EvidenceProvenance.OBSERVED
        assert metrics.token_usage.value is None
        assert metrics.token_usage.provenance is EvidenceProvenance.MISSING

    def test_budget_metrics_extra_forbidden(self) -> None:
        """BudgetMetrics is auditable: stray fields are rejected."""
        with pytest.raises(ValidationError):
            BudgetMetrics(
                token_usage=EvidenceMetric(value=None),
                runtime_seconds=EvidenceMetric(value=None),
                tool_call_count=EvidenceMetric(value=None),
                retry_count=EvidenceMetric(value=None),
                oops=True,  # type: ignore[call-arg]
            )


class TestProcessRuntimeCollector:
    """Item 7 + 8: ProcessRuntimeCollector emits VERIFIED exit + OBSERVED runtime."""

    @staticmethod
    def _result(exit_code: int | None, runtime: float = 0.01, **kw) -> CommandResult:
        return CommandResult(
            name="cmd",
            argv=["cmd"],
            cwd=None,
            exit_code=exit_code,
            runtime_seconds=runtime,
            **kw,
        )

    def test_exit_zero_verified_pass(self) -> None:
        """Exit 0 yields VERIFIED pass."""
        evidence = ProcessRuntimeCollector().collect(self._result(0))
        assert evidence.passed is True
        assert evidence.provenance is EvidenceProvenance.VERIFIED
        assert evidence.collector == "bound.process"

    def test_exit_nonzero_verified_failure(self) -> None:
        """Exit non-zero yields passed=False, FAILED, VERIFIED."""
        evidence = ProcessRuntimeCollector().collect(self._result(2))
        assert evidence.passed is False
        assert evidence.status is EvidenceStatus.FAILED
        assert evidence.provenance is EvidenceProvenance.VERIFIED

    def test_timeout_invalid_not_pass(self) -> None:
        """Item 8: no exit code (timeout) is INVALID, never a pass."""
        evidence = ProcessRuntimeCollector().collect(self._result(None, timed_out=True))
        assert evidence.passed is None
        assert evidence.status is EvidenceStatus.INVALID
        assert evidence.provenance is EvidenceProvenance.MISSING

    def test_runtime_metric_observed(self) -> None:
        """Runtime is OBSERVED even on timeout (we measured how long we waited)."""
        result = self._result(None, runtime=1.25, timed_out=True)
        metric = ProcessRuntimeCollector().runtime_metric(result)
        assert metric.value == 1.25
        assert metric.provenance is EvidenceProvenance.OBSERVED
        assert metric.collector == "bound.process"

    def test_check_id_override(self) -> None:
        """A per-call check-id override is honoured."""
        evidence = ProcessRuntimeCollector().collect(
            self._result(0), check_id="build-ok"
        )
        assert evidence.check_id == "build-ok"

