"""Shared test helpers: a scripted console and a fake Zoho (no browser, no network)."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal

import pytest

from src.approval import Console
from src.models import Approval, Invoice, Payment


def D(value) -> Decimal:
    return Decimal(str(value))


class ScriptedConsole(Console):
    """Console that answers prompts from a list and records everything printed."""

    def __init__(self, answers: list[str]):
        self.answers = list(answers)
        self.output: list[str] = []
        self.prompts: list[str] = []
        super().__init__(input_fn=self._answer, output_fn=self.output.append)

    def _answer(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)

    @property
    def text(self) -> str:
        return "\n".join(self.output)


def make_payment(**overrides) -> Payment:
    values = dict(
        payment_number="PAY-001",
        customer="ABC Company",
        amount=D(10000),
        unused_amount=D(4000),
        currency="USD",
        reference="REF-1",
        payment_date="06/10/2026",
        created_by="Harish N",
    )
    values.update(overrides)
    return Payment(**values)


def make_invoice(number: str, outstanding, total=None, **overrides) -> Invoice:
    values = dict(
        invoice_number=number,
        customer="ABC Company",
        invoice_date="01/09/2026",
        currency="USD",
        total=D(total if total is not None else outstanding),
        outstanding=D(outstanding),
    )
    values.update(overrides)
    return Invoice(**values)


@dataclass
class FakeZoho:
    """In-memory stand-in for Zoho Books implementing the PaymentGateway protocol."""

    payment: Payment
    invoices: list[Invoice]
    # If set, the unused amount Zoho "reports" after the next save is off by this much.
    tamper_after_save: Decimal | None = None
    applied: list[tuple[str, Decimal]] = field(default_factory=list)
    open_calls: int = 0

    def open_payment(self, payment_number: str) -> Payment:
        assert payment_number == self.payment.payment_number
        self.open_calls += 1
        return self.payment

    def read_payment(self, payment_number: str) -> Payment:
        assert payment_number == self.payment.payment_number
        return self.payment

    def list_invoices(self, payment: Payment) -> list[Invoice]:
        return [i for i in self.invoices if i.outstanding > 0]

    def apply_payment(self, payment: Payment, invoice: Invoice, approval: Approval) -> None:
        assert approval.matches(payment.payment_number, invoice.invoice_number, approval.amount)
        self.applied.append((invoice.invoice_number, approval.amount))
        self.invoices = [
            replace(i, outstanding=i.outstanding - approval.amount) if i.invoice_number == invoice.invoice_number else i
            for i in self.invoices
        ]
        unused = self.payment.unused_amount - approval.amount
        if self.tamper_after_save is not None:
            unused -= self.tamper_after_save
            self.tamper_after_save = None
        self.payment = replace(self.payment, unused_amount=unused)


@pytest.fixture
def payment() -> Payment:
    return make_payment()
