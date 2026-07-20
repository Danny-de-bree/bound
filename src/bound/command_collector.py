from __future__ import annotations

import hashlib
import shlex
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version
from pathlib import Path
from xml.etree import ElementTree

from pydantic import BaseModel, ConfigDict

from bound.collectors import (
    GitInspection,
    parse_git_status_porcelain,
    parse_pytest_summary,
)
from bound.evidence import (
    SECRET_PATTERN,
    CheckEvidence,
    EvidenceMetric,
    EvidenceProvenance,
    EvidenceStatus,
)

__all__ = [
    "BudgetCollector",
    "BudgetMetrics",
    "CommandCollector",
    "CommandResult",
    "CommandSpec",
    "GitCollector",
    "JUnitCollector",
    "ProcessRuntimeCollector",
    "PytestCollector",
    "Redactor",
    "default_redactor",
    "sha256_hex",
]

#: Resolution of the running ``bound-policy`` distribution version, used as the
#: ``collector_version`` on every emitted piece of evidence for reproducibility
#: audits. Resolved via :mod:`importlib.metadata` so importing this module never
#: creates a circular dependency on the package's ``__init__`` (which would run
#: before ``__version__`` is assigned).
try:
    _BOUND_VERSION: str = _dist_version("bound-policy")
except PackageNotFoundError:  # pragma: no cover - dev/editable fallback
    _BOUND_VERSION = "0.0.0+unknown"

#: Default wall-clock timeout (seconds) for a collector-run command when neither
#: the :class:`CommandSpec` nor the ``collect`` call overrides it. Bounded so a
#: hung verification command can never block the harness indefinitely.
DEFAULT_TIMEOUT: float = 30.0

#: Default cap (bytes of UTF-8) on the stdout/stderr *summary* retained on
#: evidence. The full output is hashed before truncation, so the cap never loses
#: tamper-detection; it only bounds what is *stored/displayed*.
DEFAULT_MAX_OUTPUT_BYTES: int = 65_536

#: A redaction hook: a pure function that strips secrets from a captured text
#: stream *before* it is hashed, summarised, or retained.
Redactor = Callable[[str], str]




def default_redactor(text: str) -> str:
    """Mask secret-looking ``key=value`` tokens in *text*.

    Replaces the captured secret portion of any ``key=value`` / ``key: value``
    occurrence whose key looks like a credential name with ``***REDACTED***``.
    This is the collector-side counterpart of
    :func:`bound.lineage_store.scrub_secrets`, applied to raw command output
    before hashing or summarising, so a secret in ``stdout``/``stderr`` can
    never reach a persisted trace.

    Args:
        text: Captured command output (stdout or stderr).

    Returns:
        The text with secret values masked in place.
    """
    return SECRET_PATTERN.sub(lambda m: f"{m.group(1)}=***REDACTED***", text)


def sha256_hex(data: bytes) -> str:
    """Return the ``sha256:``-prefixed hex digest of *data*.

    The ``sha256:`` prefix matches the
    :attr:`~bound.evidence.CheckEvidence.artifact_hash` convention so a verifier
    can re-fetch and re-hash a raw artefact without retaining its (possibly
    sensitive) contents.

    Args:
        data: The raw bytes to hash.

    Returns:
        ``"sha256:<64 hex chars>"``.
    """
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _truncate_text(text: str, cap: int | None) -> tuple[str, bool]:
    """Truncate *text* to at most *cap* UTF-8 bytes.

    Returns the (possibly truncated) text and a flag indicating whether
    truncation occurred. When *cap* is ``None`` or non-positive the text is
    returned unchanged (no cap enforced).

    Args:
        text: The text to cap.
        cap: Maximum number of UTF-8 bytes to retain.

    Returns:
        A ``(truncated_text, truncated)`` tuple.
    """
    if cap is None or cap <= 0:
        return text, False
    data = text.encode("utf-8", errors="replace")
    if len(data) <= cap:
        return text, False
    return data[:cap].decode("utf-8", errors="ignore"), True


def _now_utc() -> datetime:
    """A timezone-aware UTC timestamp for :attr:`CheckEvidence.observed_at`."""
    return datetime.now(UTC)


def _command_source(argv: list[str]) -> str:
    """A shell-quoted, human-readable rendering of *argv* for the ``source``."""
    return " ".join(shlex.quote(part) for part in argv)



