"""The constrained action vocabulary and typed candidate inputs.

This package answers "which interesting inputs are worth trying, and can they
be written down?". A *semantic action* is one entry from a finite vocabulary,
applied to exactly one declared parameter of one target, and justified either by
a mined boundary candidate or by the parameter's declared annotation. Nothing
here is executed: a concrete value is a *piece of Python source text* for a
future phase to use, never a value this process computed by running the target.

Two rules keep the vocabulary honest:

* the vocabulary is closed. Every action EdgeHunter can propose is a member of
  :class:`SemanticActionKind`, so "what could it possibly try?" has one answer;
* absence is meaningful. A parameter with an unknown or unparameterized
  annotation gets no generic action at all. Reporting fewer actions is better
  than inventing a value for a type the scanner could not recognize.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final

from edgehunter.analysis.models import BoundaryKind
from edgehunter.scanner.annotations import StaticAnnotation


class SemanticActionKind(str, Enum):
    """The finite vocabulary of inputs EdgeHunter is allowed to propose.

    Ten members correspond one-to-one with the numeric and relational
    :class:`~edgehunter.analysis.models.BoundaryKind` values that boundary
    mining can produce. The remaining eleven are annotation-driven and have no
    boundary counterpart: ``SET_NONE`` (the annotation permits ``None``), the
    three collection shapes, the two boolean values, and the five string edge
    shapes.

    The boolean and string members exist because a recognized ``bool`` or ``str``
    parameter was previously unreachable: no boundary candidate applies to a
    non-numeric annotation, so such a parameter received no action, no candidate
    case, and therefore no invocation plan. They are annotation-driven for
    exactly the same reason the collection members are.
    """

    JUST_BELOW_BOUNDARY = "just_below_boundary"
    AT_BOUNDARY = "at_boundary"
    JUST_ABOVE_BOUNDARY = "just_above_boundary"
    SET_ZERO = "set_zero"
    SET_ONE = "set_one"
    SET_NEGATIVE_ONE = "set_negative_one"
    SET_NEGATIVE_ZERO = "set_negative_zero"
    SET_NONE = "set_none"
    USE_NAN = "use_nan"
    USE_POSITIVE_INFINITY = "use_positive_infinity"
    USE_NEGATIVE_INFINITY = "use_negative_infinity"
    EMPTY_COLLECTION = "empty_collection"
    SINGLETON_COLLECTION = "singleton_collection"
    DUPLICATE_ELEMENT_COLLECTION = "duplicate_element_collection"
    SET_FALSE = "set_false"
    SET_TRUE = "set_true"
    EMPTY_STRING = "empty_string"
    WHITESPACE_STRING = "whitespace_string"
    UNICODE_STRING = "unicode_string"
    NULL_CHARACTER_STRING = "null_character_string"
    LONG_STRING = "long_string"


ACTION_KIND_ORDER: Final[dict[SemanticActionKind, int]] = {
    SemanticActionKind.JUST_BELOW_BOUNDARY: 0,
    SemanticActionKind.AT_BOUNDARY: 1,
    SemanticActionKind.JUST_ABOVE_BOUNDARY: 2,
    SemanticActionKind.SET_ZERO: 3,
    SemanticActionKind.SET_ONE: 4,
    SemanticActionKind.SET_NEGATIVE_ONE: 5,
    SemanticActionKind.SET_NEGATIVE_ZERO: 6,
    SemanticActionKind.SET_NONE: 7,
    SemanticActionKind.USE_NAN: 8,
    SemanticActionKind.USE_POSITIVE_INFINITY: 9,
    SemanticActionKind.USE_NEGATIVE_INFINITY: 10,
    SemanticActionKind.EMPTY_COLLECTION: 11,
    SemanticActionKind.SINGLETON_COLLECTION: 12,
    SemanticActionKind.DUPLICATE_ELEMENT_COLLECTION: 13,
    SemanticActionKind.SET_FALSE: 14,
    SemanticActionKind.SET_TRUE: 15,
    SemanticActionKind.EMPTY_STRING: 16,
    SemanticActionKind.WHITESPACE_STRING: 17,
    SemanticActionKind.UNICODE_STRING: 18,
    SemanticActionKind.NULL_CHARACTER_STRING: 19,
    SemanticActionKind.LONG_STRING: 20,
}


class MaterializationStatus(str, Enum):
    """How far a proposed action could be turned into an actual value."""

    CONCRETE = "concrete"
    """A self-contained Python expression exists; no runtime knowledge needed."""

    SYMBOLIC = "symbolic"
    """The value depends on something not knowable statically, such as a name."""

    UNSUPPORTED = "unsupported"
    """No value of this kind can be written down for this parameter."""


class LiteralKind(str, Enum):
    """Category of a materialized concrete value, for display and later phases."""

    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    TEXT = "text"
    BYTES = "bytes"
    COMPLEX = "complex"
    NOTHING = "nothing"
    COLLECTION = "collection"


class ActionOrigin(str, Enum):
    """Why an action was proposed for a parameter."""

    BOUNDARY = "boundary"
    """Derived from a mined boundary candidate in the target's own source."""

    ANNOTATION = "annotation"
    """Derived from the parameter's declared annotation, with no comparison."""


