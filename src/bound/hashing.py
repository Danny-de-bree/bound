"""Content-addressing utilities shared across the BOUND codebase.

Every SHA-256 hash in BOUND is produced here so the ``sha256:`` prefix
convention and the raw hex-digest computation each exist in exactly one place.

Provides:
    :func:`sha256_hex` — ``"sha256:<64 hex chars>"`` (the canonical
        self-describing format used by evidence, policy hashes, and
        collector artefacts).
    :func:`sha256_hex_bare` — bare 64-character hex digest (used when
        callers already prefix or when computing contract/config hashes
        that are stored *without* a prefix).
"""

from __future__ import annotations

import hashlib

__all__ = [
    "sha256_hex",
    "sha256_hex_bare",
]


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
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def sha256_hex_bare(data: str | bytes) -> str:
    """Return the bare SHA-256 hex digest of *data* (no prefix).

    Used for content-addressing contracts, policy configs, and any internal
    context where the ``sha256:`` prefix is either added by the caller or
    unnecessary.  Accepts either a string (UTF-8 encoded) or raw bytes.

    Args:
        data: A string (encoded as UTF-8) or bytes payload.

    Returns:
        The 64-character lowercase hex digest (no ``"sha256:"`` prefix).
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()
