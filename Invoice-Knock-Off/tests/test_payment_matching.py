from pathlib import Path

import pytest

from src.accounting import classify_payments, creator_matches, payment_differences
from src.config import load_settings
from src.exceptions import ConfigError
from src.marketplace import load_rules

from .conftest import D, ScriptedConsole, make_payment

RULES = load_rules(Path(__file__).resolve().parent.parent / "config" / "marketplace_rules.json")


@pytest.mark.parametrize("shown", ["Harish", "Harish N", "harish n", "HARISH N.", "  Harish   N "])
def test_creator_matches_harish_variants(shown):
    assert creator_matches(shown, ("Harish",))


@pytest.mark.parametrize("shown", ["Harishankar", "N Harish", "Ravi", "", "Hari"])
def test_creator_does_not_match_others(shown):
    assert not creator_matches(shown, ("Harish",))


def test_multiple_configured_creators():
    assert creator_matches("Priya K", ("Harish", "Priya"))


def test_classify_filters_zero_unused_and_marketplace():
    payments = [
        make_payment(payment_number="P1", customer="Amazon US", unused_amount=D(100)),
        make_payment(payment_number="P2", customer="Amazon US", unused_amount=D(0)),
        make_payment(payment_number="P3", customer="Amazon UK", unused_amount=D(50)),
        make_payment(payment_number="P4", customer="Generic Buyer", reference="", unused_amount=D(10)),
        make_payment(payment_number="P5", customer="EBAY US", unused_amount=D(10)),
        make_payment(payment_number="P6", customer="Amazon DE Fee Reversal", unused_amount=D(10)),
    ]
    groups = classify_payments(payments, "US", RULES)
    assert [p.payment_number for p in groups.matching] == ["P1"]
    assert [p.payment_number for p in groups.zero_unused] == ["P2"]
    assert [p.payment_number for p in groups.other_marketplace] == ["P3", "P5"]
    assert sorted(p.payment_number for p, _ in groups.unidentified) == ["P4", "P6"]


def test_payment_unchanged_has_no_differences():
    assert payment_differences(make_payment(), make_payment()) == []


def test_payment_change_is_detected():
    changes = payment_differences(make_payment(), make_payment(unused_amount=D(3000), customer="XYZ Ltd"))
    names = {name for name, _, _ in changes}
    assert names == {"Customer", "Unused amount"}


def test_multiple_payments_require_user_selection():
    payments = [make_payment(payment_number="P1"), make_payment(payment_number="P2")]
    console = ScriptedConsole(["5", "abc", "2"])
    assert console.choose_payment(payments).payment_number == "P2"


@pytest.mark.parametrize(
    "answer, approved",
    [("YES", True), ("yes", True), (" YES ", True), ("Y", False), ("", False), ("no", False), ("YESS", False)],
)
def test_only_yes_approves_payment(answer, approved):
    console = ScriptedConsole([answer])
    assert console.approve_payment(make_payment(), "US") is approved


def test_payment_found_screen_contents():
    console = ScriptedConsole(["NO"])
    console.approve_payment(make_payment(), "US")
    for expected in ("PAYMENT FOUND", "Marketplace: US", "Created By: Harish N", "Customer: ABC Company",
                     "Payment Received: USD 10,000.00", "Used Amount: USD 6,000.00", "Unused Amount: USD 4,000.00"):
        assert expected in console.text


# --- configuration ------------------------------------------------------------

def _env(tmp_path, body: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(body, encoding="utf-8")
    return path


def test_dry_run_is_default(tmp_path):
    env = "ZOHO_BASE_URL=https://books.zoho.com\nPAYMENT_CREATED_BY=Harish, Harish N\n"
    settings = load_settings(_env(tmp_path, env), environ={})
    assert settings.dry_run is True
    assert settings.payment_created_by == ("Harish", "Harish N")


def test_missing_creator_is_an_error_not_a_guess(tmp_path):
    with pytest.raises(ConfigError):
        load_settings(_env(tmp_path, "ZOHO_BASE_URL=https://books.zoho.com\n"), environ={})


def test_password_never_in_repr(tmp_path):
    env = "ZOHO_BASE_URL=https://books.zoho.com\nZOHO_EMAIL=a@b.c\nZOHO_PASSWORD=S3cret!\nPAYMENT_CREATED_BY=Harish\n"
    settings = load_settings(_env(tmp_path, env), environ={})
    assert "S3cret!" not in repr(settings)
    assert "S3cret!" not in settings.describe()


@pytest.mark.parametrize(
    "body",
    [
        "ZOHO_BASE_URL=\n",
        "ZOHO_BASE_URL=http://books.zoho.com\n",
        "ZOHO_BASE_URL=https://example.com\n",
        "ZOHO_BASE_URL=https://books.zoho.com\nDRY_RUN=maybe\n",
        "ZOHO_BASE_URL=https://books.zoho.com\nZOHO_EMAIL=a@b.c\n",
        "ZOHO_BASE_URL=https://books.zoho.com\nPAYMENT_CREATED_BY= , \n",
        "ZOHO_BASE_URL=https://books.zoho.com\nMAX_INVOICE_APPLICATIONS=0\n",
    ],
)
def test_invalid_configuration_rejected(tmp_path, body):
    with pytest.raises(ConfigError):
        load_settings(_env(tmp_path, body), environ={})


def test_log_redacts_password(tmp_path):
    import logging

    from src.logger import setup_logging

    logger = setup_logging("INFO", tmp_path, ["S3cret!"])
    logger.info("login with password=%s and token: abc123", "S3cret!")
    for handler in logger.handlers:
        handler.flush()
    content = (tmp_path / "reconciliation.log").read_text(encoding="utf-8")
    assert "S3cret!" not in content and "abc123" not in content
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
    logging.getLogger("zoho_reconciliation").handlers.clear()


@pytest.mark.parametrize(
    "base, expected",
    [
        ("https://books.zoho.com", "https://accounts.zoho.com/signin?servicename=ZohoBooks"),
        ("https://books.zoho.in", "https://accounts.zoho.in/signin?servicename=ZohoBooks"),
        ("https://books.zoho.eu", "https://accounts.zoho.eu/signin?servicename=ZohoBooks"),
    ],
)
def test_sign_in_url_follows_data_centre(tmp_path, base, expected):
    from src.zoho_login import ZohoLoginPage

    settings = load_settings(_env(tmp_path, f"ZOHO_BASE_URL={base}\nPAYMENT_CREATED_BY=Harish\n"), environ={})
    assert ZohoLoginPage(None, settings, ScriptedConsole([]))._sign_in_url() == expected
