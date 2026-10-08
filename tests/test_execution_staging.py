"""Tests for safe source staging and path containment."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from edgehunter.execution import StagingError, StagingFailure
from edgehunter.execution.staging import (
    MAX_FILE_BYTES,
    MAX_STAGED_FILES,
    MAX_TOTAL_BYTES,
    StagingLimits,
    discover_python_sources,
    stage_source,
)


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "module.py").write_text("def f(x: int):\n    return x\n", encoding="utf-8")
    (root / "pkg" / "nested.py").write_text("def g(x: int):\n    return x\n", encoding="utf-8")
    (root / "notes.txt").write_text("secret-ish", encoding="utf-8")
    (root / ".env").write_text("TOKEN=abc", encoding="utf-8")
    (root / ".venv").mkdir()
    (root / ".venv" / "ignored.py").write_text("x = 1\n", encoding="utf-8")
    return root


def test_selected_sources_are_copied(tmp_path: Path) -> None:
    root = _repository(tmp_path)

    staged = stage_source(root, ("module.py", "pkg/nested.py"), tmp_path / "stage")

    assert staged.files
    assert (staged.root / "module.py").is_file()
    assert (staged.root / "pkg" / "nested.py").is_file()
    assert staged.total_bytes > 0


def test_non_python_files_are_never_staged(tmp_path: Path) -> None:
    root = _repository(tmp_path)

    with pytest.raises(StagingError) as caught:
        stage_source(root, ("notes.txt",), tmp_path / "stage")

    assert caught.value.failure is StagingFailure.UNSUPPORTED_SUFFIX


def test_dotenv_files_are_never_staged(tmp_path: Path) -> None:
    root = _repository(tmp_path)

    with pytest.raises(StagingError) as caught:
        stage_source(root, (".env",), tmp_path / "stage")

    assert caught.value.failure is StagingFailure.UNSUPPORTED_SUFFIX


def test_parent_traversal_is_refused(tmp_path: Path) -> None:
    root = _repository(tmp_path)

    with pytest.raises(StagingError) as caught:
        stage_source(root, ("../outside.py",), tmp_path / "stage")

    assert caught.value.failure is StagingFailure.PATH_ESCAPE


def test_absolute_path_is_refused(tmp_path: Path) -> None:
    root = _repository(tmp_path)

    with pytest.raises(StagingError) as caught:
        stage_source(root, (str(tmp_path / "elsewhere.py"),), tmp_path / "stage")

    assert caught.value.failure is StagingFailure.PATH_ESCAPE


def test_missing_file_is_refused(tmp_path: Path) -> None:
    root = _repository(tmp_path)

    with pytest.raises(StagingError) as caught:
        stage_source(root, ("absent.py",), tmp_path / "stage")

    assert caught.value.failure is StagingFailure.NOT_A_FILE


def test_directory_is_refused(tmp_path: Path) -> None:
    root = _repository(tmp_path)

    with pytest.raises(StagingError) as caught:
        stage_source(root, ("pkg",), tmp_path / "stage")

    assert caught.value.failure in (
        StagingFailure.NOT_A_FILE,
        StagingFailure.UNSAFE_FILE_TYPE,
        StagingFailure.UNSUPPORTED_SUFFIX,
    )


def test_symlink_is_refused_and_not_followed(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("def leak():\n    return 1\n", encoding="utf-8")
    try:
        os.symlink(outside, root / "link.py")
    except (OSError, NotImplementedError):  # pragma: no cover - platform dependent
        pytest.skip("symlinks are unavailable for this user on this platform")

    with pytest.raises(StagingError) as caught:
        stage_source(root, ("link.py",), tmp_path / "stage")

    assert caught.value.failure is StagingFailure.SYMLINK
    assert not (tmp_path / "stage" / "link.py").exists()


def test_too_many_files_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "many"
    root.mkdir()
    names = tuple(f"m{index}.py" for index in range(MAX_STAGED_FILES + 2))
    for name in names:
        (root / name).write_text("x = 1\n", encoding="utf-8")

    with pytest.raises(StagingError) as caught:
        stage_source(root, names, tmp_path / "stage")

    assert caught.value.failure is StagingFailure.TOO_MANY_FILES


def test_oversized_file_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "big"
    root.mkdir()
    # Just over the documented 1 MiB per-file limit.
    (root / "huge.py").write_text("x = 1\n" * 200_000, encoding="utf-8")
    assert (root / "huge.py").stat().st_size > MAX_FILE_BYTES

    with pytest.raises(StagingError) as caught:
        stage_source(root, ("huge.py",), tmp_path / "stage")

    assert caught.value.failure is StagingFailure.FILE_TOO_LARGE


def test_total_size_limit_is_enforced(tmp_path: Path) -> None:
    root = tmp_path / "bulk"
    root.mkdir()
    names = tuple(f"m{index}.py" for index in range(4))
    for name in names:
        (root / name).write_text("x = 1\n" * 1000, encoding="utf-8")

    with pytest.raises(StagingError) as caught:
        stage_source(root, names, tmp_path / "stage", StagingLimits(max_total_bytes=1000))

    assert caught.value.failure is StagingFailure.TOTAL_TOO_LARGE


def test_duplicate_selection_is_deduplicated(tmp_path: Path) -> None:
    root = _repository(tmp_path)

    staged = stage_source(root, ("module.py", "module.py"), tmp_path / "stage")

    assert len(staged.files) == 1


def test_discover_selects_only_python_sources(tmp_path: Path) -> None:
    root = _repository(tmp_path)

    discovered = discover_python_sources(root)

    assert "module.py" in discovered
    assert "pkg/nested.py" in discovered
    assert "notes.txt" not in discovered
    assert ".env" not in discovered
    assert not any(".venv" in name for name in discovered)


def test_staging_limits_are_the_documented_defaults() -> None:
    assert MAX_STAGED_FILES == 100
    assert MAX_FILE_BYTES == 1024 * 1024
    assert MAX_TOTAL_BYTES == 8 * 1024 * 1024
