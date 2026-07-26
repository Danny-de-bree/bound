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
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

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

    For each run summary, attempts to read the full log and extract the
    most recent evaluation's decision + gated assurance.  Falls back to
    sensible defaults when the log cannot be read (corrupt, not found).

    Args:
        summaries: Run summaries from :meth:`LineageStore.list_runs`.
        store: The lineage store to read logs from.

    Returns:
        A dict keyed by ``run_id``, each value containing ``decision``,
        ``assurance``, ``final_decision`` and ``has_decision`` (bool).
    """
    result: dict[str, dict[str, Any]] = {}
    for s in summaries:
        try:
            log = store.read_run(s.run_id, strict=False)
        except Exception:
            result[s.run_id] = {
                "decision": "—",
                "assurance": None,
                "final_decision": "—",
                "has_decision": False,
            }
            continue
        decisions = _iter_latest_decisions(log)
        if decisions:
            last = decisions[-1]
            result[s.run_id] = {
                "decision": last.get("decision", "—"),
                "assurance": last.get("assurance"),
                "final_decision": last.get("final", "—"),
                "has_decision": last.get("decision", "—") not in ("—", None),
            }
        else:
            result[s.run_id] = {
                "decision": "—",
                "assurance": None,
                "final_decision": "—",
                "has_decision": False,
            }
    return result


# =========================================================================
# CSS (inline, no external assets)
# =========================================================================

# =========================================================================
# CSS (inline, no external assets) — v0.9.0 dark theme
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
.container{max-width:1280px;margin:0 auto;padding:20px}
.empty-state{text-align:center;padding:64px 24px;color:#8b949e}
.empty-state h2{font-size:1.2rem;margin-bottom:8px;color:#e6edf3}
.empty-state p{font-size:0.85rem}
.run-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));
  gap:12px;margin-bottom:20px}
.run-card{background:#161b22;border:1px solid #30363d;border-radius:8px;
  padding:14px;transition:border-color .15s;display:block}
.run-card:hover{border-color:#58a6ff;text-decoration:none}
.run-card h3{font-size:0.9rem;margin-bottom:6px;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis;color:#e6edf3}
.run-card .meta{font-size:0.75rem;color:#8b949e;margin-bottom:6px}
.run-card .tags{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:8px}
.badge{display:inline-block;padding:3px 10px;border-radius:12px;color:#fff;
  font-size:0.7rem;font-weight:600;white-space:nowrap}
.evidence-badge{font-size:0.68rem}
.kv{color:#8b949e;font-size:0.78rem}
table.run-table{width:100%;border-collapse:collapse;background:#161b22;
  border:1px solid #30363d;border-radius:8px;overflow:hidden}
table.run-table th{background:#0d1117;text-align:left;padding:8px 10px;
  font-size:0.7rem;text-transform:uppercase;color:#8b949e;
  border-bottom:1px solid #30363d}
table.run-table td{padding:8px 10px;border-bottom:1px solid #21262d;
  font-size:0.8rem;color:#e6edf3}
table.run-table tr:last-child td{border-bottom:none}
table.run-table tr:hover{background:#1c2333}

/* Run detail — v0.9.0 timeline layout */
.back-nav{margin-bottom:12px}
.back-nav a{font-size:0.8rem;color:#58a6ff}
.run-detail-header{background:#161b22;border:1px solid #30363d;
  border-radius:8px;padding:16px 20px;margin-bottom:12px}
.run-detail-header h2{font-size:1.1rem;margin-bottom:8px;color:#e6edf3}
.run-detail-header .meta-grid{display:flex;flex-wrap:wrap;gap:6px 20px;
  font-size:0.8rem;color:#8b949e}
.run-detail-header .meta-grid .label{color:#8b949e;margin-right:4px;
  font-size:0.7rem;text-transform:uppercase}

/* Current action bar */
.action-bar{padding:12px 16px;border-radius:8px;margin-bottom:12px;
  display:flex;align-items:center;gap:10px;font-size:0.85rem;border:1px solid #30363d}
.action-bar.running{background:rgba(88,166,255,0.08);border-color:rgba(88,166,255,0.25)}
.action-bar.accepted{background:rgba(63,185,80,0.08);border-color:rgba(63,185,80,0.25)}
.action-bar.retry{background:rgba(210,153,34,0.08);border-color:rgba(210,153,34,0.25)}
.action-bar.replan{background:rgba(163,113,247,0.08);border-color:rgba(163,113,247,0.25)}
.action-bar.rollback{background:rgba(248,81,73,0.08);border-color:rgba(248,81,73,0.2)}
.action-bar .action-icon{font-size:1.4rem;line-height:1}
.action-bar .action-text{flex:1}
.action-bar .action-text .action-desc{color:#e6edf3}
.action-bar .action-text .action-ctx{color:#8b949e;font-size:0.75rem;margin-top:2px}
.live-dot{display:inline-block;width:8px;height:8px;border-radius:50%;
  background:#3fb950;animation:live-pulse 1.5s infinite;margin-right:4px}
@keyframes live-pulse{0%,100%{opacity:1;box-shadow:0 0 0 0 rgba(63,185,80,0.4)}
  50%{opacity:0.6;box-shadow:0 0 0 6px rgba(63,185,80,0)}}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.3}}
.live-label{color:#3fb950;font-size:0.7rem;font-weight:600;
  text-transform:uppercase;letter-spacing:0.5px}

/* Summary cards */
.summary-cards{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:12px}
.summary-card{background:#161b22;border:1px solid #30363d;border-radius:8px;
  padding:12px;text-align:center;border-top:3px solid #30363d}
.summary-card .card-value{font-size:1.3rem;font-weight:700;color:#e6edf3}
.summary-card .card-label{font-size:0.68rem;color:#8b949e;text-transform:uppercase;
  letter-spacing:0.5px;margin-top:2px}
.summary-card.border-blue{border-top-color:#58a6ff}
.summary-card.border-green{border-top-color:#3fb950}
.summary-card.border-amber{border-top-color:#d29922}
.summary-card.border-purple{border-top-color:#a371f7}
.summary-card.border-grey{border-top-color:#8b949e}

/* Timeline + sidebar layout */
.detail-body{display:flex;gap:14px;margin-bottom:12px}
.timeline-panel{flex:0 0 70%;min-width:0}
.sidebar-panel{flex:0 0 calc(30% - 14px);min-width:0}
.timeline-panel.full-width{flex:0 0 100%}

/* Timeline */
.timeline{background:#161b22;border:1px solid #30363d;border-radius:8px;
  overflow:hidden;max-height:500px;overflow-y:auto}
.timeline-header{padding:9px 14px;background:#0d1117;border-bottom:1px solid #30363d;
  display:flex;align-items:center;justify-content:space-between;
  font-size:0.75rem;font-weight:600;color:#8b949e;text-transform:uppercase;
  letter-spacing:0.5px;position:sticky;top:0;z-index:2}
.timeline-header .jump-btn{font-size:0.68rem;background:#21262d;color:#58a6ff;
  border:1px solid #30363d;border-radius:4px;padding:3px 8px;cursor:pointer;
  display:none}
.timeline-header .jump-btn:hover{background:#30363d}
.timeline-item{padding:8px 14px;border-left:3px solid #30363d;
  border-bottom:1px solid #21262d;display:flex;gap:10px;align-items:flex-start;
  font-size:0.8rem;transition:background .1s}
.timeline-item:last-child{border-bottom:none}
.timeline-item:hover{background:rgba(88,166,255,0.03)}
.timeline-item .tl-icon{font-size:0.95rem;flex-shrink:0;width:20px;text-align:center}
.timeline-item .tl-time{color:#8b949e;font-size:0.68rem;white-space:nowrap;
  flex-shrink:0;min-width:65px}
.timeline-item .tl-body{flex:1;min-width:0}
.timeline-item .tl-type{font-size:0.66rem;text-transform:uppercase;
  letter-spacing:0.5px;color:#8b949e;margin-bottom:2px}
.timeline-item .tl-detail{color:#e6edf3;word-break:break-word}
.timeline-item .tl-detail .tl-sub{color:#8b949e;font-size:0.72rem}
.timeline-item.border-blue{border-left-color:#58a6ff}
.timeline-item.border-green{border-left-color:#3fb950}
.timeline-item.border-amber{border-left-color:#d29922}
.timeline-item.border-purple{border-left-color:#a371f7}
.timeline-item.border-red{border-left-color:#f85149}
.timeline-item.border-grey{border-left-color:#8b949e}
.timeline-item.border-orange{border-left-color:#f0883e}

/* Evidence indicators */
.ev-indicator{display:inline-block;font-size:0.7rem;margin-right:2px}
.ev-pass{color:#3fb950}
.ev-fail{color:#f85149}
.ev-skip{color:#8b949e}

/* Sidebar */
.sidebar{background:#161b22;border:1px solid #30363d;border-radius:8px;
  overflow:hidden}
.sidebar-header{padding:9px 12px;background:#0d1117;border-bottom:1px solid #30363d;
  display:flex;align-items:center;justify-content:space-between;
  font-size:0.75rem;font-weight:600;color:#8b949e;text-transform:uppercase;
  letter-spacing:0.5px;cursor:pointer;user-select:none}
.sidebar-header:hover{color:#e6edf3}
.sidebar-header .toggle-icon{font-size:0.7rem;transition:transform .2s}
.sidebar-header .toggle-icon.collapsed{transform:rotate(-90deg)}
.sidebar-body{padding:10px 12px;font-size:0.75rem}
.sidebar-body dl{margin:0}
.sidebar-body dt{color:#8b949e;font-size:0.65rem;text-transform:uppercase;
  letter-spacing:0.5px;margin-top:8px}
.sidebar-body dt:first-child{margin-top:0}
.sidebar-body dd{color:#e6edf3;word-break:break-all;margin:2px 0 0 0;font-size:0.75rem}
.sidebar-body code{background:#0d1117;border:1px solid #30363d;border-radius:3px;
  padding:1px 4px;font-size:0.68rem}

/* Evidence summary bar */
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

/* Step section (kept for backwards compat but hidden by default in new layout) */
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
.attempt-box .attempt-title .attempt-num{color:#e6edf3}
.evidence-row{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  font-size:0.7rem;margin:2px 0;display:flex;align-items:center;gap:4px;
  flex-wrap:wrap}
.evidence-row .check-id{color:#e6edf3}
.decision-gate{margin-top:4px;padding-top:4px;border-top:1px solid #21262d;
  font-size:0.75rem}
.decision-gate .gate-label{color:#8b949e}
.outcome-row{margin-top:3px;font-size:0.75rem;color:#8b949e}
.trigger-highlight{background:rgba(210,153,34,0.08);border-left:3px solid #d29922;
  padding:6px 8px;margin:6px 0;border-radius:0 4px 4px 0;font-size:0.75rem}
.trigger-highlight strong{color:#d29922}
.collector-fail{margin-top:3px;font-size:0.72rem;color:#f85149}

/* Evidence legend */
.legend{display:flex;flex-wrap:wrap;gap:4px 12px;margin-bottom:12px;
  font-size:0.72rem;color:#8b949e}
.legend-item{display:flex;align-items:center;gap:3px}

/* Raw lineage details */
.raw-lineage{margin-top:12px}
.raw-lineage summary{cursor:pointer;font-size:0.8rem;color:#8b949e;padding:4px 0}
.raw-lineage summary:hover{color:#e6edf3}
.raw-lineage pre{margin-top:6px;padding:12px;background:#0d1117;
  border:1px solid #30363d;color:#e6edf3;border-radius:4px;overflow:auto;
  font-size:0.68rem;max-height:350px}

/* SSE indicator */
.sse-indicator{display:inline-flex;align-items:center;gap:4px;font-size:0.7rem;
  color:#8b949e;margin-left:10px}
.sse-indicator.connected{color:#3fb950}
.sse-indicator .sse-dot{width:6px;height:6px;border-radius:50%;background:#8b949e}
.sse-indicator.connected .sse-dot{background:#3fb950}

/* Footer */
.page-footer{margin-top:20px;font-size:0.72rem;color:#30363d;text-align:center}

@media(max-width:768px){
  .run-grid{grid-template-columns:1fr}
  header{flex-direction:column;gap:4px}
  .detail-body{flex-direction:column}
  .timeline-panel{flex:1 1 100%}
  .sidebar-panel{flex:1 1 100%}
  .summary-cards{grid-template-columns:repeat(2,1fr)}
}
@media(max-width:480px){
  .summary-cards{grid-template-columns:1fr}
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
    trigger_icon = " ⚠️" if is_trigger else ""
    return (
        f"<div class='evidence-row'>"
        f"<span class='badge' style='background:{pcolor}'"
        f" title='provenance={html_escape(prov)}'>"
        f"{html_escape(prov)}</span>"
        f"{_evidence_status_badge(status)}"
        f"<span class='check-id'>{html_escape(label)}{trigger_icon}</span>"
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




def _render_overview_page(
    summaries: list[RunSummary],
    store_path: str,
    decisions: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Render the dashboard overview (list of all runs)."""
    total = len(summaries)
    active = sum(1 for s in summaries if s.incomplete)
    completed = sum(1 for s in summaries if not s.incomplete and str(s.status) in ("completed", "COMPLETED"))
    failed = sum(1 for s in summaries if not s.incomplete and str(s.status) in ("failed", "FAILED", "interrupted", "INTERRUPTED"))

    parts: list[str] = [
        "<!DOCTYPE html>",
        "<html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<meta http-equiv='refresh' content='15'>",
        "<title>BOUND · Dashboard</title>",
        "<style>", _CSS,
        ".stats-bar{display:flex;gap:16px;margin-bottom:24px;flex-wrap:wrap}",
        ".stat-card{flex:1;min-width:140px;background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px 20px;text-align:center}",
        ".stat-card .stat-value{font-size:1.8rem;font-weight:700;line-height:1.2}",
        ".stat-card .stat-label{font-size:0.7rem;color:#8b949e;text-transform:uppercase;letter-spacing:.5px;margin-top:2px}",
        ".stat-card.active .stat-value{color:#58a6ff}",
        ".stat-card.completed .stat-value{color:#3fb950}",
        ".stat-card.failed .stat-value{color:#f85149}",
        ".stat-card.total .stat-value{color:#e6edf3}",
        ".card-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:10px;margin-bottom:16px}",
        ".card-grid .rc{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px 16px;transition:border-color .15s;display:block;text-decoration:none}",
        ".card-grid .rc:hover{border-color:#58a6ff}",
        ".card-grid .rc .ct{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px}",
        ".card-grid .rc .ci{font-size:.7rem;color:#8b949e;font-family:monospace}",
        ".card-grid .rc .cta{font-size:.9rem;color:#e6edf3;margin-bottom:8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
        ".card-grid .rc .cm{font-size:.72rem;color:#8b949e;display:flex;gap:12px;flex-wrap:wrap}",
        ".card-grid .rc .cb{display:flex;gap:4px;flex-wrap:wrap}",
        ".empty-state{text-align:center;padding:80px 24px}",
        ".empty-state .ei{font-size:3rem;margin-bottom:16px;opacity:.3}",
        ".empty-state h2{font-size:1.1rem;color:#e6edf3;margin-bottom:8px}",
        ".empty-state p{font-size:.8rem;color:#8b949e;margin-bottom:4px}",
        ".empty-state code{background:#161b22;padding:2px 6px;border-radius:4px;font-size:.8rem;color:#58a6ff}",
        ".sec-hd{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}",
        ".sec-hd h2{font-size:.85rem;color:#8b949e;font-weight:500;text-transform:uppercase;letter-spacing:.5px}",
        "</style></head><body>",
        "<header><div>",
        "<h1 style='font-size:1rem;font-weight:600'>&#x26D3; BOUND</h1>",
        "<div class='sub'>Execution Dashboard &middot; v0.9.0</div>",
        "</div><div class='sub' style='text-align:right'>",
        f"{html_escape(store_path)}<br>{total} run{'' if total == 1 else 's'}",
        "</div></header>",
        "<div class='container'>",
    ]

    if not summaries:
        parts.append(
            "<div class='empty-state'><div class='ei'>&#x1F4E6;</div>"
            "<h2>No runs yet</h2>"
            "<p>Start your first BOUND-controlled session:</p>"
            "<p style='margin-top:8px'><code>bound run start &quot;your task&quot;</code></p>"
            "</div>",
        )
    else:
        # stats bar
        parts.append("<div class='stats-bar'>")
        parts.append(f"<div class='stat-card total'><div class='stat-value'>{total}</div><div class='stat-label'>Total Runs</div></div>")
        parts.append(f"<div class='stat-card active'><div class='stat-value'>{active}</div><div class='stat-label'>Active</div></div>")
        parts.append(f"<div class='stat-card completed'><div class='stat-value'>{completed}</div><div class='stat-label'>Completed</div></div>")
        parts.append(f"<div class='stat-card failed'><div class='stat-value'>{failed}</div><div class='stat-label'>Failed / Int.</div></div>")
        parts.append("</div>")

        # recent runs card grid (limit 50)
        parts.append("<div class='sec-hd'><h2>Recent Runs</h2></div>")
        parts.append("<div class='card-grid'>")
        for s in summaries[:50]:
            status_human = ("incomplete" if s.incomplete else (s.status.value if hasattr(s.status, "value") else str(s.status)))
            d = decisions.get(s.run_id, {}) if decisions else {}
            decision = d.get("decision", "—") if d else "—"
            assurance = d.get("assurance") if d else None
            task_display = (s.task or "(untitled)")[:80]
            parts.append(f"<a href='/run/{html_escape(s.run_id)}' class='rc'>")
            parts.append(f"<div class='ct'><span class='ci'>{html_escape(_short_id(s.run_id, 20))}</span>")
            parts.append(f"<span class='cb'>{_status_badge(status_human, _RUN_STATUS_COLORS)}{_decision_badge(decision)}{_assurance_badge(assurance)}</span></div>")
            parts.append(f"<div class='cta'>{html_escape(task_display)}</div>")
            parts.append(f"<div class='cm'><span>{fmt_dt(s.started_at)}</span><span>{s.step_count} step(s)</span></div>")
            parts.append("</a>")
        parts.append("</div>")
        if len(summaries) > 50:
            parts.append(f"<div style='text-align:center;color:#8b949e;font-size:.75rem;padding:12px'>Showing 50 of {total} runs — oldest omitted</div>")

        # all runs table
        parts.append(f"<div class='sec-hd' style='margin-top:24px'><h2>All Runs</h2><span style='font-size:.7rem;color:#8b949e'>{total} total</span></div>")
        parts.append("<table class='run-table'><thead><tr><th>Run</th><th>Task</th><th>Status</th><th>Decision</th><th>Assurance</th><th>Steps</th><th>Started</th><th>Finished</th></tr></thead><tbody>")
        for s in summaries:
            status_human = ("incomplete" if s.incomplete else (s.status.value if hasattr(s.status, "value") else str(s.status)))
            d = decisions.get(s.run_id, {}) if decisions else {}
            decision = d.get("decision", "—") if d else "—"
            assurance = d.get("assurance") if d else None
            finished = fmt_dt(s.finished_at) if s.finished_at else "—"
            parts.append(
                f"<tr><td><a href='/run/{html_escape(s.run_id)}'>{html_escape(_short_id(s.run_id, 16))}</a></td>"
                f"<td>{html_escape((s.task or '(untitled)')[:60])}</td>"
                f"<td>{_status_badge(status_human, _RUN_STATUS_COLORS)}</td>"
                f"<td>{_decision_badge(decision)}</td><td>{_assurance_badge(assurance)}</td>"
                f"<td>{s.step_count}</td><td>{fmt_dt(s.started_at)}</td><td>{finished}</td></tr>",
            )
        parts.append("</tbody></table>")

    parts.append("<div class='page-footer'>BOUND v0.9.0 — local read-only view. No data leaves your machine.</div>")
    parts.append("</div></body></html>")
    return "\n".join(parts)



