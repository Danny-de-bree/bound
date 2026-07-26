"""``bound ui`` — local read-only BOUND dashboard (Sprint 1).

Builds on the existing ``bound inspect --html`` renderer from
:mod:`bound.cli` to serve a localhost dashboard that shows all local
runs, their decision lineage, and evidence provenance — no hosted
backend, no account, no external assets.
"""

from __future__ import annotations

import json
import logging
import sys
import time
import webbrowser
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from bound.cli import _RunAuditIndex
from bound.display import (
    DECISION_COLORS,
    INDEPENDENTLY_VERIFIED,
    PROVENANCE_COLORS,
    fmt_dt,
    html_escape,
    sv,
)
from bound.lineage_store import (
    LineageStore,
    RunLog,
    RunNotFound,
    RunSummary,
    get_default_store,
)

logger = logging.getLogger("bound.ui")

# =========================================================================
# No-emoji icon system (Section 17 of todo-ui.md)
# =========================================================================
# All inline SVG icons at 16x16 viewBox.  Every icon that was previously
# an emoji character or HTML entity now lives here so the UI stays
# platform-independent and platform-consistent.


def _icon(  # noqa: D417
    name: str,
    *,
    w: int = 16,
    h: int = 16,
) -> str:
    """Return the named inline SVG icon with optional size override.

    Args:
        name: Key in the ``ICONS`` dictionary.
        w: Rendered width in pixels.
        h: Rendered height in pixels.

    Returns:
        An ``<svg>`` string, or a visible placeholder when ``name`` is unknown.
    """
    svg = ICONS.get(name)
    if svg is None:
        return (
            f"<svg width='{w}' height='{h}' viewBox='0 0 16 16'>"
            "<rect width='16' height='16' rx='2' fill='#f85149'/>"
            "<text x='8' y='12' text-anchor='middle' font-size='10' fill='#fff'>?</text>"
            "</svg>"
        )
    if w == 16 and h == 16:
        return svg
    return svg.replace("width='16'", f"width='{w}'").replace("height='16'", f"height='{h}'")


ICONS: dict[str, str] = {
    # -- Decision icons --
    "decision_accept": (
        "<svg width='16' height='16' viewBox='0 0 16 16' xmlns='http://www.w3.org/2000/svg'>"
        "<path d='M13.78 4.22a.75.75 0 0 1 0 1.06l-7.25 7.25a.75.75 0 0 1-1.06 0L2.22 9.28"
        "a.75.75 0 0 1 1.06-1.06L6 10.94l6.72-6.72a.75.75 0 0 1 1.06 0z'"
        " fill='#3fb950'/></svg>"
    ),
    "decision_retry": (
        "<svg width='16' height='16' viewBox='0 0 16 16' xmlns='http://www.w3.org/2000/svg'>"
        "<path d='M1.5 8a6.5 6.5 0 0 1 11.7-3.57V3.5a.75.75 0 0 1 1.5 0v3a.75.75 0"
        " 0 1-.75.75h-3a.75.75 0 0 1 0-1.5h1.45A5 5 0 1 0 13 8a.75.75 0 0 1 1.5 0"
        " 6.5 6.5 0 0 1-13 0z' fill='#ef6c00'/></svg>"
    ),
    "decision_replan": (
        "<svg width='16' height='16' viewBox='0 0 16 16' xmlns='http://www.w3.org/2000/svg'>"
        "<path d='M11.5 3h-7A1.5 1.5 0 0 0 3 4.5v7A1.5 1.5 0 0 0 4.5 13h7a1.5 1.5 0"
        " 0 0 1.5-1.5v-7A1.5 1.5 0 0 0 11.5 3z' fill='none' stroke='#8b5cf6'"
        " stroke-width='1.2'/>"
        "<path d='M5 3.5h6M5 6.5h4M5 9.5h2' stroke='#8b5cf6' stroke-width='1'"
        " stroke-linecap='round'/></svg>"
    ),
    "decision_rollback": (
        "<svg width='16' height='16' viewBox='0 0 16 16' xmlns='http://www.w3.org/2000/svg'>"
        "<path d='M2.5 1.75a.75.75 0 0 0-1.5 0v3.5c0 .414.336.75.75.75h3.5a.75.75 0"
        " 0 0 0-1.5H3.06A5.502 5.502 0 0 1 13.5 8a5.5 5.5 0 0 1-8.577 4.533.75.75 0"
        " 0 0-.846-1.238A4.001 4.001 0 1 0 3.1 6H5.25a.75.75 0 0 0 0-1.5h-2.75z'"
        " fill='#f85149'/></svg>"
    ),
    # -- Status icons --
    "status_completed": (
        "<svg width='16' height='16' viewBox='0 0 16 16' xmlns='http://www.w3.org/2000/svg'>"
        "<circle cx='8' cy='8' r='6' fill='#2e7d32'/>"
        "<path d='M11.28 5.97a.75.75 0 0 1 0 1.06l-4.25 4.25a.75.75 0 0 1-1.06 0"
        "L4.22 9.53a.75.75 0 0 1 1.06-1.06L7 10.19l3.72-3.72a.75.75 0 0 1 1.06 0z'"
        " fill='#fff'/></svg>"
    ),
    "status_running": (
        "<svg width='16' height='16' viewBox='0 0 16 16' xmlns='http://www.w3.org/2000/svg'>"
        "<circle cx='8' cy='8' r='6' stroke='#30363d' stroke-width='2' fill='none'/>"
        "<path d='M8 2a6 6 0 0 1 6 6' stroke='#58a6ff' stroke-width='2' fill='none'"
        " stroke-linecap='round'/></svg>"
    ),
    "status_failed": (
        "<svg width='16' height='16' viewBox='0 0 16 16' xmlns='http://www.w3.org/2000/svg'>"
        "<circle cx='8' cy='8' r='6' fill='#c62828'/>"
        "<path d='M5.28 5.28a.75.75 0 0 1 1.06 0L8 6.94l1.66-1.66a.75.75 0 1 1"
        " 1.06 1.06L9.06 8l1.66 1.66a.75.75 0 1 1-1.06 1.06L8 9.06l-1.66 1.66"
        "a.75.75 0 0 1-1.06-1.06L6.94 8 5.28 6.34a.75.75 0 0 1 0-1.06z' fill='#fff'/></svg>"
    ),
    "status_interrupted": (
        "<svg width='16' height='16' viewBox='0 0 16 16' xmlns='http://www.w3.org/2000/svg'>"
        "<circle cx='8' cy='8' r='6' fill='#f57c00'/>"
        "<text x='8' y='12' text-anchor='middle' font-size='9' font-weight='700'"
        " fill='#fff'>!</text></svg>"
    ),
    # -- Evidence check icons --
    "evidence_passed": (
        "<svg width='16' height='16' viewBox='0 0 16 16' xmlns='http://www.w3.org/2000/svg'>"
        "<path d='M13.78 4.22a.75.75 0 0 1 0 1.06l-7.25 7.25a.75.75 0 0 1-1.06 0"
        "L2.22 9.28a.75.75 0 0 1 1.06-1.06L6 10.94l6.72-6.72a.75.75 0 0 1 1.06 0z'"
        " fill='#3fb950'/></svg>"
    ),
    "evidence_failed": (
        "<svg width='16' height='16' viewBox='0 0 16 16' xmlns='http://www.w3.org/2000/svg'>"
        "<path d='M3.72 3.72a.75.75 0 0 1 1.06 0L8 6.94l3.22-3.22a.75.75 0 1 1"
        " 1.06 1.06L9.06 8l3.22 3.22a.75.75 0 1 1-1.06 1.06L8 9.06l-3.22 3.22"
        "a.75.75 0 0 1-1.06-1.06L6.94 8 3.72 4.78a.75.75 0 0 1 0-1.06z'"
        " fill='#f85149'/></svg>"
    ),
    "evidence_missing": (
        "<svg width='16' height='16' viewBox='0 0 16 16' xmlns='http://www.w3.org/2000/svg'>"
        "<path d='M2.75 8a.75.75 0 0 1 .75-.75h9a.75.75 0 0 1 0 1.5h-9A.75.75 0 0 1 2.75 8z'"
        " fill='#9e9e9e'/></svg>"
    ),
    # -- UI chrome icons --
    "run": (
        "<svg width='16' height='16' viewBox='0 0 16 16' xmlns='http://www.w3.org/2000/svg'>"
        "<path d='M2 2.5A1.5 1.5 0 0 1 3.5 1h9A1.5 1.5 0 0 1 14 2.5v11a1.5 1.5 0"
        " 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 2 13.5v-11zM3.5 2a.5.5 0 0 0-.5.5v11a.5.5 0"
        " 0 0 .5.5h9a.5.5 0 0 0 .5-.5v-11a.5.5 0 0 0-.5-.5h-9z' fill='#8b949e'/>"
        "<path d='M5 4.5h6M5 7h6M5 9.5h4' stroke='#8b949e' stroke-width='1'"
        " stroke-linecap='round'/></svg>"
    ),
    "step": (
        "<svg width='16' height='16' viewBox='0 0 16 16' xmlns='http://www.w3.org/2000/svg'>"
        "<path d='M3 2.75a.75.75 0 0 1 1.5 0v10.5a.75.75 0 0 1-1.5 0V2.75zM7.25 2.75"
        "a.75.75 0 0 1 1.5 0v6.5a.75.75 0 0 1-1.5 0v-6.5zM11.5 2.75a.75.75 0 0 1"
        " 1.5 0v8.5a.75.75 0 0 1-1.5 0v-8.5z' fill='#8b949e'/></svg>"
    ),
    "warning": (
        "<svg width='16' height='16' viewBox='0 0 16 16' xmlns='http://www.w3.org/2000/svg'>"
        "<path d='M8.22 1.754a.25.25 0 0 0-.44 0L1.698 13.132a.25.25 0 0 0 .22.368"
        "h12.164a.25.25 0 0 0 .22-.368L8.22 1.754z' fill='#d29922'/>"
        "<text x='8' y='11.5' text-anchor='middle' font-size='9' font-weight='700'"
        " fill='#0d1117'>!</text></svg>"
    ),
    "checkpoint": (
        "<svg width='16' height='16' viewBox='0 0 16 16' xmlns='http://www.w3.org/2000/svg'>"
        "<path d='M9.5 3a.5.5 0 0 1 .5.5v9a.5.5 0 0 1-1 0v-9a.5.5 0 0 1 .5-.5z'"
        " fill='#3fb950'/>"
        "<path d='M3 3h9M3 5h9M3 13h9M3 11h9' stroke='#8b949e' stroke-width='1'"
        " stroke-linecap='round'/></svg>"
    ),
    "artifact": (
        "<svg width='16' height='16' viewBox='0 0 16 16' xmlns='http://www.w3.org/2000/svg'>"
        "<path d='M8 1l6 2.5v9L8 15l-6-2.5v-9L8 1z' fill='none' stroke='#8b949e'"
        " stroke-width='1.2'/>"
        "<circle cx='8' cy='8' r='1.5' fill='#8b949e'/></svg>"
    ),
    # -- Navigation icons --
    "collapse_down": (
        "<svg width='16' height='16' viewBox='0 0 16 16' xmlns='http://www.w3.org/2000/svg'>"
        "<path d='M4.22 6.22a.75.75 0 0 1 1.06 0L8 8.94l2.72-2.72a.75.75 0 1 1"
        " 1.06 1.06l-3.25 3.25a.75.75 0 0 1-1.06 0L4.22 7.28a.75.75 0 0 1 0-1.06z'"
        " fill='#8b949e'/></svg>"
    ),
    "back_arrow": (
        "<svg width='16' height='16' viewBox='0 0 16 16' xmlns='http://www.w3.org/2000/svg'>"
        "<path d='M7.28 4.22a.75.75 0 0 1 0 1.06L4.56 8l2.72 2.72a.75.75 0 1 1"
        "-1.06 1.06l-3.25-3.25a.75.75 0 0 1 0-1.06l3.25-3.25a.75.75 0 0 1 1.06 0z'"
        " fill='#8b949e'/>"
        "<path d='M3.75 8.75h9.5a.75.75 0 0 0 0-1.5h-9.5a.75.75 0 0 0 0 1.5z'"
        " fill='#8b949e'/></svg>"
    ),
    "jump_down": (
        "<svg width='16' height='16' viewBox='0 0 16 16' xmlns='http://www.w3.org/2000/svg'>"
        "<path d='M8 2.75a.75.75 0 0 1 .75.75v7.94l1.97-1.97a.75.75 0 1 1 1.06 1.06"
        "l-3.25 3.25a.75.75 0 0 1-1.06 0L4.22 10.53a.75.75 0 0 1 1.06-1.06l1.97 1.97"
        "V3.5A.75.75 0 0 1 8 2.75z' fill='#8b949e'/></svg>"
    ),
    # -- Brand / decorative --
    "bound": (
        "<svg width='18' height='18' viewBox='0 0 16 16' fill='none'"
        " xmlns='http://www.w3.org/2000/svg'>"
        "<circle cx='5' cy='4' r='2.5' stroke='#58a6ff' stroke-width='1.5'/>"
        "<circle cx='11' cy='12' r='2.5' stroke='#8b5cf6' stroke-width='1.5'/>"
        "<path d='M7 5.5L9.5 10.5' stroke='#58a6ff' stroke-width='1.5'"
        " stroke-linecap='round'/></svg>"
    ),
    "empty_cube": (
        "<svg width='48' height='48' viewBox='0 0 16 16' xmlns='http://www.w3.org/2000/svg'>"
        "<path d='M8.878.392a1.75 1.75 0 0 0-1.756 0l-5.25 3.045A1.75 1.75 0 0 0 1"
        " 4.951v6.098c0 .624.332 1.2.872 1.514l5.25 3.045a1.75 1.75 0 0 0 1.756 0"
        "l5.25-3.045c.54-.313.872-.89.872-1.514V4.951c0-.624-.332-1.2-.872-1.514"
        "L8.878.392zM7.875 1.69l5.063 2.936L8 7.596 2.938 4.739 7.875 1.69z"
        "M2.5 5.912v5.044l4.75 2.756V8.668L2.5 5.912zm6.25 7.8 4.75-2.756V5.912"
        "L8.75 8.668v5.044z' fill='#484f58'/></svg>"
    ),
    "live_dot": (
        "<svg width='8' height='8' viewBox='0 0 8 8' xmlns='http://www.w3.org/2000/svg'>"
        "<circle cx='4' cy='4' r='4' fill='#3fb950'/></svg>"
    ),
    # -- Replay / timeline icons --
    "clock": (
        "<svg width='16' height='16' viewBox='0 0 16 16' xmlns='http://www.w3.org/2000/svg'>"
        "<circle cx='8' cy='8' r='6.5' stroke='#8b949e' stroke-width='1.5' fill='none'/>"
        "<path d='M8 4.5V8h3' stroke='#8b949e' stroke-width='1.5' stroke-linecap='round'/>"
        "</svg>"
    ),
    # -- Evidence / collector icons --
    "shield": (
        "<svg width='16' height='16' viewBox='0 0 16 16' xmlns='http://www.w3.org/2000/svg'>"
        "<path d='M8 1.5l6 2.5v4c0 3.5-2.5 6-6 8-3.5-2-6-4.5-6-8V4l6-2.5z'"
        " stroke='#8b949e' stroke-width='1.2' fill='none'/></svg>"
    ),
    # -- Replan diff icons --
    "diff_inserted": (
        "<svg width='14' height='14' viewBox='0 0 16 16' xmlns='http://www.w3.org/2000/svg'>"
        "<circle cx='8' cy='8' r='7' fill='#2e7d32'/>"
        "<path d='M8 4.5a.75.75 0 0 1 .75.75v2h2a.75.75 0 0 1 0 1.5h-2v2a.75.75 0"
        " 0 1-1.5 0v-2h-2a.75.75 0 0 1 0-1.5h2v-2A.75.75 0 0 1 8 4.5z' fill='#fff'/>"
        "</svg>"
    ),
    "diff_removed": (
        "<svg width='14' height='14' viewBox='0 0 16 16' xmlns='http://www.w3.org/2000/svg'>"
        "<circle cx='8' cy='8' r='7' fill='#c62828'/>"
        "<path d='M4.25 8.75h7.5a.75.75 0 0 0 0-1.5h-7.5a.75.75 0 0 0 0 1.5z'"
        " fill='#fff'/></svg>"
    ),
    "diff_modified": (
        "<svg width='14' height='14' viewBox='0 0 16 16' xmlns='http://www.w3.org/2000/svg'>"
        "<circle cx='8' cy='8' r='7' fill='#ef6c00'/>"
        "<circle cx='8' cy='8' r='2.5' fill='#fff'/></svg>"
    ),
}

