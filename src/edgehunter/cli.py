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
from edgehunter.scanner import ScanError, ScanSummary, scan_repository

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
) -> None:
    """Discover Python source files under PATH.

    Repository analysis is not implemented yet: this command currently
    performs repository discovery only.
    """
    try:
        summary = scan_repository(path)
    except ScanError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=EXIT_INVALID_INPUT) from exc

    _print_scan_summary(summary)


def _print_scan_summary(summary: ScanSummary) -> None:
    kind = "file" if summary.is_file else "directory"
    typer.echo("EdgeHunter scan")
    typer.echo(f"  target ({kind}): {summary.target}")
    typer.echo(f"  python files discovered: {summary.python_file_count}")
    if not summary.is_file:
        typer.echo(f"  excluded directories skipped: {len(summary.excluded_directories)}")
    typer.echo("")
    typer.echo("Repository analysis is not implemented yet (Phase 0 foundation).")


def main() -> None:
    """Run the EdgeHunter command-line application."""
    app()


if __name__ == "__main__":
    main()
