"""The pure integer domain a user-declared contract may constrain one parameter to.

This module is deliberately the dumbest part of the shrinker. It compares
integers with explicit arithmetic and does nothing else: it does not call target
code, import target modules, evaluate a condition string, or reach any object
property. Every answer is a function of two integers, so every answer is
checkable by hand.

The one non-obvious function is :meth:`IntegerDomain.closest_to_zero`, which
finds the admissible integer with the smallest magnitude -- the value a
minimizer wants to reach. It is written as four explicit cases rather than a
general search, because a general search over an unbounded domain is exactly the
kind of unbounded loop this project refuses to run.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from edgehunter.shrink.models import MAX_SIGNED_INT, MIN_SIGNED_INT


class DomainOperator(str, Enum):
    """The four comparisons a contract may declare. No others.

    No AND/OR trees, no negation, no user-defined comparators: a contract that
    could express a compound predicate would be a general property language, and
    that belongs to a phase that would have to justify it.
    """

    LT = "lt"
    LE = "le"
    GT = "gt"
    GE = "ge"


class DomainError(ValueError):
    """The declared domain cannot be represented or has no admissible value."""


@dataclass(frozen=True, slots=True)
class IntegerDomain:
    """``<parameter> <operator> <reference>`` over signed 64-bit integers."""

    operator: DomainOperator
    reference: int

    def __post_init__(self) -> None:
        if not MIN_SIGNED_INT <= self.reference <= MAX_SIGNED_INT:
            raise DomainError(
                f"domain reference {self.reference} is outside the signed 64-bit range"
            )

    def contains(self, value: int) -> bool:
        """Whether one integer satisfies the declared comparison.

        Total over every Python ``int``, including ones outside the contract
        range, so an out-of-range proposal is rejected for its value rather than
        because a check happened to be missing.
        """
        match self.operator:
            case DomainOperator.LT:
                return value < self.reference
            case DomainOperator.LE:
                return value <= self.reference
            case DomainOperator.GT:
                return value > self.reference
            case DomainOperator.GE:
                return value >= self.reference
        raise DomainError(f"unsupported domain operator {self.operator!r}")

    def closest_to_zero(self) -> int:
        """The admissible integer whose magnitude is smallest.

        A single inequality defines a half-line, so the answer is a short
        calculation rather than a search:

        * ``x < c``: when ``c <= 0`` every admissible value is negative, so the
          largest one (``c - 1``) is closest; when ``c > 0`` zero itself is
          admissible.
        * ``x <= c``: ``c`` when ``c < 0``, else zero.
        * ``x > c``: zero when ``c < 0``, else the smallest admissible value
          above it (``c + 1``).
        * ``x >= c``: zero when ``c <= 0``, else ``c``.
        """
        reference = self.reference
        match self.operator:
            case DomainOperator.LT:
                anchor = reference - 1 if reference <= 0 else 0
            case DomainOperator.LE:
                anchor = reference if reference < 0 else 0
            case DomainOperator.GT:
                anchor = 0 if reference < 0 else reference + 1
            case DomainOperator.GE:
                anchor = 0 if reference <= 0 else reference
        if not MIN_SIGNED_INT <= anchor <= MAX_SIGNED_INT:
            # Only reachable at the extremes of the range, where the inequality
            # admits no representable integer at all.
            raise DomainError(
                f"the domain {self.operator.value} {reference} admits no signed 64-bit integer "
                f"closest to zero (anchor {anchor} is out of range)"
            )
        return anchor

    def is_empty(self) -> bool:
        """True when no signed 64-bit integer satisfies the domain."""

        try:
            anchor = self.closest_to_zero()
        except DomainError:
            return True
        return not self.contains(anchor)

    def describe(self) -> str:
        """A readable rendering, e.g. ``age < 0``."""

        operator_text = {
            DomainOperator.LT: "<",
            DomainOperator.LE: "<=",
            DomainOperator.GT: ">",
            DomainOperator.GE: ">=",
        }[self.operator]
        return f"{operator_text} {self.reference}"