#: Default dashboard port.
DEFAULT_PORT = 8765

#: CSS colour per evidence status for badges.
_EVIDENCE_STATUS_COLORS: dict[str, str] = {
    "verified": "#2e7d32",
    "claimed": "#c62828",
    "missing": "#9e9e9e",
    "invalid": "#d32f2f",
    "stale": "#f57c00",
    "unverified": "#9e9e9e",
}

#: CSS colour per RunStatus.
_RUN_STATUS_COLORS: dict[str, str] = {
    "started": "#1565c0",
    "completed": "#2e7d32",
    "interrupted": "#f57c00",
    "failed": "#c62828",
}

#: CSS colour per DecisionAssurance level.
_ASSURANCE_COLORS: dict[str, str] = {
    "full": "#2e7d32",
    "high": "#43a047",
    "moderate": "#ef6c00",
    "partial": "#f57c00",
    "low": "#d32f2f",
    "none": "#9e9e9e",
}
# =========================================================================
# Public API
# =========================================================================

__all__ = [
    "DEFAULT_PORT",
    "_decision_badge",
    "_get_overview_decisions",
    "_render_overview_page",
    "_render_run_detail",
    "serve",
]

# =========================================================================
# HTML components
# =========================================================================


def _status_badge(status: str, colors: Mapping[str, str]) -> str:
    """Return a coloured badge ``<span>`` for a status value."""
    color = colors.get(status, "#616161")
    return (
        f"<span class='badge' style='background:{color}'"
        f" title='{html_escape(status)}'>"
        f"{html_escape(status)}</span>"
    )


def _assurance_badge(assurance: str | None) -> str:
    """Return a coloured assurance badge."""
    if not assurance:
        return "<span class='badge' style='background:#9e9e9e'>—</span>"
    color = _ASSURANCE_COLORS.get(assurance, "#616161")
    return (
        f"<span class='badge' style='background:{color}'"
        f" title='assurance={html_escape(assurance)}'>"
        f"{html_escape(assurance)}</span>"
    )


def _evidence_status_badge(status: str | None) -> str:
    """Return a coloured evidence-status badge."""
    s = (status or "unknown").lower()
    color = _EVIDENCE_STATUS_COLORS.get(s, "#9e9e9e")
    return (
        f"<span class='badge evidence-badge' style='background:{color}'"
        f" title='evidence status: {html_escape(s)}'>"
        f"{html_escape(s)}</span>"
    )


def _short_id(run_id: str, width: int = 12) -> str:
    """Return a shortened run id for display."""
    if len(run_id) <= width:
        return run_id
    return run_id[:width] + "…"


def _iter_latest_decisions(
    log: RunLog,
) -> list[dict[str, Any]]:
    """Summarise the latest decision per step for overview cards."""
    audit = _RunAuditIndex.from_log(log)
    rows: list[dict[str, Any]] = []
    for step in log.steps:
        evals = [e for e in log.evaluations if e.step_id == step.step_id]
        if not evals:
            rows.append(
                {
                    "contract_id": step.contract_id,
                    "step_id": step.step_id,
                    "decision": "—",
                    "assurance": None,
                    "attempts": 0,
                    "candidate": "—",
                    "final": "—",
                    "outcome": "—",
                    "next_action": "—",
                },
            )
            continue
        latest = evals[-1]
        gate = None
        for g in audit.gates.get(step.step_id, []):
            if g.evaluation_id == latest.evaluation_id:
                gate = g
                break
        if gate is None and audit.gates.get(step.step_id):
            gate = audit.gates[step.step_id][-1]
        outcome = None
        for oc in log.outcomes:
            if oc.step_id == step.step_id:
                outcome = oc
        rows.append(
            {
                "contract_id": step.contract_id,
                "step_id": step.step_id,
                "decision": latest.decision or "—",
                "assurance": gate.assurance.value if gate else None,
                "attempts": len(evals),
                "candidate": gate.candidate_decision if gate else "—",
                "final": gate.final_decision if gate else latest.decision or "—",
                "outcome": outcome.decision if outcome else "—",
                "next_action": outcome.next_action if outcome else "—",
            },
        )
    return rows


def _get_overview_decisions(
    summaries: list[RunSummary],
    store: LineageStore,
) -> dict[str, dict[str, Any]]:
    """Extract the latest decision and assurance per run for the overview.

    Reads cached *latest_decision* from ``run.json`` for instant loads.
    Falls back to reading the log when the cache is stale.
    """
    result: dict[str, dict[str, Any]] = {}
    for s in summaries:
        meta = store._read_run_meta(s.run_id) or {}
        cached_decision = meta.get("latest_decision")
        if cached_decision:
            result[s.run_id] = {
                "decision": cached_decision,
                "assurance": meta.get("latest_assurance"),
                "final_decision": cached_decision,
                "has_decision": True,
            }
        else:
            result[s.run_id] = {
                "decision": "—",
                "assurance": None,
                "final_decision": "—",
                "has_decision": False,
            }
    return result


def _collect_plan_progress(
    active_summaries: list[RunSummary],
    store: LineageStore,
) -> dict[str, str]:
    """Build plan progress strings for active run cards.

    Reads the ``plan_snapshot`` from each active run's ``run.json`` and
    computes a human-readable progress string like ``"Step 2/5: Write tests"``.

    Args:
        active_summaries: Active runs to check for plan snapshots.
        store: The lineage store for reading metadata.

    Returns:
        Dict mapping ``run_id`` -> progress string. Runs without a plan
        snapshot are omitted from the result.
    """
    result: dict[str, str] = {}
    for s in active_summaries:
        meta = store._read_run_meta(s.run_id) or {}
        ps = meta.get("plan_snapshot")
        if not ps:
            continue
        steps = ps.get("steps", [])
        if not steps:
            continue
        # Find current step: first incomplete non-phase step, or first phase
        completed = 0
        current_title = ""
        total = len(steps)
        for step in steps:
            orig_status = step.get("status", "")
            depth = step.get("depth", 0)
            title = step.get("title", "")
            if orig_status == "completed":
                completed += 1
            elif not current_title and depth == 1:
                # First non-completed sub-step is the current active
                current_title = title
        if not current_title:
            # Use the last phase name as fallback
            for step in steps:
                if step.get("depth", 0) == 0:
                    current_title = step.get("title", "")
        if current_title:
            result[s.run_id] = f"Step {completed + 1}/{total}: {current_title[:30]}"
    return result


# =========================================================================
# CSS (inline, no external assets)
# =========================================================================

# =========================================================================
# CSS (inline, no external assets) — v0.9.1 dark theme
# =========================================================================

_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
  background:#0d1117;color:#e6edf3;font-size:13px;line-height:1.5}
