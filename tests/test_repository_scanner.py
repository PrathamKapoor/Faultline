"""Tests for filesystem-level repository discovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from edgehunter.scanner import (
    PathNotFoundError,
    ScanError,
    UnsupportedTargetError,
    is_excluded_directory,
    scan_repository,
)


def test_collects_python_files_and_ignores_other_files(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    package = root / "package"
    package.mkdir()
    (package / "mod.py").write_text("", encoding="utf-8")
    (root / "README.md").write_text("", encoding="utf-8")
    (root / "data.json").write_text("", encoding="utf-8")

    summary = scan_repository(tmp_path)

    assert summary.is_file is False
    assert summary.target == root
    assert summary.python_files == (package / "mod.py",)
    assert summary.python_file_count == 1


def test_excluded_directories_are_pruned_and_recorded(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("", encoding="utf-8")

    for name in (".venv", "__pycache__", "build", "demo.egg-info"):
        directory = root / name
        directory.mkdir()
        (directory / "generated.py").write_text("", encoding="utf-8")

    summary = scan_repository(tmp_path)

    assert summary.python_files == (root / "src" / "app.py",)
    assert summary.excluded_directories == (
        root / ".venv",
        root / "__pycache__",
        root / "build",
        root / "demo.egg-info",
    )


def test_single_python_file_is_accepted(tmp_path: Path) -> None:
    target = tmp_path / "solo.py"
    target.write_text("", encoding="utf-8")

    summary = scan_repository(target)

    assert summary.is_file is True
    assert summary.target == target.resolve()
    assert summary.python_files == (target.resolve(),)
    assert summary.excluded_directories == ()


def test_empty_directory_yields_empty_summary(tmp_path: Path) -> None:
    summary = scan_repository(tmp_path)

    assert summary.is_file is False
    assert summary.python_files == ()
    assert summary.python_file_count == 0
    assert summary.excluded_directories == ()


def test_missing_path_raises_path_not_found(tmp_path: Path) -> None:
    missing = tmp_path / "missing"

    with pytest.raises(PathNotFoundError) as excinfo:
        scan_repository(missing)

    assert "path does not exist" in str(excinfo.value)
    assert str(missing) in str(excinfo.value)


def test_non_python_file_raises_unsupported_target(tmp_path: Path) -> None:
    target = tmp_path / "data.txt"
    target.write_text("", encoding="utf-8")

    with pytest.raises(UnsupportedTargetError) as excinfo:
        scan_repository(target)

    assert "not a Python source file" in str(excinfo.value)


def test_scan_errors_share_a_common_base() -> None:
    assert issubclass(PathNotFoundError, ScanError)
    assert issubclass(UnsupportedTargetError, ScanError)


def test_directory_walk_order_is_deterministic(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    (root / "b.py").write_text("", encoding="utf-8")
    (root / "a.py").write_text("", encoding="utf-8")
    sub = root / "sub"
    sub.mkdir()
    (sub / "z.py").write_text("", encoding="utf-8")
    (sub / "a.py").write_text("", encoding="utf-8")

    summary = scan_repository(tmp_path)

    assert summary.python_files == (
        root / "a.py",
        root / "b.py",
        sub / "a.py",
        sub / "z.py",
    )


def test_is_excluded_directory_knows_common_locations() -> None:
    assert is_excluded_directory(".venv") is True
    assert is_excluded_directory("__pycache__") is True
    assert is_excluded_directory("demo.egg-info") is True
    assert is_excluded_directory("src") is False
