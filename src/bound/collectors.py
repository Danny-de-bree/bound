from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "GitInspection",
    "PytestSummary",
    "ServiceTestEvidence",
    "parse_git_status_porcelain",
    "parse_pytest_summary",
]


#: Match a pytest ``-q`` summary token that represents a *test* outcome. We
#: deliberately EXCLUDE ``warnings`` (a warning is not a test), ``deselected``
#: (a filter, not an execution), and ``rerun`` (a retry, not a distinct
#: test). ``\b`` boundaries keep us from matching substrings of other words.
#: Supports the common pytest summary states: ``passed``, ``failed``,
#: ``error``/``errors``, ``skipped``, ``xfailed``, ``xpassed``. Mirrors the
#: reference parser in the Todo benchmark integration.
_TEST_OUTCOME_RE = re.compile(
    r"\b(\d+)\s+(passed|failed|errors?|skipped|xfailed|xpassed)\b"
)

#: Map each summary keyword to the :class:`PytestSummary` field that counts
#: it. ``error`` and ``errors`` both fold into the ``errors`` field. Kept as a
#: plain dict (not an enum) so the parser stays trivially inspectable.
_OUTCOME_TO_FIELD: dict[str, str] = {
    "passed": "passed",
    "failed": "failed",
    "error": "errors",
    "errors": "errors",
    "skipped": "skipped",
    "xfailed": "xfailed",
    "xpassed": "xpassed",
}

class PytestSummary(BaseModel):
    """Counts of pytest test outcomes parsed from a ``-q`` summary line.

    Each field is the number of tests that ended in that pytest outcome.
    **Warnings are not tests**, so there is no ``warnings`` field: a summary
    such as ``30 passed, 2 warnings`` records ``passed == 30``, *not* 32 —
    the ``2 warnings`` token is deliberately excluded, as are ``deselected``
    (a collection filter, not an execution) and ``rerun`` (a retry, not a
    distinct test). This is the Phase 4 fix: the old parser counted warnings
    as tests.

    Use :func:`parse_pytest_summary` to build one from captured ``pytest``
    output, then read :attr:`executed_test_count` for the contractually
    meaningful "how many tests actually ran" number. The field names measure
    *executed* tests (the old ``collected_count`` name is misleading unless
    pytest collection is explicitly measured, which it is not here).

    Attributes:
        passed: Number of tests that passed (``N passed``).
        failed: Number of tests that failed (``N failed``).
        errors: Number of collection/session errors (``N error`` /
            ``N errors``).
        skipped: Number of tests that were skipped (``N skipped``).
        xfailed: Number of tests that xfailed (expected failure; ``N
            xfailed``).
        xpassed: Number of tests that xpassed (unexpectedly passed an
            xfail; ``N xpassed``).
    """

    model_config = ConfigDict(extra="forbid")

    passed: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    errors: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)
    xfailed: int = Field(default=0, ge=0)
    xpassed: int = Field(default=0, ge=0)

    @property
    def executed_test_count(self) -> int:
        """Total number of tests that actually executed.

        Sums every executed-outcome count — passed, failed, errors, skipped,
        xfailed, xpassed — and **excludes** warnings, deselected, and rerun,
        which are not distinct executed tests. This is the number a contract's
        ``tests-pass`` / ``service-tests-pass`` check should treat as "tests
        that ran".

        Returns:
            The total count of executed tests across all counted outcomes.
        """
        return (
            self.passed
            + self.failed
            + self.errors
            + self.skipped
            + self.xfailed
            + self.xpassed
        )


def parse_pytest_summary(text: str) -> PytestSummary:
    """Parse pytest ``-q`` summary output into a :class:`PytestSummary`.

    pytest's final summary is the last line that carries a test-outcome token
    (``N passed``, ``N failed``, ...). A trailing "warnings summary" detail
    block — if present — carries no test-outcome token and is ignored, so a
    line such as ``30 passed, 2 warnings`` yields ``executed_test_count == 30``
    (the warnings are not counted). Every supported outcome (passed, failed,
    error(s), skipped, xfailed, xpassed) on that final summary line is summed
    into its own field.

    The no-summary / empty case is handled gracefully: an empty string, a
    string with no outcome token, or a collection error with no counts all
    return an all-zero :class:`PytestSummary` (so :attr:`executed_test_count`
    is ``0``). The caller decides whether zero executed tests means "passing
    because nothing failed" or "unproven because nothing ran"; for a
    *service-specific* check that distinction is what
    :class:`ServiceTestEvidence` encodes.

    Args:
        text: The captured stdout of ``uv run pytest -q`` (or the
            ``-q``-style summary block). Pure parsing only — the caller is
            responsible for capturing it.

    Returns:
        A :class:`PytestSummary` whose fields count the test outcomes on the
        final summary line (all zero when no summary line is found).
    """
    summary_line = _last_summary_line(text)
    if summary_line is None:
        return PytestSummary()
    counts: dict[str, int] = {
        "passed": 0,
        "failed": 0,
        "errors": 0,
        "skipped": 0,
        "xfailed": 0,
        "xpassed": 0,
    }
    for match in _TEST_OUTCOME_RE.finditer(summary_line):
        field = _OUTCOME_TO_FIELD[match.group(2)]
        counts[field] += int(match.group(1))
    return PytestSummary(**counts)