def _bound_check(
    check_id: str,
    *,
    passed: bool | None,
    provenance: EvidenceProvenance,
    collector: str,
    status: EvidenceStatus | None = None,
    details: str | None = None,
    artifact_hash: str | None = None,
    raw_artifact_ref: str | None = None,
    source: str = "",
) -> CheckEvidence:
    """Build a :class:`CheckEvidence` with the standard collector metadata.

    Centralises collector-version + observed-at stamping so every collector emits
    reproducible, auditable evidence. Raw command output is never placed here —
    only a hash, a short summary (folded into ``details``), and an optional
    artefact reference.

    Args:
        check_id: The contract check identifier this evidence speaks to.
        passed: Observed pass/fail outcome (``None`` = undetermined).
        provenance: Trust provenance — VERIFIED for independently executed,
            MISSING for unavailable/crashed/stale.
        collector: Collector name (e.g. ``"bound.pytest"``).
        status: Optional :class:`EvidenceStatus` separating failure from
            unverifiable/invalid evidence.
        details: Human-readable elaboration (summary, hashes, counts).
        artifact_hash: ``sha256:`` content hash of the backing raw artefact.
        raw_artifact_ref: Optional path/URI to the raw artefact (no contents).
        source: Free-form provenance string (command, path, tool name).

    Returns:
        A fully-stamped :class:`CheckEvidence`.
    """
    return CheckEvidence(
        check_id=check_id,
        passed=passed,
        provenance=provenance,
        collector=collector,
        collector_version=_BOUND_VERSION,
        observed_at=_now_utc(),
        status=status,
        details=details,
        artifact_hash=artifact_hash,
        raw_artifact_ref=raw_artifact_ref,
        source=source,
    )


# ---------------------------------------------------------------------------
# CommandCollector
# ---------------------------------------------------------------------------


class CommandSpec(BaseModel):
    """A single preconfigured verification command.

    The agent may only *name* a command; it can never supply arbitrary ``argv``.
    A :class:`CommandCollector` is constructed with a registry of these specs and
    refuses any name not in that registry, which is the BOUND v0.7 rule that
    keeps the agent out of the verification-command loop (item 6).

    Attributes:
        argv: The exact argument vector to execute. Set once, by the integrator.
        cwd: Working directory for the command, or ``None`` to inherit.
        timeout: Wall-clock timeout (seconds) for this command, overriding the
            collector default. ``None`` falls back to the collector default.
        max_output_bytes: Cap (UTF-8 bytes) on the retained stdout/stderr
            summary, overriding the collector default.
        env: Optional full environment mapping for the subprocess. When ``None``
            the process inherits the parent environment. Never stored on emitted
            evidence (may contain secrets).
    """

    model_config = ConfigDict(extra="forbid")

    argv: list[str]
    cwd: str | None = None
    timeout: float | None = None
    max_output_bytes: int | None = None
    env: dict[str, str] | None = None




