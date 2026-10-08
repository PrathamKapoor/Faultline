"""Safe baseline values synthesized from static annotation shapes.

A *baseline* is **not** an oracle and **not** a validity claim. It is a
deterministic, statically constructible value that exists so that an invocation
plan can fill an argument EdgeHunter has no adversarial intent for. ``0`` is not
described as "a valid integer" here: it is "the simplest statically constructible
integer", and nothing more. Whether the target accepts it is exactly what a
later execution phase has to find out.

Every baseline records where it came from, so a failure can later be attributed
to the adversarial action rather than to the filler:

* :attr:`BaselineSource.STATIC_TYPE` — synthesized from the annotation shape.
* :attr:`BaselineSource.EXISTING_TEST` — a literal argument the repository's own
  test already passed to this target.
* :attr:`BaselineSource.DECLARED_DEFAULT` — the parameter is omitted and its
  declared default applies. The default itself is never evaluated.
* :attr:`BaselineSource.DEPENDENCY` — filled so that another parameter's
  symbolic boundary could be resolved.

When no trustworthy value exists, :func:`baseline_for` returns ``None``. That is
the important result: an unknown type yields no baseline, and in particular
never a fabricated ``None``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

from edgehunter.generation.models import LiteralKind
from edgehunter.scanner.annotations import AnnotationKind, StaticAnnotation


class BaselineSource(str, Enum):
    """Why a value was chosen to fill an argument."""

    STATIC_TYPE = "static_type_baseline"
    EXISTING_TEST = "existing_test_baseline"
    DECLARED_DEFAULT = "declared_default"
    DEPENDENCY = "dependency_baseline"


@dataclass(frozen=True, slots=True)
class BaselineValue:
    """One trustworthy filler value together with its provenance."""

    source: BaselineSource
    value_text: str
    literal_kind: LiteralKind
    reason: str


# The simplest statically constructible value for each recognized shape. These
# are deliberately boring: they exist to fill an argument, not to be
# interesting. Empty containers need no element knowledge, which is why a bare
# ``list`` still has a usable baseline while a bare ``list`` has no usable
# singleton element.
_PRIMITIVE_BASELINES: Final[dict[AnnotationKind, tuple[str, LiteralKind]]] = {
    AnnotationKind.INT: ("0", LiteralKind.INTEGER),
    AnnotationKind.FLOAT: ("0.0", LiteralKind.FLOAT),
    AnnotationKind.BOOL: ("False", LiteralKind.BOOLEAN),
    AnnotationKind.STR: ('""', LiteralKind.TEXT),
    AnnotationKind.NONE: ("None", LiteralKind.NOTHING),
    AnnotationKind.LIST: ("[]", LiteralKind.COLLECTION),
    AnnotationKind.TUPLE: ("()", LiteralKind.COLLECTION),
    AnnotationKind.SET: ("set()", LiteralKind.COLLECTION),
    AnnotationKind.DICT: ("{}", LiteralKind.COLLECTION),
}


def baseline_for(shape: StaticAnnotation) -> BaselineValue | None:
    """Return a trustworthy baseline for ``shape``, or ``None`` when there is none.

    A nullable shape is filled with its *non-null* baseline by default, because
    ``None`` is then an adversarial choice worth testing on its own rather than
    an automatic filler. Unknown shapes and multi-type unions yield ``None``.
    """
    target = shape.non_nullable()
    entry = _PRIMITIVE_BASELINES.get(target.kind)
    if entry is None:
        return None
    value_text, literal_kind = entry
    return BaselineValue(
        source=BaselineSource.STATIC_TYPE,
        value_text=value_text,
        literal_kind=literal_kind,
        reason=(
            f"static baseline for {shape.description}; a filler value, not a claim "
            "that the target accepts it"
        ),
    )