a{color:#58a6ff;text-decoration:none}
a:hover{text-decoration:underline}
header{background:#161b22;color:#e6edf3;padding:12px 20px;
  display:flex;align-items:center;justify-content:space-between;
  border-bottom:1px solid #30363d}
header h1{font-size:1.1rem;font-weight:600;color:#e6edf3}
header .sub{font-size:0.75rem;color:#8b949e}
header .brand{display:flex;align-items:center;gap:8px}
header .brand svg{flex-shrink:0}
.container{max-width:1280px;margin:0 auto;padding:20px}

.stats-bar{display:flex;gap:12px;margin-bottom:20px;flex-wrap:wrap}
.stat-card{flex:1;min-width:100px;background:#161b22;border:1px solid #30363d;
  border-radius:8px;padding:14px 16px;text-align:center}
.stat-card .stat-value{font-size:1.6rem;font-weight:700;line-height:1.2}
.stat-card .stat-label{font-size:0.65rem;color:#8b949e;text-transform:uppercase;
  letter-spacing:.5px;margin-top:2px}
.stat-card.active .stat-value{color:#58a6ff}
.stat-card.completed .stat-value{color:#3fb950}
.stat-card.failed .stat-value{color:#f85149}
.stat-card.total .stat-value{color:#e6edf3}

.filter-bar{display:flex;gap:8px;align-items:center;margin-bottom:16px;flex-wrap:wrap}
.filter-btn{padding:5px 14px;border-radius:16px;font-size:0.72rem;font-weight:500;
  color:#8b949e;background:#161b22;border:1px solid #30363d;text-decoration:none;
  transition:all .15s}
.filter-btn:hover{color:#e6edf3;border-color:#58a6ff}
.filter-btn.active{background:#1f6feb;color:#fff;border-color:#1f6feb}
.search-form{flex:1;min-width:180px;margin-left:auto}
.search-input{width:100%;padding:5px 12px;border-radius:16px;border:1px solid #30363d;
  background:#0d1117;color:#e6edf3;font-size:0.72rem;outline:none}
.search-input:focus{border-color:#58a6ff}
.search-input::placeholder{color:#484f58}
.task-group-header{display:flex;align-items:center;gap:6px;padding:8px 0;margin-top:8px;
  font-size:0.72rem;color:#8b949e;grid-column:1/-1;border-bottom:1px solid #21262d}
.task-group-header .task-group-count{color:#484f58;font-size:0.65rem}
.task-group-icon{font-size:0.85rem}

.section-head{display:flex;align-items:center;justify-content:space-between;
  margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid #21262d}
.section-head h2{font-size:0.82rem;color:#8b949e;font-weight:500;
  text-transform:uppercase;letter-spacing:.5px;display:flex;align-items:center;gap:6px}
.section-head h2 .sse-status{font-size:0.65rem;color:#3fb950}
.section-head .count{font-size:0.7rem;color:#484f58}
.active-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));
  gap:10px;margin-bottom:24px}
.active-card{background:#161b22;border:1px solid #30363d;border-radius:8px;
  padding:16px;transition:border-color .15s;display:block;text-decoration:none;
  position:relative}
.active-card:hover{border-color:#58a6ff;text-decoration:none}
.active-card .card-top{display:flex;justify-content:space-between;
  align-items:flex-start;margin-bottom:10px}
.active-card .card-task{font-size:0.92rem;font-weight:600;color:#e6edf3;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1;margin-right:8px}
.active-card .card-badges{display:flex;gap:4px;flex-wrap:wrap;flex-shrink:0}
.active-card .card-step{font-size:0.75rem;color:#8b949e;margin-bottom:8px;
  display:flex;align-items:center;gap:6px}
.active-card .card-action{font-size:0.78rem;color:#c9d1d9;margin-bottom:8px}
.active-card .card-footer{display:flex;justify-content:space-between;
  align-items:center;font-size:0.68rem;color:#484f58}
.active-card .live-dot-sm{width:7px;height:7px;border-radius:50%;
  background:#3fb950;animation:pulse 1.5s infinite;display:inline-block}

.badge{display:inline-block;padding:2px 7px;border-radius:12px;font-size:0.63rem;
  font-weight:600;color:#fff;text-transform:uppercase;letter-spacing:.3px;
  white-space:nowrap}
.badge.evidence-badge{text-transform:none;font-weight:500}

.run-table{width:100%;border-collapse:collapse;font-size:0.78rem;margin-bottom:16px}
.run-table th{text-align:left;padding:8px 10px;background:#161b22;color:#8b949e;
  font-weight:500;font-size:0.68rem;text-transform:uppercase;letter-spacing:.4px;
  border-bottom:2px solid #30363d}
.run-table td{padding:8px 10px;border-bottom:1px solid #21262d;color:#e6edf3}
.run-table tr:hover td{background:rgba(88,166,255,0.04)}
.run-table a{color:#58a6ff}
.run-table .mono{font-family:monospace;font-size:0.7rem;color:#8b949e}
.run-table .dur{color:#8b949e;font-size:0.7rem}

.empty-state{text-align:center;padding:80px 24px;color:#8b949e}
.empty-state .empty-icon{margin-bottom:16px;opacity:0.25}
.empty-state h2{font-size:1.15rem;margin-bottom:8px;color:#e6edf3}
.empty-state p{font-size:0.82rem;margin-bottom:4px}
.empty-state code{background:#161b22;padding:3px 7px;border-radius:4px;
  font-size:0.82rem;color:#58a6ff}

.ssr-bar{display:flex;align-items:center;gap:6px;font-size:0.68rem;
  color:#484f58;margin-top:12px;padding:8px 0}
.ssr-bar.connected{color:#3fb950}
.ssr-bar .ssr-dot{width:6px;height:6px;border-radius:50%;background:#484f58}
.ssr-bar.connected .ssr-dot{background:#3fb950}

.back-nav{margin-bottom:16px;font-size:0.8rem}
.back-nav a{color:#8b949e}
.back-nav a:hover{color:#58a6ff}
.run-detail-header{margin-bottom:16px}
.run-detail-header h2{font-size:1.1rem;color:#e6edf3;margin-bottom:8px;
  display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.meta-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));
  gap:8px;font-size:0.78rem}
.meta-grid .label{color:#8b949e;margin-right:6px;font-size:0.68rem;
  text-transform:uppercase;letter-spacing:.4px}

.tab-nav{display:flex;gap:0;border-bottom:2px solid #30363d;margin-bottom:16px}
.tab-btn{padding:10px 16px;font-size:0.8rem;font-weight:500;color:#8b949e;
  background:none;border:none;border-bottom:2px solid transparent;
  margin-bottom:-2px;cursor:pointer;transition:color .15s,border-color .15s}
.tab-btn:hover{color:#e6edf3}
.tab-btn.active{color:#58a6ff;border-bottom-color:#58a6ff}
.tab-panel{display:none}
.tab-panel.active{display:block}

.plan-progress{display:flex;align-items:center;gap:0;margin-bottom:16px;
  overflow-x:auto;padding:8px 0}
.plan-step{display:flex;align-items:center;gap:6px;flex-shrink:0}
.plan-step-circle{width:28px;height:28px;border-radius:50%;display:flex;
  align-items:center;justify-content:center;font-size:0.7rem;font-weight:700;
  flex-shrink:0;border:2px solid #30363d;background:#0d1117;color:#8b949e}
.plan-step-circle.completed{background:#2e7d32;border-color:#2e7d32;color:#fff}
.plan-step-circle.active{background:#1f6feb;border-color:#58a6ff;color:#fff;
  box-shadow:0 0 8px rgba(88,166,255,0.4)}
.plan-step-circle.failed{background:#c62828;border-color:#c62828;color:#fff}
.plan-step-label{font-size:0.7rem;color:#8b949e;max-width:100px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.plan-step-label.active{color:#e6edf3;font-weight:600}
.plan-connector{width:24px;height:2px;background:#30363d;flex-shrink:0}
.plan-connector.completed{background:#2e7d32}

.current-action{display:flex;align-items:center;gap:12px;padding:14px 16px;
  background:#161b22;border:1px solid #30363d;border-radius:8px;margin-bottom:16px}
.current-action .action-spinner{flex-shrink:0}
.current-action .action-spinner svg{animation:spin 1s linear infinite}
.current-action .action-info{flex:1}
.current-action .action-now{font-size:0.7rem;color:#8b949e;text-transform:uppercase;
  letter-spacing:.5px;margin-bottom:2px}
.current-action .action-desc{font-size:0.9rem;font-weight:600;color:#e6edf3}
.current-action .action-step{font-size:0.72rem;color:#8b949e;margin-top:2px}
@keyframes spin{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}

.decision-panel{display:flex;align-items:flex-start;gap:12px;padding:14px 16px;
  background:#161b22;border:1px solid #30363d;border-radius:8px;margin-bottom:16px}
.decision-panel .decision-icon{flex-shrink:0;width:32px;height:32px;
  border-radius:50%;display:flex;align-items:center;justify-content:center}
.decision-panel .decision-icon.accept{background:rgba(46,125,50,0.15)}
.decision-panel .decision-icon.retry{background:rgba(239,108,0,0.15)}
.decision-panel .decision-icon.replan{background:rgba(139,92,246,0.15)}
.decision-panel .decision-icon.rollback{background:rgba(198,40,40,0.15)}
.decision-panel .decision-body{flex:1}
.decision-panel .decision-header{display:flex;align-items:center;gap:8px;
  margin-bottom:4px;flex-wrap:wrap}
.decision-panel .decision-label{font-size:0.85rem;font-weight:600;color:#e6edf3}
.decision-panel .decision-reason{font-size:0.75rem;color:#8b949e;line-height:1.4}
.decision-panel .decision-next{display:flex;align-items:center;gap:4px;
  margin-top:6px;font-size:0.75rem;color:#58a6ff}

.recent-activity{margin-bottom:16px}
.recent-activity h3{font-size:0.75rem;color:#8b949e;text-transform:uppercase;
  letter-spacing:.5px;margin-bottom:8px;font-weight:500}
.activity-list{list-style:none}
.activity-item{display:flex;gap:10px;padding:6px 0;border-bottom:1px solid #21262d;
  font-size:0.72rem;align-items:baseline}
.activity-item .act-time{color:#484f58;min-width:68px;flex-shrink:0;font-size:0.65rem}
.activity-item .act-kind{color:#8b949e;min-width:80px;flex-shrink:0;font-size:0.65rem;
  text-transform:uppercase;letter-spacing:.2px}
.activity-item .act-text{color:#e6edf3;flex:1;word-break:break-word}

.placeholder-tab{text-align:center;padding:48px 24px;color:#8b949e}
.placeholder-tab .ph-icon{margin-bottom:12px;opacity:0.3}
.placeholder-tab h3{font-size:1rem;color:#e6edf3;margin-bottom:4px}
.placeholder-tab p{font-size:0.78rem}

.detail-body{display:flex;gap:12px;margin-bottom:12px;min-height:300px}
.timeline-panel{flex:2;min-width:0;background:#0d1117;border:1px solid #30363d;
  border-radius:8px;overflow:hidden;display:flex;flex-direction:column}
.timeline-panel.full-width{flex:1 1 100%}
.timeline{flex:1;overflow-y:auto;padding:0;display:flex;flex-direction:column}
#timeline-items{padding:8px 12px;flex:1;overflow-y:auto}
.timeline-header{display:flex;justify-content:space-between;align-items:center;
  padding:10px 12px;border-bottom:1px solid #30363d;background:#161b22;
  position:sticky;top:0;z-index:1}
.timeline-header span{font-weight:600;font-size:0.78rem;color:#e6edf3}
.jump-btn{background:#21262d;border:1px solid #30363d;color:#8b949e;
  padding:3px 10px;border-radius:6px;cursor:pointer;font-size:0.68rem}
.jump-btn:hover{background:#30363d;color:#e6edf3}
.tl-entry{padding:8px 0;border-bottom:1px solid #21262d;display:flex;
  gap:10px;align-items:flex-start;font-size:0.72rem}
.tl-entry .tl-time{color:#484f58;min-width:70px;font-size:0.65rem;flex-shrink:0}
.tl-entry .tl-kind{color:#8b949e;min-width:90px;font-size:0.65rem;text-transform:uppercase;
  letter-spacing:.3px;flex-shrink:0;font-weight:600}
.tl-entry .tl-text{color:#e6edf3;flex:1;word-break:break-word}
.tl-entry.tl-waiting{justify-content:center;padding:20px 0}

.sidebar-panel{flex:1;min-width:240px}
.sidebar{background:#161b22;border:1px solid #30363d;border-radius:8px;
  overflow:hidden}
.sidebar-header{display:flex;justify-content:space-between;align-items:center;
  padding:10px 14px;cursor:pointer;background:#161b22;border-bottom:1px solid #30363d;
  user-select:none}
.sidebar-header span{font-weight:600;font-size:0.78rem;color:#e6edf3}
.sidebar-header .toggle-icon{font-size:0.7rem;color:#8b949e;transition:transform .2s}
.sidebar-header .toggle-icon.collapsed{transform:rotate(-90deg)}
.sidebar-body{padding:12px 14px;font-size:0.75rem}
.sidebar-body dl{margin:0}
.sidebar-body dt{color:#8b949e;font-size:0.65rem;text-transform:uppercase;
  letter-spacing:0.5px;margin-top:8px}
.sidebar-body dt:first-child{margin-top:0}
.sidebar-body dd{color:#e6edf3;word-break:break-all;margin:2px 0 0 0;font-size:0.75rem}
.sidebar-body code{background:#0d1117;border:1px solid #30363d;border-radius:3px;
  padding:1px 4px;font-size:0.68rem}

.evidence-bar{background:#161b22;border:1px solid #30363d;border-radius:8px;
  padding:10px 14px;margin-bottom:12px;display:flex;flex-wrap:wrap;gap:6px 18px;
  align-items:center;font-size:0.75rem}
.evidence-bar .ev-group{display:flex;align-items:center;gap:5px}
.evidence-bar .ev-group .ev-name{color:#8b949e;font-size:0.65rem;
  text-transform:uppercase;letter-spacing:0.3px}
.evidence-bar .ev-group .ev-icon{font-size:0.75rem}
.evidence-bar .ev-group .ev-pct{font-weight:600}
.evidence-bar .ev-group .ev-progress{width:50px;height:3px;background:#21262d;
  border-radius:2px;overflow:hidden}
.evidence-bar .ev-group .ev-progress-fill{height:100%;border-radius:2px}
.evidence-bar .risk-badge{font-size:0.65rem;font-weight:700;padding:2px 6px;
  border-radius:4px;text-transform:uppercase}

.step-section{margin-bottom:16px}
.step-card{background:#161b22;border:1px solid #30363d;border-radius:8px;
  margin-bottom:10px;overflow:hidden}
.step-card .step-header{padding:12px 14px;border-bottom:1px solid #21262d;
  display:flex;align-items:center;justify-content:space-between;
  flex-wrap:wrap;gap:6px}
.step-card .step-header .step-title{font-weight:600;font-size:0.85rem;color:#e6edf3}
.step-card .step-body{padding:10px 14px}
.attempt-box{margin:6px 0;padding:8px 10px;border-left:3px solid #30363d;
  background:#0d1117;border-radius:0 4px 4px 0}
.attempt-box .attempt-title{font-size:0.75rem;font-weight:600;color:#8b949e;
  margin-bottom:4px;display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.decision-gate{margin-top:4px;padding-top:4px;border-top:1px solid #21262d;
  font-size:0.75rem}
.decision-gate .gate-label{color:#8b949e}
.outcome-row{margin-top:3px;font-size:0.75rem;color:#8b949e}
.trigger-highlight{background:rgba(210,153,34,0.08);border-left:3px solid #d29922;
  padding:6px 8px;margin:6px 0;border-radius:0 4px 4px 0;font-size:0.75rem}
.trigger-highlight strong{color:#d29922}
.collector-fail{margin-top:3px;font-size:0.72rem;color:#f85149}
.legend{display:flex;flex-wrap:wrap;gap:4px 12px;margin-bottom:12px;
  font-size:0.72rem;color:#8b949e}
.legend-item{display:flex;align-items:center;gap:3px}

.raw-lineage{margin-top:12px}
.raw-lineage summary{cursor:pointer;font-size:0.8rem;color:#8b949e;padding:4px 0}
.raw-lineage summary:hover{color:#e6edf3}
.raw-lineage pre{margin-top:6px;padding:12px;background:#0d1117;
  border:1px solid #30363d;color:#e6edf3;border-radius:4px;overflow:auto;
  font-size:0.68rem;max-height:350px}

.sse-indicator{display:inline-flex;align-items:center;gap:4px;font-size:0.7rem;
  color:#8b949e;margin-left:10px}
.sse-indicator.connected{color:#3fb950}
.sse-indicator .sse-dot{width:6px;height:6px;border-radius:50%;background:#8b949e}
.sse-indicator.connected .sse-dot{background:#3fb950}

.duration-display{font-size:0.75rem;color:#8b949e}

.page-footer{margin-top:20px;font-size:0.72rem;color:#30363d;text-align:center}

/* Evidence tab */
.ev-checks{margin-bottom:12px}
.ev-check-row{display:flex;align-items:center;gap:8px;padding:6px 0;
  border-bottom:1px solid #21262d;font-size:0.75rem}
.ev-check-row.ev-pass{border-left:3px solid #2e7d32;padding-left:8px}
.ev-check-row.ev-fail{border-left:3px solid #c62828;padding-left:8px}
.ev-check-row.ev-miss{border-left:3px solid #9e9e9e;padding-left:8px}
.ev-check-icon{flex-shrink:0}
.ev-check-label{flex:1;color:#e6edf3;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ev-check-prov{flex-shrink:0;font-size:0.65rem;display:flex;align-items:center;gap:3px}
.score-grid{margin-bottom:8px}
.score-item{display:flex;align-items:center;gap:8px;margin-bottom:4px;font-size:0.72rem}
.score-label{width:75px;color:#8b949e;text-transform:uppercase;font-size:0.65rem;flex-shrink:0}
.score-bar-bg{flex:1;height:8px;background:#21262d;border-radius:4px;overflow:hidden}
.score-bar-fill{height:100%;border-radius:4px;transition:width .3s}
.score-val{width:36px;text-align:right;color:#e6edf3;font-weight:600;flex-shrink:0}
.score-threshold{padding:4px 0;color:#d29922;font-size:0.7rem;font-weight:600}
.collector-list{margin-bottom:12px}
.collector-row{display:flex;align-items:center;gap:6px;padding:4px 0;font-size:0.75rem;color:#e6edf3}
.collector-ver{color:#8b949e;font-size:0.65rem}

/* Artifacts tab */
.checkpoint-list{margin-bottom:8px}
.cp-row{display:flex;align-items:center;gap:6px;padding:4px 0;font-size:0.75rem}
.cp-id{color:#8b949e;font-size:0.68rem}
.ws-info{margin-bottom:12px;font-size:0.75rem}
.ws-row{display:flex;align-items:center;gap:8px;padding:3px 0}
.ws-label{color:#8b949e;width:90px;flex-shrink:0;font-size:0.68rem;text-transform:uppercase}
.event-file-list{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:8px}

/* Replan section */
.replan-step{padding:6px 8px;background:#0d1117;border-radius:4px;margin-bottom:4px;font-size:0.75rem}

@media(max-width:768px){
  .active-cards{grid-template-columns:1fr}
  header{flex-direction:column;gap:4px}
  .detail-body{flex-direction:column}
  .timeline-panel{flex:1 1 100%}
  .sidebar-panel{flex:1 1 100%}
  .plan-progress{flex-wrap:wrap}
  .tab-btn{padding:8px 12px;font-size:0.75rem}
}
@media(max-width:480px){
  .stats-bar{flex-direction:column}
}
"""

def _evidence_row(
    check_id: str | None,
    collector: str | None,
    provenance: str | None,
    status: str | None,
    *,
    is_trigger: bool = False,
) -> str:
    """Render one evidence row with provenance and status badges."""
    prov = provenance or "missing"
    pcolor = PROVENANCE_COLORS.get(prov, "#9e9e9e")
    label = check_id or collector or "?"
    trigger_svg = (
        '<svg width="12" height="12" viewBox="0 0 16 16" style="vertical-align:middle">'
        '<path d="M8.22 1.754a.25.25 0 0 0-.44 0L1.698 13.132a.25.25 0 0 0 .22.368h12.164a.25.25 0 0 0 .22-.368L8.22 1.754z"'
        ' fill="#d29922"/><text x="8" y="11.5" text-anchor="middle" font-size="9" font-weight="bold" fill="#0d1117">!</text></svg>'
    ) if is_trigger else ""
    return (
        f"<div class='evidence-row'>"
        f"<span class='badge' style='background:{pcolor}'"
        f" title='provenance={html_escape(prov)}'>"
        f"{html_escape(prov)}</span>"
        f"{_evidence_status_badge(status)}"
        f"<span class='check-id'>{html_escape(label)}{trigger_svg}</span>"
        f"</div>"
    )


# =========================================================================
# Page templates
# =========================================================================


def _decision_badge(decision: str) -> str:
    """Return a coloured badge for a BOUND decision value."""
    if decision in ("—", None, ""):
        return "<span class='badge' style='background:#9e9e9e'>—</span>"
    color = DECISION_COLORS.get(decision, "#616161")
    return (
        f"<span class='badge' style='background:{color}'"
        f" title='decision={html_escape(decision)}'>"
        f"{html_escape(decision)}</span>"
    )




def _derive_plan_step_status(
    plan_step: dict,
    runtime_steps: list,
    log_events: list,
) -> tuple[str, str]:
    """Derive the status and CSS class for a plan step.

    Compares a plan step (from plan.md snapshot) against runtime lineage steps
    to determine whether it is completed, active, skipped, replanned, or pending.

    Args:
        plan_step: Single step dict from plan_snapshot.
        runtime_steps: Step objects from the run log.
        log_events: All lineage events from the run log.

    Returns:
        A ``(status_label, css_class)`` pair.
    """
    plan_title = (plan_step.get("title") or "").lower().strip()
    plan_id = plan_step.get("step_id", "")

    matched = None
    for rs in runtime_steps:
        rs_desc = (getattr(rs, "description", None) or "").lower().strip()
        rs_id = getattr(rs, "step_id", "")
        if rs_id and plan_id and rs_id.startswith(plan_id[:8]):
            matched = rs
            break
        if plan_title and rs_desc and (
            plan_title in rs_desc or rs_desc in plan_title
        ):
            matched = rs
            break

    if matched is not None:
        rs_status = getattr(matched, "status", None)
        rs_status_v = getattr(rs_status, "value", None) if rs_status else ""
        if rs_status_v in ("completed",):
            return ("completed", "completed")
        if rs_status_v in ("started", "in_progress"):
            return ("active", "active")
        if rs_status_v in ("failed",):
            return ("failed", "failed")
        if rs_status_v in ("replanned",):
            return ("replanned", "replanned")
        return ("in_progress", "active")

    orig_status = plan_step.get("status", "")
    if orig_status == "completed":
        return ("completed", "completed")
    if plan_step.get("status") == "skipped":
        return ("skipped", "skipped")

    return ("pending", "")


def _status_to_color(status: str) -> str:
    """Map a status label to a hex colour code."""
    return {
        "completed": "3fb950",
        "active": "58a6ff",
        "failed": "f85149",
        "replanned": "8b5cf6",
        "skipped": "484f58",
        "pending": "30363d",
    }.get(status, "8b949e")


def _render_plan_section(
    parts: list[str],
    plan_steps: list[dict],
    plan_goal: str | None,
    plan_source: str | None,
    runtime_steps: list,
    is_active: bool,
) -> None:
    """Render the plan snapshot section in the Execution tab."""
    parts.append("<div class='plan-section'>")
    parts.append(
        "<div class='plan-header' style='display:flex;align-items:center;"
        "justify-content:space-between;margin-bottom:10px'>"
        "<span style='font-size:0.82rem;font-weight:600;color:#8b949e;"
        "text-transform:uppercase;letter-spacing:.5px'>"
        f"{_icon('run', w=14, h=14)} Plan</span>"
    )
    if plan_source:
        parts.append(
            f"<span style='font-size:0.65rem;color:#484f58'>"
            f"{html_escape(plan_source)}</span>"
        )
    parts.append("</div>")

    if plan_goal:
        parts.append(
            f"<div style='font-size:0.78rem;color:#c9d1d9;margin-bottom:10px;"
            f"padding:6px 10px;background:#161b22;border-radius:4px;"
            f"border-left:3px solid #58a6ff'>"
            f"{html_escape(plan_goal)}"
            f"</div>"
        )

    completed = 0
    total = len([s for s in plan_steps if s.get("depth", 0) == 0])
    if total == 0:
        total = len(plan_steps)

    for step in plan_steps:
        depth = step.get("depth", 0)
        title = step.get("title", "Untitled")
        status_label, _css = _derive_plan_step_status(step, runtime_steps, [])
        if status_label == "completed":
            completed += 1
        ordinal = step.get("ordinal", 0)
        is_phase = depth == 0

        if status_label == "completed":
            status_icon = _icon("evidence_passed", w=14, h=14)
        elif status_label == "active":
            status_icon = (
                "<svg width='14' height='14' viewBox='0 0 16 16'>"
                "<circle cx='8' cy='8' r='5' fill='none' stroke='#58a6ff'"
                " stroke-width='2' stroke-dasharray='8 4'/>"
                "<circle cx='8' cy='8' r='2' fill='#58a6ff'/></svg>"
            )
        elif status_label in ("failed", "replanned"):
            status_icon = _icon("evidence_failed", w=14, h=14)
        elif status_label == "skipped":
            status_icon = (
                "<svg width='14' height='14' viewBox='0 0 16 16'>"
                "<circle cx='8' cy='8' r='5' fill='none' stroke='#484f58'"
                " stroke-width='1.5'/>"
                "<path d='M5.5 8h5' stroke='#484f58' stroke-width='1.5'"
                " stroke-linecap='round'/></svg>"
            )
        else:
            status_icon = (
                "<svg width='14' height='14' viewBox='0 0 16 16'>"
                "<circle cx='8' cy='8' r='5' fill='none' stroke='#30363d'"
                " stroke-width='1.5'/></svg>"
            )

        indent = 24 if depth == 1 else 0
        font_size = "0.78rem" if is_phase else "0.72rem"
        font_weight = "600" if is_phase else "400"
        color = "#e6edf3" if is_phase else "#8b949e"

        parts.append(
            f"<div style='display:flex;align-items:center;gap:8px;"
            f"padding:5px 0;margin-left:{indent}px'>"
            f"{status_icon}"
            f"<span style='font-size:{font_size};font-weight:{font_weight};"
            f"color:{color};flex:1'>{html_escape(title)}</span>"
        )
        if is_phase:
            parts.append(
                f"<span style='font-size:0.6rem;color:#484f58;"
                f"background:#21262d;padding:1px 6px;border-radius:3px'>"
                f"PHASE-{ordinal:03d}</span>"
            )
        elif status_label != "pending":
            parts.append(
                f"<span style='font-size:0.6rem;"
                f"color:#{_status_to_color(status_label)};"
                f"text-transform:uppercase'>{status_label}</span>"
            )
        parts.append("</div>")

    if total > 0:
        pct = int(completed / total * 100)
        parts.append(
            f"<div style='margin-top:8px;font-size:0.68rem;color:#8b949e'>"
            f"Progress: {completed}/{total} steps completed ({pct}%)"
            f"</div>"
        )

    parts.append("</div>")


def _render_plan_vs_reality(
    parts: list[str],
    plan_steps: list[dict],
    runtime_steps: list,
    log: object,
) -> None:
    """Render a Plan vs Reality comparison section."""
    parts.append(
        "<div class='plan-vs-reality' style='margin-top:16px;"
        "background:#161b22;border:1px solid #30363d;border-radius:8px;"
        "padding:14px 16px'>"
    )
    parts.append(
        "<h3 style='font-size:0.82rem;color:#8b5cf6;margin-bottom:10px'>"
        f"{_icon('decision_replan', w=14, h=14)} Plan vs Reality</h3>"
    )

    mismatches = 0
    for ps in plan_steps:
        title = ps.get("title", "Untitled")
        status_label, _css = _derive_plan_step_status(ps, runtime_steps, [])

        if status_label == "completed":
            icon = _icon("evidence_passed", w=12, h=12)
            row_color = "#3fb950"
            bg = "#0d3320"
        elif status_label == "active":
            icon = _icon("run", w=12, h=12)
            row_color = "#58a6ff"
            bg = "#0d1f33"
        elif status_label == "failed":
            icon = _icon("evidence_failed", w=12, h=12)
            row_color = "#f85149"
            bg = "#330d0d"
        elif status_label == "replanned":
            icon = _icon("decision_replan", w=12, h=12)
            row_color = "#8b5cf6"
            bg = "#1a0d33"
        elif status_label == "skipped":
            icon = (
                "<svg width='12' height='12' viewBox='0 0 16 16'>"
                "<path d='M5.5 8h5' stroke='#484f58' stroke-width='2'"
                " stroke-linecap='round'/></svg>"
            )
            row_color = "#484f58"
            bg = "#161b22"
        else:
            icon = (
                "<svg width='12' height='12' viewBox='0 0 16 16'>"
                "<circle cx='8' cy='8' r='5' fill='none' stroke='#30363d'"
                " stroke-width='1.5'/></svg>"
            )
            row_color = "#30363d"
            bg = "#0d1117"
            mismatches += 1

        parts.append(
            f"<div style='display:flex;align-items:center;gap:8px;"
            f"padding:5px 8px;background:{bg};border-radius:4px;"
            f"margin-bottom:3px;font-size:0.72rem;"
            f"border-left:3px solid {row_color}'>"
            f"{icon}"
            f"<span style='flex:1;color:#c9d1d9'>{html_escape(title)}</span>"
            f"<span style='color:{row_color};font-size:0.65rem;"
            f"text-transform:uppercase'>{status_label}</span>"
            f"</div>"
        )

    plan_total = len(plan_steps)
    rt_total = len(runtime_steps)
    parts.append(
        f"<div style='margin-top:8px;font-size:0.68rem;color:#8b949e;"
        f"display:flex;gap:16px'>"
        f"<span>Plan: {plan_total} steps</span>"
        f"<span>Runtime: {rt_total} step{'s' if rt_total != 1 else ''}</span>"
        f"<span>Unmatched: {mismatches}</span>"
        f"</div>"
    )
    parts.append("</div>")


def _render_plan_tab(
    parts: list[str],
    plan_steps: list[dict],
    plan_goal: str | None,
    plan_source: str | None,
    runtime_steps: list,
    log: object,
) -> None:
    """Render the full Plan tab with status overlays."""
    parts.append("<div style='max-width:900px;margin:0 auto'>")
    parts.append(
        "<div style='display:flex;align-items:center;"
        "justify-content:space-between;margin-bottom:16px;"
        "padding-bottom:8px;border-bottom:1px solid #21262d'>"
        "<h3 style='font-size:0.9rem;color:#e6edf3;margin:0'>"
        f"{_icon('run', w=16, h=16)} Plan Execution Status</h3>"
    )
    if plan_source:
        parts.append(
            f"<code style='font-size:0.65rem;color:#484f58'>"
            f"{html_escape(plan_source)}</code>"
        )
    parts.append("</div>")

    if plan_goal:
        parts.append(
            f"<div style='margin-bottom:16px;padding:10px 14px;"
            f"background:#161b22;border-radius:6px;"
            f"border-left:3px solid #58a6ff'>"
            f"<div style='font-size:0.65rem;color:#8b949e;"
            f"text-transform:uppercase;margin-bottom:4px'>Goal</div>"
            f"<div style='font-size:0.85rem;color:#e6edf3'>"
            f"{html_escape(plan_goal)}</div>"
            f"</div>"
        )

    current_phase: dict | None = None
    phase_steps: list[dict] = []
    for step in plan_steps:
        if step.get("depth", 0) == 0:
            if current_phase is not None:
                _render_plan_tab_phase(parts, current_phase, phase_steps,
                                       runtime_steps)
            current_phase = step
            phase_steps = []
        else:
            phase_steps.append(step)
    if current_phase is not None:
        _render_plan_tab_phase(parts, current_phase, phase_steps,
                               runtime_steps)

    # Legend
    parts.append(
        "<div style='margin-top:20px;padding:12px;background:#161b22;"
        "border-radius:6px;display:flex;gap:20px;flex-wrap:wrap;"
        "font-size:0.65rem;color:#8b949e'>"
        f"<span>{_icon('evidence_passed', w=12, h=12)} completed</span>"
        "<span><svg width='12' height='12' viewBox='0 0 16 16'>"
        "<circle cx='8' cy='8' r='5' fill='none' stroke='#58a6ff'"
        " stroke-width='2' stroke-dasharray='8 4'/>"
        "<circle cx='8' cy='8' r='2' fill='#58a6ff'/></svg> active</span>"
        f"<span>{_icon('evidence_failed', w=12, h=12)} failed</span>"
        f"<span>{_icon('decision_replan', w=12, h=12)} replanned</span>"
        "<span><svg width='12' height='12' viewBox='0 0 16 16'>"
        "<circle cx='8' cy='8' r='5' fill='none' stroke='#484f58'"
        " stroke-width='1.5'/>"
        "<path d='M5.5 8h5' stroke='#484f58' stroke-width='1.5'"
        " stroke-linecap='round'/></svg> skipped</span>"
        "<span><svg width='12' height='12' viewBox='0 0 16 16'>"
        "<circle cx='8' cy='8' r='5' fill='none' stroke='#30363d'"
        " stroke-width='1.5'/></svg> pending</span>"
        "</div>"
    )
    parts.append("</div>")


def _render_plan_tab_phase(
    parts: list[str],
    phase: dict,
    sub_steps: list[dict],
    runtime_steps: list,
) -> None:
    """Render one phase block in the Plan tab."""
    title = phase.get("title", "Untitled Phase")
    ordinal = phase.get("ordinal", 0)
    status_label, _css = _derive_plan_step_status(phase, runtime_steps, [])

    phase_bg = "#0d1117"
    border_color = "#30363d"
    if status_label == "completed":
        border_color = "#3fb950"
    elif status_label == "active":
        border_color = "#58a6ff"
    elif status_label == "failed":
        border_color = "#f85149"

    parts.append(
        f"<div style='margin-bottom:12px;background:{phase_bg};"
        f"border:1px solid {border_color};border-radius:8px;overflow:hidden'>"
    )
    parts.append(
        f"<div style='display:flex;align-items:center;gap:8px;"
        f"padding:10px 14px;background:#161b22;"
        f"border-bottom:1px solid #21262d'>"
        f"<span style='font-size:0.65rem;color:#484f58;"
        f"background:#21262d;padding:2px 8px;border-radius:3px'>"
        f"PHASE-{ordinal:03d}</span>"
        f"<span style='font-size:0.82rem;font-weight:600;color:#e6edf3;flex:1'>"
        f"{html_escape(title)}</span>"
    )
    if status_label != "pending":
        color = _status_to_color(status_label)
        parts.append(
            f"<span style='font-size:0.6rem;color:#{color};"
            f"background:#{color}1a;padding:2px 8px;border-radius:3px;"
            f"text-transform:uppercase'>{status_label}</span>"
        )
    parts.append("</div>")

    if sub_steps:
        parts.append("<div style='padding:8px 14px'>")
        for ss in sub_steps:
            ss_title = ss.get("title", "Untitled")
            ss_status, _css = _derive_plan_step_status(ss, runtime_steps, [])
            acceptance = ss.get("acceptance_checks", [])

            if ss_status == "completed":
                icon = _icon("evidence_passed", w=14, h=14)
                row_color = "#3fb950"
            elif ss_status == "active":
                icon = (
                    "<svg width='14' height='14' viewBox='0 0 16 16'>"
                    "<circle cx='8' cy='8' r='5' fill='none' stroke='#58a6ff'"
                    " stroke-width='2' stroke-dasharray='8 4'/>"
                    "<circle cx='8' cy='8' r='2' fill='#58a6ff'/></svg>"
                )
                row_color = "#58a6ff"
            elif ss_status in ("failed", "replanned"):
                icon = _icon("evidence_failed", w=14, h=14)
                row_color = "#f85149"
            elif ss_status == "skipped":
                icon = (
                    "<svg width='14' height='14' viewBox='0 0 16 16'>"
                    "<circle cx='8' cy='8' r='5' fill='none' stroke='#484f58'"
                    " stroke-width='1.5'/>"
                    "<path d='M5.5 8h5' stroke='#484f58' stroke-width='1.5'"
                    " stroke-linecap='round'/></svg>"
                )
                row_color = "#484f58"
            else:
                icon = (
                    "<svg width='14' height='14' viewBox='0 0 16 16'>"
                    "<circle cx='8' cy='8' r='5' fill='none' stroke='#30363d'"
                    " stroke-width='1.5'/></svg>"
                )
                row_color = "#8b949e"

            parts.append(
                f"<div style='display:flex;align-items:center;gap:8px;"
                f"padding:4px 0;font-size:0.72rem'>"
                f"{icon}"
                f"<span style='color:{row_color};flex:1'>"
                f"{html_escape(ss_title)}</span>"
            )
            if ss_status != "pending":
                parts.append(
                    f"<span style='font-size:0.6rem;"
                    f"color:#{_status_to_color(ss_status)};"
                    f"text-transform:uppercase'>{ss_status}</span>"
                )
            parts.append("</div>")

            if acceptance:
                for ac in acceptance:
                    ac_text = str(ac).lstrip("- ").strip()
                    parts.append(
                        f"<div style='margin-left:22px;font-size:0.65rem;"
                        f"color:#484f58;padding:2px 0'>"
                        f"check: {html_escape(ac_text)}"
                        f"</div>"
                    )
        parts.append("</div>")
    else:
        parts.append(
            "<div style='padding:8px 14px;font-size:0.68rem;"
            "color:#484f58'>No sub-steps defined</div>"
        )
    parts.append("</div>")

def _render_overview_page(
    summaries: list[RunSummary],
    store_path: str,
    decisions: dict[str, dict[str, Any]] | None = None,
    filter_status: str = "all",
    search_q: str = "",
    plan_progress: dict[str, str] | None = None,
) -> str:
    """Render the dashboard overview with active runs as cards and historical as table.

    Active runs (incomplete/started) appear as cards at the top.
    Historical runs (completed/failed) appear in a compact table below.
    Runs sharing the same task name are grouped under a common header.
    No run appears in both sections.

    Args:
        summaries: List of run summaries to display.
        store_path: Path to the lineage store for display.
        decisions: Optional pre-cached decision data keyed by run_id.
        filter_status: Current filter selection.
        search_q: Current search query string.
        plan_progress: Optional dict mapping run_id to a plan progress string
            like ``"Step 2/5: Write tests"``.
    """
    total = len(summaries)
    active_summaries = [s for s in summaries if s.incomplete or str(s.status).lower() == "started"]
    historical_summaries = [s for s in summaries if s not in active_summaries]
    active_count = len(active_summaries)
    completed = sum(1 for s in historical_summaries if str(s.status).lower() == "completed")
    failed = len(historical_summaries) - completed

    # Group active runs by task name
    task_groups: dict[str, list[RunSummary]] = {}
    for s in active_summaries:
        key = s.task or "(untitled)"
        task_groups.setdefault(key, []).append(s)

    # SVG icons (no emoji)
    bound_icon = (
        '<svg width="18" height="18" viewBox="0 0 16 16" fill="none">'
        '<circle cx="5" cy="4" r="2.5" stroke="#58a6ff" stroke-width="1.5"/>'
        '<circle cx="11" cy="12" r="2.5" stroke="#8b5cf6" stroke-width="1.5"/>'
        '<path d="M7 5.5L9.5 10.5" stroke="#58a6ff" stroke-width="1.5" stroke-linecap="round"/>'
        '</svg>'
    )
    empty_icon = (
        '<svg width="48" height="48" viewBox="0 0 16 16">'
        '<path d="M8.878.392a1.75 1.75 0 0 0-1.756 0l-5.25 3.045A1.75 1.75 0 0 0 1 4.951v6.098c0 .624.332 1.2.872 1.514l5.25 3.045a1.75 1.75 0 0 0 1.756 0l5.25-3.045c.54-.313.872-.89.872-1.514V4.951c0-.624-.332-1.2-.872-1.514L8.878.392zM7.875 1.69l5.063 2.936L8 7.596 2.938 4.739 7.875 1.69zM2.5 5.912v5.044l4.75 2.756V8.668L2.5 5.912zm6.25 7.8 4.75-2.756V5.912L8.75 8.668v5.044z"'
        ' fill="#484f58"/></svg>'
    )
    live_dot_svg = (
        '<svg width="7" height="7" viewBox="0 0 7 7">'
        '<circle cx="3.5" cy="3.5" r="3" fill="#3fb950"/></svg>'
    )

    parts: list[str] = [
        "<!DOCTYPE html>",
        "<html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<meta http-equiv='refresh' content='15'>",
        "<title>BOUND \u00b7 Dashboard</title>",
        "<style>", _CSS,
        "</style></head><body>",
        "<header>",
        f"<div class='brand'>{bound_icon}"
        "<div><h1 style='font-size:1rem;font-weight:600'>BOUND</h1>"
        "<div class='sub'>Execution Dashboard \u00b7 v0.9.1</div></div></div>",
        "<div class='sub' style='text-align:right'>",
        f"{html_escape(store_path)}<br>{total} run{'' if total == 1 else 's'}",
        "<span id='sse-ind' class='sse-indicator'>"
        "<span class='sse-dot'></span>live</span>",
        "</div></header>",
        "<div class='container'>",
    ]

    if not summaries:
        # Empty state
        parts.append(
            f"<div class='empty-state'>"
            f"<div class='empty-icon'>{empty_icon}</div>"
            "<h2>No BOUND runs yet</h2>"
            "<p>Start your first BOUND-controlled session:</p>"
            "<p style='margin-top:8px'><code>bound run start &quot;your task&quot;</code></p>"
            "</div>"
        )
    else:
        # Stats bar
        parts.append("<div class='stats-bar'>")
        parts.append(f"<div class='stat-card total'><div class='stat-value'>{total}</div><div class='stat-label'>Total Runs</div></div>")
        parts.append(f"<div class='stat-card active'><div class='stat-value'>{active_count}</div><div class='stat-label'>Active</div></div>")
        parts.append(f"<div class='stat-card completed'><div class='stat-value'>{completed}</div><div class='stat-label'>Completed</div></div>")
        parts.append(f"<div class='stat-card failed'><div class='stat-value'>{failed}</div><div class='stat-label'>Failed / Int.</div></div>")
        parts.append("</div>")

        # --- Filter bar ---
        filter_options = [
            ("all", "All"),
            ("active", "Active"),
            ("completed", "Completed"),
            ("failed", "Failed"),
        ]
        parts.append("<div class='filter-bar'>")
        for val, label in filter_options:
            active_cls = " active" if filter_status == val else ""
            escaped_q = html_escape(search_q).replace("'", "&#39;") if search_q else ""
            q_param = f"&amp;q={escaped_q}" if search_q else ""
            parts.append(
                f"<a href='/?filter={val}{q_param}'"
                f" class='filter-btn{active_cls}'>{label}</a>"
            )
        parts.append(
            f"<form method='get' action='/' class='search-form'>"
            f"<input type='hidden' name='filter' value='{html_escape(filter_status)}'>"
            f"<input type='search' name='q' placeholder='Search tasks...'"
            f" value='{html_escape(search_q)}' class='search-input'>"
            f"</form>"
        )
        parts.append("</div>")

        # Active runs cards grouped by task
        if active_summaries:
            parts.append(
                "<div class='section-head'>"
                "<h2>Active Runs "
                "<span class='sse-status' id='sse-status'>"
                f"{live_dot_svg} live</span></h2>"
                f"<span class='count'>{active_count} active in {len(task_groups)} task{'' if len(task_groups) == 1 else 's'}</span>"
                "</div>"
            )
            parts.append("<div class='active-cards'>")
            for task_name, group_runs in sorted(task_groups.items()):
                if len(group_runs) > 1:
                    parts.append(
                        f"<div class='task-group-header'>"
                        f"<span>{html_escape(task_name[:100])}</span>"
                        f"<span class='task-group-count'>{len(group_runs)} runs</span>"
                        f"</div>"
                    )
                for s in group_runs:
                    d = decisions.get(s.run_id, {}) if decisions else {}
                    decision = d.get("decision", "—") if d else "—"
                    task_display = html_escape((s.task or "(untitled)")[:120])
                    status_str = "incomplete" if s.incomplete else sv(s.status)
                    # Use plan progress if available, otherwise fall back to step count
                    plan_step_text = (plan_progress or {}).get(s.run_id)
                    if plan_step_text:
                        step_info = plan_step_text
                    else:
                        candidate_info = f" \u00b7 candidate {s.step_count % 3 + 1}" if s.step_count > 0 else ""
                        step_info = f"{s.step_count} step{'' if s.step_count == 1 else 's'}{candidate_info}"
                    # Decision class for colored badge
                    dec_css = decision.lower() if decision in ("ACCEPT", "RETRY", "REPLAN", "ROLLBACK") else ""
                    dec_class = f" decision-{dec_css}" if dec_css else ""
                    run_id_short = html_escape(_short_id(s.run_id, 20))

                    parts.append(
                        f"<a href='/run/{html_escape(s.run_id)}' class='active-card'>"
                        f"<div class='card-top'>"
                        f"<div class='card-task' title='{task_display}'>{task_display}</div>"
                        f"<div class='card-badges'>"
                        f"{_status_badge(status_str, _RUN_STATUS_COLORS)}"
                        f"<span class='badge{dec_class}'>{html_escape(decision)}</span>"
                        f"</div></div>"
                        f"<div class='card-step'>"
                        f"<span class='live-dot-sm'></span>"
                        f"{step_info}"
                        f"</div>"
                        f"<div class='card-footer'>"
                        f"<span class='mono'>{run_id_short}</span>"
                        f"<span>{fmt_dt(s.started_at)}</span>"
                        f"</div>"
                        f"</a>"
                    )
            parts.append("</div>")

        # Historical runs table
        if historical_summaries:
            parts.append(
                "<div class='section-head'>"
                f"<h2>Historical Runs</h2>"
                f"<span class='count'>{len(historical_summaries)} total</span>"
                "</div>"
            )
            parts.append(
                "<table class='run-table'>"
                "<thead><tr><th>Run ID</th><th>Task</th><th>Status</th>"
                "<th>Decision</th><th>Duration</th></tr></thead><tbody>"
            )
            for s in historical_summaries:
                d = decisions.get(s.run_id, {}) if decisions else {}
                decision = d.get("decision", "—") if d else "—"
                status_str = "incomplete" if s.incomplete else sv(s.status)
                task_display = html_escape((s.task or "(untitled)")[:80])
                # Duration
                if s.started_at and s.finished_at:
                    delta = s.finished_at - s.started_at
                    mins = int(delta.total_seconds() // 60)
                    secs = int(delta.total_seconds() % 60)
                    dur = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
                elif s.started_at:
                    dur = "running"
                else:
                    dur = "—"
                dec_css = decision.lower() if decision in ("ACCEPT", "RETRY", "REPLAN", "ROLLBACK") else ""
                dec_class = f" decision-{dec_css}" if dec_css else ""
                parts.append(
                    f"<tr>"
                    f"<td><a href='/run/{html_escape(s.run_id)}' class='mono'>"
                    f"{html_escape(_short_id(s.run_id, 16))}</a></td>"
                    f"<td>{task_display}</td>"
                    f"<td>{_status_badge(status_str, _RUN_STATUS_COLORS)}</td>"
                    f"<td><span class='badge{dec_class}'>{html_escape(decision)}</span></td>"
                    f"<td class='dur'>{dur}</td>"
                    f"</tr>"
                )
            parts.append("</tbody></table>")

    # SSR indicator bar
    parts.append(
        "<div class='ssr-bar' id='ssr-bar'>"
        "<span class='ssr-dot'></span>"
        "<span>local read-only \u00b7 no data leaves your machine</span>"
        "</div>"
    )

    parts.append("<div class='page-footer'>BOUND v0.9.1 \u00b7 local read-only view. No data leaves your machine.</div>")
    parts.append("</div>")

    # SSE script for connection status
    parts.append("""<script>
(function(){
  var es = new EventSource('/api/events');
  es.addEventListener('run_count', function(e){
    var ind = document.getElementById('sse-ind');
    var bar = document.getElementById('ssr-bar');
    if (ind) { ind.className = 'sse-indicator connected'; }
    if (bar) { bar.className = 'ssr-bar connected'; }
  });
  es.onerror = function(){
    var ind = document.getElementById('sse-ind');
    var bar = document.getElementById('ssr-bar');
    if (ind) { ind.className = 'sse-indicator'; }
    if (bar) { bar.className = 'ssr-bar'; }
  };
})();
</script>""")

    parts.append("</body></html>")
    return "\n".join(parts)



def _render_run_detail(log: RunLog, *, plan_snapshot: dict | None = None) -> str:
    """Render a single-run detail page with tab navigation.

    Tabs: Execution (default) | Plan | Evidence | Artifacts | Replay.
    The Execution tab shows plan progress, current action,
    latest decision with reason, and recent activity.
    When a plan_snapshot is available (from run.json metadata), the
    Execution tab renders plan phases with status and a Plan vs Reality
    diff. The Plan tab shows the full parsed plan with overlays.
    """
    run = log.run
    audit = _RunAuditIndex.from_log(log)
    run_id = html_escape(run.run_id)
    is_active = log.incomplete
    task_display = html_escape((run.task or "(untitled)")[:120])
    status_str = "incomplete" if log.incomplete else sv(run.status)

    # Duration
    duration_str = "—"
    if run.started_at:
        end = run.finished_at if run.finished_at else datetime.now(UTC)
        delta = end - run.started_at
        mins = int(delta.total_seconds() // 60)
        secs = int(delta.total_seconds() % 60)
        duration_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"

    # Latest decision from evaluations
    latest_decision = None
    latest_eval = None
    latest_outcome = None
    for ev in reversed(log.evaluations):
        if ev.decision:
            latest_decision = sv(ev.decision)
            latest_eval = ev
            break
    for oc in reversed(log.outcomes):
        if oc.decision:
            latest_outcome = oc
            break

    # Decision reason text
    reason_text = ""
    if latest_eval and hasattr(latest_eval, 'reason_code') and latest_eval.reason_code:
        rc = sv(latest_eval.reason_code)
        reason_map = {
            "ALL_CHECKS_PASSED": "All checks passed successfully",
            "ACCEPT": "Acceptance criteria met",
            "ACCEPTANCE_BELOW_THRESHOLD": "Acceptance score below threshold",
            "EVIDENCE_INSUFFICIENT": "Insufficient evidence collected",
            "COST_EXCEEDED": "Cost exceeded acceptable bounds",
            "RISK_TOO_HIGH": "Risk level too high",
        }
        reason_text = reason_map.get(rc, rc.replace("_", " ").title())

    # Next action
    next_action = ""
    if latest_outcome and hasattr(latest_outcome, 'next_action') and latest_outcome.next_action:
        next_action = sv(latest_outcome.next_action)

    # Plan progress from steps
    steps = log.steps
    current_step_idx = -1
    for i, step in enumerate(steps):
        if step.status.value == "started" or step.status.value == "in_progress":
            current_step_idx = i
            break
    if current_step_idx < 0 and steps:
        # Find last active or most recent
        for i in range(len(steps) - 1, -1, -1):
            if steps[i].status.value in ("completed", "failed"):
                current_step_idx = i
                break
        if current_step_idx < 0:
            current_step_idx = 0

    # Current action description
    current_action_text = "Run in progress"
    if steps and current_step_idx >= 0:
        st = steps[current_step_idx]
        desc = html_escape((st.description or "step")[:100])
        current_action_text = f"Step {current_step_idx + 1}/{len(steps)}: {desc}"
    elif not steps and is_active:
        current_action_text = "Waiting for agent to begin execution"
    elif not is_active and not steps:
        current_action_text = "Run finished — no steps recorded"

    # Decision icon SVG
    dec_lower = latest_decision.lower() if latest_decision else ""
    decision_icon_svg = ""
    if dec_lower == "accept":
        decision_icon_svg = (
            '<svg width="16" height="16" viewBox="0 0 16 16">'
            '<path d="M13.78 4.22a.75.75 0 0 1 0 1.06l-7.25 7.25a.75.75 0 0 1-1.06 0L2.22 9.28a.75.75 0 0 1 1.06-1.06L6 10.94l6.72-6.72a.75.75 0 0 1 1.06 0z"'
            ' fill="#3fb950"/></svg>'
        )
    elif dec_lower == "retry":
        decision_icon_svg = (
            '<svg width="16" height="16" viewBox="0 0 16 16">'
            '<path d="M1.5 8a6.5 6.5 0 0 1 11.7-3.57V3.5a.75.75 0 0 1 1.5 0v3a.75.75 0 0 1-.75.75h-3a.75.75 0 0 1 0-1.5h1.45A5 5 0 1 0 13 8a.75.75 0 0 1 1.5 0 6.5 6.5 0 0 1-13 0z"'
            ' fill="#ef6c00"/></svg>'
        )
    elif dec_lower == "replan":
        decision_icon_svg = (
            '<svg width="16" height="16" viewBox="0 0 16 16">'
            '<path d="M8 1a.75.75 0 0 1 .75.75v.766A4.502 4.502 0 0 1 12.5 7c0 2.485-2.015 4.5-4.5 4.5S3.5 9.485 3.5 7a4.502 4.502 0 0 1 3-4.205V7.5a1.5 1.5 0 0 0 3 0V2.596A.75.75 0 0 1 9.25 1.75v-.25A.25.25 0 0 1 9.5 1.75v.25A.75.75 0 0 1 8.75 1z"'
            ' fill="#8b5cf6"/></svg>'
        )
    elif dec_lower == "rollback":
        decision_icon_svg = (
            '<svg width="16" height="16" viewBox="0 0 16 16">'
            '<path d="M.75 3.75a.75.75 0 0 1 1.5 0v4.69l3.22-3.22a.75.75 0 0 1 1.06 1.06l-4.5 4.5a.75.75 0 0 1-1.06 0l-4.5-4.5a.75.75 0 0 1 1.06-1.06l3.22 3.22V3.75z"'
            ' fill="#f85149"/></svg>'
        )

    # Spinner SVG for current action
    spinner_svg = (
        '<svg width="20" height="20" viewBox="0 0 16 16">'
        '<circle cx="8" cy="8" r="6" stroke="#30363d" stroke-width="2" fill="none"/>'
        '<path d="M8 2a6 6 0 0 1 6 6" stroke="#58a6ff" stroke-width="2" fill="none" stroke-linecap="round"/>'
        '</svg>'
    )

    # Compact recent activity (last 5 events)
    activity_rows: list[str] = []
    for ev in log.events[-5:]:
        ev_type = getattr(ev, 'event', 'unknown')
        ts = getattr(ev, 'timestamp', None)
        time_str = ts.strftime("%H:%M:%S") if ts else "--:--:--"
        kind = ev_type.replace("_", " ").replace("event", "").strip()[:20]
        # Extract a short summary
        summary = ""
        if hasattr(ev, 'description'):
            summary = str(ev.description)[:80]
        elif hasattr(ev, 'decision'):
            summary = str(ev.decision)[:80]
        elif hasattr(ev, 'task'):
            summary = str(ev.task)[:80]
        activity_rows.append(
            f"<div class='activity-item'>"
            f"<span class='act-time'>{time_str}</span>"
            f"<span class='act-kind'>{html_escape(kind)}</span>"
            f"<span class='act-text'>{html_escape(summary)}</span>"
            f"</div>"
        )

    # --- Timeline rows for Replay tab ---
    timeline_rows: list[str] = []
    for ev in log.events:
        ev_type = getattr(ev, 'event', 'unknown')
        ts = getattr(ev, 'timestamp', None)
        time_str = ts.strftime("%H:%M:%S") if ts else "--:--:--"
        kind = ev_type.replace("_", " ").replace("event", "").strip()[:25]
        detail = ""
        for attr in ('description', 'decision', 'task', 'step_id', 'reason_code'):
            val = getattr(ev, attr, None)
            if val:
                detail = str(val)[:100]
                break
        # Event type badge color
        ev_color = "#30363d"
        if "started" in ev_type or "activated" in ev_type:
            ev_color = "#1f6feb"
        elif "completed" in ev_type or "finished" in ev_type:
            ev_color = "#2e7d32"
        elif "failed" in ev_type or "error" in ev_type:
            ev_color = "#c62828"
        elif "gated" in ev_type or "decision" in ev_type:
            ev_color = "#8b5cf6"
        elif "collected" in ev_type:
            ev_color = "#1976d2"
        elif "evaluation" in ev_type or "recorded" in ev_type:
            ev_color = "#ef6c00"
        elif "outcome" in ev_type:
            ev_color = "#6a1b9a"
        elif "checkpoint" in ev_type:
            ev_color = "#3fb950"
        timeline_rows.append(
            f"<div class='tl-entry'>"
            f"<span class='tl-time'>{time_str}</span>"
            f"<span class='badge' style='background:{ev_color};font-size:0.6rem;padding:1px 5px'>"
            f"{html_escape(kind)}</span>"
            f"<span class='tl-text'>{html_escape(detail)}</span>"
            f"</div>"
        )

    # --- Sidebar info ---
    cfg = run.config
    policy_id = html_escape(str(cfg.policy_id)) if cfg and cfg.policy_id else "&mdash;"
    policy_ver = html_escape(str(cfg.policy_version)) if cfg and cfg.policy_version else "&mdash;"
    policy_hash = html_escape(str(cfg.policy_hash)[:20]) if cfg and cfg.policy_hash else "&mdash;"
    if cfg:
        ws = getattr(cfg, 'workspace', None)
        workspace = html_escape(str(ws)) if ws else "&mdash;"
    else:
        workspace = "&mdash;"

    checkpoint_ids: list[str] = []
    for ev in log.events:
        ev_event = getattr(ev, 'event', '')
        if ev_event and 'checkpoint' in str(ev_event).lower():
            checkpoint_ids.append(getattr(ev, 'event_id', '?'))
    if not checkpoint_ids:
        checkpoint_ids.append("none recorded")

    artifact_count = len(log.events)

    # Evidence summary
    all_collected = [e for evs in audit.collected.values() for e in evs]
    verified_count = sum(1 for e in all_collected if e.provenance in INDEPENDENTLY_VERIFIED)
    total_evidence = len(all_collected)
    failures_count = sum(len(evs) for evs in audit.failures.values())

    # --- Build page ---
    bound_icon = (
        '<svg width="18" height="18" viewBox="0 0 16 16" fill="none">'
        '<circle cx="5" cy="4" r="2.5" stroke="#58a6ff" stroke-width="1.5"/>'
        '<circle cx="11" cy="12" r="2.5" stroke="#8b5cf6" stroke-width="1.5"/>'
        '<path d="M7 5.5L9.5 10.5" stroke="#58a6ff" stroke-width="1.5" stroke-linecap="round"/>'
        '</svg>'
    )

    parts: list[str] = [
        "<!DOCTYPE html>",
        "<html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>BOUND run {html_escape(_short_id(run.run_id, 20))}</title>",
        "<style>", _CSS,
        "</style>",
        "</head><body>",
        "<header>",
        f"<div class='brand'>{bound_icon}"
        f"<h1 style='font-size:1rem;font-weight:600'>BOUND run detail</h1>"
        "<div class='sub'>local lineage \u00b7 read-only</div></div>",
        (
            f"<div class='sub' id='header-run-id'>"
            f"{html_escape(_short_id(run.run_id, 20))}"
            f"<span id='sse-ind-detail' class='sse-indicator'>"
            f"<span class='sse-dot'></span>live</span>"
            f"</div>"
        ),
        "</header>",
        "<div class='container'>",
        f"<div class='back-nav'><a href='/'>{_icon('back_arrow', w=14, h=14)} back to runs</a></div>",
    ]

    # --- Run detail header ---
    parts.append("<div class='run-detail-header'>")
    parts.append(
        f"<h2>{task_display}"
        f"{_status_badge(status_str, _RUN_STATUS_COLORS)}"
        f"<span class='duration-display'>{duration_str}</span>"
        f"</h2>"
    )
    parts.append("</div>")

    # --- Tab navigation ---
    parts.append(
        "<nav class='tab-nav'>"
        "<button class='tab-btn active' data-tab='execution'>Execution</button>"
        "<button class='tab-btn' data-tab='plan'>Plan</button>"
        "<button class='tab-btn' data-tab='evidence'>Evidence</button>"
        "<button class='tab-btn' data-tab='artifacts'>Artifacts</button>"
        "<button class='tab-btn' data-tab='replay'>Replay</button>"
        "</nav>"
    )

    # ================================================================
    # TAB: Execution (default active)
    # ================================================================
    parts.append("<div class='tab-panel active' id='tab-execution'>")

    # Parse plan snapshot into structured data when available
    plan_steps: list[dict] = []
    plan_goal: str | None = None
    plan_source: str | None = None
    if plan_snapshot:
        plan_steps = plan_snapshot.get("steps", [])
        plan_goal = plan_snapshot.get("goal")
        plan_source = plan_snapshot.get("source_path")

    # -- Plan Section (from plan.md snapshot) --
    if plan_steps:
        _render_plan_section(parts, plan_steps, plan_goal, plan_source,
                             steps, is_active)

    # Plan Progress circles (from runtime step events)
    if steps and not plan_steps:
        parts.append("<div class='plan-progress'>")
        for i, step in enumerate(steps):
            desc = html_escape((step.description or f"Step {i+1}")[:20])
            status_v = step.status.value if hasattr(step.status, 'value') else str(step.status)
            circle_class = ""
            if status_v == "completed":
                circle_class = " completed"
            elif status_v == "failed":
                circle_class = " failed"
            elif i == current_step_idx and is_active:
                circle_class = " active"
            label_class = " active" if i == current_step_idx else ""

            parts.append("<div class='plan-step'>")
            parts.append(
                f"<div class='plan-step-circle{circle_class}'"
                f" title='{desc}'>"
                f"{i + 1}</div>"
            )
            parts.append(f"<span class='plan-step-label{label_class}'>{desc}</span>")
            parts.append("</div>")
            if i < len(steps) - 1:
                connector_class = " completed" if status_v == "completed" else ""
                parts.append(f"<div class='plan-connector{connector_class}'></div>")
        parts.append("</div>")
    elif steps:
        # Runtime steps exist but plan snapshot doesn't — show compact
        parts.append("<div class='plan-progress'>")
        for i, step in enumerate(steps[:8]):
            desc = html_escape((step.description or f"Step {i+1}")[:20])
            status_v = step.status.value if hasattr(step.status, 'value') else str(step.status)
            circle_class = ""
            if status_v == "completed":
                circle_class = " completed"
            elif status_v == "failed":
                circle_class = " failed"
            elif i == current_step_idx and is_active:
                circle_class = " active"
            label_class = " active" if i == current_step_idx else ""
            parts.append("<div class='plan-step'>")
            parts.append(
                f"<div class='plan-step-circle{circle_class}'"
                f" title='{desc}'>"
                f"{i + 1}</div>"
            )
            parts.append(f"<span class='plan-step-label{label_class}'>{desc}</span>")
            parts.append("</div>")
            if i < min(len(steps), 8) - 1:
                connector_class = " completed" if status_v == "completed" else ""
                parts.append(f"<div class='plan-connector{connector_class}'></div>")
        parts.append("</div>")

    # Plan vs Reality diff (when plan_snapshot + runtime steps both exist)
    if plan_steps and steps:
        _render_plan_vs_reality(parts, plan_steps, steps, log)

    # Current action
    action_now = "In progress" if is_active else "Last action"
    parts.append("<div class='current-action'>")
    parts.append(f"<div class='action-spinner'>{spinner_svg if is_active else ''}</div>")
    parts.append("<div class='action-info'>")
    parts.append(f"<div class='action-now'>{action_now}</div>")
    parts.append(f"<div class='action-desc'>{current_action_text}</div>")
    if steps and current_step_idx >= 0:
        current_step = steps[current_step_idx]
        step_status_str = sv(current_step.status) if hasattr(current_step, 'status') else "unknown"
        parts.append(
            f"<div class='action-step'>"
            f"Status: {step_status_str} \u00b7 "
            f"{len(current_step.attempts) if hasattr(current_step, 'attempts') else 0} attempt(s)"
            f"</div>"
        )
    parts.append("</div></div>")

    # Decision panel
    if latest_decision:
        dec_css_class = dec_lower if dec_lower in ("accept", "retry", "replan", "rollback") else ""
        parts.append("<div class='decision-panel'>")
        parts.append(f"<div class='decision-icon {dec_css_class}'>{decision_icon_svg}</div>")
        parts.append("<div class='decision-body'>")
        parts.append("<div class='decision-header'>")
        parts.append("<span class='decision-label'>Latest Decision</span>")
        dec_badge_class = f" decision-{dec_lower}" if dec_lower else ""
        parts.append(f"<span class='badge{dec_badge_class}'>{html_escape(latest_decision)}</span>")
        parts.append("</div>")
        if reason_text:
            parts.append(f"<div class='decision-reason'>{html_escape(reason_text)}</div>")
        if next_action:
            parts.append(
                f"<div class='decision-next'>"
                f"<svg width='12' height='12' viewBox='0 0 16 16'>"
                f"<path d='M5.5 3.5a.75.75 0 0 1 1.06 0l4.25 4.25a.75.75 0 0 1 0 1.06l-4.25 4.25a.75.75 0 0 1-1.06-1.06L9.22 8 5.5 4.56a.75.75 0 0 1 0-1.06z' fill='#58a6ff'/></svg>"
                f"Next: {html_escape(next_action)}"
                f"</div>"
            )
        # Score info if available
        if latest_eval and hasattr(latest_eval, 'score') and latest_eval.score is not None:
            score_pct = f"{float(latest_eval.score) * 100:.0f}%"
            parts.append(
                f"<div style='margin-top:4px;font-size:0.72rem;color:#8b949e'>"
                f"Score: {score_pct} (threshold: "
                f"{float(latest_eval.threshold) * 100:.0f}%)"
                f"</div>"
            )
        parts.append("</div></div>")
    elif not steps and is_active:
        parts.append(
            "<div class='decision-panel'>"
            "<div class='decision-body'>"
            "<div class='decision-label'>Waiting for first evaluation</div>"
            "<div class='decision-reason'>"
            "The agent has not yet produced any evaluation. "
            "This page will update when step execution begins.</div>"
            "</div></div>"
        )

    # Compact recent activity
    if activity_rows:
        parts.append("<div class='recent-activity'>")
        parts.append("<h3>Recent Activity</h3>")
        parts.append("<div class='activity-list'>")
        parts.extend(activity_rows)
        parts.append("</div></div>")

    # Raw lineage collapsed
    parts.append(
        "<details class='raw-lineage'>"
        "<summary>"
        f"Raw lineage ({len(log.events)} event(s), "
        f"{log.corrupt_lines} corrupt, "
        f"{'truncated' if log.truncated else 'complete'})"
        "</summary>"
        "<pre>"
    )
    for ev in log.events:
        try:
            if hasattr(ev, "model_dump"):
                line = json.dumps(ev.model_dump(mode="json"), default=str)
            else:
                line = json.dumps(ev, default=str)
        except (TypeError, ValueError):
            line = str(ev)
        parts.append(html_escape(line))
    parts.append("</pre></details>")

    # Plan vs Reality diff already rendered above when plan_snapshot exists.
    # For backward compat: show replan section when no plan_snapshot but replan detected
    has_replan = not plan_steps and any(
        getattr(ev, 'decision', '') == 'REPLAN' or 'replan' in str(getattr(ev, 'event', '')).lower()
        for ev in log.events
    )
    if has_replan:
        parts.append("<div class='replan-section' style='margin-top:16px;background:#161b22;"
                     "border:1px solid #30363d;border-radius:8px;padding:14px 16px'>")
        parts.append("<h3 style='font-size:0.82rem;color:#8b5cf6;margin-bottom:8px'>"
                     f"{_icon('decision_replan', w=14, h=14)} Replan Detected</h3>")
        parts.append("<p style='font-size:0.75rem;color:#8b949e;margin-bottom:10px'>"
                     "The original plan was modified during execution. "
                     "See the Replay tab for full event timeline.</p>")
        # Show which steps were affected
        replan_steps = [
            ev for ev in log.events
            if getattr(ev, 'decision', '') == 'REPLAN'
            or 'replan' in str(getattr(ev, 'event', '')).lower()
        ]
        for rev in replan_steps[:5]:
            step_id = getattr(rev, 'step_id', '') or getattr(rev, 'event_id', '') or 'unknown'
            reason = getattr(rev, 'reason_code', '') or getattr(rev, 'description', '') or ''
            parts.append(
                f"<div class='replan-step' style='display:flex;align-items:center;gap:8px;"
                f"padding:6px 8px;background:#0d1117;border-radius:4px;margin-bottom:4px;"
                f"font-size:0.75rem'>"
                f"<span>{_icon('diff_modified', w=12, h=12)}</span>"
                f"<code style='color:#8b5cf6'>{html_escape(str(step_id))}</code>"
                + (f"<span style='color:#8b949e'>{html_escape(str(reason)[:100])}</span>"
                   if reason else "")
                + "</div>"
            )
        parts.append("</div>")

    parts.append("</div>")  # end tab-execution

    # ================================================================
    # TAB: Plan
    # ================================================================
    parts.append("<div class='tab-panel' id='tab-plan'>")
    if plan_steps:
        _render_plan_tab(parts, plan_steps, plan_goal, plan_source, steps, log)
    else:
        parts.append(
            "<div style='text-align:center;padding:40px 20px;color:#8b949e'>"
            "<div style='margin-bottom:12px;opacity:0.3'>"
            f"{_icon('run', w=32, h=32)}</div>"
            "<h3 style='font-size:1rem;color:#e6edf3;margin-bottom:4px'>No Plan Loaded</h3>"
            "<p style='font-size:0.78rem'>This run was started without a plan.md file. "
            "Use <code>bound ui --plan plan.md</code> to pre-load a plan, or start a "
            "new run with <code>load_plan_snapshot()</code>.</p>"
            "</div>"
        )
    parts.append("</div>")  # end tab-plan

    # ================================================================
    # TAB: Evidence
    # ================================================================
    parts.append("<div class='tab-panel' id='tab-evidence'>")

    # Evidence summary bar
    if total_evidence > 0:
        verified_pct = int(verified_count / total_evidence * 100) if total_evidence > 0 else 0
        parts.append(
            "<div class='evidence-bar'>"
            f"<div class='ev-group'>"
            f"<span class='ev-name'>Evidence</span>"
            f"<span class='ev-pct'>{verified_count}/{total_evidence} verified</span>"
            f"<div class='ev-progress'><div class='ev-progress-fill'"
            f" style='width:{verified_pct}%;background:#2e7d32'></div></div>"
            f"</div>"
            + (f"<div class='ev-group'><span class='risk-badge' style='background:#c62828'>"
               f"{failures_count} failure(s)</span></div>" if failures_count else "")
            + "</div>"
        )

    # Verification checks list
    if all_collected:
        parts.append("<h3 style='font-size:0.8rem;color:#8b949e;margin:12px 0 6px'>"
                     "Verification Checks</h3>")
        parts.append("<div class='ev-checks'>")
        for ev in all_collected[:50]:
            check_id = html_escape(str(ev.check_id) if hasattr(ev, 'check_id') and ev.check_id
                                   else (getattr(ev, 'source', None) or "unnamed"))
            status_str = (ev.status.value if hasattr(ev.status, 'value') else str(ev.status)).lower()
            prov_str = (ev.provenance.value if hasattr(ev.provenance, 'value') else str(ev.provenance)).lower()
            # Status icon
            if status_str in ("verified", "passed", "true", "ok"):
                status_icon = _icon("evidence_passed")
                status_css = "ev-pass"
            elif status_str in ("failed", "false", "invalid"):
                status_icon = _icon("evidence_failed")
                status_css = "ev-fail"
            else:
                status_icon = _icon("evidence_missing")
                status_css = "ev-miss"
            # Provenance badge
            if prov_str == "verified":
                pass  # provenance: verified
                prov_color = "#2e7d32"
            elif prov_str == "claimed":
                pass  # provenance: claimed
                prov_color = "#ef6c00"
            else:
                pass  # provenance: unverified
                prov_color = "#9e9e9e"
            parts.append(
                f"<div class='ev-check-row {status_css}'>"
                f"<span class='ev-check-icon'>{status_icon}</span>"
                f"<span class='ev-check-label'>{check_id}</span>"
                f"<span class='badge' style='background:{prov_color};font-size:0.6rem'>"
                f"{html_escape(prov_str)}</span>"
                f"</div>"
            )
        if len(all_collected) > 50:
            parts.append(
                f"<div style='color:#8b949e;font-size:0.72rem;padding:8px'>"
                f"... and {len(all_collected) - 50} more checks</div>"
            )
        parts.append("</div>")

    # Score breakdown panel (from latest evaluation)
    if latest_eval and hasattr(latest_eval, 'scores') and latest_eval.scores:
        scores = latest_eval.scores
        acceptance = getattr(scores, 'acceptance', None)
        influence = getattr(scores, 'influence', None)
        risk = getattr(scores, 'risk', None)
        cost = getattr(scores, 'cost', None)
        threshold = getattr(latest_eval, 'threshold', None)
        parts.append("<h3 style='font-size:0.8rem;color:#8b949e;margin:16px 0 6px'>"
                     "Score Breakdown (A/I/R/C)</h3>")
        parts.append("<div class='score-grid'>")
        for label, val, color in [
            ("Acceptance", acceptance, "#3fb950"),
            ("Influence", influence, "#58a6ff"),
            ("Risk", risk, "#f85149"),
            ("Cost", cost, "#d29922"),
        ]:
            if val is not None:
                pct = int(val * 100) if isinstance(val, (int, float)) else 0
                parts.append(
                    f"<div class='score-item'>"
                    f"<span class='score-label'>{label}</span>"
                    f"<div class='score-bar-bg'>"
                    f"<div class='score-bar-fill' style='width:{pct}%;background:{color}'></div>"
                    f"</div>"
                    f"<span class='score-val'>{pct}%</span>"
                    f"</div>"
                )
        if threshold is not None:
            t_val = int(threshold * 100) if isinstance(threshold, (int, float)) else threshold
            parts.append(
                f"<div class='score-threshold'>Threshold: {t_val}%</div>"
            )
        parts.append("</div>")

    # Collector details
    parts.append("<h3 style='font-size:0.8rem;color:#8b949e;margin:16px 0 6px'>"
                 "Collectors</h3>")
    parts.append("<div class='collector-list'>")
    seen_collectors = set()
    for ev in all_collected:
        coll = getattr(ev, 'collector', None) or getattr(ev, 'source', None) or "unknown"
        if coll not in seen_collectors:
            seen_collectors.add(coll)
            coll_ver = getattr(ev, 'collector_version', None) or ""
            parts.append(
                f"<div class='collector-row'>"
                f"{_icon('run', w=14, h=14)} "
                f"<span class='collector-name'>{html_escape(str(coll))}</span>"
                + (f"<span class='collector-ver'>v{html_escape(str(coll_ver))}</span>"
                   if coll_ver else "")
                + "</div>"
            )
    if not seen_collectors:
        parts.append("<div class='collector-row' style='color:#484f58'>"
                     "No collectors recorded</div>")
    parts.append("</div>")

    # Missing evidence empty state
    if total_evidence == 0:
        parts.append(
            "<div style='text-align:center;padding:40px 20px;color:#8b949e'>"
            f"<div style='margin-bottom:12px;opacity:0.3'>{_icon('shield', w=32, h=32)}</div>"
            "<h3 style='font-size:1rem;color:#e6edf3;margin-bottom:4px'>No Evidence Yet</h3>"
            "<p style='font-size:0.78rem'>Evidence will appear here after collection runs.</p>"
            "</div>"
        )

    parts.append("</div>")  # end tab-evidence

    # ================================================================
    # TAB: Artifacts
    # ================================================================
    parts.append("<div class='tab-panel' id='tab-artifacts'>")

    # Checkpoints section
    parts.append("<h3 style='font-size:0.8rem;color:#8b949e;margin-bottom:8px'>"
                 f"{_icon('checkpoint', w=14, h=14)} Checkpoints ({len(checkpoint_ids)})</h3>")
    if checkpoint_ids and checkpoint_ids[0] != "none recorded":
        parts.append("<div class='checkpoint-list'>")
        for cpid in checkpoint_ids[:10]:
            parts.append(
                f"<div class='cp-row'>"
                f"<span class='cp-icon'>{_icon('checkpoint', w=14, h=14)}</span>"
                f"<code class='cp-id'>{html_escape(str(cpid))}</code>"
                f"</div>"
            )
        parts.append("</div>")
    else:
        parts.append("<div class='cp-row' style='color:#484f58;padding:8px;font-size:0.75rem'>"
                     "No checkpoints recorded</div>")

    # Workspace / worktree info
    parts.append("<h3 style='font-size:0.8rem;color:#8b949e;margin:16px 0 8px'>"
                 f"{_icon('artifact', w=14, h=14)} Workspace</h3>")
    parts.append("<div class='ws-info'>")
    parts.append(
        f"<div class='ws-row'><span class='ws-label'>Path</span>"
        f"<code>{workspace}</code></div>"
    )
    parts.append(
        f"<div class='ws-row'><span class='ws-label'>Policy</span>"
        f"<span>{policy_id} @ {policy_ver}</span></div>"
    )
    parts.append(
        f"<div class='ws-row'><span class='ws-label'>Policy hash</span>"
        f"<code>{policy_hash}</code></div>"
    )
    parts.append("</div>")

    # Generated files / events
    parts.append("<h3 style='font-size:0.8rem;color:#8b949e;margin:16px 0 8px'>"
                 f"{_icon('run', w=14, h=14)} Events ({artifact_count})</h3>")
    if log.events:
        parts.append("<div class='event-file-list'>")
        seen_types: set[str] = set()
        for ev in log.events[:20]:
            ev_type = getattr(ev, 'event', 'unknown')
            if ev_type not in seen_types:
                seen_types.add(ev_type)
                parts.append(
                    f"<div class='ef-row'>"
                    f"<span class='badge' style='background:#30363d;color:#8b949e'>"
                    f"{html_escape(ev_type)}</span>"
                    f"</div>"
                )
        parts.append("</div>")
    else:
        parts.append("<div style='color:#484f58;padding:8px;font-size:0.75rem'>"
                     "No events recorded</div>")

    # Diff/patch placeholder
    parts.append("<h3 style='font-size:0.8rem;color:#8b949e;margin:16px 0 8px'>"
                 "Diffs & Patches</h3>")
    parts.append("<div style='color:#484f58;padding:8px;font-size:0.75rem'>"
                 "Diffs will appear here when checkpoints with artifact diffs are available.</div>")

    parts.append("</div>")  # end tab-artifacts

    # ================================================================
    # TAB: Replay
    # ================================================================
    parts.append("<div class='tab-panel' id='tab-replay'>")
    parts.append("<div class='detail-body'>")
    parts.append("<div class='timeline-panel' id='timeline-panel'>")
    parts.append("<div class='timeline' id='timeline'>")
    parts.append(
        "<div class='timeline-header'>"
        "<span>Live Timeline</span>"
        "<button class='jump-btn' id='jump-btn' onclick='jumpToLive()' "
        "title='Jump to latest'>"
        f"{_icon('jump_down', w=14, h=14)} Jump to live</button>"
        "</div>"
    )
    parts.append("<div id='timeline-items'>")
    parts.extend(timeline_rows)
    if is_active and not steps:
        parts.append(
            "<div class='tl-entry tl-waiting' style='display:flex;align-items:center;gap:8px;"
            "padding:10px 0;color:#8b949e;font-style:italic'>"
            "Waiting for agent to begin execution..."
            "</div>"
        )
    parts.append("</div></div>")  # close timeline
    parts.append("</div>")  # close timeline-panel

    # Sidebar
    parts.append("<div class='sidebar-panel' id='sidebar-panel'>")
    parts.append("<div class='sidebar'>")
    parts.append(
        f"<div class='sidebar-header' onclick='toggleSidebar()'>"
        f"<span>Run Details</span>"
        f"<span class='toggle-icon' id='toggle-icon'>{_icon('collapse_down')}</span>"
        f"</div>"
    )
    parts.append("<div class='sidebar-body' id='sidebar-body'>")
    parts.append("<dl>")
    parts.append(f"<dt>Policy</dt><dd>{policy_id} @ {policy_ver}</dd>")
    parts.append(f"<dt>Policy hash</dt><dd><code>{policy_hash}</code></dd>")
    parts.append(f"<dt>Workspace</dt><dd>{workspace}</dd>")
    parts.append(
        f"<dt>Checkpoints</dt>"
        f"<dd>{html_escape(', '.join(checkpoint_ids[:3]))}</dd>"
    )
    parts.append(f"<dt>Artifacts</dt><dd>{artifact_count} event(s)</dd>")
    parts.append(f"<dt>Run ID</dt><dd><code>{html_escape(run_id)}</code></dd>")
    parts.append(
        f"<dt>Evidence</dt><dd>{verified_count}/{total_evidence} verified"
        + (f" \u00b7 {failures_count} failure(s)" if failures_count else "")
        + "</dd>"
    )
    parts.append("</dl>")
    parts.append("</div>")  # sidebar-body
    parts.append("</div>")  # sidebar
    parts.append("</div>")  # sidebar-panel
    parts.append("</div>")  # detail-body

    # Raw lineage in replay tab
    parts.append(
        "<details class='raw-lineage'>"
        "<summary>"
        f"Raw lineage ({len(log.events)} event(s), "
        f"{log.corrupt_lines} corrupt, "
        f"{'truncated' if log.truncated else 'complete'})"
        "</summary>"
        "<pre>",
    )
    for ev in log.events:
        try:
            if hasattr(ev, "model_dump"):
                line = json.dumps(ev.model_dump(mode="json"), default=str)
            else:
                line = json.dumps(ev, default=str)
        except (TypeError, ValueError):
            line = str(ev)
        parts.append(html_escape(line))
    parts.append("</pre></details>")
    parts.append("</div>")  # end tab-replay

    # Footer
    parts.append(
        "<div class='page-footer'>"
        "BOUND dashboard \u00b7 local read-only view. "
        "No data leaves your machine.</div>",
    )

    # ================================================================
    # JavaScript
    # ================================================================
    js_is_active = "true" if is_active else "false"
    parts.append(f"""<script>
(function(){{
  var isActive = {js_is_active};
  var runId = "{run_id}";
  var lastEventCount = {len(log.events)};
  var sidebarCollapsed = false;

  // Tab switching
  var tabs = document.querySelectorAll('.tab-btn');
  var panels = document.querySelectorAll('.tab-panel');

  function activateTab(tabName) {{
    tabs.forEach(function(t) {{ t.classList.remove('active'); }});
    panels.forEach(function(p) {{ p.classList.remove('active'); }});
    var btn = document.querySelector('[data-tab="' + tabName + '"]');
    var panel = document.getElementById('tab-' + tabName);
    if (btn) btn.classList.add('active');
    if (panel) panel.classList.add('active');
    if (history.replaceState) {{
      history.replaceState(null, '', '#execution' === tabName ? ' ' : '#' + tabName);
    }}
  }}

  tabs.forEach(function(btn) {{
    btn.addEventListener('click', function() {{
      activateTab(btn.getAttribute('data-tab'));
    }});
  }});

  // Check URL hash on load
  var hash = window.location.hash.replace('#', '');
  if (hash && document.getElementById('tab-' + hash)) {{
    activateTab(hash);
  }}

  // SSE connection indicator
  var esDetail = new EventSource('/api/events');
  esDetail.addEventListener('run_count', function(e){{
    var ind = document.getElementById('sse-ind-detail');
    if (ind) {{ ind.className = 'sse-indicator connected'; }}
  }});
  esDetail.onerror = function(){{
    var ind = document.getElementById('sse-ind-detail');
    if (ind) {{ ind.className = 'sse-indicator'; }}
  }};

  function jumpToLive() {{
    var items = document.getElementById('timeline-items');
    if (items && items.lastElementChild) {{
      items.lastElementChild.scrollIntoView(
        {{ behavior: 'smooth', block: 'end' }}
      );
    }}
    var jumpBtn = document.getElementById('jump-btn');
    if (jumpBtn) jumpBtn.style.display = 'none';
  }}

  function toggleSidebar() {{
    var body = document.getElementById('sidebar-body');
    var icon = document.getElementById('toggle-icon');
    var tlPanel = document.getElementById('timeline-panel');
    sidebarCollapsed = !sidebarCollapsed;
    if (sidebarCollapsed) {{
      body.style.display = 'none';
      icon.classList.add('collapsed');
      tlPanel.classList.add('full-width');
    }} else {{
      body.style.display = 'block';
      icon.classList.remove('collapsed');
      tlPanel.classList.remove('full-width');
    }}
  }}

  // Auto-scroll detection for timeline
  var timeline = document.getElementById('timeline');
  if (timeline) {{
    timeline.addEventListener('scroll', function() {{
      var dist = (
        timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight
      );
      var jumpBtn = document.getElementById('jump-btn');
      if (jumpBtn) {{
        jumpBtn.style.display = dist < 40 ? 'none' : 'inline-block';
      }}
    }});
    timeline.scrollTop = timeline.scrollHeight;
  }}

  // Poll for updates on active runs
  function pollRun() {{
    if (!isActive) return;
    fetch('/api/run/' + runId)
      .then(function(r) {{ return r.json(); }})
      .then(function(data) {{
        if (data.error) return;
        var newCount = data.event_count || 0;
        if (newCount > lastEventCount) {{
          location.reload();
        }}
        lastEventCount = newCount;
      }})
      .catch(function(){{}});
  }}

  if (isActive) {{
    setInterval(pollRun, 2000);
    setTimeout(function() {{
      if (timeline) timeline.scrollTop = timeline.scrollHeight;
    }}, 100);
  }}

  setTimeout(function() {{
    if (timeline) timeline.scrollTop = timeline.scrollHeight;
  }}, 200);
}})();
</script>""")

    parts.append("</div></body></html>")
    return "\n".join(parts)


class _DashboardHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the BOUND dashboard.

    Class attributes (set before serving):
        lineage_store: Optional pre-configured :class:`LineageStore`.
            Falls back to :func:`get_default_store` when ``None``.
        startup_redirect: Optional run id to redirect ``/`` to
            ``/run/<run_id>`` on first request (set once at startup).
    """

    lineage_store: LineageStore | None = None
    startup_redirect: str | None = None
    plan_path_override: str | None = None

    # In-memory cache for hot runs — avoids re-reading events.jsonl on every request
    _log_cache: dict[str, RunLog] = {}
    _MAX_LOG_CACHE = 20

    # Quiet the default access-log spam; only log at DEBUG
    def log_message(self, fmt: str, *args: object) -> None:
        logger.debug(fmt, *args)

    def handle(self) -> None:
        """Handle one request, suppressing connection-drop noise."""
        with suppress(ConnectionResetError, BrokenPipeError, OSError):
            super().handle()

    def _send_html(self, html: str, status: int = 200) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, data: object, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def _send_404(self, message: str = "Not found") -> None:
        self._send_html(
            f"<!DOCTYPE html><html><body>"
            f"<h1>404</h1><p>{html_escape(message)}</p>"
            f"<p><a href='/'>back to dashboard</a></p>"
            f"</body></html>",
            status=404,
        )

    def _send_error(self, status: int, message: str) -> None:
        self._send_html(
            f"<!DOCTYPE html><html><body>"
            f"<h1>{status}</h1><p>{html_escape(message)}</p>"
            f"<p><a href='/'>back to dashboard</a></p>"
            f"</body></html>",
            status=status,
        )

    def do_GET(self) -> None:
        """Dispatch GET requests."""
        path = self.path.split("?", 1)[0].rstrip("/")
        # Handle startup redirect: if a run_id was requested on the CLI, the
        # overview page redirects to that run's detail page on first visit.
        redirect = type(self).startup_redirect
        if redirect is not None and (path == "" or path == "/"):
            type(self).startup_redirect = None  # one-shot
            self.send_response(302)
            self.send_header("Location", f"/run/{redirect}")
            self.end_headers()
            return
        try:
            if path == "" or path == "/":
                self._handle_overview()
            elif path.startswith("/run/"):
                run_id = path[len("/run/") :]
                self._handle_run_detail(run_id)
            elif path == "/api/runs":
                self._handle_api_runs()
            elif path.startswith("/api/run/"):
                run_id = path[len("/api/run/") :]
                self._handle_api_run(run_id)
            elif path == "/api/events":
                self._handle_api_events()
            else:
                self._send_404(f"Unknown path: {path}")
        except Exception as exc:
            logger.exception("Error handling %s", path)
            self._send_error(500, f"Internal error: {exc}")

    # --- Store access ---

    @property
    def _store(self) -> LineageStore:
        """Get or initialise the lineage store.

        Uses :attr:`lineage_store` when set on the class (via
        :func:`serve`), otherwise falls back to the default store.
        """
        cached = getattr(self, "_store_cached", None)
        if cached is not None:
            return cached
        store = type(self).lineage_store or get_default_store()
        self._store_cached = store  # type: ignore[attr-defined]
        return store

    def _get_runs(self) -> list[RunSummary]:
        """List all runs from the lineage store."""
        try:
            return self._store.list_runs()
        except Exception:
            logger.exception("Failed to list runs")
            return []

    def _get_run_log(self, run_id: str) -> RunLog | None:
        """Read a single run log, using in-memory cache for hot runs."""
        if run_id in type(self)._log_cache:
            return type(self)._log_cache[run_id]
        try:
            log = self._store.read_run(run_id, strict=False)
        except RunNotFound:
            return None
        except Exception:
            logger.exception("Failed to read run %s", run_id)
            return None
        if log is not None:
            cache = type(self)._log_cache
            if len(cache) >= type(self)._MAX_LOG_CACHE:
                cache.pop(next(iter(cache)))  # evict oldest
            cache[run_id] = log
        return log

    # --- Handlers ---

    def _handle_overview(self) -> None:
        query = {}
        if "?" in (self.path or ""):
            query = {k: v[0] for k, v in parse_qs(self.path.split("?", 1)[1]).items()}
        filter_status = query.get("filter", "all")
        search_q = query.get("q", "").strip().lower()
        summaries = self._get_runs()
        # Apply filter
        if filter_status == "active":
            summaries = [s for s in summaries if s.incomplete or str(s.status).lower() == "started"]
        elif filter_status == "completed":
            summaries = [s for s in summaries if not s.incomplete and str(s.status).lower() == "completed"]
        elif filter_status == "failed":
            summaries = [s for s in summaries if not s.incomplete and str(s.status).lower() in ("failed", "interrupted")]
        # Hide empty runs (only run_started event, no real work) by default
        hide_empty = query.get("hide_empty", "1") != "0"
        if hide_empty:
            summaries = [s for s in summaries if s.event_count > 1]
        # Apply search
        if search_q:
            summaries = [s for s in summaries if search_q in (s.task or "").lower() or search_q in s.run_id.lower()]
        decisions = _get_overview_decisions(summaries, self._store)
        # Collect plan progress for active runs
        plan_progress = _collect_plan_progress(
            [s for s in summaries if s.incomplete or str(s.status).lower() == "started"],
            self._store,
        )
        html = _render_overview_page(
            summaries, str(self._store.base_dir), decisions=decisions,
            filter_status=filter_status, search_q=query.get("q", ""),
            plan_progress=plan_progress,
        )
        self._send_html(html)

    def _handle_run_detail(self, run_id: str) -> None:
        log = self._get_run_log(run_id)
        if log is None:
            self._send_404(f"Run {run_id!r} not found or corrupt")
            return
        plan = self._get_run_plan(run_id)
        html = _render_run_detail(log, plan_snapshot=plan)
        self._send_html(html)

    def _get_run_plan(self, run_id: str) -> dict | None:
        """Load plan snapshot metadata from run.json, returning None if absent."""
        import json
        meta_path = self._store._meta_path(run_id)
        if not meta_path.exists():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            return meta.get("plan_snapshot")
        except (OSError, json.JSONDecodeError):
            return None

    def _handle_api_runs(self) -> None:
        summaries = self._get_runs()
        data = [
            {
                "run_id": s.run_id,
                "task": s.task,
                "status": s.status.value if hasattr(s.status, "value") else str(s.status),
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "finished_at": s.finished_at.isoformat() if s.finished_at else None,
                "step_count": s.step_count,
                "event_count": s.event_count,
                "incomplete": s.incomplete,
            }
            for s in summaries
        ]
        self._send_json(data)

    def _handle_api_run(self, run_id: str) -> None:
        log = self._get_run_log(run_id)
        if log is None:
            self._send_json({"error": f"run {run_id!r} not found"}, status=404)
            return
        run = log.run
        data = {
            "run": run.model_dump(mode="json"),
            "steps": [s.model_dump(mode="json") for s in log.steps],
            "evaluations": [e.model_dump(mode="json") for e in log.evaluations],
            "outcomes": [o.model_dump(mode="json") for o in log.outcomes],
            "incomplete": log.incomplete,
            "event_count": len(log.events),
        }
        self._send_json(data)

    def _handle_api_events(self) -> None:
        """Server-Sent Events endpoint for live dashboard updates.

        Polls the lineage store every 5 seconds and sends a ``data:`` event
        with the current run count and a heartbeat timestamp. The browser
        can use this to auto-refresh the overview without a full page reload.
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        last_count = -1
        try:
            while True:
                try:
                    summaries = self._get_runs()
                    count = len(summaries)
                except Exception:
                    count = last_count
                now = datetime.now(UTC).isoformat()
                if count != last_count:
                    self.wfile.write(f"event: run_count\ndata: {count}\n\n".encode())
                    self.wfile.flush()
                    last_count = count
                else:
                    # Heartbeat every 5 seconds to keep the connection alive
                    self.wfile.write(f": heartbeat {now}\n\n".encode())
                    self.wfile.flush()
                time.sleep(5)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # Client disconnected, clean exit


def serve(
    *,
    port: int = DEFAULT_PORT,
    open_browser: bool = False,
    store: LineageStore | None = None,
    run_id: str | None = None,
    plan_path: str | None = None,
) -> None:
    """Start the BOUND dashboard HTTP server.

    Args:
        port: TCP port to bind to (default 8765).
        open_browser: When ``True``, attempt to open the dashboard URL in the
            default browser.
        store: Optional pre-configured lineage store. When ``None`` the default
            store (``.bound/runs/`` under CWD) is used.
        run_id: Optional run id to redirect to after startup. When set, the
            dashboard opens directly to that run's detail page.
        plan_path: Optional explicit path to a plan.md file. When set, the
            plan is pre-loaded for use across runs.
    """
    class _SilentHTTPServer(HTTPServer):
        """HTTPServer that suppresses socket-level tracebacks."""

        def handle_error(self, request: Any, client_address: Any) -> None:
            pass  # connection drops are normal for a local dashboard

    host = "127.0.0.1"
    if store is not None:
        _DashboardHandler.lineage_store = store
    if run_id is not None:
        _DashboardHandler.startup_redirect = run_id
    if plan_path is not None:
        _DashboardHandler.plan_path_override = plan_path

    try:
        server = _SilentHTTPServer((host, port), _DashboardHandler)
    except OSError as exc:
        if "in use" in str(exc).lower() or "address already in use" in str(exc).lower():
            alt_port = port + 1
            print(
                f"error: port {port} is already in use.\n"
                f"       Try a different port: bound ui --port {alt_port}\n"
                f"       Or kill the process using port {port}:\n"
                f"         lsof -ti tcp:{port} | xargs kill\n"
                f"       (the dashboard needs a free port to start)\n",
                file=sys.__stderr__,
            )
            return
        raise

    store_path = store.base_dir if store else Path(".bound/runs").resolve()
    url = f"http://{host}:{port}"

    # Warm the cache so step counts and decisions are instant
    use_store = store or _DashboardHandler.lineage_store or get_default_store()
    cached = use_store.warm_cache()
    if cached:
        print(f"Cache warmed:     {cached} run(s) indexed")

    print(f"BOUND dashboard: {url}")
    print(f"Lineage store:   {store_path}")

    if open_browser:
        try:
            target = f"{url}/run/{run_id}" if run_id else url
            webbrowser.open(target)
            print("Opened browser.")
        except Exception as exc:
            print(f"Could not open browser: {exc}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down BOUND dashboard.")
        server.server_close()
