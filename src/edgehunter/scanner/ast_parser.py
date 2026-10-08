"""Safe source reading and AST-based module analysis.

Reading and parsing use Python's own source machinery (``tokenize`` for PEP 263
encoding detection, ``ast`` for parsing). Analyzed code is never imported,
evaluated, or executed; a syntax failure or unreadable file becomes structured
data instead of crashing the surrounding repository scan.
"""

from __future__ import annotations

import ast
import tokenize
from pathlib import Path

from edgehunter.scanner.models import (
    ModuleAnalysis,
    ReadIssue,
    SyntaxIssue,
)
from edgehunter.scanner.targets import extract_module_targets


def analyze_python_file(path: Path, relative_path: Path) -> ModuleAnalysis:
    """Analyze one Python source file without importing or executing it.

    ``relative_path`` is the caller-chosen display path (for example relative
    to the scan root). A file that cannot be read or decoded yields a
    ``ReadIssue``; a file that cannot be parsed yields a ``SyntaxIssue``. Both
    cases return a ``ModuleAnalysis`` instead of raising.
    """
    try:
        source = _read_source(path)
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:
        return ModuleAnalysis(
            path=path,
            relative_path=relative_path,
            functions=(),
            classes=(),
            read_issue=ReadIssue(message=str(exc)),
        )

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return ModuleAnalysis(
            path=path,
            relative_path=relative_path,
            functions=(),
            classes=(),
            syntax_issue=SyntaxIssue(line=exc.lineno, column=exc.offset, message=exc.msg),
        )

    functions, classes = extract_module_targets(tree)
    return ModuleAnalysis(
        path=path,
        relative_path=relative_path,
        functions=functions,
        classes=classes,
    )


def _read_source(path: Path) -> str:
    """Read Python source using Python's own encoding rules (PEP 263)."""
    with tokenize.open(path) as stream:
        return stream.read()
