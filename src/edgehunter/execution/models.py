"""Typed models for the isolated execution layer.

Nothing in this package imports, evaluates, or executes target code on the host.
These models describe a request that *will* be executed somewhere else, a
staged copy of untrusted source, and a structured observation of what happened.
Every outcome carries its own classification so that "the function raised" and
"the sandbox could not start" are never confused, and so that neither can be
mistaken for a discovered defect.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from edgehunter.execution.codec import TypedValue, ValueKind
from edgehunter.generation.invocation import ArgumentAssignment


class OutcomeKind(str, Enum):
    """What actually happened during one invocation attempt.

    ``RETURNED`` and ``RAISED`` are observations of ordinary behaviour. None of
    these outcomes means a bug was found: Phase 6A performs no oracle evaluation,
    so an exception here is a fact about the run, not a defect claim.
    """

    RETURNED = "returned"
    RAISED = "raised"
    IMPORT_FAILURE = "import_failure"
    TARGET_NOT_FOUND = "target_not_found"
    TIMEOUT = "timeout"
    RESOURCE_LIMIT = "resource_limit"
    WORKER_FAILURE = "worker_failure"
    PROTOCOL_ERROR = "protocol_error"
    SANDBOX_UNAVAILABLE = "sandbox_unavailable"
    INELIGIBLE = "ineligible"


class SandboxState(str, Enum):
    """Whether the isolation backend could be established at all."""

    READY = "ready"
    UNAVAILABLE = "unavailable"
    ERROR = "error"
    NOT_REQUIRED = "not_required"
    """Nothing was eligible, so no sandbox was needed or probed."""


class EligibilityOutcome(str, Enum):
    """Whether one invocation plan may be handed to the sandbox."""

    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"


class IneligibilityReason(str, Enum):
    """Why a plan or target was refused.

    Refusal is always explicit and always carries a human-readable detail. An
    unsupported target never triggers a best-effort attempt.
    """

    PARTIAL_ARGUMENTS = "partial_arguments"
    SYMBOLIC_DEPENDENCY = "symbolic_dependency"
    NEEDS_RECEIVER = "needs_receiver"
    MISSING_ARGUMENTS = "missing_arguments"
    UNSUPPORTED_TARGET_KIND = "unsupported_target_kind"
    DECORATED_TARGET = "decorated_target"
    VARIADIC_PARAMETERS = "variadic_parameters"
    UNSUPPORTED_VALUE = "unsupported_value"
    UNSAFE_PATH = "unsafe_path"
    UNKNOWN_TARGET = "unknown_target"
    AMBIGUOUS_TARGET = "ambiguous_target"
    NOT_A_FUNCTION = "not_a_function"
    ASSEMBLY_FAILED = "assembly_failed"


class StagingFailure(str, Enum):
    """Why source staging refused to proceed."""

    PATH_ESCAPE = "path_escape"
    UNSAFE_FILE_TYPE = "unsafe_file_type"
    SYMLINK = "symlink"
    TOO_MANY_FILES = "too_many_files"
    FILE_TOO_LARGE = "file_too_large"
    TOTAL_TOO_LARGE = "total_too_large"
    NOT_A_FILE = "not_a_file"
    UNSUPPORTED_SUFFIX = "unsupported_suffix"


@dataclass(frozen=True, slots=True)
class TargetRef:
    """A statically discovered target, identified the way the scanner found it.

    ``relative_file`` is a POSIX-style path relative to the scanned root. The
    worker never accepts an absolute host path.
    """

    relative_file: str
    qualified_name: str
    lineno: int

    @property
    def identity(self) -> str:
        """Stable ``file::name`` identity used in reports and container names."""
        return f"{self.relative_file}::{self.qualified_name}"


@dataclass(frozen=True, slots=True)
class InvocationArguments:
    """A fully assembled call: ordered positionals plus explicit keywords."""

    args: tuple[TypedValue, ...]
    kwargs: tuple[tuple[str, TypedValue], ...]

    @property
    def is_empty(self) -> bool:
        """True when the call supplies no arguments at all."""
        return not self.args and not self.kwargs


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    """The complete, trusted description of one invocation to perform.

    This object contains structured metadata only: a target identity, typed
    argument values, the identifier of the adversarial action being exercised,
    and a nonce the worker must echo. It contains no Python source and no
    expression that could be evaluated.
    """

    protocol_version: int
    execution_id: str
    nonce: str
    target: TargetRef
    invocation: InvocationArguments
    action_id: str
    primary_action_kind: str
    primary_action_parameter: str


@dataclass(frozen=True, slots=True)
class RuntimeObservation:
    """What the worker saw, in structured form.

    ``opaque_return`` is set instead of ``return_value`` when the target returned
    something the protocol cannot represent. That is not a failure: the call
    completed and the value is simply not described.

    The exception identity fields are Phase 7A additions and every one of them is
    optional. A result document written before they existed decodes with all of
    them ``None``, which is not the same as ``exception_builtin is False``: the
    absence of a field means the identity was never observed, not that the class
    was proven to be a custom one. Keeping "unknown" representable is what stops
    the richer observation from silently upgrading a verdict.
    """

    outcome: OutcomeKind
    return_value: TypedValue | None = None
    opaque_return: str | None = None
    exception_type: str | None = None
    exception_message: str | None = None
    exception_module: str | None = None
    """``__module__`` of the raised class, as read in the sandbox."""

    exception_qualname: str | None = None
    """``__qualname__`` of the raised class, as read in the sandbox."""

    exception_builtin: bool | None = None
    """True only when the class *is* a real ``builtins`` exception class.

    ``None`` means the worker did not report it. A class merely named
    ``ValueError`` is ``False``, not ``True``.
    """

    exception_builtin_lineage: tuple[str, ...] | None = None
    """Builtin exception classes in the class's real MRO, most derived first.

    ``None`` when the worker reported no lineage at all, which is what keeps an
    older result document from being read as "proven not a subclass".
    """

    captured_stdout: str = ""
    captured_stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False

    @property
    def return_description(self) -> str | None:
        """A short rendering of the returned value, or ``None`` when absent."""
        if self.return_value is None:
            return None
        if self.return_value.kind is ValueKind.NONE:
            return "None"
        if self.return_value.kind in (
            ValueKind.LIST,
            ValueKind.TUPLE,
            ValueKind.SET,
            ValueKind.MAPPING,
        ):
            size = len(self.return_value.items) or len(self.return_value.pairs)
            return f"{self.return_value.kind.value} of {size} item(s)"
        return self.return_value.scalar

    @property
    def exception_identity_text(self) -> str:
        """A human rendering of the observed exception class identity.

        The qualified form is used when the sandbox supplied one, because a bare
        name does not identify a class uniquely. Falls back to the bare name, and
        then to ``unknown``.
        """
        if self.exception_qualname:
            if self.exception_module and self.exception_module != "builtins":
                return f"{self.exception_module}.{self.exception_qualname}"
            return self.exception_qualname
        return self.exception_type or "unknown"


@dataclass(frozen=True, slots=True)
class SandboxStatus:
    """The isolation backend's own condition, reported separately from the call."""

    backend: str
    state: SandboxState
    detail: str = ""
    image: str | None = None

    @property
    def is_usable(self) -> bool:
        """True only when a container actually ran."""
        return self.state is SandboxState.READY


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """The host-side record of one attempt, successful or not.

    ``duration_seconds`` is a measurement and is not expected to be reproducible
    across runs; nothing in the deterministic static stack depends on it.
    """

    execution_id: str
    target: TargetRef
    action_id: str
    outcome: OutcomeKind
    sandbox: SandboxStatus
    detail: str = ""
    duration_seconds: float | None = None
    observation: RuntimeObservation | None = None
    arguments: InvocationArguments | None = None
    """The assembled call, kept so a report can show what was passed."""

    assignments: tuple[ArgumentAssignment, ...] = ()
    """The plan's assignments, so each value keeps its origin in the report."""

    @property
    def executed(self) -> bool:
        """True when a container ran and the worker reported an observation."""
        return self.observation is not None


