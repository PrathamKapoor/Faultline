"""Safe comparison of decoded protocol values.

The rules this module enforces:

* Python's **actual** builtin semantics are applied for supported types, rather
  than an invented equality relation. ``True == 1`` is true in Python, so an
  existing assertion ``assert f(1) == True`` is satisfied by an observed integer
  ``1``; reporting a mismatch there would contradict the language.
* ``is`` and ``is not`` are supported **only** against ``None``, because identity
  is a safe singleton question. Identity between two arbitrary values is not
  equality, and generalizing it would produce false confidence.
* An ordering comparison that Python itself refuses with ``TypeError`` is reported
  ``UNSUPPORTED``, never ``MISMATCH``. ``3 < "a"`` is not a counterexample; it is a
  question the evaluator cannot answer.
* No user-defined comparison is ever invoked. Values reaching here are the
  codec's built-in whitelist, and only built-ins are ever constructed.

Every function returns a typed :class:`ComparisonResult` rather than a bool so a
refusal is as visible as a verdict.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Final, Protocol, cast

from edgehunter.analysis.models import ComparisonOperator
from edgehunter.evaluation.models import OracleStatus


class _Orderable(Protocol):
    """The comparison surface Python's ordering operators need.

    Declared so the ordering path can be type-checked without claiming that every
    value supports it. In practice only the built-ins in
    :data:`SUPPORTED_RETURN_TYPES` ever arrive, and Python's own ``TypeError``
    remains the authority on whether a given pair is orderable.
    """

    def __lt__(self, other: object, /) -> bool: ...
    def __gt__(self, other: object, /) -> bool: ...
    def __le__(self, other: object, /) -> bool: ...
    def __ge__(self, other: object, /) -> bool: ...


# Ordering operators that require two mutually comparable values.
_ORDERING_OPERATORS: Final[frozenset[ComparisonOperator]] = frozenset(
    {
        ComparisonOperator.LESS_THAN,
        ComparisonOperator.LESS_THAN_OR_EQUAL,
        ComparisonOperator.GREATER_THAN,
        ComparisonOperator.GREATER_THAN_OR_EQUAL,
    }
)

_EQUALITY_OPERATORS: Final[frozenset[ComparisonOperator]] = frozenset(
    {ComparisonOperator.EQUAL, ComparisonOperator.NOT_EQUAL}
)

_IDENTITY_OPERATORS: Final[frozenset[ComparisonOperator]] = frozenset(
    {ComparisonOperator.IS, ComparisonOperator.IS_NOT}
)

SUPPORTED_OPERATORS: Final[frozenset[ComparisonOperator]] = frozenset(
    _EQUALITY_OPERATORS | _ORDERING_OPERATORS | _IDENTITY_OPERATORS
)

# The built-in types the codec's whitelist can produce. Used only for annotations:
# every value reaching this module is one of them.
# The built-in types the codec's whitelist can produce. Listed for documentation
# only: values reaching this module are already restricted to them upstream.
SUPPORTED_RETURN_TYPES: Final[tuple[type[object], ...]] = (
    type(None),
    bool,
    int,
    float,
    str,
    list,
    tuple,
    set,
    dict,
)


class ComparisonOutcome(str, Enum):
    """Whether two decoded values satisfy a comparison, or why it could not be tried."""

    SATISFIED = "satisfied"
    CONTRADICTED = "contradicted"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class ComparisonResult:
    """One comparison attempt with its verdict and the reason behind it."""

    outcome: ComparisonOutcome
    reason: str
    observation_key: str = ""
    """Canonical descriptor of the observed value, for identity and dedup."""


def compare_typed_values(
    operator: ComparisonOperator,
    observed: object,
    expected: object,
    *,
    observed_kind: str = "",
) -> ComparisonResult:
    """Apply one supported comparison using Python's own builtin semantics.

    The function is deliberately total: an unsupported operator, an identity
    comparison against a non-``None`` value, or a Python ``TypeError`` all produce
    ``UNSUPPORTED`` rather than a verdict.
    """
    if operator not in SUPPORTED_OPERATORS:
        return ComparisonResult(
            outcome=ComparisonOutcome.UNSUPPORTED,
            reason=f"operator {operator.value!r} is not supported for runtime evaluation",
        )

    if operator in _IDENTITY_OPERATORS:
        return _compare_identity(operator, observed, expected)

    try:
        satisfied = bool(_apply(operator, observed, expected))
    except TypeError as exc:
        # Python itself refuses this comparison. Calling it a contradiction would
        # claim the target violated an assertion it was never evaluated against.
        return ComparisonResult(
            outcome=ComparisonOutcome.UNSUPPORTED,
            reason=(
                f"Python does not define {operator.value!r} for "
                f"{observed_kind or type(observed).__name__} and "
                f"{type(expected).__name__}: {exc}"
            ),
            observation_key=canonical_value_text_key(observed),
        )

    return ComparisonResult(
        outcome=(ComparisonOutcome.SATISFIED if satisfied else ComparisonOutcome.CONTRADICTED),
        reason=(
            f"the observed value {operator.value} the expected value is "
            + ("true" if satisfied else "false")
        ),
        observation_key=canonical_value_text_key(observed),
    )


def _compare_identity(
    operator: ComparisonOperator, observed: object, expected: object
) -> ComparisonResult:
    """Evaluate ``is`` / ``is not`` only against ``None``.

    Restricting identity to the ``None`` singleton is what keeps this honest:
    ``x is y`` between two target-defined objects depends on identity EdgeHunter
    cannot observe, and ``x == y`` is not a substitute for it.
    """
    if expected is not None or observed is not None:
        return ComparisonResult(
            outcome=ComparisonOutcome.UNSUPPORTED,
            reason=(
                "identity comparisons are only supported against None; identity between "
                "arbitrary values is not something the evaluator can observe"
            ),
            observation_key=canonical_value_text_key(observed),
        )
    # Both are None, so `is` is satisfied and `is not` is contradicted.
    satisfied = operator is ComparisonOperator.IS
    return ComparisonResult(
        outcome=ComparisonOutcome.SATISFIED if satisfied else ComparisonOutcome.CONTRADICTED,
        reason=(
            "the observed value is the expected None singleton"
            if satisfied
            else "the observed value was None but the expectation required a non-None identity"
        ),
        observation_key=canonical_value_text_key(observed),
    )


def _apply(operator: ComparisonOperator, observed: object, expected: object) -> bool:
    """Apply the operator exactly as Python would, with no added type checks.

    The ordering branch is cast rather than type-narrowed because Python's
    ``TypeError`` is the real authority on whether two values are orderable. That
    exception is caught by the caller and reported as ``UNSUPPORTED``, so the cast
    cannot cause an incorrect verdict.
    """
    if operator is ComparisonOperator.EQUAL:
        return bool(observed == expected)
    if operator is ComparisonOperator.NOT_EQUAL:
        return bool(observed != expected)

    left = cast(_Orderable, observed)
    right = cast(_Orderable, expected)
    if operator is ComparisonOperator.LESS_THAN:
        return bool(left < right)
    if operator is ComparisonOperator.LESS_THAN_OR_EQUAL:
        return bool(left <= right)
    if operator is ComparisonOperator.GREATER_THAN:
        return bool(left > right)
    return bool(left >= right)


def canonical_value_text_key(observed: object) -> str:
    """Canonical descriptor of an already-materialized built-in value.

    Used for observation identity in recheck comparison and deduplication.
    Rendered as text rather than compared by equality so ``nan`` and ``-0.0`` stay
    distinguishable, and so no user-defined ``__eq__`` can be reached.
    """
    if observed is None:
        return "none"
    # bool before int, because bool subclasses int.
    if isinstance(observed, bool):
        return f"boolean:{'true' if observed else 'false'}"
    if isinstance(observed, int):
        return f"integer:{observed}"
    if isinstance(observed, float):
        return f"float:{_float_text(observed)}"
    if isinstance(observed, str):
        return f"string:{observed!r}"
    if isinstance(observed, list):
        return "list:[" + ",".join(canonical_value_text_key(item) for item in observed) + "]"
    if isinstance(observed, tuple):
        return "tuple:(" + ",".join(canonical_value_text_key(item) for item in observed) + ")"
    if isinstance(observed, (set, frozenset)):
        return "set:{" + ",".join(sorted(canonical_value_text_key(i) for i in observed)) + "}"
    if isinstance(observed, dict):
        entries = sorted(
            f"{canonical_value_text_key(key)}:{canonical_value_text_key(value)}"
            for key, value in observed.items()
        )
        return "dict:{" + ",".join(entries) + "}"
    # The codec's whitelist makes this unreachable for decoded observations; the
    # fallback exists so an unexpected type degrades to "unknown" instead of raising.
    return f"opaque:{type(observed).__name__}"


def _float_text(value: float) -> str:
    """Canonical float text, keeping the special values distinguishable."""
    if math.isnan(value):
        return "nan"
    if math.isinf(value):
        return "inf" if value > 0 else "-inf"
    if value == 0.0:
        return "-0.0" if math.copysign(1.0, value) < 0 else "0.0"
    return repr(value)


def status_for(outcome: ComparisonOutcome) -> OracleStatus:
    """Translate a comparison outcome into the matching oracle status."""
    if outcome is ComparisonOutcome.SATISFIED:
        return OracleStatus.MATCH
    if outcome is ComparisonOutcome.CONTRADICTED:
        return OracleStatus.MISMATCH
    return OracleStatus.UNSUPPORTED


__all__ = [
    "SUPPORTED_OPERATORS",
    "ComparisonOutcome",
    "ComparisonResult",
    "canonical_value_text_key",
    "compare_typed_values",
    "status_for",
]
