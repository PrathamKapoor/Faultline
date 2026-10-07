"""Repository discovery for EdgeHunter."""

from edgehunter.scanner.repository import (
    PathNotFoundError,
    ScanError,
    ScanSummary,
    UnsupportedTargetError,
    is_excluded_directory,
    scan_repository,
)

__all__ = [
    "PathNotFoundError",
    "ScanError",
    "ScanSummary",
    "UnsupportedTargetError",
    "is_excluded_directory",
    "scan_repository",
]
