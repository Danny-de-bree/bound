"""Display constants and helpers shared between CLI and UI layers.

Colour maps, provenance-strength rankings, and formatting utilities live
here so that :mod:`bound.cli` and :mod:`bound.ui` share one copy.
"""

from __future__ import annotations

from datetime import datetime

from bound.evidence import EvidenceProvenance, EvidenceStatus

__all__ = [
    "DECISION_COLORS",
    "INDEPENDENTLY_VERIFIED",
    "PROVENANCE_COLORS",
    "PROVENANCE_STRENGTH",
    "UNVERIFIED_PROVENANCE",
    "UNVERIFIED_STATUS",
    "fmt_dt",
    "html_escape",
    "provenance_label",
    "sv",
]

# ---------------------------------------------------------------------------
# CSS colour maps
# ---------------------------------------------------------------------------

#: CSS colour per evidence provenance class (used by HTML timeline/dashboard).
PROVENANCE_COLORS: dict[str, str] = {
    "verified": "#2e7d32",
    "observed": "#1976d2",
    "attested": "#6a1b9a",
    "evaluated": "#ef6c00",
    "claimed": "#c62828",
    "defaulted": "#8d6e63",
    "missing": "#9e9e9e",
    "unverified": "#9e9e9e",
}

#: CSS colour per BOUND decision (replan -> accept trajectory highlighted).
DECISION_COLORS: dict[str, str] = {
    "ACCEPT": "#2e7d32",
    "RETRY": "#ef6c00",
    "REPLAN": "#1565c0",
    "ROLLBACK": "#c62828",
}

# ---------------------------------------------------------------------------
# Provenance visibility (item 14)
# ---------------------------------------------------------------------------

#: Provenance ranked by trust strength (higher = more trustworthy). Used to
#: pick the strongest provenance backing a score and to decide what counts as
#: independently verified. OBSERVED/VERIFIED/ATTESTED are the only provenances
#: that count as *independent* — agent self-report (CLAIMED) never does.
PROVENANCE_STRENGTH: dict[EvidenceProvenance, int] = {
    EvidenceProvenance.VERIFIED: 60,
    EvidenceProvenance.OBSERVED: 50,
    EvidenceProvenance.ATTESTED: 40,
    EvidenceProvenance.EVALUATED: 30,
    EvidenceProvenance.CLAIMED: 20,
    EvidenceProvenance.DEFAULTED: 10,
    EvidenceProvenance.MISSING: 0,
}

#: Provenance that counts as *independently verified* — produced by a
#: BOUND-controlled collector or a trusted attestation, never agent
#: self-report. Drives the "Critical evidence coverage" metric.
INDEPENDENTLY_VERIFIED: frozenset[EvidenceProvenance] = frozenset(
    {EvidenceProvenance.OBSERVED, EvidenceProvenance.VERIFIED, EvidenceProvenance.ATTESTED},
)

#: Provenance that is *not* independently verified — selected by
#: ``bound inspect --only-unverified``.
UNVERIFIED_PROVENANCE: frozenset[EvidenceProvenance] = frozenset(
    {EvidenceProvenance.CLAIMED, EvidenceProvenance.DEFAULTED, EvidenceProvenance.MISSING},
)

#: Evidence statuses that mean the check could not be independently confirmed.
UNVERIFIED_STATUS: frozenset[EvidenceStatus] = frozenset(
    {EvidenceStatus.UNVERIFIED, EvidenceStatus.MISSING, EvidenceStatus.INVALID},
)

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def fmt_dt(dt: datetime | None) -> str:
    """Format a UTC datetime for human-readable CLI/UI output."""
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC") if dt else "-"


def html_escape(text: str) -> str:
    """Escape a string for safe inclusion in HTML text content."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def sv(value: object) -> str:
    """Return the string value of an enum member or a plain string.

    ``Decision`` / ``NextAction`` are ``Literal`` type aliases (plain strings),
    while provenance / status / assurance are ``StrEnum`` members, so a single
    helper normalises both to their lower/upper string value for rendering.
    """
    return value.value if hasattr(value, "value") else str(value)


def provenance_label(provenance: EvidenceProvenance | None) -> str:
    """Render a provenance value as an upper-case label, or ``-`` when absent."""
    if provenance is None:
        return "-"
    return provenance.value.upper()


def evidence_status_colors() -> dict[str, str]:
    """Return CSS colour per evidence status for badges."""
    return {
        "verified": "#2e7d32",
        "claimed": "#c62828",
        "missing": "#9e9e9e",
        "invalid": "#d32f2f",
        "stale": "#f57c00",
        "unverified": "#9e9e9e",
    }


def run_status_colors() -> dict[str, str]:
    """Return CSS colour per RunStatus."""
    return {
        "started": "#1565c0",
        "completed": "#2e7d32",
        "interrupted": "#f57c00",
        "failed": "#c62828",
    }


def assurance_colors() -> dict[str, str]:
    """Return CSS colour per DecisionAssurance level."""
    return {
        "full": "#2e7d32",
        "high": "#43a047",
        "moderate": "#ef6c00",
        "partial": "#f57c00",
        "low": "#d32f2f",
        "none": "#9e9e9e",
    }