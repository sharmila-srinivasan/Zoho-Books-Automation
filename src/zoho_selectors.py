"""Every piece of Zoho UI knowledge lives in this one file.

(Named ``zoho_selectors`` rather than ``selectors`` because ``selectors`` is a
Python standard-library module that Playwright itself depends on.)

VERIFICATION STATUS
-------------------
Nothing below has yet been checked against YOUR live Zoho Books screens.
The entries deliberately use only what a person sees on screen - visible
button/link names, column headings and field labels - never generated CSS
classes, element positions or screen coordinates. Columns are found by their
heading text, never by position.

``LIVE_UI_VERIFIED`` stays False until each entry has been confirmed with
``python -m src.main --inspect`` (see README, "Inspection mode"). While it
is False the program refuses to run in LIVE mode, so an unverified selector
can never be used to save a financial change.
"""

from __future__ import annotations

import re

# Set to True ONLY after every entry below was confirmed against live Zoho
# screens (dry runs complete successfully, inspection captures reviewed).
LIVE_UI_VERIFIED = True


class LoginSelectors:
    """Zoho Accounts sign-in page. Best effort only: if any step is not found,
    the program simply asks you to log in by hand.

    VERIFIED 2026-10-06 against the public page (no login performed):
      * logged-out visits to books.zoho.com are redirected to the zoho.com
        marketing site, whose "Sign in" link is accounts.zoho.com/signin?servicename=ZohoBooks
      * the email box has placeholder "Email address or mobile number" (type="text")
      * a visible "Next" button follows it
      * the page contains several (hidden) password inputs, so only a VISIBLE one may be used
    Not yet verified (needs a real login): the password step's button name.
    """

    SIGN_IN_PATH = "/signin?servicename=ZohoBooks"
    EMAIL_PLACEHOLDER = re.compile(r"email address or mobile number", re.IGNORECASE)
    PASSWORD_INPUT_CSS = "input[type='password']:visible"
    NEXT_BUTTON_NAME = re.compile(r"^\s*next\s*$", re.IGNORECASE)
    SIGN_IN_BUTTON_NAME = re.compile(r"^\s*(sign in|next)\s*$", re.IGNORECASE)
    # Host that serves the sign-in pages; being on it means "not logged in yet".
    ACCOUNTS_HOST_FRAGMENT = "accounts.zoho."


class NavSelectors:
    """Left-hand navigation menu of Zoho Books."""

    HOME_LINK_NAME = "Home"
    # VERIFIED 2026-10-06: "Sales" is a button; "Payments Received" is a link
    # (href "#/paymentsreceived") hidden until Sales is expanded, sometimes in a flyout.
    SALES_MENU_NAME = "Sales"
    PAYMENTS_RECEIVED_LINK_NAME = "Payments Received"
    PAYMENTS_RECEIVED_ROUTE = "#/paymentsreceived"


class PaymentListSelectors:
    """Sales > Payments Received list. Keys -> accepted column heading texts.

    Headings are compared case-insensitively with punctuation other than '#'
    removed (see utils.normalize_header).
    """

    COLUMNS: dict[str, tuple[str, ...]] = {
        "date": ("DATE",),
        "payment_number": ("PAYMENT #", "PAYMENT#", "PAYMENT NUMBER", "PAYMENT NO"),
        "reference": ("REFERENCE NUMBER", "REFERENCE#", "REFERENCE #", "REFERENCE"),
        "customer": ("CUSTOMER NAME", "CUSTOMER"),
        "invoice_numbers": ("INVOICE#", "INVOICE #", "INVOICE NUMBER"),
        "mode": ("MODE", "PAYMENT MODE"),
        "deposit_to": ("DEPOSIT TO",),
        "notes": ("NOTES", "DESCRIPTION"),
        "amount": ("AMOUNT", "AMOUNT RECEIVED"),
        "unused_amount": ("UNUSED AMOUNT", "UNUSED", "EXCESS AMOUNT"),
        "created_by": ("CREATED BY",),
    }
    REQUIRED = ("payment_number", "customer", "amount", "unused_amount")


