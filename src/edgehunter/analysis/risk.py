"""Explainable structural risk scoring for callable targets.

The score is a deterministic *search-priority heuristic*: it answers "does this
target contain structural characteristics that justify allocating more testing
budget?". It is not a probability, not a bug estimate, and not a security
severity. Every point is attributable to a named feature, and each feature has
a cap so no single characteristic can dominate the total.

The formula lives here, in one place:

====================  =========================  =====
feature               points                    cap
====================  =========================  =====
predicates            +6 per predicate          30
arithmetic            +4 per expression        20
boundary groups       +4 per unique group       20
input parameters      +3 per parameter          15
source span           +3 per 20-line block      15
====================  =========================  =====

Maximum total: 100.

A boundary *group* is a unique ``(parameter, reference)`` pair, where a
``None`` reference means the parameter's generic numeric candidates. The three
kinds ``JUST_BELOW``/``AT_BOUNDARY``/``JUST_ABOVE`` for one reference form one
group, and a float parameter's seven generic numeric values form one group, so
numeric type richness cannot dominate the score.

The implicit receiver of a method (``self``/``cls`` as the first parameter) is
not counted as user input.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Final

from edgehunter.analysis.models import (
    ModuleSemantics,
    RepositoryRiskProfile,
    RiskContribution,
    RiskFeatureKind,
    TargetBoundaries,
    TargetRiskProfile,
    TargetSemantics,
)
from edgehunter.scanner.models import (
    METHOD_KINDS,
    RECEIVER_PARAMETER_NAMES,
    FunctionInfo,
    ModuleAnalysis,
)

__all__ = [
    "MAX_SCORE",
    "RECEIVER_PARAMETER_NAMES",
    "count_boundary_groups",
    "count_input_parameters",
    "rank_profiles",
    "score_module",
    "score_target",
    "source_span_lines",
]

PREDICATES_PER_POINT: Final[int] = 6
PREDICATES_CAP: Final[int] = 30
ARITHMETIC_PER_POINT: Final[int] = 4
ARITHMETIC_CAP: Final[int] = 20
BOUNDARY_GROUP_PER_POINT: Final[int] = 4
BOUNDARY_GROUP_CAP: Final[int] = 20
INPUT_PARAMETER_PER_POINT: Final[int] = 3
INPUT_PARAMETER_CAP: Final[int] = 15
SOURCE_SPAN_BLOCK_LINES: Final[int] = 20
SOURCE_SPAN_PER_POINT: Final[int] = 3
SOURCE_SPAN_CAP: Final[int] = 15

MAX_SCORE: Final[int] = (
    PREDICATES_CAP + ARITHMETIC_CAP + BOUNDARY_GROUP_CAP + INPUT_PARAMETER_CAP + SOURCE_SPAN_CAP
)


def count_boundary_groups(boundaries: TargetBoundaries | None) -> int:
    """Number of unique ``(parameter, reference)`` boundary groups."""
    if boundaries is None:
        return 0
    return len({(candidate.parameter, candidate.reference) for candidate in boundaries.candidates})


def count_input_parameters(function: FunctionInfo) -> int:
    """Number of user input parameters, excluding a method's implicit receiver."""
    parameters = list(function.parameters)
    if (
        function.kind in METHOD_KINDS
        and parameters
        and parameters[0].name in RECEIVER_PARAMETER_NAMES
    ):
        parameters = parameters[1:]
    return len(parameters)


def source_span_lines(function: FunctionInfo) -> int:
    """Length of the target's own source span in lines.

    Uses the structural ``lineno``/``end_lineno`` recorded by the scanner, so no
    source is re-read. When ``end_lineno`` is unavailable the span falls back to
    a single line rather than inventing a location.
    """
    if function.end_lineno is None:
        return 1
    return max(1, function.end_lineno - function.lineno + 1)


