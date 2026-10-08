"""Tests for the Docker command's security arguments and failure handling.

These assert the *constructed command*, not the presence of a string in the
source, and every mocked failure must produce a truthful result with no host
execution fallback.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import IO, Any, cast

import pytest

from edgehunter.execution.docker import (
    CONTAINER_USER,
    MAX_CAPTURED_OUTPUT_BYTES,
    MAX_TIMEOUT_SECONDS,
    MIN_TIMEOUT_SECONDS,
    PROHIBITED_ARGUMENTS,
    DockerPaths,
    build_run_command,
    clamp_timeout,
    container_exists,
    docker_available,
    remove_container,
    run_docker,
)


def _paths(tmp_path: Path) -> DockerPaths:
    workspace = tmp_path / "workspace"
    request_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    workspace.mkdir()
    request_dir.mkdir()
    output_dir.mkdir()
    return DockerPaths(
        workspace=workspace,
        request=request_dir / "request.json",
        output=output_dir / "result.json",
    )


def _joined(command: tuple[str, ...]) -> str:
    return " ".join(command)


def test_command_uses_an_argument_list_without_a_shell(tmp_path: Path) -> None:
    command = build_run_command(
        container_name="edgehunter-run-test", image="img:1", paths=_paths(tmp_path)
    )

    assert isinstance(command, tuple)
    assert command[0] == "docker"
    assert command[1] == "run"
    assert all(isinstance(part, str) for part in command)
    assert "sh -c" not in _joined(command)


@pytest.mark.parametrize(
    "required",
    [
        "--network none",
        "--read-only",
        "--cap-drop ALL",
        "--security-opt no-new-privileges",
        "--pids-limit 64",
        "--memory 256m",
        "--cpus 1.0",
        f"--user {CONTAINER_USER}",
    ],
)
def test_required_hardening_is_present(tmp_path: Path, required: str) -> None:
    command = build_run_command(
        container_name="edgehunter-run-test", image="img:1", paths=_paths(tmp_path)
    )

    assert required in _joined(command)


@pytest.mark.parametrize("prohibited", PROHIBITED_ARGUMENTS)
def test_prohibited_arguments_are_absent(tmp_path: Path, prohibited: str) -> None:
    command = build_run_command(
        container_name="edgehunter-run-test", image="img:1", paths=_paths(tmp_path)
    )

    assert prohibited not in _joined(command)


def test_workspace_is_mounted_read_only(tmp_path: Path) -> None:
    paths = _paths(tmp_path)

    command = build_run_command(container_name="edgehunter-run-test", image="img:1", paths=paths)
    joined = _joined(command)

    assert f"source={paths.workspace},target=/workspace,readonly" in joined


def test_docker_socket_is_never_mounted(tmp_path: Path) -> None:
    paths = _paths(tmp_path)

    command = build_run_command(container_name="edgehunter-run-test", image="img:1", paths=paths)
    joined = _joined(command)

    assert "docker.sock" not in joined
    assert "/var/run" not in joined


def test_no_host_environment_is_forwarded(tmp_path: Path) -> None:
    command = build_run_command(
        container_name="edgehunter-run-test", image="img:1", paths=_paths(tmp_path)
    )

    assert "--env" not in command
    assert "-e" not in command


def test_timeout_is_clamped() -> None:
    assert clamp_timeout(0.01) == MIN_TIMEOUT_SECONDS
    assert clamp_timeout(10_000) == MAX_TIMEOUT_SECONDS
    assert clamp_timeout(5.0) == 5.0


def _fake_completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> Any:
    return subprocess.CompletedProcess(
        args=["docker"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _file_capture(
    returncode: int, *, stdout: bytes = b"", stderr: bytes = b"", log: list[str] | None = None
) -> Any:
    """A ``subprocess.run`` stand-in matching the file-backed capture contract."""

    def run(*args: object, **kwargs: object) -> Any:
        argv = [str(part) for part in _as_sequence(args[0])]
        if log is not None:
            log.append(" ".join(argv))
        for key, payload in (("stdout", stdout), ("stderr", stderr)):
            handle = kwargs.get(key)
            if handle is not None and payload:
                cast("IO[bytes]", handle).write(payload)
        return subprocess.CompletedProcess(args=argv, returncode=returncode)

    return run


def _as_sequence(value: object) -> Sequence[object]:
    """Narrow a command argument for the mocked runner."""
    assert isinstance(value, Sequence)
    return value


def test_missing_docker_cli_is_reported_as_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("edgehunter.execution.docker.shutil.which", lambda name: None)

    available, detail = docker_available()

    assert available is False
    assert "docker CLI" in detail


def test_unreachable_daemon_is_reported_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("edgehunter.execution.docker.shutil.which", lambda name: "docker")

    def exploding(*args: object, **kwargs: object) -> Any:
        raise OSError("daemon not running")

    monkeypatch.setattr(subprocess, "run", exploding)

    available, detail = docker_available()

    assert available is False
    assert "not reachable" in detail


def test_non_linux_daemon_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("edgehunter.execution.docker.shutil.which", lambda name: "docker")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _fake_completed(stdout="windows\n"))

    available, detail = docker_available()

    assert available is False
    assert "Linux" in detail


def test_linux_daemon_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("edgehunter.execution.docker.shutil.which", lambda name: "docker")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _fake_completed(stdout="linux\n"))

    available, _ = docker_available()

    assert available is True


def test_timeout_runs_cleanup_and_reports_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def timing_out(*args: object, **kwargs: object) -> Any:
        seen["timeout"] = kwargs.get("timeout")
        raise subprocess.TimeoutExpired(cmd=["docker"], timeout=2.0)

    def cleaning(*args: object, **kwargs: object) -> Any:
        seen["cleanup"] = list(_as_sequence(args[0])) if args else None
        return _fake_completed()

    calls: list[Any] = [timing_out, cleaning]
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.pop(0)(*a, **k))

    outcome = run_docker(
        ("docker", "run", "img"), timeout_seconds=2.0, cleanup_command=("docker", "rm", "-f", "c")
    )

    assert outcome.timed_out is True
    assert outcome.returncode == -1
    assert seen["cleanup"] == ["docker", "rm", "-f", "c"]


def test_non_zero_exit_also_triggers_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    actions = [
        _file_capture(125, stderr=b"boom", log=calls),
        _file_capture(0, log=calls),
    ]
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: actions.pop(0)(*a, **k))

    outcome = run_docker(
        ("docker", "run", "img"), timeout_seconds=1.0, cleanup_command=("docker", "rm", "-f", "c")
    )

    assert outcome.returncode == 125
    assert "boom" in outcome.stderr
    assert calls == ["docker run img", "docker rm -f c"]


def test_missing_docker_binary_during_run_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    def exploding(*args: object, **kwargs: object) -> Any:
        raise FileNotFoundError("docker")

    monkeypatch.setattr(subprocess, "run", exploding)

    outcome = run_docker(("docker", "run", "img"), timeout_seconds=1.0)

    assert outcome.returncode == -1
    assert "docker" in outcome.stderr


def test_cleanup_failure_does_not_hide_the_original_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def exploding(*args: object, **kwargs: object) -> Any:
        raise OSError("cleanup also failed")

    actions = [_file_capture(1, stderr=b"original failure"), exploding]
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: actions.pop(0)(*a, **k))

    outcome = run_docker(
        ("docker", "run", "img"), timeout_seconds=1.0, cleanup_command=("docker", "rm", "-f", "c")
    )

    assert outcome.returncode == 1
    assert outcome.stderr == "original failure"


def test_captured_output_is_bounded_even_for_a_flood(tmp_path: Path) -> None:
    """A noisy process must not be able to grow host memory without limit."""
    flood = sys.executable
    script = "import sys\nsys.stdout.write('o' * 500000)\nsys.stderr.write('e' * 500000)\n"

    outcome = run_docker((flood, "-c", script), timeout_seconds=60.0)

    assert outcome.returncode == 0
    assert len(outcome.stdout) <= MAX_CAPTURED_OUTPUT_BYTES
    assert len(outcome.stderr) <= MAX_CAPTURED_OUTPUT_BYTES
    assert outcome.stdout == "o" * MAX_CAPTURED_OUTPUT_BYTES


def test_container_existence_check_is_scoped_by_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def capturing(*args: object, **kwargs: object) -> Any:
        captured["command"] = list(_as_sequence(args[0]))
        return _fake_completed(stdout="edgehunter-run-abc\n")

    monkeypatch.setattr(subprocess, "run", capturing)

    assert container_exists("edgehunter-run-abc") is True
    command = captured["command"]
    assert "ps" in command
    assert "--all" in command
    assert "name=^/edgehunter-run-abc$" in command


def test_container_existence_is_false_when_docker_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def exploding(*args: object, **kwargs: object) -> Any:
        raise OSError("no daemon")

    monkeypatch.setattr(subprocess, "run", exploding)

    assert container_exists("edgehunter-run-abc") is False


def test_remove_container_uses_force(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def capturing(*args: object, **kwargs: object) -> Any:
        captured["command"] = list(_as_sequence(args[0]))
        captured["shell"] = kwargs.get("shell")
        return _fake_completed()

    monkeypatch.setattr(subprocess, "run", capturing)

    remove_container("edgehunter-run-abc")

    assert captured["command"][:4] == ["docker", "rm", "--force", "edgehunter-run-abc"]
    assert captured["shell"] is False
