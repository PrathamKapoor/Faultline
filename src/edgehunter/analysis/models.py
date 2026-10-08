"""Immutable models for static semantic analysis and boundary mining.

Every model here describes *syntactic* evidence collected from a parsed AST.
Nothing in this package imports, evaluates, or executes analyzed code, and no
model claims that a discovered candidate is a defect: a boundary candidate is
a region worth testing, nothing more.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final


class ExpressionKind(str, Enum):
    """Coarse syntactic category of an expression."""

    NAME = "name"
    ATTRIBUTE = "attribute"
    CONSTANT = "constant"
    CALL = "call"
    BINARY = "binary"
    UNARY = "unary"
    SUBSCRIPT = "subscript"
    OTHER = "other"


class ComparisonOperator(str, Enum):
    """Comparison operators, as written in the source."""

    LESS_THAN = "<"
    LESS_THAN_OR_EQUAL = "<="
    GREATER_THAN = ">"
    GREATER_THAN_OR_EQUAL = ">="
    EQUAL = "=="
    NOT_EQUAL = "!="
    IS = "is"
    IS_NOT = "is not"
    IN = "in"
    NOT_IN = "not in"


INVERTIBLE_COMPARISON_OPERATORS: Final[dict[ComparisonOperator, ComparisonOperator]] = {
    ComparisonOperator.LESS_THAN: ComparisonOperator.GREATER_THAN,
    ComparisonOperator.LESS_THAN_OR_EQUAL: ComparisonOperator.GREATER_THAN_OR_EQUAL,
    ComparisonOperator.GREATER_THAN: ComparisonOperator.LESS_THAN,
    ComparisonOperator.GREATER_THAN_OR_EQUAL: ComparisonOperator.LESS_THAN_OR_EQUAL,
    ComparisonOperator.EQUAL: ComparisonOperator.EQUAL,
    ComparisonOperator.NOT_EQUAL: ComparisonOperator.NOT_EQUAL,
}


def invert_comparison_operator(operator: ComparisonOperator) -> ComparisonOperator | None:
    """Return the equivalent operator with the two sides swapped.

    ``None`` means the reversal is not provably equivalent and the caller must
    not rewrite the comparison. ``is``, ``is not``, ``in``, and ``not in`` are
    deliberately excluded.
    """
    return INVERTIBLE_COMPARISON_OPERATORS.get(operator)


class ArithmeticOperator(str, Enum):
    """Binary arithmetic operators recorded on arithmetic expressions."""

    ADD = "+"
    SUBTRACT = "-"
    MULTIPLY = "*"
    DIVIDE = "/"
    FLOOR_DIVIDE = "//"
    MODULO = "%"
    POWER = "**"
    MATRIX_MULTIPLY = "@"


class ArithmeticContext(str, Enum):
    """Where an arithmetic expression appeared."""

    RETURN = "return"
    ASSIGNMENT = "assignment"
    AUGMENTED_ASSIGNMENT = "augmented_assignment"
    EXPRESSION = "expression"


class PredicateKind(str, Enum):
    """Syntactic source of a predicate."""

    IF = "if"
    WHILE = "while"
    ASSERT = "assert"


class ConstantKind(str, Enum):
    """Kind of a literal constant; ``bool`` stays separate from ``int``.

    ``...`` (Ellipsis) is deliberately absent: it is a stub placeholder, not a
    value literal, so it is not recorded.
    """

    INTEGER = "integer"
    FLOAT = "float"
    COMPLEX = "complex"
    STRING = "string"
    BYTES = "bytes"
    BOOL = "bool"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class ExpressionInfo:
    """An expression recorded as source text, never as a runtime value.

    ``names`` lists the referenced names in first-appearance order. ``simple``
    is true for literals, bare names, and plain dotted name chains: those are
    stable references a future generator can materialize without evaluating
    anything. ``constant_kind`` is set only for literal constants.
    """

    text: str
    kind: ExpressionKind
    names: tuple[str, ...]
    simple: bool = False
    constant_kind: ConstantKind | None = None


@dataclass(frozen=True, slots=True)
class ComparisonInfo:
    """One adjacent comparison; a chained comparison is decomposed into pairs."""

    left: ExpressionInfo
    operator: ComparisonOperator
    right: ExpressionInfo
    lineno: int


@dataclass(frozen=True, slots=True)
class PredicateInfo:
    """A condition taken from ``if``, ``while``, or ``assert``.

    An ``assert`` predicate is recorded as syntax only. Whether it is a real
    program property is a separate question belonging to a later phase.
    """

    kind: PredicateKind
    expression: ExpressionInfo
    comparisons: tuple[ComparisonInfo, ...]
    lineno: int


@dataclass(frozen=True, slots=True)
class ArithmeticInfo:
    """A maximal arithmetic subexpression and the operators it contains.

    No simplification or constant folding is performed: this is the syntax as
    written.
    """

    expression: ExpressionInfo
    operators: tuple[ArithmeticOperator, ...]
    context: ArithmeticContext
    lineno: int


@dataclass(frozen=True, slots=True)
class ConstantInfo:
    """A literal constant, including signed numeric literals such as ``-1``."""

    value_text: str
    kind: ConstantKind
    lineno: int


@dataclass(frozen=True, slots=True)
class TargetSemantics:
    """Semantic facts collected inside one callable target."""

    qualified_name: str
    lineno: int
    predicates: tuple[PredicateInfo, ...]
    comparisons: tuple[ComparisonInfo, ...]
    arithmetic: tuple[ArithmeticInfo, ...]
    constants: tuple[ConstantInfo, ...]


class BoundaryKind(str, Enum):
    """Finite vocabulary of regions a future generator may attack."""

    JUST_BELOW = "just_below"
    AT_BOUNDARY = "at_boundary"
    JUST_ABOVE = "just_above"
    ZERO = "zero"
    ONE = "one"
    NEGATIVE_ONE = "negative_one"
    NEGATIVE_ZERO = "negative_zero"
    NAN = "nan"
    POSITIVE_INFINITY = "positive_infinity"
    NEGATIVE_INFINITY = "negative_infinity"


BOUNDARY_KIND_ORDER: Final[dict[BoundaryKind, int]] = {
    BoundaryKind.JUST_BELOW: 0,
    BoundaryKind.AT_BOUNDARY: 1,
    BoundaryKind.JUST_ABOVE: 2,
    BoundaryKind.ZERO: 3,
    BoundaryKind.ONE: 4,
    BoundaryKind.NEGATIVE_ONE: 5,
    BoundaryKind.NEGATIVE_ZERO: 6,
    BoundaryKind.NAN: 7,
    BoundaryKind.POSITIVE_INFINITY: 8,
    BoundaryKind.NEGATIVE_INFINITY: 9,
}


class BoundaryOrigin(str, Enum):
    """How a candidate was derived."""

    COMPARISON = "comparison"
    NUMERIC = "numeric"


_GENERIC_DESCRIPTIONS: Final[dict[BoundaryKind, str]] = {
    BoundaryKind.ZERO: "0",
    BoundaryKind.ONE: "1",
    BoundaryKind.NEGATIVE_ONE: "-1",
    BoundaryKind.NEGATIVE_ZERO: "-0.0",
    BoundaryKind.NAN: "NaN",
    BoundaryKind.POSITIVE_INFINITY: "+Infinity",
    BoundaryKind.NEGATIVE_INFINITY: "-Infinity",
}


@dataclass(frozen=True, slots=True)
class BoundaryCandidate:
    """A candidate test region for one parameter of one target.

    ``reference`` stays symbolic: for ``amount <= balance`` the candidate is
    "amount at balance", never a concrete number such as ``balance - 1``.
    ``evidence`` lists every source line that suggested this candidate, sorted
    and deduplicated.
    """

    target: str
    parameter: str
    kind: BoundaryKind
    origin: BoundaryOrigin
    reference: str | None
    evidence: tuple[int, ...]
    reason: str

    @property
    def description(self) -> str:
        """Human-readable rendering of the candidate region."""
        if self.kind is BoundaryKind.JUST_BELOW:
            return f"just below {self.reference}"
        if self.kind is BoundaryKind.AT_BOUNDARY:
            return f"at {self.reference}"
        if self.kind is BoundaryKind.JUST_ABOVE:
            return f"just above {self.reference}"
        return _GENERIC_DESCRIPTIONS[self.kind]


@dataclass(frozen=True, slots=True)
class TargetBoundaries:
    """Boundary candidates for one callable target."""

    relative_path: Path
    qualified_name: str
    lineno: int
    signature: str
    candidates: tuple[BoundaryCandidate, ...]


@dataclass(frozen=True, slots=True)
class ModuleSemantics:
    """Semantic facts and boundary candidates for one module."""

    path: Path
    relative_path: Path
    targets: tuple[TargetSemantics, ...]
    boundaries: tuple[TargetBoundaries, ...]

    @property
    def predicate_count(self) -> int:
        """Number of predicates recorded in this module."""
        return sum(len(target.predicates) for target in self.targets)

    @property
    def comparison_count(self) -> int:
        """Number of adjacent comparisons recorded in this module."""
        return sum(len(target.comparisons) for target in self.targets)

    @property
    def arithmetic_expression_count(self) -> int:
        """Number of maximal arithmetic expressions recorded in this module."""
        return sum(len(target.arithmetic) for target in self.targets)

    @property
    def boundary_candidate_count(self) -> int:
        """Number of boundary candidates in this module."""
        return sum(len(entry.candidates) for entry in self.boundaries)


class RiskFeatureKind(str, Enum):
    """One explainable component of a target's structural search priority."""

    PREDICATES = "predicates"
    ARITHMETIC = "arithmetic"
    BOUNDARY_GROUPS = "boundary_groups"
    INPUT_PARAMETERS = "input_parameters"
    SOURCE_SPAN = "source_span"


@dataclass(frozen=True, slots=True)
class RiskContribution:
    """Points contributed by one risk feature, with a human-readable reason."""

    kind: RiskFeatureKind
    points: int
    detail: str


@dataclass(frozen=True, slots=True)
class TargetRiskProfile:
    """Explainable structural search-priority score for one callable target.

    This is a deterministic heuristic for allocating testing budget. It is not
    a probability, not a bug estimate, and not a security severity.
    """

    target: str
    relative_path: Path
    lineno: int
    signature: str
    score: int
    contributions: tuple[RiskContribution, ...]


@dataclass(frozen=True, slots=True)
class RepositoryRiskProfile:
    """Structural risk profiles for every callable target, ranked."""

    profiles: tuple[TargetRiskProfile, ...]

    @property
    def scored_target_count(self) -> int:
        """Number of targets that received a risk profile."""
        return len(self.profiles)

    @property
    def highest_score(self) -> int:
        """Highest structural score in the repository, or 0 when there are none."""
        return max((profile.score for profile in self.profiles), default=0)

    @property
    def targets_with_score_above_zero(self) -> int:
        """Number of targets whose score exceeds zero."""
        return sum(1 for profile in self.profiles if profile.score > 0)
