"""Static semantic analysis and deterministic boundary mining for EdgeHunter.

``scanner`` answers *what exists*; this package answers *which conditions and
values divide behavior*. Everything here is a pure, deterministic function of a
parsed AST: no code is imported, evaluated, or executed, no annotation is
resolved, and no model claims that a discovered candidate is a defect.
"""

from edgehunter.analysis.boundaries import mine_module_boundaries, mine_target_boundaries
from edgehunter.analysis.models import (
    BOUNDARY_KIND_ORDER,
    INVERTIBLE_COMPARISON_OPERATORS,
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
    RepositoryRiskProfile,
    RiskContribution,
    RiskFeatureKind,
    TargetBoundaries,
    TargetRiskProfile,
    TargetSemantics,
    invert_comparison_operator,
)
from edgehunter.analysis.repository import (
    AnalyzedFile,
    RepositorySemantics,
    analyze_discovered_files,
    analyze_repository_semantics,
)
from edgehunter.analysis.risk import (
    MAX_SCORE,
    count_boundary_groups,
    count_input_parameters,
    rank_profiles,
    score_module,
    score_target,
    source_span_lines,
)
from edgehunter.analysis.semantics import extract_module_semantics

__all__ = [
    "BOUNDARY_KIND_ORDER",
    "INVERTIBLE_COMPARISON_OPERATORS",
    "MAX_SCORE",
    "AnalyzedFile",
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
    "RepositoryRiskProfile",
    "RepositorySemantics",
    "RiskContribution",
    "RiskFeatureKind",
    "TargetBoundaries",
    "TargetRiskProfile",
    "TargetSemantics",
    "analyze_discovered_files",
    "analyze_repository_semantics",
    "count_boundary_groups",
    "count_input_parameters",
    "extract_module_semantics",
    "invert_comparison_operator",
    "mine_module_boundaries",
    "mine_target_boundaries",
    "rank_profiles",
    "score_module",
    "score_target",
    "source_span_lines",
]
