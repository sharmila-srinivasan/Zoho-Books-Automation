"""Pure accounting rules - no browser code here, so everything is unit-testable.

Rules implemented:
* application amount = min(remaining unused payment, invoice outstanding)
* an invoice is only eligible when customer and currency match, it is open,
  has an outstanding balance and has not already received money from this payment
* anything unclear is reported as AMBIGUOUS, which stops the run
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from .exceptions import AmountMismatchError
from .marketplace import Identification, MarketplaceRules
from .models import CheckStatus, Invoice, InvoiceCheck, Payment
from .utils import normalize_text

CENT = Decimal("0.01")
ZERO = Decimal("0")

# What a displayed currency symbol can stand for. "$" is shared by several
# currencies (US, Canada, Mexico...) so on its own it cannot prove a match.
SYMBOL_CURRENCIES: dict[str, frozenset[str]] = {
    "US$": frozenset({"USD"}),
    "CA$": frozenset({"CAD"}),
    "C$": frozenset({"CAD"}),
    "MX$": frozenset({"MXN"}),
    "A$": frozenset({"AUD"}),
    "NZ$": frozenset({"NZD"}),
    "HK$": frozenset({"HKD"}),
    "S$": frozenset({"SGD"}),
    "$": frozenset({"USD", "CAD", "MXN", "AUD", "NZD", "SGD", "HKD"}),
    "£": frozenset({"GBP"}),
    "€": frozenset({"EUR"}),
    "₹": frozenset({"INR"}),
    "¥": frozenset({"JPY", "CNY"}),
}

OPEN_STATUSES = {"open", "overdue", "partially paid", "unpaid", "sent", "due", "viewed"}
CLOSED_STATUSES = {"paid", "void", "draft", "closed", "written off", "writtenoff"}


def to_money(value: Decimal | int | str) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def calculate_application(remaining_payment: Decimal | int | str, invoice_outstanding: Decimal | int | str) -> Decimal:
    """Amount to apply: never more than the payment's remaining unused amount or the invoice balance."""
    remaining = to_money(remaining_payment)
    outstanding = to_money(invoice_outstanding)
    if remaining < ZERO or outstanding < ZERO:
        raise ValueError("Amounts used in an application cannot be negative.")
    return min(remaining, outstanding)


def expected_unused_after(unused_before: Decimal, applied: Decimal) -> Decimal:
    result = to_money(unused_before) - to_money(applied)
    if result < ZERO:
        raise AmountMismatchError(
            f"Applying {applied} would make the unused amount negative ({unused_before} available)."
        )
    return result


def plan_allocations(unused: Decimal | int | str, outstanding_amounts: list[Decimal | int | str]) -> list[Decimal]:
    """Allocate an unused amount across invoices in order (used for previews and tests)."""
    remaining = to_money(unused)
    plan: list[Decimal] = []
    for outstanding in outstanding_amounts:
        if remaining <= ZERO:
            break
        amount = calculate_application(remaining, outstanding)
        plan.append(amount)
        remaining -= amount
    return plan


def creator_matches(displayed_name: str, configured_names: tuple[str, ...] | list[str]) -> bool:
    """True when the displayed creator starts with a configured name, word by word.

    "Harish" matches "Harish" and "Harish N" but not "Harishankar" or "N Harish".
    """
    shown = normalize_text(displayed_name).replace(".", " ").split()
    if not shown:
        return False
    for name in configured_names:
        wanted = normalize_text(name).replace(".", " ").split()
        if wanted and shown[: len(wanted)] == wanted:
            return True
    return False


def compare_currency(first: str | None, second: str | None) -> str:
    """Returns "match", "mismatch" or "ambiguous"."""
    if not first or not second:
        return "ambiguous"
    if first == second:
        return "match"

    def candidates(marker: str) -> frozenset[str] | None:
        if marker.isalpha() and len(marker) == 3:
            return frozenset({marker.upper()})
        return SYMBOL_CURRENCIES.get(marker)

    a, b = candidates(first), candidates(second)
    if a is None or b is None:
        return "ambiguous"
    if not a & b:
        return "mismatch"
    if len(a) == 1 and a == b:
        return "match"
    return "ambiguous"


