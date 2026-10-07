"""Tests for the EdgeHunter command-line interface."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner, Result

from edgehunter import __version__
from edgehunter.cli import app

runner = CliRunner()


def invoke(*args: str) -> Result:
    return runner.invoke(app, list(args))


def test_help_exits_successfully_and_lists_scan() -> None:
    result = invoke("--help")

    assert result.exit_code == 0
    assert "scan" in result.output


def test_version_flag_reports_package_version() -> None:
    result = invoke("--version")

    assert result.exit_code == 0
    assert result.output.strip() == f"edgehunter {__version__}"


def test_scan_help_exits_successfully() -> None:
    result = invoke("scan", "--help")

    assert result.exit_code == 0
    assert "PATH" in result.output


def test_scan_missing_argument_is_a_usage_error() -> None:
    result = invoke("scan")

    assert result.exit_code == 2
    assert "Missing argument" in result.output


def test_scan_directory_succeeds_and_reports_counts(tmp_path: Path) -> None:
    (tmp_path / "module.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("hello\n", encoding="utf-8")

    result = invoke("scan", str(tmp_path))

    assert result.exit_code == 0
    assert "python files discovered: 1" in result.output
    assert "not implemented" in result.output


def test_scan_single_python_file_succeeds(tmp_path: Path) -> None:
    target = tmp_path / "solo.py"
    target.write_text("x = 1\n", encoding="utf-8")

    result = invoke("scan", str(target))

    assert result.exit_code == 0
    assert "target (file)" in result.output
    assert "python files discovered: 1" in result.output


def test_scan_missing_path_fails_cleanly(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"

    result = invoke("scan", str(missing))

    assert result.exit_code == 2
    assert "path does not exist" in result.stderr
    assert "Traceback" not in result.output


def test_scan_non_python_file_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "data.txt"
    target.write_text("hello\n", encoding="utf-8")

    result = invoke("scan", str(target))

    assert result.exit_code == 2
    assert "not a Python source file" in result.stderr
    assert "Traceback" not in result.output


def test_python_dash_m_entry_point_reports_version() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "edgehunter", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == f"edgehunter {__version__}"
