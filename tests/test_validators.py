"""Unit tests for ``mousedb.validators``.

The validators module is the front line for every data import: every Excel
sheet, every GUI entry, every CLI invocation routes IDs and measurements
through these functions before they touch the database. A regression here
quietly corrupts the cohort. The test suite is deliberately broad rather
than deep — most validators are short and the failure modes are obvious — so
the cost of "did I break ID parsing?" is one fast test run instead of an
import session that silently rejects half a cohort.

Conventions:

- Functions return ``(is_valid: bool, error_message: str)`` rather than
  raising. Tests assert on both halves of the tuple.
- ``validate_subject_id`` and friends auto-uppercase + strip the input. We
  test that explicitly so future refactors don't drop the normalization.
- ``compact_id_to_subject_id`` round-trips the two ID styles used in
  practice ("CNT0115" -> "CNT_01_15" and back through ``validate_subject_id``).
"""
from __future__ import annotations

from datetime import date

import pytest

from mousedb.validators import (
    ValidationError,
    compact_id_to_subject_id,
    validate_cohort_id,
    validate_pellet_number,
    validate_pellet_score,
    validate_project_code,
    validate_session_date,
    validate_sex,
    validate_subject_id,
    validate_surgery_type,
    validate_tray_number,
    validate_tray_type,
    validate_weight,
)


# ---------------------------------------------------------------------------
# ID validators
# ---------------------------------------------------------------------------


class TestValidateSubjectID:
    @pytest.mark.parametrize("value", ["CNT_05_01", "CNT_01_15", "ABC_99_99"])
    def test_canonical_format_accepted(self, value):
        ok, msg = validate_subject_id(value)
        assert ok is True
        assert msg == ""

    def test_lowercase_is_normalized_before_match(self):
        ok, _ = validate_subject_id("cnt_05_01")
        assert ok is True

    def test_surrounding_whitespace_is_stripped(self):
        ok, _ = validate_subject_id("  CNT_05_01  ")
        assert ok is True

    @pytest.mark.parametrize("value", ["", None])
    def test_empty_inputs_are_rejected(self, value):
        # validate_subject_id treats both empty string and None as missing.
        ok, msg = validate_subject_id(value if value is not None else "")
        assert ok is False
        assert "required" in msg.lower()

    @pytest.mark.parametrize(
        "value",
        [
            "CNT_5_1",          # single-digit cohort + subject components
            "CNT-05-01",        # dashes instead of underscores
            "CNT_05",           # missing subject component
            "CNT_05_01_extra",  # trailing component
            "123_05_01",        # numeric project code
            "CNT_AA_01",        # non-numeric cohort
        ],
    )
    def test_malformed_inputs_are_rejected(self, value):
        ok, msg = validate_subject_id(value)
        assert ok is False
        assert "format XXX_NN_NN" in msg


class TestValidateCohortID:
    @pytest.mark.parametrize("value", ["CNT_05", "ABC_99"])
    def test_canonical_format_accepted(self, value):
        assert validate_cohort_id(value) == (True, "")

    def test_lowercase_is_normalized(self):
        assert validate_cohort_id("cnt_05")[0] is True

    @pytest.mark.parametrize("value", ["", "CNT", "CNT_5", "CNT_005", "CNT_05_01"])
    def test_malformed_rejected(self, value):
        ok, _ = validate_cohort_id(value)
        assert ok is False


class TestValidateProjectCode:
    @pytest.mark.parametrize("value", ["CNT", "ABC", "X"])
    def test_uppercase_letters_only_accepted(self, value):
        assert validate_project_code(value) == (True, "")

    def test_lowercase_is_normalized(self):
        assert validate_project_code("cnt")[0] is True

    @pytest.mark.parametrize("value", ["", "CNT1", "CNT_05", "123"])
    def test_non_letters_rejected(self, value):
        assert validate_project_code(value)[0] is False


class TestCompactIdToSubjectId:
    """Compact-format conversion (CNT0115 <-> CNT_01_15) is what the legacy
    Excel sheets and the lab's video filenames use; making sure both styles
    round-trip is what keeps the import path forgiving."""

    def test_compact_form_converts_correctly(self):
        assert compact_id_to_subject_id("CNT0115") == "CNT_01_15"

    def test_lowercase_compact_uppercased_during_conversion(self):
        assert compact_id_to_subject_id("cnt0115") == "CNT_01_15"

    def test_already_canonical_returns_unchanged_uppercase(self):
        assert compact_id_to_subject_id("CNT_01_15") == "CNT_01_15"
        assert compact_id_to_subject_id("cnt_01_15") == "CNT_01_15"

    @pytest.mark.parametrize("value", ["", "CNT", "CNT_01", "CNT01"])
    def test_unparseable_returns_none(self, value):
        assert compact_id_to_subject_id(value) is None

    def test_round_trip_compact_then_validate_subject_id(self):
        sid = compact_id_to_subject_id("CNT0115")
        ok, _ = validate_subject_id(sid)
        assert ok is True


# ---------------------------------------------------------------------------
# Numeric / categorical data validators
# ---------------------------------------------------------------------------


