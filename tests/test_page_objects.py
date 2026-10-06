"""Browser-level tests against SYNTHETIC local HTML (tests/fixtures), never against Zoho.

They prove the page-reading mechanics and the pre-save safety checks work.
They do NOT prove that Zoho's real pages look like the fixtures - that is
what `python -m src.main --inspect` and a dry run against Zoho are for.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from src import zoho_selectors
from src.browser import sanitize_html
from src.config import Settings
from src.exceptions import AmountMismatchError, FinancialActionBlockedError, ZohoUIChangedError
from src.invoice_allocation import InvoiceAllocationPage
from src.models import Approval
from src.page_helpers import read_labeled_text
from src.payment_received import PaymentsReceivedPage
from src.zoho_gateway import ZohoGateway

from .conftest import D, make_invoice, make_payment

FIXTURES = Path(__file__).parent / "fixtures"
pytestmark = pytest.mark.browser


@pytest.fixture(scope="module")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch(headless=True)
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"Chromium not installed ({exc}); run: uv run playwright install chromium")
        yield b
        b.close()


@pytest.fixture
def page(browser):
    pg = browser.new_page()
    pg.set_default_timeout(3000)
    yield pg
    pg.close()


@pytest.fixture
def settings():
    return Settings(zoho_email="", zoho_password="", zoho_base_url="https://books.zoho.com",
                    dry_run=False, action_timeout_seconds=5, save_debug_screenshots=False)


def load(page, name):
    page.goto((FIXTURES / name).as_uri())


def test_payments_list_read_by_column_headings(page, settings):
    load(page, "payments_list.html")
    payments = PaymentsReceivedPage(page, settings).read_payments()
    assert [p.payment_number for p in payments] == ["PAY-001", "PAY-002"]
    first = payments[0]
    assert first.customer == "Amazon US" and first.amount == D(10000) and first.unused_amount == D(4000)
    assert first.currency == "$" and first.reference == "REF-1"
    assert payments[1].unused_amount == D(0) and payments[1].currency == "£"


def test_missing_column_reports_ui_change(page, settings):
    load(page, "payments_list.html")
    page.evaluate("document.querySelectorAll('th')[7].innerText = 'Something else'")
    with pytest.raises(ZohoUIChangedError) as error:
        PaymentsReceivedPage(page, settings).read_payments()
    assert "Column headings seen" in str(error.value)


def test_open_payment_clicks_exact_link(page, settings):
    load(page, "payments_list.html")
    PaymentsReceivedPage(page, settings).open_payment("PAY-002")
    assert page.url.endswith("#p2")


def test_labeled_values(page):
    load(page, "edit_payment.html")
    assert read_labeled_text(page, ("Customer Name",), "customer") == "ABC Company"
    assert read_labeled_text(page, ("Amount in Excess",), "excess") == "4,000.00"
    assert read_labeled_text(page, ("No Such Label",), "x") is None


def test_edit_form_reading(page, settings):
    load(page, "edit_payment.html")
    form_page = InvoiceAllocationPage(page, settings)
    form = form_page.read_form()
    assert form.customer == "ABC Company" and form.amount_received == D(10000) and form.excess == D(4000)
    rows = form_page.read_invoices()
    assert [(r.invoice_number, r.total, r.outstanding, r.entered) for r in rows] == [
        ("INV-1001", D(2000), D(2000), D(0)),
        ("INV-1002", D(5000), D(1500), D(0)),
    ]
    assert InvoiceAllocationPage.currency_of(rows[0], None) == "USD"


def test_invoice_currency_comes_from_form_when_table_has_no_symbol(page, settings):
    # Real Zoho (2026-10-06): table shows "430,132.33"; the code sits in the Amount Received box group.
    load(page, "edit_payment.html")
    page.evaluate("""() => document.querySelectorAll('#invoices td').forEach(td => {
        if (td.children.length === 0) td.textContent = td.textContent.replace('USD ', '');
    })""")
    form_page = InvoiceAllocationPage(page, settings)
    form = form_page.read_form()
    assert form.currency == "USD"
    rows = form_page.read_invoices(form.currency)
    assert rows[0].total_text == "2,000.00"
    assert InvoiceAllocationPage.currency_of(rows[0], form.currency) == "USD"


# --- the single save path -----------------------------------------------------

def _open_gateway(page, settings):
    load(page, "edit_payment.html")
    gateway = ZohoGateway(page, settings)
    gateway._open_payment = "PAY-001"  # as if open_payment() had navigated here
    return gateway


PAYMENT = make_payment(currency="USD")
INVOICE = make_invoice("INV-1001", 2000)
APPROVAL = Approval("PAY-001", "INV-1001", D(2000), "now")


def test_apply_blocked_in_dry_run(page, settings, monkeypatch):
    monkeypatch.setattr(zoho_selectors, "LIVE_UI_VERIFIED", True)
    gateway = _open_gateway(page, replace(settings, dry_run=True))
    with pytest.raises(FinancialActionBlockedError):
        gateway.apply_payment(PAYMENT, INVOICE, APPROVAL)
    assert page.locator("#save").count() == 1  # never clicked


def test_apply_blocked_while_ui_unverified(page, settings, monkeypatch):
    monkeypatch.setattr(zoho_selectors, "LIVE_UI_VERIFIED", False)
    gateway = _open_gateway(page, settings)
    with pytest.raises(FinancialActionBlockedError):
        gateway.apply_payment(PAYMENT, INVOICE, APPROVAL)


def test_apply_blocked_when_approval_differs(page, settings, monkeypatch):
    monkeypatch.setattr(zoho_selectors, "LIVE_UI_VERIFIED", True)
    gateway = _open_gateway(page, settings)
    with pytest.raises(FinancialActionBlockedError):
        gateway.apply_payment(PAYMENT, INVOICE, Approval("PAY-001", "INV-1001", D(1999), "now"))


def test_apply_enters_amount_verifies_and_saves(page, settings, monkeypatch):
    monkeypatch.setattr(zoho_selectors, "LIVE_UI_VERIFIED", True)
    gateway = _open_gateway(page, settings)
    gateway.apply_payment(PAYMENT, INVOICE, APPROVAL)
    assert page.locator("#save").count() == 0  # Save was pressed and the form closed


def test_apply_refuses_to_save_when_form_total_disagrees(page, settings, monkeypatch):
    monkeypatch.setattr(zoho_selectors, "LIVE_UI_VERIFIED", True)
    gateway = _open_gateway(page, settings)
    page.evaluate("BASE_USED = 6500")  # the form now computes a different excess
    with pytest.raises(AmountMismatchError):
        gateway.apply_payment(PAYMENT, INVOICE, APPROVAL)
    assert page.locator("#save").count() == 1  # not saved


def test_sanitize_html_removes_secrets():
    html = (
        '<meta name="csrf-token" content="abc123"><input type="password" value="S3cret!">'
        '<input name="amount" value="100.00"><script>var t="zz-script-secret";</script>'
    )
    clean = sanitize_html(html)
    assert "abc123" not in clean and "S3cret!" not in clean and "zz-script-secret" not in clean
    assert 'value="100.00"' in clean


def test_sign_in_pages_are_never_captured():
    from src.browser import is_sign_in_url

    assert is_sign_in_url("https://accounts.zoho.com/signin?servicename=ZohoBooks")
    assert is_sign_in_url("https://accounts.zoho.in/signin")
    assert not is_sign_in_url("https://books.zoho.com/app/123#/paymentsreceived")
