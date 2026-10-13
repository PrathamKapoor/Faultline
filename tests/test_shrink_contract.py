"""Contract parsing, integer-domain arithmetic, and proposal generation.

Everything here is pure. No test in this file starts a container, and several
deliberately feed the parser something hostile: a duplicate key, a boolean where
an integer is declared, an out-of-range magnitude, a dotted exception name. The
point is that all of them are refused *by data validation*, before any target
could be staged, which is why a malformed contract can never cause a container to
start.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from edgehunter.shrink.contract import (
    MAX_CONTRACT_FILE_BYTES,
    ContractError,
    read_contract_file,
)
from edgehunter.shrink.domain import DomainError, DomainOperator, IntegerDomain
from edgehunter.shrink.models import MAX_SIGNED_INT, MIN_SIGNED_INT
from edgehunter.shrink.proposals import complexity, is_simpler, propose_values

VALID: dict[str, object] = {
    "version": 1,
    "contracts": [
        {
            "id": "negative-age",
            "target": "validators.py::validate_age",
            "source_sha256": "a" * 64,
            "parameter": "age",
            "domain": {"operator": "lt", "integer": "0"},
            "expectation": {"kind": "raises_builtin", "exception": "ValueError"},
        }
    ],
}


def _write(tmp_path: Path, document: object, name: str = "contracts.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _valid(**overrides: object) -> dict[str, object]:
    """A copy of the valid document with per-field overrides applied."""

    contract: dict[str, object] = dict(_base_contract())
    contract.update(overrides)
    return {"version": 1, "contracts": [contract]}


def _base_contract() -> dict[str, object]:
    contracts = VALID["contracts"]
    assert isinstance(contracts, list)
    first = contracts[0]
    assert isinstance(first, dict)
    return first


def _contract_at(document: dict[str, object], index: int = 0) -> dict[str, object]:
    """The contract object at ``index``, for tests that mutate one field."""

    contracts = document["contracts"]
    assert isinstance(contracts, list)
    entry = contracts[index]
    assert isinstance(entry, dict)
    return entry


class TestValidContract:
    def test_a_valid_contract_is_parsed_and_fully_described(self, tmp_path: Path) -> None:
        result = read_contract_file(_write(tmp_path, VALID))

        assert result.version == 1
        assert len(result.sha256) == 64
        contract = result.select("negative-age")
        assert contract.target_file == "validators.py"
        assert contract.target_name == "validate_age"
        assert contract.target_spec == "validators.py::validate_age"
        assert contract.parameter == "age"
        assert contract.domain.operator is DomainOperator.LT
        assert contract.domain.reference == 0
        assert contract.expectation.exception == "ValueError"
        assert contract.is_satisfiable is True
        assert contract.describe() == (
            "when age < 0, the call is expected to raise builtin ValueError"
        )

    def test_the_contract_file_records_its_own_fingerprint(self, tmp_path: Path) -> None:
        path = _write(tmp_path, VALID)
        result = read_contract_file(path)

        import hashlib

        assert result.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


class TestRejectedContracts:
    def test_an_unknown_version_is_refused(self, tmp_path: Path) -> None:
        path = _write(tmp_path, {"version": 2, "contracts": VALID["contracts"]})

        with pytest.raises(ContractError, match="unsupported contract version"):
            read_contract_file(path)

    def test_a_missing_version_is_refused(self, tmp_path: Path) -> None:
        path = _write(tmp_path, {"contracts": VALID["contracts"]})

        with pytest.raises(ContractError, match="'version' must be an integer"):
            read_contract_file(path)

    @pytest.mark.parametrize("field", ["id", "target", "source_sha256", "parameter"])
    def test_a_missing_required_field_is_refused(self, tmp_path: Path, field: str) -> None:
        document = _valid()
        _contract_at(document).pop(field, None)
        path = _write(tmp_path, document)

        with pytest.raises(ContractError):
            read_contract_file(path)

    def test_an_unknown_top_level_field_is_refused(self, tmp_path: Path) -> None:
        path = _write(tmp_path, {**VALID, "extra": 1})

        with pytest.raises(ContractError, match="unrecognized key"):
            read_contract_file(path)

    def test_an_unknown_contract_field_is_refused(self, tmp_path: Path) -> None:
        path = _write(tmp_path, _valid(confidence=0.97))

        with pytest.raises(ContractError, match="unrecognized key"):
            read_contract_file(path)

    def test_an_unknown_domain_field_is_refused(self, tmp_path: Path) -> None:
        document = _valid()
        _contract_at(document)["domain"] = {
            "operator": "lt",
            "integer": "0",
            "scale": 2,
        }
        path = _write(tmp_path, document)

        with pytest.raises(ContractError, match="unrecognized key"):
            read_contract_file(path)

    def test_a_duplicate_contract_id_is_refused(self, tmp_path: Path) -> None:
        document = _valid()
        document["contracts"] = [dict(_base_contract()), dict(_base_contract())]
        path = _write(tmp_path, document)

        with pytest.raises(ContractError, match="duplicate contract id"):
            read_contract_file(path)

    @pytest.mark.parametrize(
        "target",
        [
            "../outside.py::f",
            "/etc/passwd.py::f",
            "C:/Windows/system32/x.py::f",
            "pkg/../../escape.py::f",
        ],
    )
    def test_an_invalid_target_path_is_refused(self, tmp_path: Path, target: str) -> None:
        path = _write(tmp_path, _valid(target=target))

        with pytest.raises(ContractError):
            read_contract_file(path)

    def test_a_target_without_the_path_part_is_refused(self, tmp_path: Path) -> None:
        path = _write(tmp_path, _valid(target="validate"))

        with pytest.raises(ContractError, match="must be 'relative/path.py::"):
            read_contract_file(path)

    def test_a_target_that_is_not_python_is_refused(self, tmp_path: Path) -> None:
        path = _write(tmp_path, _valid(target="notes.txt::f"))

        with pytest.raises(ContractError, match="must name a .py file"):
            read_contract_file(path)

    def test_a_dotted_target_name_is_refused(self, tmp_path: Path) -> None:
        path = _write(tmp_path, _valid(target="m.py::Class.method"))

        with pytest.raises(ContractError, match="source-local identifier"):
            read_contract_file(path)

    @pytest.mark.parametrize("digest", ["short", "A" * 64, "g" * 64, "a" * 63, "a" * 65, 12345])
    def test_an_invalid_sha256_is_refused(self, tmp_path: Path, digest: object) -> None:
        path = _write(tmp_path, _valid(source_sha256=digest))

        with pytest.raises(ContractError, match="source_sha256"):
            read_contract_file(path)

    def test_an_oversized_file_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "big.json"
        path.write_text("x" * (MAX_CONTRACT_FILE_BYTES + 1), encoding="utf-8")

        with pytest.raises(ContractError, match="above the"):
            read_contract_file(path)

    def test_a_missing_file_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ContractError, match="does not exist"):
            read_contract_file(tmp_path / "absent.json")

    def test_a_duplicate_json_key_is_refused(self, tmp_path: Path) -> None:
        """`json.loads` keeps the last duplicate; a reviewer reads the first."""

        path = tmp_path / "dup.json"
        path.write_text(
            '{"version": 1, "contracts": [{"id": "a", "id": "b", "target": "m.py::f",'
            ' "source_sha256": "' + "a" * 64 + '", "parameter": "x",'
            ' "domain": {"operator": "lt", "integer": "0"},'
            ' "expectation": {"kind": "raises_builtin", "exception": "ValueError"}}]}',
            encoding="utf-8",
        )

        with pytest.raises(ContractError, match="duplicate key 'id'"):
            read_contract_file(path)

    def test_a_duplicate_domain_key_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "dup2.json"
        path.write_text(
            '{"version": 1, "contracts": [{"id": "a", "target": "m.py::f",'
            ' "source_sha256": "' + "a" * 64 + '", "parameter": "x",'
            ' "domain": {"operator": "lt", "integer": "0", "integer": "999999"},'
            ' "expectation": {"kind": "raises_builtin", "exception": "ValueError"}}]}',
            encoding="utf-8",
        )

        with pytest.raises(ContractError, match="duplicate key 'integer'"):
            read_contract_file(path)

    def test_a_selected_id_that_does_not_exist_lists_what_is_available(
        self, tmp_path: Path
    ) -> None:
        result = read_contract_file(_write(tmp_path, VALID))

        with pytest.raises(ContractError, match="available ids: negative-age"):
            result.select("absent")

    def test_an_ambiguous_selection_is_impossible_after_validation(self, tmp_path: Path) -> None:
        """Uniqueness is proven while parsing, so selection cannot be ambiguous."""

        result = read_contract_file(_write(tmp_path, VALID))
        ids = [c.contract_id for c in result.contracts]

        assert len(ids) == len(set(ids))


class TestIntegerField:
    @pytest.mark.parametrize("value", [1.0, True, False, None, [], {}])
    def test_a_non_string_integer_is_refused(self, tmp_path: Path, value: object) -> None:
        document = _valid()
        _contract_at(document)["domain"] = {
            "operator": "lt",
            "integer": value,
        }
        path = _write(tmp_path, document)

        with pytest.raises(ContractError, match="decimal string|not a boolean"):
            read_contract_file(path)

    @pytest.mark.parametrize("value", ["1.0", "1e3", "0x10", " 0", "0 ", "abc", "+1", "", "0.5"])
    def test_a_non_decimal_integer_is_refused(self, tmp_path: Path, value: str) -> None:
        document = _valid()
        _contract_at(document)["domain"] = {
            "operator": "lt",
            "integer": value,
        }
        path = _write(tmp_path, document)

        with pytest.raises(ContractError, match="plain decimal integer"):
            read_contract_file(path)

    @pytest.mark.parametrize("value", ["9223372036854775808", "-9223372036854775809"])
    def test_an_out_of_range_integer_is_refused(self, tmp_path: Path, value: str) -> None:
        document = _valid()
        _contract_at(document)["domain"] = {
            "operator": "lt",
            "integer": value,
        }
        path = _write(tmp_path, document)

        with pytest.raises(ContractError, match="outside the signed 64-bit range"):
            read_contract_file(path)

    def test_an_absurdly_long_integer_is_refused_before_conversion(self, tmp_path: Path) -> None:
        document = _valid()
        _contract_at(document)["domain"] = {
            "operator": "lt",
            "integer": "9" * 5000,
        }
        path = _write(tmp_path, document)

        with pytest.raises(ContractError, match="too long"):
            read_contract_file(path)

    @pytest.mark.parametrize("value", [str(MAX_SIGNED_INT), str(MIN_SIGNED_INT), "0", "-1"])
    def test_the_range_endpoints_are_accepted(self, tmp_path: Path, value: str) -> None:
        document = _valid()
        _contract_at(document)["domain"] = {
            "operator": "le",
            "integer": value,
        }
        path = _write(tmp_path, document)

        assert read_contract_file(path).select("negative-age").domain.reference == int(value)

    @pytest.mark.parametrize("value", ["lt", "le", "gt", "ge"])
    def test_every_supported_operator_is_accepted(self, tmp_path: Path, value: str) -> None:
        document = _valid()
        _contract_at(document)["domain"] = {
            "operator": value,
            "integer": "0",
        }
        path = _write(tmp_path, document)

        assert read_contract_file(path).select("negative-age").domain.operator.value == value

    @pytest.mark.parametrize("value", ["eq", "ne", "lt_eq", "&&", "lt(0)", "in", "notlt", "LTEQ"])
    def test_an_unsupported_operator_is_refused(self, tmp_path: Path, value: str) -> None:
        document = _valid()
        _contract_at(document)["domain"] = {
            "operator": value,
            "integer": "0",
        }
        path = _write(tmp_path, document)

        with pytest.raises(ContractError, match="unsupported domain operator"):
            read_contract_file(path)


class TestExpectation:
    def _with_expectation(self, tmp_path: Path, **expectation: object) -> Path:
        document = _valid()
        _contract_at(document)["expectation"] = expectation
        return _write(tmp_path, document)

    def test_an_unsupported_expectation_kind_is_refused(self, tmp_path: Path) -> None:
        path = self._with_expectation(tmp_path, kind="returns_200", exception="ValueError")

        with pytest.raises(ContractError, match="unsupported expectation kind"):
            read_contract_file(path)

    def test_a_custom_exception_name_is_refused(self, tmp_path: Path) -> None:
        path = self._with_expectation(tmp_path, kind="raises_builtin", exception="PaymentError")

        with pytest.raises(ContractError, match="not a real Python builtin exception"):
            read_contract_file(path)

    def test_a_dotted_exception_name_is_refused(self, tmp_path: Path) -> None:
        path = self._with_expectation(
            tmp_path, kind="raises_builtin", exception="errors.ValueError"
        )

        with pytest.raises(ContractError, match="plain class name"):
            read_contract_file(path)

    def test_a_non_exception_builtin_is_refused(self, tmp_path: Path) -> None:
        path = self._with_expectation(tmp_path, kind="raises_builtin", exception="int")

        with pytest.raises(ContractError, match="not a real Python builtin exception"):
            read_contract_file(path)

    def test_builtin_identity_is_checked_by_class_not_by_name(self, tmp_path: Path) -> None:
        """A real builtin exception subclass is accepted; a non-exception is not.

        The check resolves the name against `builtins` and tests the object, so a
        name that merely resembles an exception cannot pass.
        """

        for name in ("ValueError", "ExceptionGroup", "UnicodeDecodeError"):
            path = self._with_expectation(tmp_path, kind="raises_builtin", exception=name)
            assert read_contract_file(path).select("negative-age").expectation.exception == name

        for name in ("int", "str", "len", "True", "valueerror"):
            path = self._with_expectation(tmp_path, kind="raises_builtin", exception=name)
            with pytest.raises(ContractError):
                read_contract_file(path)


class TestDomainArithmetic:
    @pytest.mark.parametrize(
        ("operator", "reference", "anchor"),
        [
            ("lt", 0, -1),
            ("lt", -5, -6),
            ("lt", 10, 0),
            ("le", 0, 0),
            ("le", -5, -5),
            ("le", 10, 0),
            ("gt", 0, 1),
            ("gt", 10, 11),
            ("gt", -5, 0),
            ("ge", 0, 0),
            ("ge", 10, 10),
            ("ge", -5, 0),
        ],
    )
    def test_the_closest_admissible_integer_is_exact(
        self, operator: str, reference: int, anchor: int
    ) -> None:
        domain = IntegerDomain(operator=DomainOperator(operator), reference=reference)

        assert domain.closest_to_zero() == anchor
        assert domain.contains(anchor) is True

    @pytest.mark.parametrize(
        ("operator", "reference", "in_domain", "out_of_domain"),
        [
            ("lt", 0, [-100, -1, -2], [0, 1, 100]),
            ("le", 0, [-1, 0], [1, 2]),
            ("gt", 0, [1, 2, 100], [0, -1]),
            ("ge", 10, [10, 11, 100], [9, 0, -1]),
        ],
    )
    def test_membership_matches_the_declared_comparison(
        self, operator: str, reference: int, in_domain: list[int], out_of_domain: list[int]
    ) -> None:
        domain = IntegerDomain(operator=DomainOperator(operator), reference=reference)

        for value in in_domain:
            assert domain.contains(value) is True, (operator, reference, value)
        for value in out_of_domain:
            assert domain.contains(value) is False, (operator, reference, value)

    def test_boundary_equality_is_respected(self) -> None:
        assert IntegerDomain(DomainOperator.LT, 0).contains(0) is False
        assert IntegerDomain(DomainOperator.LE, 0).contains(0) is True
        assert IntegerDomain(DomainOperator.GT, 10).contains(10) is False
        assert IntegerDomain(DomainOperator.GE, 10).contains(10) is True

    def test_an_out_of_range_reference_is_refused(self) -> None:
        with pytest.raises(DomainError, match="outside the signed 64-bit range"):
            IntegerDomain(DomainOperator.LT, MAX_SIGNED_INT + 1)

    def test_a_domain_with_no_admissible_representable_value_is_reported(self) -> None:
        """`x < MIN_INT` admits no signed 64-bit integer at all."""

        domain = IntegerDomain(DomainOperator.LT, MIN_SIGNED_INT)

        with pytest.raises(DomainError, match="no signed 64-bit integer"):
            domain.closest_to_zero()
        assert domain.is_empty() is True

    def test_the_domain_describes_itself(self) -> None:
        assert IntegerDomain(DomainOperator.LT, 0).describe() == "< 0"
        assert IntegerDomain(DomainOperator.GE, 10).describe() == ">= 10"


class TestProposals:
    def test_proposals_from_minus_100_under_x_lt_0(self) -> None:
        domain = IntegerDomain(DomainOperator.LT, 0)

        values = propose_values(-100, domain, 8)

        assert values == (-1, -51, -26, -14, -8, -5, -3, -2)
        # Every proposal is admissible, strictly simpler, and non-duplicated.
        assert all(domain.contains(v) for v in values)
        assert all(v < 0 for v in values)
        assert all(abs(v) < 100 for v in values)
        assert len(values) == len(set(values))

    def test_proposals_from_100_under_x_ge_10(self) -> None:
        domain = IntegerDomain(DomainOperator.GE, 10)

        values = propose_values(100, domain, 8)

        # The anchor is considered first, and nothing below the domain is proposed.
        assert values[0] == 10
        assert all(v >= 10 for v in values)
        assert all(abs(v) < 100 for v in values)

    def test_proposal_generation_is_deterministic(self) -> None:
        domain = IntegerDomain(DomainOperator.LT, 0)

        assert propose_values(-100, domain, 8) == propose_values(-100, domain, 8)
        assert propose_values(100, IntegerDomain(DomainOperator.GE, 10), 8) == propose_values(
            100, IntegerDomain(DomainOperator.GE, 10), 8
        )

    def test_the_hard_cap_is_respected(self) -> None:
        domain = IntegerDomain(DomainOperator.LT, 0)

        for limit in (0, 1, 3, 8, 20):
            assert len(propose_values(-1_000_000, domain, limit)) <= limit

    def test_a_zero_limit_proposes_nothing(self) -> None:
        assert propose_values(-100, IntegerDomain(DomainOperator.LT, 0), 0) == ()

    def test_the_simplest_admissible_seed_proposes_nothing(self) -> None:
        """`-1` under `x < 0` is already the closest admissible value."""

        assert propose_values(-1, IntegerDomain(DomainOperator.LT, 0), 8) == ()
        assert propose_values(0, IntegerDomain(DomainOperator.GE, 0), 8) == ()
        assert propose_values(10, IntegerDomain(DomainOperator.GE, 10), 8) == ()

    def test_an_out_of_domain_seed_proposes_nothing(self) -> None:
        assert propose_values(5, IntegerDomain(DomainOperator.LT, 0), 8) == ()

    def test_an_out_of_range_seed_proposes_nothing(self) -> None:
        assert propose_values(MAX_SIGNED_INT + 10, IntegerDomain(DomainOperator.LT, 0), 8) == ()

    def test_an_unrepresentable_domain_proposes_nothing(self) -> None:
        assert propose_values(-100, IntegerDomain(DomainOperator.LT, MIN_SIGNED_INT), 8) == ()

    def test_the_metric_is_magnitude_not_signed_value(self) -> None:
        assert complexity(-5) == complexity(5) == 5
        # Equal magnitude is never an improvement, in either direction.
        assert is_simpler(1, -1) is False
        assert is_simpler(-1, 1) is False
        assert is_simpler(0, 1) is True
        assert is_simpler(1, 2) is True

    def test_a_large_seed_reaches_the_anchor_quickly(self) -> None:
        """The anchor is proposed first, then magnitudes strictly decrease.

        The order is anchor-then-midpoints, so magnitude is *not* decreasing
        across the whole list -- the first entry is the smallest possible value.
        What the algorithm does guarantee is that the anchor comes first and that
        the midpoint walk then closes in on it without repeating.
        """

        domain = IntegerDomain(DomainOperator.LT, 0)

        values = propose_values(-1_000_000, domain, 8)

        assert values[0] == -1
        assert values[1] < 0
        rest = values[1:]
        assert all(abs(rest[i]) > abs(rest[i + 1]) for i in range(len(rest) - 1))
        assert all(abs(v) < 1_000_000 for v in values)
