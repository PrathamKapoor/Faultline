"""Structural extraction of classes and callables from parsed ASTs.

Classification rule: the kind of a callable is decided by its *immediate
lexical scope*. A ``def`` directly inside a class body is a method; directly
inside a function body it is a nested function; at module scope it is a
function. Compound statements (``if``, ``try``, loops, ``with``, ``match``) do
not change the scope: definitions inside them inherit the nearest lexical
scope.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from enum import Enum

from edgehunter.scanner.models import (
    ClassInfo,
    FunctionInfo,
    ParameterInfo,
    ParameterKind,
    TargetKind,
)
from edgehunter.scanner.rendering import render_source


class _Scope(str, Enum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"


@dataclass(frozen=True, slots=True)
class CallableNode:
    """A callable target together with the AST node it was built from.

    The AST node is internal pipeline state: it lets later stages extract facts
    for the same target without reading or parsing the file again.
    """

    info: FunctionInfo
    node: ast.FunctionDef | ast.AsyncFunctionDef


def extract_module_targets(
    tree: ast.Module,
) -> tuple[tuple[FunctionInfo, ...], tuple[ClassInfo, ...]]:
    """Return every callable and class in ``tree``, ordered by source line.

    Callables inside compound statements are extracted with their nearest
    lexical scope. Results are sorted by ``(lineno, qualified_name)`` so the
    ordering is deterministic regardless of traversal details.
    """
    callables, classes = _collect(tree)
    return tuple(item.info for item in callables), tuple(classes)


def collect_callable_nodes(tree: ast.Module) -> tuple[CallableNode, ...]:
    """Return every callable target with its AST node, ordered by source line.

    Used by semantic analysis so predicate, comparison, constant, and
    arithmetic facts are attributed to exactly the targets the structural
    scanner discovered.
    """
    callables, _ = _collect(tree)
    return tuple(callables)


def _collect(tree: ast.Module) -> tuple[list[CallableNode], list[ClassInfo]]:
    callables: list[CallableNode] = []
    classes: list[ClassInfo] = []
    for statement in tree.body:
        _visit(statement, (), _Scope.MODULE, callables, classes)

    callables.sort(key=lambda item: (item.info.lineno, item.info.qualified_name))
    classes.sort(key=lambda klass: (klass.lineno, klass.qualified_name))
    return callables, classes


def _visit(
    node: ast.AST,
    prefix: tuple[str, ...],
    scope: _Scope,
    callables: list[CallableNode],
    classes: list[ClassInfo],
) -> None:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        callables.append(CallableNode(info=_build_function(node, prefix, scope), node=node))
        child_prefix = (*prefix, node.name)
        child_scope = _Scope.FUNCTION
    elif isinstance(node, ast.ClassDef):
        classes.append(_build_class(node, prefix))
        child_prefix = (*prefix, node.name)
        child_scope = _Scope.CLASS
    else:
        child_prefix = prefix
        child_scope = scope

    for child in ast.iter_child_nodes(node):
        _visit(child, child_prefix, child_scope, callables, classes)


def _build_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    prefix: tuple[str, ...],
    scope: _Scope,
) -> FunctionInfo:
    is_async = isinstance(node, ast.AsyncFunctionDef)

    owner: str | None = None
    if scope is _Scope.CLASS:
        kind = TargetKind.ASYNC_METHOD if is_async else TargetKind.METHOD
        owner = ".".join(prefix) or None
    elif scope is _Scope.FUNCTION:
        kind = TargetKind.NESTED_ASYNC_FUNCTION if is_async else TargetKind.NESTED_FUNCTION
    else:
        kind = TargetKind.ASYNC_FUNCTION if is_async else TargetKind.FUNCTION

    return FunctionInfo(
        name=node.name,
        qualified_name=".".join((*prefix, node.name)),
        kind=kind,
        lineno=node.lineno,
        end_lineno=node.end_lineno,
        is_async=is_async,
        owner=owner,
        parameters=_extract_parameters(node.args),
        return_annotation=_render_optional(node.returns),
        decorators=tuple(render_source(decorator) for decorator in node.decorator_list),
    )


def _build_class(node: ast.ClassDef, prefix: tuple[str, ...]) -> ClassInfo:
    return ClassInfo(
        name=node.name,
        qualified_name=".".join((*prefix, node.name)),
        lineno=node.lineno,
        end_lineno=node.end_lineno,
        bases=tuple(render_source(base) for base in node.bases),
        decorators=tuple(render_source(decorator) for decorator in node.decorator_list),
    )


def _extract_parameters(arguments: ast.arguments) -> tuple[ParameterInfo, ...]:
    positional = [*arguments.posonlyargs, *arguments.args]
    first_defaulted = len(positional) - len(arguments.defaults)

    parameters: list[ParameterInfo] = []
    for index, argument in enumerate(positional):
        if index < len(arguments.posonlyargs):
            kind = ParameterKind.POSITIONAL_ONLY
        else:
            kind = ParameterKind.POSITIONAL_OR_KEYWORD
        parameters.append(_build_parameter(argument, kind, has_default=index >= first_defaulted))

    if arguments.vararg is not None:
        parameters.append(
            _build_parameter(arguments.vararg, ParameterKind.VAR_POSITIONAL, has_default=False)
        )

    for argument, default in zip(arguments.kwonlyargs, arguments.kw_defaults, strict=True):
        parameters.append(
            _build_parameter(argument, ParameterKind.KEYWORD_ONLY, has_default=default is not None)
        )

    if arguments.kwarg is not None:
        parameters.append(
            _build_parameter(arguments.kwarg, ParameterKind.VAR_KEYWORD, has_default=False)
        )

    return tuple(parameters)


def _build_parameter(
    argument: ast.arg,
    kind: ParameterKind,
    *,
    has_default: bool,
) -> ParameterInfo:
    return ParameterInfo(
        name=argument.arg,
        kind=kind,
        annotation=_render_optional(argument.annotation),
        has_default=has_default,
    )


def _render_optional(node: ast.expr | None) -> str | None:
    if node is None:
        return None
    return render_source(node)
