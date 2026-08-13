from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.services import gstin, money


# --- GSTIN -----------------------------------------------------------------

@pytest.mark.parametrize(
    "value",
    ["24AAACC1206D1ZM", "27AAACS7409B1ZN", "24AABCG1234M1ZU", "04AAJFN8899K1ZE",
     "29AAGCV2233P1ZT", "24AAKFS6677L1ZG"],
)
def test_valid_gstins_pass(value):
    assert gstin.validate(value) == value


def test_a_single_transposed_character_fails_the_checksum():
    """This is the whole point of validating the checksum rather than a regex:
    a typo that looks fine would silently flip the tax split."""
    assert gstin.is_valid("24AAACC1206D1ZM")
    assert not gstin.is_valid("24AAACC1206D1ZN")


@pytest.mark.parametrize(
    "value,reason",
    [
        ("", "empty"),
        (None, "empty"),
        ("24AAACC1206D1Z", "15 characters"),
        ("24AAACC1206D1ZMM", "15 characters"),
        ("2AAACC1206D11ZM", "format"),
        ("99AAACC1206D1ZM", "unknown state code"),
        ("24aaacc1206d1zm".upper()[:14] + "X", "checksum"),
    ],
)
def test_invalid_gstins_are_rejected_with_a_reason(value, reason):
    with pytest.raises(gstin.InvalidGSTIN, match=reason):
        gstin.validate(value)


def test_lowercase_and_spaces_are_normalised():
    assert gstin.validate(" 24aaacc1206d1zm ") == "24AAACC1206D1ZM"


def test_state_code_and_name_come_from_a_valid_gstin_only():
    assert gstin.state_code_of("27AAACS7409B1ZN") == "27"
    assert gstin.state_name("27") == "Maharashtra"
    assert gstin.state_name("24") == "Gujarat"
    # A bad GSTIN yields no state code rather than a wrong one.
    assert gstin.state_code_of("27AAACS7409B1ZZ") is None
    assert gstin.state_code_of(None) is None


def test_checksum_computation_is_reversible():
    for value in ["24AAACC1206D1ZM", "27AAACS7409B1ZN", "29AAGCV2233P1ZT"]:
        assert gstin.compute_checksum(value[:14]) == value[14]


# --- money -----------------------------------------------------------------

@pytest.mark.parametrize(
    "value,expected",
    [
        ("0", "0.00"),
        ("1", "1.00"),
        ("999", "999.00"),
        ("1000", "1,000.00"),
        ("100000", "1,00,000.00"),
        ("1000000", "10,00,000.00"),
        ("12345678.9", "1,23,45,678.90"),
        ("-1234.5", "-1,234.50"),
        ("79650000", "7,96,50,000.00"),
    ],
)
def test_indian_digit_grouping(value, expected):
    assert money.fmt(Decimal(value)) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("0", "Rupees Zero Only"),
        ("100", "Rupees One Hundred Only"),
        ("1234567.89",
         "Rupees Twelve Lakh Thirty Four Thousand Five Hundred Sixty Seven and Eighty Nine Paise Only"),
        ("2950000", "Rupees Twenty Nine Lakh Fifty Thousand Only"),
        ("79650000", "Rupees Seven Crore Ninety Six Lakh Fifty Thousand Only"),
        ("15", "Rupees Fifteen Only"),
        ("90", "Rupees Ninety Only"),
    ],
)
def test_amount_in_words(value, expected):
    assert money.amount_in_words(Decimal(value)) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("₹ 1,23,456.78", "123456.78"),
        ("Rs. 50,00,000.00", "5000000.00"),
        ("INR 1000", "1000"),
        ("(1234.50)", "-1234.50"),
        ("", "0"),
        (None, "0"),
        ("not a number", "0"),
        (0.1, "0.1"),
    ],
)
def test_parsing_money_from_document_text(raw, expected):
    assert money.to_decimal(raw) == Decimal(expected)


def test_float_parsing_does_not_leak_binary_error():
    assert money.to_decimal(0.1) == Decimal("0.1")
    assert str(money.to_decimal(0.1)) == "0.1"


@pytest.mark.parametrize(
    "value,rounded,delta",
    [
        ("100.00", "100", "0.00"),
        ("100.49", "100", "-0.49"),
        ("100.50", "101", "0.50"),
        ("99.51", "100", "0.49"),
        ("11799.998", "11800", "0.00"),
    ],
)
def test_round_off_delta_closes_the_gap(value, rounded, delta):
    got_total, got_delta = money.round_to_rupee(Decimal(value))
    assert got_total == Decimal(rounded)
    assert got_delta == Decimal(delta)
    # The invariant that matters: exact + delta == rounded.
    assert money.q2(Decimal(value)) + got_delta == got_total


def test_financial_year_uses_an_april_start():
    assert money.financial_year(date(2025, 4, 1)) == "25-26"
    assert money.financial_year(date(2025, 3, 31)) == "24-25"
    assert money.financial_year(date(2026, 3, 31)) == "25-26"
    assert money.financial_year(date(2026, 4, 1)) == "26-27"


def test_quantisation_is_half_up_not_bankers():
    """Python's default rounding is half-even, which is not what an Indian
    invoice does."""
    assert money.q2(Decimal("2.345")) == Decimal("2.35")
    assert money.q2(Decimal("2.355")) == Decimal("2.36")
    assert money.q2(Decimal("0.005")) == Decimal("0.01")
