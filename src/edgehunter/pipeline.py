"""Single-parse repository pipeline: structure, semantics, oracles, risk, actions.

This module is the composition root. It owns the one-pass loop: every
discovered file is read and parsed exactly once, and the resulting AST feeds
structural extraction, semantic extraction, boundary mining, oracle mining, risk
scoring, and action selection. Later phases that need the AST must consume
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
from edgehunter.generation.repository import build_repository_generation
from edgehunter.oracle.models import ProductionPropertyCandidate, TestExpectationCandidate
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
    """Everything one scan produces: structure, semantics, oracles, risk, actions."""

    semantics: RepositorySemantics
    oracles: RepositoryOracleAnalysis
    risk: RepositoryRiskProfile
    generation: RepositoryGeneration


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

    return RepositoryScan(
        semantics=semantics,
        oracles=build_oracle_analysis(
            production=tuple(production),
            expectations=tuple(expectations),
            modules=tuple(item.module for item in analyzed),
        ),
        risk=rank_profiles(tuple(profiles)),
        generation=build_repository_generation(tuple(action_sets)),
    )
