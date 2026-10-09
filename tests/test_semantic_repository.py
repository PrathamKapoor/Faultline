"""Tests for repository-level semantic aggregation."""

from __future__ import annotations

from pathlib import Path

import pytest

from edgehunter.analysis import analyze_repository_semantics


def test_counts_aggregate_across_modules(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(
        "def first(x: int):\n    if x > 1:\n        return x * 2\n",
        encoding="utf-8",
    )
    (tmp_path / "b.py").write_text(
        "class Box:\n    def check(self, y: float):\n        while y < 0.5:\n            y += 1\n",
        encoding="utf-8",
    )

    analysis = analyze_repository_semantics(tmp_path)

    assert analysis.predicate_count == 2
    assert analysis.comparison_count == 2
    assert analysis.arithmetic_expression_count == 2
    assert analysis.boundary_candidate_count > 0
    assert analysis.targets_with_boundaries == 2


def test_structure_counts_are_preserved_through_semantic_analysis(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(
        "class C:\n    def m(self):\n        ...\n    async def a(self):\n        ...\n",
        encoding="utf-8",
    )

    analysis = analyze_repository_semantics(tmp_path)

    structure = analysis.structure
    assert structure.python_file_count == 1
    assert structure.parsed_file_count == 1
    assert structure.class_count == 1
    assert structure.method_count == 2
    assert structure.async_target_count == 1
    assert structure.issue_count == 0


def test_syntax_issue_is_reported_but_produces_no_facts(tmp_path: Path) -> None:
    (tmp_path / "good.py").write_text(
        "def fine(x: int):\n    if x > 0:\n        ...\n", encoding="utf-8"
    )
    (tmp_path / "broken.py").write_text("def broken(\n", encoding="utf-8")

    analysis = analyze_repository_semantics(tmp_path)

    assert analysis.structure.syntax_issue_count == 1
    assert analysis.structure.issue_count == 1
    assert analysis.predicate_count == 1
    assert [module.relative_path.as_posix() for module in analysis.modules if module.targets] == [
        "good.py"
    ]


def test_read_issue_produces_no_facts(tmp_path: Path) -> None:
    (tmp_path / "latin.py").write_bytes(b"# -*- coding: ascii -*-\nname = 'caf\xe9'\n")

    analysis = analyze_repository_semantics(tmp_path)

    assert analysis.structure.read_issue_count == 1
    assert analysis.modules[0].targets == ()
    assert analysis.boundary_candidate_count == 0


def test_single_file_target_analyzes_semantics(tmp_path: Path) -> None:
    target = tmp_path / "solo.py"
    target.write_text("def f(x: int):\n    if x > 0:\n        ...\n", encoding="utf-8")

    analysis = analyze_repository_semantics(target)

    assert analysis.structure.is_file is True
    assert analysis.modules[0].relative_path == Path("solo.py")
    assert analysis.predicate_count == 1
    assert analysis.boundary_candidate_count > 0


def test_empty_directory_has_no_semantics(tmp_path: Path) -> None:
    analysis = analyze_repository_semantics(tmp_path)

    assert analysis.modules == ()
    assert analysis.predicate_count == 0
    assert analysis.boundary_candidate_count == 0
    assert analysis.boundaries == ()


def test_excluded_directories_contribute_no_semantics(tmp_path: Path) -> None:
    (tmp_path / "good.py").write_text(
        "def fine(x: int):\n    if x > 0:\n        ...\n", encoding="utf-8"
    )
    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / "vendored.py").write_text(
        "def vendored(x):\n    if x > 0:\n        ...\n", encoding="utf-8"
    )

    analysis = analyze_repository_semantics(tmp_path)

    assert analysis.predicate_count == 1
    assert [target.qualified_name for module in analysis.modules for target in module.targets] == [
        "fine"
    ]


def test_each_source_file_is_read_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import edgehunter.scanner.ast_parser as ast_parser_module
    from edgehunter.scanner.ast_parser import SourceText

    (tmp_path / "a.py").write_text("def f(x: int):\n    if x > 0:\n        ...\n", encoding="utf-8")

    reads: list[Path] = []
    original = ast_parser_module._read_source

    def counting_read_source(path: Path) -> SourceText:
        reads.append(path)
        return original(path)

    monkeypatch.setattr(ast_parser_module, "_read_source", counting_read_source)

    analysis = analyze_repository_semantics(tmp_path)

    assert len(reads) == 1
    assert analysis.predicate_count == 1
