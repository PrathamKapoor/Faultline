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
    assert "--actions" in result.output
    assert "--candidates" in result.output
    assert "--annotations" in result.output
    assert "--plans" in result.output
    assert "--strategies" in result.output


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
        "--actions",
        "--candidates",
        "--annotations",
        "--plans",
        "--strategies",
    )

    assert result.exit_code == 0
    assert "Targets" in result.output
    assert "Boundaries" in result.output
    assert "Structural risk" in result.output
    assert "Properties" in result.output
    assert "Actions" in result.output
    assert "Candidates" in result.output
    assert "Annotations" in result.output
    assert "Invocation plans" in result.output
    assert "Strategy plans" in result.output


def test_scan_reports_planning_statistics(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(
        "def f(x: int, name: str, user: Widget):\n"
        "    if x > 0:\n"
        "        return x, name, user\n"
        "    return None\n",
        encoding="utf-8",
    )

    result = invoke("scan", str(tmp_path))

    assert result.exit_code == 0
    assert "Planning" in result.output
    assert stat_value(result.output, "Parameters recognized") == 2
    assert stat_value(result.output, "Parameters unknown") == 1
    assert stat_value(result.output, "Invocation plans") > 0
    # Every plan carries the unknown argument, so none is complete.
    assert stat_value(result.output, "Complete function plans") == 0
    assert stat_value(result.output, "Partial plans") > 0
    assert stat_value(result.output, "Strategy plans") == 1


def test_scan_counts_complete_function_plans(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(
        "def f(x: int, name: str):\n    if x > 0:\n        return x, name\n    return None\n",
        encoding="utf-8",
    )

    result = invoke("scan", str(tmp_path))

    assert result.exit_code == 0
    assert stat_value(result.output, "Complete function plans") > 0
    assert stat_value(result.output, "Partial plans") == 0
    assert stat_value(result.output, "Needs receiver") == 0


def test_scan_lists_annotation_shapes_with_flag(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(
        "def f(tags: list[int], limit: int | None, user: Widget):\n    return tags, limit, user\n",
        encoding="utf-8",
    )

    result = invoke("scan", str(tmp_path), "--annotations")

    assert result.exit_code == 0
    assert "not type resolution" in result.output
    assert "tags: list[int] -> LIST[INT]" in result.output
    assert "limit: int | None -> OPTIONAL[INT]" in result.output
    assert "user: Widget -> UNKNOWN" in result.output


def test_scan_lists_invocation_plans_with_flag(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(
        "def f(x: int, name: str):\n    if x > 0:\n        return x, name\n    return None\n",
        encoding="utf-8",
    )

    result = invoke("scan", str(tmp_path), "--plans")

    assert result.exit_code == 0
    assert "One adversarial action per plan" in result.output
    assert "x <- set_zero" in result.output
    assert "[adversarial_action" in result.output
    assert "[static_type_baseline" in result.output
    assert "status: complete_function" in result.output


def test_scan_shows_missing_arguments_and_unresolved_values(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(
        "def f(x: int, user: Widget):\n    if x > 0:\n        return x, user\n    return None\n",
        encoding="utf-8",
    )

    result = invoke("scan", str(tmp_path), "--plans")

    assert result.exit_code == 0
    assert "missing: user" in result.output
    assert "status: partial_arguments" in result.output


def test_scan_marks_declared_defaults_and_receivers(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(
        "class C:\n"
        "    def f(self, x: int, limit: int = 10):\n"
        "        if x > 0:\n"
        "            return x, limit\n"
        "        return limit\n",
        encoding="utf-8",
    )

    result = invoke("scan", str(tmp_path), "--plans")

    assert result.exit_code == 0
    assert "(declared default)" in result.output
    assert "[declared_default, omitted]" in result.output
    assert "receiver: self (not constructed)" in result.output
    assert "status: needs_receiver" in result.output


def test_scan_lists_strategy_plans_with_flag(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(
        "def f(x: int, r: float):\n    if x < 10:\n        return x + r\n    return r\n",
        encoding="utf-8",
    )

    result = invoke("scan", str(tmp_path), "--strategies")

    assert result.exit_code == 0
    assert "not generated samples" in result.output
    assert "x: INT  [sampled_from]" in result.output
    assert "r: FLOAT  [sampled_from]" in result.output
    assert "just_above.10" in result.output
    assert 'float("nan")' in result.output


def test_scan_without_phase5_flags_omits_their_sections(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("def f(x: int):\n    return x\n", encoding="utf-8")

    result = invoke("scan", str(tmp_path))

    assert result.exit_code == 0
    assert "\nAnnotations\n" not in result.output
    assert "\nInvocation plans\n" not in result.output
    assert "\nStrategy plans\n" not in result.output


def test_scan_reports_empty_phase5_sections(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")

    result = invoke("scan", str(tmp_path), "--annotations", "--plans", "--strategies")

    assert result.exit_code == 0
    assert "(no callable targets)" in result.output
    assert "(no invocation plans)" in result.output
    assert "(no strategy plans)" in result.output


def test_scan_reports_semantic_action_statistics(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(
        "def f(x: int, items: list):\n    if x > 0:\n        return items\n    return []\n",
        encoding="utf-8",
    )

    result = invoke("scan", str(tmp_path))

    assert result.exit_code == 0
    assert "Semantic actions" in result.output
    assert stat_value(result.output, "Actions proposed") == 9
    assert stat_value(result.output, "Concrete values") == 7
    assert stat_value(result.output, "Symbolic values") == 0
    assert stat_value(result.output, "Unsupported actions") == 2
    assert stat_value(result.output, "Targets with actions") == 1
    assert stat_value(result.output, "Candidate cases") == 7


def test_scan_lists_actions_with_flag(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(
        "def f(x: int):\n    if x > 5:\n        return x\n    return 0\n", encoding="utf-8"
    )

    result = invoke("scan", str(tmp_path), "--actions")

    assert result.exit_code == 0
    assert "Actions" in result.output
    assert "Nothing here is executed" in result.output
    assert "[boundary]" in result.output
    assert "just_below_boundary 5" in result.output
    assert "4" in result.output


def test_scan_lists_collection_actions_from_annotations(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("def f(items: list[int]):\n    return items\n", encoding="utf-8")

    result = invoke("scan", str(tmp_path), "--actions")

    assert result.exit_code == 0
    assert "[annotation]" in result.output
    assert "[]" in result.output
    assert "[0]" in result.output
    assert "[0, 0]" in result.output


def test_scan_never_fabricates_a_none_placeholder(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("def f(items: list):\n    return items\n", encoding="utf-8")

    result = invoke("scan", str(tmp_path), "--actions")

    assert result.exit_code == 0
    assert "[]" in result.output
    assert "[None]" not in result.output
    assert "not treated as nullable" in result.output


def test_scan_lists_candidates_with_flag(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(
        "def f(x: int, rate: float = 0.1):\n    if x > 0:\n        return rate\n    return rate\n",
        encoding="utf-8",
    )

    result = invoke("scan", str(tmp_path), "--candidates")

    assert result.exit_code == 0
    assert "Candidates" in result.output
    assert "never invented" in result.output
    assert "[complete]" in result.output
    assert "x = 1" in result.output
    assert "missing: x" in result.output


def test_scan_shows_symbolic_values_in_angle_brackets(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(
        "def f(x: float, limit: float):\n    if x < limit:\n        return limit\n    return x\n",
        encoding="utf-8",
    )

    result = invoke("scan", str(tmp_path), "--candidates")

    assert result.exit_code == 0
    assert "[symbolic]" in result.output
    assert "<limit>" in result.output


def test_scan_reports_unsupported_actions_with_reason(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(
        "def f(z: complex):\n    if z > 1j:\n        return z\n    return 0j\n", encoding="utf-8"
    )

    result = invoke("scan", str(tmp_path), "--actions")

    assert result.exit_code == 0
    assert "unsupported" in result.output
    assert "no neighbour of a complex literal" in result.output
    assert stat_value(result.output, "Unsupported actions") == 2


def test_scan_without_action_flags_omits_their_sections(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("def f(x: int):\n    return x\n", encoding="utf-8")

    result = invoke("scan", str(tmp_path))

    assert result.exit_code == 0
    assert "\nActions\n" not in result.output
    assert "\nCandidates\n" not in result.output


def test_scan_reports_no_actions_for_a_repository_without_evidence(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("def f(x):\n    return x\n", encoding="utf-8")

    result = invoke("scan", str(tmp_path), "--actions", "--candidates")

    assert result.exit_code == 0
    assert "(no applicable semantic actions)" in result.output
    assert "(no candidate cases)" in result.output


def test_section_order_places_issues_last(tmp_path: Path) -> None:
    (tmp_path / "broken.py").write_text("def broken(\n", encoding="utf-8")
    (tmp_path / "m.py").write_text("def f(x: int):\n    return x\n", encoding="utf-8")

    result = invoke("scan", str(tmp_path), "--boundaries", "--actions", "--candidates")

    assert result.exit_code == 1
    assert result.output.index("Semantic actions") < result.output.index("\nActions\n")
    assert result.output.index("\nActions\n") < result.output.index("\nCandidates\n")
    assert result.output.index("\nCandidates\n") < result.output.index("\nSyntax issues\n")


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
    # Match the section header, not the word: the summary block legitimately
    # contains labels such as "Targets with actions".
    assert "\nTargets\n" not in result.output


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
