"""Repository discovery and static AST analysis for EdgeHunter."""

from edgehunter.scanner.ast_parser import analyze_python_file
from edgehunter.scanner.models import (
    ClassInfo,
    FunctionInfo,
    ModuleAnalysis,
    ParameterInfo,
    ParameterKind,
    ReadIssue,
    SyntaxIssue,
    TargetKind,
)
from edgehunter.scanner.repository import (
    PathNotFoundError,
    RepositoryAnalysis,
    ScanError,
    ScanSummary,
    UnsupportedTargetError,
    analyze_repository,
    is_excluded_directory,
    scan_repository,
)

__all__ = [
    "ClassInfo",
    "FunctionInfo",
    "ModuleAnalysis",
    "ParameterInfo",
    "ParameterKind",
    "PathNotFoundError",
    "ReadIssue",
    "RepositoryAnalysis",
    "ScanError",
    "ScanSummary",
    "SyntaxIssue",
    "TargetKind",
    "UnsupportedTargetError",
    "analyze_python_file",
    "analyze_repository",
    "is_excluded_directory",
    "scan_repository",
]
