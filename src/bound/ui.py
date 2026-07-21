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
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from bound.cli import (
    _DECISION_COLORS,
    _PROVENANCE_COLORS,
    _RunAuditIndex,
    _fmt_dt,
    _html_escape,
    _sv,
    _INDEPENDENTLY_VERIFIED,
)
from bound.lineage import RunStatus
from bound.lineage_store import (
    LineageStore,
    RunLog,
    RunSummary,
    RunNotFound,
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
    "serve",
    "_render_overview_page",
    "_render_run_detail",
    "_decision_badge",
    "_get_overview_decisions",
]

# =========================================================================
# HTML components
# =========================================================================


def _status_badge(status: str, colors: Mapping[str, str]) -> str:
    """Return a coloured badge ``<span>`` for a status value."""
    color = colors.get(status, "#616161")
    return (
        f"<span class='badge' style='background:{color}'"
        f" title='{_html_escape(status)}'>"
        f"{_html_escape(status)}</span>"
    )


def _assurance_badge(assurance: str | None) -> str:
    """Return a coloured assurance badge."""
    if not assurance:
        return "<span class='badge' style='background:#9e9e9e'>—</span>"
    color = _ASSURANCE_COLORS.get(assurance, "#616161")
    return (
        f"<span class='badge' style='background:{color}'"
        f" title='assurance={_html_escape(assurance)}'>"
        f"{_html_escape(assurance)}</span>"
    )


def _evidence_status_badge(status: str | None) -> str:
    """Return a coloured evidence-status badge."""
    s = (status or "unknown").lower()
    color = _EVIDENCE_STATUS_COLORS.get(s, "#9e9e9e")
    return (
        f"<span class='badge evidence-badge' style='background:{color}'"
        f" title='evidence status: {_html_escape(s)}'>"
        f"{_html_escape(s)}</span>"
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
                }
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
            }
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

_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
  background:#f5f5f5;color:#222;font-size:14px;line-height:1.5}
a{color:#1565c0;text-decoration:none}
a:hover{text-decoration:underline}
header{background:#1a237e;color:#fff;padding:16px 24px;
  display:flex;align-items:center;justify-content:space-between}
header h1{font-size:1.3rem;font-weight:600}
header .sub{font-size:0.8rem;opacity:0.8}
.container{max-width:1200px;margin:0 auto;padding:24px}
.empty-state{text-align:center;padding:64px 24px;color:#757575}
.empty-state h2{font-size:1.2rem;margin-bottom:8px}
.empty-state p{font-size:0.9rem}
.run-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));
  gap:16px;margin-bottom:24px}
.run-card{background:#fff;border:1px solid #e0e0e0;border-radius:8px;
  padding:16px;transition:box-shadow .15s}
