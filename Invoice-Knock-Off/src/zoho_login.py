"""Zoho login. Automated where possible, always with a manual fallback.

OTP / MFA / CAPTCHA are never bypassed: the program pauses and you complete
them yourself in the browser window.
"""

from __future__ import annotations

from urllib.parse import urlparse

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from .approval import Console
from .config import Settings
from .exceptions import ZohoLoginError
from .logger import get_logger
from .page_helpers import first_visible
from .zoho_selectors import LoginSelectors, NavSelectors

MANUAL_VERIFICATION_MESSAGE = (
    "\nManual verification required.\n\n"
    "Please complete the Zoho verification in the browser.\n\n"
    "Press ENTER after verification is completed."
)
MAX_MANUAL_ATTEMPTS = 3


class ZohoLoginPage:
    def __init__(self, page: Page, settings: Settings, console: Console):
        self.page = page
        self.settings = settings
        self.console = console
        self.log = get_logger()

    def ensure_logged_in(self) -> None:
        self.log.info("Zoho login started")
        self.page.goto(self.settings.zoho_base_url, wait_until="domcontentloaded")
        if self._wait_for_books(seconds=10):
            self.log.info("Zoho login completed (existing session)")
            return

        # Logged-out visits to Books land on the zoho.com marketing site, so go
        # straight to the sign-in page of the same data centre.
        sign_in_url = self._sign_in_url()
        if sign_in_url and not self._on_sign_in_page():
            self.page.goto(sign_in_url, wait_until="domcontentloaded")

        if self.settings.has_credentials and self._on_sign_in_page():
            try:
                self._automated_login()
            except PlaywrightTimeout:
                self.log.info("Automatic login could not finish - switching to manual login")
            if self._reach_books(seconds=20):
                self.log.info("Zoho login completed")
                return

        for _ in range(MAX_MANUAL_ATTEMPTS):
            self.console.wait_for_enter(MANUAL_VERIFICATION_MESSAGE)
            if self._reach_books(seconds=10):
                self.log.info("Zoho login completed (manual verification)")
                return
            self.console.say(
                "Zoho Books is not open yet. Finish logging in (and choose the organization if asked), "
                "then press ENTER again."
            )
        raise ZohoLoginError("Could not confirm a logged-in Zoho Books session.")

    # ------------------------------------------------------------------
    def _sign_in_url(self) -> str | None:
        """books.zoho.<dc> -> https://accounts.zoho.<dc>/signin?servicename=ZohoBooks"""
        host = self.settings.zoho_host
        if not host.startswith("books."):
            return None
        return f"https://accounts.{host[len('books.'):]}{LoginSelectors.SIGN_IN_PATH}"

    def _on_sign_in_page(self) -> bool:
        return LoginSelectors.ACCOUNTS_HOST_FRAGMENT in (urlparse(self.page.url).hostname or "")

    def _in_books_app(self) -> bool:
        host = urlparse(self.page.url).hostname or ""
        if host != self.settings.zoho_host:
            return False
        # The Sales menu only exists once Books has loaded inside an organization.
        sales = self.page.get_by_role("link", name=NavSelectors.SALES_MENU_NAME, exact=True)
        sales_text = self.page.get_by_text(NavSelectors.SALES_MENU_NAME, exact=True)
        try:
            return sales.first.is_visible() or sales_text.first.is_visible()
        except Exception:  # noqa: BLE001 - page navigating
            return False

    def _wait_for_books(self, seconds: int) -> bool:
        for _ in range(seconds * 2):
            if self._in_books_app():
                return True
            self.page.wait_for_timeout(500)
        return False

    def _reach_books(self, seconds: int) -> bool:
        """Wait for Books; if the login finished somewhere else, open Books once more."""
        if self._wait_for_books(seconds):
            return True
        if self._on_sign_in_page():
            return False  # still signing in (OTP, CAPTCHA, ...)
        self.page.goto(self.settings.zoho_base_url, wait_until="domcontentloaded")
        return self._wait_for_books(seconds)

    def _automated_login(self) -> None:
        page = self.page
        timeout = self.settings.timeout_ms
        email_box = first_visible([page.get_by_placeholder(LoginSelectors.EMAIL_PLACEHOLDER)], timeout)
        if email_box is None:
            self.log.info("Email field not recognised on the sign-in page")
            return
        email_box.fill(self.settings.zoho_email)
        page.get_by_role("button", name=LoginSelectors.NEXT_BUTTON_NAME).first.click()

        password_box = first_visible([page.locator(LoginSelectors.PASSWORD_INPUT_CSS)], timeout)
        if password_box is None:
            self.log.info("Password field did not appear (Zoho may be asking for another verification)")
            return
        password_box.fill(self.settings.zoho_password)
        page.get_by_role("button", name=LoginSelectors.SIGN_IN_BUTTON_NAME).first.click()
        self.log.info("Credentials submitted")
