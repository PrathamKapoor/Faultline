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
) -> None:
    """Discover Python sources under PATH and analyze them statically.

    Files are parsed once with Python's ``ast`` module, then analyzed for
    structure, predicates, comparisons, arithmetic, boundary candidates, mined
    oracle evidence, and structural risk. Analyzed code is never imported or
    executed. The command exits with 1 when one or more files could not be read
    or parsed; oracle ambiguity is not a source failure.
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

    if show_targets:
        _print_targets(structure)

    if show_boundaries:
        _print_boundaries(analysis)

    if show_risk:
        _print_risk(analysis)

    if show_properties:
        _print_properties(analysis)

    _print_issues(structure)


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
