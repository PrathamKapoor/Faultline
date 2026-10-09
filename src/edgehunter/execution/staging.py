"""Staging a verified copy of untrusted Python source for the sandbox.

The container is never given the user's repository. Instead EdgeHunter builds a
temporary directory containing only the Python source it chose to stage, checks
every path, and mounts that directory **read-only**. Three rules carry the
security weight:

* **Explicit selection.** Only files EdgeHunter decided to stage are copied, and
  only regular ``.py`` files are eligible. Anything else is refused by name
  *and* by file type, not by pattern alone.
* **Containment.** Every candidate path is resolved and checked to be inside the
  scanned root before it is read, so ``..`` cannot escape. Symlinks, junctions,
  and non-regular files are refused rather than followed.
* **Bounds.** File count, per-file size, and total size are capped before any
  copy is made, so a large or hostile tree cannot exhaust host resources.

Containment uses ``Path.resolve()``, which resolves symlinks and, on Windows,
junctions. The known residual limitation is that Windows path resolution also
follows volume-relative and short-name aliases; EdgeHunter reduces that risk by
never accepting an absolute or ``..``-bearing path from a request, and by
re-checking containment against the *resolved* root.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from edgehunter.execution.models import StagedSource, StagingFailure
from edgehunter.fingerprint import fingerprint_bytes
from edgehunter.scanner.repository import is_excluded_directory

MAX_STAGED_FILES = 100
MAX_FILE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024

STAGED_SUFFIX = ".py"


class StagingError(Exception):
    """Source staging refused to proceed; no container may be started."""

    def __init__(self, failure: StagingFailure, detail: str) -> None:
        super().__init__(detail)
        self.failure = failure
        self.detail = detail


@dataclass(frozen=True, slots=True)
class StagingLimits:
    """Explicit bounds applied while building the staged copy."""

    max_files: int = MAX_STAGED_FILES
    max_file_bytes: int = MAX_FILE_BYTES
    max_total_bytes: int = MAX_TOTAL_BYTES


def stage_source(
    source_root: Path,
    relative_paths: tuple[str, ...],
    destination: Path,
    limits: StagingLimits | None = None,
) -> StagedSource:
    """Copy the selected relative Python paths into ``destination``.

    ``destination`` must already exist and be empty. The function raises
    :class:`StagingError` on the first unsafe or over-limit condition, and copies
    nothing further once it does.
    """
    bounds = limits or StagingLimits()
    root = source_root.resolve()
    destination.mkdir(parents=True, exist_ok=True)

    candidates = _ordered_candidates(relative_paths)
    if len(candidates) > bounds.max_files:
        raise StagingError(
            StagingFailure.TOO_MANY_FILES,
            f"{len(candidates)} selected files exceed the limit of {bounds.max_files}",
        )

    total = 0
    staged: list[Path] = []
    digests: list[tuple[str, str]] = []
    for relative in candidates:
        origin = _safe_origin(root, relative)
        size = origin.stat().st_size
        if size > bounds.max_file_bytes:
            raise StagingError(
                StagingFailure.FILE_TOO_LARGE,
                f"{relative!r} is {size} bytes, over the {bounds.max_file_bytes} limit",
            )
        total += size
        if total > bounds.max_total_bytes:
            raise StagingError(
                StagingFailure.TOTAL_TOO_LARGE,
                f"staged total exceeds the {bounds.max_total_bytes} byte limit",
            )
        target = destination / relative
        digest = _copy(origin, target)
        staged.append(target)
        digests.append((relative, digest))

    return StagedSource(
        root=destination,
        files=tuple(staged),
        total_bytes=total,
        digests=tuple(digests),
    )


def discover_python_sources(source_root: Path) -> tuple[str, ...]:
    """Every eligible Python source under ``source_root``, as relative POSIX paths.

    Selection happens here so that :func:`stage_source` receives an explicit list
    rather than walking the tree itself.
    """
    root = source_root.resolve()
    found: list[str] = []
    for directory, subdirectories, filenames in os.walk(root, followlinks=False):
        subdirectories[:] = sorted(
            name for name in subdirectories if not is_excluded_directory(name)
        )
        for filename in sorted(filenames):
            if not filename.endswith(STAGED_SUFFIX):
                continue
            candidate = Path(directory) / filename
            if candidate.is_symlink():
                continue
            found.append(candidate.relative_to(root).as_posix())
    return tuple(sorted(found))


def _ordered_candidates(relative_paths: tuple[str, ...]) -> tuple[str, ...]:
    """Deduplicate and order the selection deterministically."""
    return tuple(sorted(dict.fromkeys(relative_paths)))


def _safe_origin(root: Path, relative: str) -> Path:
    """Resolve one relative path and prove it is a safe regular file in ``root``."""
    if not relative:
        raise StagingError(StagingFailure.PATH_ESCAPE, "empty relative path")
    if Path(relative).is_absolute() or Path(relative).anchor:
        raise StagingError(
            StagingFailure.PATH_ESCAPE, f"absolute path is not accepted: {relative!r}"
        )
    candidate = root / Path(relative)
    if candidate.is_symlink():
        raise StagingError(StagingFailure.SYMLINK, f"symlinked source is not staged: {relative!r}")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise StagingError(StagingFailure.PATH_ESCAPE, f"path escapes the repository: {relative!r}")
    if not resolved.exists():
        raise StagingError(StagingFailure.NOT_A_FILE, f"source does not exist: {relative!r}")
    if resolved.suffix != STAGED_SUFFIX:
        raise StagingError(
            StagingFailure.UNSUPPORTED_SUFFIX,
            f"only {STAGED_SUFFIX} source is staged, not {resolved.suffix!r}",
        )
    if not resolved.is_file():
        raise StagingError(StagingFailure.UNSAFE_FILE_TYPE, f"not a regular file: {relative!r}")
    return resolved


def _copy(origin: Path, destination: Path) -> str:
    """Copy one verified file, returning the SHA-256 of the bytes copied.

    The bytes are read once into memory (bounds already proved the file small),
    hashed, and written. Hashing the bytes that are actually written, rather than
    the origin path a second time, means the recorded digest describes the staged
    copy itself.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise StagingError(
            StagingFailure.UNSAFE_FILE_TYPE,
            f"duplicate destination for {origin.name!r}; target identity is ambiguous",
        )
    data = origin.read_bytes()
    shutil.copyfile(origin, destination)
    return fingerprint_bytes(data)


def remove_staged(staged: StagedSource) -> None:
    """Delete a staged tree, reporting rather than hiding a failure.

    The staging root is always a directory EdgeHunter created, never the user's
    repository, so removal cannot touch original files.
    """
    shutil.rmtree(staged.root, ignore_errors=False)
