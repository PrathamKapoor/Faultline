"""A target that contradicts the contract with the wrong exception class.

It raises, as the contract requires -- but not the class the contract names. The
engine must report a mismatch, and must not report it as though the observed
`TypeError` were the expected `ValueError`.
"""

from __future__ import annotations


def validate(x: int) -> int:
    """Raise TypeError where the contract declares ValueError."""

    if x < 0:
        raise TypeError("wrong class")
    return x