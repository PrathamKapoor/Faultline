"""Filesystem-level repository discovery for EdgeHunter.

This module performs discovery only. It never imports, evaluates, or executes
code from the scanned repository; code execution belongs to a separate,
isolated subsystem that does not exist yet.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

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
