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
from edgehunter.analysis import RepositorySemantics, analyze_repository_semantics
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
) -> None:
    """Discover Python sources under PATH and analyze them statically.

    Files are parsed with Python's ``ast`` module and analyzed for predicates,
    comparisons, constants, and arithmetic; boundary candidates are derived
    deterministically. Analyzed code is never imported or executed. The command
    exits with 1 when one or more files could not be read or parsed.
    """
    try:
        analysis = analyze_repository_semantics(path)
    except ScanError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=EXIT_INVALID_INPUT) from exc

    _print_analysis(analysis, show_targets=targets, show_boundaries=boundaries)

    if analysis.structure.issue_count:
        raise typer.Exit(code=EXIT_SCAN_ISSUES)


def _print_analysis(
    analysis: RepositorySemantics, *, show_targets: bool, show_boundaries: bool
) -> None:
    structure = analysis.structure
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
    typer.echo(f"  Predicates:              {analysis.predicate_count}")
    typer.echo(f"  Comparisons:             {analysis.comparison_count}")
    typer.echo(f"  Arithmetic expressions:  {analysis.arithmetic_expression_count}")
    typer.echo(f"  Boundary candidates:     {analysis.boundary_candidate_count}")

    _print_issues(structure)

    if show_targets:
        _print_targets(structure)

    if show_boundaries:
        _print_boundaries(analysis)


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


def _print_boundaries(analysis: RepositorySemantics) -> None:
    typer.echo("")
    typer.echo("Boundaries")
    typer.echo("")

    found = False
    for entry in analysis.boundaries:
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
