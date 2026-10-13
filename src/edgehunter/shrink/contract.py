"""The versioned JSON contract a user may explicitly select to author an oracle.

A contract is **data**, parsed with the standard library's JSON reader and never
with ``eval``, ``exec``, or a deserializer. Every field is validated for type,
length, and range before it is used, and every rejection happens before any
target is executed, so a malformed contract can never cause a container to start.

The format is deliberately one contract family wide:

    when <one int parameter> <lt|le|gt|ge> <one int literal>
    the call is expected to raise <one real builtin exception class>

It is a *user declaration*, not a proven fact. It binds to a specific source
revision by SHA-256, and a contract whose target has changed is refused rather
than rebound. Nothing is inferred: a production ``if x < 0: raise ValueError`` is
not converted into a contract here, and an existing ``assert f(1) == 2`` is never
turned into a formula.
"""

from __future__ import annotations

import builtins
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Final

from edgehunter.execution.eligibility import check_relative_path
from edgehunter.shrink.domain import DomainOperator, IntegerDomain
from edgehunter.shrink.models import MAX_SIGNED_INT, MIN_SIGNED_INT

CONTRACT_VERSION: Final[int] = 1

# Bounds. A contract file is data the shrinker reads into memory, so its size is
# capped well below the protocol's 64 KiB document budget.
MAX_CONTRACT_FILE_BYTES: Final[int] = 32 * 1024
MAX_CONTRACT_ID_LENGTH: Final[int] = 128
MAX_TARGET_LENGTH: Final[int] = 512
MAX_PARAMETER_LENGTH: Final[int] = 128
MAX_EXCEPTION_LENGTH: Final[int] = 128
MAX_CONTRACTS_PER_FILE: Final[int] = 64

# Validation is written as explicit character predicates rather than compiled
# patterns. Two reasons: this module handles untrusted user data, where a short
# check a reader can verify by eye is worth more than a pattern whose escaping has
# to be trusted; and the whole validator then depends on no pattern engine.
_HEX_DIGITS: Final[frozenset[str]] = frozenset("0123456789abcdef")
_DIGITS: Final[frozenset[str]] = frozenset("0123456789")
_LABEL_CHARACTERS: Final[frozenset[str]] = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
)
_IDENTIFIER_FIRST: Final[frozenset[str]] = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_"
)
_IDENTIFIER_REST: Final[frozenset[str]] = _LABEL_CHARACTERS

_EXPECTATION_KIND: Final[str] = "raises_builtin"

_ROOT_KEYS: Final[frozenset[str]] = frozenset({"version", "contracts"})
_CONTRACT_KEYS: Final[frozenset[str]] = frozenset(
    {"id", "target", "source_sha256", "parameter", "domain", "expectation"}
)
_DOMAIN_KEYS: Final[frozenset[str]] = frozenset({"operator", "integer"})
_EXPECTATION_KEYS: Final[frozenset[str]] = frozenset({"kind", "exception"})


class ContractError(ValueError):
    """A contract file, or one contract inside it, is not acceptable."""


@dataclass(frozen=True, slots=True)
class RaisesBuiltinExpectation:
    """The single supported expectation: this real builtin exception is raised."""

    exception: str

    def describe(self) -> str:
        return f"raise builtin {self.exception}"


@dataclass(frozen=True, slots=True)
class Contract:
    """One validated, user-declared reusable oracle."""

    contract_id: str
    target_file: str
    """Repository-relative POSIX path of the analyzed target file."""

    target_name: str
    """Source-local qualified name of the callable."""

    source_sha256: str
    parameter: str
    domain: IntegerDomain
    expectation: RaisesBuiltinExpectation

    @property
    def target_spec(self) -> str:
        """The ``path::name`` identity this contract binds to."""

        return f"{self.target_file}::{self.target_name}"

    def describe(self) -> str:
        return (
            f"when {self.parameter} {self.domain.describe()}, "
            f"the call is expected to {self.expectation.describe()}"
        )

    @property
    def is_satisfiable(self) -> bool:
        """False when no signed 64-bit integer satisfies the declared domain."""

        return not self.domain.is_empty()


@dataclass(frozen=True, slots=True)
class ContractFile:
    """One parsed contract file, with the file's own fingerprint recorded."""

    path: Path
    sha256: str
    version: int
    contracts: tuple[Contract, ...]

    def select(self, contract_id: str) -> Contract:
        """Return exactly one contract by id, or refuse.

        Ambiguity is impossible because ids are unique within a file, and that
        uniqueness is validated while parsing rather than assumed here.
        """

        for contract in self.contracts:
            if contract.contract_id == contract_id:
                return contract
        known = ", ".join(sorted(c.contract_id for c in self.contracts)) or "<none>"
        raise ContractError(
            f"no contract with id {contract_id!r} in {self.path}; available ids: {known}"
        )


