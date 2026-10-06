"""Loads and validates settings from the .env file.

The .env file is read into a private dictionary (it is NOT copied into the
process environment), and the password is excluded from ``repr`` so it can
never appear in a log line or traceback by accident.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values

from .exceptions import ConfigError

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_TRUE = {"true", "yes", "1", "on"}
_FALSE = {"false", "no", "0", "off"}
_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}


@dataclass(frozen=True)
class Settings:
    zoho_email: str
    zoho_password: str = field(repr=False)
    zoho_base_url: str = ""
    zoho_organization: str = ""
    headless: bool = False
    dry_run: bool = True
    payment_created_by: tuple[str, ...] = ("Harish",)
    log_level: str = "INFO"
    max_invoice_applications: int = 50
    max_payments_to_review: int = 25
    save_debug_screenshots: bool = True
    browser_channel: str = "chrome"
    persist_browser_session: bool = True
    action_timeout_seconds: int = 30
    amount_decimal_separator: str = "auto"
    project_root: Path = PROJECT_ROOT

    @property
    def has_credentials(self) -> bool:
        return bool(self.zoho_email and self.zoho_password)

    @property
    def zoho_host(self) -> str:
        return urlparse(self.zoho_base_url).hostname or ""

    @property
    def timeout_ms(self) -> int:
        return self.action_timeout_seconds * 1000

    @property
    def logs_dir(self) -> Path:
        return self.project_root / "logs"

    @property
    def screenshots_dir(self) -> Path:
        return self.logs_dir / "screenshots"

    @property
    def errors_dir(self) -> Path:
        return self.logs_dir / "errors"

    @property
    def inspection_dir(self) -> Path:
        return self.logs_dir / "inspection"

    @property
    def browser_profile_dir(self) -> Path:
        return self.project_root / "data" / "browser-profile"

    @property
    def marketplace_rules_path(self) -> Path:
        return self.project_root / "config" / "marketplace_rules.json"

    def secrets(self) -> list[str]:
        """Values that must never be written to logs."""
        return [s for s in (self.zoho_password,) if s]

    def describe(self) -> str:
        """Human-readable summary that is safe to print (no password)."""
        return "\n".join(
            [
                f"Zoho Books URL:        {self.zoho_base_url}",
                f"Organization:          {self.zoho_organization or '(not checked)'}",
                f"Login email:           {self.zoho_email or '(manual login)'}",
                f"Password:              {'set' if self.zoho_password else '(not set)'}",
                f"Mode:                  {'DRY RUN' if self.dry_run else 'LIVE'}",
                f"Payments created by:   {', '.join(self.payment_created_by)}",
                f"Max applications:      {self.max_invoice_applications}",
                f"Max payments reviewed: {self.max_payments_to_review}",
                f"Browser:               {self.browser_channel} (headless={self.headless})",
                f"Remember login:        {self.persist_browser_session}",
                f"Timeout (seconds):     {self.action_timeout_seconds}",
                f"Decimal separator:     {self.amount_decimal_separator!r}",
                f"Screenshots:           {self.save_debug_screenshots}",
                f"Log level:             {self.log_level}",
            ]
        )


def _bool(values: dict[str, str], name: str, default: bool) -> bool:
    raw = values.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise ConfigError(f"{name} must be true or false (found {raw!r}).")


def _int(values: dict[str, str], name: str, default: int, minimum: int, maximum: int) -> int:
    raw = values.get(name, "").strip()
    if not raw:
        return default
    try:
        number = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a whole number (found {raw!r}).") from exc
    if not minimum <= number <= maximum:
        raise ConfigError(f"{name} must be between {minimum} and {maximum} (found {number}).")
    return number


def load_settings(env_file: Path | None = None, environ: dict[str, str] | None = None) -> Settings:
    """Read .env (and real environment variables, which take priority) and validate."""
    env_path = env_file or PROJECT_ROOT / ".env"
    if not env_path.exists():
        raise ConfigError(
            f"Configuration file not found: {env_path}\n"
            "Copy .env.example to .env and fill in your values (see README)."
        )
    file_values = {k: (v or "") for k, v in dotenv_values(env_path).items()}
    overrides = os.environ if environ is None else environ
    values = {**file_values, **{k: v for k, v in overrides.items() if k in file_values}}

    email = values.get("ZOHO_EMAIL", "").strip()
    password = values.get("ZOHO_PASSWORD", "")
    if bool(email) != bool(password):
        raise ConfigError("Set both ZOHO_EMAIL and ZOHO_PASSWORD, or leave both blank for manual login.")

    base_url = values.get("ZOHO_BASE_URL", "").strip().rstrip("/")
    if not base_url:
        raise ConfigError("ZOHO_BASE_URL is required (for example the Zoho Books address you log in to).")
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.hostname or ".zoho." not in f".{parsed.hostname}.":
        raise ConfigError(f"ZOHO_BASE_URL must be an https:// Zoho address (found {base_url!r}).")

    creators = tuple(n.strip() for n in values.get("PAYMENT_CREATED_BY", "").split(",") if n.strip())
    if not creators:
        raise ConfigError("PAYMENT_CREATED_BY must contain at least one name.")

    log_level = values.get("LOG_LEVEL", "INFO").strip().upper() or "INFO"
    if log_level not in _LOG_LEVELS:
        raise ConfigError(f"LOG_LEVEL must be one of {sorted(_LOG_LEVELS)} (found {log_level!r}).")

    channel = values.get("BROWSER_CHANNEL", "chrome").strip().lower() or "chrome"
    if channel not in {"chrome", "chromium", "msedge"}:
        raise ConfigError(f"BROWSER_CHANNEL must be chrome, chromium or msedge (found {channel!r}).")

    separator = values.get("AMOUNT_DECIMAL_SEPARATOR", "auto").strip().lower() or "auto"
    if separator not in {"auto", ".", ","}:
        raise ConfigError(f'AMOUNT_DECIMAL_SEPARATOR must be auto, "." or "," (found {separator!r}).')

    return Settings(
        zoho_email=email,
        zoho_password=password,
        zoho_base_url=base_url,
        zoho_organization=values.get("ZOHO_ORGANIZATION", "").strip(),
        headless=_bool(values, "HEADLESS", False),
        # Missing DRY_RUN means dry run: the safe default.
        dry_run=_bool(values, "DRY_RUN", True),
        payment_created_by=creators,
        log_level=log_level,
        max_invoice_applications=_int(values, "MAX_INVOICE_APPLICATIONS", 50, 1, 500),
        max_payments_to_review=_int(values, "MAX_PAYMENTS_TO_REVIEW", 25, 1, 500),
        save_debug_screenshots=_bool(values, "SAVE_DEBUG_SCREENSHOTS", True),
        browser_channel=channel,
        persist_browser_session=_bool(values, "PERSIST_BROWSER_SESSION", True),
        action_timeout_seconds=_int(values, "ACTION_TIMEOUT_SECONDS", 30, 5, 300),
        amount_decimal_separator=separator,
    )
