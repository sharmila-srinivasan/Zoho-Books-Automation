import pytest

from src.accounting import compare_currency, validate_invoice
from src.models import CheckStatus

from .conftest import D, make_invoice, make_payment


def check(invoice, remaining=4000, payment=None, outstanding=None):
    return validate_invoice(payment or make_payment(), invoice, D(remaining), outstanding)


def test_valid_open_invoice_is_eligible():
    assert check(make_invoice("INV-1", 2000)).status is CheckStatus.ELIGIBLE


def test_partially_paid_invoice_is_eligible():
    assert check(make_invoice("INV-1", 500, total=2000, status="Partially Paid")).status is CheckStatus.ELIGIBLE


def test_customer_mismatch_skipped():
    result = check(make_invoice("INV-1", 2000, customer="Other Co"))
    assert result.status is CheckStatus.SKIP and "customer" in result.reason


def test_customer_comparison_ignores_case_and_spacing():
    assert check(make_invoice("INV-1", 2000, customer="abc  company")).status is CheckStatus.ELIGIBLE


def test_missing_customer_is_ambiguous():
    assert check(make_invoice("INV-1", 2000, customer="")).status is CheckStatus.AMBIGUOUS


def test_currency_mismatch_skipped():
    result = check(make_invoice("INV-1", 2000, currency="CAD"))
    assert result.status is CheckStatus.SKIP and "currency" in result.reason


def test_unknown_currency_is_ambiguous():
    assert check(make_invoice("INV-1", 2000, currency=None)).status is CheckStatus.AMBIGUOUS


def test_already_paid_invoice_skipped():
    assert check(make_invoice("INV-1", 0, total=2000)).status is CheckStatus.SKIP
    assert check(make_invoice("INV-1", 2000, status="Paid")).status is CheckStatus.SKIP


def test_void_and_draft_invoices_skipped():
    assert check(make_invoice("INV-1", 2000, status="Void")).status is CheckStatus.SKIP
    assert check(make_invoice("INV-1", 2000, status="Draft")).status is CheckStatus.SKIP


def test_unknown_status_is_ambiguous():
    assert check(make_invoice("INV-1", 2000, status="Disputed")).status is CheckStatus.AMBIGUOUS


def test_already_applied_from_this_payment_skipped():
    result = check(make_invoice("INV-1", 2000, applied_from_this_payment=D(100)))
    assert result.status is CheckStatus.SKIP


def test_outstanding_larger_than_total_is_ambiguous():
    assert check(make_invoice("INV-1", 3000, total=2000)).status is CheckStatus.AMBIGUOUS


def test_no_remaining_payment_skips():
    assert check(make_invoice("INV-1", 2000), remaining=0).status is CheckStatus.SKIP


def test_simulated_outstanding_override():
    invoice = make_invoice("INV-1", 2000)
    assert check(invoice, outstanding=D(0)).status is CheckStatus.SKIP


@pytest.mark.parametrize(
    "a, b, expected",
    [
        ("USD", "USD", "match"),
        ("$", "$", "match"),
        ("USD", "US$", "match"),
        ("USD", "CAD", "mismatch"),
        ("USD", "€", "mismatch"),
        ("£", "GBP", "match"),
        ("USD", "$", "ambiguous"),
        ("CAD", "$", "ambiguous"),
        (None, "USD", "ambiguous"),
        ("USD", "Ж", "ambiguous"),
    ],
)
def test_compare_currency(a, b, expected):
    assert compare_currency(a, b) == expected
