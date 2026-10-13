"""Qualified exception identity: observation, classification, and conservatism.

Phase 6B recorded an exception by bare class name only, so a custom exception
could never be matched and a class that merely *shares* a builtin name was
indistinguishable from the builtin itself. Phase 7A adds qualified runtime
identity -- module, qualified name, and the class's real ``builtins`` lineage --
to the worker protocol.

The point of these tests is the tension the phase has to hold: the observation
becomes much more precise, and the oracle must *not* become more confident by
accident. Every case below either uses a real fact reported by the sandbox, or
stays ``INCONCLUSIVE``.
"""

from __future__ import annotations

from edgehunter.evaluation.exceptions import (
    ExceptionIdentity,
    exception_descendants,
    is_builtin_exception,
    match_exception,
    match_exception_observed,
)
from edgehunter.evaluation.models import OracleStatus
from edgehunter.execution.models import OutcomeKind, RuntimeObservation
from edgehunter.runtime import worker


def _identity(**kwargs: object) -> ExceptionIdentity:
    return ExceptionIdentity(**kwargs)  # type: ignore[arg-type]


def _from_worker(document: dict[str, object]) -> ExceptionIdentity:
    """Translate a worker result document's exception fields into an identity.

    The worker's document uses the protocol's key names; the evaluator's identity
    uses the domain's. This mapping is what the host decoder performs, and a test
    that skipped it would not be testing the real path.
    """
    lineage = document.get("exception_builtin_lineage")
    return ExceptionIdentity(
        name=document.get("exception_type"),  # type: ignore[arg-type]
        module=document.get("exception_module"),  # type: ignore[arg-type]
        qualname=document.get("exception_qualname"),  # type: ignore[arg-type]
        is_builtin=document.get("exception_builtin"),  # type: ignore[arg-type]
        builtin_lineage=tuple(lineage) if isinstance(lineage, list) else None,
    )


# --- A. a real builtin exception --------------------------------------------


class TestARealBuiltin:
    def test_a_real_builtin_value_error_is_reported_as_builtin(self) -> None:
        identity = worker._exception_identity(ValueError("bad"))

        assert identity["exception_builtin"] is True
        assert identity["exception_module"] == "builtins"
        assert identity["exception_qualname"] == "ValueError"
        assert "ValueError" in identity["exception_builtin_lineage"]

    def test_a_real_builtin_matches_a_builtin_expectation(self) -> None:
        # The string-only form stays usable, but with no identity evidence it no
        # longer decides anything (D-078): a bare name is not an identity.
        assert match_exception("ValueError", None).status is OracleStatus.INCONCLUSIVE
        assert match_exception("ValueError", "ValueError").status is OracleStatus.INCONCLUSIVE

        verdict = match_exception_observed(
            "ValueError", _identity(name="ValueError", module="builtins", is_builtin=True)
        )
        assert verdict.status is OracleStatus.MATCH

    def test_a_real_builtin_subclass_matches_through_the_real_hierarchy(self) -> None:
        # UnicodeDecodeError is a real builtin subclass of ValueError, so this is
        # the case where Python's own `pytest.raises(ValueError)` is satisfied.
        identity = worker._exception_identity(UnicodeDecodeError("utf-8", b"", 0, 1, "reason"))

        assert identity["exception_builtin"] is True
        verdict = match_exception_observed("ValueError", _from_worker(identity))
        assert verdict.status is OracleStatus.MATCH
        assert verdict.matched_subclass is True

    def test_the_host_hierarchy_still_comes_from_builtins(self) -> None:
        assert is_builtin_exception("ValueError")
        assert not is_builtin_exception("MyValueError")
        assert "UnicodeDecodeError" in exception_descendants("ValueError")


# --- B. a custom exception keeps its metadata -------------------------------


class TestBCustomException:
    def test_a_custom_exception_keeps_its_module_and_qualified_name(self) -> None:
        class InvalidInput(Exception):
            pass

        identity = worker._exception_identity(InvalidInput())

        assert identity["exception_module"] == __name__
        assert identity["exception_qualname"].endswith(".<locals>.InvalidInput")
        assert identity["exception_builtin"] is False

    def test_a_custom_exception_against_a_custom_expectation_is_inconclusive(self) -> None:
        class InvalidInput(Exception):
            pass

        identity = worker._exception_identity(InvalidInput())

        verdict = match_exception_observed("InvalidInput", _from_worker(identity))
        # The names are identical, and it is still undecided: the test may refer
        # to a different class with the same name.
        assert verdict.status is OracleStatus.INCONCLUSIVE

    def test_a_qualified_custom_observation_never_becomes_a_match(self) -> None:
        observed = _identity(
            name="InvalidInput",
            module="myapp.errors",
            qualname="InvalidInput",
            is_builtin=False,
            builtin_lineage=("Exception", "BaseException"),
        )

        verdict = match_exception_observed("myapp.errors.InvalidInput", observed)

        # Textually identical, and still refused: the expectation's class was
        # never resolved, only spelled the same way.
        assert verdict.status is OracleStatus.INCONCLUSIVE
        assert "cannot be established without importing the target module" in verdict.reason
        assert "myapp.errors.InvalidInput" in verdict.reason


