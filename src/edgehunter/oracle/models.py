"""Immutable models for mined oracle and property evidence.

Every model records *explicit repository evidence*: what the source itself
says should happen. Evidence is never a defect claim. Nothing here imports,
evaluates, or executes analyzed code, and strength is categorical rather than
a fabricated numeric confidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from edgehunter.analysis.models import ComparisonOperator


class OracleStrength(str, Enum):
    """Categorical strength of the evidence source, never a fake percentage."""

    STRONG = "strong"


class OracleEvidenceKind(str, Enum):
    """Where an oracle signal came from."""

    PRODUCTION_ASSERTION = "production_assertion"
    VALIDATION_GUARD = "validation_guard"
    TEST_ASSERTION = "test_assertion"
    TEST_RAISES = "test_raises"


class PropertyKind(str, Enum):
    """Kind of mined property/expectation."""

    ASSERTED_CONDITION = "asserted_condition"
    REJECTION_RULE = "rejection_rule"
    EXAMPLE_EXPECTATION = "example_expectation"
    EXAMPLE_RAISES = "example_raises"


class ExpectedOutcome(str, Enum):
    """What the source says should happen."""

    CONDITION_HOLDS = "condition_holds"
    RETURNS = "returns"
    RAISES = "raises"


class TargetResolution(str, Enum):
    """How confidently a mined signal was tied to a production target."""

    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    AMBIGUOUS = "ambiguous"


class OracleSupport(str, Enum):
    """Overall oracle availability for a target; independent of risk score."""

    NONE = "none"
    STRONG = "strong"


@dataclass(frozen=True, slots=True)
class OracleEvidence:
    """A single supporting source location for an oracle signal."""

    kind: OracleEvidenceKind
    relative_path: Path
    lineno: int
    detail: str


@dataclass(frozen=True, slots=True)
class ProductionPropertyCandidate:
    """A property mined from production source inside a callable target.

    For an ``ASSERTED_CONDITION`` this records "the code explicitly expects this
    condition when execution reaches the assertion". It is deliberately not
    restated as a universal contract on the function's inputs. For a
    ``REJECTION_RULE`` it records "this condition leads to this raise"; it is
    deliberately not inverted into "the opposite condition is always valid".
    """

    kind: PropertyKind
    target: str
    condition: str
    strength: OracleStrength
    expected_outcome: ExpectedOutcome
    evidence: tuple[OracleEvidence, ...]
    message: str | None = None
    exception: str | None = None


@dataclass(frozen=True, slots=True)
class TestExpectationCandidate:
    """An expectation mined from an existing test file, without running it.

    ``target`` stays ``None`` when the callee cannot be resolved to exactly one
    production target; ``resolution`` then records why. The expectation is kept
    either way because the evidence itself is useful.
    """

    __test__ = False

    kind: PropertyKind
    strength: OracleStrength
    expected_outcome: ExpectedOutcome
    callee: str
    evidence: tuple[OracleEvidence, ...]
    target: str | None = None
    resolution: TargetResolution = TargetResolution.UNRESOLVED
    resolution_reason: str = ""
    call_expression: str | None = None
    comparison_operator: ComparisonOperator | None = None
    expected_expression: str | None = None
    exception: str | None = None
    literal_arguments: tuple[str, ...] = ()
    """Positional arguments that are literals, in call order.

    Only literal syntax is recorded: a fixture name, a constant reference, or a
    constructor call is deliberately absent rather than evaluated. The text is
    recorded untyped; a later stage knows the parameter's annotation and can
    classify it.
    """


@dataclass(frozen=True, slots=True)
class TargetOracleSupport:
    """How much explicit oracle evidence exists for one target."""

    target: str
    level: OracleSupport
    assertion_count: int
    validation_rule_count: int
    resolved_test_expectation_count: int
