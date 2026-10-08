"""Isolated execution of static invocation plans.

This package is the boundary between EdgeHunter's static analysis and the
execution of untrusted target code. The rules it exists to enforce:

* **Target code runs only inside a container.** No module in ``scanner``,
  ``analysis``, ``oracle``, ``generation``, ``pipeline``, or the host half of
  this package ever imports or calls a target. The single exception is
  :mod:`edgehunter.runtime.worker`, which is a container entry point.
* **Docker is the only backend, and there is no fallback.** If Docker is absent
  or unusable the result is ``SANDBOX_UNAVAILABLE``; the project's own Python is
  never used to run target code instead.
* **Values travel as typed data.** A rendered literal is *recognized* by a
  whitelist grammar, never evaluated, and never turned into code.
* **Execution is explicit.** The caller must authorize it; ``scan`` never does.
* **Nothing here judges bugs.** Phase 6A records observations. An exception is an
  observation, not a defect.
"""

from edgehunter.execution.assembly import (
    AssemblyFailure,
    action_identifier,
    assemble_invocation,
    build_target_ref,
)
from edgehunter.execution.codec import (
    MAX_COLLECTION_ITEMS,
    MAX_DOCUMENT_BYTES,
    MAX_MAPPING_ENTRIES,
    MAX_NESTING_DEPTH,
    MAX_STRING_LENGTH,
    PROTOCOL_VERSION,
    ProtocolError,
    TypedValue,
    ValueKind,
    decode_document,
    decode_value,
    dumps,
    encode_float,
    encode_value,
    recognize_literal,
)
from edgehunter.execution.docker import (
    CONTAINER_USER,
    DEFAULT_TIMEOUT_SECONDS,
    MEMORY_LIMIT,
    PROHIBITED_ARGUMENTS,
    DockerPaths,
    DockerUnavailable,
    build_run_command,
    clamp_timeout,
    container_exists,
    docker_available,
    new_execution_id,
    remove_container,
    run_docker,
)
from edgehunter.execution.eligibility import check_eligibility, check_relative_path
from edgehunter.execution.models import (
    EligibilityOutcome,
    ExecutionEligibility,
    ExecutionRequest,
    ExecutionResult,
    IneligibilityReason,
    InvocationArguments,
    OutcomeKind,
    RunReport,
    RuntimeObservation,
    SandboxState,
    SandboxStatus,
    StagedSource,
    StagingFailure,
    TargetRef,
)
from edgehunter.execution.repository import (
    BACKEND_NAME,
    DEFAULT_MAX_CASES,
    WORKER_IMAGE,
    ExecutionRefused,
    RunOptions,
    dockerfile_path,
    execute_target,
    runtime_context,
)
from edgehunter.execution.staging import (
    MAX_FILE_BYTES,
    MAX_STAGED_FILES,
    MAX_TOTAL_BYTES,
    StagingError,
    discover_python_sources,
    remove_staged,
    stage_source,
)

__all__ = [
    "BACKEND_NAME",
    "CONTAINER_USER",
    "DEFAULT_MAX_CASES",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_COLLECTION_ITEMS",
    "MAX_DOCUMENT_BYTES",
    "MAX_FILE_BYTES",
    "MAX_MAPPING_ENTRIES",
    "MAX_NESTING_DEPTH",
    "MAX_STAGED_FILES",
    "MAX_STRING_LENGTH",
    "MAX_TOTAL_BYTES",
    "MEMORY_LIMIT",
    "PROHIBITED_ARGUMENTS",
    "PROTOCOL_VERSION",
    "WORKER_IMAGE",
    "AssemblyFailure",
    "DockerPaths",
    "DockerUnavailable",
    "EligibilityOutcome",
    "ExecutionEligibility",
    "ExecutionRefused",
    "ExecutionRequest",
    "ExecutionResult",
    "IneligibilityReason",
    "InvocationArguments",
    "OutcomeKind",
    "ProtocolError",
    "RunOptions",
    "RunReport",
    "RuntimeObservation",
    "SandboxState",
    "SandboxStatus",
    "StagedSource",
    "StagingError",
    "StagingFailure",
    "TargetRef",
    "TypedValue",
    "ValueKind",
    "action_identifier",
    "assemble_invocation",
    "build_run_command",
    "build_target_ref",
    "check_eligibility",
    "check_relative_path",
    "clamp_timeout",
    "container_exists",
    "decode_document",
    "decode_value",
    "discover_python_sources",
    "docker_available",
    "dockerfile_path",
    "dumps",
    "encode_float",
    "encode_value",
    "execute_target",
    "new_execution_id",
    "recognize_literal",
    "remove_container",
    "remove_staged",
    "run_docker",
    "runtime_context",
    "stage_source",
]