@dataclass(frozen=True, slots=True)
class ExecutionEligibility:
    """The deterministic verdict for one plan, before anything is staged."""

    outcome: EligibilityOutcome
    detail: str = ""
    reason: IneligibilityReason | None = None


@dataclass(frozen=True, slots=True)
class StagedSource:
    """A verified temporary copy of selected Python source.

    ``root`` is a directory EdgeHunter created. Nothing inside it is a symlink,
    every path was checked for containment, and the copy is mounted read-only.
    ``digests`` pairs each staged relative POSIX path with the SHA-256 of the
    bytes actually written, so the executed revision can be compared with the
    analyzed one.
    """

    root: Path
    files: tuple[Path, ...]
    total_bytes: int
    digests: tuple[tuple[str, str], ...] = ()

    def relative_path(self, relative: str) -> Path:
        """Absolute staged path for a POSIX-style relative source path."""
        return self.root / Path(relative)

    def digest_for(self, relative: str) -> str | None:
        """The staged digest for one relative POSIX path, or ``None`` if absent."""
        for name, digest in self.digests:
            if name == relative:
                return digest
        return None


@dataclass(frozen=True, slots=True)
class RunReport:
    """Everything one ``edgehunter run`` invocation produced."""

    results: tuple[ExecutionResult, ...]
    sandbox: SandboxStatus
    skipped: tuple[ExecutionEligibility, ...]
    rechecks: dict[str, tuple[ExecutionResult, ...]] = field(default_factory=dict)
    """Additional attempts per action id, present only when rechecks were requested.

    Keyed by ``ExecutionResult.action_id`` so an attempt is always attached to the
    plan that produced it. Empty by default: nothing retries automatically.
    """

    staged_digests: tuple[tuple[str, str], ...] = ()
    """``(relative POSIX path, SHA-256)`` for the staged copy, captured before deletion.

    Retained so oracle evaluation can verify that the executed revision matches the
    analyzed one after the temporary tree has been removed.
    """

    @property
    def executed_count(self) -> int:
        """Number of results that came from a container run."""
        return sum(1 for result in self.results if result.executed)
