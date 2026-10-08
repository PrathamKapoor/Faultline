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
    ANNOTATION_CATEGORIES,
    BOUNDARY_ACTIONS,
    classify_annotation,
    reference_constant_kinds,
    required_parameters,
    select_module_actions,
    select_target_actions,
)
from edgehunter.generation.materialize import (
    Materialization,
    materialize_collection,
    materialize_generic_numeric,
    materialize_relational,
    parse_float_literal,
    parse_integer_literal,
)
from edgehunter.generation.models import (
    ACTION_KIND_ORDER,
    ActionEvidence,
    ActionOrigin,
    AnnotationCategory,
    CandidateCase,
    CandidateCompleteness,
    LiteralKind,
    MaterializationStatus,
    RepositoryGeneration,
    SemanticAction,
    SemanticActionKind,
    TargetActions,
)
from edgehunter.generation.repository import build_repository_generation

__all__ = [
    "ACTION_KIND_ORDER",
    "ANNOTATION_ACTIONS",
    "ANNOTATION_CATEGORIES",
    "BOUNDARY_ACTIONS",
    "ActionEvidence",
    "ActionOrigin",
    "AnnotationCategory",
    "CandidateCase",
    "CandidateCompleteness",
    "LiteralKind",
    "Materialization",
    "MaterializationStatus",
    "RepositoryGeneration",
    "SemanticAction",
    "SemanticActionKind",
    "TargetActions",
    "build_repository_generation",
    "classify_annotation",
    "materialize_collection",
    "materialize_generic_numeric",
    "materialize_relational",
    "parse_float_literal",
    "parse_integer_literal",
    "reference_constant_kinds",
    "required_parameters",
    "select_module_actions",
    "select_target_actions",
]
