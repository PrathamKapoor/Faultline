"""Immutable models produced by EdgeHunter's static scanner.

These models describe the *syntactic* structure of Python source code.
Nothing in this module imports, evaluates, or executes analyzed code, and all
annotations or decorators are stored as source text, never as runtime objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class ParameterKind(str, Enum):
    """How a parameter may be supplied by callers (mirrors the ``ast`` buckets)."""

    POSITIONAL_ONLY = "positional_only"
    POSITIONAL_OR_KEYWORD = "positional_or_keyword"
    VAR_POSITIONAL = "var_positional"
    KEYWORD_ONLY = "keyword_only"
    VAR_KEYWORD = "var_keyword"


class TargetKind(str, Enum):
    """Scope-aware classification of a callable definition."""

    FUNCTION = "function"
    ASYNC_FUNCTION = "async_function"
    METHOD = "method"
    ASYNC_METHOD = "async_method"
    NESTED_FUNCTION = "nested_function"
    NESTED_ASYNC_FUNCTION = "nested_async_function"


METHOD_KINDS: frozenset[TargetKind] = frozenset({TargetKind.METHOD, TargetKind.ASYNC_METHOD})

# A method's implicit receiver is supplied by the interpreter, so analyses that
# reason about user-supplied inputs (risk scoring, candidate cases) treat it as
# structural rather than as an input parameter.
RECEIVER_PARAMETER_NAMES: frozenset[str] = frozenset({"self", "cls"})

# Parameter buckets a caller supplies as one value per call. ``*args`` and
# ``**kwargs`` are excluded because they bundle an unknown number of values.
SINGLE_VALUE_PARAMETER_KINDS: frozenset[ParameterKind] = frozenset(
    {
        ParameterKind.POSITIONAL_ONLY,
        ParameterKind.POSITIONAL_OR_KEYWORD,
        ParameterKind.KEYWORD_ONLY,
    }
)


@dataclass(frozen=True, slots=True)
class ParameterInfo:
    """One declared parameter of a callable, as written in the source."""

    name: str
    kind: ParameterKind
    annotation: str | None
    has_default: bool


@dataclass(frozen=True, slots=True)
class FunctionInfo:
    """A function or method definition found in a module."""

    name: str
    qualified_name: str
    kind: TargetKind
    lineno: int
    end_lineno: int | None
    is_async: bool
    owner: str | None
    parameters: tuple[ParameterInfo, ...]
    return_annotation: str | None
    decorators: tuple[str, ...]

    @property
    def signature(self) -> str:
        """Static, source-based signature; defaults are rendered as ``...``."""
        prefix = "async " if self.is_async else ""
        parameters = render_parameters(self.parameters)
        returns = f" -> {self.return_annotation}" if self.return_annotation is not None else ""
        return f"{prefix}{self.qualified_name}({parameters}){returns}"


@dataclass(frozen=True, slots=True)
class ClassInfo:
    """A class definition found in a module."""

    name: str
    qualified_name: str
    lineno: int
    end_lineno: int | None
    bases: tuple[str, ...]
    decorators: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SyntaxIssue:
    """A source file that could not be parsed as Python."""

    line: int | None
    column: int | None
    message: str


@dataclass(frozen=True, slots=True)
class ReadIssue:
    """A source file that could not be read or decoded."""

    message: str


@dataclass(frozen=True, slots=True)
class ModuleAnalysis:
    """Structural analysis of one Python source file."""

    path: Path
    relative_path: Path
    functions: tuple[FunctionInfo, ...]
    classes: tuple[ClassInfo, ...]
    syntax_issue: SyntaxIssue | None = None
    read_issue: ReadIssue | None = None

    @property
    def is_parsed(self) -> bool:
        """True when the file was read and parsed without issues."""
        return self.syntax_issue is None and self.read_issue is None

    @property
    def has_issue(self) -> bool:
        """True when the file produced a syntax or read issue."""
        return not self.is_parsed


def render_parameters(parameters: tuple[ParameterInfo, ...]) -> str:
    """Render a parameter list the way it would appear in a ``def`` statement."""
    last_positional_only = -1
    first_keyword_only = -1
    has_var_positional = False
    for index, parameter in enumerate(parameters):
        if parameter.kind is ParameterKind.POSITIONAL_ONLY:
            last_positional_only = index
        elif parameter.kind is ParameterKind.KEYWORD_ONLY and first_keyword_only == -1:
            first_keyword_only = index
        elif parameter.kind is ParameterKind.VAR_POSITIONAL:
            has_var_positional = True

    parts: list[str] = []
    for index, parameter in enumerate(parameters):
        if index == first_keyword_only and not has_var_positional:
            parts.append("*")
        parts.append(_render_parameter(parameter))
        if index == last_positional_only:
            parts.append("/")
    return ", ".join(parts)


def _render_parameter(parameter: ParameterInfo) -> str:
    prefix = ""
    if parameter.kind is ParameterKind.VAR_POSITIONAL:
        prefix = "*"
    elif parameter.kind is ParameterKind.VAR_KEYWORD:
        prefix = "**"

    text = f"{prefix}{parameter.name}"
    if parameter.annotation is not None:
        text += f": {parameter.annotation}"
    if parameter.has_default:
        text += " = ..."
    return text
