"""Matching an expected exception against an observed one.

Phase 6A records an exception by ``type(exc).__name__`` only. A bare name is not
always enough to decide identity safely, and this module is where that limitation
becomes explicit rather than being papered over.

The rules:

* **A qualified expectation only matches a fully qualified observation.** Writing
  ``pytest.raises(mymodule.MyError)`` proves the test author meant *that* class, so
  an observation recorded only as ``MyError`` cannot confirm it. The result is
  ``INCONCLUSIVE``, not a match and not a mismatch.
* **A builtin expectation may use the real builtin hierarchy.** ``pytest.raises(
  ValueError)`` is satisfied by ``UnicodeDecodeError`` in Python, and EdgeHunter
  knows the standard library's own inheritance without importing the target. The
  hierarchy is derived from :mod:`builtins` on the host, where importing
  ``builtins`` cannot execute target code.
* **A custom expectation matched by bare name only is ``INCONCLUSIVE``.** Two
  unrelated target modules may each define ``PaymentError``. Without a qualified
  observation there is no evidence they are the same class, and guessing from
  filename similarity is exactly the behaviour this project refuses.
"""

from __future__ import annotations

import builtins
from dataclasses import dataclass
from typing import Final

from edgehunter.evaluation.models import OracleStatus


@dataclass(frozen=True, slots=True)
class ExceptionMatch:
    """The verdict for one expected-versus-observed exception comparison."""

    status: OracleStatus
    reason: str
    matched_subclass: bool = False
    """True when a builtin subclass satisfied a builtin expectation."""


@dataclass(frozen=True, slots=True)
class ExceptionIdentity:
    """What the sandbox could establish about the class of a raised exception.

    This is an **observation**, not an identity proof, and it deliberately keeps
    "unknown" representable. Every field except :attr:`name` is optional because
    a result document written before these fields existed still has to decode.

    :attr:`is_builtin` is the important one. It is ``True`` only when the class
    *is* one of the real ``builtins`` exception classes, compared by identity in
    the sandbox. A target that defines ``class ValueError(Exception)`` therefore
    reports ``False``: same name, different class. ``None`` means the field was
    never reported, which must never be read as ``False`` or as ``True``.
    """

    name: str | None = None
    module: str | None = None
    qualname: str | None = None
    is_builtin: bool | None = None
    builtin_lineage: tuple[str, ...] | None = None

    @property
    def has_qualified_identity(self) -> bool:
        """True when a module-qualified name was observed."""
        return bool(self.qualname)

    @property
    def display_name(self) -> str:
        """How this identity is shown in a report."""
        if self.qualname:
            if self.module and self.module != "builtins":
                return f"{self.module}.{self.qualname}"
            return self.qualname
        return self.name or "unknown"


def _builtin_exception_subclasses() -> dict[str, frozenset[str]]:
    """Map each builtin exception name to the builtin names that subclass it.

    Built from :mod:`builtins` by introspection rather than a hand-written table,
    so it cannot drift from the interpreter's own definition. Only names actually
    present in ``builtins`` are recorded, which keeps this a statement about the
    standard library and never about target code.
    """
    builtin_names: set[str] = set()
    for name in dir(builtins):
        candidate = getattr(builtins, name, None)
        if isinstance(candidate, type) and issubclass(candidate, BaseException):
            builtin_names.add(candidate.__name__)

    subclasses: dict[str, set[str]] = {name: set() for name in builtin_names}
    for name in builtin_names:
        candidate = getattr(builtins, name)
        for base in candidate.__mro__[1:]:
            if base is BaseException or base.__name__ not in subclasses:
                continue
            subclasses[base.__name__].add(name)
    return {name: frozenset(children) for name, children in subclasses.items()}


_BUILTIN_SUBCLASSES: Final[dict[str, frozenset[str]]] = _builtin_exception_subclasses()
_BUILTIN_EXCEPTION_NAMES: Final[frozenset[str]] = frozenset(_BUILTIN_SUBCLASSES)


