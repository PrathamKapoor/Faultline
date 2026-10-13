"""The fault sits exactly at the user-supplied starting value.

This is the case where the static baseline's documented order is *better* than
proximity. Its order puts the user seed second; the feedback policy instead tries
the nearest untested neighbour of the anchor and has to walk towards this value.

It exists because a benchmark that can only produce a favourable result for the
adaptive policy is not measuring anything.
"""

from __future__ import annotations


def validate(x: int) -> int:
    """Be wrong at -64, which is the seed the harness supplies."""

    if x == -64:
        return x

    raise ValueError("negative")