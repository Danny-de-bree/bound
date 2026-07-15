"""Optional LLM contract-generator adapters (v0.3 Phase 4 boundary).

This module is a **documentation placeholder** for the optional, provider-backed
:class:`~bound.contracts.ContractGenerator` adapters. It intentionally contains
**no imports and no provider code**: an LLM SDK must never be a mandatory
dependency of the deterministic ``bound`` core, and the core must work entirely
without an LLM, network access, or API key.

Where a real adapter belongs
----------------------------
A concrete LLM-backed generator (e.g. an ``OpenAIContractGenerator`` or
``AnthropicContractGenerator``) belongs in a **separate, optional** package or
dependency group (for example ``pip install bound[llm]``), never in the core
distribution. It should:

* implement the :class:`~bound.contracts.ContractGenerator` Protocol;
* translate a natural-language goal + plan into **structured data only** —
  :class:`~bound.contracts.AcceptanceCheck`,
  :class:`~bound.contracts.RiskCheck`,
  :class:`~bound.contracts.StepBudget`, expected artefacts, and so on;
* return a :class:`~bound.contracts.BoundPlan` that has passed Pydantic
  validation;
* **never** return a BOUND decision (ACCEPT / RETRY / REPLAN / ROLLBACK) and
  **never** assign final ``A / I / R / C`` scores — those remain the exclusive
  responsibility of the deterministic evaluator and policy.

Keeping this seam import-free is what lets the architecture tests guarantee
that importing ``bound`` pulls in no provider SDK and makes no network call.
"""
