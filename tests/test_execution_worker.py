"""Protocol behaviour of the trusted worker, exercised on the host.

The worker is the only module allowed to import target code, so its decision
logic is unit-tested directly. These tests never load a real target module; the
path and decode helpers are checked in isolation, and the real invocation path is
covered by the Docker integration suite.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from edgehunter.execution.codec import PROTOCOL_VERSION, ProtocolError, ValueKind, encode_float
from edgehunter.runtime import worker


@pytest.fixture
def workspace(tmp_path: Path) -> Iterator[Path]:
    """Point the worker at a temporary staged tree."""
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "module.py").write_text("def f(x):\n    return x\n", encoding="utf-8")
    (root / "pkg").mkdir()
    (root / "pkg" / "nested.py").write_text("def g():\n    return 1\n", encoding="utf-8")
    monkey = pytest.MonkeyPatch()
    monkey.setattr(worker, "WORKSPACE", root)
    try:
        yield root
    finally:
        monkey.undo()


class TestBoundedStream:
    def test_short_output_is_kept_whole(self) -> None:
        stream = worker._BoundedStream()

        stream.write("hello ")
        stream.write("world")

        assert stream.getvalue() == "hello world"
        assert stream.truncated is False

    def test_output_beyond_the_cap_is_discarded(self) -> None:
        stream = worker._BoundedStream(limit=8)

        accepted = stream.write("abcdefghijklmnop")

        assert stream.getvalue() == "abcdefgh"
        assert stream.truncated is True
        # A discarding sink reports a full write so the target never errors.
        assert accepted == 16

    def test_flooding_never_grows_past_the_cap(self) -> None:
        stream = worker._BoundedStream(limit=64)

        for _ in range(10_000):
            stream.write("x" * 128)

        assert len(stream.getvalue().encode("utf-8")) == 64
        assert stream.truncated is True

    def test_multibyte_truncation_stays_valid_text(self) -> None:
        stream = worker._BoundedStream(limit=5)

        stream.write("naïve✓")

        captured = stream.getvalue()
        assert captured.encode("utf-8") == captured.encode("utf-8")
        assert len(captured.encode("utf-8")) == 5
        assert stream.truncated is True

    def test_non_string_write_is_refused(self) -> None:
        stream = worker._BoundedStream()

        with pytest.raises(TypeError):
            stream.write(b"bytes")  # type: ignore[arg-type]


class TestEncodeReturn:
    @pytest.mark.parametrize(
        ("value", "kind"),
        [
            (None, ValueKind.NONE),
            (True, ValueKind.BOOLEAN),
            (False, ValueKind.BOOLEAN),
            (7, ValueKind.INTEGER),
            (1.5, ValueKind.FLOAT),
            ("text", ValueKind.STRING),
            ([1, 2], ValueKind.LIST),
            ((1, 2), ValueKind.TUPLE),
            ({1, 2}, ValueKind.SET),
            ({"a": 1}, ValueKind.MAPPING),
        ],
    )
    def test_supported_returns_are_encoded(self, value: object, kind: ValueKind) -> None:
        document = worker._encode_return(value)

        assert document["outcome"] == "returned"
        assert document["return_value"]["kind"] == kind.value

    def test_special_floats_survive(self) -> None:
        for value in (float("nan"), float("inf"), float("-inf"), -0.0):
            document = worker._encode_return(value)
            assert document["return_value"]["value"] == encode_float(value)

    def test_nested_structures_are_encoded_recursively(self) -> None:
        document = worker._encode_return({"a": [1, (2, 3)]})
        mapping = document["return_value"]

        assert mapping["kind"] == ValueKind.MAPPING.value
        listed = mapping["entries"][0][1]
        assert listed["kind"] == ValueKind.LIST.value
        assert [item["kind"] for item in listed["items"]] == [
            ValueKind.INTEGER.value,
            ValueKind.TUPLE.value,
        ]
        nested_tuple = listed["items"][1]
        assert [item["value"] for item in nested_tuple["items"]] == ["2", "3"]

    def test_subclassed_int_is_not_encoded_as_an_integer(self) -> None:
        class Sneaky(int):
            def __repr__(self) -> str:
                return "SHOULD NOT APPEAR"

        document = worker._encode_return(Sneaky(3))

        assert "return_value" not in document
        assert document["opaque_return"] == "returned Sneaky (not representable)"
        assert "SHOULD NOT APPEAR" not in str(document)

    def test_custom_object_is_reported_opaque_not_serialized(self) -> None:
        class Dangerous:
            def __reduce__(self) -> str:
                return "os.system"

            def __repr__(self) -> str:
                return "LEAKED"

        document = worker._encode_return(Dangerous())

        assert document["outcome"] == "returned"
        assert document["opaque_return"] == "returned Dangerous (not representable)"
        assert "LEAKED" not in str(document)
        assert "os.system" not in str(document)

    def test_set_of_unhashable_elements_is_refused(self) -> None:
        document = worker._encode_return({(1, 2)})

        assert document["opaque_return"] == "returned set (not representable)"

    def test_non_finite_float_is_not_nan_from_host_side(self) -> None:
        encoded = worker._encode_value(float("nan"))

        assert encoded.kind is ValueKind.FLOAT
        assert encoded.scalar == "nan"
        # The wire form is text, never a bare float, so no JSON encoder can
        # produce the invalid JSON tokens ``NaN``/``Infinity``.
        assert isinstance(encoded.scalar, str)


class TestResolveTarget:
    def test_relative_file_resolves(self, workspace: Path) -> None:
        resolved = worker._resolve_target("module.py")

        assert resolved == (workspace / "module.py").resolve()

    def test_nested_relative_file_resolves(self, workspace: Path) -> None:
        resolved = worker._resolve_target("pkg/nested.py")

        assert resolved == (workspace / "pkg" / "nested.py").resolve()

    @pytest.mark.parametrize(
        "relative",
        ["../escape.py", "pkg/../../escape.py", "/etc/passwd", "", "module.txt"],
    )
    def test_unsafe_or_non_python_paths_are_refused(self, workspace: Path, relative: str) -> None:
        assert worker._resolve_target(relative) is None

    def test_missing_file_is_refused(self, workspace: Path) -> None:
        assert worker._resolve_target("absent.py") is None


class TestDecodeArguments:
    def test_positional_and_keyword_arguments_decode(self) -> None:
        args, kwargs = worker._decode_arguments(
            {
                "args": [{"kind": "integer", "value": "1"}],
                "kwargs": {"name": {"kind": "string", "value": "x"}},
            }
        )

        assert args == [1]
        assert kwargs == {"name": "x"}

    def test_missing_args_key_is_refused(self) -> None:
        with pytest.raises(ProtocolError):
            worker._decode_arguments({"kwargs": {}})

    def test_missing_kwargs_key_is_refused(self) -> None:
        with pytest.raises(ProtocolError):
            worker._decode_arguments({"args": []})

    def test_unknown_value_kind_is_refused(self) -> None:
        with pytest.raises(ProtocolError):
            worker._decode_arguments(
                {"args": [{"kind": "file", "path": "/etc/passwd"}], "kwargs": {}}
            )


class TestFailureDocument:
    def test_failure_records_the_protocol_version(self) -> None:
        document = worker._failure("protocol_error", "bad request")

        assert document["protocol_version"] == PROTOCOL_VERSION
        assert document["outcome"] == "protocol_error"
        assert document["detail"] == "bad request"

    def test_failure_detail_is_clipped(self) -> None:
        document = worker._failure("protocol_error", "x" * 10_000)

        assert len(document["detail"]) == worker.MAX_DETAIL_LENGTH
