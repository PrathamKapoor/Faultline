"""Tests for repository-level AST analysis aggregation."""

from __future__ import annotations

from pathlib import Path

from edgehunter.scanner import analyze_repository


def test_multiple_files_are_aggregated(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(
        "def free():\n    ...\n\nclass Widget:\n    def render(self):\n        ...\n",
        encoding="utf-8",
    )
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.py").write_text("async def fetch():\n    ...\n", encoding="utf-8")

    analysis = analyze_repository(tmp_path)

    assert analysis.python_file_count == 2
    assert analysis.parsed_file_count == 2
    assert analysis.issue_count == 0
    assert analysis.class_count == 1
    assert analysis.function_count == 2
    assert analysis.method_count == 1
    assert analysis.async_target_count == 1
    assert analysis.callable_target_count == 3


def test_statistics_distinguish_target_kinds(tmp_path: Path) -> None:
    source = (
        "def plain():\n"
        "    ...\n"
        "\n"
        "async def fetch():\n"
        "    ...\n"
        "\n"
        "class Account:\n"
        "    def deposit(self):\n"
        "        ...\n"
        "    async def withdraw(self):\n"
        "        ...\n"
        "    def outer(self):\n"
        "        def inner():\n"
        "            ...\n"
        "        return inner\n"
    )
    (tmp_path / "shapes.py").write_text(source, encoding="utf-8")

    analysis = analyze_repository(tmp_path)

    assert analysis.callable_target_count == 6
    assert analysis.function_count == 3
    assert analysis.method_count == 3
    assert analysis.async_target_count == 2
    assert analysis.class_count == 1
    assert [function.qualified_name for function in analysis.callable_targets] == [
        "plain",
        "fetch",
        "Account.deposit",
        "Account.withdraw",
        "Account.outer",
        "Account.outer.inner",
    ]


def test_ordering_is_deterministic_regardless_of_creation_order(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    (root / "z.py").write_text("def zed():\n    ...\n", encoding="utf-8")
    mid = root / "mid"
    mid.mkdir()
    (mid / "m.py").write_text("def em():\n    ...\n", encoding="utf-8")
    (root / "a.py").write_text("def ay():\n    ...\n", encoding="utf-8")

    analysis = analyze_repository(tmp_path)

    assert [module.relative_path.as_posix() for module in analysis.modules] == [
        "a.py",
        "z.py",
        "mid/m.py",
    ]
    assert analysis == analyze_repository(tmp_path)


def test_single_file_target_uses_file_name_as_relative_path(tmp_path: Path) -> None:
    target = tmp_path / "solo.py"
    target.write_text("def only():\n    ...\n", encoding="utf-8")

    analysis = analyze_repository(target)

    assert analysis.is_file is True
    assert analysis.python_file_count == 1
    assert len(analysis.modules) == 1
    assert analysis.modules[0].relative_path == Path("solo.py")
    assert analysis.modules[0].relative_path.as_posix() == "solo.py"


def test_syntax_issues_are_aggregated_without_aborting(tmp_path: Path) -> None:
    (tmp_path / "good.py").write_text("def fine():\n    ...\n", encoding="utf-8")
    (tmp_path / "broken.py").write_text("def broken(\n", encoding="utf-8")

    analysis = analyze_repository(tmp_path)

    assert analysis.python_file_count == 2
    assert analysis.parsed_file_count == 1
    assert analysis.syntax_issue_count == 1
    assert analysis.read_issue_count == 0
    assert analysis.issue_count == 1
    assert analysis.callable_target_count == 1


def test_read_issues_are_aggregated_without_aborting(tmp_path: Path) -> None:
    (tmp_path / "good.py").write_text("def fine():\n    ...\n", encoding="utf-8")
    (tmp_path / "latin.py").write_bytes(b"# -*- coding: ascii -*-\nname = 'caf\xe9'\n")

    analysis = analyze_repository(tmp_path)

    assert analysis.parsed_file_count == 1
    assert analysis.syntax_issue_count == 0
    assert analysis.read_issue_count == 1
    assert analysis.issue_count == 1


def test_excluded_directories_are_not_analyzed(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "good.py").write_text("def fine():\n    ...\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "broken.py").write_text("def broken(\n", encoding="utf-8")

    analysis = analyze_repository(tmp_path)

    assert analysis.python_file_count == 1
    assert analysis.syntax_issue_count == 0
    assert [module.relative_path.as_posix() for module in analysis.modules] == ["src/good.py"]


def test_empty_directory_produces_empty_analysis(tmp_path: Path) -> None:
    analysis = analyze_repository(tmp_path)

    assert analysis.python_file_count == 0
    assert analysis.modules == ()
    assert analysis.callable_target_count == 0
    assert analysis.class_count == 0
    assert analysis.issue_count == 0
