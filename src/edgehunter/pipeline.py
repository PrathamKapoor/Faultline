"""Single-parse repository pipeline: structure, semantics, oracles, risk, actions, planning.

This module is the composition root. It owns the one-pass loop: every
discovered file is read and parsed exactly once, and the resulting AST feeds
structural extraction, semantic extraction, boundary mining, oracle mining, risk
scoring, action selection, and invocation/strategy planning. Later phases that
need the AST must consume
:func:`edgehunter.analysis.repository.analyze_discovered_files` rather than
re-reading or re-parsing source.

No stage imports, evaluates, or executes analyzed code.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from edgehunter.analysis.models import RepositoryRiskProfile, TargetRiskProfile
from edgehunter.analysis.repository import (
    AnalyzedFile,
    RepositorySemantics,
    analyze_discovered_files,
)
from edgehunter.analysis.risk import rank_profiles, score_module
from edgehunter.generation.actions import select_module_actions
from edgehunter.generation.models import RepositoryGeneration, TargetActions
from edgehunter.generation.planning import (
    RepositoryAnnotations,
    RepositoryPlanning,
    annotation_report,
    build_repository_planning,
)
from edgehunter.generation.repository import build_repository_generation
from edgehunter.oracle.models import (
    ProductionPropertyCandidate,
    TargetResolution,
    TestExpectationCandidate,
)
from edgehunter.oracle.production import extract_production_properties
from edgehunter.oracle.repository import RepositoryOracleAnalysis, build_oracle_analysis
from edgehunter.oracle.test_expectations import extract_test_expectations
from edgehunter.scanner.repository import (
    RepositoryAnalysis,
    ScanSummary,
    scan_repository,
)


@dataclass(frozen=True, slots=True)
class RepositoryScan:
    """Everything one scan produces, from structure through to plans."""

    semantics: RepositorySemantics
    oracles: RepositoryOracleAnalysis
    risk: RepositoryRiskProfile
    generation: RepositoryGeneration
    planning: RepositoryPlanning
    annotations: RepositoryAnnotations


def analyze_repository(target: Path) -> RepositoryScan:
    """Analyze every Python source under ``target`` in a single parse pass.

    The returned object carries the Phase 1/2 aggregate as ``semantics`` so
    existing structure and semantics consumers keep working unchanged.
    """
    discovery = scan_repository(target)
    analyzed = analyze_discovered_files(discovery)
    return analyze_discovered(analyzed, discovery)


def analyze_discovered(
    analyzed: tuple[AnalyzedFile, ...],
    discovery: ScanSummary,
) -> RepositoryScan:
    """Derive all analyses from files that were already read and parsed."""
    semantics = RepositorySemantics(
        structure=RepositoryAnalysis(
            discovery=discovery,
            modules=tuple(item.module for item in analyzed),
        ),
        modules=tuple(item.semantics for item in analyzed),
    )

    production: list[ProductionPropertyCandidate] = []
    expectations: list[TestExpectationCandidate] = []
    profiles: list[TargetRiskProfile] = []
    action_sets: list[TargetActions] = []
    for item in analyzed:
        production.extend(extract_production_properties(item.source, item.semantics))
        expectations.extend(extract_test_expectations(item.source))
        profiles.extend(score_module(item.module, item.semantics))
        action_sets.extend(select_module_actions(item.module, item.semantics))

    modules = tuple(item.module for item in analyzed)
    generation = build_repository_generation(tuple(action_sets))
    oracles = build_oracle_analysis(
        production=tuple(production),
        expectations=tuple(expectations),
        modules=modules,
    )
    return RepositoryScan(
        semantics=semantics,
        oracles=oracles,
        risk=rank_profiles(tuple(profiles)),
        generation=generation,
        planning=build_repository_planning(
            generation,
            modules,
            _observed_literals(oracles.test_expectations),
            _observed_positions(oracles.test_expectations),
        ),
        annotations=annotation_report(modules),
    )


def _observed_literals(
    expectations: tuple[TestExpectationCandidate, ...],
) -> dict[str, tuple[str, ...]]:
    """Literal arguments a resolved existing test passed to each target.

    Only expectations that resolved to exactly one production target contribute,
    so an ambiguous callee never invents a baseline. When several tests exercise
    one target the first expectation in deterministic order wins, which keeps the
    choice reproducible rather than merging examples from different tests.
    """
    observed: dict[str, tuple[str, ...]] = {}
    for expectation in expectations:
        if expectation.resolution is not TargetResolution.RESOLVED:
            continue
        if expectation.target is None or not expectation.literal_arguments:
            continue
        observed.setdefault(expectation.target, expectation.literal_arguments)
    return observed


def _observed_positions(
    expectations: tuple[TestExpectationCandidate, ...],
) -> dict[str, tuple[int, ...]]:
    """The call position of each observed literal, keyed by resolved target.

    Kept beside :func:`_observed_literals` so a literal is bound by the position
    it was written at. A literal tuple on its own would shift left whenever an
    earlier argument was not a literal.
    """
    positions: dict[str, tuple[int, ...]] = {}
    for expectation in expectations:
        if expectation.resolution is not TargetResolution.RESOLVED:
            continue
        if expectation.target is None or not expectation.literal_arguments:
            continue
        positions.setdefault(expectation.target, expectation.argument_positions)
    return positions