def _last_summary_line(text: str) -> str | None:
    """Return the last non-empty line of *text* with a test-outcome token.

    pytest prints its ``N passed, ...`` summary on the final non-empty line of
    a ``-q`` run, but a trailing "warnings summary" detail block can add more
    non-empty lines afterwards that carry no test-outcome token. We scan from
    the end and return the first (i.e. last-in-file) line that *does* carry a
    test-outcome token, so the trailing detail block is ignored.

    Args:
        text: The captured pytest output.

    Returns:
        The last line containing a test-outcome token, or ``None`` when no
        such line exists (empty input, no summary line, a pure collection
        error, ...).
    """
    lines = [line for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return None
    for line in reversed(lines):
        if _TEST_OUTCOME_RE.search(line):
            return line
    return None
class GitInspection(BaseModel):
    """Result of inspecting a working tree via ``git status --porcelain``.

    The crucial invariant (Phase 3): the git command's **success is tracked
    separately** from the parsed path list. A *failed* git command yields an
    empty path list because git could not report anything — *not* because the
    tree is clean — so any "no unexpected files" conclusion would be
    unproven. :meth:`is_clean_proven` is the only safe way to read "was the
    tree clean", and it returns ``False`` whenever :attr:`command_succeeded`
    is ``False``. This makes it impossible to convert unavailable evidence
    into a passing risk check: see :meth:`command_failed` for the failed-path
    factory and :meth:`is_clean_proven` for the guarded read.

    Attributes:
        command_succeeded: Whether the underlying ``git status`` command
            exited 0. ``False`` means the path list is *untrustworthy* (git
            could not report), so no "clean tree" conclusion may be drawn.
        changed_paths: Paths that ``git status --porcelain`` reported as
            modified/added/deleted/untracked, exactly as git reports them
            (status flags stripped). Empty on a genuinely clean tree *and*
            on git failure — disambiguate the two with
            :attr:`command_succeeded` (or :meth:`is_clean_proven`).
        unexpected_paths: The subset of :attr:`changed_paths` whose prefix
            is NOT in the contract's allowed set. A non-empty list is
            concrete risk evidence; an empty list is *only* meaningful when
            :attr:`command_succeeded` is ``True``.
    """

    model_config = ConfigDict(extra="forbid")

    command_succeeded: bool
    changed_paths: list[str] = Field(default_factory=list)
    unexpected_paths: list[str] = Field(default_factory=list)

    @classmethod
    def command_failed(cls) -> GitInspection:
        """Build a :class:`GitInspection` for a git command that failed.

        A failed ``git status`` produces no trustworthy output: the path
        lists are empty because git could not report anything, *not* because
        the tree is clean. This factory encodes that fact directly — both
        path lists are empty and :attr:`command_succeeded` is ``False`` —
        and :meth:`is_clean_proven` then refuses to treat the empty lists as
        proof of cleanliness. Use it from the I/O glue whenever the captured
        ``git status`` exited non-zero (or could not be captured at all), so
        a missing command can never become a passing ``no-unexpected-files``
        risk check.

        Returns:
            A :class:`GitInspection` with ``command_succeeded=False`` and
            empty path lists; safe to feed to the contract layer because it
            will be scored as unproven, not as clean.
        """
        return cls(command_succeeded=False, changed_paths=[], unexpected_paths=[])

    def is_clean_proven(self) -> bool:
        """Whether the tree is *proven* clean (no unexpected files, command OK).

        This is the only safe way to read "is the working tree clean" from a
        :class:`GitInspection`. It returns ``True`` only when **both** hold:

        * :attr:`command_succeeded` is ``True`` (git actually reported), and
        * :attr:`unexpected_paths` is empty (no path fell outside the
          allowed set).

        It deliberately returns ``False`` when the git command failed, even
        though :attr:`unexpected_paths` is empty in that case — an empty
        list that came from a command that could not run is *unavailable*
        evidence, not *clean* evidence. Per BOUND's inviolable rule we never
        convert unavailable evidence into a passing check.

        Returns:
            ``True`` iff the git command succeeded AND no unexpected paths
            were observed; ``False`` otherwise (including the failed-command
            case).
        """
        return self.command_succeeded and not self.unexpected_paths

def parse_git_status_porcelain(
    output: str,
    allowed_prefixes: tuple[str, ...],
) -> GitInspection:
    """Parse ``git status --porcelain`` output into a :class:`GitInspection`.

    The caller is expected to have *already* captured (and verified the exit
    code of) a real ``git status --porcelain`` invocation — this function
    does pure parsing only, with no subprocess and no filesystem access.
    Because we are parsing the output of a command that *did* run, the
    returned :class:`GitInspection` has :attr:`command_succeeded` set to
    ``True``; callers whose git command failed must instead use
    :meth:`GitInspection.command_failed` so the failed command is not
    misrecorded as a successful (and thus clean-tree) inspection.

    Each porcelain line is ``XY path`` (two status flags, a space, the
    path), or ``XY old -> new`` for a rename; the status flags are stripped
    and, for renames, the destination path is taken. Paths are returned
    exactly as git reports them (relative to the repo root), with
    surrounding quotes — used by git for paths with special characters —
    stripped. A path is "unexpected" when none of ``allowed_prefixes`` is a
    prefix of it.

    Args:
        output: The captured stdout of ``git status --porcelain``. Pure
            parsing only — the caller captured it.
        allowed_prefixes: The contract's allowed path prefixes (e.g.
            ``("src/todo_app", "tests", "bound_integration", ...)``). A
            changed path whose prefix is not in this set lands in
            :attr:`GitInspection.unexpected_paths`.

    Returns:
        A :class:`GitInspection` with ``command_succeeded=True``, the parsed
        :attr:`~GitInspection.changed_paths`, and the subset of those that
        are :attr:`~GitInspection.unexpected_paths`.
    """
    changed_paths: list[str] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        # Format is "XY path" (or "XY old -> new" for renames); drop the
        # leading "XY " flags and, for renames, take the destination.
        path = line[3:].split(" -> ", 1)[-1].strip().strip('"')
        if path:
            changed_paths.append(path)
    unexpected_paths = [
        path for path in changed_paths if not _path_allowed(path, allowed_prefixes)
    ]
    return GitInspection(
        command_succeeded=True,
        changed_paths=changed_paths,
        unexpected_paths=unexpected_paths,
    )
class ServiceTestEvidence(BaseModel):
    """Service-specific acceptance evidence for ``service-tests-pass``.

    Phase 5 keeps the *service-specific* ``service-tests-pass`` check distinct
    from the full-suite ``tests-pass`` check. The contract meaning of
    ``service-tests-pass`` is precise: ``tests/test_service.py`` executed *at
    least one test* **AND** the verification command exited successfully. A
    green full suite is not proof that the service tests ran (they may have
    been skipped, or the module may be empty), and a zero-exit command that
    executed zero tests is not proof that the service tests passed (nothing
    ran to pass).

    This model records exactly the two facts the contract needs — whether
    the command succeeded and how many tests it executed — and exposes them
    through :attr:`passed`, which is ``True`` **only** when both hold. The
    full-suite ``tests-pass`` check is separate and uses
    :class:`PytestSummary` on the output of the unscoped
    ``uv run pytest -q`` run.

    Attributes:
        command_succeeded: Whether the service-specific verification command
            (e.g. ``uv run pytest tests/test_service.py -q``) exited 0.
        executed_test_count: Number of tests that actually executed in the
            service-specific run (parsed via :func:`parse_pytest_summary`).
            ``0`` means nothing ran — which fails :attr:`passed` regardless
            of :attr:`command_succeeded`.
    """

    model_config = ConfigDict(extra="forbid")

    command_succeeded: bool
    executed_test_count: int = Field(default=0, ge=0)

    @property
    def passed(self) -> bool:
        """Whether the ``service-tests-pass`` contract check is satisfied.

        ``True`` **only** when :attr:`command_succeeded` is ``True`` **and**
        :attr:`executed_test_count` is at least ``1``. This encodes the
        contract meaning directly: the service tests genuinely ran (>=1
        executed) AND the command exited successfully. A passing full suite
        whose service module executed zero tests (or a zero-exit command
        that ran nothing) does **not** satisfy it — that is the Phase 5 fix.

        Returns:
            ``True`` iff the command succeeded and at least one test
            executed.
        """
        return self.command_succeeded and self.executed_test_count >= 1



def _path_allowed(path: str, allowed_prefixes: tuple[str, ...]) -> bool:
    """Whether *path* starts with any of *allowed_prefixes*.

    A path is allowed when at least one prefix is a prefix of it. Matching
    is literal string prefix matching on the path as git reports it
    (relative to the repo root); a prefix of ``"src/todo_app"`` allows
    ``src/todo_app/service.py`` but not ``src/other_app/service.py``.

    Args:
        path: A changed path reported by ``git status --porcelain``.
        allowed_prefixes: The contract's allowed path prefixes.

    Returns:
        ``True`` iff *path* starts with at least one allowed prefix.
    """
    return any(path.startswith(prefix) for prefix in allowed_prefixes)

