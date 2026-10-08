"""Command-line interface for EdgeHunter.

This module is a thin presentation layer: it parses user input, delegates to
the library modules, and formats their results. Analysis logic stays out of
this module so it can be tested without going through the command line.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from edgehunter import __version__
from edgehunter.generation.invocation import ArgumentAssignment, ArgumentPlacement
from edgehunter.generation.models import MaterializationStatus, SemanticAction
from edgehunter.oracle.models import (
    ProductionPropertyCandidate,
    PropertyKind,
    TestExpectationCandidate,
)
from edgehunter.pipeline import RepositoryScan, analyze_repository
from edgehunter.scanner import RepositoryAnalysis, ScanError

EXIT_SCAN_ISSUES = 1
EXIT_INVALID_INPUT = 2

app = typer.Typer(
    name="edgehunter",
    no_args_is_help=True,
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"edgehunter {__version__}")
        raise typer.Exit()


@app.callback()
def _root_callback(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Show the EdgeHunter version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """Oracle-aware counterexample search engine for Python."""


@app.command()
def scan(
    path: Annotated[
        Path,
        typer.Argument(metavar="PATH", help="Directory to scan, or a single Python file."),
    ],
    targets: Annotated[
        bool,
        typer.Option("--targets", help="List discovered callable targets."),
    ] = False,
    boundaries: Annotated[
        bool,
        typer.Option("--boundaries", help="List boundary candidates per target."),
    ] = False,
    risk: Annotated[
        bool,
        typer.Option("--risk", help="List targets ranked by structural risk score."),
    ] = False,
    properties: Annotated[
        bool,
        typer.Option("--properties", help="List mined oracle and property evidence."),
    ] = False,
    actions: Annotated[
        bool,
        typer.Option("--actions", help="List the constrained semantic actions per target."),
    ] = False,
    candidates: Annotated[
        bool,
        typer.Option("--candidates", help="List materialized candidate cases per target."),
    ] = False,
    annotations: Annotated[
        bool,
        typer.Option("--annotations", help="List each parameter's static annotation shape."),
    ] = False,
    plans: Annotated[
        bool,
        typer.Option("--plans", help="List invocation plans with argument origins."),
    ] = False,
    strategies: Annotated[
        bool,
        typer.Option("--strategies", help="List deterministic strategy plans per target."),
    ] = False,
) -> None:
    """Discover Python sources under PATH and analyze them statically.

    Files are parsed once with Python's ``ast`` module, then analyzed for
    structure, predicates, comparisons, arithmetic, boundary candidates, mined
    oracle evidence, structural risk, constrained semantic actions, static
    annotation shapes, invocation plans, and strategy plans. Analyzed code is
    never imported or executed, and candidate inputs are never run. The command
    exits with 1 when one or more files could not be read or parsed; oracle
    ambiguity is not a source failure.
    """
    try:
        analysis = analyze_repository(path)
    except ScanError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=EXIT_INVALID_INPUT) from exc

    _print_analysis(
        analysis,
        show_targets=targets,
        show_boundaries=boundaries,
        show_risk=risk,
        show_properties=properties,
        show_actions=actions,
        show_candidates=candidates,
        show_annotations=annotations,
        show_plans=plans,
        show_strategies=strategies,
    )

    if analysis.semantics.structure.issue_count:
        raise typer.Exit(code=EXIT_SCAN_ISSUES)


def _print_analysis(
    analysis: RepositoryScan,
    *,
    show_targets: bool,
    show_boundaries: bool,
    show_risk: bool,
    show_properties: bool,
    show_actions: bool,
    show_candidates: bool,
    show_annotations: bool,
    show_plans: bool,
    show_strategies: bool,
) -> None:
    semantics = analysis.semantics
    structure = semantics.structure
    kind = "file" if structure.is_file else "directory"
    typer.echo("EdgeHunter scan")
    typer.echo(f"  target ({kind}): {structure.target}")
    typer.echo("")
    typer.echo(f"  Python files discovered: {structure.python_file_count}")
    typer.echo(f"  Parsed successfully:     {structure.parsed_file_count}")
    typer.echo(f"  Syntax issues:           {structure.syntax_issue_count}")
    typer.echo(f"  Read issues:             {structure.read_issue_count}")
    typer.echo("")
    typer.echo(f"  Classes:                 {structure.class_count}")
    typer.echo(f"  Functions:               {structure.function_count}")
    typer.echo(f"  Methods:                 {structure.method_count}")
    typer.echo(f"  Async targets:           {structure.async_target_count}")
    typer.echo(f"  Callable targets:        {structure.callable_target_count}")
    if not structure.is_file:
        typer.echo(f"  Excluded directories:    {len(structure.excluded_directories)}")
    typer.echo("")
    typer.echo(f"  Predicates:              {semantics.predicate_count}")
    typer.echo(f"  Comparisons:             {semantics.comparison_count}")
    typer.echo(f"  Arithmetic expressions:  {semantics.arithmetic_expression_count}")
    typer.echo(f"  Boundary candidates:     {semantics.boundary_candidate_count}")

    _print_oracle_counts(analysis)
    _print_generation_counts(analysis)
    _print_planning_counts(analysis)

    if show_annotations:
        _print_annotations(analysis)

    if show_targets:
        _print_targets(structure)

    if show_boundaries:
        _print_boundaries(analysis)

    if show_risk:
        _print_risk(analysis)

    if show_properties:
        _print_properties(analysis)

    if show_actions:
        _print_actions(analysis)

    if show_candidates:
        _print_candidates(analysis)

    if show_plans:
        _print_plans(analysis)

    if show_strategies:
        _print_strategies(analysis)

    _print_issues(structure)


def _print_planning_counts(analysis: RepositoryScan) -> None:
    planning = analysis.planning
    annotations = analysis.annotations
    typer.echo("")
    typer.echo("  Planning")
    typer.echo(f"    Parameters recognized:    {annotations.recognized_count}")
    typer.echo(f"    Parameters unknown:       {annotations.unknown_count}")
    typer.echo(f"    Invocation plans:         {planning.plan_count}")
    typer.echo(f"    Complete function plans:  {planning.complete_plan_count}")
    typer.echo(f"    Needs receiver:           {planning.needs_receiver_count}")
    typer.echo(f"    Partial plans:            {planning.partial_plan_count}")
    typer.echo(f"    Symbolic plans:           {planning.symbolic_plan_count}")
    typer.echo(f"    Strategy plans:           {planning.strategy_plan_count}")


def _print_generation_counts(analysis: RepositoryScan) -> None:
    generation = analysis.generation
    typer.echo("")
    typer.echo("  Semantic actions")
    typer.echo(f"    Actions proposed:          {generation.action_count}")
    typer.echo(f"    Concrete values:           {generation.concrete_action_count}")
    typer.echo(f"    Symbolic values:           {generation.symbolic_action_count}")
    typer.echo(f"    Unsupported actions:       {generation.unsupported_action_count}")
    typer.echo(f"    Targets with actions:      {generation.targets_with_actions}")
    typer.echo(f"    Candidate cases:           {generation.case_count}")
    typer.echo(f"    Complete cases:            {generation.complete_case_count}")
    typer.echo(f"    Partial cases:             {generation.partial_case_count}")
    typer.echo(f"    Symbolic cases:            {generation.symbolic_case_count}")


def _print_oracle_counts(analysis: RepositoryScan) -> None:
    oracles = analysis.oracles
    typer.echo("")
    typer.echo("  Oracle evidence")
    typer.echo(f"    Assertions:                   {oracles.assertion_count}")
    typer.echo(f"    Validation rules:             {oracles.validation_rule_count}")
    typer.echo(f"    Resolved test expectations:   {oracles.resolved_test_expectation_count}")
    typer.echo(f"    Unresolved test expectations: {oracles.unresolved_test_expectation_count}")
    typer.echo(f"    Ambiguous test expectations:  {oracles.ambiguous_test_expectation_count}")
    typer.echo(f"    Property candidates:          {oracles.property_candidate_count}")
    typer.echo(f"    Strongly supported targets:   {oracles.strongly_supported_target_count}")
    typer.echo(f"    Risk-scored targets:          {analysis.risk.scored_target_count}")


def _print_issues(analysis: RepositoryAnalysis) -> None:
    syntax_modules = [module for module in analysis.modules if module.syntax_issue is not None]
    if syntax_modules:
        typer.echo("")
        typer.echo("Syntax issues")
        typer.echo("")
        for module in syntax_modules:
            syntax_issue = module.syntax_issue
            if syntax_issue is None:
                continue
            location = _format_location(
                module.relative_path, syntax_issue.line, syntax_issue.column
            )
            typer.echo(f"  {location}")
            typer.echo(f"    {syntax_issue.message}")

    read_modules = [module for module in analysis.modules if module.read_issue is not None]
    if read_modules:
        typer.echo("")
        typer.echo("Read issues")
        typer.echo("")
        for module in read_modules:
            read_issue = module.read_issue
            if read_issue is None:
                continue
            typer.echo(f"  {module.relative_path.as_posix()}")
            typer.echo(f"    {read_issue.message}")


def _print_targets(analysis: RepositoryAnalysis) -> None:
    typer.echo("")
    typer.echo("Targets")
    typer.echo("")

    found = False
    for module in analysis.modules:
        for function in module.functions:
            found = True
            typer.echo(f"  {module.relative_path.as_posix()}:{function.lineno}")
            typer.echo(f"    {function.signature}")

    if not found:
        typer.echo("  (no callable targets)")


def _print_boundaries(analysis: RepositoryScan) -> None:
    typer.echo("")
    typer.echo("Boundaries")
    typer.echo("")

    found = False
    for entry in analysis.semantics.boundaries:
        found = True
        typer.echo(f"  {entry.relative_path.as_posix()}:{entry.lineno}")
        typer.echo(f"    {entry.signature}")
        typer.echo("")
        current_parameter = ""
        for candidate in entry.candidates:
            if candidate.parameter != current_parameter:
                current_parameter = candidate.parameter
                typer.echo(f"    {candidate.parameter}")
            typer.echo(f"      [{candidate.origin.value}] {candidate.description}")

    if not found:
        typer.echo("  (no boundary candidates)")


def _print_risk(analysis: RepositoryScan) -> None:
    typer.echo("")
    typer.echo("Structural risk")
    typer.echo("  Search-priority heuristic, not a probability of a bug.")
    typer.echo("")

    if not analysis.risk.profiles:
        typer.echo("  (no callable targets)")
        return

    for profile in analysis.risk.profiles:
        location = f"{profile.relative_path.as_posix()}:{profile.lineno}"
        typer.echo(f"  {profile.score:>3}  {location}")
        typer.echo(f"      {profile.signature}")
        for contribution in profile.contributions:
            points = f"+{contribution.points}"
            typer.echo(f"      {contribution.kind.value:<18} {points:<4} {contribution.detail}")
        typer.echo("")


def _print_properties(analysis: RepositoryScan) -> None:
    oracles = analysis.oracles

    typer.echo("")
    typer.echo("Properties")
    typer.echo("")
    typer.echo("  Mined evidence, not confirmed defects.")
    typer.echo("")

    typer.echo("  Production")
    typer.echo("")
    if oracles.production_properties:
        for property_candidate in oracles.production_properties:
            _print_production_property(property_candidate)
    else:
        typer.echo("    (no production assertions or validation guards)")

    typer.echo("")
    typer.echo("  Existing test expectations")
    typer.echo("")
    if oracles.test_expectations:
        for expectation in oracles.test_expectations:
            _print_test_expectation(expectation)
    else:
        typer.echo("    (no test expectations recognized)")

    typer.echo("")
    typer.echo("  Oracle support by target")
    typer.echo("")
    if oracles.support:
        for entry in oracles.support:
            typer.echo(
                f"    {entry.level.value.upper():<6} {entry.target}  "
                f"({entry.assertion_count} assertion(s), "
                f"{entry.validation_rule_count} validation rule(s), "
                f"{entry.resolved_test_expectation_count} resolved test(s))"
            )
    else:
        typer.echo("    (no target has explicit oracle evidence)")


def _print_production_property(item: ProductionPropertyCandidate) -> None:
    strength = item.strength.value.upper()
    typer.echo(f"    [{strength}] {item.kind.value}  {item.target}")
    for entry in item.evidence:
        typer.echo(f"      evidence: {entry.relative_path.as_posix()}:{entry.lineno}")
    if item.kind is PropertyKind.ASSERTED_CONDITION:
        typer.echo(f"      condition: {item.condition}")
        if item.message is not None:
            typer.echo(f"      message:   {item.message}")
    else:
        typer.echo(f"      when:      {item.condition}")
        outcome = item.expected_outcome.value
        if item.exception is not None:
            outcome = f"{outcome} {item.exception}"
        typer.echo(f"      expected:  {outcome}")
    typer.echo("")


def _print_test_expectation(item: TestExpectationCandidate) -> None:
    strength = item.strength.value.upper()
    typer.echo(f"    [{strength}] {item.kind.value}  [{item.resolution.value}]")
    for entry in item.evidence:
        typer.echo(f"      evidence: {entry.relative_path.as_posix()}:{entry.lineno}")
    if item.comparison_operator is not None:
        typer.echo(
            f"      expect:   {item.call_expression} "
            f"{item.comparison_operator.value} {item.expected_expression}"
        )
    else:
        typer.echo(f"      expect:   {item.call_expression} raises {item.exception}")
    typer.echo(f"      callee:   {item.callee}")
    typer.echo(f"      target:   {item.target if item.target is not None else 'unresolved'}")
    typer.echo(f"      reason:   {item.resolution_reason}")
    typer.echo("")


def _print_actions(analysis: RepositoryScan) -> None:
    typer.echo("")
    typer.echo("Actions")
    typer.echo("")
    typer.echo("  Constrained input vocabulary. Nothing here is executed.")
    typer.echo("")

    found = False
    for entry in analysis.generation.action_sets:
        found = True
        typer.echo(f"  {entry.relative_path.as_posix()}:{entry.lineno}")
        typer.echo(f"    {entry.signature}")
        typer.echo("")
        current_parameter = ""
        for action in entry.actions:
            if action.parameter != current_parameter:
                current_parameter = action.parameter
                typer.echo(f"    {action.parameter}")
            typer.echo(
                f"      [{action.origin.value}] {action.description:<34} "
                f"{action.status.value:<11} {_render_value(action)}"
            )
            if action.status is not MaterializationStatus.CONCRETE:
                typer.echo(f"        {action.materialization_reason}")

    if not found:
        typer.echo("  (no applicable semantic actions)")


def _print_candidates(analysis: RepositoryScan) -> None:
    typer.echo("")
    typer.echo("Candidates")
    typer.echo("")
    typer.echo("  One action per case. Values for other parameters are never invented.")
    typer.echo("")

    found = False
    for entry in analysis.generation.action_sets:
        cases = [
            case
            for case in analysis.generation.cases
            if case.relative_path == entry.relative_path and case.target == entry.target
        ]
        if not cases:
            continue
        found = True
        typer.echo(f"  {entry.relative_path.as_posix()}:{entry.lineno}")
        typer.echo(f"    {entry.signature}")
        typer.echo("")
        for case in cases:
            assignment = f"{case.parameter} = {_render_value(case.action)}"
            typer.echo(
                f"      [{case.completeness.value:<8}] {assignment:<28} {case.action.description}"
            )
            if case.missing_parameters:
                typer.echo(f"        missing: {', '.join(case.missing_parameters)}")
        typer.echo("")

    if not found:
        typer.echo("  (no candidate cases)")


def _render_value(action: SemanticAction) -> str:
    """Render a materialized value; a symbolic value is shown in angle brackets."""
    if action.value_text is not None:
        return action.value_text
    if action.symbolic_text is not None:
        return f"<{action.symbolic_text}>"
    return "(none)"


def _print_annotations(analysis: RepositoryScan) -> None:
    typer.echo("")
    typer.echo("Annotations")
    typer.echo("")
    typer.echo("  Static annotation interpretation is syntactic, not type resolution.")
    typer.echo("")

    found = False
    current = ("", 0)
    for item in analysis.annotations.parameters:
        location = (item.relative_path.as_posix(), item.lineno)
        if location != current:
            current = location
            found = True
            typer.echo(f"  {location[0]}:{location[1]}  {item.target}")
        rendered = item.rendered if item.rendered is not None else "(unannotated)"
        typer.echo(f"    {item.parameter}: {rendered} -> {item.description}")

    if not found:
        typer.echo("  (no callable targets)")


def _print_plans(analysis: RepositoryScan) -> None:
    typer.echo("")
    typer.echo("Invocation plans")
    typer.echo("")
    typer.echo("  One adversarial action per plan. Filler values record their origin.")
    typer.echo("")

    if not analysis.planning.plans:
        typer.echo("  (no invocation plans)")
        return

    current = ("", 0)
    for plan in analysis.planning.plans:
        location = (plan.relative_path.as_posix(), plan.lineno)
        if location != current:
            current = location
            typer.echo(f"  {location[0]}:{location[1]}  {plan.signature}")
            typer.echo("")

        action = plan.primary_action
        header = f"    {action.parameter} <- {action.kind.value}"
        if action.reference is not None:
            header = f"{header} {action.reference}"
        typer.echo(header)
        for item in plan.assignments:
            typer.echo(
                f"      {item.parameter} = {_render_assignment(item)}"
                f"  [{item.origin.value}, {item.placement.value}]"
            )
        if plan.requires_receiver:
            typer.echo(f"      receiver: {plan.receiver_name} (not constructed)")
        if plan.missing_parameters:
            typer.echo(f"      missing: {', '.join(plan.missing_parameters)}")
        typer.echo(f"      status: {plan.status.value}")
        typer.echo("")


def _print_strategies(analysis: RepositoryScan) -> None:
    typer.echo("")
    typer.echo("Strategy plans")
    typer.echo("")
    typer.echo("  Deterministic descriptions, not generated samples.")
    typer.echo("")

    if not analysis.planning.strategies:
        typer.echo("  (no strategy plans)")
        return

    for entry in analysis.planning.strategies:
        typer.echo(f"  {entry.relative_path.as_posix()}:{entry.lineno}  {entry.signature}")
        typer.echo("")
        for parameter in entry.parameters:
            typer.echo(
                f"    {parameter.parameter}: {parameter.annotation.description}"
                f"  [{parameter.kind.value}]"
            )
            for value in parameter.values:
                typer.echo(f"      {value.descriptor:<28} {value.text}")
            if parameter.depends_on:
                typer.echo(f"      depends on: {', '.join(parameter.depends_on)}")
            typer.echo("")


def _render_assignment(item: ArgumentAssignment) -> str:
    """Render a planned argument value, or how it is left unsupplied."""
    if item.value_text is not None:
        return item.value_text
    if item.placement is ArgumentPlacement.OMITTED:
        return "(declared default)"
    return "(unresolved)"


def _format_location(relative_path: Path, line: int | None, column: int | None) -> str:
    location = relative_path.as_posix()
    if line is not None:
        location = f"{location}:{line}"
        if column is not None:
            location = f"{location}:{column}"
    return location


def main() -> None:
    """Run the EdgeHunter command-line application."""
    app()


if __name__ == "__main__":
    main()
