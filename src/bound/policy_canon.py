"""Canonicalisation and hashing of the approved policy (BOUND v0.7.0, todo 4.2).

A decision must be reproducible from its recorded *policy hash*: two runs that
agree on the policy hash must have been governed by byte-for-byte equivalent
policy content. YAML formatting (key order, comments, indentation) must never
affect that hash, so the policy is reduced to a deterministic *canonical form*
before SHA-256 hashing.

This module owns:

* :func:`canonicalize_policy` — a deterministic, recursively key-sorted dict
  independent of the source YAML's formatting. Resolved effective weights and
  thresholds are part of the canonical form (todo 2.2/4.2).
* :func:`compute_policy_hash` — ``"sha256:<hex>"`` of the canonical policy.
* :func:`compute_contract_hash` — the canonical SHA-256 hex of a contract (bare
  64-char hex, consistent with :func:`bound.lineage.compute_contract_hash` so a
  policy-canonical contract hash matches the contract hash recorded in a
  :class:`~bound.lineage.RunConfigSnapshot`).
* :func:`policy_changed_since` — detect that the active policy changed between
  two snapshots during a run.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel

from bound.policy_schema import BoundPolicyConfig

__all__ = [
    "canonicalize_policy",
    "compute_contract_hash",
    "compute_policy_hash",
    "policy_changed_since",
]


def _sha256_hex(data: str | bytes) -> str:
    """Return the bare SHA-256 hex digest of ``data``.

    Args:
        data: A string (UTF-8 encoded) or bytes payload.

    Returns:
        The 64-character lowercase hex digest (no ``"sha256:"`` prefix).
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _canonical_json(obj: object) -> str:
    """Return a deterministic, sorted-keys JSON string for hashing.

    Pydantic models are dumped to JSON-mode dicts first so the hash is stable
    across Python object identity; ``sort_keys=True`` and tight separators
    guarantee field order never affects the digest.

    Args:
        obj: A Pydantic model, a dict, or a JSON string.

    Returns:
        A canonical JSON string with sorted keys.
    """
    if isinstance(obj, BaseModel):
        obj = obj.model_dump(mode="json")
    elif isinstance(obj, str):
        # A raw JSON string: normalise through json round-trip for key sorting.
        obj = json.loads(obj)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _sort_mapping(value: Any) -> Any:
    """Recursively return a copy of ``value`` with all dict keys sorted.

    Determinism requires every unordered mapping to be sorted; lists keep their
    order (a check list's order is significant, not unordered).

    Args:
        value: A JSON-mode value (dict, list, scalar).

    Returns:
        A value of the same shape with every nested dict's keys sorted.
    """
    if isinstance(value, dict):
        return {k: _sort_mapping(value[k]) for k in sorted(value, key=str)}
    if isinstance(value, list):
        return [_sort_mapping(v) for v in value]
    return value


def canonicalize_policy(policy: BoundPolicyConfig) -> dict[str, Any]:
    """Return the canonical, formatting-independent form of a policy.

    The canonical form is a JSON-mode dict with every nested mapping's keys
    sorted deterministically, so two policies that differ only in YAML key
    order, comments, or indentation produce the *same* canonical form (and thus
    the same hash). Resolved effective weights are part of the dump (they are
    stored on :class:`~bound.policy_schema.WeightedSignal` during validation),
    so the canonical form captures the *effective* policy, not just the
    authored text.

    Args:
        policy: A validated :class:`BoundPolicyConfig`.

    Returns:
        A deterministic dict suitable for stable hashing / serialisation.
    """
    dumped = policy.model_dump(mode="json")
    return _sort_mapping(dumped)


def compute_policy_hash(policy: BoundPolicyConfig) -> str:
    """Return ``"sha256:<hex>"`` of the canonical policy (todo 4.2).

    The hash identifies the *exact* policy content that governed a run, enabling
    replay/diffing and the release blocker "every decision records the policy
    hash". The ``"sha256:"`` prefix makes the value self-describing.

    Args:
        policy: A validated :class:`BoundPolicyConfig`.

    Returns:
        ``"sha256:"`` followed by the 64-character lowercase hex digest of the
        canonical policy form.
    """
    canonical = canonicalize_policy(policy)
    digest = _sha256_hex(json.dumps(canonical, sort_keys=True, separators=(",", ":")))
    return f"sha256:{digest}"


def compute_contract_hash(contract: BaseModel | dict[str, Any] | str) -> str:
    """Return the canonical SHA-256 hex of a contract (todo 4.2).

    The contract hash is the *bare* 64-character hex digest (no prefix),
    consistent with :func:`bound.lineage.compute_contract_hash` so the
    policy-canonical contract hash matches the ``contract_hash`` recorded in a
    :class:`~bound.lineage.RunConfigSnapshot`. Canonicalisation uses sorted
    keys so contract key order never affects the digest.

    Args:
        contract: A :class:`~bound.contracts.StepContract` (or any Pydantic
            model), a dict, or a raw JSON string.

    Returns:
        The 64-character lowercase hex digest identifying the exact contract.
    """
    return _sha256_hex(_canonical_json(contract))


def policy_changed_since(
    a: BoundPolicyConfig | str,
    b: BoundPolicyConfig | str,
) -> bool:
    """Return ``True`` when two policy snapshots differ (todo 4.2).

    Either argument may be a :class:`BoundPolicyConfig` (hashed via
    :func:`compute_policy_hash`) or an already-computed policy hash string
    (``"sha256:<hex>"``). This lets a run detect that the active policy changed
    between the run-start snapshot and a later checkpoint.

    Args:
        a: The first policy snapshot (model or hash string).
        b: The second policy snapshot (model or hash string).

    Returns:
        ``True`` if the two snapshots have different policy hashes;
        ``False`` if they are equivalent.
    """
    hash_a = a if isinstance(a, str) else compute_policy_hash(a)
    hash_b = b if isinstance(b, str) else compute_policy_hash(b)
    return hash_a != hash_b