class CandidateCompleteness(str, Enum):
    """Whether a candidate case carries enough information to be run as-is.

    This describes the *case*, not the action: a fully concrete value still
    leaves a multi-parameter target under-specified.
    """

    COMPLETE = "complete"
    """The action materialized concretely and every required parameter is covered."""

    PARTIAL = "partial"
    """The action materialized concretely, but required parameters are uncovered."""

    SYMBOLIC = "symbolic"
    """The action's value depends on a name, so no concrete input exists yet."""


@dataclass(frozen=True, slots=True)
class ActionEvidence:
    """One source location that justified an action."""

    relative_path: Path
    lineno: int
    detail: str


@dataclass(frozen=True, slots=True)
class SemanticAction:
    """One constrained action applicable to one parameter of one target.

    ``status`` says whether a value could be written down at all. Exactly one of
    ``value_text`` (a concrete Python expression) or ``symbolic_text`` (a name
    the value depends on) is set when the status is not ``UNSUPPORTED``, and
    ``literal_kind`` is set only for concrete values.

    Two reasons are kept apart on purpose: ``reason`` explains why the action was
    proposed for this parameter, while ``materialization_reason`` explains how the
    value was (or was not) written down.
    """

    relative_path: Path
    target: str
    lineno: int
    signature: str
    parameter: str
    kind: SemanticActionKind
    origin: ActionOrigin
    status: MaterializationStatus
    annotation: StaticAnnotation
    boundary_kind: BoundaryKind | None
    reference: str | None
    value_text: str | None
    symbolic_text: str | None
    literal_kind: LiteralKind | None
    reason: str
    materialization_reason: str
    evidence: tuple[ActionEvidence, ...]

    @property
    def description(self) -> str:
        """Human-readable rendering of the action."""
        if self.kind in (
            SemanticActionKind.JUST_BELOW_BOUNDARY,
            SemanticActionKind.AT_BOUNDARY,
            SemanticActionKind.JUST_ABOVE_BOUNDARY,
        ):
            return f"{self.kind.value} {self.reference}"
        return f"{self.kind.value}"


@dataclass(frozen=True, slots=True)
class TargetActions:
    """Every action applicable to one callable target.

    ``required_parameters`` lists the parameters a caller must supply: no
    default, not ``*args``/``**kwargs``, and not the implicit receiver of a
    method. It is recorded so candidate cases can report what they leave
    uncovered without inventing values for those parameters.
    """

    relative_path: Path
    target: str
    lineno: int
    signature: str
    required_parameters: tuple[str, ...]
    actions: tuple[SemanticAction, ...]


@dataclass(frozen=True, slots=True)
class CandidateCase:
    """One primary action applied to exactly one parameter.

    A case never combines several actions and never supplies a value for another
    parameter: doing either would mean inventing a baseline input that no
    evidence supports. ``missing_parameters`` names the required parameters the
    case leaves uncovered instead of guessing them.
    """

    relative_path: Path
    target: str
    lineno: int
    signature: str
    action: SemanticAction
    completeness: CandidateCompleteness
    missing_parameters: tuple[str, ...]

    @property
    def parameter(self) -> str:
        """Parameter the primary action is applied to."""
        return self.action.parameter

    @property
    def status(self) -> MaterializationStatus:
        """Materialization status of the primary action."""
        return self.action.status


@dataclass(frozen=True, slots=True)
class RepositoryGeneration:
    """All constrained actions and candidate cases derived for a repository."""

    action_sets: tuple[TargetActions, ...]
    cases: tuple[CandidateCase, ...]

    @property
    def action_count(self) -> int:
        """Total number of proposed actions."""
        return sum(len(entry.actions) for entry in self.action_sets)

    @property
    def case_count(self) -> int:
        """Total number of candidate cases."""
        return len(self.cases)

    @property
    def targets_with_actions(self) -> int:
        """Number of targets that received at least one action."""
        return len(self.action_sets)

    @property
    def concrete_action_count(self) -> int:
        """Number of actions that materialized to a concrete value."""
        return self._count_status(MaterializationStatus.CONCRETE)

    @property
    def symbolic_action_count(self) -> int:
        """Number of actions whose value depends on a name."""
        return self._count_status(MaterializationStatus.SYMBOLIC)

    @property
    def unsupported_action_count(self) -> int:
        """Number of actions that could not be written down at all."""
        return self._count_status(MaterializationStatus.UNSUPPORTED)

    @property
    def complete_case_count(self) -> int:
        """Number of cases that cover every required parameter."""
        return self._count_completeness(CandidateCompleteness.COMPLETE)

    @property
    def partial_case_count(self) -> int:
        """Number of cases that leave required parameters uncovered."""
        return self._count_completeness(CandidateCompleteness.PARTIAL)

    @property
    def symbolic_case_count(self) -> int:
        """Number of cases whose primary action is still symbolic."""
        return self._count_completeness(CandidateCompleteness.SYMBOLIC)

    @property
    def targets_with_cases(self) -> int:
        """Number of targets that received at least one candidate case."""
        return len({(case.relative_path, case.target) for case in self.cases})

    def _count_status(self, status: MaterializationStatus) -> int:
        """Count actions in one materialization status."""
        return sum(
            1 for entry in self.action_sets for action in entry.actions if action.status is status
        )

    def _count_completeness(self, completeness: CandidateCompleteness) -> int:
        """Count cases in one completeness state."""
        return sum(1 for case in self.cases if case.completeness is completeness)
