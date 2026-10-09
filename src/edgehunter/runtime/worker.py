"""The trusted sandbox worker: the only place target code is ever loaded.

This module runs **inside** the container. It is the deliberate, narrow exception
to the project's static-analysis invariant. It does exactly one job:

1. read a bounded request document from a fixed path,
2. validate the protocol version and the target path,
3. decode typed argument values with the shared codec,
4. load the staged module and find the selected callable,
5. invoke it **once**,
6. write one structured result document to a fixed path.

It contains no fuzzing loop, no retry, no auto-discovery of other callables, and
no oracle evaluation. An exception raised by the target is recorded as an
observation; it is never reported as a defect.

Two safety properties are load-bearing:

* The codec is imported at module load, **before** the staged tree is added to
  ``sys.path``, so a target module cannot shadow it.
* The result travels in a file, not on stdout, because target code can write to
  file descriptor 1 at will and corrupt a stdout protocol.

Target output capture is deliberately partial and says so: Python-level writes
are captured and bounded, while native writes to file descriptors 1 and 2 are
redirected to ``/dev/null`` and therefore *discarded*. No ``__repr__``,
``pickle``, or other serializer is ever invoked on a target-defined object, and
return values are matched by exact built-in type so a subclass's ``__repr__``
cannot run.
"""

from __future__ import annotations

import builtins
import contextlib
import io
import json
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

# Imported before the staged tree reaches sys.path on purpose.
from edgehunter.execution.codec import (
    PROTOCOL_VERSION,
    ProtocolError,
    TypedValue,
    ValueKind,
    check_document_size,
    decode_value,
    dumps,
    encode_float,
    encode_value,
)

WORKSPACE = Path("/workspace")
REQUEST_PATH = Path("/input/request.json")
RESULT_PATH = Path("/output/result.json")

# Bounds on captured target output. Target code may print without limit, so both
# streams are truncated rather than allowed to grow the container's memory.
MAX_CAPTURED_OUTPUT_BYTES = 16 * 1024
MAX_OPAQUE_TYPE_LENGTH = 200
MAX_DETAIL_LENGTH = 4096

# Exactly the built-in scalar types we are willing to describe. ``type(x) is``
# rather than ``isinstance`` so a subclass with a custom ``__repr__`` is refused.
_SCALAR_TYPES = (bool, int, float, str)

# Bounds on the exception-identity metadata. Module and qualified names are
# class attributes, so they are short in practice; the cap exists so a target
# cannot make the result document grow.
MAX_IDENTITY_FIELD_LENGTH = 200
MAX_BUILTIN_LINEAGE = 16


def _trusted_builtin_exception_classes() -> frozenset[type]:
    """The real exception classes defined by :mod:`builtins`, by identity.

    This is the only trusted statement about exception identity in the whole
    project: a class is a standard-library exception when it **is** one of these
    objects, not when it merely has the same name. A target may define
    ``class ValueError(Exception)``; that class is a different object and is
    correctly reported as non-builtin.
    """
    return frozenset(
        candidate
        for candidate in vars(builtins).values()
        if isinstance(candidate, type) and issubclass(candidate, BaseException)
    )


def _class_attribute(cls: type, name: str) -> str | None:
    """Read a plain string class attribute without invoking target code.

    ``cls.__module__`` normally goes through ``type(cls).__getattribute__``, which
    for a class with a custom metaclass is target-defined code. ``__module__`` and
    ``__qualname__`` are ordinary ``str`` entries in the class dictionary, so they
    are read with ``type.__getattribute__`` directly, which bypasses any
    metaclass override. Anything that is not a ``str`` is reported as unknown
    rather than coerced.
    """
    try:
        value = type.__getattribute__(cls, name)
    except BaseException:  # noqa: BLE001 - a hostile metaclass may raise
        return None
    if isinstance(value, str):
        return value[:MAX_IDENTITY_FIELD_LENGTH]
    return None


def _method_resolution_order(cls: type) -> tuple[type, ...]:
    """The class's real MRO, or an empty tuple when it cannot be read safely."""
    try:
        mro = type.__getattribute__(cls, "__mro__")
    except BaseException:  # noqa: BLE001 - a hostile metaclass may raise
        return ()
    if not isinstance(mro, tuple) or not all(isinstance(entry, type) for entry in mro):
        return ()
    return mro


