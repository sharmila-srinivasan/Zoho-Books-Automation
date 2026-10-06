"""End-to-end tests of the reconciliation loop against a fake Zoho (no browser)."""

import pytest

from src.exceptions import AmountMismatchError, InvoiceMismatchError, PaymentVerificationError, UserAbortError
from src.invoice_reconciliation import ReconciliationEngine
from src.models import ResultStatus, RunContext

from .conftest import D, FakeZoho, ScriptedConsole, make_invoice, make_payment


def engine_for(zoho, answers, dry_run=False, max_applications=50):
    console = ScriptedConsole(answers)
    engine = ReconciliationEngine(
        zoho, console, dry_run=dry_run, max_applications=max_applications, context=RunContext(marketplace="US")
    )
    return engine, console


def test_spec_example_applies_three_invoices_until_zero():
    zoho = FakeZoho(
        make_payment(unused_amount=D(5000)),
        [make_invoice("A", 2000), make_invoice("B", 1500), make_invoice("C", 1500)],
    )
    engine, console = engine_for(zoho, ["YES", "YES", "YES"])
    result = engine.run(zoho.payment)
    assert zoho.applied == [("A", D(2000)), ("B", D(1500)), ("C", D(1500))]
    assert result.status is ResultStatus.COMPLETE and result.final_unused == 0
    assert "Payment Unused After: USD 3,000.00" in console.text


def test_partial_invoice_then_stop():
    zoho = FakeZoho(make_payment(unused_amount=D(3000)), [make_invoice("A", 5000), make_invoice("B", 100)])
    engine, _ = engine_for(zoho, ["YES"])
    result = engine.run(zoho.payment)
    assert zoho.applied == [("A", D(3000))]
    assert result.status is ResultStatus.COMPLETE


def test_zero_unused_does_nothing():
    zoho = FakeZoho(make_payment(unused_amount=D(0)), [make_invoice("A", 100)])
    engine, console = engine_for(zoho, [])
    result = engine.run(zoho.payment)
    assert result.status is ResultStatus.COMPLETE and zoho.applied == [] and console.prompts == []


def test_dry_run_never_calls_apply_but_simulates_full_plan():
    zoho = FakeZoho(
        make_payment(unused_amount=D(5000)),
        [make_invoice("A", 2000), make_invoice("B", 1500), make_invoice("C", 1500)],
    )
    engine, console = engine_for(zoho, ["YES", "YES", "YES"], dry_run=True)
    result = engine.run(zoho.payment)
    assert zoho.applied == []
    assert [(r.invoice_number, r.amount, r.simulated) for r in result.applications] == [
        ("A", D(2000), True), ("B", D(1500), True), ("C", D(1500), True)
    ]
    assert result.final_unused == 0
    assert "DRY RUN" in console.text


@pytest.mark.parametrize("answer", ["NO", "", "y", "ok"])
def test_anything_but_yes_does_not_apply(answer):
    zoho = FakeZoho(make_payment(unused_amount=D(1000)), [make_invoice("A", 500)])
    engine, _ = engine_for(zoho, [answer])
    result = engine.run(zoho.payment)
    assert zoho.applied == []
    assert result.status is ResultStatus.NO_ELIGIBLE_INVOICES


def test_declined_invoice_moves_to_next():
    zoho = FakeZoho(make_payment(unused_amount=D(1000)), [make_invoice("A", 500), make_invoice("B", 1000)])
    engine, _ = engine_for(zoho, ["NO", "YES"])
    result = engine.run(zoho.payment)
    assert zoho.applied == [("B", D(1000))]
    assert result.status is ResultStatus.COMPLETE


def test_stop_ends_run():
    zoho = FakeZoho(make_payment(unused_amount=D(1000)), [make_invoice("A", 500), make_invoice("B", 500)])
    engine, _ = engine_for(zoho, ["YES", "STOP"])
    result = engine.run(zoho.payment)
    assert zoho.applied == [("A", D(500))]
    assert result.status is ResultStatus.STOPPED_BY_USER


def test_closed_input_stops_safely():
    zoho = FakeZoho(make_payment(unused_amount=D(1000)), [make_invoice("A", 500)])
    engine, _ = engine_for(zoho, [])
    with pytest.raises(UserAbortError):
        engine.run(zoho.payment)
    assert zoho.applied == []


