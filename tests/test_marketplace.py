import json
from pathlib import Path

import pytest

from src.exceptions import ConfigError
from src.marketplace import MARKETPLACES, InvalidMarketplaceError, load_rules, normalize_marketplace, parse_rules

from .conftest import ScriptedConsole, make_payment

RULES_FILE = Path(__file__).resolve().parent.parent / "config" / "marketplace_rules.json"


def test_exactly_the_ten_marketplaces():
    assert MARKETPLACES == ("US", "UK", "eBay", "Shopify", "Walmart", "TikTok", "Italy", "Germany", "Canada", "Mexico")


@pytest.mark.parametrize("raw", ["us", "US", "Us", " us ", "1"])
def test_us_variants_normalise(raw):
    assert normalize_marketplace(raw) == "US"


@pytest.mark.parametrize("raw, expected", [("EBAY", "eBay"), ("tiktok", "TikTok"), ("10", "Mexico"), ("3", "eBay")])
def test_other_names_normalise(raw, expected):
    assert normalize_marketplace(raw) == expected


@pytest.mark.parametrize("raw", ["", "USA", "Amazon", "0", "11", "United States", "e bay"])
def test_unknown_input_is_rejected_not_mapped(raw):
    with pytest.raises(InvalidMarketplaceError):
        normalize_marketplace(raw)


def test_console_reprompts_until_valid_choice():
    console = ScriptedConsole(["usa", "", "uk"])
    assert console.choose_marketplace() == "UK"
    assert "not a recognised marketplace" in console.text


def test_console_never_selects_automatically():
    console = ScriptedConsole([])
    from src.exceptions import UserAbortError

    with pytest.raises(UserAbortError):
        console.choose_marketplace()


def test_project_rules_file_is_valid():
    rules = load_rules(RULES_FILE)
    assert set(rules.keywords) == set(MARKETPLACES)


# --- generic matching mechanics (test-only rules) ---------------------------

def generic_rules():
    return parse_rules(
        {
            "fields_to_search": ["customer", "reference"],
            "marketplaces": {
                name: ({"keywords": [name], "case_sensitive": True} if name in ("US", "UK") else {"keywords": [name]})
                for name in MARKETPLACES
            },
        }
    )


def test_identifies_whole_word():
    ident = generic_rules().identify(make_payment(customer="Amazon US"))
    assert ident.is_identified and ident.marketplace == "US"


@pytest.mark.parametrize("customer", ["Business Supplies", "USD Holdings", "Amazon us"])
def test_case_sensitive_keyword_does_not_match_inside_words_or_lowercase(customer):
    assert generic_rules().identify(make_payment(customer=customer, reference="")).status == "none"


def test_two_marketplaces_is_ambiguous():
    ident = generic_rules().identify(make_payment(customer="eBay UK"))
    assert ident.status == "ambiguous" and ident.marketplace is None


def test_reference_field_is_searched_when_configured():
    ident = generic_rules().identify(make_payment(customer="Settlement Co", reference="SHOPIFY-PAYOUT-55"))
    assert ident.marketplace == "Shopify"


# --- the project's rules against the REAL Zoho customer names (2026-10-06) --

REAL_CUSTOMERS = {
    "Amazon US": "US",
    "Amazon UK GBP": "UK",
    "EBAY US": "eBay",
    "SHOPIFY Sales": "Shopify",
    "Shopify US": "Shopify",
    "Walmart US": "Walmart",
    "TIKTOK Sales": "TikTok",
    "TIK TOK Sales": "TikTok",
    "Amazon Italy - Europe": "Italy",
    "Amazon Germany": "Germany",
    "Amazon Canada (CAD $)": "Canada",
    "Amazon Mexico": "Mexico",
    "Amazon Mexico ($)": "Mexico",
}


@pytest.mark.parametrize("customer, marketplace", REAL_CUSTOMERS.items())
def test_real_customer_names_map_to_one_marketplace(customer, marketplace):
    ident = load_rules(RULES_FILE).identify(make_payment(customer=customer, reference="ANY SETTLEMENT"))
    assert ident.is_identified and ident.marketplace == marketplace


@pytest.mark.parametrize("customer", ["Amazon DE Fee Reversal", "Souq.com"])
def test_excluded_customers_match_no_marketplace(customer):
    assert load_rules(RULES_FILE).identify(make_payment(customer=customer)).status == "none"


def test_rules_require_all_marketplaces():
    data = json.loads(RULES_FILE.read_text(encoding="utf-8"))
    del data["marketplaces"]["Mexico"]
    with pytest.raises(ConfigError):
        parse_rules(data)


def test_rules_reject_unknown_field():
    data = json.loads(RULES_FILE.read_text(encoding="utf-8"))
    data["fields_to_search"] = ["customer", "password"]
    with pytest.raises(ConfigError):
        parse_rules(data)