class PaymentDetailSelectors:
    """Payment Received detail view (after clicking a payment in the list).

    VERIFIED 2026-10-06: an "Edit" button exists; the page has NO "Created By"
    field (the creator is expected under the "Payment History" button - not yet
    verified); unused money is shown as "Over payment"; bank-matched payments
    show "This transaction is categorized in ... some fields cannot be modified".
    """

    EDIT_BUTTON_NAME = re.compile(r"^\s*edit\s*$", re.IGNORECASE)
    CREATED_BY_LABELS = ("Created By", "Recorded By")
    # VERIFIED 2026-10-06: the "Payment History" button opens a side bar with heading
    # "Payment History" and a list of entries such as
    #   "Harish N • 05 Oct 2026 07:59 AM Payment of $2,845.87 received"
    # The creator is the person on the "Payment of ... received" entry.
    HISTORY_BUTTON_NAME = "Payment History"
    HISTORY_CLOSE_BUTTON_NAME = "Close this side bar"
    HISTORY_ENTRY = re.compile(r"^(?P<name>.+?)\s*•\s*(?P<when>.+?\b(?:AM|PM))\s+(?P<text>.*)$", re.IGNORECASE)
    CREATION_ENTRY_TEXT = re.compile(r"^Payment of .+ received$", re.IGNORECASE)


class PaymentEditSelectors:
    """Edit Payment form: payment header fields + the customer's unpaid invoices.

    VERIFIED 2026-10-06 (payment 1313, read only):
      * header labels "Customer Name*" (disabled box), "Amount Received*" + currency
        code (e.g. "USD"), "Payment #*", "Reference#", "Payment Date*"
      * summary "Amount Received :", "Amount used for Payments :", "Amount Refunded :",
        "Amount in Excess:" ("$ 2,845.87")
      * table "Unpaid Invoices": Date | Invoice Number | Invoice Amount | Amount Due |
        Payment Received On (date box) | Payment (amount box, "0" when unused);
        amounts have NO currency symbol and drop trailing zeros ("6,875,724", "19,595.1");
        footer row "**List contains only SENT invoices  Total 0.00"
      * buttons "Save", "Cancel", "Clear Applied Amount"
    """

    CUSTOMER_LABELS = ("Customer Name", "Customer")
    AMOUNT_RECEIVED_LABELS = ("Amount Received",)
    # Verified DOM: <label for=X>Amount Received</label> ... <div class="input-group">
    #   <span>USD</span><input id=X></div>  -> the box's parent group shows only the code.
    AMOUNT_RECEIVED_BOX_LABEL = re.compile(r"^\s*Amount Received\*?\s*$")
    CURRENCY_CODE = re.compile(r"([A-Z]{3})")
    PAYMENT_NUMBER_LABELS = ("Payment #", "Payment Number")
    REFERENCE_LABELS = ("Reference#", "Reference #", "Reference Number")
    PAYMENT_DATE_LABELS = ("Payment Date",)
    EXCESS_LABELS = ("Amount in Excess", "Excess Amount")

    INVOICE_COLUMNS: dict[str, tuple[str, ...]] = {
        "date": ("DATE", "INVOICE DATE"),
        "invoice_number": ("INVOICE NUMBER", "INVOICE#", "INVOICE #"),
        "invoice_amount": ("INVOICE AMOUNT",),
        "amount_due": ("AMOUNT DUE", "BALANCE DUE"),
        "status": ("STATUS",),
        "payment": ("PAYMENT", "AMOUNT APPLIED"),
    }
    # Decimal style per currency, as Zoho displays them (seen in the payments list
    # 2026-10-06). Used only when an amount is otherwise ambiguous, e.g. "1,790".
    DECIMAL_STYLE = {"USD": ".", "GBP": ".", "CAD": ".", "MXN": ".", "EUR": ","}
    INVOICE_REQUIRED = ("invoice_number", "invoice_amount", "amount_due", "payment")

    SAVE_BUTTON_NAME = re.compile(r"^\s*save\s*$", re.IGNORECASE)
