"""Repository-level orchestration for static semantic analysis.

This module reuses the Phase 1 safe reading and parsing stages, so a scan reads
and parses each file exactly once and then derives structural facts *and*
semantic facts from the same AST.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from edgehunter.analysis.models import ModuleSemantics, TargetBoundaries
from edgehunter.analysis.semantics import extract_module_semantics
from edgehunter.scanner.ast_parser import (
    SourceFile,
    build_module_analysis,
    parse_source_file,
)
from edgehunter.scanner.models import ModuleAnalysis
from edgehunter.scanner.repository import (
    RepositoryAnalysis,
    ScanSummary,
    scan_repository,
)


@dataclass(frozen=True, slots=True)
class RepositorySemantics:
    """Structural analysis plus static semantic facts for a whole repository."""

    structure: RepositoryAnalysis
    modules: tuple[ModuleSemantics, ...]

    @property
    def boundaries(self) -> tuple[TargetBoundaries, ...]:
        """Boundary candidates for every target, in module then source order."""
        return tuple(entry for module in self.modules for entry in module.boundaries)

    @property
    def predicate_count(self) -> int:
        """Total number of predicates across all modules."""
        return sum(module.predicate_count for module in self.modules)

    @property
    def comparison_count(self) -> int:
        """Total number of adjacent comparisons across all modules."""
        return sum(module.comparison_count for module in self.modules)

    @property
    def arithmetic_expression_count(self) -> int:
        """Total number of maximal arithmetic expressions across all modules."""
        return sum(module.arithmetic_expression_count for module in self.modules)

    @property
    def boundary_candidate_count(self) -> int:
        """Total number of boundary candidates across all modules."""
        return sum(module.boundary_candidate_count for module in self.modules)

    @property
    def targets_with_boundaries(self) -> int:
        """Number of targets that received at least one boundary candidate."""
        return len(self.boundaries)


@dataclass(frozen=True, slots=True)
class AnalyzedFile:
    """Structure, semantics, and parsed AST for one file.

    Internal pipeline state: it carries the parsed ``SourceFile`` so later stages
    (oracle mining) reuse the same read and parse instead of re-reading source.
    """

    source: SourceFile
    module: ModuleAnalysis
    semantics: ModuleSemantics


def analyze_discovered_files(discovery: ScanSummary) -> tuple[AnalyzedFile, ...]:
    """Read and parse every discovered file once, deriving structure and semantics.

    The parsed AST is shared with later stages through :class:`AnalyzedFile`.
    Files are processed in discovery order, so results are deterministic for a
    given filesystem state.
    """
    base = discovery.target.parent if discovery.is_file else discovery.target

    analyzed: list[AnalyzedFile] = []
    for path in discovery.python_files:
        relative_path = path.relative_to(base)
        source = parse_source_file(path, relative_path)
        module = build_module_analysis(source)
        analyzed.append(
            AnalyzedFile(
                source=source,
                module=module,
                semantics=extract_module_semantics(source, module),
            )
        )
    return tuple(analyzed)


def analyze_repository_semantics(target: Path) -> RepositorySemantics:
    """Discover, parse, and analyze Python sources under ``target``.

    Each discovered file is read and parsed once; the resulting AST feeds both
    structural extraction and semantic extraction. Files are processed in
    discovery order, so results are deterministic for a given filesystem
    state. Analyzed code is never imported or executed, and a file that cannot
    be read or parsed contributes a structured issue instead of facts.
    """
    discovery = scan_repository(target)
    analyzed = analyze_discovered_files(discovery)
    structure = RepositoryAnalysis(
        discovery=discovery,
        modules=tuple(item.module for item in analyzed),
    )
    return RepositorySemantics(
        structure=structure,
        modules=tuple(item.semantics for item in analyzed),
    )
