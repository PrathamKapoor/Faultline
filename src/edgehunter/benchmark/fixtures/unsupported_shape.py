"""A target whose shape is outside the supported contract subset.

The signature carries a second parameter and a default, both of which Phase 7B's
shape check refuses. There is no contract that could be soundly written for this
function in this phase, so the correct outcome is a refusal *before any
execution*, not a best-effort probe.
"""

from __future__ import annotations

from typing import Any


def validate(x: int, limit: int = 10) -> Any:
    """Two parameters, one with a default, and a non-int return annotation."""

    if x < 0:
        raise ValueError("negative")
    return {"value": x, "limit": limit}