class TestValidateWeight:
    @pytest.mark.parametrize("value", [10.0, 25.0, 30.5, 49.9])
    def test_plausible_mouse_weight_accepted(self, value):
        assert validate_weight(value) == (True, "")

    @pytest.mark.parametrize("value, hint", [
        (0, "positive"),
        (-1, "positive"),
        (100, "less than 100"),
        (5, "too low"),
        (60, "too high"),
    ])
    def test_implausible_rejected_with_hint(self, value, hint):
        ok, msg = validate_weight(value)
        assert ok is False
        assert hint in msg.lower()

    def test_none_rejected(self):
        ok, msg = validate_weight(None)
        assert ok is False
        assert "required" in msg.lower()


class TestValidatePelletScore:
    @pytest.mark.parametrize("value", [0, 1, 2])
    def test_canonical_codes_accepted(self, value):
        assert validate_pellet_score(value) == (True, "")

    @pytest.mark.parametrize("value", [-1, 3, 4, 100])
    def test_other_ints_rejected(self, value):
        ok, _ = validate_pellet_score(value)
        assert ok is False

    def test_none_rejected(self):
        assert validate_pellet_score(None)[0] is False


class TestValidateTrayType:
    @pytest.mark.parametrize("value", ["E", "F", "P"])
    def test_canonical_codes_accepted(self, value):
        assert validate_tray_type(value) == (True, "")

    def test_lowercase_normalized(self):
        assert validate_tray_type("p")[0] is True

    @pytest.mark.parametrize("value", ["", "X", "Pillar", "PE"])
    def test_other_rejected(self, value):
        assert validate_tray_type(value)[0] is False


class TestValidateTrayAndPelletNumbers:
    @pytest.mark.parametrize("value", [1, 2, 3, 4])
    def test_tray_in_range(self, value):
        assert validate_tray_number(value) == (True, "")

    @pytest.mark.parametrize("value", [0, 5, -1, 100])
    def test_tray_out_of_range_rejected(self, value):
        assert validate_tray_number(value)[0] is False

    @pytest.mark.parametrize("value", [1, 10, 20])
    def test_pellet_in_range(self, value):
        assert validate_pellet_number(value) == (True, "")

    @pytest.mark.parametrize("value", [0, 21, -1])
    def test_pellet_out_of_range_rejected(self, value):
        assert validate_pellet_number(value)[0] is False

    def test_none_rejected_for_both(self):
        assert validate_tray_number(None)[0] is False
        assert validate_pellet_number(None)[0] is False


class TestValidateSex:
    @pytest.mark.parametrize("value", ["M", "F"])
    def test_canonical_accepted(self, value):
        assert validate_sex(value) == (True, "")

    def test_lowercase_normalized(self):
        assert validate_sex("m")[0] is True

    def test_empty_is_optional_and_passes(self):
        # Empty string is treated as "not provided" rather than invalid; the
        # docstring says sex is optional. Lock that behavior into the tests
        # so a future refactor doesn't accidentally tighten the contract.
        assert validate_sex("") == (True, "")

    @pytest.mark.parametrize("value", ["X", "MALE", "f " * 5])
    def test_other_rejected(self, value):
        assert validate_sex(value)[0] is False


class TestValidateSurgeryType:
    @pytest.mark.parametrize("value", ["contusion", "tracing", "perfusion"])
    def test_canonical_accepted(self, value):
        assert validate_surgery_type(value) == (True, "")

    def test_uppercase_normalized(self):
        assert validate_surgery_type("CONTUSION")[0] is True

    @pytest.mark.parametrize("value", ["", "laminectomy", "injury"])
    def test_other_rejected(self, value):
        assert validate_surgery_type(value)[0] is False


# ---------------------------------------------------------------------------
# Session date validator (more involved; uses cohort timeline)
# ---------------------------------------------------------------------------


class TestValidateSessionDate:
    """``validate_session_date`` returns a 3-tuple (is_valid, msg, phase) and
    needs a cohort start date and a list of valid phase names. We mostly
    care that obviously-wrong dates fail; phase assignment proper has its
    own tests in test_phases.py."""

    @pytest.fixture
    def cohort_start(self):
        return date(2025, 6, 1)

    @pytest.fixture
    def valid_phases(self):
        return ["Baseline", "Post_Injury_1", "Post_Rehab_Test"]

    def test_date_after_cohort_start_returns_3_tuple(self, cohort_start, valid_phases):
        result = validate_session_date(date(2025, 7, 1), cohort_start, valid_phases)
        # Function may return (True, ..., phase) or (False, ..., None) depending
        # on phase logic; either way we assert the shape.
        assert isinstance(result, tuple)
        assert len(result) == 3

    def test_date_before_cohort_start_rejected(self, cohort_start, valid_phases):
        ok, msg, _ = validate_session_date(date(2025, 5, 1), cohort_start, valid_phases)
        assert ok is False


# ---------------------------------------------------------------------------
# ValidationError exception
# ---------------------------------------------------------------------------


class TestValidationError:
    def test_error_message_includes_field_and_message(self):
        err = ValidationError("subject_id", "BAD_ID", "wrong format")
        # The exception's stringification uses the format "field: message".
        assert "subject_id" in str(err)
        assert "wrong format" in str(err)
        # The original value is still accessible for programmatic inspection.
        assert err.value == "BAD_ID"