def score_target(
    relative_path: Path,
    function: FunctionInfo,
    semantics: TargetSemantics,
    boundaries: TargetBoundaries | None,
) -> TargetRiskProfile:
    """Compute the explainable structural risk profile of one callable target."""
    predicates = len(semantics.predicates)
    arithmetic = len(semantics.arithmetic)
    groups = count_boundary_groups(boundaries)
    inputs = count_input_parameters(function)
    span = source_span_lines(function)
    blocks = math.ceil(span / SOURCE_SPAN_BLOCK_LINES)

    contributions = (
        RiskContribution(
            kind=RiskFeatureKind.PREDICATES,
            points=min(predicates * PREDICATES_PER_POINT, PREDICATES_CAP),
            detail=(
                f"{predicates} predicate(s), +{PREDICATES_PER_POINT} each (cap {PREDICATES_CAP})"
            ),
        ),
        RiskContribution(
            kind=RiskFeatureKind.ARITHMETIC,
            points=min(arithmetic * ARITHMETIC_PER_POINT, ARITHMETIC_CAP),
            detail=(
                f"{arithmetic} arithmetic expression(s), +{ARITHMETIC_PER_POINT} each "
                f"(cap {ARITHMETIC_CAP})"
            ),
        ),
        RiskContribution(
            kind=RiskFeatureKind.BOUNDARY_GROUPS,
            points=min(groups * BOUNDARY_GROUP_PER_POINT, BOUNDARY_GROUP_CAP),
            detail=(
                f"{groups} boundary group(s), +{BOUNDARY_GROUP_PER_POINT} each "
                f"(cap {BOUNDARY_GROUP_CAP})"
            ),
        ),
        RiskContribution(
            kind=RiskFeatureKind.INPUT_PARAMETERS,
            points=min(inputs * INPUT_PARAMETER_PER_POINT, INPUT_PARAMETER_CAP),
            detail=(
                f"{inputs} input parameter(s), +{INPUT_PARAMETER_PER_POINT} each "
                f"(cap {INPUT_PARAMETER_CAP})"
            ),
        ),
        RiskContribution(
            kind=RiskFeatureKind.SOURCE_SPAN,
            points=min(blocks * SOURCE_SPAN_PER_POINT, SOURCE_SPAN_CAP),
            detail=(
                f"{span} line(s) span, {blocks} block(s) of "
                f"{SOURCE_SPAN_BLOCK_LINES}, +{SOURCE_SPAN_PER_POINT} each (cap {SOURCE_SPAN_CAP})"
            ),
        ),
    )

    return TargetRiskProfile(
        target=function.qualified_name,
        relative_path=relative_path,
        lineno=function.lineno,
        signature=function.signature,
        score=min(sum(item.points for item in contributions), MAX_SCORE),
        contributions=contributions,
    )


def score_module(
    module: ModuleAnalysis, semantics: ModuleSemantics
) -> tuple[TargetRiskProfile, ...]:
    """Score every callable target of one module, in source order."""
    semantics_by_name = {target.qualified_name: target for target in semantics.targets}
    boundaries_by_name = {entry.qualified_name: entry for entry in semantics.boundaries}

    profiles: list[TargetRiskProfile] = []
    for function in module.functions:
        target = semantics_by_name.get(function.qualified_name)
        if target is None:
            continue
        profiles.append(
            score_target(
                module.relative_path,
                function,
                target,
                boundaries_by_name.get(function.qualified_name),
            )
        )
    return tuple(profiles)


def rank_profiles(profiles: tuple[TargetRiskProfile, ...]) -> RepositoryRiskProfile:
    """Rank profiles deterministically: score descending, then path, line, name."""
    ordered = tuple(
        sorted(
            profiles,
            key=lambda profile: (
                -profile.score,
                profile.relative_path.as_posix(),
                profile.lineno,
                profile.target,
            ),
        )
    )
    return RepositoryRiskProfile(profiles=ordered)
