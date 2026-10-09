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
    """
    if expected is None:
        return ExceptionMatch(
            status=OracleStatus.INCONCLUSIVE,
            reason="the recorded expectation does not name an exception class",
        )
    if observed is None:
        return ExceptionMatch(
            status=OracleStatus.INCONCLUSIVE,
            reason="the execution recorded no exception type, so identity cannot be compared",
        )

    expected_tail = expected.rsplit(".", 1)[-1]
    expected_qualified = "." in expected
    observed_tail = observed.rsplit(".", 1)[-1]
    observed_qualified = "." in observed

    if expected_qualified:
        if not observed_qualified:
            return ExceptionMatch(
                status=OracleStatus.INCONCLUSIVE,
                reason=(
                    f"the expectation names {expected!r} but the runtime recorded only the "
                    f"unqualified name {observed!r}; a different module may define the same "
                    "class name, so identity is not established"
                ),
            )
        if expected == observed:
            return ExceptionMatch(
                status=OracleStatus.MATCH, reason=f"the execution raised {expected}"
            )
        if is_builtin_exception(expected_tail) and is_builtin_exception(observed_tail):
            # Both are builtin and both fully qualified: `builtins` is the only
            # module these can be, so the hierarchy is trustworthy here.
            if observed_tail in exception_descendants(expected_tail):
                return ExceptionMatch(
                    status=OracleStatus.MATCH,
                    reason=f"{observed} is a subclass of the expected {expected}",
                    matched_subclass=True,
                )
        return ExceptionMatch(
            status=OracleStatus.MISMATCH,
            reason=f"the execution raised {observed}, which is not the expected {expected}",
        )

    # A bare expectation name.
    if not is_builtin_exception(expected_tail):
        return ExceptionMatch(
            status=OracleStatus.INCONCLUSIVE,
            reason=(
                f"{expected!r} is not a standard-library exception and the runtime recorded "
                "only an unqualified name; the class identity cannot be established without "
                "importing the target module"
            ),
        )
    if observed_qualified:
        if observed.rsplit(".", 1)[0] != "builtins":
            return ExceptionMatch(
                status=OracleStatus.INCONCLUSIVE,
                reason=(
                    f"the expectation is the builtin {expected_tail} but the execution raised "
                    f"{observed}, which is defined outside the standard library"
                ),
            )
    if observed_tail == expected_tail:
        return ExceptionMatch(
            status=OracleStatus.MATCH, reason=f"the execution raised the expected {expected_tail}"
        )
    if observed_tail in exception_descendants(expected_tail):
        return ExceptionMatch(
            status=OracleStatus.MATCH,
            reason=(
                f"the execution raised {observed_tail}, which Python defines as a subclass "
                f"of {expected_tail}"
            ),
            matched_subclass=True,
        )
    return ExceptionMatch(
        status=OracleStatus.MISMATCH,
        reason=f"the execution raised {observed_tail}, which is not {expected_tail} or a subclass",
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
