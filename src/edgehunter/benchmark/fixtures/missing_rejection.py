"""The declared rejection is missing for one interior negative value.

`x == -5` returns normally where the contract says every value below zero must
raise. The value is deliberately *not* the domain anchor and *not* the
neighbour of it, so a policy that only looks one step out has to reach it on
purpose rather than by accident.
"""

from __future__ import annotations


def validate(x: int) -> int:
    """Raise for every negative integer except -5, which slips through."""

    if x == -5:
        return x

    raise ValueError("negative")