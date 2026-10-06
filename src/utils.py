"""Small, dependency-free helpers: amount parsing, text normalisation, timestamps."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

# A number with optional grouping, e.g. 10,000.00 / 1,00,000.00 / 12.5 / 7
_NUMBER_TOKEN = re.compile(r"\d[\d.,'  ]*\d|\d")
_DOT_DECIMAL_FORMAT = re.compile(
    r"^(?:\d{1,3}(?:,\d{3})+"  # 10,000 / 1,000,000
    r"|\d{1,2}(?:,\d{2})+,\d{3}"  # Indian grouping 1,00,000
    r"|\d+)"  # 10000
    r"(?:\.\d+)?$"
)
_ISO_CODE = re.compile(r"(?<![A-Za-z])([A-Z]{3})(?![A-Za-z])")
# Longest first so "CA$" wins over "$".
CURRENCY_SYMBOLS = ("US$", "CA$", "MX$", "NZ$", "HK$", "C$", "A$", "S$", "$", "€", "£", "₹", "¥")


def detect_decimal_separator(number: str) -> str:
    """Work out the decimal separator of one displayed number.

    Zoho shows each currency in its own style in the same list (verified
    2026-10-06: "$1,412,078.15" next to "€1.790,51"). Rules:
    * both "." and "," present -> the LAST one is the decimal separator
    * one kind, followed by 1-2 digits at the end -> decimal separator ("€0,00", "$195.16")
    * one kind, followed by 3 digits at the end ("1,790") -> ambiguous -> error
    * no separator -> whole number
    """
    last_dot, last_comma = number.rfind("."), number.rfind(",")
    if last_dot == -1 and last_comma == -1:
        return "."
    if last_dot != -1 and last_comma != -1:
        return "." if last_dot > last_comma else ","
    separator = "." if last_dot != -1 else ","
    decimals = len(number) - max(last_dot, last_comma) - 1
    if decimals in (1, 2) and number.count(separator) == 1:
        return separator
    if decimals == 3 or number.count(separator) > 1:
        # "1,790" / "1.790.000": thousands grouping or 3 decimals? Refuse to guess.
        if number.count(separator) > 1:
            return "," if separator == "." else "."
        raise ValueError(f"Amount {number!r} is ambiguous (thousands separator or decimals?)")
    raise ValueError(f"Amount {number!r} is not in a recognised format")


def parse_amount(text: str | None, decimal_separator: str = "auto") -> Decimal:
    """Turn a displayed amount such as "$10,000.00", "€1.790,51" or "USD 1,234.50" into a Decimal.

    ``decimal_separator`` is "auto" (detect per amount), "." or ",".
    Raises ValueError when the text does not contain exactly one well-formed
    number - we never guess at money values.
    """
    if decimal_separator not in (".", ",", "auto"):
        raise ValueError(f"Unsupported decimal separator: {decimal_separator!r}")
    if text is None or not str(text).strip():
        raise ValueError("Amount text is empty")
    raw = str(text).strip()
    tokens = _NUMBER_TOKEN.findall(raw)
    if len(tokens) != 1:
        raise ValueError(f"Expected exactly one number in amount text {raw!r}")
    number = tokens[0].replace(" ", "").replace(" ", "").replace("'", "")
    if decimal_separator == "auto":
        decimal_separator = detect_decimal_separator(number)
    if decimal_separator == ",":
        number = number.replace(".", "\0").replace(",", ".").replace("\0", ",")
    if not _DOT_DECIMAL_FORMAT.match(number):
        raise ValueError(f"Amount {raw!r} is not in the expected format")
    try:
        value = Decimal(number.replace(",", ""))
    except InvalidOperation as exc:  # pragma: no cover - regex already guards this
        raise ValueError(f"Amount {raw!r} could not be read") from exc
    negative = raw.startswith(("-", "−")) or "-" + tokens[0] in raw or (
        raw.startswith("(") and raw.endswith(")")
    )
    return -value if negative else value


def parse_optional_amount(text: str | None, decimal_separator: str = "auto") -> Decimal:
    """Like parse_amount, but an empty cell (e.g. an untouched input box) means 0."""
    if text is None or not str(text).strip():
        return Decimal("0")
    return parse_amount(text, decimal_separator)


def extract_currency(text: str | None) -> str | None:
    """Return the currency marker shown with an amount ("USD", "$", "CA$", ...), or None."""
    if not text:
        return None
    match = _ISO_CODE.search(text)
    if match:
        return match.group(1)
    for symbol in CURRENCY_SYMBOLS:
        if symbol in text:
            return symbol
    return None


def format_money(amount: Decimal, currency: str | None = None) -> str:
    number = f"{amount:,.2f}"
    if not currency:
        return number
    if currency.isalpha():
        return f"{currency} {number}"
    return f"{currency}{number}"


def normalize_text(value: str | None) -> str:
    """Case-insensitive, whitespace-collapsed comparison form of a piece of text."""
    return " ".join((value or "").split()).casefold()


def normalize_header(value: str | None) -> str:
    """Comparison form of a table column header (must match the JS in page_helpers)."""
    upper = " ".join((value or "").split()).upper()
    return re.sub(r"[^A-Z0-9# ]", "", upper).strip()


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")
    return cleaned[:80] or "item"
