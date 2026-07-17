"""BOUND's own reference integration (v0.6 Phase 10) — the I/O glue layer.

This package is **not** part of the deterministic core. It uses
:mod:`subprocess` to run BOUND's own verification commands
(``uv run pytest -q`` and a service-specific ``uv run pytest
tests/test_calculator.py -q``), parses the captured output with the pure
collectors in :mod:`bound.collectors`, builds a real
:class:`~bound.evidence.ExecutionEvidence`, evaluates it via BOUND's
deterministic policy (:func:`bound.evaluate_agent_step`), and writes a real
:class:`~bound.report.RunTrace` to ``bound_integration/run.json`` plus a
standardized ``bound_integration/INTEGRATION_REPORT.md`` rendered from the same
trace.

The deterministic core (:mod:`bound.report`, :mod:`bound.collectors`,
:mod:`bound.contracts`, ...) stays pure; the subprocess glue lives here.
"""

from __future__ import annotations

__all__ = ["run_demo"]
