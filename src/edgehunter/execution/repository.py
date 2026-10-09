"""Orchestration: static plans in, structured observations out.

This module is the host side of the isolation boundary. It performs, in order:

1. verify that Docker is usable (and stop with ``SANDBOX_UNAVAILABLE`` if not),
2. confirm the caller authorized execution,
3. select a bounded number of eligible plans for one target,
4. stage a verified copy of the source once, read-only, into a temporary tree,
5. for each selected plan: check eligibility, encode a request, run exactly one
   container, read exactly one result document, and remove the container,
6. delete the temporary tree.

Nothing here imports or executes target code. The only subprocess it starts is
the ``docker`` CLI, and the result document is parsed as data — never evaluated.

A missing, malformed, or nonce-mismatched result is reported as
``PROTOCOL_ERROR``/``WORKER_FAILURE``. It is never assumed to be a successful run,
because a missing result is not a passing result.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final

from edgehunter.execution.assembly import action_identifier, assemble_invocation, build_target_ref
from edgehunter.execution.codec import (
    PROTOCOL_VERSION,
    ProtocolError,
    TypedValue,
    check_document_size,
    decode_document,
    decode_value,
    dumps,
    encode_value,
)
from edgehunter.execution.docker import (
    DEFAULT_TIMEOUT_SECONDS,
    DockerPaths,
    DockerUnavailable,
    build_build_command,
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
    OutcomeKind,
    RunReport,
    RuntimeObservation,
    SandboxState,
    SandboxStatus,
    StagedSource,
    StagingFailure,
)
from edgehunter.execution.staging import (
    StagingError,
    discover_python_sources,
    remove_staged,
    stage_source,
)
from edgehunter.generation.invocation import ArgumentAssignment, InvocationPlan
from edgehunter.pipeline import RepositoryScan
from edgehunter.scanner.models import FunctionInfo

WORKER_IMAGE: Final[str] = "edgehunter-runtime:1"

DEFAULT_MAX_CASES: Final[int] = 5
HARD_MAX_CASES: Final[int] = 20

HARD_MAX_RECHECKS: Final[int] = 5
"""The absolute ceiling on extra attempts per plan.

