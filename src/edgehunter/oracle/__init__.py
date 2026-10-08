"""Explicit oracle and property mining for EdgeHunter.

This package answers "what behavior does the repository itself explicitly say
should happen?", using only statements the source writes: ``assert``
conditions, conservative ``if ...: raise`` validation guards, and a small,
deliberate subset of existing test expectations.

Everything is static. Nothing is imported, evaluated, or executed, no
annotation is resolved, and no evidence here is a defect claim. An assertion
means "the code expects this when execution reaches it", nothing stronger.
"""

from edgehunter.oracle.models import (
    ExpectedOutcome,
    OracleEvidence,
    OracleEvidenceKind,
    OracleStrength,
    OracleSupport,
    ProductionPropertyCandidate,
    PropertyKind,
    TargetOracleSupport,
    TargetResolution,
    TestExpectationCandidate,
)
from edgehunter.oracle.production import extract_production_properties
from edgehunter.oracle.repository import (
    RepositoryOracleAnalysis,
    build_oracle_analysis,
    build_target_index,
)
from edgehunter.oracle.test_expectations import (
    deduplicate_expectations,
    extract_test_expectations,
    is_test_file,
    resolve_expectations,
)

__all__ = [
    "ExpectedOutcome",
    "OracleEvidence",
    "OracleEvidenceKind",
    "OracleStrength",
    "OracleSupport",
    "ProductionPropertyCandidate",
    "PropertyKind",
    "RepositoryOracleAnalysis",
    "TargetOracleSupport",
    "TargetResolution",
    "TestExpectationCandidate",
    "build_oracle_analysis",
    "build_target_index",
    "deduplicate_expectations",
    "extract_production_properties",
    "extract_test_expectations",
    "is_test_file",
    "resolve_expectations",
]
