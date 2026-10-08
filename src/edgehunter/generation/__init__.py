"""Constrained semantic actions and materialized candidate inputs for EdgeHunter.

``analysis`` says *which conditions and regions divide behavior*. This package
turns that into the finite set of inputs EdgeHunter is allowed to propose for a
parameter, and writes down the ones it can write down concretely.

The vocabulary is closed and static. Every proposed action is a member of
:class:`SemanticActionKind`, every value is source text produced without reading
anything twice, and no analyzed code is imported, called, or evaluated. A
concrete value is a Python expression for a *later* phase to use; EdgeHunter
never runs it here, and a candidate case is not a defect claim.
"""

from edgehunter.generation.actions import (
    ANNOTATION_ACTIONS,
    BOUNDARY_ACTIONS,
    receiver_parameter,
    reference_constant_kinds,
    required_parameters,
    select_module_actions,
    select_target_actions,
)
from edgehunter.generation.baselines import BaselineSource, BaselineValue, baseline_for
from edgehunter.generation.invocation import (
    ArgumentAssignment,
    ArgumentPlacement,
    AssignmentOrigin,
    InvocationPlan,
    InvocationStatus,
    plan_case,
)
from edgehunter.generation.materialize import (
    Materialization,
    materialize_collection,
    materialize_generic_numeric,
    materialize_none,
    materialize_relational,
    parse_float_literal,
    parse_integer_literal,
)
from edgehunter.generation.models import (
    ACTION_KIND_ORDER,
    ActionEvidence,
    ActionOrigin,
    CandidateCase,
    CandidateCompleteness,
    LiteralKind,
    MaterializationStatus,
    RepositoryGeneration,
    SemanticAction,
    SemanticActionKind,
    TargetActions,
)
from edgehunter.generation.planning import (
    ParameterAnnotation,
    RepositoryAnnotations,
    RepositoryPlanning,
    annotation_report,
    build_repository_planning,
)
from edgehunter.generation.repository import build_repository_generation
from edgehunter.generation.strategy import (
    ParameterStrategy,
    StrategyKind,
    StrategyPlan,
    StrategyValue,
    plan_strategies,
)

__all__ = [
    "ACTION_KIND_ORDER",
    "ANNOTATION_ACTIONS",
    "BOUNDARY_ACTIONS",
    "ActionEvidence",
    "ActionOrigin",
    "ArgumentAssignment",
    "ArgumentPlacement",
    "AssignmentOrigin",
    "BaselineSource",
    "BaselineValue",
    "CandidateCase",
    "CandidateCompleteness",
    "InvocationPlan",
    "InvocationStatus",
    "LiteralKind",
    "Materialization",
    "MaterializationStatus",
    "ParameterAnnotation",
    "ParameterStrategy",
    "RepositoryAnnotations",
    "RepositoryGeneration",
    "RepositoryPlanning",
    "SemanticAction",
    "SemanticActionKind",
    "StrategyKind",
    "StrategyPlan",
    "StrategyValue",
    "TargetActions",
    "annotation_report",
    "baseline_for",
    "build_repository_generation",
    "build_repository_planning",
    "materialize_collection",
    "materialize_generic_numeric",
    "materialize_none",
    "materialize_relational",
    "parse_float_literal",
    "parse_integer_literal",
    "plan_case",
    "plan_strategies",
    "receiver_parameter",
    "reference_constant_kinds",
    "required_parameters",
    "select_module_actions",
    "select_target_actions",
]
