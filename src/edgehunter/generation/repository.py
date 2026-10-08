"""Repository-level aggregation of constrained actions and candidate cases.

Actions are selected per module during the single parse pass, because that is
where the parsed AST and the target's parameter list are already in hand. This
module then turns the per-target action lists into the repository aggregate and
into candidate cases, which is a repository-wide concern only because a case's
completeness is measured against its target's full signature.

A case carries exactly one action and never fills in a value for another
parameter. What it does instead is name the required parameters it leaves
uncovered, so the information that a future harness needs in order to build the
rest of the call is explicit rather than invented here.
"""

from __future__ import annotations

from edgehunter.generation.models import (
    ACTION_KIND_ORDER,
    CandidateCase,
    CandidateCompleteness,
    MaterializationStatus,
    RepositoryGeneration,
    SemanticAction,
    TargetActions,
)


def build_repository_generation(action_sets: tuple[TargetActions, ...]) -> RepositoryGeneration:
    """Aggregate per-target actions and derive one candidate case per action.

    Only actions that materialized at all become cases. An ``UNSUPPORTED`` action
    stays visible in the action list with the reason it could not be written down,
    but it is not a candidate input, because there is nothing to hand a runner.
    """
    cases: list[CandidateCase] = []
    for entry in action_sets:
        for action in entry.actions:
            if action.status is MaterializationStatus.UNSUPPORTED:
                continue
            cases.append(_candidate_case(entry, action))

    ordered = tuple(
        sorted(
            cases,
            key=lambda case: (
                case.relative_path.as_posix(),
                case.lineno,
                case.target,
                case.parameter,
                ACTION_KIND_ORDER[case.action.kind],
                case.action.reference or "",
            ),
        )
    )
    return RepositoryGeneration(action_sets=action_sets, cases=ordered)


def _candidate_case(entry: TargetActions, action: SemanticAction) -> CandidateCase:
    """Build the single-action candidate case for one materialized action."""
    missing = tuple(name for name in entry.required_parameters if name != action.parameter)
    if action.status is MaterializationStatus.SYMBOLIC:
        completeness = CandidateCompleteness.SYMBOLIC
    elif missing:
        completeness = CandidateCompleteness.PARTIAL
    else:
        completeness = CandidateCompleteness.COMPLETE
    return CandidateCase(
        relative_path=entry.relative_path,
        target=entry.target,
        lineno=entry.lineno,
        signature=entry.signature,
        action=action,
        completeness=completeness,
        missing_parameters=missing,
    )