def test_post_save_verification_mismatch_stops_everything():
    zoho = FakeZoho(
        make_payment(unused_amount=D(4000)),
        [make_invoice("A", 2000), make_invoice("B", 2000)],
        tamper_after_save=D(500),
    )
    engine, _ = engine_for(zoho, ["YES", "YES"])
    with pytest.raises(AmountMismatchError) as error:
        engine.run(zoho.payment)
    assert "Expected unused amount: USD 2,000.00" in str(error.value)
    assert "Actual unused amount: USD 1,500.00" in str(error.value)
    assert zoho.applied == [("A", D(2000))]  # nothing after the failure


def test_maximum_applications_limit():
    zoho = FakeZoho(make_payment(unused_amount=D(1000)), [make_invoice(f"I{n}", 100) for n in range(10)])
    engine, _ = engine_for(zoho, ["YES"] * 10, max_applications=3)
    result = engine.run(zoho.payment)
    assert len(zoho.applied) == 3
    assert result.status is ResultStatus.MAX_APPLICATIONS_REACHED


def test_no_invoices_stops():
    zoho = FakeZoho(make_payment(unused_amount=D(1000)), [])
    engine, _ = engine_for(zoho, [])
    assert engine.run(zoho.payment).status is ResultStatus.NO_ELIGIBLE_INVOICES


def test_customer_and_currency_mismatches_are_skipped():
    zoho = FakeZoho(
        make_payment(unused_amount=D(1000)),
        [make_invoice("X", 500, customer="Other Co"), make_invoice("Y", 500, currency="CAD"), make_invoice("Z", 300)],
    )
    engine, _ = engine_for(zoho, ["YES"])
    engine.run(zoho.payment)
    assert zoho.applied == [("Z", D(300))]


def test_ambiguous_invoice_stops_before_any_change():
    zoho = FakeZoho(make_payment(unused_amount=D(1000)), [make_invoice("A", 500, currency=None)])
    engine, _ = engine_for(zoho, ["YES"])
    with pytest.raises(InvoiceMismatchError):
        engine.run(zoho.payment)
    assert zoho.applied == []


def test_duplicate_invoice_numbers_stop():
    zoho = FakeZoho(make_payment(unused_amount=D(1000)), [make_invoice("A", 500), make_invoice("A", 200)])
    engine, _ = engine_for(zoho, ["YES"])
    with pytest.raises(InvoiceMismatchError):
        engine.run(zoho.payment)


def test_unused_amount_changed_by_someone_else_stops():
    zoho = FakeZoho(make_payment(unused_amount=D(1000)), [make_invoice("A", 500), make_invoice("B", 500)])
    engine, _ = engine_for(zoho, ["YES", "YES"])
    original_apply = zoho.apply_payment

    def apply_then_external_change(payment, invoice, approval):
        original_apply(payment, invoice, approval)
        zoho.open_payment = lambda number: make_payment(unused_amount=D(100))

    zoho.apply_payment = apply_then_external_change
    with pytest.raises(AmountMismatchError):
        engine.run(zoho.payment)
    assert len(zoho.applied) == 1


def test_payment_identity_change_stops():
    zoho = FakeZoho(make_payment(unused_amount=D(1000)), [make_invoice("A", 500), make_invoice("B", 500)])
    engine, _ = engine_for(zoho, ["YES"])
    original_apply = zoho.apply_payment

    def apply_then_customer_changes(payment, invoice, approval):
        original_apply(payment, invoice, approval)
        zoho.payment = make_payment(unused_amount=D(500), customer="Someone Else")

    zoho.apply_payment = apply_then_customer_changes
    with pytest.raises(PaymentVerificationError):
        engine.run(zoho.payment)


def test_changed_payment_requires_reconfirmation():
    selected = make_payment(unused_amount=D(4000))
    zoho = FakeZoho(make_payment(unused_amount=D(3500)), [])
    engine, console = engine_for(zoho, ["NO"])
    with pytest.raises(UserAbortError):
        engine.confirm_opened_payment(selected)
    assert "PAYMENT CHANGED SINCE SELECTION" in console.text

    engine, _ = engine_for(zoho, ["YES"])
    assert engine.confirm_opened_payment(selected).unused_amount == D(3500)
