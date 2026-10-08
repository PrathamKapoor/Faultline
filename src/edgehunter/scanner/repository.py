"""Filesystem-level repository discovery for EdgeHunter.

Discovery and analysis never import, evaluate, or execute code from the
scanned repository; that belongs to a separate, isolated subsystem that does
not exist yet.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from edgehunter.scanner.ast_parser import analyze_python_file
from edgehunter.scanner.models import METHOD_KINDS, FunctionInfo, ModuleAnalysis

PYTHON_FILE_SUFFIX = ".py"

EXCLUDED_DIRECTORY_NAMES: frozenset[str] = frozenset(
    {
        ".eggs",
        ".git",
        ".hg",
        ".hypothesis",
        ".mypy_cache",
        ".nox",
        ".pytest_cache",
        ".ruff_cache",
        ".svn",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "env",
        "node_modules",
        "site-packages",
        "venv",
        "virtualenv",
    }
)

EXCLUDED_DIRECTORY_SUFFIXES: tuple[str, ...] = (".egg-info",)


class ScanError(Exception):
    """Base class for recoverable scan failures."""


class PathNotFoundError(ScanError):
    """Raised when the scan target does not exist."""


class UnsupportedTargetError(ScanError):
    """Raised when the scan target is neither a Python file nor a directory."""


@dataclass(frozen=True, slots=True)
class ScanSummary:
    """Outcome of a filesystem discovery pass over one scan target."""

    target: Path
    is_file: bool
    python_files: tuple[Path, ...]
    excluded_directories: tuple[Path, ...]

    @property
    def python_file_count(self) -> int:
        """Number of discovered Python source files."""
        return len(self.python_files)


@dataclass(frozen=True, slots=True)
class RepositoryAnalysis:
    """Discovery results plus static AST analysis of every discovered file."""

    discovery: ScanSummary
    modules: tuple[ModuleAnalysis, ...]

    @property
    def target(self) -> Path:
        """Canonical scan target (directory or single Python file)."""
        return self.discovery.target

    @property
    def is_file(self) -> bool:
        """True when the scan target is a single Python file."""
        return self.discovery.is_file

    @property
    def python_files(self) -> tuple[Path, ...]:
        """Discovered Python files in deterministic order."""
        return self.discovery.python_files

    @property
    def excluded_directories(self) -> tuple[Path, ...]:
        """Directories pruned during discovery, in deterministic order."""
        return self.discovery.excluded_directories

    @property
    def python_file_count(self) -> int:
        """Number of discovered Python source files."""
        return self.discovery.python_file_count

    @property
    def parsed_file_count(self) -> int:
        """Number of files read and parsed without issues."""
        return sum(1 for module in self.modules if module.is_parsed)

    @property
    def syntax_issue_count(self) -> int:
        """Number of files with a syntax issue."""
        return sum(1 for module in self.modules if module.syntax_issue is not None)

    @property
    def read_issue_count(self) -> int:
        """Number of files that could not be read or decoded."""
        return sum(1 for module in self.modules if module.read_issue is not None)

    @property
    def issue_count(self) -> int:
        """Total number of files with a syntax or read issue."""
        return self.syntax_issue_count + self.read_issue_count

    @property
    def callable_targets(self) -> tuple[FunctionInfo, ...]:
        """Every callable target from every module, in scan order."""
        return tuple(function for module in self.modules for function in module.functions)

    @property
    def callable_target_count(self) -> int:
        """Number of callable targets (functions, methods, nested functions)."""
        return len(self.callable_targets)

    @property
    def method_count(self) -> int:
        """Number of method targets, synchronous and asynchronous."""
        return sum(1 for function in self.callable_targets if function.kind in METHOD_KINDS)

    @property
    def function_count(self) -> int:
        """Number of non-method callable targets; nested functions included."""
        return self.callable_target_count - self.method_count

    @property
    def async_target_count(self) -> int:
        """Number of asynchronous callable targets."""
        return sum(1 for function in self.callable_targets if function.is_async)

    @property
    def class_count(self) -> int:
        """Number of discovered classes."""
        return sum(len(module.classes) for module in self.modules)


def is_excluded_directory(name: str) -> bool:
    """Return True when a directory name is a known non-source location."""
    return name in EXCLUDED_DIRECTORY_NAMES or name.endswith(EXCLUDED_DIRECTORY_SUFFIXES)


def scan_repository(target: Path) -> ScanSummary:
    """Discover Python source files under ``target``.

    ``target`` may be a directory or a single Python file. Directories are
    walked without following symlinks, and known non-source directories
    (virtual environments, caches, build output) are pruned. Results are
    deterministic: directory and file names are visited in sorted order.
    """
    resolved = _resolve_target(target)

    if resolved.is_file():
        if resolved.suffix != PYTHON_FILE_SUFFIX:
            raise UnsupportedTargetError(f"not a Python source file: {resolved}")
        return ScanSummary(
            target=resolved,
            is_file=True,
            python_files=(resolved,),
            excluded_directories=(),
        )

    if not resolved.is_dir():
        raise UnsupportedTargetError(
            f"target is neither a regular file nor a directory: {resolved}"
        )

    python_files, excluded_directories = _walk_python_files(resolved)
    return ScanSummary(
        target=resolved,
        is_file=False,
        python_files=python_files,
        excluded_directories=excluded_directories,
    )


def analyze_repository(target: Path) -> RepositoryAnalysis:
    """Discover Python source files under ``target`` and parse each one.

    Analysis order follows discovery order, so results are deterministic for a
    given filesystem state. Files are read and parsed with the standard
    library only; analyzed code is never imported or executed. A file that
    cannot be read or parsed yields a structured issue instead of aborting the
    scan.
    """
    discovery = scan_repository(target)
    base = discovery.target.parent if discovery.is_file else discovery.target
    modules = tuple(
        analyze_python_file(path, path.relative_to(base)) for path in discovery.python_files
    )
    return RepositoryAnalysis(discovery=discovery, modules=modules)


def _resolve_target(target: Path) -> Path:
    try:
        return target.expanduser().resolve(strict=True)
    except FileNotFoundError as exc:
        raise PathNotFoundError(f"path does not exist: {target}") from exc
    except (OSError, RuntimeError) as exc:
        raise ScanError(f"path cannot be resolved: {target} ({exc})") from exc


def _walk_python_files(root: Path) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    python_files: list[Path] = []
    excluded_directories: list[Path] = []

    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)

        kept: list[str] = []
        for name in sorted(dirnames):
            if is_excluded_directory(name):
                excluded_directories.append(current / name)
            else:
                kept.append(name)
        dirnames[:] = kept

        for filename in sorted(filenames):
            if filename.endswith(PYTHON_FILE_SUFFIX):
                python_files.append(current / filename)

    return tuple(python_files), tuple(excluded_directories)
