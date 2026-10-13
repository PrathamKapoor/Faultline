"""The failure signature that a shrink candidate must preserve.

A smaller input is not automatically the same failure. If the original call
returned normally where a ``ValueError`` was expected, then a trial that raises
``TypeError`` is *also* a contradiction of the contract -- but it is a different
observed behaviour, and reporting it as a reduction of the original one would be
misleading. So acceptance compares a signature, not merely a status.

The signature deliberately uses only what the protocol establishes as class
identity: the outcome category, the observed exception's qualified identity, and
the builtin flag the sandbox determined. It never compares exception messages,
durations, execution ids, container ids, or temporary paths, because none of
those is a property of the failure. An infrastructure outcome has **no** signature
at all, which is what makes it unusable as a comparison basis.
"""

from __future__ import annotations

from dataclasses import dataclass

from edgehunter.evaluation.evaluator import identity_from_observation
from edgehunter.execution.models import OutcomeKind, RuntimeObservation


@dataclass(frozen=True, slots=True)
class FailureSignature:
    """The observable character of one failure, as a comparable value.

    ``raised`` is ``False`` for a normal return, in which case the remaining
    fields are empty by construction rather than by sentinel values.
    """

    outcome: str
    raised: bool
    exception_identity: str = ""
    exception_is_builtin: bool | None = None

    def matches(self, other: FailureSignature) -> bool:
        """Whether two observations are the same kind of failure.

        A normal return only ever matches another normal return, and a raised
        exception must have the same observed identity *and* the same builtin
        status. The identity comparison is by the text the worker reported, which
        for a custom class is a qualified name when one is available -- this is an
        observation comparison, not a proof that two classes are the same, and it
        inherits the limitation D-061 records.
        """

        if self.raised is not other.raised or self.outcome != other.outcome:
            return False
        if not self.raised:
            return True
        if self.exception_identity != other.exception_identity:
            return False
        if self.exception_is_builtin != other.exception_is_builtin:
            return False
        return True

    def describe(self) -> str:
        """A short rendering for the CLI and the evidence artifact."""

        if not self.raised:
            return "returned normally"
        return f"raised {self.exception_identity or 'unknown exception'}"


def signature_of(observation: RuntimeObservation) -> FailureSignature | None:
    """Build the signature of one observation, or ``None`` if it is not a failure.

    ``None`` means the outcome cannot be compared at all: a timeout, an import
    failure, or a protocol error says nothing about what the target does with a
    given input, so it must never be treated as a matching (or mismatching)
    failure.
    """

    if observation.outcome is OutcomeKind.RETURNED:
        return FailureSignature(outcome=observation.outcome.value, raised=False)
    if observation.outcome is OutcomeKind.RAISED:
        identity = identity_from_observation(observation)
        return FailureSignature(
            outcome=observation.outcome.value,
            raised=True,
            exception_identity=identity.display_name,
            exception_is_builtin=identity.is_builtin,
        )
    return None
