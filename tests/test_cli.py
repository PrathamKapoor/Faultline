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


def stat_value(output: str, label: str) -> int:
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{label}:"):
            return int(stripped.split(":", 1)[1].strip())
    raise AssertionError(f"stat not found in output: {label}")


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
    assert "--targets" in result.output


def test_scan_missing_argument_is_a_usage_error() -> None:
    result = invoke("scan")

    assert result.exit_code == 2
    assert "Missing argument" in result.output


def test_scan_directory_succeeds_and_reports_counts(tmp_path: Path) -> None:
    (tmp_path / "module.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("hello\n", encoding="utf-8")

    result = invoke("scan", str(tmp_path))

    assert result.exit_code == 0
    assert stat_value(result.output, "Python files discovered") == 1
    assert stat_value(result.output, "Parsed successfully") == 1
    assert "not implemented" not in result.output


def test_scan_single_python_file_succeeds(tmp_path: Path) -> None:
    target = tmp_path / "solo.py"
    target.write_text("x = 1\n", encoding="utf-8")

    result = invoke("scan", str(target))

    assert result.exit_code == 0
    assert "target (file)" in result.output
    assert stat_value(result.output, "Python files discovered") == 1


def test_scan_reports_ast_statistics(tmp_path: Path) -> None:
    (tmp_path / "shapes.py").write_text(
        "def plain():\n"
        "    ...\n"
        "\n"
        "class Widget:\n"
        "    def render(self):\n"
        "        ...\n"
        "    async def load(self):\n"
        "        ...\n",
        encoding="utf-8",
    )

    result = invoke("scan", str(tmp_path))

    assert result.exit_code == 0
    assert stat_value(result.output, "Classes") == 1
    assert stat_value(result.output, "Functions") == 1
    assert stat_value(result.output, "Methods") == 2
    assert stat_value(result.output, "Async targets") == 1
    assert stat_value(result.output, "Callable targets") == 3


def test_scan_lists_targets_with_flag(tmp_path: Path) -> None:
    (tmp_path / "account.py").write_text(
        "class Account:\n    def withdraw(self, amount: float) -> float:\n        return 0.0\n",
        encoding="utf-8",
    )

    result = invoke("scan", str(tmp_path), "--targets")

    assert result.exit_code == 0
    assert "Targets" in result.output
    assert "account.py:2" in result.output
    assert "Account.withdraw(self, amount: float) -> float" in result.output


def test_scan_without_targets_flag_omits_listing(tmp_path: Path) -> None:
    (tmp_path / "module.py").write_text("def f():\n    ...\n", encoding="utf-8")

    result = invoke("scan", str(tmp_path))

    assert result.exit_code == 0
    assert "Targets" not in result.output


def test_scan_syntax_issue_exits_with_code_1(tmp_path: Path) -> None:
    (tmp_path / "broken.py").write_text("def broken(\n", encoding="utf-8")

    result = invoke("scan", str(tmp_path))

    assert result.exit_code == 1
    assert "Syntax issues" in result.output
    assert "broken.py:1" in result.output
    assert "never closed" in result.output
    assert "Traceback" not in result.output


def test_scan_read_issue_exits_with_code_1(tmp_path: Path) -> None:
    (tmp_path / "latin.py").write_bytes(b"# -*- coding: ascii -*-\nname = 'caf\xe9'\n")

    result = invoke("scan", str(tmp_path))

    assert result.exit_code == 1
    assert "Read issues" in result.output
    assert "latin.py" in result.output
    assert "Traceback" not in result.output


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
