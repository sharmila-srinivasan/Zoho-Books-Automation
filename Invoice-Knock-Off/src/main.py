"""Command-line entry point.

    uv run python -m src.main                 # reconcile (DRY RUN unless .env says otherwise)
    uv run python -m src.main --check-config  # validate .env and rules, no browser
    uv run python -m src.main --inspect       # capture Zoho pages to verify selectors
"""

from __future__ import annotations

import argparse
import sys

from playwright.sync_api import Error as PlaywrightError

from . import zoho_selectors
from .accounting import ZERO
from .approval import Console
from .browser import BrowserSession
from .config import Settings, load_settings
from .exceptions import ConfigError, ReconciliationError, UserAbortError
from .inspector import run_inspection
from .invoice_reconciliation import ReconciliationEngine
from .logger import get_logger, setup_logging
from .marketplace import MarketplaceRules, load_rules
from .models import ReconciliationResult, RunContext
from .utils import format_money
from .zoho_gateway import Discovery, ZohoGateway
from .zoho_login import ZohoLoginPage

EXIT_OK, EXIT_STOPPED, EXIT_CONFIG, EXIT_BLOCKED, EXIT_INTERRUPTED = 0, 1, 2, 3, 130


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Zoho Books payment reconciliation")
    parser.add_argument("--check-config", action="store_true", help="validate configuration and exit")
    parser.add_argument("--inspect", action="store_true", help="capture Zoho pages for selector verification")
    args = parser.parse_args(argv)
    # Amounts contain €, £, ₹ ... never crash on a terminal that cannot show them.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    console = Console()
    try:
        settings = load_settings()
        rules = load_rules(settings.marketplace_rules_path)
    except ConfigError as exc:
        console.say(f"CONFIGURATION ERROR:\n{exc}")
        return EXIT_CONFIG
    setup_logging(settings.log_level, settings.logs_dir, settings.secrets())

    if args.check_config:
        console.banner("CONFIGURATION OK")
        console.say(settings.describe())
        console.say(f"Marketplace rules:     {'reviewed' if rules.reviewed else 'DEFAULTS - not reviewed yet'}")
        console.say(f"Zoho UI verified:      {zoho_selectors.LIVE_UI_VERIFIED}")
        return EXIT_OK
    try:
        if args.inspect:
            return inspect(settings, console)
        return reconcile(settings, rules, console)
    except KeyboardInterrupt:
        console.say("\nStopped by user (Ctrl+C). No further actions were taken.")
        get_logger().warning("Run interrupted with Ctrl+C")
        return EXIT_INTERRUPTED


def inspect(settings: Settings, console: Console) -> int:
    with BrowserSession(settings) as session:
        try:
            ZohoLoginPage(session.page, settings, console).ensure_logged_in()
            run_inspection(session.page, settings, console)
        except UserAbortError:
            console.say("Inspection ended.")
        except (ReconciliationError, PlaywrightError) as exc:
            folder = session.save_error_report(exc, RunContext(action="Inspection"))
            console.say(f"Inspection stopped: {exc}\nDetails saved in {folder}")
            return EXIT_STOPPED
    return EXIT_OK


def reconcile(settings: Settings, rules: MarketplaceRules, console: Console) -> int:
    log = get_logger()
    console.banner("ZOHO PAYMENT RECONCILIATION")
    console.say()
    console.say(f"Mode: {'DRY RUN (nothing will be saved)' if settings.dry_run else 'LIVE (approved changes WILL be saved)'}")
    console.say()

    if not settings.dry_run:
        if not zoho_selectors.LIVE_UI_VERIFIED:
            console.say(
                "LIVE MODE IS BLOCKED: the automation has not yet been verified against your Zoho screens.\n"
                "Run in DRY RUN mode and complete the verification steps in the README first.\n"
                "No financial changes were made."
            )
            log.warning("Live mode refused: LIVE_UI_VERIFIED is False")
            return EXIT_BLOCKED
        console.say("WARNING: LIVE MODE. Each application you approve will be saved in Zoho Books.")
        if not console.confirm_yes("Type YES to continue in LIVE mode: "):
            console.say("Cancelled. No financial changes were made.")
            return EXIT_OK

    if not rules.reviewed:
        console.say(
            "NOTE: config/marketplace_rules.json still contains the default keywords.\n"
            "Review it so marketplaces are recognised the way your Zoho data is named.\n"
        )

    try:
        marketplace = console.choose_marketplace()
    except UserAbortError:
        console.say("No marketplace selected. Nothing was done.")
        return EXIT_OK
    log.info("Marketplace selected: %s", marketplace)
    context = RunContext(marketplace=marketplace)

    with BrowserSession(settings) as session:
        try:
            result = _reconcile_in_browser(session, settings, rules, console, marketplace, context)
        except UserAbortError as exc:
            console.say(f"\nStopped: {exc}\nNo further financial actions were performed.")
            log.info("Run stopped by user: %s", exc)
            return EXIT_OK
        except (ReconciliationError, PlaywrightError) as exc:
            log.error("STOPPED - %s: %s", type(exc).__name__, exc)
            folder = None
            try:
                folder = session.save_error_report(exc, context)
            except Exception as report_exc:  # noqa: BLE001
                log.error("Could not save the error report: %s", report_exc)
            console.say()
            console.banner("RECONCILIATION STOPPED")
            console.say(str(exc))
            console.say("\nNo further financial actions will be performed.")
            if folder:
                console.say(f"Diagnostics saved in: {folder}")
            return EXIT_STOPPED
    if result is not None:
        _print_summary(console, result)
    return EXIT_OK