def validate_invoice(
    payment: Payment,
    invoice: Invoice,
    remaining: Decimal,
    outstanding: Decimal | None = None,
) -> InvoiceCheck:
    """Decide whether money from ``payment`` may be applied to ``invoice``.

    ``outstanding`` overrides invoice.outstanding (used by dry-run simulation).
    """
    balance = invoice.outstanding if outstanding is None else outstanding

    if remaining <= ZERO:
        return InvoiceCheck(CheckStatus.SKIP, "payment has no unused amount left")

    if not normalize_text(invoice.customer):
        return InvoiceCheck(CheckStatus.AMBIGUOUS, "invoice customer could not be read")
    if normalize_text(invoice.customer) != normalize_text(payment.customer):
        return InvoiceCheck(
            CheckStatus.SKIP, f"customer mismatch (invoice: {invoice.customer!r}, payment: {payment.customer!r})"
        )

    currency = compare_currency(payment.currency, invoice.currency)
    if currency == "mismatch":
        return InvoiceCheck(
            CheckStatus.SKIP, f"currency mismatch (invoice: {invoice.currency}, payment: {payment.currency})"
        )
    if currency == "ambiguous":
        return InvoiceCheck(
            CheckStatus.AMBIGUOUS,
            f"cannot confirm currency (invoice: {invoice.currency or 'unknown'}, payment: {payment.currency or 'unknown'})",
        )

    status = normalize_text(invoice.status)
    if status in CLOSED_STATUSES:
        return InvoiceCheck(CheckStatus.SKIP, f"invoice status is {invoice.status!r}")
    if status and status not in OPEN_STATUSES:
        return InvoiceCheck(CheckStatus.AMBIGUOUS, f"unrecognised invoice status {invoice.status!r}")

    if invoice.total < ZERO or balance < ZERO:
        return InvoiceCheck(CheckStatus.AMBIGUOUS, "invoice shows a negative amount")
    if balance == ZERO:
        return InvoiceCheck(CheckStatus.SKIP, "invoice is already fully paid (nothing outstanding)")
    if invoice.paid_amount is not None and invoice.paid_amount >= invoice.total:
        return InvoiceCheck(CheckStatus.SKIP, "invoice is already fully paid")
    if balance > invoice.total:
        return InvoiceCheck(CheckStatus.AMBIGUOUS, "outstanding amount is larger than the invoice total")
    if invoice.applied_from_this_payment != ZERO:
        return InvoiceCheck(
            CheckStatus.SKIP, "invoice already has an amount applied from this payment (review manually)"
        )
    return InvoiceCheck(CheckStatus.ELIGIBLE)


def payment_differences(before: Payment, after: Payment) -> list[tuple[str, str, str]]:
    """Fields that changed between the payment the user selected and what Zoho shows now."""
    changes: list[tuple[str, str, str]] = []

    def record(name: str, old: object, new: object) -> None:
        changes.append((name, str(old), str(new)))

    if before.payment_number != after.payment_number:
        record("Payment number", before.payment_number, after.payment_number)
    if normalize_text(before.customer) != normalize_text(after.customer):
        record("Customer", before.customer, after.customer)
    if before.amount != after.amount:
        record("Payment amount", before.amount, after.amount)
    if before.unused_amount != after.unused_amount:
        record("Unused amount", before.unused_amount, after.unused_amount)
    if before.currency and after.currency and compare_currency(before.currency, after.currency) == "mismatch":
        record("Currency", before.currency, after.currency)
    if before.reference and after.reference and before.reference.strip() != after.reference.strip():
        record("Reference", before.reference, after.reference)
    if before.payment_date and after.payment_date and before.payment_date.strip() != after.payment_date.strip():
        record("Payment date", before.payment_date, after.payment_date)
    return changes


@dataclass
class PaymentClassification:
    """Result of sorting the Payments Received list for one marketplace."""

    matching: list[Payment] = field(default_factory=list)
    unidentified: list[tuple[Payment, Identification]] = field(default_factory=list)
    other_marketplace: list[Payment] = field(default_factory=list)
    zero_unused: list[Payment] = field(default_factory=list)


def classify_payments(payments: list[Payment], marketplace: str, rules: MarketplaceRules) -> PaymentClassification:
    result = PaymentClassification()
    for payment in payments:
        if payment.unused_amount <= ZERO:
            result.zero_unused.append(payment)
            continue
        identification = rules.identify(payment)
        if not identification.is_identified:
            result.unidentified.append((payment, identification))
        elif identification.marketplace == marketplace:
            result.matching.append(payment)
        else:
            result.other_marketplace.append(payment)
    return result