def _render_run_detail(log: RunLog) -> str:
    """Render a single-run detail page with live timeline and dark theme."""
    run = log.run
    audit = _RunAuditIndex.from_log(log)
    run_id = html_escape(run.run_id)
    is_active = log.incomplete

    # -- Derive state info from events --
    # Find the latest decision from evaluations
    latest_decision = None
    latest_eval = None
    for ev in reversed(log.evaluations):
        if ev.decision:
            latest_decision = sv(ev.decision)
            latest_eval = ev
            break

    # Gather evidence summary per check name
    all_collected = [e for evs in audit.collected.values() for e in evs]
    verified_count = sum(1 for e in all_collected if e.provenance in INDEPENDENTLY_VERIFIED)
    total_count = len(all_collected)
    failures_count = sum(len(evs) for evs in audit.failures.values())

    # Per-check evidence groups
    ev_groups: dict[str, dict[str, Any]] = {}
    for e in all_collected:
        cid = e.check_id or e.collector or "?"
        if cid not in ev_groups:
            ev_groups[cid] = {"pass": 0, "fail": 0, "total": 0}
        ev_groups[cid]["total"] += 1
        status_s = (sv(e.status) if e.status else "").lower()
        if status_s in ("pass", "passed", "success", "ok"):
            ev_groups[cid]["pass"] += 1
        elif status_s in ("fail", "failed", "failure", "error"):
            ev_groups[cid]["fail"] += 1

    # -- Build current action bar --
    action_icon = "&#x1F504;"  # default: running
    action_desc = "Run in progress"
    action_ctx = ""
    action_class = "running"

    if is_active and not log.steps:
        # Active run with no steps yet — agent hasn't started working
        task_text = html_escape((run.task or "unknown task")[:80])
        action_desc = f"Waiting for agent &mdash; {task_text}"
        action_ctx = "No steps recorded yet"
    elif latest_decision:
        if latest_decision == "ACCEPT":
            action_icon = "&#x2705;"
            action_desc = "ACCEPT &mdash; step passed"
            action_class = "accepted"
        elif latest_decision == "RETRY":
            action_icon = "&#x1F501;"
            action_desc = "RETRY &mdash; retrying step"
            action_class = "retry"
        elif latest_decision == "REPLAN":
            action_icon = "&#x1F33F;"
            action_desc = "REPLAN &mdash; re-planning step"
            action_class = "replan"
        elif latest_decision == "ROLLBACK":
            action_icon = "&#x23EA;"
            action_desc = "ROLLBACK &mdash; rolling back"
            action_class = "rollback"
    if latest_eval and hasattr(latest_eval, 'contract_id'):
        action_ctx = f"Step: {html_escape(str(latest_eval.step_id))}"
    if not is_active and latest_decision:
        if latest_decision == "ACCEPT":
            action_ctx += " &middot; Run complete"
        else:
            action_ctx += " &middot; Run finished"
    if not is_active and not latest_decision:
        action_icon = "&#x1F3C1;"
        action_desc = "Run finished"
        action_class = "accepted"
        action_ctx = "No evaluations recorded"

    live_html = ""
    if is_active:
        live_html = "<span class='live-dot'></span><span class='live-label'>LIVE</span>"

    # -- Summary cards --
    decision_card = latest_decision or "&mdash;"
    decision_color = DECISION_COLORS.get(latest_decision or "", "#8b949e")
    assurance_level = None
    for gs in audit.gates.values():
        for g in gs:
            assurance_level = sv(g.assurance)
    assurance_display = assurance_level or "&mdash;"
    step_count = len(log.steps)
    total_attempts = sum(
        len([e for e in log.evaluations if e.step_id == s.step_id])
        for s in log.steps
    )
    duration_str = "&mdash;"
    if run.started_at:
        end = run.finished_at if run.finished_at else datetime.now(UTC)
        delta = end - run.started_at
        mins = int(delta.total_seconds() // 60)
        secs = int(delta.total_seconds() % 60)
        duration_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"

    # -- Build timeline from raw events --
    timeline_rows: list[str] = []
    for ev in log.events:
        ev_type = getattr(ev, 'event', 'unknown')
        ts = getattr(ev, 'timestamp', None)
        ts_str = fmt_dt(ts) if ts else "?"

        icon = "&#x1F4CC;"
        border = "border-grey"
        type_label = ev_type
        detail = ""

        if ev_type == "run_started":
            icon = "&#x1F680;"
            border = "border-blue"
            type_label = "run started"
            task = getattr(ev, 'task', '')
            detail = f"Task: {html_escape(str(task)[:100])}" if task else ""
        elif ev_type == "run_finished":
            icon = "&#x1F3C1;"
            border = "border-green"
            type_label = "run finished"
            status = getattr(ev, 'status', '')
            detail = f"Status: {html_escape(sv(status))}"
        elif ev_type == "step_started":
            icon = "&#x1F4CB;"
            border = "border-blue"
            type_label = "step started"
            cid = getattr(ev, 'contract_id', '')
            att = getattr(ev, 'attempt', '')
            detail = f"{html_escape(str(cid))} (attempt {att})"
        elif ev_type == "evaluation_recorded":
            dec = sv(getattr(ev, 'decision', '?'))
            score = getattr(ev, 'score', 0)
            threshold = getattr(ev, 'threshold', 0)
            detail = (
                f"Score: {score:.3f} / {threshold:.3f}"
                f" &rarr; {html_escape(dec)}"
            )
            if dec == "ACCEPT":
                icon = "&#x2705;"
                border = "border-green"
            elif dec == "RETRY":
                icon = "&#x1F501;"
                border = "border-amber"
            elif dec == "REPLAN":
                icon = "&#x1F33F;"
                border = "border-purple"
            elif dec == "ROLLBACK":
                icon = "&#x23EA;"
                border = "border-orange"
            else:
                icon = "&#x1F4CA;"
                border = "border-grey"
            type_label = f"evaluation &rarr; {html_escape(dec)}"
        elif ev_type == "evidence.collected":
            icon = "&#x1F50D;"
            border = "border-grey"
            type_label = "evidence collected"
            cid = getattr(ev, 'check_id', '') or getattr(ev, 'collector', '?')
            status = getattr(ev, 'status', '')
            prov = getattr(ev, 'provenance', '')
            status_str = sv(status) if status else "?"
            status_lower = status_str.lower()
            if status_lower in ("pass", "passed", "success", "ok"):
                ind = "<span class='ev-indicator ev-pass'>&#x2705;</span>"
            elif status_lower in ("fail", "failed", "failure", "error"):
                ind = "<span class='ev-indicator ev-fail'>&#x274C;</span>"
            else:
                ind = "<span class='ev-indicator ev-skip'>&mdash;</span>"
            detail = (
                f"{ind} {html_escape(str(cid))}"
                f" <span class='tl-sub'>"
                f"{html_escape(status_str)} &middot; {html_escape(sv(prov))}"
                f"</span>"
            )
        elif ev_type == "evidence.collection_failed":
            icon = "&#x26A0;&#xFE0F;"
            border = "border-red"
            type_label = "collection failed"
            cid = getattr(ev, 'check_id', '') or getattr(ev, 'collector', '?')
            err = getattr(ev, 'error', '')
            detail = (
                f"{html_escape(str(cid))}:"
                f" <span class='tl-sub'>{html_escape(str(err)[:120])}</span>"
            )
        elif ev_type == "decision.gated":
            cd = sv(getattr(ev, 'candidate_decision', '?'))
            fd = sv(getattr(ev, 'final_decision', '?'))
            ass = sv(getattr(ev, 'assurance', '?'))
            icon = "&#x1F510;"
            border = "border-purple"
            type_label = "decision gated"
            detail = (
                f"candidate {html_escape(cd)} &rarr;"
                f" final {html_escape(fd)}"
                f" (assurance: {html_escape(ass)})"
            )
        elif ev_type == "outcome_recorded":
            na = sv(getattr(ev, 'next_action', '?'))
            icon = "&#x1F3AF;"
            border = "border-blue"
            type_label = "outcome"
            detail = f"Next action: {html_escape(na)}"
        elif ev_type == "action.reported":
            icon = "&#x1F4E3;"
            border = "border-grey"
            type_label = "action reported"
            ra = getattr(ev, 'reported_action', '')
            detail = f"{html_escape(str(ra)[:100])}" if ra else ""
        else:
            detail = str(ev)[:120]

        timeline_rows.append(
            f"<div class='timeline-item {border}'>"
            f"<span class='tl-icon'>{icon}</span>"
            f"<span class='tl-time'>{html_escape(ts_str)}</span>"
            f"<div class='tl-body'>"
            f"<div class='tl-type'>{type_label}</div>"
            f"<div class='tl-detail'>{detail}</div>"
            f"</div></div>"
        )

    # -- Evidence summary bar --
    ev_summary_parts: list[str] = []
    for cid, counts in sorted(ev_groups.items()):
        pct = (
            (counts["pass"] / counts["total"] * 100)
            if counts["total"] > 0
            else 0
        )
        if pct >= 100:
            icon_e = "&#x2705;"
        elif counts["fail"] > 0:
            icon_e = "&#x274C;"
        else:
            icon_e = "&#x26A0;&#xFE0F;"
        fail_count_str = f" ({counts['fail']})" if counts["fail"] > 0 else ""
        if pct >= 100:
            pct_color = "#3fb950"
        elif counts["fail"] > 0:
            pct_color = "#f85149"
        else:
            pct_color = "#d29922"
        ev_summary_parts.append(
            f"<div class='ev-group'>"
            f"<span class='ev-icon'>{icon_e}</span>"
            f"<span class='ev-name'>{html_escape(cid[:20])}</span>"
            f"<span class='ev-pct' style='color:{pct_color}'>"
            f"{pct:.0f}%{fail_count_str}</span>"
            f"<div class='ev-progress'>"
            f"<div class='ev-progress-fill'"
            f" style='width:{min(pct,100):.0f}%;background:{pct_color}'></div>"
            f"</div></div>"
        )

    # Risk level from evaluations
    risk_level = "LOW"
    risk_color = "#3fb950"
    if latest_eval and hasattr(latest_eval, 'scores'):
        scores = latest_eval.scores
        if hasattr(scores, 'risk') and scores.risk is not None:
            r = float(scores.risk)
            if r > 0.6:
                risk_level = "HIGH"
                risk_color = "#f85149"
            elif r > 0.3:
                risk_level = "MEDIUM"
                risk_color = "#d29922"
    ev_summary_parts.append(
        f"<span class='risk-badge'"
        f" style='background:{risk_color};color:#0d1117'>"
        f"Risk {risk_level}</span>"
    )

    # -- Build policy/sidebar info --
    cfg = run.config
    policy_id = html_escape(str(cfg.policy_id)) if cfg and cfg.policy_id else "&mdash;"
    policy_ver = html_escape(str(cfg.policy_version)) if cfg and cfg.policy_version else "&mdash;"
    policy_hash = html_escape(str(cfg.policy_hash)[:20]) if cfg and cfg.policy_hash else "&mdash;"
    if cfg:
        ws = getattr(cfg, 'workspace', None)
        workspace = html_escape(str(ws)) if ws else "&mdash;"
    else:
        workspace = "&mdash;"
    status_str = "incomplete" if log.incomplete else sv(run.status)

    # Checkpoints from events
    checkpoint_ids: list[str] = []
    for ev in log.events:
        ev_event = getattr(ev, 'event', '')
        if ev_event and 'checkpoint' in str(ev_event).lower():
            checkpoint_ids.append(getattr(ev, 'event_id', '?'))
    if not checkpoint_ids:
        checkpoint_ids.append("none recorded")

    artifact_count = len(log.events)

    # -- Assemble page --
    parts: list[str] = [
        "<!DOCTYPE html>",
        "<html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>BOUND run {html_escape(_short_id(run.run_id, 20))}</title>",
        "<style>",
        _CSS,
        "</style>",
        "</head><body>",
        "<header>",
        "<div><h1>&#x26D3; BOUND run detail</h1>"
        "<div class='sub'>local lineage &middot; read-only</div></div>",
        (
            f"<div class='sub' id='header-run-id'>"
            f"{html_escape(_short_id(run.run_id, 20))}"
            f"<span id='sse-ind-detail' class='sse-indicator'>"
            f"<span class='sse-dot'></span>live</span>"
            f"</div>"
        ),
        "</header>",
        "<div class='container'>",
        "<div class='back-nav'><a href='/'>&larr; back to runs</a></div>",
    ]

    # --- Run metadata header ---
    parts.append("<div class='run-detail-header'>")
    parts.append(f"<h2>{html_escape(run.task or '(untitled)')}</h2>")
    parts.append("<div class='meta-grid'>")
    parts.append(
        f"<div><span class='label'>Status:</span>"
        f"{_status_badge(status_str, _RUN_STATUS_COLORS)}</div>"
    )
    parts.append(
        f"<div><span class='label'>Policy:</span>"
        f"{policy_id}@{policy_ver}</div>"
    )
    parts.append(
        f"<div><span class='label'>Started:</span>"
        f"{fmt_dt(run.started_at)}</div>"
    )
    if run.finished_at:
        parts.append(
            f"<div><span class='label'>Finished:</span>"
            f"{fmt_dt(run.finished_at)}</div>"
        )
    parts.append("</div></div>")

    # --- Current action bar ---
    parts.append(
        f"<div class='action-bar {action_class}' id='action-bar'>"
        f"<span class='action-icon'>{action_icon}</span>"
        f"<div class='action-text'>"
        f"<div class='action-desc'>{action_desc}</div>"
        f"<div class='action-ctx'>{action_ctx}</div>"
        f"</div>"
        f"{live_html}"
        f"</div>"
    )

    # --- Summary cards ---
    parts.append("<div class='summary-cards'>")
    parts.append(
        f"<div class='summary-card border-blue'>"
        f"<div class='card-value' style='color:{decision_color}'>"
        f"{html_escape(decision_card)}</div>"
        f"<div class='card-label'>Decision</div></div>"
    )
    parts.append(
        f"<div class='summary-card border-green'>"
        f"<div class='card-value'>{html_escape(assurance_display)}</div>"
        f"<div class='card-label'>Assurance</div></div>"
    )
    parts.append(
        f"<div class='summary-card border-amber'>"
        f"<div class='card-value'>{step_count}</div>"
        f"<div class='card-label'>Steps</div></div>"
    )
    parts.append(
        f"<div class='summary-card border-purple'>"
        f"<div class='card-value'>{total_attempts}</div>"
        f"<div class='card-label'>Attempts</div></div>"
    )
    parts.append(
        f"<div class='summary-card border-grey'>"
        f"<div class='card-value' id='duration-display'>"
        f"{html_escape(duration_str)}</div>"
        f"<div class='card-label'>Duration</div></div>"
    )
    parts.append("</div>")

    # --- Timeline + Sidebar ---
    if is_active:
        sidebar_display = "none"
        sidebar_collapsed_class = "collapsed"
        timeline_class = "full-width"
    else:
        sidebar_display = "block"
        sidebar_collapsed_class = ""
        timeline_class = ""

    parts.append("<div class='detail-body'>")
    parts.append(
        f"<div class='timeline-panel {timeline_class}' id='timeline-panel'>"
    )
    parts.append("<div class='timeline' id='timeline'>")
    parts.append(
        "<div class='timeline-header'>"
        "<span>&#x1F4C5; Live Timeline</span>"
        "<button class='jump-btn' id='jump-btn' onclick='jumpToLive()' "
        "title='Jump to latest'>"
        "&darr; Jump to live</button>"
        "</div>"
    )
    parts.append("<div id='timeline-items'>")
    parts.extend(timeline_rows)
    if is_active and not log.steps:
        parts.append(
            "<div class='tl-entry tl-waiting' style='display:flex;align-items:center;gap:8px;"
            "padding:10px 0;color:#8b949e;font-style:italic'>"
            "<span class='tl-dot live' style='width:8px;height:8px;border-radius:50%;"
            "background:#58a6ff;animation:pulse 1.5s infinite;display:inline-block'></span>"
            "Waiting for agent to begin execution &mdash; "
            "this page will auto-update when steps are recorded..."
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
        f"<span class='toggle-icon {sidebar_collapsed_class}' "
        f"id='toggle-icon'>&#x25BC;</span>"
        f"</div>"
    )
    parts.append(
        f"<div class='sidebar-body' id='sidebar-body'"
        f" style='display:{sidebar_display}'>"
    )
    parts.append("<dl>")
    parts.append(f"<dt>Policy</dt><dd>{policy_id} @ {policy_ver}</dd>")
    parts.append(f"<dt>Policy hash</dt><dd><code>{policy_hash}</code></dd>")
    parts.append(f"<dt>Workspace</dt><dd>{workspace}</dd>")
    parts.append(
        f"<dt>Checkpoints</dt>"
        f"<dd>{html_escape(', '.join(checkpoint_ids[:3]))}</dd>"
    )
    parts.append(f"<dt>Artifacts</dt><dd>{artifact_count} event(s)</dd>")
    parts.append(f"<dt>Run ID</dt><dd><code>{run_id}</code></dd>")
    parts.append(
        f"<dt>Evidence</dt><dd>{verified_count}/{total_count} verified"
        + (f" &middot; {failures_count} failure(s)" if failures_count else "")
        + "</dd>"
    )
    parts.append("</dl>")
    parts.append("</div>")  # sidebar-body
    parts.append("</div>")  # sidebar
    parts.append("</div>")  # sidebar-panel
    parts.append("</div>")  # detail-body

    # --- Evidence summary bar ---
    if ev_summary_parts:
        parts.append("<div class='evidence-bar'>")
        parts.extend(ev_summary_parts)
        parts.append("</div>")

    # --- Raw lineage ---
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

    parts.append(
        "<div class='page-footer'>"
        "BOUND dashboard &mdash; local read-only view. "
        "No data leaves your machine.</div>",
    )

    # --- JavaScript for live updates ---
    js_is_active = "true" if is_active else "false"
    parts.append(f"""<script>
(function(){{
  var isActive = {js_is_active};
  var runId = "{run_id}";
  var timeline = document.getElementById('timeline');
  var jumpBtn = document.getElementById('jump-btn');
  var sidebarCollapsed = {str(is_active).lower()};
  var lastEventCount = {len(log.events)};

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
    jumpBtn.style.display = 'none';
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

  // Auto-scroll detection
  if (timeline) {{
    timeline.addEventListener('scroll', function() {{
      var dist = (
        timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight
      );
      if (dist < 40) {{
        jumpBtn.style.display = 'none';
      }} else {{
        jumpBtn.style.display = 'inline-block';
      }}
    }});
    timeline.scrollTop = timeline.scrollHeight;
  }}

  // Live timer for active runs
  var startTime = Date.now();
  var durationEl = document.getElementById('duration-display');
  function updateTimer() {{
    if (!durationEl) return;
    var elapsed = Math.floor((Date.now() - startTime) / 1000);
    var mins = Math.floor(elapsed / 60);
    var secs = elapsed % 60;
    durationEl.textContent = (
      mins > 0 ? mins + 'm ' + secs + 's' : secs + 's'
    );
  }}

  // Poll API for updates on active runs
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
        updateTimer();
      }})
      .catch(function(){{}});
  }}

  if (isActive) {{
    setInterval(pollRun, 2000);
    setInterval(updateTimer, 1000);
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

    # Quiet the default logging
    def log_message(self, fmt: str, *args: object) -> None:
        logger.debug(fmt, *args)

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
        """Read a single run log, returning None on failure."""
        try:
            return self._store.read_run(run_id, strict=False)
        except RunNotFound:
            return None
        except Exception:
            logger.exception("Failed to read run %s", run_id)
            return None

    # --- Handlers ---

    def _handle_overview(self) -> None:
        summaries = self._get_runs()
        decisions = _get_overview_decisions(summaries, self._store)
        html = _render_overview_page(summaries, str(self._store.base_dir), decisions=decisions)
        self._send_html(html)

    def _handle_run_detail(self, run_id: str) -> None:
        log = self._get_run_log(run_id)
        if log is None:
            self._send_404(f"Run {run_id!r} not found or corrupt")
            return
        html = _render_run_detail(log)
        self._send_html(html)

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
    """
    host = "127.0.0.1"
    if store is not None:
        _DashboardHandler.lineage_store = store
    if run_id is not None:
        _DashboardHandler.startup_redirect = run_id

    try:
        server = HTTPServer((host, port), _DashboardHandler)
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
