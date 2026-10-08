"""Shared rendering helpers for static analysis.

Rendering turns AST nodes back into source text with ``ast.unparse``. It never
evaluates anything: the result is a textual rendering of syntax only.
"""

from __future__ import annotations

import ast

UNPARSE_FALLBACK = "<unparseable>"


def render_source(node: ast.AST) -> str:
    """Render a node as source text, falling back instead of crashing."""
    try:
        return ast.unparse(node)
    except Exception:
        return UNPARSE_FALLBACK


def source_lineno(node: ast.AST) -> int:
    """Best-effort 1-based line of a node, or 0 when the node has no position."""
    lineno: int | None = getattr(node, "lineno", None)
    return lineno if lineno is not None else 0