def read_contract_file(path: Path) -> ContractFile:
    """Read, bound, parse, and validate one contract file.

    Every refusal here happens before any target is staged or executed, which is
    what makes "do not execute a target merely to discover its contract is
    malformed" true by construction.
    """

    if not path.is_file():
        raise ContractError(f"contract file does not exist: {path}")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ContractError(f"contract file could not be read: {exc}") from exc
    if len(raw) > MAX_CONTRACT_FILE_BYTES:
        raise ContractError(
            f"contract file is {len(raw)} bytes, above the {MAX_CONTRACT_FILE_BYTES} byte limit"
        )
    document = _parse_json_object(raw, path)
    return _build_contract_file(document, path)


def _is_sha256(value: object) -> bool:
    """Exactly 64 lowercase hexadecimal characters, checked explicitly.

    Written as a predicate rather than a compiled pattern on purpose: this module
    is a security boundary for user-supplied data, and a four-line character check
    can be read and verified by eye where a pattern's escaping cannot. It also
    keeps the whole validator free of any pattern engine.
    """

    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(character in _HEX_DIGITS for character in value)


def _is_decimal_integer(value: object) -> bool:
    """A plain decimal integer literal, with no sign variants or digit tricks.

    Refuses a leading ``+``, leading zeros, whitespace, ``0x``/``0o``/``0b``
    prefixes, exponent notation, and a bare ``-``. What survives is exactly what
    ``int(text, 10)`` would read back as the same digits.
    """

    if not isinstance(value, str) or not value:
        return False
    digits = value[1:] if value.startswith("-") else value
    if not digits or not all(character in _DIGITS for character in digits):
        return False
    return digits == "0" or digits[0] != "0"


def _is_contract_id(value: object) -> bool:
    """A contract id is a user-chosen *label*, so hyphens are allowed."""

    if not isinstance(value, str) or not value:
        return False
    return all(character in _LABEL_CHARACTERS for character in value)


def _is_identifier(value: object) -> bool:
    """A plain ASCII Python identifier, and nothing looser.

    Applied to a parameter name, an exception class name, and a target name,
    because all three are looked up in code.
    """

    if not isinstance(value, str) or not value:
        return False
    if value[0] not in _IDENTIFIER_FIRST:
        return False
    return all(character in _IDENTIFIER_REST for character in value[1:])


