"""Repository-level oracle aggregation.

Production evidence is mined per module during the single parse pass; test
expectations are resolved afterwards because resolution needs the whole
repository's target names. Deduplication, provenance, and per-target support
summaries are computed here.

Every source of evidence implemented so far is *explicit*: an assertion, a
validation guard, or an existing test expectation. That justifies a single
categorical strength (``STRONG``); no numeric confidence is derived from it.
"""

from __future__ import annotations

from dataclasses import dataclass

from edgehunter.oracle.models import (
    OracleSupport,
    ProductionPropertyCandidate,
    PropertyKind,
    TargetOracleSupport,
    TargetResolution,
    TestExpectationCandidate,
)
from edgehunter.oracle.test_expectations import deduplicate_expectations, resolve_expectations
from edgehunter.scanner.models import ModuleAnalysis


@dataclass(frozen=True, slots=True)
class RepositoryOracleAnalysis:
    """All mined oracle evidence for a repository."""

    production_properties: tuple[ProductionPropertyCandidate, ...]
    test_expectations: tuple[TestExpectationCandidate, ...]
    support: tuple[TargetOracleSupport, ...]

    @property
    def assertion_count(self) -> int:
        """Number of assertion-derived properties."""
        return sum(
            1 for item in self.production_properties if item.kind is PropertyKind.ASSERTED_CONDITION
        )

    @property
    def validation_rule_count(self) -> int:
        """Number of validation-guard rejection rules."""
        return sum(
            1 for item in self.production_properties if item.kind is PropertyKind.REJECTION_RULE
        )

    @property
    def resolved_test_expectation_count(self) -> int:
        """Number of test expectations tied to exactly one target."""
        return sum(
            1 for item in self.test_expectations if item.resolution is TargetResolution.RESOLVED
        )

    @property
    def unresolved_test_expectation_count(self) -> int:
        """Number of test expectations whose callee could not be resolved."""
        return sum(
            1 for item in self.test_expectations if item.resolution is TargetResolution.UNRESOLVED
        )

    @property
    def ambiguous_test_expectation_count(self) -> int:
        """Number of test expectations whose callee matched several targets."""
        return sum(
            1 for item in self.test_expectations if item.resolution is TargetResolution.AMBIGUOUS
        )

    @property
    def property_candidate_count(self) -> int:
        """Total number of mined property/expectation candidates."""
        return len(self.production_properties) + len(self.test_expectations)

    @property
    def strongly_supported_target_count(self) -> int:
        """Number of targets with strong explicit oracle evidence."""
        return sum(1 for entry in self.support if entry.level is OracleSupport.STRONG)


def build_target_index(modules: tuple[ModuleAnalysis, ...]) -> dict[str, tuple[str, ...]]:
    """Map each simple callable name to the qualified names that carry it."""
    index: dict[str, list[str]] = {}
    for module in modules:
        for function in module.functions:
            index.setdefault(function.name, []).append(function.qualified_name)
    return {name: tuple(sorted(qualified)) for name, qualified in sorted(index.items())}


def build_oracle_analysis(
    production: tuple[ProductionPropertyCandidate, ...],
    expectations: tuple[TestExpectationCandidate, ...],
    modules: tuple[ModuleAnalysis, ...],
) -> RepositoryOracleAnalysis:
    """Resolve, deduplicate, and summarise mined evidence for a repository."""
    resolved = resolve_expectations(
        deduplicate_expectations(list(expectations)), build_target_index(modules)
    )
    return RepositoryOracleAnalysis(
        production_properties=tuple(production),
        test_expectations=resolved,
        support=_summarize_support(tuple(production), resolved),
    )


def _summarize_support(
    production: tuple[ProductionPropertyCandidate, ...],
    expectations: tuple[TestExpectationCandidate, ...],
) -> tuple[TargetOracleSupport, ...]:
    """Per-target oracle availability, based on the strongest evidence source."""
    assertions: dict[str, int] = {}
    rules: dict[str, int] = {}
    for production_item in production:
        if production_item.kind is PropertyKind.ASSERTED_CONDITION:
            assertions[production_item.target] = assertions.get(production_item.target, 0) + 1
        elif production_item.kind is PropertyKind.REJECTION_RULE:
            rules[production_item.target] = rules.get(production_item.target, 0) + 1

    resolved_tests: dict[str, int] = {}
    for expectation in expectations:
        if expectation.resolution is TargetResolution.RESOLVED and expectation.target is not None:
            resolved_tests[expectation.target] = resolved_tests.get(expectation.target, 0) + 1

    targets = sorted(set(assertions) | set(rules) | set(resolved_tests))
    return tuple(
        TargetOracleSupport(
            target=target,
            level=OracleSupport.STRONG,
            assertion_count=assertions.get(target, 0),
            validation_rule_count=rules.get(target, 0),
            resolved_test_expectation_count=resolved_tests.get(target, 0),
        )
        for target in targets
    )
