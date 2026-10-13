"""Correct rejection: every in-domain input raises, so neither policy may claim a defect.

This is the corpus's most important fixture. A search engine that reports a
contradiction on a target that behaves correctly is worse than useless, and the
only way to know whether one does is to run it against a target that is right.
"""

from __future__ import annotations


def validate(x: int) -> int:
    """Reject every negative integer, as the contract declares."""

    if x < 0:
        raise ValueError("negative")
    return x