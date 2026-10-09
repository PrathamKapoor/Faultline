"""Safe source reading and AST-based module analysis.

Reading and parsing use Python's own source machinery (``tokenize`` for PEP 263
encoding detection, ``ast`` for parsing). Analyzed code is never imported,
evaluated, or executed; a syntax failure or unreadable file becomes structured
data instead of crashing the surrounding repository scan.

The pipeline is split in two stages so a file is read and parsed exactly once
per scan:

```text
parse_source_file()   source text  -> ast.Module (or a structured issue)
        ├── build_module_analysis()    AST -> structural facts (scanner)
        └── extract_module_semantics() AST -> semantic facts (analysis)
```

``SourceFile.tree`` is internal pipeline state; it is not part of the public
scanner models.
"""

from __future__ import annotations

import ast
import io
import tokenize
from dataclasses import dataclass
from pathlib import Path

from edgehunter.fingerprint import fingerprint_bytes
from edgehunter.scanner.models import (
    ModuleAnalysis,
    ReadIssue,
    SyntaxIssue,
)
from edgehunter.scanner.targets import extract_module_targets


@dataclass(frozen=True, slots=True)
class SourceText:
    """Decoded source text plus the fingerprint of the bytes it came from.

    The digest is computed from the raw bytes, because those are what the
    interpreter executes. It lets a later stage prove that the copy it is about
    to run is the same revision this text was analyzed from.
    """

    text: str
    digest: str


@dataclass(frozen=True, slots=True)
class SourceFile:
    """Outcome of the safe read-and-parse stage for one Python source file."""

    path: Path
    relative_path: Path
    tree: ast.Module | None
    syntax_issue: SyntaxIssue | None = None
    read_issue: ReadIssue | None = None
    digest: str | None = None
    """SHA-256 of the source bytes, or ``None`` when the file could not be read.

    Carried so a staged execution copy can be checked against the revision the
    oracle evidence was mined from. It is not part of any public analysis model.
    """

    @property
    def is_parsed(self) -> bool:
        """True when the file was read and parsed without issues."""
        return self.syntax_issue is None and self.read_issue is None


def parse_source_file(path: Path, relative_path: Path) -> SourceFile:
    """Read and parse one Python source file without importing or executing it.

    Failures are captured, not raised: an unreadable file yields a
    ``read_issue`` and an unparseable file a ``syntax_issue``.
    """
    try:
        source = _read_source(path)
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:
        return SourceFile(
            path=path,
            relative_path=relative_path,
            tree=None,
            read_issue=ReadIssue(message=str(exc)),
        )

    try:
        tree = ast.parse(source.text, filename=str(path))
    except SyntaxError as exc:
        return SourceFile(
            path=path,
            relative_path=relative_path,
            tree=None,
            syntax_issue=SyntaxIssue(line=exc.lineno, column=exc.offset, message=exc.msg),
            digest=source.digest,
        )

    return SourceFile(path=path, relative_path=relative_path, tree=tree, digest=source.digest)


def build_module_analysis(source: SourceFile) -> ModuleAnalysis:
    """Extract structural facts from an already-parsed file."""
    if source.tree is None:
        return ModuleAnalysis(
            path=source.path,
            relative_path=source.relative_path,
            functions=(),
            classes=(),
            syntax_issue=source.syntax_issue,
            read_issue=source.read_issue,
            digest=source.digest,
        )

    functions, classes = extract_module_targets(source.tree)
    return ModuleAnalysis(
        path=source.path,
        relative_path=source.relative_path,
        functions=functions,
        classes=classes,
        digest=source.digest,
    )


def analyze_python_file(path: Path, relative_path: Path) -> ModuleAnalysis:
    """Analyze one Python source file without importing or executing it.

    ``relative_path`` is the caller-chosen display path (for example relative
    to the scan root). A file that cannot be read or decoded yields a
    ``ReadIssue``; a file that cannot be parsed yields a ``SyntaxIssue``. Both
    cases return a ``ModuleAnalysis`` instead of raising.
    """
    return build_module_analysis(parse_source_file(path, relative_path))


def _read_source(path: Path) -> SourceText:
    """Read Python source once, using Python's own encoding rules (PEP 263).

    The bytes are read once and both the decoded text and the SHA-256 digest are
    derived from that single read, so fingerprinting adds no extra I/O. Decoding
    reproduces ``tokenize.open`` exactly: ``detect_encoding`` handles the BOM and
    any coding cookie, and ``TextIOWrapper`` applies universal newlines, so the
    text handed to ``ast.parse`` is identical to what the previous implementation
    produced.
    """
    data = path.read_bytes()
    buffer = io.BytesIO(data)
    encoding, _ = tokenize.detect_encoding(buffer.readline)
    buffer.seek(0)
    with io.TextIOWrapper(buffer, encoding=encoding, line_buffering=True) as stream:
        return SourceText(text=stream.read(), digest=fingerprint_bytes(data))