# --- C. a shadowed builtin name ---------------------------------------------


class TestCShadowedBuiltin:
    def test_a_class_named_value_error_is_not_the_builtin(self) -> None:
        class ValueError(Exception):  # noqa: A001 - the shadowing is the point
            pass

        identity = worker._exception_identity(ValueError())

        # Same name, different class: the worker must not claim builtin's identity.
        assert identity["exception_builtin"] is False
        assert identity["exception_qualname"].endswith("ValueError")
        assert "ValueError" not in identity.get("exception_builtin_lineage", [])

    def test_a_shadowed_builtin_name_is_not_a_match(self) -> None:
        class ValueError(Exception):  # noqa: A001
            pass

        identity = worker._exception_identity(ValueError())

        verdict = match_exception_observed("ValueError", _from_worker(identity))

        # Its lineage really does lack the builtin, so the contradiction is a
        # fact rather than a guess.
        assert verdict.status is OracleStatus.MISMATCH
        assert "does not contain ValueError" in verdict.reason

    def test_a_shadowed_builtin_expectation_in_the_test_module_is_inconclusive(self) -> None:
        """A test module that binds the name is not asking about the builtin."""
        verdict = match_exception_observed(
            "ValueError",
            _identity(name="ValueError", is_builtin=True),
            expected_binding="class or function ValueError",
        )

        assert verdict.status is OracleStatus.INCONCLUSIVE
        assert "binds the name" in verdict.reason

    def test_the_shadowing_safeguard_needs_a_recorded_binding(self) -> None:
        verdict = match_exception_observed(
            "ValueError",
            _identity(name="ValueError", is_builtin=True),
            expected_binding=None,
        )
        assert verdict.status is OracleStatus.MATCH


# --- D. a genuine builtin subclass ------------------------------------------


class TestDBuiltinSubclass:
    def test_a_real_subclass_reports_the_builtin_in_its_lineage(self) -> None:
        class MyValueError(ValueError):
            pass

        identity = worker._exception_identity(MyValueError())

        assert identity["exception_builtin"] is False
        lineage = identity["exception_builtin_lineage"]
        assert "MyValueError" not in lineage
        assert "ValueError" in lineage

    def test_a_real_subclass_matches_a_builtin_expectation(self) -> None:
        class MyValueError(ValueError):
            pass

        identity = worker._exception_identity(MyValueError())

        verdict = match_exception_observed("ValueError", _from_worker(identity))

        # Python's own `pytest.raises(ValueError)` accepts a subclass, and here
        # the subclass relationship is a real MRO fact, not a name guess.
        assert verdict.status is OracleStatus.MATCH
        assert verdict.matched_subclass is True

    def test_a_class_only_deriving_from_exception_does_not_match(self) -> None:
        class MyValueError(Exception):
            pass

        identity = worker._exception_identity(MyValueError())

        verdict = match_exception_observed("ValueError", _from_worker(identity))

        assert verdict.status is OracleStatus.MISMATCH
        assert "does not contain ValueError" in verdict.reason

    def test_inheritance_is_never_approximated_from_names_or_suffixes(self) -> None:
        class ValueErrorLikeError(Exception):
            pass

        class MyValueError(Exception):
            pass

        for cls in (ValueErrorLikeError, MyValueError):
            identity = worker._exception_identity(cls())
            assert "ValueError" not in identity["exception_builtin_lineage"]
            verdict = match_exception_observed("ValueError", _from_worker(identity))
            assert verdict.status is OracleStatus.MISMATCH


# --- E. same name from different modules ------------------------------------


class TestESameNameDifferentModules:
    def test_two_modules_define_the_same_name_and_they_stay_distinct(self) -> None:
        class PaymentError(Exception):
            pass

        first = _identity(
            name="PaymentError", module="billing", qualname="PaymentError", is_builtin=False
        )
        second = _identity(
            name="PaymentError", module="ledger", qualname="PaymentError", is_builtin=False
        )

        assert first.display_name == "billing.PaymentError"
        assert second.display_name == "ledger.PaymentError"
        assert first != second
        # Neither can be matched to a bare `PaymentError` expectation.
        assert match_exception_observed("PaymentError", first).status is OracleStatus.INCONCLUSIVE
        assert match_exception_observed("PaymentError", second).status is OracleStatus.INCONCLUSIVE

    def test_the_display_name_omits_the_builtins_module(self) -> None:
        identity = _identity(
            name="ValueError", module="builtins", qualname="ValueError", is_builtin=True
        )
        assert identity.display_name == "ValueError"


