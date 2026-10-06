"""Plain data objects shared by the business logic and the browser layer.

All money values are ``decimal.Decimal`` - never ``float`` - so that amounts
such as 0.10 + 0.20 are exact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


@dataclass(frozen=True)
class Payment:
    """One Zoho "Payment Received" entry as read from the screen."""

    payment_number: str
    customer: str
    amount: Decimal
    unused_amount: Decimal
    currency: str | None = None
    reference: str = ""
    payment_date: str = ""
    created_by: str = ""
    notes: str = ""
    deposit_to: str = ""
    marketplace: str | None = None
    # Only set when Zoho shows it; otherwise derived from amount - unused.
    used_amount_shown: Decimal | None = None

    @property
    def used_amount(self) -> Decimal:
        if self.used_amount_shown is not None:
            return self.used_amount_shown
        return self.amount - self.unused_amount


@dataclass(frozen=True)
class Invoice:
    """One open invoice offered by Zoho for this payment's customer."""

    invoice_number: str
    customer: str
    invoice_date: str
    currency: str | None
    total: Decimal
    outstanding: Decimal
    paid_amount: Decimal | None = None
    # Amount of THIS payment already applied to the invoice (Zoho pre-fills it).
    applied_from_this_payment: Decimal = Decimal("0")
    status: str = ""


@dataclass(frozen=True)
class ApplicationProposal:
    payment_number: str
    customer: str
    invoice_number: str
    invoice_outstanding: Decimal
    unused_before: Decimal
    application: Decimal
    unused_after: Decimal
    currency: str | None


@dataclass(frozen=True)
class Approval:
    """Proof that the user typed YES for exactly this payment/invoice/amount."""

    payment_number: str
    invoice_number: str
    amount: Decimal
    approved_at: str

    def matches(self, payment_number: str, invoice_number: str, amount: Decimal) -> bool:
        return (
            self.payment_number == payment_number
            and self.invoice_number == invoice_number
            and self.amount == amount
        )


class Decision(Enum):
    APPROVED = "approved"
    DECLINED = "declined"
    STOP = "stop"


class CheckStatus(Enum):
    ELIGIBLE = "eligible"
    SKIP = "skip"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class InvoiceCheck:
    status: CheckStatus
    reason: str = ""


@dataclass(frozen=True)
class AppliedRecord:
    invoice_number: str
    amount: Decimal
    unused_after: Decimal
    simulated: bool


class ResultStatus(Enum):
    COMPLETE = "Unused amount is zero - payment fully applied"
    NO_ELIGIBLE_INVOICES = "No further eligible invoice for this payment"
    STOPPED_BY_USER = "Stopped by user"
    MAX_APPLICATIONS_REACHED = "Maximum number of invoice applications reached"


@dataclass
class ReconciliationResult:
    payment_number: str
    status: ResultStatus
    starting_unused: Decimal
    final_unused: Decimal
    dry_run: bool
    applications: list[AppliedRecord] = field(default_factory=list)


@dataclass
class RunContext:
    """What the automation is doing right now - written into error reports."""

    marketplace: str = ""
    payment_number: str = ""
    invoice_number: str = ""
    action: str = ""
    expected: str = ""
    actual: str = ""

    def as_text(self) -> str:
        return "\n".join(
            [
                f"Marketplace: {self.marketplace or '-'}",
                f"Payment: {self.payment_number or '-'}",
                f"Invoice: {self.invoice_number or '-'}",
                f"Current action: {self.action or '-'}",
                f"Expected state: {self.expected or '-'}",
                f"Actual state: {self.actual or '-'}",
            ]
        )
