"""Stable id generation for traceable entities (traceability, charter I).

Ids are derived from the entity's natural key so re-running a phase yields the
same id (idempotent rework / recovery), and different sources never collide.
"""
from __future__ import annotations

import hashlib


def stable_id(prefix: str, *parts: str) -> str:
    """Return a stable ``prefix-<hash10>`` id from the entity's key parts."""
    digest = hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()[:10]
    return f"{prefix}-{digest}"