def _parse_json_object(raw: bytes, path: Path) -> dict[str, Any]:
    """Parse strict JSON, refusing duplicate keys anywhere in the document.

    ``json.loads`` keeps the last value for a repeated key, which would let
    ``{"integer": "0", "integer": "999999"}`` be read as the second one while a
    reviewer reads the first. The hook below turns that into an error.
    """

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractError(f"contract file is not valid UTF-8: {exc}") from exc

    def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        seen: set[str] = set()
        for key, _ in pairs:
            if key in seen:
                raise ContractError(f"contract file has a duplicate key {key!r}")
            seen.add(key)
        return dict(pairs)

    try:
        document = json.loads(text, object_pairs_hook=_reject_duplicates)
    except ContractError:
        raise
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ContractError(f"contract file is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise ContractError("contract file must be a JSON object")
    return document


def _build_contract_file(document: dict[str, Any], path: Path) -> ContractFile:
    """Validate the envelope and every contract in it."""

    _reject_unknown_keys(document, _ROOT_KEYS, "contract file")
    version = document.get("version")
    # `isinstance(True, int)` is True, so a boolean version must be refused first.
    if not isinstance(version, int) or isinstance(version, bool):
        raise ContractError("contract 'version' must be an integer")
    if version != CONTRACT_VERSION:
        raise ContractError(
            f"unsupported contract version {version}; this release understands {CONTRACT_VERSION}"
        )

    raw_contracts = document.get("contracts")
    if not isinstance(raw_contracts, list):
        raise ContractError("contract 'contracts' must be an array")
    if not raw_contracts:
        raise ContractError("contract file declares no contracts")
    if len(raw_contracts) > MAX_CONTRACTS_PER_FILE:
        raise ContractError(
            f"contract file declares {len(raw_contracts)} contracts, above the "
            f"{MAX_CONTRACTS_PER_FILE} limit"
        )

    contracts: list[Contract] = []
    seen_ids: set[str] = set()
    for index, entry in enumerate(raw_contracts):
        contract = _build_contract(entry, index)
        if contract.contract_id in seen_ids:
            raise ContractError(f"duplicate contract id {contract.contract_id!r}")
        seen_ids.add(contract.contract_id)
        contracts.append(contract)

    return ContractFile(
        path=path,
        sha256=_file_sha256(path),
        version=version,
        contracts=tuple(contracts),
    )


def _build_contract(entry: object, index: int) -> Contract:
    """Validate one contract object."""

    if not isinstance(entry, dict):
        raise ContractError(f"contract at index {index} must be a JSON object")
    _reject_unknown_keys(entry, _CONTRACT_KEYS, f"contract at index {index}")

    contract_id = _require_id(entry, "id", index)
    target_file, target_name = _parse_target(entry, index)
    source_sha256 = _require_sha256(entry, index)
    parameter = _require_parameter(entry, index)
    domain = _build_domain(entry.get("domain"), index)
    expectation = _build_expectation(entry.get("expectation"), index)
    return Contract(
        contract_id=contract_id,
        target_file=target_file,
        target_name=target_name,
        source_sha256=source_sha256,
        parameter=parameter,
        domain=domain,
        expectation=expectation,
    )


def _parse_target(entry: dict[str, Any], index: int) -> tuple[str, str]:
    """Split and validate a ``path::name`` target spec.

    The path is validated with the *existing* execution path checker rather than
    a new one, so a contract cannot be a way around the escape rules that guard
    staged source.
    """

    target = entry.get("target")
    if not isinstance(target, str) or not target:
        raise ContractError(f"contract at index {index} needs a non-empty 'target' string")
    if len(target) > MAX_TARGET_LENGTH:
        raise ContractError(f"contract target exceeds {MAX_TARGET_LENGTH} characters")
    if "::" not in target:
        raise ContractError(
            f"contract target {target!r} must be 'relative/path.py::qualified_name'"
        )
    raw_file, _, name = target.rpartition("::")
    raw_file = raw_file.strip()
    name = name.strip()
    if not raw_file or not name:
        raise ContractError(f"contract target {target!r} has an empty path or name")

    if PurePosixPath(raw_file).is_absolute() or PureWindowsPath(raw_file).is_absolute():
        raise ContractError(
            f"contract target path must be relative, got {raw_file!r}; a contract cannot name an "
            "absolute location"
        )
    normalized = PurePosixPath(raw_file.replace("\\", "/")).as_posix()
    verdict = check_relative_path(normalized)
    if verdict is not None:
        raise ContractError(
            f"contract target path {raw_file!r} is refused by the execution path rules: "
            f"{verdict.detail}"
        )
    if not normalized.endswith(".py"):
        raise ContractError(f"contract target path must name a .py file, got {normalized!r}")
    if not _is_identifier(name):
        raise ContractError(
            f"contract target name {name!r} must be a source-local identifier such as 'validate'"
        )
    return normalized, name


def _require_id(entry: dict[str, Any], field: str, index: int) -> str:
    value = entry.get(field)
    if not isinstance(value, str) or not value:
        raise ContractError(f"contract at index {index} needs a non-empty {field!r}")
    if len(value) > MAX_CONTRACT_ID_LENGTH:
        raise ContractError(f"contract {field!r} exceeds {MAX_CONTRACT_ID_LENGTH} characters")
    if not _is_contract_id(value):
        raise ContractError(
            f"contract {field} {value!r} may contain only letters, digits, '-' and '_'"
        )
    return value


def _require_parameter(entry: dict[str, Any], index: int) -> str:
    parameter = entry.get("parameter")
    if not isinstance(parameter, str) or not parameter:
        raise ContractError(f"contract at index {index} needs a non-empty 'parameter'")
    if len(parameter) > MAX_PARAMETER_LENGTH:
        raise ContractError(f"contract 'parameter' exceeds {MAX_PARAMETER_LENGTH} characters")
    if not _is_identifier(parameter):
        raise ContractError(
            f"contract parameter {parameter!r} must be a plain identifier; a dotted or "
            "subscripted parameter is not supported"
        )
    return parameter


def _require_sha256(entry: dict[str, Any], index: int) -> str:
    value = entry.get("source_sha256")
    if not isinstance(value, str):
        raise ContractError(f"contract at index {index} needs a string 'source_sha256'")
    if not _is_sha256(value):
        raise ContractError(
            "contract 'source_sha256' must be exactly 64 lowercase hexadecimal characters; "
            f"got {value!r}"
        )
    return value


def _build_domain(raw: object, index: int) -> IntegerDomain:
    """Validate the declared integer domain."""

    if not isinstance(raw, dict):
        raise ContractError(f"contract at index {index} needs a 'domain' object")
    _reject_unknown_keys(raw, _DOMAIN_KEYS, f"domain of contract at index {index}")

    operator_text = raw.get("operator")
    if not isinstance(operator_text, str):
        raise ContractError("domain 'operator' must be a string")
    try:
        operator = DomainOperator(operator_text)
    except ValueError as exc:
        supported = ", ".join(op.value for op in DomainOperator)
        raise ContractError(
            f"unsupported domain operator {operator_text!r}; supported: {supported}"
        ) from exc

    reference = _require_integer(raw.get("integer"), f"domain of contract at index {index}")
    return IntegerDomain(operator=operator, reference=reference)


def _require_integer(value: object, where: str) -> int:
    """Accept only a decimal integer literal written as a string.

    A JSON number would let ``true`` and ``1.0`` through, and an arbitrary
    precision integer would let a file choose the planner's allocation, so the
    value is required to be a string and then bounded to signed 64-bit.
    """

    if isinstance(value, bool):
        raise ContractError(
            f"{where}: 'integer' must be an integer, not a boolean; a boolean is not a bound"
        )
    if isinstance(value, int):
        raise ContractError(
            f"{where}: 'integer' must be written as a decimal string such as \"-1\" so that a "
            "JSON number cannot smuggle in a float or arbitrary precision"
        )
    if not isinstance(value, str):
        raise ContractError(f"{where}: 'integer' must be a decimal string")
    if not _is_decimal_integer(value):
        raise ContractError(
            f'{where}: \'integer\' {value!r} must be a plain decimal integer such as "0" or "-5"'
        )
    if len(value) > 20:
        raise ContractError(f"{where}: 'integer' {value!r} is too long to be a signed 64-bit value")
    number = int(value, 10)
    if not MIN_SIGNED_INT <= number <= MAX_SIGNED_INT:
        raise ContractError(
            f"{where}: 'integer' {value!r} is outside the signed 64-bit range "
            f"[{MIN_SIGNED_INT}, {MAX_SIGNED_INT}]"
        )
    return number


def _build_expectation(raw: object, index: int) -> RaisesBuiltinExpectation:
    """Validate the single supported expectation kind."""

    if not isinstance(raw, dict):
        raise ContractError(f"contract at index {index} needs an 'expectation' object")
    _reject_unknown_keys(raw, _EXPECTATION_KEYS, f"expectation of contract at index {index}")

    kind = raw.get("kind")
    if kind != _EXPECTATION_KIND:
        raise ContractError(
            f"unsupported expectation kind {kind!r}; this release supports only "
            f"{_EXPECTATION_KIND!r}"
        )
    exception = raw.get("exception")
    if not isinstance(exception, str) or not exception:
        raise ContractError("expectation 'exception' must be a non-empty string")
    if len(exception) > MAX_EXCEPTION_LENGTH:
        raise ContractError(f"expectation 'exception' exceeds {MAX_EXCEPTION_LENGTH} characters")
    if not _is_identifier(exception):
        raise ContractError(
            f"expectation 'exception' {exception!r} must be a plain class name; a dotted name is "
            "not resolved and a custom class cannot be expected"
        )
    if not _is_builtin_exception(exception):
        raise ContractError(
            f"expectation 'exception' {exception!r} is not a real Python builtin exception class; "
            "a custom class cannot be expected because its identity is never resolved"
        )
    return RaisesBuiltinExpectation(exception=exception)


def _is_builtin_exception(name: str) -> bool:
    """True when ``builtins`` really defines this exception class.

    The same identity test the evaluator uses, not a name spelling, so a
    contract cannot name something that merely looks like a builtin.
    """

    candidate = getattr(builtins, name, None)
    return isinstance(candidate, type) and issubclass(candidate, BaseException)


def _reject_unknown_keys(document: dict[str, Any], allowed: frozenset[str], where: str) -> None:
    """Refuse any key the schema does not define.

    An unknown key is either a typo or an attempt to smuggle in a field this
    release would silently ignore. Both are refused, so a contract cannot appear
    to mean something the evaluator does not implement.
    """

    unknown = sorted(set(document) - allowed)
    if unknown:
        raise ContractError(
            f"{where} has unrecognized key(s): {', '.join(unknown)}; "
            f"supported: {', '.join(sorted(allowed))}"
        )


def _file_sha256(path: Path) -> str:
    """The SHA-256 of the contract file itself, recorded in every result."""

    from edgehunter.fingerprint import fingerprint_file

    return fingerprint_file(path)