def _builtin_lineage(cls: type) -> tuple[str, ...]:
    """Builtin exception classes in ``cls``'s real MRO, most derived first.

    Walking the MRO and testing each base *by identity* against
    :func:`_trusted_builtin_exception_classes` is what makes the result a fact
    about Python's inheritance rather than a guess from names. A class deriving
    from the real ``ValueError`` lists it; ``class MyValueError(Exception)`` does
    not, and neither does ``class ValueError(Exception)`` which merely shares a
    name.
    """
    trusted = _trusted_builtin_exception_classes()
    names: list[str] = []
    for base in _method_resolution_order(cls):
        if base in trusted:
            name = _class_attribute(base, "__name__")
            if name is not None and name not in names:
                names.append(name)
        if len(names) >= MAX_BUILTIN_LINEAGE:
            break
    return tuple(names)


def _exception_identity(exc: BaseException) -> dict[str, Any]:
    """Describe the class of a raised exception using only safe metadata.

    Nothing here calls ``repr``, ``str``, or any method on the exception or its
    class, and nothing is serialized: only the class dictionary's own string
    entries and its real MRO are read. Every field is omitted when it cannot be
    established, so "unknown" stays distinguishable from "recorded".
    """
    cls = type(exc)
    module = _class_attribute(cls, "__module__")
    qualname = _class_attribute(cls, "__qualname__")
    lineage = _builtin_lineage(cls)
    is_builtin = cls in _trusted_builtin_exception_classes()

    identity: dict[str, Any] = {}
    if module is not None:
        identity["exception_module"] = module
    if qualname is not None:
        identity["exception_qualname"] = qualname
    identity["exception_builtin"] = is_builtin
    if lineage:
        identity["exception_builtin_lineage"] = list(lineage)
    return identity


class ImportFailure(Exception):
    """The staged module could not be imported."""


class TargetMissing(Exception):
    """The module imported but the selected callable is absent or unusable."""


class _BoundedStream(io.TextIOBase):
    """A text sink that keeps at most ``limit`` bytes and discards the rest.

    Target code may print without limit. Accumulating into a plain ``StringIO``
    would grow memory until the container's cgroup limit is reached; here the
    excess is dropped as it arrives, so a flooding target costs a fixed amount of
    memory regardless of how much it writes, and the capture still ends with an
    accurate ``truncated`` flag. Writes are always reported as accepted, like
    ``/dev/null``, so a target never sees a short write.
    """

    def __init__(self, limit: int = MAX_CAPTURED_OUTPUT_BYTES) -> None:
        self._chunks: list[str] = []
        self._size = 0
        self._limit = limit
        self.truncated = False

    def write(self, text: str) -> int:
        """Accept ``text``, keeping only what fits in the remaining budget."""
        if not isinstance(text, str):
            raise TypeError(f"write() takes str, not {type(text).__name__}")
        if self._size >= self._limit:
            self.truncated = True
            return len(text)
        encoded = text.encode("utf-8", errors="replace")
        remaining = self._limit - self._size
        if len(encoded) <= remaining:
            self._chunks.append(text)
            self._size += len(encoded)
            return len(text)
        self._chunks.append(encoded[:remaining].decode("utf-8", errors="ignore"))
        self._size = self._limit
        self.truncated = True
        return len(text)

    def getvalue(self) -> str:
        """Return the retained prefix of everything written so far."""
        return "".join(self._chunks)


def main() -> int:
    """Run one invocation and emit one result document."""
    started = time.monotonic()
    python_stdout = _BoundedStream()
    python_stderr = _BoundedStream()
    document = _run_once(python_stdout, python_stderr)

    document["duration_seconds"] = round(time.monotonic() - started, 6)
    document["captured_stdout"] = python_stdout.getvalue()
    document["stdout_truncated"] = python_stdout.truncated
    document["captured_stderr"] = python_stderr.getvalue()
    document["stderr_truncated"] = python_stderr.truncated
    document["native_output_discarded"] = True

    try:
        RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESULT_PATH.write_text(dumps(document), encoding="utf-8")
    except OSError:
        return 1
    return 0


