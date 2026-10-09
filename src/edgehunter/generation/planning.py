"""Repository-level aggregation of invocation plans and strategy plans.

A Phase 4 :class:`~edgehunter.generation.models.CandidateCase` names one
adversarial action and nothing else. This module lifts each case into an
:class:`~edgehunter.generation.invocation.InvocationPlan` using trustworthy
baselines, and derives one
:class:`~edgehunter.generation.strategy.StrategyPlan` per target. It sits above
the candidate layer rather than replacing it, so the single-action abstraction
that Phase 4 established stays intact and auditable.

Optional existing-test baselines are passed in from the oracle layer, which is
the only place that has already seen a call's arguments. They are used only for
expectations that resolved to exactly one production target, and only their
*literal* arguments: a fixture name or a constructor call is never evaluated.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from edgehunter.generation.invocation import InvocationPlan, InvocationStatus, plan_case
from edgehunter.generation.models import RepositoryGeneration
from edgehunter.generation.strategy import StrategyPlan, plan_strategies
from edgehunter.scanner.annotations import StaticAnnotation
from edgehunter.scanner.models import FunctionInfo, ModuleAnalysis


@dataclass(frozen=True, slots=True)
class RepositoryPlanning:
    """Invocation plans and strategy plans for a whole repository."""

    plans: tuple[InvocationPlan, ...]
    strategies: tuple[StrategyPlan, ...]

    @property
    def plan_count(self) -> int:
        """Number of invocation plans."""
        return len(self.plans)

    @property
    def strategy_plan_count(self) -> int:
        """Number of targets with a strategy plan."""
        return len(self.strategies)

    def _count_status(self, status: InvocationStatus) -> int:
        """Count plans in one invocation status."""
        return sum(1 for plan in self.plans if plan.status is status)

    @property
    def complete_plan_count(self) -> int:
        """Number of plans that are fully specified for a plain function."""
        return self._count_status(InvocationStatus.COMPLETE_FUNCTION)

    @property
    def needs_receiver_count(self) -> int:
        """Number of plans that require a receiver before they can run."""
        return self._count_status(InvocationStatus.NEEDS_RECEIVER)

    @property
    def partial_plan_count(self) -> int:
        """Number of plans with at least one uncovered required argument."""
        return self._count_status(InvocationStatus.PARTIAL_ARGUMENTS)

    @property
    def symbolic_plan_count(self) -> int:
        """Number of plans whose primary value is still symbolic."""
        return self._count_status(InvocationStatus.SYMBOLIC_DEPENDENCY)


def build_repository_planning(
    generation: RepositoryGeneration,
    modules: tuple[ModuleAnalysis, ...],
    test_literals: dict[str, tuple[str, ...]] | None = None,
    test_positions: dict[str, tuple[int, ...]] | None = None,
) -> RepositoryPlanning:
    """Lift every candidate case into a plan and derive every strategy plan.

    ``test_literals`` maps a target's qualified name to the literal arguments a
    resolved existing test passed to it, and ``test_positions`` gives the call
    position of each. They are separate maps because a literal's binding follows
    the position it was written at, not its index within the literal tuple.
    """
    located = _functions_by_target(modules)
    functions = {name: function for name, (_, function) in located.items()}
    actions_by_target = {entry.target: entry.actions for entry in generation.action_sets}
    observed = test_literals or {}
    positions = test_positions or {}

    plans: list[InvocationPlan] = []
    for case in generation.cases:
        function = functions.get(case.target)
        if function is None:
            continue
        plans.append(
            plan_case(
                case,
                function,
                observed.get(case.target, ()),
                positions.get(case.target, ()),
            )
        )

    strategies = tuple(
        plan_strategies(relative_path, function, actions_by_target.get(name, ()))
        for name, (relative_path, function) in sorted(
            located.items(),
            key=lambda item: (
                item[1][0].as_posix(),
                item[1][1].lineno,
                item[0],
            ),
        )
        if actions_by_target.get(name)
    )

    ordered = tuple(
        sorted(
            plans,
            key=lambda plan: (
                plan.relative_path.as_posix(),
                plan.lineno,
                plan.target,
                plan.primary_action.parameter,
                plan.primary_action.kind.value,
                plan.primary_action.reference or "",
            ),
        )
    )
    return RepositoryPlanning(plans=ordered, strategies=strategies)


def _functions_by_target(
    modules: tuple[ModuleAnalysis, ...],
) -> dict[str, tuple[Path, FunctionInfo]]:
    """Map qualified target name to its module-relative path and structural facts."""
    located: dict[str, tuple[Path, FunctionInfo]] = {}
    for module in modules:
        for function in module.functions:
            located.setdefault(function.qualified_name, (module.relative_path, function))
    return located


def annotation_report(modules: tuple[ModuleAnalysis, ...]) -> RepositoryAnnotations:
    """Every parameter with its static shape, for the ``--annotations`` view."""
    reported: list[ParameterAnnotation] = []
    for module in modules:
        for function in module.functions:
            for parameter in function.parameters:
                reported.append(
                    ParameterAnnotation(
                        relative_path=module.relative_path,
                        target=function.qualified_name,
                        lineno=function.lineno,
                        parameter=parameter.name,
                        rendered=parameter.annotation,
                        shape=parameter.shape,
                    )
                )
    return RepositoryAnnotations(parameters=tuple(reported))


@dataclass(frozen=True, slots=True)
class ParameterAnnotation:
    """One parameter's rendered annotation beside its interpreted shape."""

    relative_path: Path
    target: str
    lineno: int
    parameter: str
    rendered: str | None
    shape: StaticAnnotation

    @property
    def description(self) -> str:
        """The shape's symbolic rendering."""
        return self.shape.description


@dataclass(frozen=True, slots=True)
class RepositoryAnnotations:
    """Every parameter in the repository with its interpreted annotation shape."""

    parameters: tuple[ParameterAnnotation, ...]

    @property
    def parameter_count(self) -> int:
        """Total number of declared parameters."""
        return len(self.parameters)

    @property
    def recognized_count(self) -> int:
        """Number of parameters whose annotation shape was recognized."""
        return sum(1 for item in self.parameters if item.shape.is_known)

    @property
    def unknown_count(self) -> int:
        """Number of parameters whose annotation stayed ``UNKNOWN``."""
        return self.parameter_count - self.recognized_count
