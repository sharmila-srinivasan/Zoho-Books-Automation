"""Page object for Zoho's Edit Payment form, where money is applied to invoices.

This is the only module that types into an amount box or presses Save, and
it only does so when called by ZohoGateway.apply_payment after every safety
check has passed. See zoho_selectors.PaymentEditSelectors for what was
verified on the real form.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from playwright.sync_api import Page

from .config import Settings
from .exceptions import InvoiceNotFoundError, ZohoUIChangedError
from .logger import get_logger
from .page_helpers import TableData, cell_locator, read_labeled_text, read_labeled_values, read_table
from .utils import extract_currency, parse_amount, parse_optional_amount
from .zoho_selectors import PaymentEditSelectors as S


@dataclass(frozen=True)
class PaymentForm:
    customer: str
    amount_received: Decimal
    excess: Decimal
    payment_number: str
    reference: str
    payment_date: str
    currency: str | None


@dataclass(frozen=True)
class InvoiceRow:
    invoice_number: str
    invoice_date: str
    total_text: str
    due_text: str
    status: str
    total: Decimal
    outstanding: Decimal
    entered: Decimal  # value currently in this row's payment box


class InvoiceAllocationPage:
    def __init__(self, page: Page, settings: Settings):
        self.page = page
        self.settings = settings
        self.log = get_logger()

    # -- reading ----------------------------------------------------------
    def _money(self, text: str | None, what: str, currency: str | None = None) -> Decimal:
        separator = self.settings.amount_decimal_separator
        try:
            return parse_amount(text, separator)
        except ValueError as exc:
            # "1,790" is ambiguous on its own; the currency's display style decides.
            style = S.DECIMAL_STYLE.get(currency or "")
            if separator == "auto" and style:
                try:
                    return parse_amount(text, style)
                except ValueError:
                    pass
            raise ZohoUIChangedError(f"Edit Payment form: could not read {what} from {text!r}") from exc

    def _labeled_amount(self, labels: tuple[str, ...], what: str) -> Decimal:
        """A label can appear twice (form box and summary); every copy must show the same amount."""
        texts = read_labeled_values(self.page, labels)
        if not texts:
            raise ZohoUIChangedError(f"Edit Payment form: '{what}' not found (looked for labels {labels}).")
        amounts = {self._money(t, what) for t in texts}
        if len(amounts) != 1:
            raise ZohoUIChangedError(f"Edit Payment form: '{what}' shows different amounts: {texts}")
        return amounts.pop()

    def _required_label(self, labels: tuple[str, ...], what: str) -> str:
        value = read_labeled_text(self.page, labels, what)
        if value is None:
            raise ZohoUIChangedError(f"Edit Payment form: '{what}' not found (looked for labels {labels}).")
        return value

    def _form_currency(self) -> str | None:
        """Currency code shown inside the Amount Received box group (verified: "USD")."""
        box = self.page.get_by_label(S.AMOUNT_RECEIVED_BOX_LABEL)
        if box.count() != 1:
            raise ZohoUIChangedError(f"Edit Payment form: expected one 'Amount Received' box, found {box.count()}.")
        group_text = box.locator("xpath=..").inner_text()
        match = S.CURRENCY_CODE.fullmatch(group_text.strip())
        return match.group(1) if match else None

    def wait_until_open(self) -> None:
        last: Exception | None = None
        for _ in range(self.settings.action_timeout_seconds * 2):
            try:
                self._table()
                return
            except ZohoUIChangedError as exc:
                last = exc
                self.page.wait_for_timeout(500)
        raise ZohoUIChangedError(f"Edit Payment form with the invoice table did not appear. {last}")

    def read_form(self) -> PaymentForm:
        return PaymentForm(
            customer=self._required_label(S.CUSTOMER_LABELS, "Customer"),
            amount_received=self._labeled_amount(S.AMOUNT_RECEIVED_LABELS, "Amount Received"),
            excess=self._labeled_amount(S.EXCESS_LABELS, "Amount in Excess"),
            payment_number=read_labeled_text(self.page, S.PAYMENT_NUMBER_LABELS, "Payment #") or "",
            reference=read_labeled_text(self.page, S.REFERENCE_LABELS, "Reference") or "",
            payment_date=read_labeled_text(self.page, S.PAYMENT_DATE_LABELS, "Payment Date") or "",
            currency=self._form_currency(),
        )

    def _table(self) -> TableData:
        return read_table(self.page, S.INVOICE_COLUMNS, S.INVOICE_REQUIRED, "invoices (Edit Payment)")

    def read_invoices(self, currency: str | None = None) -> list[InvoiceRow]:
        table = self._table()
        invoices: list[InvoiceRow] = []
        for row in table.rows:
            number = table.text(row, "invoice_number")
            if not number:
                continue
            payment_cell = table.cell(row, "payment")
            if payment_cell is None or payment_cell.input_count != 1:
                raise ZohoUIChangedError(f"Invoice {number}: expected exactly one payment amount box in its row.")
            total_text = table.text(row, "invoice_amount")
            due_text = table.text(row, "amount_due")
            try:
                entered = parse_optional_amount(payment_cell.input_value, self.settings.amount_decimal_separator)
            except ValueError as exc:
                raise ZohoUIChangedError(f"Invoice {number}: unreadable payment box value") from exc
            invoices.append(
                InvoiceRow(
                    invoice_number=number,
                    invoice_date=table.text(row, "date"),
                    total_text=total_text,
                    due_text=due_text,
                    status=table.text(row, "status"),
                    total=self._money(total_text, f"invoice amount of {number}", currency),
                    outstanding=self._money(due_text, f"amount due of {number}", currency),
                    entered=entered,
                )
            )
        self.log.info("Edit Payment form lists %s invoices", len(invoices))
        return invoices

    @staticmethod
    def currency_of(row: InvoiceRow, form_currency: str | None) -> str | None:
        """Currency of an invoice row.

        The real table shows no currency symbol (verified 2026-10-06). Zoho's
        Edit Payment form lists only the selected customer's invoices, in the
        currency shown next to Amount Received, so that code applies. A symbol
        in the row itself, if Zoho ever shows one, takes precedence.
        """
        return extract_currency(row.total_text) or extract_currency(row.due_text) or form_currency

    # -- changing (only called from ZohoGateway.apply_payment) -------------
    def enter_amount(self, invoice_number: str, amount: Decimal) -> None:
        table = self._table()
        rows = [i for i, row in enumerate(table.rows) if table.text(row, "invoice_number") == invoice_number]
        if len(rows) != 1:
            raise InvoiceNotFoundError(f"Invoice {invoice_number} found {len(rows)} times in the form (expected 1).")
        box = cell_locator(self.page, rows[0], table.column_index["payment"]).locator(
            "input:not([type='checkbox']):not([type='hidden'])"
        )
        if box.count() != 1:
            raise ZohoUIChangedError(f"Invoice {invoice_number}: payment amount box not found.")
        text = f"{amount:.2f}"
        if self.settings.amount_decimal_separator == ",":
            text = text.replace(".", ",")
        box.fill(text)
        box.press("Tab")  # let Zoho recalculate its totals

    def click_save(self) -> None:
        save = self.page.get_by_role("button", name=S.SAVE_BUTTON_NAME)
        if save.count() != 1:
            raise ZohoUIChangedError(f"Expected exactly one Save button, found {save.count()}.")
        save.click()

    def wait_until_closed(self) -> bool:
        """True once the form (its invoice table) has gone away after Save."""
        for _ in range(self.settings.action_timeout_seconds * 2):
            try:
                self._table()
            except ZohoUIChangedError:
                return True
            self.page.wait_for_timeout(500)
        return False
