"""The reconciliation loop: Payment Received -> open invoices, one approved step at a time.

The engine never touches the browser directly. It talks to a ``PaymentGateway``
(the real one is ``zoho_gateway.ZohoGateway``; tests use a fake), which keeps
the accounting/safety logic testable without Zoho.

Loop (each step re-reads Zoho; nothing is assumed):

    read payment -> unused == 0 ? COMPLETE
                 -> find next eligible invoice (none? STOP)
                 -> calculate min(unused, outstanding)
                 -> ask YES/NO
                 -> DRY RUN: simulate only | LIVE: enter, save, re-read, verify
                 -> repeat (bounded by MAX_INVOICE_APPLICATIONS)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal
from typing import Protocol

from .accounting import (
    ZERO,
    calculate_application,
    expected_unused_after,
    payment_differences,
    validate_invoice,
)
from .approval import Console
from .exceptions import (
    AmountMismatchError,
    FinancialActionBlockedError,
    InvoiceMismatchError,
    PaymentVerificationError,
    UserAbortError,
)
from .logger import get_logger
from .models import (
    ApplicationProposal,
    AppliedRecord,
    Approval,
    CheckStatus,
    Decision,
    Invoice,
    Payment,
    ReconciliationResult,
    ResultStatus,
    RunContext,
)
from .utils import format_money

# Hard ceiling on declined proposals in one run, so the loop is always bounded.
MAX_DECLINED_INVOICES = 200


class PaymentGateway(Protocol):
    """What the engine needs from Zoho."""

    def open_payment(self, payment_number: str) -> Payment:
        """Open the payment for editing and return the values shown there."""

    def read_payment(self, payment_number: str) -> Payment:
        """Re-read the payment from the Payments Received list (independent source)."""

    def list_invoices(self, payment: Payment) -> list[Invoice]:
        """Invoices Zoho offers for applying this payment (payment must be open)."""

    def apply_payment(self, payment: Payment, invoice: Invoice, approval: Approval) -> None:
        """Enter the approved amount, verify the form, save, and wait for Zoho to finish."""


class ReconciliationEngine:
    def __init__(
        self,
        gateway: PaymentGateway,
        console: Console,
        *,
        dry_run: bool,
        max_applications: int,
        context: RunContext,
        capture: Callable[[str], None] = lambda step: None,
    ):
        if max_applications < 1:
            raise ValueError("max_applications must be at least 1")
        self.gateway = gateway
        self.console = console
        self.dry_run = dry_run
        self.max_applications = max_applications
        self.context = context
        self.capture = capture
        self.log = get_logger()

    # ------------------------------------------------------------------
    def confirm_opened_payment(self, selected: Payment) -> Payment:
        """Open the selected payment and make sure it is still what the user approved."""
        self.context.action = "Opening payment"
        opened = self.gateway.open_payment(selected.payment_number)
        opened_with_meta = replace(
            opened,
            created_by=opened.created_by or selected.created_by,
            marketplace=selected.marketplace,
        )
        self.capture("payment_opened")
        changes = payment_differences(selected, opened_with_meta)
        if changes:
            self.log.warning("Payment %s changed since selection: %s", selected.payment_number, changes)
            if not self.console.confirm_changed_payment(changes):
                raise UserAbortError("User did not re-approve the changed payment.")
            self.log.info("User re-approved payment %s with new values", selected.payment_number)
        return opened_with_meta

    # ------------------------------------------------------------------
    def run(self, payment: Payment) -> ReconciliationResult:
        number = payment.payment_number
        ctx = self.context
        ctx.payment_number = number
        result = ReconciliationResult(
            payment_number=number,
            status=ResultStatus.COMPLETE,
            starting_unused=payment.unused_amount,
            final_unused=payment.unused_amount,
            dry_run=self.dry_run,
        )
        expected_zoho_unused = payment.unused_amount
        simulated_total = ZERO
        simulated_by_invoice: dict[str, Decimal] = {}
        declined: set[str] = set()
        current = payment
        max_rounds = self.max_applications + MAX_DECLINED_INVOICES

        for round_number in range(1, max_rounds + 1):
            if round_number > 1:
                ctx.action = "Re-reading payment"
                current = self.gateway.open_payment(number)
                self._verify_same_payment(payment, current)

            if current.unused_amount != expected_zoho_unused:
                ctx.expected = f"unused {expected_zoho_unused}"
                ctx.actual = f"unused {current.unused_amount}"
                raise AmountMismatchError(
                    f"Unused amount in Zoho is {current.unused_amount}, expected {expected_zoho_unused}."
                )

            remaining = current.unused_amount - simulated_total
            result.final_unused = remaining
            self.log.info(
                "Unused amount: %s%s",
                format_money(current.unused_amount, current.currency),
                f" (after simulated dry-run applications: {format_money(remaining, current.currency)})"
                if simulated_total
                else "",
            )
            if remaining <= ZERO:
                result.status = ResultStatus.COMPLETE
                return result
            if len(result.applications) >= self.max_applications:
                self.log.warning("Maximum of %s applications reached - stopping safely.", self.max_applications)
                result.status = ResultStatus.MAX_APPLICATIONS_REACHED
                return result

            ctx.action = "Finding invoices"
            invoices = self.gateway.list_invoices(current)
            choice = self._next_invoice(current, invoices, remaining, simulated_by_invoice, declined)
            if choice is None:
                result.status = ResultStatus.NO_ELIGIBLE_INVOICES
                return result
            invoice, outstanding = choice
            ctx.invoice_number = invoice.invoice_number
            self.log.info(
                "Invoice found: %s (outstanding %s)",
                invoice.invoice_number,
                format_money(outstanding, invoice.currency),
            )
            self.capture("invoice_found")

            amount = calculate_application(remaining, outstanding)
            proposal = ApplicationProposal(
                payment_number=number,
                customer=current.customer,
                invoice_number=invoice.invoice_number,
                invoice_outstanding=outstanding,
                unused_before=remaining,
                application=amount,
                unused_after=remaining - amount,
                currency=current.currency,
            )
            self.log.info("Proposed application: %s to %s", format_money(amount, current.currency), invoice.invoice_number)
            decision, approval = self.console.propose_application(proposal, self.dry_run)

            if decision is Decision.STOP:
                self.log.info("User stopped the run at invoice %s", invoice.invoice_number)
                result.status = ResultStatus.STOPPED_BY_USER
                return result
            if decision is Decision.DECLINED or approval is None:
                self.log.info("User declined application to %s - trying the next invoice", invoice.invoice_number)
                declined.add(invoice.invoice_number)
                continue
            if not approval.matches(number, invoice.invoice_number, amount):
                raise FinancialActionBlockedError("Approval does not match the proposed application.")
            self.log.info("User approved invoice application %s -> %s", invoice.invoice_number, amount)

            if self.dry_run:
                simulated_total += amount
                simulated_by_invoice[invoice.invoice_number] = (
                    simulated_by_invoice.get(invoice.invoice_number, ZERO) + amount
                )
                result.applications.append(
                    AppliedRecord(invoice.invoice_number, amount, remaining - amount, simulated=True)
                )
                result.final_unused = remaining - amount
                self.console.say("DRY RUN: no change was made in Zoho. Simulating the result and continuing.")
                self.log.info("DRY RUN - simulated application of %s to %s", amount, invoice.invoice_number)
                continue

            # ---- LIVE: the only path that changes Zoho data --------------
            expected = expected_unused_after(current.unused_amount, amount)
            ctx.action = f"Applying {amount} to {invoice.invoice_number}"
            ctx.expected = f"unused after save {expected}"
            self.capture("before_application")
            self.gateway.apply_payment(current, invoice, approval)
            self.log.info("Application saved: %s -> %s", amount, invoice.invoice_number)

            ctx.action = "Verifying saved application"
            after = self.gateway.read_payment(number)
            self._verify_same_payment(payment, after)
            ctx.actual = f"unused after save {after.unused_amount}"
            self.capture("after_application")
            if after.unused_amount != expected:
                raise AmountMismatchError(
                    "APPLICATION VERIFICATION FAILED\n\n"
                    f"Expected unused amount: {format_money(expected, current.currency)}\n"
                    f"Actual unused amount: {format_money(after.unused_amount, current.currency)}\n\n"
                    "No further financial actions will be performed."
                )
            self.log.info("Verification successful: unused amount is now %s", format_money(expected, current.currency))
            result.applications.append(AppliedRecord(invoice.invoice_number, amount, expected, simulated=False))
            result.final_unused = expected
            expected_zoho_unused = expected
            ctx.invoice_number = ""

        raise FinancialActionBlockedError(f"Safety limit of {max_rounds} rounds reached - stopping.")

    # ------------------------------------------------------------------
    def _verify_same_payment(self, original: Payment, current: Payment) -> None:
        """Identity fields (not the unused amount) must never change during a run."""
        problems = [
            (name, before, after)
            for name, before, after in payment_differences(original, current)
            if name not in {"Unused amount"}
        ]
        if problems:
            details = "; ".join(f"{n}: {b} -> {a}" for n, b, a in problems)
            raise PaymentVerificationError(f"Payment {original.payment_number} changed unexpectedly: {details}")

    def _next_invoice(
        self,
        payment: Payment,
        invoices: list[Invoice],
        remaining: Decimal,
        simulated_by_invoice: dict[str, Decimal],
        declined: set[str],
    ) -> tuple[Invoice, Decimal] | None:
        numbers = [i.invoice_number for i in invoices]
        duplicates = {n for n in numbers if numbers.count(n) > 1}
        if duplicates:
            raise InvoiceMismatchError(f"Invoice number(s) listed more than once: {sorted(duplicates)}")
        if not invoices:
            self.log.info("Zoho shows no open invoices for %s", payment.customer)

        for invoice in invoices:
            if invoice.invoice_number in declined:
                continue
            outstanding = invoice.outstanding - simulated_by_invoice.get(invoice.invoice_number, ZERO)
            check = validate_invoice(payment, invoice, remaining, outstanding)
            if check.status is CheckStatus.AMBIGUOUS:
                self.context.invoice_number = invoice.invoice_number
                raise InvoiceMismatchError(
                    f"Invoice {invoice.invoice_number}: {check.reason}. "
                    "Stopping so you can review it - no change was made."
                )
            if check.status is CheckStatus.SKIP:
                self.log.info("Skipping invoice %s: %s", invoice.invoice_number, check.reason)
                continue
            return invoice, outstanding
        return None