class CommandResult(BaseModel):
    """The outcome of executing one preconfigured command.

    This is the *transient* execution record shared with the official collectors
    (pytest/git/process). It carries everything the collectors need to build
    evidence — exit code, runtime, hashes, a redacted+capped summary — but
    **not** the full raw output by default. Full redacted output is retained in
    :attr:`stdout_raw`/:attr:`stderr_raw` only when the caller explicitly opted in
    via ``store_raw=True``; otherwise those fields are ``None`` (item 13 privacy:
    raw output is not stored in full by default).

    Attributes:
        name: The registered command name that was run.
        argv: The argument vector that executed.
        cwd: Working directory used, or ``None``.
        exit_code: The process exit code. ``None`` when the command timed out,
            could not be started, or crashed.
        runtime_seconds: Wall-clock runtime in seconds.
        timed_out: Whether the command exceeded its timeout.
        stdout_hash: ``sha256:`` digest of the *redacted* full stdout, or ``None``
            when nothing was captured.
        stderr_hash: ``sha256:`` digest of the *redacted* full stderr, or ``None``.
        stdout_summary: Short, redacted, size-capped excerpt of stdout for
            evidence ``details`` (never the full output).
        stderr_summary: Short, redacted, size-capped excerpt of stderr.
        stdout_truncated: Whether :attr:`stdout_summary` was truncated.
        stderr_truncated: Whether :attr:`stderr_summary` was truncated.
        stdout_raw: Full redacted stdout — populated only when ``store_raw`` was
            requested; ``None`` otherwise.
        stderr_raw: Full redacted stderr — populated only when ``store_raw`` was
            requested; ``None`` otherwise.
        error: Collector error message (e.g. command not found, unexpected
            exception), or ``None`` on a clean run (including timeout, which is
            reported via :attr:`timed_out`).
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    argv: list[str]
    cwd: str | None
    exit_code: int | None
    runtime_seconds: float
    timed_out: bool = False
    stdout_hash: str | None = None
    stderr_hash: str | None = None
    stdout_summary: str = ""
    stderr_summary: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    stdout_raw: str | None = None
    stderr_raw: str | None = None
    error: str | None = None




class CommandCollector:
    """Executes preconfigured commands and captures fail-safe evidence.

    The core of item 6. A :class:`CommandCollector` owns a registry of
    :class:`CommandSpec` objects keyed by name; it runs a command *only* when the
    caller names one from that registry. The agent cannot inject arbitrary
    verification commands. Each run records command, cwd, exit code, runtime;
    hashes stdout/stderr (sha256); applies a redactor; enforces a timeout and a
    maximum output size; and truncates the retained summary. Failures (timeout,
    missing executable, unexpected exception) are captured and surfaced on the
    :class:`CommandResult` rather than swallowed.
    """

    def __init__(
        self,
        commands: dict[str, CommandSpec],
        *,
        redactor: Redactor | None = None,
        store_raw: bool = False,
        default_timeout: float | None = DEFAULT_TIMEOUT,
        default_max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    ) -> None:
        """Configure the command registry and privacy defaults.

        Args:
            commands: Mapping of command *name* -> :class:`CommandSpec`. Only
                these names are runnable; unknown names raise ``ValueError``.
            redactor: Secret-redaction hook applied to stdout/stderr before any
                storage. Defaults to :func:`default_redactor`. Pass a no-op
                ``lambda s: s`` to disable redaction explicitly.
            store_raw: Whether to retain the full redacted output in
                :attr:`CommandResult.stdout_raw`/:attr:`stderr_raw` by default.
                ``False`` (the default) honours item 13: raw output is not stored
                in full. Overridable per ``run`` call.
            default_timeout: Wall-clock timeout when a spec/call does not set one.
            default_max_output_bytes: Default summary byte cap.
        """
        self._commands: dict[str, CommandSpec] = dict(commands)
        self._redactor: Redactor = (
            redactor if redactor is not None else default_redactor
        )
        self._store_raw = store_raw
        self._default_timeout = default_timeout
        self._default_max_output_bytes = default_max_output_bytes

    @property
    def known_commands(self) -> tuple[str, ...]:
        """The command names this collector is permitted to run (frozen tuple)."""
        return tuple(self._commands)


    def run(
        self,
        name: str,
        *,
        cwd: str | None = None,
        timeout: float | None = None,
        max_output_bytes: int | None = None,
        store_raw: bool | None = None,
    ) -> CommandResult:
        """Execute the preconfigured command *name* and capture its outcome.

        The command must be registered up front; an unknown name raises
        ``ValueError`` (a misconfiguration, not a recoverable runtime failure).
        Overrides for ``cwd``/``timeout``/``max_output_bytes`` apply to this run
        only; unspecified values fall back to the spec then the collector
        defaults. Timeout, missing-executable, and unexpected exceptions are
        captured onto the returned :class:`CommandResult` (``timed_out`` /
        ``error`` / ``exit_code is None``) — never raised past this method,
        matching the item-8 fail-safe contract.

        Args:
            name: A registered command name.
            cwd: Optional working-directory override.
            timeout: Optional wall-clock timeout override (seconds).
            max_output_bytes: Optional summary byte-cap override.
            store_raw: Optional override for retaining full redacted output.
                ``None`` uses the collector default.

        Returns:
            A :class:`CommandResult` describing the execution.

        Raises:
            ValueError: If *name* is not a registered command.
        """
        if name not in self._commands:
            raise ValueError(
                f"unknown command {name!r}; only preconfigured commands may run "
                f"(known: {sorted(self._commands)})"
            )
        spec = self._commands[name]
        argv = list(spec.argv)
        run_cwd = cwd if cwd is not None else spec.cwd
        run_timeout = (
            timeout
            if timeout is not None
            else (spec.timeout if spec.timeout is not None else self._default_timeout)
        )
        cap = (
            max_output_bytes
            if max_output_bytes is not None
            else (
                spec.max_output_bytes
                if spec.max_output_bytes is not None
                else self._default_max_output_bytes
            )
        )
        retain_raw = self._store_raw if store_raw is None else store_raw

        exit_code: int | None = None
        timed_out = False
        stdout_bytes = b""
        stderr_bytes = b""
        error: str | None = None

        start = time.perf_counter()
        try:
            completed = subprocess.run(
                argv,
                cwd=run_cwd,
                capture_output=True,
                timeout=run_timeout,
                env=spec.env,
            )
            stdout_bytes = completed.stdout or b""
            stderr_bytes = completed.stderr or b""
            exit_code = completed.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout_bytes = exc.stdout or b""
            stderr_bytes = exc.stderr or b""
            exit_code = None
        except FileNotFoundError as exc:
            error = f"command not found: {exc}"
            exit_code = None
        except OSError as exc:
            error = f"command failed to start: {exc}"
            exit_code = None
        except Exception as exc:  # pragma: no cover - defensive, surfaced
            error = f"collector error: {exc!r}"
            exit_code = None
        runtime_seconds = time.perf_counter() - start

        return self._build_result(
            name=name,
            argv=argv,
            cwd=run_cwd,
            exit_code=exit_code,
            runtime_seconds=runtime_seconds,
            timed_out=timed_out,
            stdout_bytes=stdout_bytes,
            stderr_bytes=stderr_bytes,
            cap=cap,
            retain_raw=retain_raw,
            error=error,
        )


    def _build_result(
        self,
        *,
        name: str,
        argv: list[str],
        cwd: str | None,
        exit_code: int | None,
        runtime_seconds: float,
        timed_out: bool,
        stdout_bytes: bytes,
        stderr_bytes: bytes,
        cap: int | None,
        retain_raw: bool,
        error: str | None,
    ) -> CommandResult:
        """Redact, hash, truncate, and assemble a :class:`CommandResult`.

        Redaction runs *before* hashing, summarising, and raw retention, so a
        secret in captured output can never reach any downstream store (item 13).
        The hash covers the *full* redacted output (pre-truncation) so the size
        cap never weakens tamper detection.

        Args:
            name: Registered command name.
            argv: Argument vector executed.
            cwd: Working directory.
            exit_code: Process exit code (``None`` on timeout/crash).
            runtime_seconds: Wall-clock runtime.
            timed_out: Whether the command timed out.
            stdout_bytes: Captured raw stdout.
            stderr_bytes: Captured raw stderr.
            cap: Summary byte cap.
            retain_raw: Whether to keep full redacted output.
            error: Collector error message, if any.

        Returns:
            The assembled :class:`CommandResult`.
        """
        stdout_text = self._redactor(stdout_bytes.decode("utf-8", errors="replace"))
        stderr_text = self._redactor(stderr_bytes.decode("utf-8", errors="replace"))
        stdout_hash = sha256_hex(stdout_text.encode("utf-8", errors="replace"))
        stderr_hash = sha256_hex(stderr_text.encode("utf-8", errors="replace"))
        stdout_summary, stdout_truncated = _truncate_text(stdout_text, cap)
        stderr_summary, stderr_truncated = _truncate_text(stderr_text, cap)
        return CommandResult(
            name=name,
            argv=argv,
            cwd=cwd,
            exit_code=exit_code,
            runtime_seconds=runtime_seconds,
            timed_out=timed_out,
            stdout_hash=stdout_hash,
            stderr_hash=stderr_hash,
            stdout_summary=stdout_summary,
            stderr_summary=stderr_summary,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            stdout_raw=stdout_text if retain_raw else None,
            stderr_raw=stderr_text if retain_raw else None,
            error=error,
        )




# ---------------------------------------------------------------------------
# Official collectors
# ---------------------------------------------------------------------------


class PytestCollector:
    """Runs a configured pytest command and emits VERIFIED test evidence.

    Wraps a :class:`CommandCollector` whose registry contains a pytest command.
    It executes the command itself (BOUND, not the agent), parses the captured
    summary via the existing :func:`bound.collectors.parse_pytest_summary`, and
    emits :class:`CheckEvidence` with VERIFIED provenance. A pass requires **both**
    exit code 0 **and** at least one executed test (item 8: a zero-test run is no
    proven pass). Timeout, crash, and parse failure never yield VERIFIED pass.
    """

    COLLECTOR_NAME = "bound.pytest"

    def __init__(
        self,
        runner: CommandCollector,
        *,
        command_name: str = "pytest",
        check_id: str = "tests-pass",
    ) -> None:
        """Bind to a :class:`CommandCollector` and a registered pytest command.

        Args:
            runner: A :class:`CommandCollector` whose registry contains
                *command_name* (a ``pytest -q ...`` invocation).
            command_name: The registered command name to run.
            check_id: The contract check id to stamp on the evidence.
        """
        self._runner = runner
        self._command_name = command_name
        self._check_id = check_id

    def collect(
        self,
        *,
        cwd: str | None = None,
        timeout: float | None = None,
        max_output_bytes: int | None = None,
    ) -> CheckEvidence:
        """Run pytest and return VERIFIED evidence of the test outcome.

        Args:
            cwd: Optional working-directory override.
            timeout: Optional wall-clock timeout override.
            max_output_bytes: Optional summary byte-cap override.

        Returns:
            :class:`CheckEvidence` with VERIFIED provenance when pytest ran to
            completion; INVALID/MISSING when it timed out, crashed, or could not
            be parsed. ``passed`` is ``True`` only on exit 0 with >0 executed tests.
        """
        try:
            result = self._runner.run(
                self._command_name,
                cwd=cwd,
                timeout=timeout,
                max_output_bytes=max_output_bytes,
                store_raw=True,
            )
        except Exception as exc:  # pragma: no cover - defensive
            return _bound_check(
                self._check_id,
                passed=None,
                provenance=EvidenceProvenance.MISSING,
                collector=self.COLLECTOR_NAME,
                status=EvidenceStatus.INVALID,
                details=f"collector crashed before execution: {exc!r}",
                source=self._command_name,
            )
        return self._evidence_from_result(result)


    def _evidence_from_result(self, result: CommandResult) -> CheckEvidence:
        """Turn a pytest :class:`CommandResult` into :class:`CheckEvidence`.

        Encodes the item-8 fail-safe contract: timeout/crash/no-exit-code and
        parse failure yield INVALID with MISSING provenance (never a pass); a
        zero-test run yields ``passed=False`` with UNVERIFIED status (no proven
        pass); only exit 0 with >0 executed tests yields VERIFIED pass.

        Args:
            result: The pytest execution result.

        Returns:
            The stamped :class:`CheckEvidence`.
        """
        source = _command_source(result.argv)
        hashes = f"stdout={result.stdout_hash} stderr={result.stderr_hash}"

        # Item 8: timeout / crash / no exit code -> never a verified pass.
        if result.error is not None or result.timed_out or result.exit_code is None:
            reason = result.error or (
                "timeout" if result.timed_out else "no exit code"
            )
            return _bound_check(
                self._check_id,
                passed=None,
                provenance=EvidenceProvenance.MISSING,
                collector=self.COLLECTOR_NAME,
                status=EvidenceStatus.INVALID,
                details=f"pytest did not complete ({reason}); {hashes}",
                source=source,
                artifact_hash=result.stdout_hash,
            )

        text = (
            result.stdout_raw
            if result.stdout_raw is not None
            else result.stdout_summary
        )
        try:
            summary = parse_pytest_summary(text)
        except Exception as exc:
            return _bound_check(
                self._check_id,
                passed=None,
                provenance=EvidenceProvenance.MISSING,
                collector=self.COLLECTOR_NAME,
                status=EvidenceStatus.INVALID,
                details=f"summary parse failure: {exc!r}; {hashes}",
                source=source,
                artifact_hash=result.stdout_hash,
            )

        executed = summary.executed_test_count
        details = (
            f"pytest exit={result.exit_code}; {summary.passed} passed, "
            f"{summary.failed} failed, {summary.errors} errors, "
            f"{summary.skipped} skipped, {executed} executed; {hashes}"
        )

        # Item 8: zero tests found -> no proven pass (passed=False, UNVERIFIED).
        if executed == 0:
            return _bound_check(
                self._check_id,
                passed=False,
                provenance=EvidenceProvenance.VERIFIED,
                collector=self.COLLECTOR_NAME,
                status=EvidenceStatus.UNVERIFIED,
                details=f"{details}; zero tests executed — no proven pass",
                source=source,
                artifact_hash=result.stdout_hash,
            )

        passed = result.exit_code == 0
        status = None if passed else EvidenceStatus.FAILED
        return _bound_check(
            self._check_id,
            passed=passed,
            provenance=EvidenceProvenance.VERIFIED,
            collector=self.COLLECTOR_NAME,
            status=status,
            details=details,
            source=source,
            artifact_hash=result.stdout_hash,
        )




class JUnitCollector:
    """Parses a trusted JUnit XML artefact directly and emits VERIFIED evidence.

    Unlike :class:`PytestCollector`, this collector does not re-run tests; it
    reads a JUnit XML file *directly* (a trusted artefact), hashes it, checks its
    freshness, and parses the testsuite totals. VERIFIED provenance comes from
    BOUND hashing and parsing the artefact itself. A pass requires tests>0 with
    no failures/errors. A stale or oversized artefact, a missing file, or a parse
    failure yields INVALID/MISSING — never a pass (item 8).
    """

    COLLECTOR_NAME = "bound.junit"

    def __init__(
        self,
        *,
        check_id: str = "tests-pass",
        max_file_bytes: int = 1_048_576,
        max_age_seconds: float | None = None,
    ) -> None:
        """Configure artefact limits and the check id.

        Args:
            check_id: Contract check id stamped on the evidence.
            max_file_bytes: Hard cap on the artefact size; a larger file is
                rejected as INVALID (item 13 artefact size limit).
            max_age_seconds: Optional freshness window; an artefact whose mtime
                is older than this is INVALID (stale, no current verification).
                ``None`` disables the freshness check.
        """
        self._check_id = check_id
        self._max_file_bytes = max_file_bytes
        self._max_age_seconds = max_age_seconds

    def collect(
        self, path: str | Path, *, now: datetime | None = None
    ) -> CheckEvidence:
        """Hash, freshness-check, and parse the JUnit XML at *path*.

        Args:
            path: Path to the JUnit XML artefact.
            now: Optional "current" timestamp for the freshness check (UTC,
                timezone-aware). Defaults to :func:`_now_utc`; tests inject a
                fixed time.

        Returns:
            VERIFIED :class:`CheckEvidence` when the artefact parsed and tests
            ran; INVALID/MISSING when the file is missing, oversized, stale, or
            malformed; ``passed`` is ``True`` only on tests>0 with no
            failures/errors.
        """
        artefact = Path(path)
        source = str(artefact)
        now_utc = now if now is not None else _now_utc()

        if not artefact.exists():
            return _bound_check(
                self._check_id,
                passed=None,
                provenance=EvidenceProvenance.MISSING,
                collector=self.COLLECTOR_NAME,
                status=EvidenceStatus.MISSING,
                details=f"junit artefact not found: {source}",
                source=source,
            )

        try:
            raw = artefact.read_bytes()
        except OSError as exc:
            return _bound_check(
                self._check_id,
                passed=None,
                provenance=EvidenceProvenance.MISSING,
                collector=self.COLLECTOR_NAME,
                status=EvidenceStatus.INVALID,
                details=f"junit artefact unreadable: {exc!r}",
                source=source,
            )

        if len(raw) > self._max_file_bytes:
            return _bound_check(
                self._check_id,
                passed=None,
                provenance=EvidenceProvenance.MISSING,
                collector=self.COLLECTOR_NAME,
                status=EvidenceStatus.INVALID,
                details=(
                    f"junit artefact exceeds size limit: {len(raw)} > "
                    f"{self._max_file_bytes} bytes"
                ),
                source=source,
            )

        artifact_hash = sha256_hex(raw)
        return self._finish(
            artefact=artefact,
            source=source,
            raw=raw,
            artifact_hash=artifact_hash,
            now_utc=now_utc,
        )


    def _finish(
        self,
        *,
        artefact: Path,
        source: str,
        raw: bytes,
        artifact_hash: str,
        now_utc: datetime,
    ) -> CheckEvidence:
        """Apply the freshness check and parse the JUnit XML body.

        Args:
            artefact: The artefact path.
            source: String form of the path for the ``source`` field.
            raw: The artefact bytes (already size-checked).
            artifact_hash: ``sha256:`` digest of *raw*.
            now_utc: Reference timestamp for the freshness check.

        Returns:
            The stamped :class:`CheckEvidence`.
        """
        # Item 8: stale artefact -> no current verification.
        if self._max_age_seconds is not None:
            try:
                mtime = datetime.fromtimestamp(
                    artefact.stat().st_mtime, tz=UTC
                )
            except OSError as exc:
                return _bound_check(
                    self._check_id,
                    passed=None,
                    provenance=EvidenceProvenance.MISSING,
                    collector=self.COLLECTOR_NAME,
                    status=EvidenceStatus.INVALID,
                    details=f"junit artefact mtime unavailable: {exc!r}",
                    source=source,
                    artifact_hash=artifact_hash,
                )
            age = (now_utc - mtime).total_seconds()
            if age > self._max_age_seconds:
                return _bound_check(
                    self._check_id,
                    passed=None,
                    provenance=EvidenceProvenance.MISSING,
                    collector=self.COLLECTOR_NAME,
                    status=EvidenceStatus.INVALID,
                    details=(
                        f"junit artefact stale: age={age:.0f}s > "
                        f"{self._max_age_seconds:.0f}s"
                    ),
                    source=source,
                    artifact_hash=artifact_hash,
                    raw_artifact_ref=source,
                )

        try:
            root = ElementTree.fromstring(raw)
        except ElementTree.ParseError as exc:
            return _bound_check(
                self._check_id,
                passed=None,
                provenance=EvidenceProvenance.MISSING,
                collector=self.COLLECTOR_NAME,
                status=EvidenceStatus.INVALID,
                details=f"junit xml parse failure: {exc!r}",
                source=source,
                artifact_hash=artifact_hash,
            )

        suites = (
            root.findall("testsuite")
            if root.tag == "testsuites"
            else ([root] if root.tag == "testsuite" else [])
        )

        def _attr_int(node: object, name: str) -> int:
            if not isinstance(node, ElementTree.Element):
                return 0
            value = node.get(name, "0")
            try:
                return int(value)
            except (TypeError, ValueError):
                return 0

        tests = sum(_attr_int(s, "tests") for s in suites)
        failures = sum(_attr_int(s, "failures") for s in suites)
        errors = sum(_attr_int(s, "errors") for s in suites)
        skipped = sum(_attr_int(s, "skipped") for s in suites)

        details = (
            f"junit {source}; tests={tests}, failures={failures}, "
            f"errors={errors}, skipped={skipped}; {artifact_hash}"
        )

        if tests == 0:
            return _bound_check(
                self._check_id,
                passed=False,
                provenance=EvidenceProvenance.VERIFIED,
                collector=self.COLLECTOR_NAME,
                status=EvidenceStatus.UNVERIFIED,
                details=f"{details}; zero tests — no proven pass",
                source=source,
                artifact_hash=artifact_hash,
                raw_artifact_ref=source,
            )

        passed = failures == 0 and errors == 0
        status = None if passed else EvidenceStatus.FAILED
        return _bound_check(
            self._check_id,
            passed=passed,
            provenance=EvidenceProvenance.VERIFIED,
            collector=self.COLLECTOR_NAME,
            status=status,
            details=details,
            source=source,
            artifact_hash=artifact_hash,
            raw_artifact_ref=source,
        )




class GitCollector:
    """Runs ``git status --porcelain`` and emits VERIFIED clean-workspace evidence.

    Wraps a :class:`CommandCollector` whose registry contains a
    ``git status --porcelain`` command. It executes git itself, parses the output
    with the existing :func:`bound.collectors.parse_git_status_porcelain`, and
    emits VERIFIED evidence that the working tree has no unexpected changes. A
    failed or timed-out git command yields INVALID/MISSING — an empty path list
    from a command that could not run is *unavailable*, never *clean* (item 8),
    mirroring :meth:`bound.collectors.GitInspection.is_clean_proven`.
    """

    COLLECTOR_NAME = "bound.git"

    def __init__(
        self,
        runner: CommandCollector,
        *,
        command_name: str = "git-status",
        check_id: str = "no-unexpected-files",
        allowed_prefixes: tuple[str, ...] = (),
    ) -> None:
        """Bind to a :class:`CommandCollector` and a registered git-status command.

        Args:
            runner: A :class:`CommandCollector` whose registry contains
                *command_name* (a ``git status --porcelain`` invocation).
            command_name: The registered command name to run.
            check_id: Contract check id stamped on the evidence.
            allowed_prefixes: Path prefixes the contract permits to change; any
                changed path outside this set is unexpected risk evidence.
        """
        self._runner = runner
        self._command_name = command_name
        self._check_id = check_id
        self._allowed_prefixes = allowed_prefixes

    def collect(
        self,
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> CheckEvidence:
        """Run git status and return VERIFIED clean-workspace evidence.

        Args:
            cwd: Optional working-directory override (the repo to inspect).
            timeout: Optional wall-clock timeout override.

        Returns:
            VERIFIED :class:`CheckEvidence` when git ran and the tree is clean of
            unexpected paths; FAILED when unexpected paths exist; INVALID/MISSING
            when git timed out, crashed, or exited non-zero.
        """
        try:
            result = self._runner.run(
                self._command_name,
                cwd=cwd,
                timeout=timeout,
                store_raw=True,
            )
        except Exception as exc:  # pragma: no cover - defensive
            return _bound_check(
                self._check_id,
                passed=None,
                provenance=EvidenceProvenance.MISSING,
                collector=self.COLLECTOR_NAME,
                status=EvidenceStatus.INVALID,
                details=f"collector crashed before execution: {exc!r}",
                source=self._command_name,
            )
        return self._evidence_from_result(result)


    def _evidence_from_result(self, result: CommandResult) -> CheckEvidence:
        """Turn a git-status :class:`CommandResult` into :class:`CheckEvidence`.

        Encodes the item-8 fail-safe contract: timeout/crash/no-exit-code yields
        INVALID with MISSING provenance (never a clean pass); a non-zero git exit
        yields INVALID (empty path list is untrustworthy, not clean); only a
        successful git run with no unexpected paths yields VERIFIED clean.

        Args:
            result: The git-status execution result.

        Returns:
            The stamped :class:`CheckEvidence`.
        """
        source = _command_source(result.argv)
        hashes = f"stdout={result.stdout_hash} stderr={result.stderr_hash}"

        # Item 8: timeout / crash / no exit code -> never a proven clean tree.
        if result.error is not None or result.timed_out or result.exit_code is None:
            reason = result.error or (
                "timeout" if result.timed_out else "no exit code"
            )
            return _bound_check(
                self._check_id,
                passed=None,
                provenance=EvidenceProvenance.MISSING,
                collector=self.COLLECTOR_NAME,
                status=EvidenceStatus.INVALID,
                details=f"git did not complete ({reason}); {hashes}",
                source=source,
                artifact_hash=result.stdout_hash,
            )

        # Item 8: failed git command -> empty path list is untrustworthy, not clean.
        if result.exit_code != 0:
            inspection = GitInspection.command_failed()
            return _bound_check(
                self._check_id,
                passed=False,
                provenance=EvidenceProvenance.MISSING,
                collector=self.COLLECTOR_NAME,
                status=EvidenceStatus.INVALID,
                details=(
                    f"git status exited {result.exit_code}; "
                    f"{inspection.changed_paths!r} untrustworthy; {hashes}"
                ),
                source=source,
                artifact_hash=result.stdout_hash,
            )

        text = (
            result.stdout_raw
            if result.stdout_raw is not None
            else result.stdout_summary
        )
        inspection = parse_git_status_porcelain(text, self._allowed_prefixes)
        passed = inspection.is_clean_proven()
        status = None if passed else EvidenceStatus.FAILED
        details = (
            f"git status exit={result.exit_code}; "
            f"changed={inspection.changed_paths!r}, "
            f"unexpected={inspection.unexpected_paths!r}; {hashes}"
        )
        return _bound_check(
            self._check_id,
            passed=passed,
            provenance=EvidenceProvenance.VERIFIED,
            collector=self.COLLECTOR_NAME,
            status=status,
            details=details,
            source=source,
            artifact_hash=result.stdout_hash,
        )




class BudgetMetrics(BaseModel):
    """Telemetry metrics paired with trust provenance (item 2 / item 7).

    Each field is an :class:`~bound.evidence.EvidenceMetric`, so a *measured*
    value (provenance OBSERVED) is always distinguishable from an *unmeasured*
    one (provenance MISSING, ``value is None``). ``None`` is missing, never a
    silent zero — :class:`BudgetCollector` never fabricates a measured zero.

    Attributes:
        token_usage: Token usage count, or MISSING.
        runtime_seconds: Wall-clock runtime, or MISSING.
        tool_call_count: Tool-call count, or MISSING.
        retry_count: Retry count, or MISSING.
    """

    model_config = ConfigDict(extra="forbid")

    token_usage: EvidenceMetric
    runtime_seconds: EvidenceMetric
    tool_call_count: EvidenceMetric
    retry_count: EvidenceMetric


class BudgetCollector:
    """Collects token/runtime/tool-call telemetry as OBSERVED :class:`EvidenceMetric`.

    A This collector does not execute a command; it wraps telemetry values
    the harness *directly observed* with the v0.7 missing-vs-zero discipline: a
    measured value is OBSERVED, an absent signal is MISSING (``value is None``,
    never a silent ``0``). Agent self-report is never routed through this
    collector — only harness-observed telemetry is.

    Attributes:
        COLLECTOR_NAME: ``"bound.budget"``.
    """

    COLLECTOR_NAME = "bound.budget"

    def __init__(self, *, source: str = "harness.telemetry") -> None:
        """Configure the default source label for emitted metrics.

        Args:
            source: Free-form source string stamped on each metric.
        """
        self._source = source

    def metrics(
        self,
        *,
        token_usage: int | float | bool | None = None,
        runtime_seconds: int | float | bool | None = None,
        tool_call_count: int | float | bool | None = None,
        retry_count: int | float | bool | None = None,
        source: str | None = None,
    ) -> BudgetMetrics:
        """Build provenance-stamped :class:`EvidenceMetric` telemetry.

        Each argument is the *observed* scalar, or ``None`` when that signal was
        not instrumented. ``None`` becomes MISSING (never ``0``); a real value
        becomes OBSERVED.

        Args:
            token_usage: Observed token usage, or ``None``.
            runtime_seconds: Observed runtime, or ``None``.
            tool_call_count: Observed tool-call count, or ``None``.
            retry_count: Observed retry count, or ``None``.
            source: Per-call source override.

        Returns:
            A :class:`BudgetMetrics` of provenance-stamped metrics.
        """
        src = source if source is not None else self._source

        def _metric(value: int | float | bool | None) -> EvidenceMetric:
            return EvidenceMetric(
                value=value,
                provenance=(
                    EvidenceProvenance.OBSERVED
                    if value is not None
                    else EvidenceProvenance.MISSING
                ),
                source=src,
                collector=self.COLLECTOR_NAME,
            )

        return BudgetMetrics(
            token_usage=_metric(token_usage),
            runtime_seconds=_metric(runtime_seconds),
            tool_call_count=_metric(tool_call_count),
            retry_count=_metric(retry_count),
        )




class ProcessRuntimeCollector:
    """Wall-clock runtime + exit-code evidence from a :class:`CommandResult`.

    A Turns a :class:`CommandResult` (from :class:`CommandCollector`) into
    an OBSERVED runtime :class:`EvidenceMetric` and a VERIFIED exit-code
    :class:`CheckEvidence`. A timeout (``exit_code is None``) yields INVALID
    evidence, never a pass (item 8).
    """

    COLLECTOR_NAME = "bound.process"

    def __init__(self, *, check_id: str = "process-exit-zero") -> None:
        """Configure the exit-code check id.

        Args:
            check_id: Contract check id stamped on the exit-code evidence.
        """
        self._check_id = check_id

    def collect(
        self,
        result: CommandResult,
        *,
        check_id: str | None = None,
    ) -> CheckEvidence:
        """Build VERIFIED exit-code evidence from *result*.

        Args:
            result: A :class:`CommandResult` produced by :class:`CommandCollector`.
            check_id: Optional per-call check-id override.

        Returns:
            VERIFIED :class:`CheckEvidence` (``passed`` = exit 0) when the process
            exited; INVALID/MISSING when it timed out or crashed.
        """
        cid = check_id if check_id is not None else self._check_id
        source = _command_source(result.argv)
        hashes = f"stdout={result.stdout_hash}"

        if result.exit_code is None:
            reason = result.error or (
                "timeout" if result.timed_out else "no exit code"
            )
            return _bound_check(
                cid,
                passed=None,
                provenance=EvidenceProvenance.MISSING,
                collector=self.COLLECTOR_NAME,
                status=EvidenceStatus.INVALID,
                details=(
                    f"process did not complete ({reason}); "
                    f"runtime={result.runtime_seconds:.3f}s; {hashes}"
                ),
                source=source,
                artifact_hash=result.stdout_hash,
            )

        passed = result.exit_code == 0
        status = None if passed else EvidenceStatus.FAILED
        return _bound_check(
            cid,
            passed=passed,
            provenance=EvidenceProvenance.VERIFIED,
            collector=self.COLLECTOR_NAME,
            status=status,
            details=(
                f"process exit={result.exit_code}; "
                f"runtime={result.runtime_seconds:.3f}s; {hashes}"
            ),
            source=source,
            artifact_hash=result.stdout_hash,
        )

    def runtime_metric(
        self,
        result: CommandResult,
        *,
        source: str | None = None,
    ) -> EvidenceMetric:
        """Build an OBSERVED runtime :class:`EvidenceMetric` from *result*.

        Runtime is always directly measured by :class:`CommandCollector`, so it is
        OBSERVED even on timeout (we measured how long we waited). Contrast with
        exit code, which is MISSING/INVALID when the process did not complete.

        Args:
            result: A :class:`CommandResult`.
            source: Optional source override.

        Returns:
            An OBSERVED :class:`EvidenceMetric` carrying the wall-clock runtime.
        """
        return EvidenceMetric(
            value=result.runtime_seconds,
            provenance=EvidenceProvenance.OBSERVED,
            source=source if source is not None else self.COLLECTOR_NAME,
            collector=self.COLLECTOR_NAME,
        )

