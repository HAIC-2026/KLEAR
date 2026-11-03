# Offline tests for ACS estimate and margin-of-error parsing.
import pytest

from klear.etl.acs_values import ESTIMATE_CODES, MOE_CODES, parse_estimate, parse_moe
from klear.store.db import MOE_STATUSES, VALUE_STATUSES


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("8,619", (8619.0, "ok")),
        ("85,122", (85122.0, "ok")),
        ("12.5", (12.5, "ok")),
        ("0", (0.0, "ok")),
        ("(X)", (None, "not_applicable")),
        ("-", (None, "suppressed")),
        ("N", (None, "suppressed")),
        ("250,000+", (250000.0, "top_coded")),
        ("2,500-", (2500.0, "bottom_coded")),
        (-666666666, (None, "suppressed")),
        (-888888888, (None, "not_applicable")),
        (None, (None, "suppressed")),
        (float("nan"), (None, "suppressed")),
        ("-666666666", (None, "suppressed")),
        ("  8,619  ", (8619.0, "ok")),
        ("-888888888.0", (None, "not_applicable")),
    ],
)
def test_parse_estimate(raw, expected):
    assert parse_estimate(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("±436", (436.0, "ok")),
        ("+/-436", (436.0, "ok")),
        ("*****", (None, "controlled")),
        (-555555555, (None, "controlled")),
        (-222222222, (None, "not_computable")),
        ("(X)", (None, "not_applicable")),
        (float("nan"), (None, "not_computable")),
        ("-", (None, "not_computable")),
        ("N", (None, "not_computable")),
        ("-555555555", (None, "controlled")),
        ("  ±436  ", (436.0, "ok")),
        (-666666666, (None, "not_computable")),
        ("-555555555.0", (None, "controlled")),
        ("-333333333.0", (None, "open_ended")),
    ],
)
def test_parse_moe(raw, expected):
    assert parse_moe(raw) == expected


@pytest.mark.parametrize("func", [parse_estimate, parse_moe])
def test_unparseable_raises_with_input(func):
    with pytest.raises(ValueError, match="not-a-number"):
        func("not-a-number")


@pytest.mark.parametrize("raw", [-666666666, -999999999, -333333333, -222222222, -555555555])
def test_estimate_suppressed_sentinels(raw):
    assert parse_estimate(raw) == (None, "suppressed")
    assert parse_estimate(float(raw)) == (None, "suppressed")
    assert parse_estimate(str(raw)) == (None, "suppressed")


def test_estimate_not_applicable_sentinel():
    assert parse_estimate(-888888888) == (None, "not_applicable")
    assert parse_estimate(-888888888.0) == (None, "not_applicable")


@pytest.mark.parametrize(
    "raw,status",
    [
        (-555555555, "controlled"),
        (-222222222, "not_computable"),
        (-333333333, "open_ended"),
        (-888888888, "not_applicable"),
        (-999999999, "not_computable"),
        (-666666666, "not_computable"),
    ],
)
def test_moe_sentinels(raw, status):
    assert parse_moe(raw) == (None, status)
    assert parse_moe(float(raw)) == (None, status)


def test_integral_float_keeps_value():
    assert parse_estimate(8619.0) == (8619.0, "ok")
    assert parse_moe(436.0) == (436.0, "ok")


@pytest.mark.parametrize("raw", ["1,23", "8,,619", ",123", "1,2345", "12,34,567"])
@pytest.mark.parametrize("func", [parse_estimate, parse_moe])
def test_bad_thousands_grouping_raises(func, raw):
    with pytest.raises(ValueError):
        func(raw)


def test_good_grouping_parses():
    assert parse_estimate("1,234,567") == (1234567.0, "ok")
    assert parse_estimate("1234") == (1234.0, "ok")
    assert parse_moe("±1,234") == (1234.0, "ok")


@pytest.mark.parametrize(
    "raw,status", [("**", "not_computable"), ("***", "open_ended"), ("*****", "controlled")]
)
def test_moe_star_annotations(raw, status):
    assert parse_moe(raw) == (None, status)


def test_codes_map_into_store_vocabulary():
    assert set(ESTIMATE_CODES.values()) <= set(VALUE_STATUSES)
    assert set(MOE_CODES.values()) <= set(MOE_STATUSES)
    for raw in ["1", "1+", "1-", None]:
        assert parse_estimate(raw)[1] in VALUE_STATUSES
    for raw in ["±1", None]:
        assert parse_moe(raw)[1] in MOE_STATUSES
