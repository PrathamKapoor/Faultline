"""The fault sits far from both the anchor and the seed.

At ``-1000`` the value is out of reach of any neighbourhood walk in a small
probe budget: proximity moves one step at a time, while the static order reaches
it because a mined comparison names it directly.

This is the honest failure mode of the adaptive policy, and it is in the corpus
so that failure is measured rather than assumed away.
"""

from __future__ import annotations


def validate(x: int) -> int:
    """Be wrong at -1000, far outside the anchor's neighbourhood."""

    if x == -1000:
        return x

    raise ValueError("negative")