# --- F. unknown identity -----------------------------------------------------


class TestFUnknownIdentity:
    def test_a_missing_observation_is_inconclusive(self) -> None:
        verdict = match_exception_observed("ValueError", _identity(name=None))
        assert verdict.status is OracleStatus.INCONCLUSIVE
        assert "recorded no exception type" in verdict.reason

    def test_an_observation_without_identity_evidence_decides_nothing(self) -> None:
        """No evidence must not become a verdict in *either* direction (D-078).

        A name-only MATCH is unsound because a target's own
        ``class ValueError(Exception)`` does not satisfy ``pytest.raises(builtin
        ValueError)``. A name-only MISMATCH is equally unsound because a target's
        own ``class KeyError(ValueError)`` *does* satisfy it. With no evidence
        about which class was raised, both are guesses, so the outcome is
        ``INCONCLUSIVE``.
        """
        observed = _identity(name="ValueError")

        assert match_exception_observed("ValueError", observed).status is OracleStatus.INCONCLUSIVE
        assert match_exception_observed("KeyError", observed).status is OracleStatus.INCONCLUSIVE

    def test_the_string_only_wrapper_is_equally_undecided(self) -> None:
        """The legacy entry point must not be a loophole around the same rule."""

        assert match_exception("ValueError", "ValueError").status is OracleStatus.INCONCLUSIVE
        assert match_exception("ValueError", "KeyError").status is OracleStatus.INCONCLUSIVE
        # A genuinely absent observation is still inconclusive, for its own reason.
        assert "recorded no exception type" in match_exception("ValueError", None).reason

    def test_a_custom_class_with_no_lineage_is_inconclusive_not_a_mismatch(self) -> None:
        observed = _identity(
            name="Thing",
            module="app",
            qualname="Thing",
            is_builtin=False,
            builtin_lineage=None,
        )

        verdict = match_exception_observed("ValueError", observed)

        assert verdict.status is OracleStatus.INCONCLUSIVE
        assert "no inheritance information" in verdict.reason

    def test_an_absent_expectation_is_inconclusive(self) -> None:
        verdict = match_exception_observed(None, _identity(name="ValueError", is_builtin=True))
        assert verdict.status is OracleStatus.INCONCLUSIVE
        assert "does not name an exception class" in verdict.reason


# --- false-positive prevention ----------------------------------------------


class TestNoFalsePositives:
    def test_more_metadata_never_makes_an_unprovable_case_a_match(self) -> None:
        """The exact tension the phase must hold.

        A richer observation must not become a licence to match. These are the
        shapes that Phase 6B left ``INCONCLUSIVE`` and that must stay there.
        """
        unresolvable = [
            ("InvalidInput", _identity(name="InvalidInput", module="app.errors", is_builtin=False)),
            (
                "app.errors.InvalidInput",
                _identity(name="InvalidInput", module="app.errors", is_builtin=False),
            ),
            ("InvalidInput", _identity(name="InvalidInput", is_builtin=None)),
        ]
        for expected, observed in unresolvable:
            verdict = match_exception_observed(expected, observed)
            assert verdict.status is OracleStatus.INCONCLUSIVE, (expected, observed)

    def test_observation_identity_is_not_a_license_to_match_custom_classes(self) -> None:
        observed = _identity(
            name="MyValueError",
            module="app",
            qualname="MyValueError",
            is_builtin=False,
            builtin_lineage=("Exception", "BaseException"),
        )
        assert (
            match_exception_observed("MyValueError", observed).status is OracleStatus.INCONCLUSIVE
        )

    def test_a_different_known_builtin_is_a_mismatch(self) -> None:
        verdict = match_exception_observed(
            "ValueError", _identity(name="KeyError", module="builtins", is_builtin=True)
        )
        assert verdict.status is OracleStatus.MISMATCH


# --- observation plumbing ---------------------------------------------------


class TestObservationFields:
    def test_the_observation_exposes_the_qualified_identity(self) -> None:
        observation = RuntimeObservation(
            outcome=OutcomeKind.RAISED,
            exception_type="InvalidInput",
            exception_module="app.errors",
            exception_qualname="InvalidInput",
            exception_builtin=False,
            exception_builtin_lineage=("Exception", "BaseException"),
        )

        assert observation.exception_identity_text == "app.errors.InvalidInput"

    def test_an_observation_without_identity_fields_still_describes_itself(self) -> None:
        observation = RuntimeObservation(outcome=OutcomeKind.RAISED, exception_type="ValueError")

        assert observation.exception_builtin is None
        assert observation.exception_builtin_lineage is None
        assert observation.exception_identity_text == "ValueError"

    def test_an_observation_with_no_exception_at_all_is_unknown(self) -> None:
        observation = RuntimeObservation(outcome=OutcomeKind.RETURNED)
        assert observation.exception_identity_text == "unknown"
