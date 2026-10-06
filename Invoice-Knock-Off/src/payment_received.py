"""Page objects for the Payments Received list and a single payment's detail view.

These classes only read the screen and click; all decisions are made in
accounting.py / invoice_reconciliation.py.
"""

from __future__ import annotations

from decimal import Decimal

from playwright.sync_api import Page

from .config import Settings
from .exceptions import PaymentNotFoundError, ZohoUIChangedError
from .logger import get_logger
from .models import Payment
from .page_helpers import TableData, cell_locator, read_labeled_text, read_table
from .utils import extract_currency, parse_amount
from .zoho_selectors import PaymentDetailSelectors, PaymentListSelectors


class PaymentsReceivedPage:
    def __init__(self, page: Page, settings: Settings):
        self.page = page
        self.settings = settings
        self.log = get_logger()
        self.has_created_by_column = False

    def _table(self) -> TableData:
        return read_table(self.page, PaymentListSelectors.COLUMNS, PaymentListSelectors.REQUIRED, "Payments Received")

    def _amount(self, text: str, what: str, number: str) -> Decimal:
        try:
            return parse_amount(text, self.settings.amount_decimal_separator)
        except ValueError as exc:
            raise ZohoUIChangedError(f"Payment {number}: could not read {what} from {text!r}") from exc

    def read_payments(self) -> list[Payment]:
        """All payments visible in the current list page."""
        table = self._table()
        self.has_created_by_column = "created_by" in table.column_index
        payments: list[Payment] = []
        for row in table.rows:
            number = table.text(row, "payment_number")
            if not number:
                continue
            amount_text = table.text(row, "amount")
            unused_text = table.text(row, "unused_amount")
            payments.append(
                Payment(
                    payment_number=number,
                    customer=table.text(row, "customer"),
                    amount=self._amount(amount_text, "amount", number),
                    unused_amount=self._amount(unused_text, "unused amount", number),
                    currency=extract_currency(amount_text) or extract_currency(unused_text),
                    reference=table.text(row, "reference"),
                    payment_date=table.text(row, "date"),
                    created_by=table.text(row, "created_by"),
                    notes=table.text(row, "notes"),
                    deposit_to=table.text(row, "deposit_to"),
                )
            )
        if table.skipped_rows:
            self.log.debug("Ignored %s non-data rows in the payments table", table.skipped_rows)
        self.log.info("Read %s payments from the Payments Received list", len(payments))
        return payments

    def find_payment(self, payment_number: str) -> Payment:
        matches = [p for p in self.read_payments() if p.payment_number == payment_number]
        if not matches:
            raise PaymentNotFoundError(
                f"Payment {payment_number} is not in the visible Payments Received list "
                "(check the list filter / page in Zoho)."
            )
        if len(matches) > 1:
            raise ZohoUIChangedError(f"Payment {payment_number} appears more than once in the list.")
        return matches[0]

    def open_payment(self, payment_number: str) -> None:
        table = self._table()
        rows = [i for i, row in enumerate(table.rows) if table.text(row, "payment_number") == payment_number]
        if len(rows) != 1:
            raise PaymentNotFoundError(f"Payment {payment_number} found {len(rows)} times in the list (expected 1).")
        cell = cell_locator(self.page, rows[0], table.column_index["payment_number"])
        cell.get_by_text(payment_number, exact=True).first.click()
        self.log.info("Opened payment %s", payment_number)


class PaymentDetailsPage:
    def __init__(self, page: Page, settings: Settings):
        self.page = page
        self.settings = settings

    def wait_until_open(self) -> None:
        self.page.get_by_role("button", name=PaymentDetailSelectors.EDIT_BUTTON_NAME).first.wait_for(state="visible")

    def read_created_by(self) -> str | None:
        """Who recorded the payment: a 'Created By' label if Zoho shows one, else the Payment History."""
        shown = read_labeled_text(self.page, PaymentDetailSelectors.CREATED_BY_LABELS, "Payment creator")
        return shown or self._creator_from_history()

    def _creator_from_history(self) -> str | None:
        s = PaymentDetailSelectors
        button = self.page.get_by_role("button", name=s.HISTORY_BUTTON_NAME, exact=True)
        if button.count() != 1:
            return None
        button.click()
        heading = self.page.get_by_role("heading", name=s.HISTORY_BUTTON_NAME, exact=True)
        heading.wait_for(state="visible")
        # Verified DOM: the heading sits in the side bar's header and the entries in its body,
        # so take the nearest ancestor of the heading that contains list entries.
        entries = heading.locator("xpath=ancestor::*[.//li][1]").get_by_role("listitem")
        entries.first.wait_for(state="visible")
        texts = [" ".join(t.split()) for t in entries.all_inner_texts()]
        close = self.page.get_by_role("button", name=s.HISTORY_CLOSE_BUTTON_NAME)
        if close.count():
            close.first.click()
        creators = set()
        for text in texts:
            match = s.HISTORY_ENTRY.match(text)
            if match and s.CREATION_ENTRY_TEXT.match(match.group("text").strip()):
                creators.add(match.group("name").strip())
        if len(creators) > 1:
            raise ZohoUIChangedError(f"Payment History shows more than one creator: {sorted(creators)}")
        if not creators:
            raise ZohoUIChangedError(f"No 'Payment of ... received' entry in Payment History: {texts}")
        return creators.pop()

    def click_edit(self) -> None:
        self.page.get_by_role("button", name=PaymentDetailSelectors.EDIT_BUTTON_NAME).first.click()
