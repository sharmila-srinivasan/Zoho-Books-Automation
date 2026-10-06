"""Zoho Books -> Home -> Sales -> Payments Received, using visible menu names only."""

from __future__ import annotations

from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

from .config import Settings
from .exceptions import ZohoNavigationError, ZohoUIChangedError
from .logger import get_logger
from .page_helpers import first_visible, read_table
from .zoho_selectors import NavSelectors, PaymentListSelectors


class ZohoNavigator:
    def __init__(self, page: Page, settings: Settings):
        self.page = page
        self.settings = settings
        self.log = get_logger()

    def _menu_item(self, name: str):
        # Verified 2026-10-06: Zoho's "Sales" menu entry is a button, sub-entries are hidden until expanded.
        return first_visible(
            [
                self.page.get_by_role("link", name=name, exact=True),
                self.page.get_by_role("button", name=name, exact=True),
                self.page.get_by_text(name, exact=True),
            ],
            timeout_ms=4000,
        )

    def open_home(self) -> None:
        home = self._menu_item(NavSelectors.HOME_LINK_NAME)
        if home is None:
            raise ZohoNavigationError("The 'Home' menu item was not found.")
        home.click()
        self.log.info("Home opened")

    def open_payments_received(self) -> None:
        """Sales > Payments Received, then wait until the payments table is shown.

        Verified 2026-10-06: clicking the menu item is unreliable (Zoho's menu
        overlay intercepts the click on every attempt), while the link's own
        route "#/paymentsreceived" (read from Zoho's DOM) always works. So the
        route is used inside the current organization; the menu is the fallback.
        """
        app_url = self.page.url.split("#", 1)[0]
        if urlparse(app_url).hostname == self.settings.zoho_host and "/app" in urlparse(app_url).path:
            self.page.goto(f"{app_url}{NavSelectors.PAYMENTS_RECEIVED_ROUTE}", wait_until="domcontentloaded")
        elif not self._click_menu_path():
            raise ZohoNavigationError("Could not open Payments Received (not inside a Zoho Books organization).")
        self._wait_for_payments_table()
        self.log.info("Payments Received opened")

    def _click_menu_path(self) -> bool:
        for attempt in range(2):
            try:
                link = self._menu_item(NavSelectors.PAYMENTS_RECEIVED_LINK_NAME)
                if link is None or attempt == 1:
                    sales = self._menu_item(NavSelectors.SALES_MENU_NAME)
                    if sales is None:
                        return False
                    sales.click(timeout=5000)
                    link = self._menu_item(NavSelectors.PAYMENTS_RECEIVED_LINK_NAME)
                if link is None:
                    continue
                link.click(timeout=5000)
                return True
            except PlaywrightError as exc:
                self.log.info("Menu click attempt %s failed: %s", attempt + 1, str(exc).splitlines()[0])
        return False

    def _wait_for_payments_table(self) -> None:
        """Wait until the list shows real rows (with a payment number).

        Verified 2026-10-06: coming back from an open payment, Zoho can show 15
        blank placeholder rows that never fill in. If that lasts, reload once.
        """
        if self._payments_loaded(seconds=8):
            return
        self.log.info("Payments list still loading - reloading the page once")
        self.page.reload(wait_until="domcontentloaded")
        if self._payments_loaded(seconds=self.settings.action_timeout_seconds):
            return
        raise ZohoNavigationError(f"Payments Received list did not appear with any rows. {self._last_error}")

    def _payments_loaded(self, seconds: int) -> bool:
        self._last_error: Exception | None = None
        for _ in range(seconds * 2):
            try:
                table = read_table(
                    self.page, PaymentListSelectors.COLUMNS, PaymentListSelectors.REQUIRED, "Payments Received"
                )
                if any(table.text(row, "payment_number") for row in table.rows):
                    return True
                self._last_error = ZohoNavigationError("the table shows no payment rows yet")
            except ZohoUIChangedError as exc:
                self._last_error = exc
            self.page.wait_for_timeout(500)
        return False
