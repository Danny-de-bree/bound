from __future__ import annotations

import pytest
from pydantic import ValidationError

from bound.collectors import (
    GitInspection,
    PytestSummary,
    ServiceTestEvidence,
    parse_git_status_porcelain,
    parse_pytest_summary,
)

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


