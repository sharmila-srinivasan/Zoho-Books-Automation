"""Marketplace list, user-input normalisation and payment -> marketplace identification.

How a marketplace shows up on a Zoho payment (customer name, reference, notes,
deposit account, ...) is business data that only you know, so it lives in
``config/marketplace_rules.json`` rather than in code. Identification is
deliberately strict: a payment matching zero marketplaces, or more than one,
is reported as "not confidently identified" and is never used.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .exceptions import ConfigError
from .models import Payment

MARKETPLACES: tuple[str, ...] = (
    "US",
    "UK",
    "eBay",
    "Shopify",
    "Walmart",
    "TikTok",
    "Italy",
    "Germany",
    "Canada",
    "Mexico",
)

# Payment fields a rule may search. Must match attribute names on models.Payment.
SEARCHABLE_FIELDS = ("customer", "reference", "notes", "deposit_to")


class InvalidMarketplaceError(ValueError):
    pass


def normalize_marketplace(raw: str) -> str:
    """Map "us", "Us", "1" ... to the canonical name "US". Unknown input is rejected."""
    value = (raw or "").strip()
    if not value:
        raise InvalidMarketplaceError("No marketplace entered.")
    if value.isdigit():
        index = int(value)
        if 1 <= index <= len(MARKETPLACES):
            return MARKETPLACES[index - 1]
        raise InvalidMarketplaceError(f"{value} is not a number between 1 and {len(MARKETPLACES)}.")
    for name in MARKETPLACES:
        if value.casefold() == name.casefold():
            return name
    raise InvalidMarketplaceError(f"{value!r} is not a recognised marketplace.")


@dataclass(frozen=True)
class Keyword:
    text: str
    case_sensitive: bool

    def pattern(self) -> re.Pattern[str]:
        # Whole-word match: "US" must not match "USD" or "Business".
        flags = 0 if self.case_sensitive else re.IGNORECASE
        return re.compile(rf"(?<![A-Za-z0-9]){re.escape(self.text)}(?![A-Za-z0-9])", flags)


@dataclass(frozen=True)
class Identification:
    status: str  # "identified" | "none" | "ambiguous"
    marketplace: str | None
    evidence: tuple[str, ...]

    @property
    def is_identified(self) -> bool:
        return self.status == "identified"


@dataclass(frozen=True)
class MarketplaceRules:
    fields: tuple[str, ...]
    keywords: dict[str, tuple[Keyword, ...]]
    reviewed: bool

    def identify(self, payment: Payment) -> Identification:
        hits: dict[str, list[str]] = {}
        for marketplace, keywords in self.keywords.items():
            for keyword in keywords:
                pattern = keyword.pattern()
                for field_name in self.fields:
                    value = getattr(payment, field_name, "") or ""
                    if pattern.search(value):
                        hits.setdefault(marketplace, []).append(f'{field_name} contains "{keyword.text}"')
        if not hits:
            return Identification("none", None, ())
        if len(hits) > 1:
            evidence = tuple(f"{m}: {'; '.join(e)}" for m, e in hits.items())
            return Identification("ambiguous", None, evidence)
        marketplace, evidence = next(iter(hits.items()))
        return Identification("identified", marketplace, tuple(evidence))


def load_rules(path: Path) -> MarketplaceRules:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"Marketplace rules file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Marketplace rules file is not valid JSON ({path}): {exc}") from exc
    return parse_rules(data)


def parse_rules(data: dict) -> MarketplaceRules:
    fields = tuple(data.get("fields_to_search", ()))
    if not fields:
        raise ConfigError("marketplace_rules.json: 'fields_to_search' must list at least one field.")
    unknown_fields = [f for f in fields if f not in SEARCHABLE_FIELDS]
    if unknown_fields:
        raise ConfigError(
            f"marketplace_rules.json: unknown field(s) {unknown_fields}. Allowed: {list(SEARCHABLE_FIELDS)}"
        )

    raw_markets = data.get("marketplaces", {})
    unknown = [m for m in raw_markets if m not in MARKETPLACES]
    missing = [m for m in MARKETPLACES if m not in raw_markets]
    if unknown or missing:
        raise ConfigError(f"marketplace_rules.json: unknown marketplaces {unknown}, missing marketplaces {missing}.")

    keywords: dict[str, tuple[Keyword, ...]] = {}
    for name in MARKETPLACES:
        entry = raw_markets[name]
        case_sensitive = bool(entry.get("case_sensitive", False))
        words = tuple(Keyword(str(w).strip(), case_sensitive) for w in entry.get("keywords", ()) if str(w).strip())
        if not words:
            raise ConfigError(f"marketplace_rules.json: marketplace {name} has no keywords.")
        keywords[name] = words
    return MarketplaceRules(fields=fields, keywords=keywords, reviewed=bool(data.get("reviewed", False)))
