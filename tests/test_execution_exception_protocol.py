"""Protocol compatibility and safety for the Phase 7A exception-identity fields.

The result document gained four optional fields. This file pins exactly what that
means in practice: an older document still decodes, a malformed one is rejected
rather than trusted, and nothing about the change re-introduces a deserializer, a
literal-evaluator, or a way to reconstruct an exception object on the host.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from edgehunter.execution.codec import PROTOCOL_VERSION, ProtocolError, decode_document, dumps
from edgehunter.execution.models import OutcomeKind
from edgehunter.execution.repository import _RESULT_KEYS, _observation_from
from edgehunter.runtime import worker

_V1_RAISED = {
    "protocol_version": PROTOCOL_VERSION,
    "execution_id": "abc",
    "nonce": "n",
    "outcome": "raised",
    "exception_type": "ValueError",
    "exception_message": "bad",
    "duration_seconds": 0.1,
    "captured_stdout": "",
    "captured_stderr": "",
    "stdout_truncated": False,
    "stderr_truncated": False,
    "native_output_discarded": True,
}


def _v2(**extra: object) -> dict[str, object]:
    return {**_V1_RAISED, **extra}


class TestBackwardCompatibility:
    def test_a_v1_document_still_decodes_without_the_new_fields(self) -> None:
        observation = _observation_from(_V1_RAISED)

        assert observation.outcome is OutcomeKind.RAISED
        assert observation.exception_type == "ValueError"
        # Absent means "never observed", which is not the same as False.
        assert observation.exception_module is None
        assert observation.exception_qualname is None
        assert observation.exception_builtin is None
        assert observation.exception_builtin_lineage is None

    def test_a_v1_document_still_passes_the_key_allowlist(self) -> None:
        assert set(_V1_RAISED) <= _RESULT_KEYS

    def test_the_new_fields_are_in_the_key_allowlist(self) -> None:
        assert {
            "exception_module",
            "exception_qualname",
            "exception_builtin",
            "exception_builtin_lineage",
        } <= _RESULT_KEYS

    def test_the_protocol_version_is_unchanged_and_old_documents_are_accepted(self) -> None:
        # The change is additive: no required field changed type or meaning, so a
        # version bump would claim an incompatibility that does not exist and would
        # reject evidence this release can still read.
        assert PROTOCOL_VERSION == 1
        assert decode_document(dumps(_V1_RAISED))["outcome"] == "raised"
        assert decode_document(dumps(_v2(exception_builtin=True)))["outcome"] == "raised"

    def test_the_new_fields_round_trip(self) -> None:
        document = _v2(
            exception_module="app.errors",
            exception_qualname="InvalidInput",
            exception_builtin=False,
            exception_builtin_lineage=["Exception", "BaseException"],
        )

        observation = _observation_from(decode_document(dumps(document)))

        assert observation.exception_module == "app.errors"
        assert observation.exception_qualname == "InvalidInput"
        assert observation.exception_builtin is False
        assert observation.exception_builtin_lineage == ("Exception", "BaseException")

    def test_an_unsupported_protocol_version_is_still_rejected(self) -> None:
        for version in (0, 2, 99, "1", None, True):
            document = {**_V1_RAISED, "protocol_version": version}
            with pytest.raises(ProtocolError):
                decode_document(dumps(document))

    def test_a_non_raised_outcome_carries_no_exception_identity(self) -> None:
        document = {**_V1_RAISED, "outcome": "returned", "return_value": {"kind": "none"}}
        observation = _observation_from(document)

        assert observation.outcome is OutcomeKind.RETURNED
        assert observation.exception_builtin is None
        assert observation.exception_builtin_lineage is None


class TestMalformedMetadataIsRefused:
    @pytest.mark.parametrize("value", ["true", 1, 0, [], {}, None])
    def test_a_non_boolean_builtin_flag_becomes_unknown(self, value: object) -> None:
        """Never coerced: a hostile document must not manufacture a verdict."""

        observation = _observation_from(_v2(exception_builtin=value))

        assert observation.exception_builtin is None

    @pytest.mark.parametrize(
        "value",
        ["ValueError", 1, {"a": 1}, ["ValueError", 2], [["nested"]]],
    )
    def test_a_malformed_lineage_is_dropped_entirely(self, value: object) -> None:
        """A partly-trusted inheritance chain is worse than none.

        It could make a class that is not a subclass look like one, so the whole
        lineage is discarded rather than repaired.
        """

        observation = _observation_from(_v2(exception_builtin_lineage=value))

        assert observation.exception_builtin_lineage is None

    @pytest.mark.parametrize("value", [1, [], {}, True])
    def test_a_non_string_identity_field_is_dropped(self, value: object) -> None:
        observation = _observation_from(_v2(exception_module=value, exception_qualname=value))

        assert observation.exception_module is None
        assert observation.exception_qualname is None

    def test_malformed_metadata_never_raises_out_of_the_decoder(self) -> None:
        hostile = _v2(
            exception_module={"nested": ["deep"]},
            exception_qualname=[1, 2, 3],
            exception_builtin="yes",
            exception_builtin_lineage=[None, "ValueError"],
        )

        observation = _observation_from(hostile)

        assert observation.outcome is OutcomeKind.RAISED
        assert observation.exception_builtin is None
        assert observation.exception_builtin_lineage is None

    def test_an_empty_lineage_list_is_kept_as_an_empty_tuple(self) -> None:
        observation = _observation_from(_v2(exception_builtin_lineage=[]))

        assert observation.exception_builtin_lineage == ()


def _module_path(module: object) -> Path:
    """The file a module was loaded from, for source-level safety checks."""
    path = getattr(module, "__file__", None)
    assert isinstance(path, str)
    return Path(path)


def _imported_names(module: object) -> set[str]:
    """Every module name imported anywhere in a module's AST."""
    tree = ast.parse(_module_path(module).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def _attribute_reads(module: object) -> set[str]:
    """Every ``<expr>.<attr>`` read anywhere in a module's AST."""
    tree = ast.parse(_module_path(module).read_text(encoding="utf-8"))
    return {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}


class TestNoDeserialization:
    def test_the_worker_imports_no_deserializer(self) -> None:
        """Identity is metadata only: nothing reconstructs an exception object."""

        assert _imported_names(worker) & {"pickle", "marshal", "shelve", "dill"} == set()

    def test_the_worker_evaluates_nothing(self) -> None:
        tree = ast.parse(_module_path(worker).read_text(encoding="utf-8"))
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert called & {"eval", "exec", "compile"} == set()

    def test_the_worker_calls_no_method_on_the_exception_or_its_class(self) -> None:
        """Only ``type.__getattribute__`` may read a class attribute.

        A custom ``__repr__`` or a metaclass ``__getattribute__`` is target code,
        so the worker must not reach it while describing a failure.
        """
        tree = ast.parse(_module_path(worker).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr == "__getattribute__":
                owner = node.func.value
                # Only `type.__getattribute__(...)` is permitted.
                assert isinstance(owner, ast.Name) and owner.id == "type", ast.dump(node)

    def test_the_host_decoder_never_selects_a_class_to_construct(self) -> None:
        """A name inside a document must never choose a class to build."""
        import edgehunter.execution.repository as repository

        tree = ast.parse(_module_path(repository).read_text(encoding="utf-8"))
        # The decode helpers are the boundary: whatever they do is what a hostile
        # document can reach. `_decode_outcome` is deliberately not listed --
        # its `getattr` calls read the *subprocess* result object, which Docker
        # produced, and all of its document handling is delegated to
        # `decode_document` and `_observation_from`, both of which are listed.
        for function in (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name
            in {
                "_observation_from",
                "_optional_str",
                "_optional_bool",
                "_optional_str_tuple",
            }
        ):
            called = {
                node.func.id
                for node in ast.walk(function)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            }
            assert called & {"getattr", "__import__", "eval", "exec", "compile"} == set(), (
                function.name,
                called,
            )
        assert _imported_names(repository) & {"importlib", "pickle"} == set()

    def test_a_document_is_strict_json_with_no_object_reconstruction(self) -> None:
        payload = _v2(exception_qualname="InvalidInput", exception_builtin=False)
        text = dumps(payload)

        # Round-trips as plain JSON data, with no class markers.
        assert json.loads(text) == payload
        assert "!!python" not in text

    def test_the_worker_uses_type_getattribute_for_class_metadata(self) -> None:
        assert "__getattribute__" in _attribute_reads(worker)


class TestBoundedIdentity:
    def test_identity_fields_are_clipped(self) -> None:
        class Wide(Exception):
            pass

        Wide.__qualname__ = "Q" * 5_000
        Wide.__module__ = "M" * 5_000

        identity = worker._exception_identity(Wide())

        assert identity["exception_qualname"] is not None
        assert len(identity["exception_qualname"]) == worker.MAX_IDENTITY_FIELD_LENGTH
        assert identity["exception_module"] is not None
        assert len(identity["exception_module"]) == worker.MAX_IDENTITY_FIELD_LENGTH

    def test_the_builtin_lineage_is_bounded(self) -> None:
        identity = worker._exception_identity(ValueError("x"))

        lineage = identity["exception_builtin_lineage"]
        assert len(lineage) <= worker.MAX_BUILTIN_LINEAGE

    def test_the_exception_message_stays_bounded(self) -> None:
        message = "x" * (worker.MAX_DETAIL_LENGTH * 2)
        document = worker._outcome("raised", exception_type="ValueError", exception_message=message)

        assert document["exception_message"] is not None
        assert len(document["exception_message"]) == worker.MAX_DETAIL_LENGTH

    def test_a_very_deep_inheritance_chain_is_truncated(self) -> None:
        cls: type = ValueError
        for _ in range(40):
            cls = type("Derived", (cls,), {})
        cls.__module__ = "deep"

        identity = worker._exception_identity(cls())

        assert identity["exception_builtin"] is False
        lineage = identity["exception_builtin_lineage"]
        assert len(lineage) <= worker.MAX_BUILTIN_LINEAGE
        # The first entry is always the nearest builtin ancestor.
        assert lineage[0] == "ValueError"


class TestNonceAndSnapshotUnchanged:
    def test_the_nonce_binding_is_still_echoed(self) -> None:
        document = worker._failure("protocol_error", "bad request")
        assert document["nonce"] == ""
        assert document["execution_id"] == "unknown"
        assert document["protocol_version"] == PROTOCOL_VERSION

    def test_the_source_snapshot_verification_is_untouched_by_this_change(self) -> None:
        from edgehunter.evaluation.repository import SourceSnapshot

        assert hasattr(SourceSnapshot, "verify")
