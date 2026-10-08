"""Repository discovery and static AST analysis for EdgeHunter."""

from edgehunter.scanner.ast_parser import (
    SourceFile,
    analyze_python_file,
    build_module_analysis,
    parse_source_file,
)
from edgehunter.scanner.models import (
    METHOD_KINDS,
    RECEIVER_PARAMETER_NAMES,
    SINGLE_VALUE_PARAMETER_KINDS,
    ClassInfo,
    FunctionInfo,
    ModuleAnalysis,
    ParameterInfo,
    ParameterKind,
    ReadIssue,
    SyntaxIssue,
    TargetKind,
)
from edgehunter.scanner.rendering import render_source
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
from edgehunter.scanner.targets import CallableNode, collect_callable_nodes, iter_callable_body

__all__ = [
    "METHOD_KINDS",
    "RECEIVER_PARAMETER_NAMES",
    "SINGLE_VALUE_PARAMETER_KINDS",
    "CallableNode",
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
    "SourceFile",
    "SyntaxIssue",
    "TargetKind",
    "UnsupportedTargetError",
    "analyze_python_file",
    "analyze_repository",
    "build_module_analysis",
    "collect_callable_nodes",
    "is_excluded_directory",
    "iter_callable_body",
    "parse_source_file",
    "render_source",
    "scan_repository",
]
