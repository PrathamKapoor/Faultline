"""The Docker execution backend: command construction, supervision, and cleanup.

This is the only code in EdgeHunter that may start a process on the host, and the
only thing it ever runs is the ``docker`` CLI. There is deliberately **no**
fallback: if Docker is missing, the daemon is unreachable, or the image cannot be
used, the result is ``SANDBOX_UNAVAILABLE`` and no target code runs anywhere.

Every security-relevant argument is a literal in this module or a value EdgeHunter
generated. Nothing from the scanned repository is ever interpolated into a
command, and commands are executed as an argument list with ``shell=False``, so
no shell is involved and no repository string can become a flag.

The container is hardened before it runs: no network, read-only root filesystem,
all capabilities dropped, no privilege escalation, bounded processes, memory and
CPU, a non-root user, and the staged source mounted read-only. Docker's default
seccomp profile is retained; nothing here weakens it.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

DEFAULT_TIMEOUT_SECONDS: Final[float] = 3.0
MIN_TIMEOUT_SECONDS: Final[float] = 0.5
MAX_TIMEOUT_SECONDS: Final[float] = 60.0

MEMORY_LIMIT: Final[str] = "256m"
CPU_LIMIT: Final[str] = "1.0"
PIDS_LIMIT: Final[str] = "64"
CONTAINER_USER: Final[str] = "65534:65534"
TMPFS_SPEC: Final[str] = "/tmp:rw,nosuid,nodev,size=16m"

WORKSPACE_MOUNT: Final[str] = "/workspace"
INPUT_MOUNT: Final[str] = "/input"
OUTPUT_MOUNT: Final[str] = "/output"

DOCKER_BINARY: Final[str] = "docker"

# Prohibited flags, asserted absent by the security tests. They are listed here so
# the test and the documentation cannot drift from the builder.
PROHIBITED_ARGUMENTS: Final[tuple[str, ...]] = (
    "--privileged",
    "--pid=host",
    "--ipc=host",
    "--network=host",
    "seccomp=unconfined",
    "--userns=host",
)

# Host memory spent holding Docker CLI output. The target never writes to the
# container's stdout or stderr, so this only covers Docker's own diagnostics and
# build logs, but it is still bounded explicitly rather than by luck.
MAX_CAPTURED_OUTPUT_BYTES: Final[int] = 64 * 1024


class DockerUnavailable(Exception):
    """Docker could not be used, so no target code may be executed."""


@dataclass(frozen=True, slots=True)
class DockerPaths:
    """Host directories the container needs, all created by EdgeHunter."""

    workspace: Path
    request: Path
    output: Path

    def input_mount(self) -> Path:
        """Directory mounted read-only at ``/input``."""
        return self.request.parent

    def output_mount(self) -> Path:
        """Directory mounted at ``/output``; contains only the result file."""
        return self.output.parent


@dataclass(frozen=True, slots=True)
class ProcessOutcome:
    """What one supervised Docker CLI invocation produced."""

    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False


def new_execution_id() -> str:
    """A unique, unguessable identifier for one execution."""
    return uuid.uuid4().hex


def clamp_timeout(value: float) -> float:
    """Clamp a caller-supplied timeout into the supported range."""
    return max(MIN_TIMEOUT_SECONDS, min(MAX_TIMEOUT_SECONDS, value))


def build_run_command(
    *,
    container_name: str,
    image: str,
    paths: DockerPaths,
) -> tuple[str, ...]:
    """Build the hardened ``docker run`` argument list.

    The staged workspace is mounted read-only; only the dedicated result
    directory is writable, and it contains nothing but the result file.
    """
    return (
        DOCKER_BINARY,
        "run",
        "--rm",
        "--name",
        container_name,
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        PIDS_LIMIT,
        "--memory",
        MEMORY_LIMIT,
        "--cpus",
        CPU_LIMIT,
        "--user",
        CONTAINER_USER,
        "--tmpfs",
        TMPFS_SPEC,
        "--mount",
        f"type=bind,source={paths.workspace},target={WORKSPACE_MOUNT},readonly",
        "--mount",
        f"type=bind,source={paths.input_mount()},target={INPUT_MOUNT},readonly",
        "--mount",
        f"type=bind,source={paths.output_mount()},target={OUTPUT_MOUNT}",
        image,
    )


def build_build_command(*, context: Path, tag: str, dockerfile: Path) -> tuple[str, ...]:
    """Build the trusted worker image from EdgeHunter's own sources."""
    return (
        DOCKER_BINARY,
        "build",
        "--tag",
        tag,
        "--file",
        str(dockerfile),
        str(context),
    )