def _reconcile_in_browser(
    session: BrowserSession,
    settings: Settings,
    rules: MarketplaceRules,
    console: Console,
    marketplace: str,
    context: RunContext,
) -> ReconciliationResult | None:
    log = get_logger()
    page = session.page

    context.action = "Logging in"
    ZohoLoginPage(page, settings, console).ensure_logged_in()
    session.capture("login")

    if settings.zoho_organization:
        console.say(f"\nPlease check the browser: is the organization '{settings.zoho_organization}' open?")
        if not console.confirm_yes():
            raise UserAbortError(f"Organization '{settings.zoho_organization}' not confirmed.")

    gateway = ZohoGateway(page, settings, capture=session.capture)
    context.action = "Searching payments"
    discovery = gateway.discover_payments(marketplace, rules)
    _report_discovery(console, discovery, marketplace, settings.payment_created_by)
    if not discovery.candidates:
        return None

    if len(discovery.candidates) == 1:
        selected = discovery.candidates[0]
    else:
        selected = console.choose_payment(discovery.candidates)
    context.payment_number = selected.payment_number
    session.capture("payment_found")
    if not console.approve_payment(selected, marketplace):
        console.say("Payment not approved. No financial changes were made.")
        log.info("User did not approve payment %s", selected.payment_number)
        return None
    log.info("User approved payment %s", selected.payment_number)

    engine = ReconciliationEngine(
        gateway,
        console,
        dry_run=settings.dry_run,
        max_applications=settings.max_invoice_applications,
        context=context,
        capture=session.capture,
    )
    opened = engine.confirm_opened_payment(selected)
    return engine.run(opened)


def _report_discovery(console: Console, found: Discovery, marketplace: str, creators: tuple[str, ...]) -> None:
    log = get_logger()
    log.info(
        "Payments listed: %s | zero unused: %s | other marketplaces: %s | not identified: %s | other creators: %s | matching: %s",
        found.total_rows,
        found.zero_unused,
        found.other_marketplace,
        len(found.unidentified),
        len(found.other_creator),
        len(found.candidates),
    )
    if found.not_reviewed:
        console.say(
            f"NOTE: {found.not_reviewed} more {marketplace} payments were not checked "
            "(MAX_PAYMENTS_TO_REVIEW limit)."
        )
    if found.candidates:
        return
    console.say()
    if found.unidentified:
        console.say("Unable to confidently identify the marketplace for this payment.\n")
        console.say("No financial changes were made.\n")
        console.say("Payments with an unused amount that matched no marketplace (or more than one):")
        for payment, ident in found.unidentified:
            evidence = f" [{'; '.join(ident.evidence)}]" if ident.evidence else ""
            console.say(f"  - {payment.payment_number} | {payment.customer} | ref {payment.reference or '-'}{evidence}")
        console.say(
            "\nGuidance needed: tell the tool how these payments show their marketplace by editing\n"
            "config/marketplace_rules.json (see README, 'Marketplace rules')."
        )
    else:
        console.say(
            f"No {marketplace} payments created by {' / '.join(creators)} with an unused amount were found.\n"
            "No financial changes were made."
        )


def _print_summary(console: Console, result: ReconciliationResult) -> None:
    console.say()
    console.banner("RECONCILIATION SUMMARY" + (" (DRY RUN)" if result.dry_run else ""))
    console.say(f"Payment: {result.payment_number}")
    console.say(f"Result: {result.status.value}")
    console.say(f"Unused at start: {format_money(result.starting_unused)}")
    for record in result.applications:
        tag = "would apply" if record.simulated else "applied"
        console.say(
            f"  {record.invoice_number}: {tag} {format_money(record.amount)}"
            f" -> unused {format_money(record.unused_after)}"
        )
    simulated = " (simulated)" if result.dry_run and result.applications else ""
    console.say(f"Unused at end{simulated}: {format_money(result.final_unused)}")
    if result.dry_run:
        console.say("\nDRY RUN: nothing was saved in Zoho Books.")
    elif result.final_unused == ZERO:
        console.say("\nCOMPLETE: the payment's unused amount is now zero.")


if __name__ == "__main__":
    sys.exit(main())
