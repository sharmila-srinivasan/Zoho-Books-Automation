from decimal import Decimal

import pytest

from src.accounting import calculate_application, expected_unused_after, plan_allocations
from src.exceptions import AmountMismatchError
from src.utils import extract_currency, format_money, parse_amount, parse_optional_amount

from .conftest import D


def test_full_invoice_when_payment_is_larger():
    assert calculate_application(4000, 2000) == D(2000)


def test_partial_payment():
    assert calculate_application(3000, 5000) == D(3000)


def test_exact_match():
    assert calculate_application(1500, 1500) == D(1500)


def test_zero_unused_applies_nothing():
    assert calculate_application(0, 500) == D(0)


def test_cents_are_exact():
    assert calculate_application(D("0.30"), D("0.10") + D("0.20")) == D("0.30")


def test_negative_amounts_rejected():
    with pytest.raises(ValueError):
        calculate_application(-1, 100)
    with pytest.raises(ValueError):
        calculate_application(100, -1)


def test_spec_example_three_invoices():
    # Unused 5,000 across invoices of 2,000 / 1,500 / 1,500 -> remaining 0
    plan = plan_allocations(5000, [2000, 1500, 1500])
    assert plan == [D(2000), D(1500), D(1500)]
    assert D(5000) - sum(plan) == 0


def test_partial_invoice_stops_after_payment_used_up():
    # Unused 3,000, first invoice 5,000 -> apply 3,000 and stop
    assert plan_allocations(3000, [5000, 1000]) == [D(3000)]


def test_remaining_amount_calculation():
    assert expected_unused_after(D(4000), D(2000)) == D(2000)
    assert expected_unused_after(D(2000), D(2000)) == D(0)


def test_remaining_can_never_go_negative():
    with pytest.raises(AmountMismatchError):
        expected_unused_after(D(1000), D(1500))


@pytest.mark.parametrize(
    "text, expected",
    [
        ("$10,000.00", "10000.00"),
        ("USD 1,234.50", "1234.50"),
        ("₹1,00,000.00", "100000.00"),
        ("CA$ 75.5", "75.5"),
        ("0.00", "0.00"),
        ("7", "7"),
        ("-$25.00", "-25.00"),
        ("($25.00)", "-25.00"),
    ],
)
def test_parse_amount(text, expected):
    assert parse_amount(text) == Decimal(expected)


def test_parse_amount_forced_comma_decimal():
    assert parse_amount("€1.234,56", decimal_separator=",") == Decimal("1234.56")


@pytest.mark.parametrize("text", ["", "abc", "1,00", "10,0000.00", "1,000.00 500.00", None])
def test_parse_amount_forced_dot_rejects_unclear_values(text):
    with pytest.raises(ValueError):
        parse_amount(text, decimal_separator=".")


# Formats seen side by side in the real Payments Received list (2026-10-06).
@pytest.mark.parametrize(
    "text, expected",
    [
        ("$195.16", "195.16"),
        ("$1,412,078.15", "1412078.15"),
        ("€0,00", "0.00"),
        ("€1.790,51", "1790.51"),
        ("MX$7,726.65", "7726.65"),
        ("CAD$1,379.80", "1379.80"),
        ("£21,174.57", "21174.57"),
        ("€1.234.567,89", "1234567.89"),
        ("$1,000,000", "1000000"),
    ],
)
def test_parse_amount_auto_detects_each_currency_style(text, expected):
    assert parse_amount(text) == Decimal(expected)


@pytest.mark.parametrize("text", ["1,790", "€1.790", "10,0000.00", "1,000.00 500.00", "abc", None])
def test_parse_amount_auto_refuses_ambiguous_values(text):
    with pytest.raises(ValueError):
        parse_amount(text)


def test_empty_input_box_means_zero():
    assert parse_optional_amount("") == D(0)
    assert parse_optional_amount(None) == D(0)


def test_extract_currency():
    assert extract_currency("USD 1,000.00") == "USD"
    assert extract_currency("CA$75.00") == "CA$"
    assert extract_currency("CAD$1,379.80") == "CAD"
    assert extract_currency("MX$7,726.65") == "MX$"
    assert extract_currency("$75.00") == "$"
    assert extract_currency("€75,00") == "€"
    assert extract_currency("75.00") is None


def test_format_money():
    assert format_money(D(10000), "$") == "$10,000.00"
    assert format_money(D("2.5"), "USD") == "USD 2.50"
    assert format_money(D(1)) == "1.00"