def is_builtin_exception(name: str) -> bool:
    """True when the bare name is a standard-library exception class."""
    return name in _BUILTIN_EXCEPTION_NAMES


def exception_descendants(name: str) -> frozenset[str]:
    """Every builtin exception name that is ``name`` or subclasses it."""
    if name not in _BUILTIN_SUBCLASSES:
        return frozenset()
    return frozenset({name}) | _BUILTIN_SUBCLASSES[name]


def match_exception(expected: str | None, observed: str | None) -> ExceptionMatch:
    """Decide whether an observed exception satisfies ``pytest.raises(expected)``.

    An absent expectation or an absent observation is always ``INCONCLUSIVE``: the
    evaluator must never invent either side.

    This is the string-only form, kept for observations that carry no richer
    identity. It is deliberately the Phase 6B behaviour: a bare name proves
    nothing beyond the name, so custom identity stays ``INCONCLUSIVE``.
    :func:`match_exception_observed` is the form that can use sandbox evidence.
    """
    return match_exception_observed(expected, ExceptionIdentity(name=observed))


def match_exception_observed(
    expected: str | None,
    observed: ExceptionIdentity,
    *,
    expected_binding: str | None = None,
) -> ExceptionMatch:
    """Decide whether an observed exception satisfies ``pytest.raises(expected)``.

    ``expected_binding`` names a conflicting binding of the expected name found in
    the test module's own source. A bare spelling of a builtin name is only
    evidence of a builtin when nothing in the test module shadows it.

    The rules, in order of how much they are allowed to conclude:

    * **A builtin expectation against a real builtin class** uses the standard
      library hierarchy, so ``UnicodeDecodeError`` satisfies ``ValueError`` the way
      Python itself does. This is a fact about ``builtins``, not about the target.
    * **A builtin expectation against a custom class with a trusted lineage** may
      conclude: the sandbox walked the class's real MRO, so ``class
      MyValueError(ValueError)`` matches ``ValueError`` and ``class
      MyValueError(Exception)`` provably does not. The lineage is trusted exactly
      as far as the builtin hierarchy already is.
    * **A custom expectation stays ``INCONCLUSIVE``.** A qualified observation such
      as ``myapp.errors.InvalidInput`` is good diagnostic evidence, but the test
      may have imported or aliased ``errors`` differently, so the expectation's
      class cannot be resolved without importing target code. Textual similarity
      is never promoted to a match.
    * **No identity evidence yields ``INCONCLUSIVE`` for a builtin expectation
      too.** A bare name proves nothing in either direction, so a result document
      without the identity fields is still decodable but supports no verdict. This
      is the correction recorded in D-078.
    """
    if expected is None:
        return ExceptionMatch(
            status=OracleStatus.INCONCLUSIVE,
            reason="the recorded expectation does not name an exception class",
        )
    if observed.name is None and observed.qualname is None:
        return ExceptionMatch(
            status=OracleStatus.INCONCLUSIVE,
            reason="the execution recorded no exception type, so identity cannot be compared",
        )

    expected_tail = expected.rsplit(".", 1)[-1]
    expected_qualified = "." in expected
    expected_is_builtin = is_builtin_exception(expected_tail)
    observed_text = observed.display_name
    observed_tail = (observed.qualname or observed.name or "").rsplit(".", 1)[-1]
    observed_qualified = observed.module is not None or "." in observed_tail

    # A builtin-looking expectation is not proof when the test module itself binds
    # that name: `class ValueError(Exception)` beside `pytest.raises(ValueError)`
    # refers to the module's own class, not the standard library's.
    if expected_is_builtin and expected_binding is not None:
        return ExceptionMatch(
            status=OracleStatus.INCONCLUSIVE,
            reason=(
                f"the test module binds the name {expected!r} ({expected_binding}), so the "
                "expectation cannot be assumed to name the standard-library class; resolving "
                "test-local shadowing needs import resolution, which is not performed"
            ),
        )

    if not expected_is_builtin:
        if expected_qualified and not observed_qualified:
            return ExceptionMatch(
                status=OracleStatus.INCONCLUSIVE,
                reason=(
                    f"the expectation names {expected!r} but the runtime recorded only the "
                    f"unqualified name {observed_text!r}; a different module may define the "
                    "same class name, so identity cannot be established"
                ),
            )
        return ExceptionMatch(
            status=OracleStatus.INCONCLUSIVE,
            reason=(
                f"{expected!r} is not a standard-library exception and the runtime recorded "
                "the class name only; the class identity cannot be established without "
                "importing the target module, even though the runtime observed "
                f"{observed_text!r}"
            ),
        )

    # From here the expectation names a real builtin class. Everything below uses
    # the standard library's own hierarchy plus whatever the sandbox reported.
    if observed.is_builtin is True:
        if observed_tail == expected_tail:
            return ExceptionMatch(
                status=OracleStatus.MATCH, reason=f"the execution raised {observed_text}"
            )
        if observed_tail in exception_descendants(expected_tail):
            return ExceptionMatch(
                status=OracleStatus.MATCH,
                reason=f"{observed_text} is a subclass of the expected {expected}",
                matched_subclass=True,
            )
        return ExceptionMatch(
            status=OracleStatus.MISMATCH,
            reason=f"the execution raised {observed_text}, which is not the expected {expected}",
        )

    if observed.is_builtin is False:
        lineage = observed.builtin_lineage
        if lineage is None:
            return ExceptionMatch(
                status=OracleStatus.INCONCLUSIVE,
                reason=(
                    f"the execution raised {observed_text}, which is not a standard-library "
                    f"exception, and the sandbox reported no inheritance information for it, so "
                    f"it cannot be shown to be a subclass of {expected_tail}"
                ),
            )
        if expected_tail in lineage:
            return ExceptionMatch(
                status=OracleStatus.MATCH,
                reason=(
                    f"the execution raised {observed_text}, whose recorded inheritance chain "
                    f"contains the builtin {expected_tail}"
                ),
                matched_subclass=True,
            )
        return ExceptionMatch(
            status=OracleStatus.MISMATCH,
            reason=(
                f"the execution raised {observed_text}, whose recorded inheritance chain "
                f"({', '.join(lineage) or 'none'}) does not contain {expected_tail}"
            ),
        )

    # No identity evidence at all. A name is not an identity, in *either*
    # direction, so this cannot be decided rather than merely not being decided
    # in one direction:
    #
    #   observed "ValueError", expected builtin ValueError -> MATCH is unsound,
    #   because the raised class may be a target's own `class ValueError(Exception)`
    #   which `pytest.raises(builtin ValueError)` would not catch.
    #
    #   observed "KeyError", expected builtin ValueError -> MISMATCH is unsound,
    #   because a target's own `class KeyError(ValueError)` *does* satisfy it.
    #
    # Phase 7A kept a name-only fallback here "so an older result document is not
    # silently downgraded". That traded a false MATCH for the appearance of
    # continuity, which is the wrong direction for this project: the document is
    # still decodable, it just no longer supports a verdict it never had the
    # evidence for. See D-078.
    return ExceptionMatch(
        status=OracleStatus.INCONCLUSIVE,
        reason=(
            f"the execution recorded only the unqualified name {observed_text!r} and reported no "
            f"evidence of whether it is a standard-library class, so a name comparison against "
            f"{expected_tail} proves nothing in either direction"
        ),
    )


def observation_exception_key(observed: str | None) -> str:
    """Stable identity component for an exception observation.

    Only the class identity participates. The message is excluded because it can
    embed a value, a path, or a memory address, none of which should affect whether
    two runs produced the same observation.
    """
    return f"exception:{observed or 'unknown'}"


def assertion_error_key() -> str:
    """Identity component for a production assertion being hit."""
    return observation_exception_key("AssertionError")
