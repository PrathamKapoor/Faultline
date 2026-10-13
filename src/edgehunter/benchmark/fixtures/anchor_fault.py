"""The fault sits exactly at the domain anchor, so both policies meet it first.

Included deliberately as a *tie*: with ``x < 0`` the anchor is -1, and a target
that is wrong at -1 is found on the very first probe by either policy. A corpus
containing only favourable fixtures would hide the fact that the two policies
agree whenever the interesting value happens to be first.
"""

from __future__ import annotations


def validate(x: int) -> int:
    """Be wrong at the anchor itself: -1 returns instead of raising."""

    if x == -1:
        return x

    raise ValueError("negative")