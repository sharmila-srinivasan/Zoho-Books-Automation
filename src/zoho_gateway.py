"""Connects the reconciliation engine to the real Zoho Books pages.

``apply_payment`` is the single place where a financial change can be saved.
Before pressing Save it checks, in order:
  1. not DRY_RUN, and the Zoho UI has been verified (zoho_selectors.LIVE_UI_VERIFIED)
  2. the approval matches this exact payment, invoice and amount
  3. the open form belongs to this payment (number, customer, amount received)
  4. the invoice row is unchanged since it was proposed and has nothing entered yet
  5. after typing: ONLY that invoice's box changed, and it shows the approved amount
  6. Zoho's "Amount in Excess" equals unused-before minus the application
Any failure raises before Save is pressed.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from playwright.sync_api import Page

from . import zoho_selectors
from .accounting import (
    ZERO,
    calculate_application,
    classify_payments,
    compare_currency,
    creator_matches,
    expected_unused_after,
)
from .config import Settings
from .exceptions import (
    AmountMismatchError,
    FinancialActionBlockedError,
    InvoiceMismatchError,
    PaymentVerificationError,
    ZohoUIChangedError,
)
from .invoice_allocation import InvoiceAllocationPage, InvoiceRow
from .logger import get_logger
from .marketplace import Identification, MarketplaceRules
from .models import Approval, Invoice, Payment
from .payment_received import PaymentDetailsPage, PaymentsReceivedPage
from .utils import normalize_text
from .zoho_navigation import ZohoNavigator


@dataclass
class Discovery:
    candidates: list[Payment] = field(default_factory=list)
    unidentified: list[tuple[Payment, Identification]] = field(default_factory=list)
    other_creator: list[Payment] = field(default_factory=list)
    zero_unused: int = 0
    other_marketplace: int = 0
    not_reviewed: int = 0
    total_rows: int = 0


class ZohoGateway:
    def __init__(self, page: Page, settings: Settings, capture=lambda step: None):
        self.page = page
        self.settings = settings
        self.capture = capture
        self.log = get_logger()
        self.nav = ZohoNavigator(page, settings)
        self.payments_page = PaymentsReceivedPage(page, settings)
        self.details_page = PaymentDetailsPage(page, settings)
        self.form = InvoiceAllocationPage(page, settings)
        self._creators: dict[str, str] = {}
        self._open_payment: str | None = None

    # ------------------------------------------------------------------
    # Discovery (read only)
    # ------------------------------------------------------------------
    def discover_payments(self, marketplace: str, rules: MarketplaceRules) -> Discovery:
        self.nav.open_payments_received()
        self.capture("payments_page")
        payments = self.payments_page.read_payments()
        groups = classify_payments(payments, marketplace, rules)
        found = Discovery(
            unidentified=groups.unidentified,
            zero_unused=len(groups.zero_unused),
            other_marketplace=len(groups.other_marketplace),
            total_rows=len(payments),
        )
        to_check = groups.matching
        if not self.payments_page.has_created_by_column:
            limit = self.settings.max_payments_to_review
            found.not_reviewed = max(len(to_check) - limit, 0)
            to_check = [replace(p, created_by=self._creator_from_details(p)) for p in to_check[:limit]]
        for payment in to_check:
            self._creators[payment.payment_number] = payment.created_by
            if creator_matches(payment.created_by, self.settings.payment_created_by):
                self.log.info("Payment found: %s", payment.payment_number)
                self.log.info("Creator: %s", payment.created_by)
                self.log.info("Unused amount: %s", payment.unused_amount)
                found.candidates.append(replace(payment, marketplace=marketplace))
            else:
                found.other_creator.append(payment)
        return found

    def _creator_from_details(self, payment: Payment) -> str:
        self.payments_page.open_payment(payment.payment_number)
        self.details_page.wait_until_open()
        creator = self.details_page.read_created_by()
        self.nav.open_payments_received()
        if not creator:
            raise ZohoUIChangedError(
                f"Could not find who created payment {payment.payment_number}: there is no 'Created By' "
                "column in the list and no 'Created By' label on the payment page."
            )
        return creator

    # ------------------------------------------------------------------
    # PaymentGateway interface
    # ------------------------------------------------------------------
    def read_payment(self, payment_number: str) -> Payment:
        self._open_payment = None
        self.nav.open_payments_received()
        payment = self.payments_page.find_payment(payment_number)
        return replace(payment, created_by=payment.created_by or self._creators.get(payment_number, ""))

    def open_payment(self, payment_number: str) -> Payment:
        listed = self.read_payment(payment_number)
        self.payments_page.open_payment(payment_number)
        self.details_page.wait_until_open()
        self.details_page.click_edit()
        self.form.wait_until_open()
        form = self.form.read_form()
        if form.payment_number and form.payment_number.strip() != payment_number:
            raise PaymentVerificationError(
                f"Opened form shows payment {form.payment_number!r}, expected {payment_number!r}."
            )
        self._open_payment = payment_number
        return Payment(
            payment_number=payment_number,
            customer=form.customer,
            amount=form.amount_received,
            unused_amount=form.excess,
            # The form shows the currency CODE (e.g. USD); the list only a symbol ($).
            currency=form.currency or listed.currency,
            reference=form.reference or listed.reference,
            payment_date=form.payment_date or listed.payment_date,
            created_by=listed.created_by,
            notes=listed.notes,
            deposit_to=listed.deposit_to,
        )

    def list_invoices(self, payment: Payment) -> list[Invoice]:
        if self._open_payment != payment.payment_number:
            self.open_payment(payment.payment_number)
        form = self.form.read_form()
        return [
            self._to_invoice(row, form.customer, form.currency)
            for row in self.form.read_invoices(form.currency)
        ]

    @staticmethod
    def _to_invoice(row: InvoiceRow, customer: str, form_currency: str | None) -> Invoice:
        # Zoho's Edit Payment form lists only the selected customer's invoices,
        # so the invoice customer is the form's customer.
        return Invoice(
            invoice_number=row.invoice_number,
            customer=customer,
            invoice_date=row.invoice_date,
            currency=InvoiceAllocationPage.currency_of(row, form_currency),
            total=row.total,
            outstanding=row.outstanding,
            paid_amount=row.total - row.outstanding,
            applied_from_this_payment=row.entered,
            status=row.status,
        )

    def apply_payment(self, payment: Payment, invoice: Invoice, approval: Approval) -> None:
        number, inv_no = payment.payment_number, invoice.invoice_number
        # Recomputed here independently - never taken from the approval itself.
        amount = calculate_application(payment.unused_amount, invoice.outstanding)

        # 1-2. global and approval guards
        if self.settings.dry_run:
            raise FinancialActionBlockedError("DRY_RUN is on - saving is not allowed.")
        if not zoho_selectors.LIVE_UI_VERIFIED:
            raise FinancialActionBlockedError("The Zoho UI has not been verified (LIVE_UI_VERIFIED is False).")
        if not approval.matches(number, inv_no, amount) or amount <= ZERO:
            raise FinancialActionBlockedError(
                f"Approval ({approval.invoice_number}, {approval.amount}) does not match the calculated "
                f"application ({inv_no}, {amount}). Not saved."
            )
        if self._open_payment != number:
            raise FinancialActionBlockedError(f"The Edit Payment form for {number} is not open.")

        # 3. the open form is this payment
        form = self.form.read_form()
        if normalize_text(form.customer) != normalize_text(payment.customer):
            raise PaymentVerificationError(f"Form customer {form.customer!r} != payment customer {payment.customer!r}.")
        if form.amount_received != payment.amount:
            raise PaymentVerificationError(f"Form amount {form.amount_received} != payment amount {payment.amount}.")
        if form.excess != payment.unused_amount:
            raise AmountMismatchError(f"Form excess {form.excess} != unused amount {payment.unused_amount}.")

        # 4. the invoice row is still as proposed
        rows = {row.invoice_number: row for row in self.form.read_invoices(form.currency)}
        row = rows.get(inv_no)
        if row is None:
            raise InvoiceMismatchError(f"Invoice {inv_no} is no longer in the form.")
        if row.outstanding != invoice.outstanding or row.total != invoice.total:
            raise InvoiceMismatchError(f"Invoice {inv_no} amounts changed since it was proposed.")
        if row.entered != ZERO:
            raise InvoiceMismatchError(f"Invoice {inv_no} already has {row.entered} entered.")
        if compare_currency(payment.currency, InvoiceAllocationPage.currency_of(row, form.currency)) != "match":
            raise InvoiceMismatchError(f"Invoice {inv_no} currency could not be confirmed.")
        before = {k: r.entered for k, r in rows.items()}

        # 5. type the amount, then prove only that box changed
        self.log.info("Entering %s for invoice %s", amount, inv_no)
        self.form.enter_amount(inv_no, amount)
        after = {r.invoice_number: r.entered for r in self.form.read_invoices(form.currency)}
        expected_boxes = {**before, inv_no: amount}
        if after != expected_boxes:
            changed = {k: (before.get(k), after.get(k)) for k in set(before) | set(after) if before.get(k) != after.get(k)}
            raise InvoiceMismatchError(f"Form amounts are not as expected after typing (changed: {changed}). Not saved.")

        # 6. Zoho's own total must agree with our arithmetic
        form_after = self.form.read_form()
        expected_excess = expected_unused_after(payment.unused_amount, amount)
        same_customer = normalize_text(form_after.customer) == normalize_text(payment.customer)
        if form_after.amount_received != payment.amount or not same_customer:
            raise PaymentVerificationError("Payment amount or customer changed in the form. Not saved.")
        if form_after.excess != expected_excess:
            raise AmountMismatchError(
                f"Zoho shows Amount in Excess {form_after.excess}, expected {expected_excess}. Not saved."
            )

        self.capture("before_save")
        self.form.click_save()
        if not self.form.wait_until_closed():
            raise ZohoUIChangedError("Zoho did not close the Edit Payment form after Save - the save may have failed.")
        self._open_payment = None
        self.log.info("Zoho accepted the save for %s -> %s", number, inv_no)