def docker_available() -> tuple[bool, str]:
    """Report whether a usable Docker backend is present, without running target code."""
    if shutil.which(DOCKER_BINARY) is None:
        return False, "the docker CLI was not found on PATH"
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argument list, shell=False
            [DOCKER_BINARY, "version", "--format", "{{.Server.Os}}"],
            capture_output=True,
            text=True,
            timeout=MIN_TIMEOUT_SECONDS * 10,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"the docker daemon is not reachable: {exc}"
    if completed.returncode != 0:
        return False, "the docker daemon is not reachable"
    operating_system = completed.stdout.strip().lower()
    if operating_system != "linux":
        return False, f"a Linux container backend is required, daemon reports {operating_system!r}"
    return True, "Docker Linux container backend available"


def run_docker(
    command: Sequence[str],
    *,
    timeout_seconds: float,
    cleanup_command: Sequence[str] | None = None,
) -> ProcessOutcome:
    """Run one Docker CLI command with a host-owned deadline.

    The command is always an argument list executed without a shell. Output is
    captured through temporary files and read back under
    ``MAX_CAPTURED_OUTPUT_BYTES`` rather than through ``capture_output=True``,
    because an in-memory pipe would let a runaway process grow host memory
    without limit. On timeout the process is killed and, if a cleanup command was
    supplied, it is still run so that a started container is removed.
    """
    started = time.monotonic()
    try:
        returncode, stdout, stderr = _run_bounded(list(command), timeout_seconds=timeout_seconds)
    except subprocess.TimeoutExpired as expired:
        _terminate(cleanup_command)
        return ProcessOutcome(
            returncode=-1,
            stdout=_as_text(expired.stdout),
            stderr=_as_text(expired.stderr),
            duration_seconds=round(time.monotonic() - started, 6),
            timed_out=True,
        )
    except OSError as exc:
        return ProcessOutcome(
            returncode=-1,
            stdout="",
            stderr=str(exc),
            duration_seconds=round(time.monotonic() - started, 6),
        )

    if returncode != 0 and cleanup_command is not None:
        # A non-zero exit can still leave a container behind.
        _terminate(cleanup_command)
    return ProcessOutcome(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=round(time.monotonic() - started, 6),
    )


def _run_bounded(command: list[str], *, timeout_seconds: float) -> tuple[int, str, str]:
    """Run ``command`` with file-backed, size-bounded output capture."""
    with tempfile.TemporaryDirectory(prefix="edgehunter-capture-") as directory:
        out_path = Path(directory) / "stdout"
        err_path = Path(directory) / "stderr"
        with (
            out_path.open("wb") as out_file,
            err_path.open("wb") as err_file,
        ):
            completed = subprocess.run(  # noqa: S603 - fixed argument list, shell=False
                command,
                stdout=out_file,
                stderr=err_file,
                stdin=subprocess.DEVNULL,
                timeout=timeout_seconds,
                check=False,
                shell=False,
            )
        return (
            completed.returncode,
            _read_bounded(out_path),
            _read_bounded(err_path),
        )


def _read_bounded(path: Path) -> str:
    """Read at most ``MAX_CAPTURED_OUTPUT_BYTES`` from ``path``, decoding safely."""
    with path.open("rb") as handle:
        raw = handle.read(MAX_CAPTURED_OUTPUT_BYTES)
    return raw.decode("utf-8", errors="replace")


def remove_container(container_name: str) -> ProcessOutcome:
    """Force-remove a container, used for cleanup and verification."""
    return run_docker(
        (DOCKER_BINARY, "rm", "--force", container_name),
        timeout_seconds=clamp_timeout(DEFAULT_TIMEOUT_SECONDS),
    )


def container_exists(container_name: str) -> bool:
    """True when a container with this name still exists on the host."""
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argument list, shell=False
            [
                DOCKER_BINARY,
                "ps",
                "--all",
                "--filter",
                f"name=^/{container_name}$",
                "--format",
                "{{.Names}}",
            ],
            capture_output=True,
            text=True,
            timeout=clamp_timeout(DEFAULT_TIMEOUT_SECONDS),
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return container_name in completed.stdout


def _terminate(cleanup_command: Sequence[str] | None) -> None:
    """Run a cleanup command, ignoring its outcome because we are already failing."""
    if cleanup_command is None:
        return
    try:
        subprocess.run(  # noqa: S603 - fixed argument list, shell=False
            list(cleanup_command),
            capture_output=True,
            text=True,
            timeout=clamp_timeout(DEFAULT_TIMEOUT_SECONDS),
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return


def _as_text(raw: object) -> str:
    """Coerce captured process output to text regardless of bytes/str capture."""
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)