def _run_once(python_stdout: _BoundedStream, python_stderr: _BoundedStream) -> dict[str, Any]:
    """Validate the request, then import and invoke the target exactly once."""
    try:
        request = _read_request()
    except ProtocolError as exc:
        return _failure("protocol_error", str(exc))
    except OSError as exc:
        return _failure("protocol_error", f"request unreadable: {exc}")

    target = request.get("target")
    invocation = request.get("invocation")
    if not isinstance(target, dict) or not isinstance(invocation, dict):
        return _failure("protocol_error", "request is missing target or invocation")

    relative_file = target.get("relative_file")
    qualified_name = target.get("qualified_name")
    if not isinstance(relative_file, str) or not isinstance(qualified_name, str):
        return _failure("protocol_error", "target identity is malformed")

    resolved = _resolve_target(relative_file)
    if resolved is None:
        return _failure("protocol_error", f"unsafe target path: {relative_file!r}")

    try:
        positional, keywords = _decode_arguments(invocation)
    except ProtocolError as exc:
        return _failure("protocol_error", str(exc))

    result: dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION,
        "execution_id": str(request.get("execution_id", "unknown")),
        "nonce": str(request.get("nonce", "")),
    }

    with contextlib.redirect_stdout(python_stdout), contextlib.redirect_stderr(python_stderr):
        with _discard_native_output():
            # Import is deliberately separate from invocation so an import-time
            # failure is never reported as a function exception.
            try:
                function = _load_callable(resolved, qualified_name)
            except ImportFailure as exc:
                result.update(_outcome("import_failure", detail=str(exc)))
                return result
            except TargetMissing as exc:
                result.update(_outcome("target_not_found", detail=str(exc)))
                return result

            try:
                returned = function(*positional, **keywords)
            except BaseException as exc:  # noqa: BLE001 - observation, not handling
                result.update(
                    _outcome(
                        "raised",
                        exception_type=type(exc).__name__,
                        exception_message=_clip(str(exc)),
                        **_exception_identity(exc),
                    )
                )
                return result

    result.update(_encode_return(returned))
    return result


@contextlib.contextmanager
def _discard_native_output() -> Any:
    """Point file descriptors 1 and 2 at /dev/null for the duration of the target call.

    Python-level writes are captured by the ``redirect_stdout`` calls; anything a
    target writes natively would otherwise land on the container's stdout. The
    result document does not travel on stdout, so discarding is safe and is
    recorded in the result as ``native_output_discarded``.
    """
    saved_out = os.dup(1)
    saved_err = os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
        yield
    finally:
        os.dup2(saved_out, 1)
        os.dup2(saved_err, 2)
        for descriptor in (saved_out, saved_err, devnull):
            with contextlib.suppress(OSError):
                os.close(descriptor)


def _read_request() -> dict[str, Any]:
    """Read and validate the bounded request document."""
    text = REQUEST_PATH.read_text(encoding="utf-8")
    check_document_size(text)
    try:
        document = json.loads(text)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ProtocolError("request is not valid JSON") from exc
    if not isinstance(document, dict):
        raise ProtocolError("request must be a JSON object")
    version = document.get("protocol_version")
    if version != PROTOCOL_VERSION:
        raise ProtocolError(f"unsupported protocol version {version!r}")
    return document


def _resolve_target(relative_file: str) -> Path | None:
    """Resolve a relative staged path, refusing anything outside the workspace."""
    candidate = PurePosixPath(relative_file)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    root = WORKSPACE.resolve()
    resolved = (WORKSPACE / candidate).resolve()
    if not resolved.is_relative_to(root):
        return None
    if resolved.suffix != ".py" or not resolved.is_file():
        return None
    return resolved


def _decode_arguments(invocation: dict[str, Any]) -> tuple[list[Any], dict[str, Any]]:
    """Decode the argument payload into positional and keyword Python values."""
    raw_args = invocation.get("args")
    raw_kwargs = invocation.get("kwargs")
    if not isinstance(raw_args, list):
        raise ProtocolError("invocation requires an 'args' array")
    if not isinstance(raw_kwargs, dict):
        raise ProtocolError("invocation requires a 'kwargs' object")
    positional = [decode_value(item).to_python() for item in raw_args]
    keywords = {str(name): decode_value(item).to_python() for name, item in raw_kwargs.items()}
    return positional, keywords


