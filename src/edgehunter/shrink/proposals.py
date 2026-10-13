"""Deterministic, bounded candidate generation for a single integer input.

The metric is exactly one: ``abs(value)``. A candidate replaces the current best
only when its magnitude is *strictly* smaller, so two values of equal magnitude
are never called an improvement, and the search never claims to minimize the
numeric value rather than the magnitude.

The proposal list is a **heuristic**, and is described as one. Nothing here
assumes the target's behaviour is monotone in the input, and a proposal that
happens to pass says nothing about the untested values between it and the seed.
Every accepted candidate is executed and verified independently by the caller.

Order, which is fixed and reproducible:

1. the domain's closest-to-zero anchor, which is usually the value a minimizer
   wants and the first one worth spending a container on;
2. repeated integer midpoints walking from the seed toward the anchor, so a
   large seed reaches small magnitudes in a handful of steps;
3. deduplicated, then truncated to the caller's budget.

There is no randomness and no unbounded search: the loop is bounded by the
budget and terminates on a value that is no longer strictly simpler.
"""

from __future__ import annotations

from edgehunter.shrink.domain import IntegerDomain
from edgehunter.shrink.models import MAX_SIGNED_INT, MIN_SIGNED_INT

# A midpoint walk that needs more steps than this has stalled; the guard exists so
# the generator cannot loop even if a future domain shape breaks progress.
MAX_MIDPOINT_STEPS: int = 128


def complexity(value: int) -> int:
    """The one simplification metric: magnitude, never the signed value."""

    return abs(value)


def is_simpler(candidate: int, current: int) -> bool:
    """Whether ``candidate`` is a strict improvement under the metric."""

    return complexity(candidate) < complexity(current)


def propose_values(seed: int, domain: IntegerDomain, limit: int) -> tuple[int, ...]:
    """Propose at most ``limit`` strictly simpler admissible integers.

    Returns an empty tuple when nothing can be simpler -- the seed is already the
    smallest admissible magnitude, or the budget is zero. Every returned value
    satisfies the declared domain and has a strictly smaller magnitude, so the
    caller never has to re-check those two properties before deciding to execute.
    """

    if limit <= 0:
        return ()
    if not domain.contains(seed):
        return ()
    if not MIN_SIGNED_INT <= seed <= MAX_SIGNED_INT:
        return ()

    try:
        anchor = domain.closest_to_zero()
    except Exception:  # noqa: BLE001 - an unrepresentable domain proposes nothing
        return ()
    if not domain.contains(anchor) or not is_simpler(anchor, seed):
        # The seed is already the simplest admissible value. Nothing to try.
        return ()

    ordered: list[int] = []
    seen: set[int] = set()

    def _add(value: int) -> None:
        if value in seen:
            return
        if not domain.contains(value):
            return
        if not is_simpler(value, seed):
            return
        seen.add(value)
        ordered.append(value)

    _add(anchor)
    current = seed
    for _ in range(MAX_MIDPOINT_STEPS):
        if len(ordered) >= limit:
            break
        # Floor division walks toward the anchor from either sign, and the
        # loop stops as soon as a step stops making progress.
        step = current + (anchor - current) // 2
        if step == current or not is_simpler(step, seed):
            break
        _add(step)
        current = step
    return tuple(ordered[:limit])
