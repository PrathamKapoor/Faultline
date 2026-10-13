"""Behaviour alternates across the domain, so no neighbourhood rule is right.

Even values raise, odd values return. Neither "behave as before" nor "look one
step further" predicts the next input, and the fixture exists to show what the
engine reports when its central assumption does not hold.

Note that the contract still applies to every odd input: this is a genuine,
reproducible disagreement with the declaration, not a bug in the engine.
"""

from __future__ import annotations


def validate(x: int) -> int:
    """Return normally for odd negative integers, raise for even ones."""

    if x % 2 != 0:
        return x

    raise ValueError("negative")