def _load_callable(module_path: Path, qualified_name: str) -> Callable[..., object]:
    """Import the staged module and return the selected callable.

    Importing is where a module's top-level statements run, which is why it
    happens here and only here.
    """
    import importlib.util

    # Appended, not prepended: a staged module must not be able to shadow a
    # standard-library module the worker (or the target) relies on. The codec is
    # already imported by this point, so this cannot affect the protocol either.
    staged_parent = str(module_path.parent)
    if staged_parent not in sys.path:
        sys.path.append(staged_parent)
    try:
        spec = importlib.util.spec_from_file_location(module_path.stem, module_path)
        if spec is None or spec.loader is None:
            raise ImportFailure(f"no import spec for {module_path.name}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except ImportFailure:
        raise
    except BaseException as exc:  # noqa: BLE001 - observation, not handling
        raise ImportFailure(f"{type(exc).__name__}: {exc}") from exc

    target: object = module
    for part in qualified_name.split("."):
        target = getattr(target, part, None)
        if target is None:
            raise TargetMissing(f"no attribute {qualified_name!r} in {module_path.name}")
    if not callable(target):
        raise TargetMissing(f"{qualified_name!r} is not callable")
    return target


def _encode_return(returned: object) -> dict[str, Any]:
    """Describe the returned value, or report it as opaque without serializing it."""
    try:
        encoded = _encode_value(returned)
    except ProtocolError:
        return _opaque(returned)
    return _outcome("returned", return_value=encode_value(encoded))


def _encode_value(value: object) -> TypedValue:
    """Encode a returned value, refusing anything outside the supported types.

    Exact type checks are used throughout so that a subclass of ``int`` or
    ``str`` with a custom ``__repr__`` is refused rather than described by
    invoking user code.
    """
    if value is None:
        return TypedValue(kind=ValueKind.NONE)
    if type(value) is bool:
        return TypedValue(kind=ValueKind.BOOLEAN, scalar="true" if value else "false")
    if type(value) is int:
        return TypedValue(kind=ValueKind.INTEGER, scalar=str(value))
    if type(value) is float:
        return TypedValue(kind=ValueKind.FLOAT, scalar=encode_float(value))
    if type(value) is str:
        return TypedValue(kind=ValueKind.STRING, scalar=value)
    if type(value) is list:
        return TypedValue(kind=ValueKind.LIST, items=tuple(_encode_value(item) for item in value))
    if type(value) is tuple:
        return TypedValue(kind=ValueKind.TUPLE, items=tuple(_encode_value(item) for item in value))
    if type(value) is set:
        items = tuple(_encode_value(item) for item in value)
        for item in items:
            if item.kind in (ValueKind.LIST, ValueKind.TUPLE, ValueKind.SET, ValueKind.MAPPING):
                raise ProtocolError("set element is not representable")
        return TypedValue(kind=ValueKind.SET, items=items)
    if type(value) is dict:
        return TypedValue(
            kind=ValueKind.MAPPING,
            pairs=tuple((_encode_value(key), _encode_value(item)) for key, item in value.items()),
        )
    raise ProtocolError(f"{type(value).__name__} is not representable")


def _opaque(value: object) -> dict[str, Any]:
    """Report an unrepresentable return without pickling, repr-ing, or guessing."""
    name = type(value).__name__[:MAX_OPAQUE_TYPE_LENGTH]
    return _outcome("returned", opaque_return=f"returned {name} (not representable)")


def _outcome(
    outcome: str,
    *,
    detail: str = "",
    exception_type: str | None = None,
    exception_message: str | None = None,
    exception_module: str | None = None,
    exception_qualname: str | None = None,
    exception_builtin: bool | None = None,
    exception_builtin_lineage: list[str] | None = None,
    return_value: dict[str, Any] | None = None,
    opaque_return: str | None = None,
) -> dict[str, Any]:
    document: dict[str, Any] = {"outcome": outcome}
    if detail:
        document["detail"] = _clip(detail)
    if exception_type is not None:
        document["exception_type"] = exception_type[:MAX_OPAQUE_TYPE_LENGTH]
    if exception_message is not None:
        # Clipped here as well as at the call site, so the bound is a property of
        # the document builder rather than of whoever remembered to call _clip.
        document["exception_message"] = _clip(exception_message)
    # Phase 7A exception identity. Every field is optional and purely additive:
    # a document without them is still a valid, decodable result.
    if exception_module is not None:
        document["exception_module"] = exception_module[:MAX_IDENTITY_FIELD_LENGTH]
    if exception_qualname is not None:
        document["exception_qualname"] = exception_qualname[:MAX_IDENTITY_FIELD_LENGTH]
    if exception_builtin is not None:
        document["exception_builtin"] = exception_builtin
    if exception_builtin_lineage is not None:
        document["exception_builtin_lineage"] = [
            name[:MAX_IDENTITY_FIELD_LENGTH] for name in exception_builtin_lineage
        ][:MAX_BUILTIN_LINEAGE]
    if return_value is not None:
        document["return_value"] = return_value
    if opaque_return is not None:
        document["opaque_return"] = opaque_return
    return document


def _failure(outcome: str, detail: str) -> dict[str, Any]:
    """A result document for a failure that happened before any target code ran."""
    return {
        "protocol_version": PROTOCOL_VERSION,
        "execution_id": "unknown",
        "nonce": "",
        "outcome": outcome,
        "detail": _clip(detail),
    }


def _clip(text: str) -> str:
    """Clip text to the protocol's detail budget."""
    return text[:MAX_DETAIL_LENGTH]


if __name__ == "__main__":
    raise SystemExit(main())