.run-card:hover{box-shadow:0 2px 8px rgba(0,0,0,.1)}
.run-card h3{font-size:1rem;margin-bottom:6px;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
.run-card .meta{font-size:0.8rem;color:#757575;margin-bottom:8px}
.run-card .tags{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:8px}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;color:#fff;
  font-size:0.7rem;font-weight:600;margin-right:2px;white-space:nowrap}
.evidence-badge{font-size:0.65rem}
.kv{color:#757575;font-size:0.8rem}
table.run-table{width:100%;border-collapse:collapse;background:#fff;
  border:1px solid #e0e0e0;border-radius:8px;overflow:hidden}
table.run-table th{background:#f5f5f5;text-align:left;padding:10px 12px;
  font-size:0.75rem;text-transform:uppercase;color:#757575;
  border-bottom:1px solid #e0e0e0}
table.run-table td{padding:10px 12px;border-bottom:1px solid #f0f0f0;
  font-size:0.85rem}
table.run-table tr:last-child td{border-bottom:none}
table.run-table tr:hover{background:#fafafa}

/* Run detail */
.back-nav{margin-bottom:16px}
.back-nav a{font-size:0.85rem;color:#1565c0}
.run-detail-header{background:#fff;border:1px solid #e0e0e0;
  border-radius:8px;padding:20px;margin-bottom:16px}
.run-detail-header h2{font-size:1.2rem;margin-bottom:10px}
.run-detail-header .meta-grid{display:flex;flex-wrap:wrap;gap:8px 24px;
  font-size:0.85rem}
.run-detail-header .meta-grid .label{color:#757575;margin-right:4px}
.step-section{margin-bottom:16px}
.step-card{background:#fff;border:1px solid #e0e0e0;border-radius:8px;
  margin-bottom:12px;overflow:hidden}
.step-card .step-header{padding:14px 16px;border-bottom:1px solid #f0f0f0;
  display:flex;align-items:center;justify-content:space-between;
  flex-wrap:wrap;gap:8px}
.step-card .step-header .step-title{font-weight:600;font-size:0.95rem}
.step-card .step-body{padding:12px 16px}
.attempt-box{margin:8px 0;padding:10px 12px;border-left:3px solid #bdbdbd;
  background:#fafafa;border-radius:0 4px 4px 0}
.attempt-box .attempt-title{font-size:0.8rem;font-weight:600;color:#555;
  margin-bottom:6px}
.attempt-box .attempt-title .attempt-num{color:#222}
.evidence-row{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  font-size:0.75rem;margin:3px 0;display:flex;align-items:center;gap:4px;
  flex-wrap:wrap}
.evidence-row .check-id{color:#222}
.decision-gate{margin-top:6px;padding-top:6px;border-top:1px solid #eee;
  font-size:0.8rem}
.decision-gate .gate-label{color:#757575}
.outcome-row{margin-top:4px;font-size:0.8rem;color:#555}
.trigger-highlight{background:#fff3e0;border-left:3px solid #ef6c00;
  padding:8px 10px;margin:8px 0;border-radius:0 4px 4px 0;font-size:0.8rem}
.trigger-highlight strong{color:#e65100}
.collector-fail{margin-top:4px;font-size:0.78rem;color:#c62828}

/* Evidence legend */
.legend{display:flex;flex-wrap:wrap;gap:4px 16px;margin-bottom:16px;
  font-size:0.78rem;color:#555}
.legend-item{display:flex;align-items:center;gap:4px}

@media(max-width:600px){
  .run-grid{grid-template-columns:1fr}
  header{flex-direction:column;gap:4px}
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
    pcolor = _PROVENANCE_COLORS.get(prov, "#9e9e9e")
    label = check_id or collector or "?"
    trigger_icon = " ⚠️" if is_trigger else ""
    return (
        f"<div class='evidence-row'>"
        f"<span class='badge' style='background:{pcolor}'"
        f" title='provenance={_html_escape(prov)}'>"
        f"{_html_escape(prov)}</span>"
        f"{_evidence_status_badge(status)}"
        f"<span class='check-id'>{_html_escape(label)}{trigger_icon}</span>"
        f"</div>"
    )

# =========================================================================
# Page templates
# =========================================================================


def _decision_badge(decision: str) -> str:
    """Return a coloured badge for a BOUND decision value."""
    if decision in ("—", None, ""):
        return "<span class='badge' style='background:#9e9e9e'>—</span>"
    color = _DECISION_COLORS.get(decision, "#616161")
    return (
        f"<span class='badge' style='background:{color}'"
        f" title='decision={_html_escape(decision)}'>"
        f"{_html_escape(decision)}</span>"
    )


def _render_overview_page(
    summaries: list[RunSummary],
    store_path: str,
    decisions: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Render the dashboard overview (list of all runs).

    Args:
        summaries: Run summaries from the lineage store.
        store_path: Filesystem path to the store (displayed in the header).
        decisions: Optional dict mapping ``run_id`` to decision/assurance
            info (from :func:`_get_overview_decisions`). When omitted the
            overview omits decision and assurance columns.
    """
    parts: list[str] = [
        "<!DOCTYPE html>",
        "<html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        # Auto-refresh every 10 seconds so the overview stays current when new
        # runs appear (Sprint 1 live-update requirement).
        "<meta http-equiv='refresh' content='10'>",
        "<title>BOUND dashboard</title>",
        "<style>",
        _CSS,
        """
.live-indicator{display:inline-flex;align-items:center;gap:4px;font-size:0.7rem;color:#81c784}
.live-dot{width:6px;height:6px;border-radius:50%;background:#81c784;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.3}}
""",
        "</style></head><body>",
        "<header>",
        "<div><h1>BOUND dashboard</h1>",
        "<div class='sub'>local lineage &middot; read-only</div></div>",
        f"<div class='sub'>"
        f"<span class='live-indicator'><span class='live-dot'></span>live</span>"
        f" &middot; {_html_escape(store_path)}</div>",
        "</header>",
        "<div class='container'>",
    ]

    if not summaries:
        parts.append(
            "<div class='empty-state'>"
            "<h2>No BOUND runs yet</h2>"
            "<p>Start a BOUND-controlled agent session to see runs appear here.</p>"
            "<p style='margin-top:12px'><code>bound run start</code> &mdash; "
            "or let your agent integration create one automatically.</p>"
            "</div>"
        )
    else:
        parts.append(
            f"<div style='margin-bottom:12px;color:#757575;font-size:0.85rem'>"
            f"{len(summaries)} run(s) &middot; "
            f"newest first</div>"
        )
        parts.append("<div class='run-grid'>")
        for s in summaries:
            status_human = (
                "incomplete"
                if s.incomplete
                else (s.status.value if hasattr(s.status, "value") else str(s.status))
            )
            d = decisions.get(s.run_id, {}) if decisions else {}
            decision = d.get("decision", "—") if d else "—"
            assurance = d.get("assurance") if d else None

            parts.append(
                f"<a href='/run/{_html_escape(s.run_id)}' class='run-card'>"
            )
            parts.append(
                f"<div class='tags'>"
                f"{_status_badge(status_human, _RUN_STATUS_COLORS)}"
                f"{_decision_badge(decision)}"
                f"{_assurance_badge(assurance)}"
                f"</div>"
            )
            task_display = s.task or "(untitled)"
            if len(task_display) > 80:
                task_display = task_display[:80] + "…"
            parts.append(
                f"<h3 title='{_html_escape(s.task)}'>{_html_escape(task_display)}</h3>"
            )
            parts.append(
                f"<div class='meta'>{_short_id(s.run_id, 16)}"
                f" &middot; {_fmt_dt(s.started_at)}"
                f" &middot; {s.step_count} step(s)"
                f"</div>"
            )
            parts.append("</a>")
        parts.append("</div>")

        # Compact table view for quick scanning
        parts.append("<table class='run-table'>")
        parts.append(
            "<thead><tr>"
            "<th>Run</th><th>Task</th><th>Status</th>"
            "<th>Decision</th><th>Assurance</th>"
            "<th>Steps</th><th>Started</th><th>Finished</th>"
            "</tr></thead><tbody>"
        )
        for s in summaries:
            status_human = (
                "incomplete"
                if s.incomplete
                else (s.status.value if hasattr(s.status, "value") else str(s.status))
            )
            d = decisions.get(s.run_id, {}) if decisions else {}
            decision = d.get("decision", "—") if d else "—"
            assurance = d.get("assurance") if d else None
            finished = _fmt_dt(s.finished_at) if s.finished_at else "—"
            parts.append(
                "<tr>"
                f"<td><a href='/run/{_html_escape(s.run_id)}'>"
                f"{_html_escape(_short_id(s.run_id, 16))}</a></td>"
                f"<td>{_html_escape((s.task or '(untitled)')[:60])}</td>"
                f"<td>{_status_badge(status_human, _RUN_STATUS_COLORS)}</td>"
                f"<td>{_decision_badge(decision)}</td>"
                f"<td>{_assurance_badge(assurance)}</td>"
                f"<td>{s.step_count}</td>"
                f"<td>{_fmt_dt(s.started_at)}</td>"
                f"<td>{finished}</td>"
                "</tr>"
            )
        parts.append("</tbody></table>")

    parts.append(
        "<p style='margin-top:24px;font-size:0.78rem;color:#9e9e9e;"
        "text-align:center'>"
        "BOUND dashboard &mdash; local read-only view. "
        "No data leaves your machine.</p>"
    )
    parts.append("</div></body></html>")
    return "\n".join(parts)

def _render_run_detail(log: RunLog) -> str:
    """Render a single-run detail page with full decision tree."""
    run = log.run
    audit = _RunAuditIndex.from_log(log)
    parts: list[str] = [
        "<!DOCTYPE html>",
        "<html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        # Auto-refresh every 5 seconds so the page stays current when new
        # append-only events arrive (Sprint 1 live-update requirement).
        "<meta http-equiv='refresh' content='5'>",
        f"<title>BOUND run {_html_escape(run.run_id)}</title>",
        "<style>",
        _CSS,
        "</style></head><body>",
        "<header>",
        "<div><h1>BOUND run detail</h1>"
        "<div class='sub'>local lineage &middot; read-only</div></div>",
        f"<div class='sub'>{_html_escape(_short_id(run.run_id, 20))}</div>",
        "</header>",
        "<div class='container'>",
        "<div class='back-nav'><a href='/'>&larr; back to runs</a></div>",
    ]

    # --- Run metadata header ---
    status_str = "incomplete" if log.incomplete else _sv(run.status)
    parts.append("<div class='run-detail-header'>")
    parts.append(
        f"<h2>{_html_escape(run.task or '(untitled)')}</h2>"
    )
    parts.append("<div class='meta-grid'>")
    parts.append(
        f"<div><span class='label'>Run ID:</span>"
        f"<code>{_html_escape(run.run_id)}</code></div>"
    )
    parts.append(
        f"<div><span class='label'>Status:</span>"
        f"{_status_badge(status_str, _RUN_STATUS_COLORS)}</div>"
    )
    parts.append(
        f"<div><span class='label'>Started:</span>"
        f"{_fmt_dt(run.started_at)}</div>"
    )
    if run.finished_at:
        parts.append(
            f"<div><span class='label'>Finished:</span>"
            f"{_fmt_dt(run.finished_at)}</div>"
        )
    # Policy info
    cfg = run.config
    if cfg is not None and cfg.policy_id is not None:
        parts.append(
            f"<div><span class='label'>Policy:</span>"
            f"{_html_escape(cfg.policy_id)}@{_html_escape(cfg.policy_version or '?')}</div>"
        )
        if cfg.policy_hash is not None:
            parts.append(
                f"<div><span class='label'>Policy hash:</span>"
                f"<code>{_html_escape(cfg.policy_hash[:16])}…</code></div>"
            )
    # Evidence coverage summary
    all_collected = [e for evs in audit.collected.values() for e in evs]
    verified_count = sum(
        1 for e in all_collected
        if e.provenance in _INDEPENDENTLY_VERIFIED
    )
    total_count = len(all_collected)
    failures_count = sum(len(evs) for evs in audit.failures.values())
    parts.append(
        f"<div><span class='label'>Evidence:</span>"
        f"{verified_count}/{total_count} independently verified"
        + (f" &middot; {failures_count} failure(s)" if failures_count else "")
        + "</div>"
    )
    parts.append("</div></div>")

    # --- Evidence legend ---
    parts.append("<div class='legend'>")
    for prov, color in sorted(_PROVENANCE_COLORS.items(), key=lambda x: x[0]):
        parts.append(
            f"<div class='legend-item'>"
            f"<span class='badge' style='background:{color};font-size:0.6rem'>"
            f"{_html_escape(prov)}</span>"
            f"<span>{_html_escape(prov)}</span></div>"
        )
    parts.append("</div>")

    if not log.steps:
        parts.append("<p><em>No steps recorded.</em></p>")
        parts.append("</div></body></html>")
        return "\n".join(parts)

    # --- Step sections ---
    parts.append("<div class='step-section'>")
    for step in log.steps:
        evals = [e for e in log.evaluations if e.step_id == step.step_id]

        parts.append("<div class='step-card'>")
        # Step header
        parts.append("<div class='step-header'>")
        parts.append(
            f"<div class='step-title'>"
            f"{_html_escape(step.contract_id)}"
            f"</div>"
        )
        parts.append(
            f"<div><span class='kv'>step_id={_html_escape(step.step_id)}</span>"
            f" &middot; <span class='kv'>{_sv(step.status)}</span>"
            f" &middot; {len(evals)} attempt(s)"
            f"</div>"
        )
        parts.append("</div>")

        # Step body: attempts
        parts.append("<div class='step-body'>")
        if not evals:
            parts.append("<div class='kv'><em>No evaluations recorded.</em></div>")
        else:
            for ev in evals:
                decision = ev.decision or "(none)"
                dcolor = _DECISION_COLORS.get(decision, "#616161")
                is_retry_decision = decision in ("RETRY", "REPLAN", "ROLLBACK")

                parts.append("<div class='attempt-box'")
                if is_retry_decision:
                    parts.append(" style='border-left-color:#ef6c00'")
                parts.append(">")

                # Attempt header
                parts.append("<div class='attempt-title'>")
                parts.append(
                    f"<span class='badge' style='background:{dcolor}'>"
                    f"{_html_escape(decision)}</span>"
                )
                if ev.attempt is not None:
                    parts.append(
                        f" <span class='attempt-num'>attempt {ev.attempt}</span>"
                    )
                if ev.score is not None:
                    parts.append(
                        f" <span class='kv'>score {ev.score:.4f}"
                        f" (threshold {ev.threshold:.4f})</span>"
                    )
                if ev.reason_code:
                    parts.append(
                        f" <span class='kv'>{_html_escape(str(ev.reason_code))}</span>"
                    )
                parts.append("</div>")

                # Evidence rows for this attempt
                collected = audit.collected.get(ev.step_id, [])
                if collected:
                    for row in collected:
                        prov = _sv(row.provenance) if row.provenance else "missing"
                        status = _sv(row.status) if row.status else "?"
                        # Mark as trigger if decision was non-ACCEPT and evidence is weak
                        is_trigger = is_retry_decision and (
                            row.status in ("MISSING", "INVALID", "STALE")
                            or row.provenance in ("CLAIMED", "DEFAULTED", "MISSING")
                        )
                        parts.append(
                            _evidence_row(
                                row.check_id,
                                row.collector,
                                prov,
                                status,
                                is_trigger=is_trigger,
                            )
                        )

                # Collector failures
                failures = audit.failures.get(ev.step_id, [])
                for fail in failures:
                    parts.append(
                        f"<div class='collector-fail'>"
                        f"&#9888; collector {_html_escape(fail.collector or '?')}: "
                        f"{_html_escape(fail.error or 'unknown error')}</div>"
                    )

                # Decision gate (candidate vs final)
                gate = None
                for g in audit.gates.get(ev.step_id, []):
                    if g.evaluation_id == ev.evaluation_id:
                        gate = g
                        break
                if gate is None and audit.gates.get(ev.step_id):
                    gate = audit.gates[ev.step_id][-1]

                if gate:
                    cd = gate.candidate_decision
                    fd = gate.final_decision
                    fd_color = _DECISION_COLORS.get(fd, "#616161")
                    cd_color = _DECISION_COLORS.get(cd, "#616161")
                    parts.append("<div class='decision-gate'>")
                    parts.append(
                        f"<span class='gate-label'>candidate</span> "
                        f"<span class='badge' style='background:{cd_color}'>"
                        f"{_html_escape(cd)}</span>"
                        f" <span class='gate-label'>&rarr; final</span> "
                        f"<span class='badge' style='background:{fd_color}'>"
                        f"{_html_escape(fd)}</span>"
                        f" {_assurance_badge(_sv(gate.assurance))}"
                    )
                    if gate.assurance_reasons:
                        parts.append(
                            f"<div style='margin-top:4px;font-size:0.75rem;"
                            f"color:#757575'>"
                            f"{' &middot; '.join(_html_escape(r) for r in gate.assurance_reasons)}"
                            f"</div>"
                        )
                    parts.append("</div>")

                    # Highlight trigger if this gate caused RETRY/REPLAN/ROLLBACK
                    if fd in ("RETRY", "REPLAN", "ROLLBACK"):
                        reasons = list(gate.assurance_reasons) if gate.assurance_reasons else []
                        trigger_note = ""
                        if fd == "RETRY":
                            trigger_note = "Score below threshold or insufficient assurance"
                        elif fd == "REPLAN":
                            trigger_note = "Blocking evidence missing/invalid"
                        elif fd == "ROLLBACK":
                            trigger_note = "Critical check failed"
                        parts.append(
                            f"<div class='trigger-highlight'>"
                            f"<strong>&#9654; {trigger_note}</strong>"
                            + (f"<br><em>{' &middot; '.join(_html_escape(r) for r in reasons)}</em>"
                               if reasons else "")
                            + "</div>"
                        )

                # Outcome
                for oc in log.outcomes:
                    if oc.step_id == step.step_id:
                        parts.append(
                            f"<div class='outcome-row'>"
                            f"outcome: {_html_escape(oc.decision)}"
                            f" &rarr; {_html_escape(oc.next_action)}"
                            + (f" ({_html_escape(oc.note)})" if oc.note else "")
                            + "</div>"
                        )

                parts.append("</div>")  # end attempt-box

        parts.append("</div>")  # end step-body
        parts.append("</div>")  # end step-card

    parts.append("</div>")  # end step-section

    # Raw events accordion
    parts.append(
        "<details style='margin-top:16px'>"
        "<summary style='cursor:pointer;font-size:0.85rem;color:#757575'>"
        f"Raw lineage ({len(log.events)} event(s), "
        f"{log.corrupt_lines} corrupt, "
        f"{'truncated' if log.truncated else 'complete'})"
        "</summary>"
        "<pre style='margin-top:8px;padding:12px;background:#263238;"
        "color:#eceff1;border-radius:4px;overflow:auto;"
        "font-size:0.7rem;max-height:400px'>"
    )
    for ev in log.events:
        try:
            line = json.dumps(ev, default=str, indent=None)
        except (TypeError, ValueError):
            line = str(ev)
        parts.append(_html_escape(line) + "\n")
    parts.append("</pre></details>")

    parts.append(
        "<p style='margin-top:24px;font-size:0.78rem;color:#9e9e9e;"
        "text-align:center'>"
        "BOUND dashboard &mdash; local read-only view. "
        "No data leaves your machine.</p>"
    )
    parts.append("</div></body></html>")
    return "\n".join(parts)

# =========================================================================
# HTTP Server
# =========================================================================


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
            f"<h1>404</h1><p>{_html_escape(message)}</p>"
            f"<p><a href='/'>back to dashboard</a></p>"
            f"</body></html>",
            status=404,
        )

    def _send_error(self, status: int, message: str) -> None:
        self._send_html(
            f"<!DOCTYPE html><html><body>"
            f"<h1>{status}</h1><p>{_html_escape(message)}</p>"
            f"<p><a href='/'>back to dashboard</a></p>"
            f"</body></html>",
            status=status,
        )

    def do_GET(self) -> None:  # noqa: N802
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
                run_id = path[len("/run/"):]
                self._handle_run_detail(run_id)
            elif path == "/api/runs":
                self._handle_api_runs()
            elif path.startswith("/api/run/"):
                run_id = path[len("/api/run/"):]
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
                now = datetime.now(timezone.utc).isoformat()
                if count != last_count:
                    self.wfile.write(
                        f"event: run_count\ndata: {count}\n\n".encode("utf-8")
                    )
                    self.wfile.flush()
                    last_count = count
                else:
                    # Heartbeat every 5 seconds to keep the connection alive
                    self.wfile.write(
                        f": heartbeat {now}\n\n".encode("utf-8")
                    )
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
            alt_url = f"http://{host}:{alt_port}"
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
