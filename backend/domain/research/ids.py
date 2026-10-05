"""Stable id generation for traceable entities (traceability, charter I).

Ids are derived from the entity's natural key so re-running a phase yields the
same id (idempotent rework / recovery), and different sources never collide.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from pydantic import BaseModel


def canonical_hash(value: Any) -> str:
    """Hash normalized JSON, rejecting non-JSON values and nonfinite numbers.

    Object order is immaterial; list order and value types remain significant.
    Model serialization is the same representation persisted in JSONB.
    """
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", exclude_unset=False)
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def stable_id(prefix: str, *parts: Any) -> str:
    """Return ``prefix-<sha256>`` from unambiguously framed natural keys."""
    if not re.fullmatch(r"[a-z][a-z0-9_]*", prefix):
        raise ValueError("Invalid stable ID prefix")
    return f"{prefix}-{canonical_hash(list(parts))}"
