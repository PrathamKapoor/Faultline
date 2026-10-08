"""Static semantic analysis and deterministic boundary mining for EdgeHunter.

``scanner`` answers *what exists*; this package answers *which conditions and
values divide behavior*. Everything here is a pure, deterministic function of a
parsed AST: no code is imported, evaluated, or executed, no annotation is
resolved, and no model claims that a discovered candidate is a defect.
"""

from edgehunter.analysis.boundaries import mine_module_boundaries, mine_target_boundaries
from edgehunter.analysis.models import (
    BOUNDARY_KIND_ORDER,
    ArithmeticContext,
    ArithmeticInfo,
    ArithmeticOperator,
    BoundaryCandidate,
    BoundaryKind,
    BoundaryOrigin,
    ComparisonInfo,
    ComparisonOperator,
    ConstantInfo,
    ConstantKind,
    ExpressionInfo,
    ExpressionKind,
    ModuleSemantics,
    PredicateInfo,
    PredicateKind,
    TargetBoundaries,
    TargetSemantics,
)
from edgehunter.analysis.repository import RepositorySemantics, analyze_repository_semantics
from edgehunter.analysis.semantics import extract_module_semantics

__all__ = [
    "BOUNDARY_KIND_ORDER",
    "ArithmeticContext",
    "ArithmeticInfo",
    "ArithmeticOperator",
    "BoundaryCandidate",
    "BoundaryKind",
    "BoundaryOrigin",
    "ComparisonInfo",
    "ComparisonOperator",
    "ConstantInfo",
    "ConstantKind",
    "ExpressionInfo",
    "ExpressionKind",
    "ModuleSemantics",
    "PredicateInfo",
    "PredicateKind",
    "RepositorySemantics",
    "TargetBoundaries",
    "TargetSemantics",
    "analyze_repository_semantics",
    "extract_module_semantics",
    "mine_module_boundaries",
    "mine_target_boundaries",
]
