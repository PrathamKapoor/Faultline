"""Shrinkability assessment and bounded counterexample minimization.

This package exists because the project's central correctness rule needs an
implementation: **an exact test expectation does not survive changing an input.**
``assert f(100) == 200`` describes one invocation, so it cannot authorize
shrinking, and the first thing :mod:`edgehunter.shrink.shrinkability` does is
refuse it -- before any smaller input is executed, and with no flag that
bypasses the refusal.

Minimization then needs an oracle that genuinely applies across a range of
inputs. In Phase 7B that comes from exactly one place: a **user-declared
contract** the user explicitly selects, which states a condition over a declared
integer domain and is bound to one source revision by SHA-256. It is an
expectation the user asserts, never a fact proven from the repository, and no
production guard or existing test is ever converted into one automatically.

Everything the search does is bounded, deterministic, and snapshot-fixed. A
result is reported as the smallest input *this bounded search verified*, never
as a proven global minimum, and never as a confirmed defect.
"""

from edgehunter.shrink.contract import (
    CONTRACT_VERSION,
    MAX_CONTRACT_FILE_BYTES,
    Contract,
    ContractError,
    ContractFile,
    read_contract_file,
)
from edgehunter.shrink.domain import DomainError, DomainOperator, IntegerDomain
from edgehunter.shrink.models import (
    DEFAULT_MAX_EXECUTIONS,
    DEFAULT_MAX_PROPOSALS,
    DEFAULT_REPRODUCTION_ATTEMPTS,
    HARD_MAX_EXECUTIONS,
    HARD_MAX_PROPOSALS,
    HARD_MAX_REPRODUCTION_ATTEMPTS,
    MAX_SIGNED_INT,
    MIN_SIGNED_INT,
    AttemptOutcome,
    InvocationOrigin,
    OracleSource,
    ShrinkabilityAssessment,
    ShrinkabilityKind,
    ShrinkAttempt,
    ShrinkPolicy,
    ShrinkResult,
    ShrinkStatus,
)
from edgehunter.shrink.proposals import complexity, is_simpler, propose_values
from edgehunter.shrink.repository import (
    SHRINK_ARTIFACT_SCHEMA,
    AttemptRunner,
    ContractVerdict,
    SessionAttemptRunner,
    ShrinkRequest,
    evaluate_against_contract,
    preflight_contract,
    shrink_artifact_document,
    shrink_integer,
    write_shrink_artifact,
)
from edgehunter.shrink.shrinkability import (
    assess_production_property,
    assess_target_shrinkability,
    assess_test_expectation,
    refuse_conflicting,
)
from edgehunter.shrink.signature import FailureSignature, signature_of

__all__ = [
    "CONTRACT_VERSION",
    "DEFAULT_MAX_EXECUTIONS",
    "DEFAULT_MAX_PROPOSALS",
    "DEFAULT_REPRODUCTION_ATTEMPTS",
    "HARD_MAX_EXECUTIONS",
    "HARD_MAX_PROPOSALS",
    "HARD_MAX_REPRODUCTION_ATTEMPTS",
    "MAX_CONTRACT_FILE_BYTES",
    "MAX_SIGNED_INT",
    "MIN_SIGNED_INT",
    "SHRINK_ARTIFACT_SCHEMA",
    "AttemptOutcome",
    "AttemptRunner",
    "Contract",
    "ContractError",
    "ContractFile",
    "ContractVerdict",
    "DomainError",
    "DomainOperator",
    "FailureSignature",
    "IntegerDomain",
    "InvocationOrigin",
    "OracleSource",
    "SessionAttemptRunner",
    "ShrinkAttempt",
    "ShrinkPolicy",
    "ShrinkRequest",
    "ShrinkResult",
    "ShrinkStatus",
    "ShrinkabilityAssessment",
    "ShrinkabilityKind",
    "assess_production_property",
    "assess_target_shrinkability",
    "assess_test_expectation",
    "complexity",
    "evaluate_against_contract",
    "is_simpler",
    "preflight_contract",
    "propose_values",
    "read_contract_file",
    "refuse_conflicting",
    "shrink_artifact_document",
    "shrink_integer",
    "signature_of",
    "write_shrink_artifact",
]
