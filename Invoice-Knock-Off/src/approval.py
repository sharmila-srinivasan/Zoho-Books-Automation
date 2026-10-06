"""Everything the user sees and answers in the terminal.

Only an explicit YES (any letter case) approves. Anything else - including an
empty line, "Y" or a closed input stream - is treated as NO.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from .exceptions import UserAbortError
from .marketplace import MARKETPLACES, InvalidMarketplaceError, normalize_marketplace
from .models import ApplicationProposal, Approval, Decision, Payment
from .utils import format_money

LINE = "=" * 40


class Console:
    def __init__(self, input_fn: Callable[[str], str] = input, output_fn: Callable[[str], None] = print):
        self._input = input_fn
        self._output = output_fn

    # -- basics -----------------------------------------------------------
    def say(self, text: str = "") -> None:
        self._output(text)

    def banner(self, title: str) -> None:
        self.say(LINE)
        self.say(title)
        self.say(LINE)

    def ask(self, prompt: str) -> str:
        try:
            return self._input(prompt)
        except EOFError as exc:
            raise UserAbortError("Input closed - stopping.") from exc

    def confirm_yes(self, prompt: str = "Type YES or NO: ") -> bool:
        return self.ask(prompt).strip().upper() == "YES"

    def wait_for_enter(self, message: str) -> None:
        self.say(message)
        self.ask("")

    # -- marketplace ------------------------------------------------------
    def choose_marketplace(self) -> str:
        self.say("Select Marketplace:")
        self.say()
        for number, name in enumerate(MARKETPLACES, start=1):
            self.say(f"{number}. {name}")
        self.say()
        while True:
            raw = self.ask("Enter selection (number or name, Q to quit): ").strip()
            if raw.upper() == "Q":
                raise UserAbortError("No marketplace selected.")
            try:
                marketplace = normalize_marketplace(raw)
            except InvalidMarketplaceError as exc:
                self.say(f"{exc} Please enter a number from 1 to {len(MARKETPLACES)} or a name from the list.")
                continue
            self.say(f"Marketplace selected: {marketplace}")
            return marketplace

    # -- payments ---------------------------------------------------------
    def choose_payment(self, payments: list[Payment]) -> Payment:
        self.say("Matching payments found:")
        self.say()
        for number, p in enumerate(payments, start=1):
            self.say(
                f"{number}. {p.payment_number} | {p.customer} | {format_money(p.amount, p.currency)}"
                f" | Unused {format_money(p.unused_amount, p.currency)} | {p.payment_date}"
            )
        self.say()
        while True:
            raw = self.ask("Select payment (number, Q to quit): ").strip()
            if raw.upper() == "Q":
                raise UserAbortError("No payment selected.")
            if raw.isdigit() and 1 <= int(raw) <= len(payments):
                return payments[int(raw) - 1]
            self.say(f"Please enter a number from 1 to {len(payments)}.")

    def show_payment(self, payment: Payment, marketplace: str, title: str = "PAYMENT FOUND") -> None:
        c = payment.currency
        self.say()
        self.banner(title)
        self.say()
        self.say(f"Marketplace: {marketplace}")
        self.say(f"Created By: {payment.created_by or '-'}")
        self.say(f"Customer: {payment.customer}")
        self.say()
        self.say(f"Payment Received: {format_money(payment.amount, c)}")
        self.say(f"Used Amount: {format_money(payment.used_amount, c)}")
        self.say(f"Unused Amount: {format_money(payment.unused_amount, c)}")
        self.say()
        self.say(f"Payment Number: {payment.payment_number}")
        self.say(f"Payment Reference: {payment.reference or '-'}")
        self.say(f"Payment Date: {payment.payment_date or '-'}")
        self.say()

    def approve_payment(self, payment: Payment, marketplace: str) -> bool:
        self.show_payment(payment, marketplace)
        self.say("Do you want to use this payment?")
        self.say()
        return self.confirm_yes()

    def confirm_changed_payment(self, changes: list[tuple[str, str, str]]) -> bool:
        self.say()
        self.banner("PAYMENT CHANGED SINCE SELECTION")
        for name, before, after in changes:
            self.say(f"{name}: was {before}, now {after}")
        self.say()
        self.say("Do you still want to use this payment with the NEW values?")
        return self.confirm_yes()

    # -- invoice applications ---------------------------------------------
    def propose_application(self, proposal: ApplicationProposal, dry_run: bool) -> tuple[Decision, Approval | None]:
        c = proposal.currency
        self.say()
        self.banner("PROPOSED PAYMENT APPLICATION" + (" (DRY RUN)" if dry_run else ""))
        self.say()
        self.say(f"Payment: {proposal.payment_number}")
        self.say(f"Customer: {proposal.customer}")
        self.say()
        self.say(f"Invoice: {proposal.invoice_number}")
        self.say(f"Invoice Outstanding: {format_money(proposal.invoice_outstanding, c)}")
        self.say()
        self.say(f"Payment Unused Before: {format_money(proposal.unused_before, c)}")
        self.say()
        self.say(f"Proposed Application: {format_money(proposal.application, c)}")
        self.say()
        self.say(f"Payment Unused After: {format_money(proposal.unused_after, c)}")
        self.say()
        if dry_run:
            self.say("DRY RUN: nothing will be saved in Zoho, whatever you answer.")
        self.say("Apply this amount?")
        self.say()
        answer = self.ask("Type YES or NO (STOP to end): ").strip().upper()
        if answer == "YES":
            approval = Approval(
                payment_number=proposal.payment_number,
                invoice_number=proposal.invoice_number,
                amount=proposal.application,
                approved_at=datetime.now().isoformat(timespec="seconds"),
            )
            return Decision.APPROVED, approval
        if answer == "STOP":
            return Decision.STOP, None
        return Decision.DECLINED, None
