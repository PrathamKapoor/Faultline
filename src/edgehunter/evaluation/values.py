"""Host-side materialization and canonical identity for protocol values.

Two jobs, both narrow:

* :func:`materialize` turns a :class:`~edgehunter.execution.codec.TypedValue` into a
  plain Python value **for comparison only**. The values it accepts are the ones
  the Phase 6A protocol already produced from exact built-in type checks, so this
  is a pure decode with no target code in it. It exists because the evaluator must
  apply Python's real comparison semantics -- notably that ``True == 1`` -- and
  inventing a separate equality rule would report a mismatch for an assertion
  Python itself considers satisfied.

* :func:`canonical_value_text` produces a stable descriptor used for identity
  during deduplication and recheck comparison. It is **text**, never equality:
  ``nan != nan`` would make equality-based dedup of NaN-containing records
  unpredictable, and ``-0.0 == 0.0`` would collapse two distinguishable float
  observations.

No function here imports a target type, instantiates a target class, or evaluates
a target expression. The codec's whitelist already refused everything else before
these ever see a value.
"""

from __future__ import annotations

from typing import Final

from edgehunter.execution.codec import TypedValue, ValueKind

MAX_RENDERED_DEPTH: Final[int] = 4

_COLLECTION_KINDS: Final[frozenset[ValueKind]] = frozenset(
    {ValueKind.LIST, ValueKind.TUPLE, ValueKind.SET, ValueKind.MAPPING}
)


def materialize(value: TypedValue) -> object:
    """Build the plain Python value for comparison.

    Every branch constructs a built-in. The codec already refused anything that is
    not one, so no target-defined ``__eq__``, ``__lt__``, or ``__repr__`` can be
    reached through here.
    """
    return value.to_python()


def canonical_value_text(value: TypedValue) -> str:
    """A stable descriptor for one value, used for identity rather than equality.

    Floats are rendered by the codec's canonical spelling, so ``nan``, ``inf``,
    and ``-0.0`` each get a distinct and stable text form instead of collapsing
    through equality. Sets are ordered by their rendered elements so two equal sets
    produce one identity.
    """
    return _render(value, 0)


def _render(value: TypedValue, depth: int) -> str:
    if depth > MAX_RENDERED_DEPTH:
        return "..."
    if value.kind is ValueKind.NONE:
        return "None"
    if value.kind is ValueKind.STRING:
        return f"str:{value.scalar!r}"
    if value.kind is ValueKind.MAPPING:
        entries = ",".join(
            f"{_render(key, depth + 1)}:{_render(item, depth + 1)}" for key, item in value.pairs
        )
        return f"dict:{{{entries}}}"
    if value.kind is ValueKind.SET:
        # Sets have no order, so the identity is the sorted element descriptors.
        set_elements = sorted(_render(item, depth + 1) for item in value.items)
        return f"set:{{{','.join(set_elements)}}}"
    if value.kind in _COLLECTION_KINDS:
        opener = "[" if value.kind is ValueKind.LIST else "("
        closer = "]" if value.kind is ValueKind.LIST else ")"
        joined = ",".join(_render(item, depth + 1) for item in value.items)
        return f"{value.kind.value}:{opener}{joined}{closer}"
    return f"{value.kind.value}:{value.scalar}"


def describe_typed(value: TypedValue | None) -> str:
    """A short human-facing rendering for CLI output."""
    if value is None:
        return "(no value)"
    if value.kind in _COLLECTION_KINDS:
        size = len(value.items) or len(value.pairs)
        noun = "entry" if value.kind is ValueKind.MAPPING else "item"
        return f"{value.kind.value} with {size} {noun}{'s' if size != 1 else ''}"
    if value.kind is ValueKind.NONE:
        return "None"
    return f"{value.scalar or ''} ({value.kind.value})"