Rechecking is opt-in and bounded. Nothing in this module retries a failure on its
own, retries are never automatic, and the total attempts per plan can never exceed
``1 + HARD_MAX_RECHECKS``.
"""

BACKEND_NAME: Final[str] = "docker"


def runtime_context() -> Path:
    """The directory containing the ``edgehunter`` package.

    This is the Docker build context: it holds only EdgeHunter's own trusted
    sources, and it resolves identically from a checkout and from an installed
    distribution.
    """
    import edgehunter

    return Path(edgehunter.__file__).resolve().parent.parent


def dockerfile_path() -> Path:
    """The trusted worker Dockerfile shipped inside the package."""
    return Path(__file__).resolve().parents[1] / "runtime" / "Dockerfile"


class ExecutionRefused(Exception):
    """Execution was not attempted; the reason is safe to show the user."""


@dataclass(frozen=True, slots=True)
class RunOptions:
    """Caller-controlled bounds for one ``run`` invocation."""

    target: str
    max_cases: int = DEFAULT_MAX_CASES
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    rechecks: int = 0
    """Extra attempts per plan, only ever non-zero from an explicit request.

    Zero is the default and nothing retries on its own. Each recheck reuses the
    same staged snapshot and the same request shape, so a repetition measures the
    same source revision rather than a new one.
    """

    def bounded_case_limit(self) -> int:
        """Clamp the requested case count into the supported range."""
        return max(1, min(HARD_MAX_CASES, self.max_cases))

    def bounded_timeout(self) -> float:
        """Clamp the requested timeout into the supported range."""
        return clamp_timeout(self.timeout_seconds)

    def bounded_rechecks(self) -> int:
        """Clamp the requested recheck count into the supported range."""
        return max(0, min(HARD_MAX_RECHECKS, self.rechecks))

    @property
    def attempts_per_plan(self) -> int:
        """Total containers one plan may consume, always bounded."""
        return 1 + self.bounded_rechecks()


def execute_target(
    scan: RepositoryScan,
    source_root: Path,
    options: RunOptions,
    *,
    authorized: bool,
    repository_context: Path,
) -> RunReport:
    """Execute a bounded set of eligible plans for one selected target.

    ``authorized`` must be true, which the CLI sets only from an explicit
    opt-in flag. ``repository_context`` is the trusted directory used to build the
    worker image; it is EdgeHunter's own source, never the scanned repository.
    """
    if not authorized:
        raise ExecutionRefused("execution requires explicit authorization; pass --allow-execution")

    selected, refused = _select_plans(scan, options)
    if not selected:
        sandbox = SandboxStatus(
            backend=BACKEND_NAME,
            state=SandboxState.NOT_REQUIRED,
            detail="no eligible plan for the selected target; no sandbox was needed",
        )
        return RunReport(results=(), sandbox=sandbox, skipped=refused)

    available, detail = docker_available()
    if not available:
        return RunReport(results=(), sandbox=_unavailable_sandbox(detail), skipped=refused)

    try:
        image_ready, image_detail = ensure_image(repository_context)
    except DockerUnavailable as exc:
        return RunReport(results=(), sandbox=_unavailable_sandbox(str(exc)), skipped=refused)
    if not image_ready:
        return RunReport(results=(), sandbox=_unavailable_sandbox(image_detail), skipped=refused)

    sandbox = SandboxStatus(
        backend=BACKEND_NAME, state=SandboxState.READY, detail=image_detail, image=WORKER_IMAGE
    )

    run_root = Path(tempfile.mkdtemp(prefix="edgehunter-run-"))
    staged: StagedSource | None = None
    results: list[ExecutionResult] = []
    rechecks: dict[str, tuple[ExecutionResult, ...]] = {}
    try:
        staged = _stage_workspace(source_root, run_root)
        for plan, function in selected:
            first = _execute_one(plan, function, staged, run_root, options.bounded_timeout())
            results.append(first)
            if options.bounded_rechecks():
                # Every attempt reuses the same staged snapshot, so all attempts
                # measure the identical source revision by construction.
                rechecks[first.action_id] = tuple(
                    _execute_one(plan, function, staged, run_root, options.bounded_timeout())
                    for _ in range(options.bounded_rechecks())
                )
    except StagingError as exc:
        return RunReport(
            results=tuple(results),
            sandbox=SandboxStatus(
                backend=BACKEND_NAME, state=SandboxState.ERROR, detail=exc.detail
            ),
            skipped=refused,
        )
    finally:
        # The staged digests are captured before deletion so oracle evaluation can
        # still prove the executed bytes matched the analyzed ones after the tree
        # is gone.
        digests = staged.digests if staged is not None else ()
        cleanup_problem = _cleanup_problems(staged, run_root)
        if cleanup_problem is not None:
            sandbox = SandboxStatus(
                backend=BACKEND_NAME,
                state=SandboxState.ERROR,
                detail=f"{sandbox.detail}; {cleanup_problem}",
                image=WORKER_IMAGE,
            )

    return RunReport(
        results=tuple(results),
        sandbox=sandbox,
        skipped=refused,
        rechecks={key: value for key, value in rechecks.items()},
        staged_digests=digests,
    )


def _cleanup_problems(staged: StagedSource | None, run_root: Path) -> str | None:
    """Delete the temporary tree and report any failure instead of hiding it."""
    if staged is not None:
        try:
            remove_staged(staged)
        except OSError as exc:
            return f"staged source could not be removed: {exc}"
    try:
        shutil.rmtree(run_root)
    except OSError as exc:
        return f"temporary execution directory could not be removed: {exc}"
    return None


def ensure_image(context: Path) -> tuple[bool, str]:
    """Return whether the trusted worker image is usable, building it if needed."""
    inspect = run_docker(
        ("docker", "image", "inspect", WORKER_IMAGE), timeout_seconds=clamp_timeout(10.0)
    )
    if inspect.returncode == 0:
        return True, "trusted worker image available"
    dockerfile = dockerfile_path()
    if not dockerfile.is_file():
        return False, f"the worker Dockerfile is missing from the installation: {dockerfile}"
    build = run_docker(
        build_build_command(context=context, tag=WORKER_IMAGE, dockerfile=dockerfile),
        timeout_seconds=clamp_timeout(600.0),
    )
    if build.returncode != 0:
        return False, f"worker image build failed: {build.stderr.strip()[:400]}"
    return True, "trusted worker image built from trusted sources"


def _parse_target_spec(spec: str) -> tuple[str | None, str]:
    """Split ``path::qualified_name`` into an optional file hint and a name."""
    if "::" in spec:
        hint, _, name = spec.rpartition("::")
        return (hint.strip() or None), name.strip()
    return None, spec.strip()


def _select_plans(
    scan: RepositoryScan, options: RunOptions
) -> tuple[list[tuple[InvocationPlan, FunctionInfo]], tuple[ExecutionEligibility, ...]]:
    """Choose eligible plans for the selected target in deterministic order.

    A qualified name alone is not a unique identity: two modules may both define
    ``process``. When a file hint is supplied it must resolve to exactly one
    match, and an unqualified name that matches more than one module is refused
    rather than silently resolved to whichever file happened to be scanned first.
    """
    file_hint, qualified = _parse_target_spec(options.target)
    matches = [
        (module.relative_path, function)
        for module in scan.semantics.structure.modules
        for function in module.functions
        if function.qualified_name == qualified
    ]
    if file_hint is not None:
        wanted = PurePosixPath(file_hint.replace("\\", "/")).as_posix()
        matches = [
            (relative, function)
            for relative, function in matches
            if relative.as_posix() == wanted
            or relative.name == wanted
            or relative.as_posix().endswith(f"/{wanted}")
        ]

    if not matches:
        return [], (
            _refusal(
                IneligibilityReason.UNKNOWN_TARGET,
                f"no analyzed target named {qualified!r} in this repository"
                + (f" (file hint {file_hint!r})" if file_hint else ""),
            ),
        )
    if len(matches) > 1:
        where = ", ".join(sorted(relative.as_posix() for relative, _ in matches))
        return [], (
            _refusal(
                IneligibilityReason.AMBIGUOUS_TARGET,
                f"{qualified!r} is defined in more than one file ({where}); "
                f"select it with --target 'path::{qualified}'",
            ),
        )

    function = matches[0][1]
    refused: list[ExecutionEligibility] = []
    eligible: list[tuple[InvocationPlan, FunctionInfo]] = []
    for plan in scan.planning.plans:
        if plan.target != qualified:
            continue
        if plan.relative_path.as_posix() != matches[0][0].as_posix():
            continue
        verdict = check_relative_path(plan.relative_path.as_posix())
        if verdict is not None:
            refused.append(verdict)
            continue
        verdict = check_eligibility(plan, function)
        if verdict.outcome is EligibilityOutcome.INELIGIBLE:
            refused.append(verdict)
            continue
        eligible.append((plan, function))
    return eligible[: options.bounded_case_limit()], tuple(refused)


def _refusal(reason: IneligibilityReason, detail: str) -> ExecutionEligibility:
    """Build a refusal verdict with a reason and a message the user can act on."""
    return ExecutionEligibility(outcome=EligibilityOutcome.INELIGIBLE, reason=reason, detail=detail)


def _stage_workspace(source_root: Path, run_root: Path) -> StagedSource:
    """Stage the Python sources once, read-only, for the whole run."""
    selected = discover_python_sources(source_root)
    if not selected:
        raise StagingError(StagingFailure.NOT_A_FILE, "no python source was found to stage")
    return stage_source(source_root, selected, run_root / "workspace")


def _execute_one(
    plan: InvocationPlan,
    function: FunctionInfo,
    staged: StagedSource,
    run_root: Path,
    timeout_seconds: float,
) -> ExecutionResult:
    """Run exactly one container for one plan and decode its single result."""
    target_ref = build_target_ref(plan)
    execution_id = new_execution_id()
    container_name = f"edgehunter-run-{execution_id[:16]}"

    try:
        request = _request_for(plan, function, execution_id)
    except Exception as exc:  # noqa: BLE001 - assembly refusal becomes a result
        return ExecutionResult(
            execution_id=execution_id,
            target=target_ref,
            action_id=action_identifier(plan),
            outcome=OutcomeKind.INELIGIBLE,
            sandbox=_ready_sandbox(),
            detail=f"request could not be built: {exc}",
        )

    input_dir = run_root / f"{execution_id}-in"
    output_dir = run_root / f"{execution_id}-out"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = DockerPaths(
        workspace=staged.root, request=input_dir / "request.json", output=output_dir / "result.json"
    )
    try:
        document_text = _request_document(request)
    except Exception as exc:  # noqa: BLE001 - an oversized request is a refusal
        return ExecutionResult(
            execution_id=execution_id,
            target=target_ref,
            action_id=request.action_id,
            outcome=OutcomeKind.INELIGIBLE,
            sandbox=_ready_sandbox(),
            detail=f"request rejected before execution: {exc}",
        )
    try:
        paths.request.write_text(document_text, encoding="utf-8")
    except OSError as exc:
        return ExecutionResult(
            execution_id=execution_id,
            target=target_ref,
            action_id=request.action_id,
            outcome=OutcomeKind.WORKER_FAILURE,
            sandbox=_ready_sandbox(),
            detail=f"request could not be written: {exc}",
        )

    command = build_run_command(container_name=container_name, image=WORKER_IMAGE, paths=paths)
    cleanup = ("docker", "rm", "--force", container_name)
    outcome = run_docker(command, timeout_seconds=timeout_seconds, cleanup_command=cleanup)
    leftover = container_exists(container_name)
    if leftover:
        # A forced removal can itself fail; confirm rather than assume.
        remove_container(container_name)
        cleanup_ok = not container_exists(container_name)
    else:
        cleanup_ok = True

    return _decode_outcome(
        execution_id=execution_id,
        request=request,
        outcome=outcome,
        timeout_seconds=timeout_seconds,
        result_path=paths.output,
        cleanup_ok=cleanup_ok,
        assignments=plan.assignments,
    )


def _request_for(
    plan: InvocationPlan, function: FunctionInfo, execution_id: str
) -> ExecutionRequest:
    arguments = assemble_invocation(plan, function)
    return ExecutionRequest(
        protocol_version=PROTOCOL_VERSION,
        execution_id=execution_id,
        nonce=new_execution_id(),
        target=build_target_ref(plan),
        invocation=arguments,
        action_id=action_identifier(plan),
        primary_action_kind=plan.primary_action.kind.value,
        primary_action_parameter=plan.primary_action.parameter,
    )


def _request_document(request: ExecutionRequest) -> str:
    """Serialize the request as strict JSON with no executable content.

    The host enforces the same 64 KiB budget the worker enforces, so an oversized
    request is refused before a container is ever started rather than being sent
    only to be rejected at the far end.
    """
    text = dumps(
        {
            "protocol_version": request.protocol_version,
            "execution_id": request.execution_id,
            "nonce": request.nonce,
            "target": {
                "relative_file": request.target.relative_file,
                "qualified_name": request.target.qualified_name,
                "lineno": request.target.lineno,
            },
            "invocation": {
                "args": [encode_value(value) for value in request.invocation.args],
                "kwargs": {name: encode_value(value) for name, value in request.invocation.kwargs},
            },
            "action_id": request.action_id,
            "primary_action_kind": request.primary_action_kind,
            "primary_action_parameter": request.primary_action_parameter,
        }
    )
    check_document_size(text)
    return text


# Every key the worker is permitted to write into a result document. An unknown
# key means the container is not running the code we think it is, so the document
# is refused instead of partially trusted.
_RESULT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "protocol_version",
        "execution_id",
        "nonce",
        "outcome",
        "detail",
        "exception_type",
        "exception_message",
        "exception_module",
        "exception_qualname",
        "exception_builtin",
        "exception_builtin_lineage",
        "return_value",
        "opaque_return",
        "duration_seconds",
        "captured_stdout",
        "captured_stderr",
        "stdout_truncated",
        "stderr_truncated",
        "native_output_discarded",
    }
)


def _decode_outcome(
    *,
    execution_id: str,
    request: ExecutionRequest,
    outcome: object,
    timeout_seconds: float,
    result_path: Path,
    cleanup_ok: bool,
    assignments: tuple[ArgumentAssignment, ...],
) -> ExecutionResult:
    """Turn a finished container into a typed result, never guessing."""
    sandbox = _ready_sandbox()
    timed_out = bool(getattr(outcome, "timed_out", False))
    returncode = int(getattr(outcome, "returncode", -1))
    duration = float(getattr(outcome, "duration_seconds", 0.0))

    if timed_out:
        return ExecutionResult(
            execution_id=execution_id,
            target=request.target,
            action_id=request.action_id,
            outcome=OutcomeKind.TIMEOUT,
            sandbox=sandbox,
            detail=f"execution exceeded the {timeout_seconds}s budget; container removed",
            duration_seconds=duration,
            arguments=request.invocation,
            assignments=assignments,
        )

    if not result_path.exists():
        return ExecutionResult(
            execution_id=execution_id,
            target=request.target,
            action_id=request.action_id,
            outcome=OutcomeKind.WORKER_FAILURE,
            sandbox=sandbox,
            detail=(
                f"worker produced no result document (exit {returncode}): "
                f"{_clip(getattr(outcome, 'stderr', ''))}"
            ),
            duration_seconds=duration,
            arguments=request.invocation,
            assignments=assignments,
        )

    try:
        document = decode_document(result_path.read_text(encoding="utf-8"))
    except (ProtocolError, OSError) as exc:
        return ExecutionResult(
            execution_id=execution_id,
            target=request.target,
            action_id=request.action_id,
            outcome=OutcomeKind.PROTOCOL_ERROR,
            sandbox=sandbox,
            detail=f"result document rejected: {exc}",
            duration_seconds=duration,
            arguments=request.invocation,
            assignments=assignments,
        )

    unknown = sorted(set(document) - _RESULT_KEYS)
    if unknown:
        return ExecutionResult(
            execution_id=execution_id,
            target=request.target,
            action_id=request.action_id,
            outcome=OutcomeKind.PROTOCOL_ERROR,
            sandbox=sandbox,
            detail=f"result document carries unexpected key(s): {', '.join(unknown)}",
            duration_seconds=duration,
            arguments=request.invocation,
            assignments=assignments,
        )

    if document.get("nonce") != request.nonce:
        return ExecutionResult(
            execution_id=execution_id,
            target=request.target,
            action_id=request.action_id,
            outcome=OutcomeKind.PROTOCOL_ERROR,
            sandbox=sandbox,
            detail="result document nonce did not match this execution request",
            duration_seconds=duration,
            arguments=request.invocation,
            assignments=assignments,
        )

    if document.get("execution_id") != execution_id:
        return ExecutionResult(
            execution_id=execution_id,
            target=request.target,
            action_id=request.action_id,
            outcome=OutcomeKind.PROTOCOL_ERROR,
            sandbox=sandbox,
            detail="result document identified a different execution",
            duration_seconds=duration,
            arguments=request.invocation,
            assignments=assignments,
        )

    observation = _observation_from(document)
    detail = str(document.get("detail", ""))
    if not cleanup_ok:
        detail = f"{detail} (container cleanup required a second attempt)".strip()
    return ExecutionResult(
        execution_id=execution_id,
        target=request.target,
        action_id=request.action_id,
        outcome=observation.outcome,
        sandbox=sandbox,
        detail=detail,
        duration_seconds=float(document.get("duration_seconds", duration)),
        observation=observation,
        arguments=request.invocation,
        assignments=assignments,
    )


def _observation_from(document: dict[str, Any]) -> RuntimeObservation:
    """Decode the worker's structured outcome, refusing anything unexpected."""
    raw_outcome = document.get("outcome")
    try:
        outcome = OutcomeKind(str(raw_outcome))
    except ValueError:
        return RuntimeObservation(outcome=OutcomeKind.PROTOCOL_ERROR)

    return_value: TypedValue | None = None
    if "return_value" in document:
        try:
            return_value = decode_value(document["return_value"])
        except ProtocolError:
            return_value = None

    return RuntimeObservation(
        outcome=outcome,
        return_value=return_value,
        opaque_return=_optional_str(document.get("opaque_return")),
        exception_type=_optional_str(document.get("exception_type")),
        exception_message=_optional_str(document.get("exception_message")),
        exception_module=_optional_str(document.get("exception_module")),
        exception_qualname=_optional_str(document.get("exception_qualname")),
        exception_builtin=_optional_bool(document.get("exception_builtin")),
        exception_builtin_lineage=_optional_str_tuple(document.get("exception_builtin_lineage")),
        captured_stdout=_optional_str(document.get("captured_stdout")) or "",
        captured_stderr=_optional_str(document.get("captured_stderr")) or "",
        stdout_truncated=bool(document.get("stdout_truncated", False)),
        stderr_truncated=bool(document.get("stderr_truncated", False)),
    )


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_bool(value: object) -> bool | None:
    """A boolean field, or ``None`` when absent or of the wrong type.

    Malformed metadata must never crash the host, and it must never be coerced:
    a value that is not literally ``True`` or ``False`` is treated as unknown, not
    as ``False``, so a hostile document cannot manufacture a verdict.
    """
    if value is True:
        return True
    if value is False:
        return False
    return None


def _optional_str_tuple(value: object) -> tuple[str, ...] | None:
    """A list of names, or ``None`` when absent or not a list of strings.

    A lineage that is partly malformed is dropped entirely rather than repaired,
    because a partially-trusted inheritance chain would be worse than none: it
    could make a non-subclass look like a subclass.
    """
    if not isinstance(value, list):
        return None
    if not all(isinstance(entry, str) for entry in value):
        return None
    return tuple(value)


def _ready_sandbox() -> SandboxStatus:
    return SandboxStatus(backend=BACKEND_NAME, state=SandboxState.READY)


def _unavailable_sandbox(detail: str) -> SandboxStatus:
    return SandboxStatus(backend=BACKEND_NAME, state=SandboxState.UNAVAILABLE, detail=detail)


def _clip(text: object, limit: int = 400) -> str:
    return str(text)[:limit]
