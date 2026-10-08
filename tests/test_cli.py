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
    assert "--boundaries" in result.output
    assert "--risk" in result.output
    assert "--properties" in result.output


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


def test_scan_reports_semantic_statistics(tmp_path: Path) -> None:
    (tmp_path / "bank.py").write_text(
        "def withdraw(balance: float, amount: float) -> float:\n"
        "    if amount <= balance:\n"
        "        return balance - amount\n"
        "    return balance\n",
        encoding="utf-8",
    )

    result = invoke("scan", str(tmp_path))

    assert result.exit_code == 0
    assert stat_value(result.output, "Predicates") == 1
    assert stat_value(result.output, "Comparisons") == 1
    assert stat_value(result.output, "Arithmetic expressions") == 1
    assert stat_value(result.output, "Boundary candidates") == 17


def test_scan_lists_boundaries_with_flag(tmp_path: Path) -> None:
    (tmp_path / "bank.py").write_text(
        "def withdraw(balance: float, amount: float) -> float:\n"
        "    if amount <= balance:\n"
        "        return balance - amount\n"
        "    return balance\n",
        encoding="utf-8",
    )

    result = invoke("scan", str(tmp_path), "--boundaries")

    assert result.exit_code == 0
    assert "Boundaries" in result.output
    assert "bank.py:1" in result.output
    assert "withdraw(balance: float, amount: float) -> float" in result.output
    assert "[comparison] just below balance" in result.output
    assert "[comparison] at balance" in result.output
    assert "[comparison] just above balance" in result.output
    assert "[numeric] 0" in result.output
    assert "[numeric] NaN" in result.output
    assert "[numeric] +Infinity" in result.output


def test_scan_without_boundaries_flag_omits_listing(tmp_path: Path) -> None:
    (tmp_path / "bank.py").write_text(
        "def withdraw(balance: float, amount: float) -> float:\n"
        "    if amount <= balance:\n"
        "        return balance - amount\n",
        encoding="utf-8",
    )

    result = invoke("scan", str(tmp_path))

    assert result.exit_code == 0
    assert "Boundaries" not in result.output


def test_scan_boundaries_reports_when_no_candidates_exist(tmp_path: Path) -> None:
    (tmp_path / "plain.py").write_text("def f(x):\n    return x\n", encoding="utf-8")

    result = invoke("scan", str(tmp_path), "--boundaries")

    assert result.exit_code == 0
    assert "(no boundary candidates)" in result.output


def test_scan_reports_oracle_statistics(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(
        "def f(x):\n    if x < 0:\n        raise ValueError\n    assert x >= 0\n    return x\n",
        encoding="utf-8",
    )
    (tmp_path / "test_m.py").write_text(
        "def test_f():\n    assert f(1) == 1\n",
        encoding="utf-8",
    )

    result = invoke("scan", str(tmp_path))

    assert result.exit_code == 0
    assert stat_value(result.output, "Assertions") == 1
    assert stat_value(result.output, "Validation rules") == 1
    assert stat_value(result.output, "Resolved test expectations") == 1
    assert stat_value(result.output, "Unresolved test expectations") == 0
    assert stat_value(result.output, "Ambiguous test expectations") == 0
    assert stat_value(result.output, "Property candidates") == 3
    assert stat_value(result.output, "Strongly supported targets") == 1
    assert stat_value(result.output, "Risk-scored targets") == 2


def test_scan_lists_risk_with_flag(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(
        "def risky(a: int, b: int):\n    if a > 0:\n        return a * b\n", encoding="utf-8"
    )

    result = invoke("scan", str(tmp_path), "--risk")

    assert result.exit_code == 0
    assert "Structural risk" in result.output
    assert "not a probability of a bug" in result.output
    assert "risky" in result.output
    assert "m.py:1" in result.output
    assert "predicates" in result.output
    assert "+6" in result.output


def test_scan_without_risk_flag_omits_risk_section(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("def f(x):\n    ...\n", encoding="utf-8")

    result = invoke("scan", str(tmp_path))

    assert result.exit_code == 0
    assert "Structural risk" not in result.output


def test_scan_lists_properties_with_flag(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(
        "def f(x):\n"
        "    if x < 0:\n"
        "        raise ValueError('negative')\n"
        "    assert x >= 0, 'must be non-negative'\n"
        "    return x\n",
        encoding="utf-8",
    )

    result = invoke("scan", str(tmp_path), "--properties")

    assert result.exit_code == 0
    assert "Properties" in result.output
    assert "not confirmed defects" in result.output
    assert "[STRONG] asserted_condition" in result.output
    assert "x >= 0" in result.output
    assert "must be non-negative" in result.output
    assert "[STRONG] rejection_rule" in result.output
    assert "raises ValueError" in result.output
    assert "Oracle support by target" in result.output


def test_scan_properties_shows_unresolved_expectation_reason(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("def other():\n    ...\n", encoding="utf-8")
    (tmp_path / "test_m.py").write_text(
        "def test_other():\n    assert service.other() == 1\n", encoding="utf-8"
    )

    result = invoke("scan", str(tmp_path), "--properties")

    assert result.exit_code == 0
    assert "[unresolved]" in result.output
    assert "receiver resolution not implemented" in result.output


def test_scan_properties_report_empty_sections(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("def f(x):\n    return x\n", encoding="utf-8")

    result = invoke("scan", str(tmp_path), "--properties")

    assert result.exit_code == 0
    assert "(no production assertions or validation guards)" in result.output
    assert "(no test expectations recognized)" in result.output
    assert "(no target has explicit oracle evidence)" in result.output


def test_scan_combines_detail_flags(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(
        "def f(x: int):\n    assert x >= 0\n    return x * 2\n", encoding="utf-8"
    )

    result = invoke(
        "scan",
        str(tmp_path),
        "--targets",
        "--boundaries",
        "--risk",
        "--properties",
    )

    assert result.exit_code == 0
    assert "Targets" in result.output
    assert "Boundaries" in result.output
    assert "Structural risk" in result.output
    assert "Properties" in result.output


def test_oracle_ambiguity_does_not_change_exit_code(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def parse(v):\n    ...\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def parse(v):\n    ...\n", encoding="utf-8")
    (tmp_path / "test_parse.py").write_text(
        "def test_parse():\n    assert parse('x') is None\n", encoding="utf-8"
    )

    result = invoke("scan", str(tmp_path))

    assert result.exit_code == 0
    assert stat_value(result.output, "Ambiguous test expectations") == 1


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
