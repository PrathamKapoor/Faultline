"""A boolean and a string parameter, for materialization and planning coverage.

The benchmark's search cases are integer-only by design, but "all supported input
types materialize correctly" is a claim the corpus has to support somewhere. This
fixture is driven by the subsystem cases, not by a search policy: a contract
covers one integer, and inventing a boolean contract would be exactly the
generalization the project refuses.
"""

from __future__ import annotations


def describe(flag: bool, label: str) -> str:
    """Combine a boolean and a string parameter into a readable description."""

    prefix = "on" if flag else "off"
    if not label:
        raise ValueError("label must not be empty")
    return f"{prefix}:{label}"